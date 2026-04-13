#!/usr/bin/env python3
"""Reflection and refinement harness for Aider benchmark.

Implements iterative reflection-based planning:
1. Generate initial plan/solution
2. Evaluate (run tests)
3. If failed: Generate self-reflection on errors
4. Generate refinement feedback based on reflection
5. Refine the plan and retry
6. Repeat until success or max retries

Based on:
- Self-refine [Madaan et al., 2023]: generation → feedback → refinement
- Reflexion [Shinn et al., 2023]: self-reflections upon error detection
- CRITIC [Gou et al., 2023]: external validation and self-correction
"""

from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import random
import re
import shutil
import subprocess
import sys
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

REFLECTION_PROMPT = """
## Self-Reflection on Test Failures

You just attempted to solve a programming task but there were test failures. 
Analyze what went wrong and why.

**Your task:**
1. **Identify the exact failure**: What does the error message tell you?
2. **Trace the root cause**: 
   - Is it a logic error in your algorithm?
   - Did you misunderstand a requirement?
   - Is it an edge case or boundary condition you missed?
   - Is it a data type or format issue?
3. **Specific bugs found**: List the actual bugs in your code
4. **Connection to requirements**: How do your bugs relate to the original task requirements?
5. **Concrete fix strategy**: What specific code changes will fix each bug?

Focus on being precise and actionable. Identify the exact line(s) of code that need changing.
"""

