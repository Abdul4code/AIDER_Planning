#!/usr/bin/env python3
"""Standalone decomposition harness.

Runs interleaved planner/executor decomposition against polyglot benchmark tasks
without modifying upstream Aider benchmark code.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
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

MAX_TASK_TIMEOUT_SECONDS = 15 * 60

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

GENERIC_ACTION_PATTERNS = [
    r"^\s*(review|understand|analy[sz]e|inspect|consider|explore)\b",
    r"^\s*(identify|determine)\b.*\b(requirements|problem|issue|steps?)\b",
]

NON_CODE_ACTION_PATTERNS = [
    r"\b(run|execute)\b.*\b(test|pytest|unittest|spec)\b",
    r"\b(create|add|write|update|modify)\b.*\btest(s| cases?)?\b",
    r"\btest\s+the\b",
]


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _extract_plan_steps(plan_text: str, max_steps: int = 5) -> list[str]:
    if not plan_text:
        return []

    candidates = [plan_text]
    fenced_json = re.findall(r"```(?:json)?\s*(.*?)```", plan_text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced_json)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue

        items: list[Any] = []
        if isinstance(parsed, dict):
            items = parsed.get("plan") or parsed.get("steps") or parsed.get("sub_plan") or []
        elif isinstance(parsed, list):
            items = parsed

        steps: list[str] = []
        for item in items:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    steps.append(s)
            elif isinstance(item, dict):
                s = (
                    str(item.get("instruction") or item.get("step") or item.get("action") or "")
                    .strip()
                )
                if s:
                    steps.append(s)

            if len(steps) >= max_steps:
                return steps[:max_steps]

        if steps:
            return steps[:max_steps]

    line_steps: list[str] = []
    for line in plan_text.splitlines():
        match = re.match(r"^\s*(?:\d+[\)\.\:\-]|[-*])\s+(.*\S)\s*$", line)
        if match:
            line_steps.append(match.group(1).strip())
        if len(line_steps) >= max_steps:
            return line_steps[:max_steps]

    return line_steps[:max_steps]


def _extract_interleaved_step(plan_text: str, max_actions: int = 2) -> tuple[str, list[str], bool]:
    if not plan_text:
        return "", [], True

    candidates = [plan_text]
    fenced_json = re.findall(r"```(?:json)?\s*(.*?)```", plan_text, flags=re.DOTALL | re.IGNORECASE)
    candidates.extend(fenced_json)

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue

        sub_goal = ""
        should_continue = True
        items: list[Any] = []

        if isinstance(parsed, dict):
            sub_goal = str(parsed.get("sub_goal") or parsed.get("goal") or "").strip()
            if "continue" in parsed:
                should_continue = bool(parsed.get("continue"))
            items = parsed.get("sub_plan") or parsed.get("plan") or parsed.get("steps") or []
        elif isinstance(parsed, list):
            items = parsed

        actions: list[str] = []
        for item in items:
            if isinstance(item, str):
                s = item.strip()
                if s:
                    actions.append(s)
            elif isinstance(item, dict):
                s = (
                    str(item.get("instruction") or item.get("step") or item.get("action") or "")
                    .strip()
                )
                if s:
                    actions.append(s)

            if len(actions) >= max_actions:
                return sub_goal, actions[:max_actions], should_continue

        if actions or sub_goal:
            return sub_goal, actions[:max_actions], should_continue

    actions = _extract_plan_steps(plan_text, max_steps=max_actions)
    lowered = plan_text.lower()
    should_continue = not any(tok in lowered for tok in ["done", "complete", "no further steps"])
    return "", actions, should_continue


def _is_actionable_instruction(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if len(t.split()) < 4:
        return False
    lowered = t.lower()
    for pattern in NON_CODE_ACTION_PATTERNS:
        if re.search(pattern, lowered):
            return False

    has_code_cue = any(
        cue in lowered
        for cue in [".py", ".rs", ".go", ".js", ".cpp", ".java", "function", "class", "method", "symbol", "file", "return", "input", "output"]
    ) or "`" in t
    for pattern in GENERIC_ACTION_PATTERNS:
        if re.search(pattern, lowered) and not has_code_cue:
            return False
    return True


def _filter_actionable_actions(actions: list[str], max_actions: int = 2) -> list[str]:
    filtered: list[str] = []
    for action in actions:
        if _is_actionable_instruction(action):
            filtered.append(action)
        if len(filtered) >= max_actions:
            break
    return filtered


def _summarize_test_errors(errors: str, max_lines: int = 12, max_chars: int = 1800) -> str:
    lines = [ln.rstrip() for ln in errors.splitlines() if ln.strip()]
    priority = []
    for ln in lines:
        l = ln.lower()
        if any(tok in l for tok in ["assert", "error", "failed", "traceback", "expected", "got", "timeout"]):
            priority.append(ln)

    selected = priority[:max_lines]
    if not selected:
        selected = lines[:max_lines]

    summary = "\n".join(selected)
    if len(summary) > max_chars:
        summary = summary[:max_chars] + "\n...[truncated]"
    return summary


def _file_sha256(path: Path) -> str:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest()


def _snapshot_files(paths: list[Path]) -> dict[str, str]:
    snap: dict[str, str] = {}
    for p in paths:
        key = str(p)
        if p.exists() and p.is_file():
            snap[key] = _file_sha256(p)
        else:
            snap[key] = "<missing>"
    return snap


def _restore_protected_files(
    original_exercise_dir: Path,
    testdir: Path,
    protected_files: list[str],
) -> list[str]:
    restored: list[str] = []
    for rel in protected_files:
        src = original_exercise_dir / rel
        dst = testdir / rel
        if not src.exists() or not src.is_file():
            continue

        if dst.exists() and dst.is_file() and _file_sha256(src) == _file_sha256(dst):
            continue

        os.makedirs(dst.parent, exist_ok=True)
        shutil.copy(src, dst)
        restored.append(rel)
    return restored


ENFORCED_CODE_FILE_EXTS = {
    ".py", ".rs", ".go", ".js", ".jsx", ".ts", ".tsx", ".cpp", ".cc", ".c", ".h", ".hpp", ".java"
}


def _enforce_solution_only_writes(
    original_exercise_dir: Path,
    testdir: Path,
    allowed_solution_files: set[str],
) -> list[str]:
    reverted: list[str] = []

    for dst in testdir.rglob("*"):
        if not dst.is_file():
            continue

        rel = str(dst.relative_to(testdir))
        if rel.startswith(".aider."):
            continue
        if Path(rel).suffix.lower() not in ENFORCED_CODE_FILE_EXTS:
            continue
        if rel in allowed_solution_files:
            continue

        src = original_exercise_dir / rel
        if src.exists() and src.is_file():
            if _file_sha256(src) != _file_sha256(dst):
                os.makedirs(dst.parent, exist_ok=True)
                shutil.copy(src, dst)
                reverted.append(rel)
        else:
            dst.unlink(missing_ok=True)
            reverted.append(rel)

    for src in original_exercise_dir.rglob("*"):
        if not src.is_file():
            continue

        rel = str(src.relative_to(original_exercise_dir))
        if rel.startswith(".aider."):
            continue
        if Path(rel).suffix.lower() not in ENFORCED_CODE_FILE_EXTS:
            continue
        if rel in allowed_solution_files:
            continue

        dst = testdir / rel
        if not dst.exists():
            os.makedirs(dst.parent, exist_ok=True)
            shutil.copy(src, dst)
            reverted.append(rel)

    return sorted(set(reverted))


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
    extra_planning_instructions: str,
    arch_max_steps: int,
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
    protected_files = sorted(set(test_files + example_files))

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
    allowed_solution_relpaths = {str(p.relative_to(testdir)) for p in fnames}

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
    if extra_planning_instructions:
        instructions += f"\n\n####\n\n{extra_planning_instructions}\n"

    io = InputOutput(pretty=False, yes=True, chat_history_file=history_fname)
    planner_history_fname = testdir / ".aider.planner.history.md"
    planner_io = InputOutput(pretty=False, yes=True, chat_history_file=planner_history_fname)
    main_model = models.Model(model_name, weak_model=None, editor_model=None, editor_edit_format=None, verbose=False)

    if num_ctx:
        if not main_model.extra_params:
            main_model.extra_params = {}
        main_model.extra_params["num_ctx"] = num_ctx

    if llm_timeout and llm_timeout > 0:
        main_model.timeout = llm_timeout

    actual_edit_format = edit_format or main_model.edit_format
    # IMPORTANT: Only pass solution files to coders, never test/example files.
    # This prevents model confusion about which files are editable.
    # Test files are only used for validation, not given to the model.
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

    planner_coder = Coder.create(
        main_model,
        "ask",
        planner_io,
        fnames=fnames,
        use_git=False,
        stream=False,
        verbose=False,
        cache_prompts=True,
        suggest_shell_commands=False,
        ignore_mentions=ignore_files,
    )
    planner_coder.get_file_mentions = lambda _: set()

    timeouts = 0
    syntax_errors = 0
    indentation_errors = 0
    lazy_comments = 0
    planner_calls = 0
    executor_calls = 0
    arch_plan_steps = 0
    arch_interleaved_cycles = 0
    noop_stop_threshold = int(os.environ.get("AIDER_BENCH_DECOMP_NOOP_STOP_THRESHOLD", "3"))
    enable_repair = _env_flag("AIDER_BENCH_DECOMP_REPAIR", False)

    duration = 0.0
    test_outcomes: list[bool] = []
    current_instructions = instructions
    task_timed_out = False
    task_deadline_ts: Optional[float] = None
    if task_timeout_seconds and task_timeout_seconds > 0:
        task_deadline_ts = datetime.datetime.now().timestamp() + task_timeout_seconds

    for _ in range(tries):
        remaining_seconds = _seconds_left(task_deadline_ts)
        if remaining_seconds is not None and remaining_seconds <= 0:
            task_timed_out = True
            break

        start = datetime.datetime.now().timestamp()
        responses: list[str] = []
        interleaved_memory: list[str] = []
        no_op_action_streak = 0

        for cycle in range(1, arch_max_steps + 1):
            remaining_seconds = _seconds_left(task_deadline_ts)
            if remaining_seconds is not None and remaining_seconds <= 0:
                task_timed_out = True
                break

            call_timeout = _effective_call_timeout(remaining_seconds, llm_timeout)
            if call_timeout is None:
                task_timed_out = True
                break
            main_model.timeout = call_timeout

            arch_interleaved_cycles += 1
            planner_prompt = current_instructions + "\n\n####\n\n"
            planner_prompt += (
                "Interleaved decomposition mode. Reveal only the next sub-goal and exactly one "
                'immediate code-edit action. Return JSON with this exact schema: '
                '{"sub_goal": "...", "sub_plan": [{"instruction": "..."}], "continue": true}. '
                "Do not include markdown fences. Do not modify files in this planning call."
            )
            if interleaved_memory:
                planner_prompt += "\n\nProgress so far:\n" + "\n".join(interleaved_memory[-8:])

            planner_response = planner_coder.run(with_message=planner_prompt, preproc=False)
            planner_calls += 1
            responses.append(planner_response)

            sub_goal, cycle_actions, should_continue = _extract_interleaved_step(planner_response, max_actions=1)
            cycle_actions = _filter_actionable_actions(cycle_actions, max_actions=1)

            if not cycle_actions and should_continue and enable_repair:
                # One strict replan attempt when planner output is too generic/non-actionable.
                repair_prompt = planner_prompt + "\n\nYour previous sub-plan was not actionable. " \
                    "Return only concrete code-edit actions tied to specific symbols/files."
                planner_response = planner_coder.run(with_message=repair_prompt, preproc=False)
                planner_calls += 1
                responses.append(planner_response)
                sub_goal, cycle_actions, should_continue = _extract_interleaved_step(
                    planner_response, max_actions=1
                )
                cycle_actions = _filter_actionable_actions(cycle_actions, max_actions=1)

            arch_plan_steps += len(cycle_actions)

            if not cycle_actions:
                if not should_continue:
                    break
                # If should_continue is True, loop continues to next cycle to ask planner again
                continue

            for action_idx, action_instruction in enumerate(cycle_actions, start=1):
                remaining_seconds = _seconds_left(task_deadline_ts)
                if remaining_seconds is not None and remaining_seconds <= 0:
                    task_timed_out = True
                    should_continue = False
                    break

                call_timeout = _effective_call_timeout(remaining_seconds, llm_timeout)
                if call_timeout is None:
                    task_timed_out = True
                    should_continue = False
                    break
                main_model.timeout = call_timeout

                before_snap = _snapshot_files(fnames)
                step_message = (
                    f"Interleaved cycle {cycle}/{arch_max_steps}.\\n"
                    f"Current sub-goal: {sub_goal or 'N/A'}\\n"
                    f"Sub-plan action {action_idx}/{len(cycle_actions)}: {action_instruction}\\n\\n"
                    "Apply code edits only for this action. Keep changes minimal and aligned with "
                    "exercise requirements and test contract. Do not create new files. "
                    "Do not edit tests, examples, metadata, or docs files."
                )
                step_response = coder.run(with_message=step_message, preproc=False)
                executor_calls += 1
                responses.append(step_response)
                interleaved_memory.append(f"cycle {cycle} action {action_idx}: {action_instruction}")

                restored = _restore_protected_files(original_exercise_dir, testdir, protected_files)
                if restored:
                    interleaved_memory.append(
                        f"cycle {cycle} action {action_idx}: restored protected files ({', '.join(restored[:3])})"
                    )

                reverted_non_solution = _enforce_solution_only_writes(
                    original_exercise_dir,
                    testdir,
                    allowed_solution_relpaths,
                )
                if reverted_non_solution:
                    interleaved_memory.append(
                        f"cycle {cycle} action {action_idx}: reverted non-solution edits ({', '.join(reverted_non_solution[:3])})"
                    )

                after_snap = _snapshot_files(fnames)
                if before_snap == after_snap:
                    no_op_action_streak += 1
                else:
                    no_op_action_streak = 0

                if noop_stop_threshold > 0 and no_op_action_streak >= noop_stop_threshold:
                    interleaved_memory.append(
                        f"cycle {cycle}: stopping after {no_op_action_streak} consecutive no-op actions"
                    )
                    should_continue = False
                    break

                if coder.last_keyboard_interrupt:
                    raise KeyboardInterrupt

            if not should_continue:
                break

        if task_timed_out:
            duration += datetime.datetime.now().timestamp() - start
            test_outcomes.append(False)
            break

        response = "\n\n".join(r for r in responses if r)
        duration += datetime.datetime.now().timestamp() - start

        pattern = r"^[+]? *[#].* [.][.][.] "
        lazy_comments += len(re.findall(pattern, response, re.MULTILINE))

        if coder.last_keyboard_interrupt:
            raise KeyboardInterrupt

        try:
            _restore_protected_files(original_exercise_dir, testdir, protected_files)
            _enforce_solution_only_writes(original_exercise_dir, testdir, allowed_solution_relpaths)
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

            error_summary = _summarize_test_errors(errors)
            current_instructions = "Test failure summary:\n" + error_summary + "\n"
            current_instructions += TEST_FAILURES.format(file_list=file_list)
            if extra_planning_instructions:
                current_instructions += f"\n\n####\n\n{extra_planning_instructions}\n"

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
            task_emissions_kg = task_emissions_kg

    planner_cost = planner_coder.total_cost
    planner_prompt_tokens = planner_coder.total_tokens_sent
    planner_completion_tokens = planner_coder.total_tokens_received

    planner_chat_hashes = list(
        zip(
            planner_coder.chat_completion_call_hashes,
            planner_coder.chat_completion_response_hashes,
        )
    )
    executor_chat_hashes = list(
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
        "cost": coder.total_cost + planner_cost,
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
        "prompt_tokens": coder.total_tokens_sent + planner_prompt_tokens,
        "completion_tokens": coder.total_tokens_received + planner_completion_tokens,
        "thinking_tokens": None,
        "task_timed_out": task_timed_out,
        "task_timeout_seconds": task_timeout_seconds,
        "codecarbon_emissions_kg": task_emissions_kg,
        "codecarbon_energy_kwh": task_energy_kwh,
        "arch_planning_enabled": True,
        "arch_decomp_mode": "interleaved",
        "planner_calls": planner_calls,
        "executor_calls": executor_calls,
        "arch_plan_steps": arch_plan_steps,
        "arch_interleaved_cycles": arch_interleaved_cycles,
        "chat_hashes": planner_chat_hashes + executor_chat_hashes,
    }

    results_fname.write_text(json.dumps(results, indent=4) + "\n", encoding="utf-8")
    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--edit-format", default="whole")
    ap.add_argument("--threads", type=int, default=1)
    ap.add_argument("--tries", type=int, default=2)
    ap.add_argument("--languages", default="")
    ap.add_argument("--keywords", default="")
    ap.add_argument("--num-tests", type=int, default=-1)
    ap.add_argument("--num-ctx", type=int, default=0)
    ap.add_argument("--exercises-dir", default="polyglot-benchmark")
    
    # Read default arch_max_steps from environment, default to 4 if not set
    default_arch_max_steps = 4
    env_arch_max = os.environ.get("AIDER_BENCH_DECOMP_MAX_STEPS", "").strip()
    if env_arch_max:
        try:
            default_arch_max_steps = int(env_arch_max)
        except (ValueError, TypeError):
            default_arch_max_steps = 4
    
    ap.add_argument("--arch-max-steps", type=int, default=default_arch_max_steps)
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

    print(f"Decomposition harness run root: {run_root}")
    print(f"Selected tasks: {len(tasks)}")

    commit_hash = get_commit_hash()

    task_timeout_seconds = MAX_TASK_TIMEOUT_SECONDS
    task_timeout_raw = os.environ.get("AIDER_BENCH_TASK_TIMEOUT_SECONDS", "").strip()
    if task_timeout_raw:
        try:
            task_timeout_seconds = int(task_timeout_raw)
        except Exception:
            task_timeout_seconds = MAX_TASK_TIMEOUT_SECONDS

    if task_timeout_seconds <= 0:
        print(
            f"Invalid AIDER_BENCH_TASK_TIMEOUT_SECONDS={task_timeout_seconds}. "
            f"Using hard cap {MAX_TASK_TIMEOUT_SECONDS}s."
        )
        task_timeout_seconds = MAX_TASK_TIMEOUT_SECONDS
    elif task_timeout_seconds > MAX_TASK_TIMEOUT_SECONDS:
        print(
            f"AIDER_BENCH_TASK_TIMEOUT_SECONDS={task_timeout_seconds} exceeds hard cap "
            f"{MAX_TASK_TIMEOUT_SECONDS}s. Capping to {MAX_TASK_TIMEOUT_SECONDS}s."
        )
        task_timeout_seconds = MAX_TASK_TIMEOUT_SECONDS

    retry_timeout_raw = os.environ.get("AIDER_BENCH_RETRY_TIMEOUT", "").strip()
    if retry_timeout_raw:
        try:
            retry_timeout = int(retry_timeout_raw)
        except Exception:
            retry_timeout = 60
    else:
        # Keep retries short by default so one bad request can't stall a task for too long.
        retry_timeout = 60

    if task_timeout_seconds > 0:
        retry_timeout = min(retry_timeout, task_timeout_seconds)
    retry_timeout = min(retry_timeout, 15)

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
        # Bound single model calls so tasks can honor their wall-clock cap.
        llm_timeout = min(120, task_timeout_seconds)

    if llm_timeout > 0:
        llm_timeout = min(llm_timeout, 30)

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
            num_ctx=(args.num_ctx if args.num_ctx > 0 else None),
            extra_planning_instructions=extra_instructions,
            arch_max_steps=max(1, args.arch_max_steps),
            commit_hash=commit_hash,
            llm_timeout=(llm_timeout if llm_timeout > 0 else None),
            task_timeout_seconds=(task_timeout_seconds if task_timeout_seconds > 0 else None),
        )

    if args.threads <= 1:
        for task in tasks:
            work(task)
    else:
        with ThreadPoolExecutor(max_workers=args.threads) as pool:
            futures = [pool.submit(work, task) for task in tasks]
            for fut in as_completed(futures):
                fut.result()

    print("Decomposition harness completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
