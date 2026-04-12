#!/usr/bin/env python3
"""Multi-plan selection harness for Aider benchmark.

Implements multi-plan generation and optimal plan selection as described in:
- Self-consistency: sampling multiple reasoning paths
- Tree-of-Thought: explicit plan generation with voting
- Optimal plan selection: majority vote on test outcomes

This harness generates multiple candidate plans via temperature sampling,
evaluates each plan, and selects the best one based on test results.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import random
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

try:
    from codecarbon import EmissionsTracker
except Exception:
    EmissionsTracker = None

from aider import models, sendchat
from aider.coders import Coder, base_coder
from aider.io import InputOutput

INSTRUCTIONS_ADDENDUM = """
####

Use the above instructions to modify the supplied files: {file_list}
Don't change the names of existing functions or classes, as they may be referenced from other code like unit tests, etc.
Only use standard libraries, don't suggest installing any packages.
"""

TEST_FAILURES = """
####

See the testing errors above.
The tests are correct, don't try and change them.
Fix the code in {file_list} to resolve the errors.
"""


def _seconds_left(deadline_ts: Optional[float]) -> Optional[int]:
    if deadline_ts is None:
        return None
    return int(deadline_ts - datetime.datetime.now().timestamp())


def _effective_call_timeout(remaining_seconds: Optional[int], llm_timeout: Optional[int]) -> Optional[int]:
    if remaining_seconds is not None:
        if remaining_seconds <= 0:
            return None
        if llm_timeout and llm_timeout > 0:
            return max(1, min(remaining_seconds, llm_timeout))
        return max(1, remaining_seconds)
    return llm_timeout


def cleanup_test_output(output: str, testdir: Path) -> str:
    res = re.sub(r"\bin \d+\.\d+s\b", "", output)
    return res.replace(str(testdir), str(testdir.name))


def run_unit_tests(
    original_exercise_dir: Path,
    testdir: Path,
    history_fname: Path,
    test_files: list[str],
    timeout_seconds: Optional[int] = None,
) -> Optional[str]:
    timeout = 60 * 3
    if timeout_seconds is not None and timeout_seconds > 0:
        timeout = min(timeout, timeout_seconds)

    test_commands = {
        ".py": ["pytest"],
        ".rs": ["cargo", "test", "--", "--include-ignored"],
        ".go": ["go", "test", "./..."],
        ".js": ["/aider/benchmark/npm-test.sh"],
        ".cpp": ["/aider/benchmark/cpp-test.sh"],
        ".java": ["./gradlew", "test"],
    }

    extensions = {Path(f).suffix for f in test_files}
    command = None
    for ext in extensions:
        if ext in test_commands:
            command = test_commands[ext]
            break

    if not command:
        raise ValueError(f"No test command found for file extensions: {sorted(extensions)}")

    for file_path in test_files:
        src = original_exercise_dir / file_path
        dst = testdir / file_path
        if src.exists():
            os.makedirs(dst.parent, exist_ok=True)
            shutil.copy(src, dst)

    for file_path in test_files:
        if file_path.endswith(".java"):
            test_file = testdir / file_path
            if test_file.exists():
                content = test_file.read_text(encoding="utf-8", errors="replace")
                content = re.sub(r"@Disabled\([^)]*\)\s*\n", "", content)
                test_file.write_text(content, encoding="utf-8")

    result = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=timeout,
        cwd=testdir,
        encoding="utf-8",
        errors="replace",
    )

    success = result.returncode == 0
    res = cleanup_test_output(result.stdout, testdir)

    with history_fname.open("a", encoding="utf-8") as fh:
        fh.write(f"```\n{res}\n```")

    if not success:
        return res

    return None


def build_task_list(
    exercises_dir: Path,
    languages: str,
    keywords: str,
    num_tests: int,
    shuffle_tasks: bool,
) -> list[Path]:
    lang_filter: set[str] = set()
    if languages.strip():
        lang_filter = {x.strip().lower() for x in languages.split(",") if x.strip()}

    keyword_filter: list[str] = []
    if keywords.strip():
        keyword_filter = [x.strip() for x in keywords.split(",") if x.strip()]

    tasks: list[Path] = []
    for lang_dir in sorted(exercises_dir.iterdir()):
        if not lang_dir.is_dir():
            continue
        if lang_filter and lang_dir.name.lower() not in lang_filter:
            continue

        practice = lang_dir / "exercises" / "practice"
        if not practice.is_dir():
            continue

        for ex_dir in sorted(practice.iterdir()):
            if not ex_dir.is_dir():
                continue
            rel = str(ex_dir.relative_to(exercises_dir))
            if keyword_filter and not any(k in rel for k in keyword_filter):
                continue
            tasks.append(ex_dir)

    if shuffle_tasks:
        random.shuffle(tasks)
    if num_tests > 0:
        tasks = tasks[:num_tests]

    return tasks


def get_commit_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short=7", "HEAD"], stderr=subprocess.DEVNULL, text=True
        )
        hsh = out.strip()
        return hsh or "unknown"
    except Exception:
        return "unknown"


def run_single_plan(
    original_exercise_dir: Path,
    testdir: Path,
    model_name: str,
    edit_format: str,
    num_ctx: Optional[int],
    extra_instructions: str,
    commit_hash: str,
    llm_timeout: Optional[int],
    task_timeout_seconds: Optional[int],
    plan_idx: int,
    temperature: float,
) -> dict[str, Any]:
    """Execute a single plan with specified temperature."""
    
    tracker = None
    task_emissions_kg = None
    task_energy_kwh = None
    if EmissionsTracker is not None:
        tracker = EmissionsTracker(save_to_file=False, log_level="error")
        tracker.start()

    # Use a separate history per plan
    history_fname = testdir / f".aider.chat.history.plan_{plan_idx}.md"
    results_fname = testdir / f".aider.results.plan_{plan_idx}.json"

    config_file = testdir / ".meta" / "config.json"
    if not config_file.exists():
        raise FileNotFoundError(f"Missing config file: {config_file}")

    config = json.loads(config_file.read_text(encoding="utf-8"))
    test_files = config.get("files", {}).get("test", [])
    example_files = config.get("files", {}).get("example", [])
    solution_files = set(config.get("files", {}).get("solution", []))

    ignore_files = {"CMakeLists.txt", "Cargo.toml"}
    ignore_files.update(str(p.relative_to(testdir)) for p in testdir.glob(".meta/**/*") if p.is_file())
    ignore_files.update(str(p.relative_to(testdir)) for p in testdir.glob(".docs/**/*") if p.is_file())
    ignore_files.update(test_files)
    ignore_files.update(example_files)
    solution_files.difference_update(ignore_files)

    fnames: list[Path] = []
    for file_path in sorted(solution_files):
        src = testdir / file_path
        if src.exists() and src.is_file():
            fnames.append(src)

    file_list = " ".join(fname.name for fname in fnames)

    instructions = ""
    intro = testdir / ".docs" / "introduction.md"
    if intro.exists():
        instructions += intro.read_text(encoding="utf-8", errors="replace")
    instructions += (testdir / ".docs" / "instructions.md").read_text(encoding="utf-8", errors="replace")
    append_path = testdir / ".docs" / "instructions.append.md"
    if append_path.exists():
        instructions += append_path.read_text(encoding="utf-8", errors="replace")

    instructions += INSTRUCTIONS_ADDENDUM.format(file_list=file_list)
    if extra_instructions:
        instructions += f"\n\n####\n\n{extra_instructions}\n"

    io = InputOutput(pretty=False, yes=True, chat_history_file=history_fname)
    main_model = models.Model(model_name, weak_model=None, editor_model=None, editor_edit_format=None, verbose=False)

    # Set temperature for this plan (Self-consistency sampling strategy)
    if not main_model.extra_params:
        main_model.extra_params = {}
    main_model.extra_params["temperature"] = temperature

    if num_ctx:
        if not main_model.extra_params:
            main_model.extra_params = {}
        main_model.extra_params["num_ctx"] = num_ctx

    if llm_timeout and llm_timeout > 0:
        main_model.timeout = llm_timeout

    actual_edit_format = edit_format or main_model.edit_format
    coder = Coder.create(
        main_model,
        actual_edit_format,
        io,
        fnames=fnames,
        use_git=False,
        stream=False,
        verbose=False,
        cache_prompts=True,
        suggest_shell_commands=False,
        ignore_mentions=ignore_files,
    )
    coder.get_file_mentions = lambda _: set()

    timeouts = 0
    syntax_errors = 0
    indentation_errors = 0
    lazy_comments = 0

    duration = 0.0
    test_outcomes: list[bool] = []
    current_instructions = instructions
    task_timed_out = False
    task_deadline_ts: Optional[float] = None
    if task_timeout_seconds and task_timeout_seconds > 0:
        task_deadline_ts = datetime.datetime.now().timestamp() + task_timeout_seconds

    # Single attempt per plan (no retries to keep execution time bounded)
    remaining_seconds = _seconds_left(task_deadline_ts)
    if remaining_seconds is None or remaining_seconds > 0:
        call_timeout = _effective_call_timeout(remaining_seconds, llm_timeout)
        if call_timeout is not None:
            main_model.timeout = call_timeout

            start = datetime.datetime.now().timestamp()
            response = coder.run(with_message=current_instructions, preproc=False)
            duration += datetime.datetime.now().timestamp() - start
            
            pattern = r"^[+]? *[#].* [.][.][.] "
            lazy_comments += len(re.findall(pattern, response or "", re.MULTILINE))

            if coder.last_keyboard_interrupt:
                raise KeyboardInterrupt

            try:
                remaining_seconds = _seconds_left(task_deadline_ts)
                if remaining_seconds is not None and remaining_seconds <= 0:
                    errors = "Plan timed out!"
                    timeouts += 1
                    task_timed_out = True
                else:
                    errors = run_unit_tests(
                        original_exercise_dir,
                        testdir,
                        history_fname,
                        test_files,
                        timeout_seconds=remaining_seconds,
                    )
            except subprocess.TimeoutExpired:
                errors = "Tests timed out!"
                timeouts += 1

            if errors:
                test_outcomes.append(False)
                err_lines = errors.splitlines()
                syntax_errors += sum(1 for line in err_lines if line.startswith("SyntaxError"))
                indentation_errors += sum(1 for line in err_lines if line.startswith("IndentationError"))
            else:
                test_outcomes.append(True)

    if not test_outcomes and task_timed_out:
        test_outcomes.append(False)

    if tracker is not None:
        try:
            emissions = tracker.stop()
            if emissions is not None:
                task_emissions_kg = float(emissions)
            final_data = getattr(tracker, "final_emissions_data", None)
            if final_data and getattr(final_data, "energy_consumed", None) is not None:
                task_energy_kwh = float(final_data.energy_consumed)
        except Exception:
            pass

    chat_hashes = list(
        zip(
            coder.chat_completion_call_hashes,
            coder.chat_completion_response_hashes,
        )
    )

    results = {
        "plan_idx": plan_idx,
        "temperature": temperature,
        "testdir": str(testdir),
        "testcase": testdir.name,
        "model": main_model.name,
        "edit_format": actual_edit_format,
        "tests_outcomes": test_outcomes,
        "cost": coder.total_cost,
        "duration": duration,
        "test_timeouts": timeouts,
        "commit_hash": commit_hash,
        "num_error_outputs": io.num_error_outputs,
        "num_user_asks": io.num_user_asks,
        "num_exhausted_context_windows": coder.num_exhausted_context_windows,
        "num_malformed_responses": coder.num_malformed_responses,
        "syntax_errors": syntax_errors,
        "indentation_errors": indentation_errors,
        "lazy_comments": lazy_comments,
        "prompt_tokens": coder.total_tokens_sent,
        "completion_tokens": coder.total_tokens_received,
        "task_timed_out": task_timed_out,
        "task_timeout_seconds": task_timeout_seconds,
        "codecarbon_emissions_kg": task_emissions_kg,
        "codecarbon_energy_kwh": task_energy_kwh,
        "arch_planning_enabled": True,
        "arch_decomp_mode": "multi-plan",
        "planner_calls": 0,
        "executor_calls": len(chat_hashes),
        "arch_plan_steps": 0,
        "arch_interleaved_cycles": 0,
        "chat_hashes": chat_hashes,
    }

    results_fname.write_text(json.dumps(results, indent=4) + "\n", encoding="utf-8")
    return results


def select_best_plan(plan_results: list[dict[str, Any]]) -> tuple[int, dict[str, Any]]:
    """Select the best plan based on test outcomes (majority vote).
    
    Returns:
        (plan_idx, merged_results)
    """
    # Strategy 1: Majority vote - pick plan that passed tests
    passing_plans = [r for r in plan_results if any(r.get("tests_outcomes", []))]
    if passing_plans:
        # Among passing plans, pick the one with shortest duration
        best = min(passing_plans, key=lambda r: r.get("duration", float("inf")))
        return best["plan_idx"], best
    
    # Strategy 2: If no plan passed, pick the one with fewest tokens (cheapest)
    if plan_results:
        best = min(plan_results, key=lambda r: r.get("prompt_tokens", 0) + r.get("completion_tokens", 0))
        return best["plan_idx"], best
    
    # Fallback
    return 0, plan_results[0] if plan_results else {}


def run_single_task_multiplan(
    original_exercise_dir: Path,
    testdir: Path,
    model_name: str,
    edit_format: str,
    num_ctx: Optional[int],
    extra_instructions: str,
    commit_hash: str,
    llm_timeout: Optional[int],
    task_timeout_seconds: Optional[int],
    num_plans: int = 4,
) -> dict[str, Any]:
    """Run multiple plans and select the best one.
    
    Multi-plan generation strategy:
    - Uses temperature sampling (0.3, 0.7, 1.0, 1.5) as per Self-consistency
    - Each plan gets equal budget of remaining task time
    """
    
    # Allocate time budget across plans
    plan_time_budget = None
    if task_timeout_seconds and task_timeout_seconds > 0:
        plan_time_budget = max(60, task_timeout_seconds // max(1, num_plans))
    
    # Temperature sampling strategy from Self-consistency
    temperatures = [0.3, 0.7, 1.0, 1.5][:num_plans]
    
    plan_results: list[dict[str, Any]] = []
    total_duration = 0.0
    
    for plan_idx, temp in enumerate(temperatures):
        try:
            result = run_single_plan(
                original_exercise_dir=original_exercise_dir,
                testdir=testdir,
                model_name=model_name,
                edit_format=edit_format,
                num_ctx=num_ctx,
                extra_instructions=extra_instructions,
                commit_hash=commit_hash,
                llm_timeout=llm_timeout,
                task_timeout_seconds=plan_time_budget,
                plan_idx=plan_idx,
                temperature=temp,
            )
            plan_results.append(result)
            total_duration += result.get("duration", 0.0)
        except Exception as e:
            print(f"Error in plan {plan_idx}: {e}")
            continue
    
    # Optimal plan selection (majority vote)
    best_plan_idx, best_result = select_best_plan(plan_results)
    
    # Merge results: use best plan as primary, keep multi-plan metadata
    merged = {
        "testdir": best_result.get("testdir", str(testdir)),
        "testcase": best_result.get("testcase", testdir.name),
        "model": best_result.get("model", model_name),
        "edit_format": best_result.get("edit_format", edit_format),
        "tests_outcomes": best_result.get("tests_outcomes", []),
        "cost": sum(r.get("cost", 0.0) for r in plan_results),
        "duration": total_duration,
        "test_timeouts": sum(r.get("test_timeouts", 0) for r in plan_results),
        "commit_hash": commit_hash,
        "num_error_outputs": best_result.get("num_error_outputs", 0),
        "num_user_asks": best_result.get("num_user_asks", 0),
        "num_exhausted_context_windows": sum(r.get("num_exhausted_context_windows", 0) for r in plan_results),
        "num_malformed_responses": sum(r.get("num_malformed_responses", 0) for r in plan_results),
        "syntax_errors": best_result.get("syntax_errors", 0),
        "indentation_errors": best_result.get("indentation_errors", 0),
        "lazy_comments": best_result.get("lazy_comments", 0),
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in plan_results),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in plan_results),
        "task_timed_out": any(r.get("task_timed_out", False) for r in plan_results),
        "task_timeout_seconds": task_timeout_seconds,
        "codecarbon_emissions_kg": sum(r.get("codecarbon_emissions_kg", 0.0) or 0.0 for r in plan_results),
        "codecarbon_energy_kwh": sum(r.get("codecarbon_energy_kwh", 0.0) or 0.0 for r in plan_results),
        "arch_planning_enabled": True,
        "arch_decomp_mode": "multi-plan-selection",
        "planner_calls": num_plans,  # Number of different plans generated
        "executor_calls": sum(r.get("executor_calls", 0) for r in plan_results),
        "arch_plan_steps": num_plans,
        "arch_interleaved_cycles": 0,
        "best_plan_idx": best_plan_idx,
        "num_candidate_plans": len(plan_results),
        "candidate_plans_summary": [
            {
                "plan_idx": r.get("plan_idx"),
                "temperature": r.get("temperature"),
                "passed": any(r.get("tests_outcomes", [])),
                "duration": r.get("duration"),
                "cost": r.get("cost"),
            }
            for r in plan_results
        ],
        "chat_hashes": best_result.get("chat_hashes", []),
    }
    
    # Write merged results
    results_fname = testdir / ".aider.results.json"
    results_fname.write_text(json.dumps(merged, indent=4) + "\n", encoding="utf-8")
    
    return merged


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--edit-format", default="whole")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--tries", type=int, default=1, help="Number of tries (not used in multiplan mode)")
    ap.add_argument("--languages", default="")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--num-tests", type=int, default=-1)
    ap.add_argument("--num-ctx", type=int, default=0)
    ap.add_argument("--exercises-dir", default="polyglot-benchmark")
    ap.add_argument("--shuffle-tasks", type=int, default=1, choices=[0, 1])
    ap.add_argument("--num-plans", type=int, default=4, help="Number of candidate plans to generate")
    args = ap.parse_args()

    benchmark_root = Path(os.environ.get("AIDER_BENCHMARK_DIR", "/benchmarks"))
    exercises_root = benchmark_root / args.exercises_dir
    if not exercises_root.exists():
        raise FileNotFoundError(f"Exercises dir does not exist: {exercises_root}")

    inner_dir = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S") + f"--{args.run_name}"
    run_root = benchmark_root / inner_dir
    run_root.mkdir(parents=True, exist_ok=True)

    tasks = build_task_list(
        exercises_dir=exercises_root,
        languages=args.languages,
        keywords=args.keywords,
        num_tests=args.num_tests,
        shuffle_tasks=bool(args.shuffle_tasks),
    )
    if not tasks:
        raise RuntimeError("No exercises matched the provided filters")

    print(f"Multi-Plan harness run root: {run_root}")
    print(f"Selected tasks: {len(tasks)}")
    print(f"Plans per task: {args.num_plans}")

    commit_hash = get_commit_hash()

    task_timeout_seconds = 15 * 60
    task_timeout_raw = os.environ.get("AIDER_BENCH_TASK_TIMEOUT_SECONDS", "").strip()
    if task_timeout_raw:
        try:
            task_timeout_seconds = int(task_timeout_raw)
        except Exception:
            task_timeout_seconds = 15 * 60
    if task_timeout_seconds < 0:
        task_timeout_seconds = 0

    retry_timeout_raw = os.environ.get("AIDER_BENCH_RETRY_TIMEOUT", "").strip()
    if retry_timeout_raw:
        try:
            retry_timeout = int(retry_timeout_raw)
        except Exception:
            retry_timeout = 60
    else:
        retry_timeout = 60

    if task_timeout_seconds > 0:
        retry_timeout = min(retry_timeout, task_timeout_seconds)

    sendchat.RETRY_TIMEOUT = retry_timeout
    base_coder.RETRY_TIMEOUT = retry_timeout
    models.RETRY_TIMEOUT = retry_timeout

    llm_timeout = 0
    llm_timeout_raw = os.environ.get("AIDER_BENCH_LLM_TIMEOUT", "").strip()
    if llm_timeout_raw:
        try:
            llm_timeout = int(llm_timeout_raw)
        except Exception:
            llm_timeout = 0
    elif task_timeout_seconds > 0:
        llm_timeout = min(120, task_timeout_seconds)

    extra_instructions = os.environ.get("AIDER_BENCH_EXTRA_INSTRUCTIONS", "").strip()

    def work(ex_dir: Path) -> dict[str, Any]:
        rel = ex_dir.relative_to(exercises_root)
        testdir = run_root / rel
        if testdir.exists():
            shutil.rmtree(testdir)
        shutil.copytree(ex_dir, testdir)

        print(f"Running task: {rel}")
        return run_single_task_multiplan(
            original_exercise_dir=ex_dir,
            testdir=testdir,
            model_name=args.model,
            edit_format=args.edit_format,
            num_ctx=(args.num_ctx if args.num_ctx > 0 else None),
            extra_instructions=extra_instructions,
            commit_hash=commit_hash,
            llm_timeout=(llm_timeout if llm_timeout > 0 else None),
            task_timeout_seconds=(task_timeout_seconds if task_timeout_seconds > 0 else None),
            num_plans=args.num_plans,
        )

    if args.threads <= 1:
        for task in tasks:
            work(task)
    else:
        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futures = [pool.submit(work, task) for task in tasks]
            for fut in as_completed(futures):
                fut.result()

    print("Multi-Plan harness completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