REFINEMENT_PROMPT = """
## Refined Implementation Plan

Based on your reflection on the failures, create a detailed refinement plan:

**Step 1: Acknowledge the issue**
- Restate the bugs you identified
- Explain why each bug causes the tests to fail

**Step 2: Plan the fix**
- For each bug, describe exactly how to fix it
- Include any algorithm changes needed
- Consider edge cases and special conditions

**Step 3: Implementation**
- Apply all necessary fixes to the code
- Ensure your changes are complete and consistent
- Test mentally against the known failure cases

Now update the code to implement all the fixes.
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


def run_single_task(
    original_exercise_dir: Path,
    testdir: Path,
    model_name: str,
    edit_format: str,
    tries: int,
    num_ctx: Optional[int],
    extra_instructions: str,
    commit_hash: str,
    llm_timeout: Optional[int],
    task_timeout_seconds: Optional[int],
) -> dict[str, Any]:
    tracker = None
    task_emissions_kg = None
    task_energy_kwh = None
    if EmissionsTracker is not None:
        tracker = EmissionsTracker(save_to_file=False, log_level="error")
        tracker.start()

    history_fname = testdir / ".aider.chat.history.md"
    results_fname = testdir / ".aider.results.json"
    if results_fname.exists():
        return json.loads(results_fname.read_text(encoding="utf-8"))

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

    reflection_count = 0
    max_reflections = tries - 1  # Reserve one try for final attempt

    for attempt in range(tries):
        remaining_seconds = _seconds_left(task_deadline_ts)
        if remaining_seconds is not None and remaining_seconds <= 0:
            task_timed_out = True
            break

        call_timeout = _effective_call_timeout(remaining_seconds, llm_timeout)
        if call_timeout is None:
            task_timed_out = True
            break
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
                errors = "Task timed out (overall task deadline exceeded)!"
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

            # Reflection phase: Generate self-reflection and refinement feedback
            if reflection_count < max_reflections:
                reflection_count += 1
                
                # Log reflection step
                with history_fname.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n\n## Reflection Cycle {reflection_count}\n\n")
                
                # Generate self-reflection on the errors
                reflect_prompt = REFLECTION_PROMPT + "\n\nTest errors:\n```\n" + errors + "\n```"
                
                remaining_seconds = _seconds_left(task_deadline_ts)
                call_timeout = _effective_call_timeout(remaining_seconds, llm_timeout)
                if call_timeout is not None:
                    main_model.timeout = call_timeout
                
                # Use sendchat to get reflection (don't modify code)
                reflect_start = datetime.datetime.now().timestamp()
                try:
                    reflection_response = coder.chat(with_message=reflect_prompt)
                except Exception:
                    reflection_response = None
                duration += datetime.datetime.now().timestamp() - reflect_start
                
                if reflection_response:
                    with history_fname.open("a", encoding="utf-8") as fh:
                        fh.write(f"\n### LLM Reflection:\n\n{reflection_response}\n\n")
                
                # Generate refinement message combining original task + reflection + errors
                refinement_msg = (
                    f"{REFINEMENT_PROMPT}\n\n"
                    f"Original task:\n{instructions}\n\n"
                    f"Test errors:\n```\n{errors}\n```"
                )
                
                if reflection_response:
                    refinement_msg += f"\n\nYour previous analysis:\n{reflection_response}"
                
                current_instructions = refinement_msg
                current_instructions += TEST_FAILURES.format(file_list=file_list)
                if extra_instructions:
                    current_instructions += f"\n\n####\n\n{extra_instructions}\n"
                
                if task_timed_out:
                    break
            else:
                # No more reflections, use raw error feedback
                current_instructions = errors
                current_instructions += TEST_FAILURES.format(file_list=file_list)
                if extra_instructions:
                    current_instructions += f"\n\n####\n\n{extra_instructions}\n"

                if task_timed_out:
                    break
        else:
            test_outcomes.append(True)
            break

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
        "reasoning_effort": None,
        "prompt_tokens": coder.total_tokens_sent,
        "completion_tokens": coder.total_tokens_received,
        "thinking_tokens": None,
        "task_timed_out": task_timed_out,
        "task_timeout_seconds": task_timeout_seconds,
        "codecarbon_emissions_kg": task_emissions_kg,
        "codecarbon_energy_kwh": task_energy_kwh,
        "arch_planning_enabled": False,
        "arch_decomp_mode": "none",
        "planner_calls": 0,
        "executor_calls": len(chat_hashes),
        "arch_plan_steps": 0,
        "arch_interleaved_cycles": 0,
        "reflection_cycles": reflection_count,
        "chat_hashes": chat_hashes,
    }

    results_fname.write_text(json.dumps(results, indent=4) + "\n", encoding="utf-8")
    
    # Print result incrementally with status
    task_name = testdir.name
    passed = results.get("tests_outcomes") and results["tests_outcomes"][-1]
    status = "✓" if passed else "✗"
    print(f"{status} Task completed: {task_name} (attempts: {len([o for o in results.get('tests_outcomes', []) if o is not None])}, reflections: {reflection_count})", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--edit-format", default="whole")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--languages", default="")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--num-tests", type=int, default=-1)
    ap.add_argument("--num-ctx", type=int, default=0)
    ap.add_argument("--exercises-dir", default="polyglot-benchmark")
    ap.add_argument("--shuffle-tasks", type=int, default=1, choices=[0, 1])
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

    print(f"Reflection harness run root: {run_root}")
    print(f"Selected tasks: {len(tasks)}")

    commit_hash = get_commit_hash()
    
    # Prepare results CSV for incremental writing
    # Get absolute path - results go to workspace Reflection/results if AIDER_RESULTS_DIR not set
    results_dir_env = os.environ.get("AIDER_RESULTS_DIR", "").strip()
    if results_dir_env:
        results_dir = Path(results_dir_env)
    else:
        # Inside docker, /workspace is the mounted workspace
        # Try to find it, otherwise use relative path (will be relative to cwd)
        workspace = Path("/workspace")
        if workspace.exists():
            results_dir = workspace / "Reflection" / "results"
        else:
            results_dir = Path("Reflection/results")
    
    results_dir = results_dir.resolve()  # Make absolute
    results_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    model_safe = args.model.replace("/", "--")
    csv_base = results_dir / f"{timestamp}--reflection--{model_safe}"
    
    task_csv_path = csv_base.with_name(f"{csv_base.name}.tasks.csv")
    summary_json_path = csv_base.with_suffix(".json")
    summary_csv_path = csv_base.with_suffix(".csv")
    
    print(f"Results CSV (absolute path): {task_csv_path}")
    print(f"  Exists: {results_dir.exists()}, Writable: {os.access(results_dir, os.W_OK)}")

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
        return run_single_task(
            original_exercise_dir=ex_dir,
            testdir=testdir,
            model_name=args.model,
            edit_format=args.edit_format,
            tries=args.tries,
            num_ctx=args.num_ctx,
            extra_instructions=extra_instructions,
            commit_hash=commit_hash,
            llm_timeout=llm_timeout,
            task_timeout_seconds=task_timeout_seconds,
        )

    results = []
    csv_fieldnames = None
    csv_header_written = False
    
    with ThreadPoolExecutor(max_workers=args.threads) as executor:
        futures = [executor.submit(work, task) for task in tasks]
        completed_count = 0

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                completed_count += 1
                
                # Write result incrementally to task CSV
                if csv_fieldnames is None and result:
                    csv_fieldnames = list(result.keys())
                
                if csv_fieldnames and result:
                    # Write header on first result
                    if not csv_header_written:
                        with open(task_csv_path, "w", newline="") as f:
                            writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
                            writer.writeheader()
                            f.flush()
                            os.fsync(f.fileno())
                        csv_header_written = True
                    
                    # Append this result
                    with open(task_csv_path, "a", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=csv_fieldnames)
                        writer.writerow(result)
                        f.flush()
                        os.fsync(f.fileno())
                
                print(f"[{completed_count}/{len(tasks)}] Result collected and written to CSV", flush=True)
                sys.stdout.flush()
                sys.stderr.flush()
            except Exception as e:
                print(f"Task failed with exception: {e}", flush=True)
                sys.stderr.flush()

    passed_count = sum(1 for r in results if r["tests_outcomes"] and r["tests_outcomes"][-1])
    failed_count = sum(1 for r in results if not (r["tests_outcomes"] and r["tests_outcomes"][-1]))

    total_llm_calls = sum(len(r["chat_hashes"]) for r in results)
    total_tokens_sent = sum(r["prompt_tokens"] or 0 for r in results)
    total_tokens_received = sum(r["completion_tokens"] or 0 for r in results)
    total_cost = sum(r["cost"] or 0.0 for r in results)
    total_duration = sum(r["duration"] or 0.0 for r in results)
    total_emissions_kg = sum(r["codecarbon_emissions_kg"] or 0.0 for r in results)
    total_energy_kwh = sum(r["codecarbon_energy_kwh"] or 0.0 for r in results)

    print(f"\n=== Reflection Harness Results ===")
    print(f"Pass rate: {passed_count}/{len(tasks)} ({100*passed_count//len(tasks) if len(tasks) > 0 else 0}%)")
    print(f"LLM calls: {total_llm_calls}")
    print(f"Total duration: {total_duration:.1f}s")
    
    # Write summary JSON
    summary = {
        "run_name": args.run_name,
        "model": args.model,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
        "task_count": len(tasks),
        "passed_count": passed_count,
        "failed_count": failed_count,
        "llm_calls_total": total_llm_calls,
        "codecarbon_energy_kwh_total": total_energy_kwh,
        "codecarbon_emissions_kg_total": total_emissions_kg,
        "duration_seconds": total_duration,
        "output_path": str(run_root),
    }
    
    with open(summary_json_path, "w") as f:
        json.dump(summary, f, indent=2)
    
    # Write summary CSV
    with open(summary_csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=summary.keys())
        writer.writeheader()
        writer.writerow(summary)
    
    print(f"\nResults saved to:")
    print(f"  Task CSV: {task_csv_path}")
    print(f"  Summary JSON: {summary_json_path}")
    print(f"  Summary CSV: {summary_csv_path}")

    print("Reflection harness completed")
    return 0


if __name__ == "__main__":
    exit(main())
