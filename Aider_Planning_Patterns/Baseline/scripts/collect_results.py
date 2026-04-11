#!/usr/bin/env python3
"""Collect and summarize an Aider benchmark run.

This script is designed for the Aider benchmark harness located in the Aider repo's
`benchmark/` folder. The harness writes per-task JSON files named `.aider.results.json`
inside each exercise directory.

We keep parsing conservative and robust because upstream formats can evolve.

Outputs:
- JSON summary (single object)
- CSV summary (single row)

TODO:
- If upstream changes file names/locations, update `find_result_files()`.
- If upstream schema changes, update `parse_single_result()`.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class RunSummary:
    run_name: str
    model: str
    timestamp: str
    task_count: int
    passed_count: int
    failed_count: int
    llm_calls_total: int
    codecarbon_energy_kwh_total: float
    codecarbon_emissions_kg_total: float
    duration_seconds: float
    output_path: str


def iso_timestamp() -> str:
    # Using local time for convenience
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def find_result_files(run_dir: Path) -> List[Path]:
    """Find `.aider.results.json` files under a run directory.

    The canonical location used by the harness (as of today) is something like:
    <run>/exercises/practice/**/.aider.results.json

    We search the entire tree to be tolerant.
    """

    if not run_dir.exists():
        raise FileNotFoundError(f"run_dir does not exist: {run_dir}")

    return sorted(run_dir.rglob(".aider.results.json"))


def _boolish(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"true", "yes", "y", "1", "pass", "passed"}:
            return True
        if v in {"false", "no", "n", "0", "fail", "failed"}:
            return False
    return None


def parse_single_result(path: Path) -> Tuple[bool, Dict[str, Any]]:
    """Parse one `.aider.results.json` file.

    Returns:
      (passed, raw_dict)

    Passing detection is intentionally defensive:
    - Prefer known keys (`passed`, `pass`, `tests_passed`, etc.)
    - Fall back to `exit_code == 0` when present

    TODO: adjust heuristics if upstream schema differs.
    """

    raw = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(raw, dict) and isinstance(raw.get("tests_outcomes"), list):
        outcomes = [_boolish(v) for v in raw.get("tests_outcomes", [])]
        outcomes = [v for v in outcomes if v is not None]
        if outcomes:
            return any(outcomes), raw

    # Common-ish keys observed across tools
    for key in [
        "passed",
        "pass",
        "tests_passed",
        "test_passed",
        "all_tests_passed",
        "success",
    ]:
        if key in raw:
            b = _boolish(raw.get(key))
            if b is not None:
                return b, raw

    if "exit_code" in raw:
        try:
            return int(raw["exit_code"]) == 0, raw
        except Exception:
            pass

    # Last resort: treat missing as failed (conservative)
    return False, raw


def write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_csv(path: Path, row: RunSummary) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames = list(asdict(row).keys())
    is_new = not path.exists()

    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(asdict(row))


def _task_row(run_dir: Path, result_path: Path, raw: Dict[str, Any], passed: bool) -> Dict[str, Any]:
    outcomes = raw.get("tests_outcomes") if isinstance(raw, dict) else None
    outcomes = outcomes if isinstance(outcomes, list) else []
    chat_hashes = raw.get("chat_hashes") if isinstance(raw, dict) else None
    chat_hashes = chat_hashes if isinstance(chat_hashes, list) else []
    true_count = sum(1 for v in outcomes if _boolish(v) is True)
    false_count = sum(1 for v in outcomes if _boolish(v) is False)

    return {
        "task_path": str(result_path.relative_to(run_dir)),
        "testcase": raw.get("testcase", ""),
        "passed": passed,
        "arch_planning_enabled": raw.get("arch_planning_enabled", ""),
        "planner_calls": raw.get("planner_calls", ""),
        "executor_calls": raw.get("executor_calls", ""),
        "arch_plan_steps": raw.get("arch_plan_steps", ""),
        "tries_recorded": len(outcomes),
        "tries_passed": true_count,
        "tries_failed": false_count,
        "llm_calls": len(chat_hashes),
        "duration_seconds": raw.get("duration", ""),
        "prompt_tokens": raw.get("prompt_tokens", ""),
        "completion_tokens": raw.get("completion_tokens", ""),
        "num_error_outputs": raw.get("num_error_outputs", ""),
        "num_malformed_responses": raw.get("num_malformed_responses", ""),
        "num_exhausted_context_windows": raw.get("num_exhausted_context_windows", ""),
        "test_timeouts": raw.get("test_timeouts", ""),
        "codecarbon_energy_kwh": raw.get("codecarbon_energy_kwh", ""),
        "codecarbon_emissions_kg": raw.get("codecarbon_emissions_kg", ""),
    }


def write_task_rows(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "task_path",
        "testcase",
        "passed",
        "arch_planning_enabled",
        "planner_calls",
        "executor_calls",
        "arch_plan_steps",
        "tries_recorded",
        "tries_passed",
        "tries_failed",
        "llm_calls",
        "duration_seconds",
        "prompt_tokens",
        "completion_tokens",
        "num_error_outputs",
        "num_malformed_responses",
        "num_exhausted_context_windows",
        "test_timeouts",
        "codecarbon_energy_kwh",
        "codecarbon_emissions_kg",
    ]

    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            w.writerow(row)


def append_task_row(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "task_path",
        "testcase",
        "passed",
        "arch_planning_enabled",
        "planner_calls",
        "executor_calls",
        "arch_plan_steps",
        "tries_recorded",
        "tries_passed",
        "tries_failed",
        "llm_calls",
        "duration_seconds",
        "prompt_tokens",
        "completion_tokens",
        "num_error_outputs",
        "num_malformed_responses",
        "num_exhausted_context_windows",
        "test_timeouts",
        "codecarbon_energy_kwh",
        "codecarbon_emissions_kg",
    ]
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        if is_new:
            w.writeheader()
        w.writerow(row)


def stream_task_rows(run_dir: Path, out_task_csv: Path, poll_interval: float, stop_file: Optional[Path]) -> int:
    seen: set[str] = set()
    while True:
        if stop_file and stop_file.exists():
            return 0

        try:
            files = find_result_files(run_dir)
        except FileNotFoundError:
            files = []

        for p in files:
            key = str(p)
            if key in seen:
                continue
            try:
                passed, raw = parse_single_result(p)
            except Exception:
                continue
            append_task_row(out_task_csv, _task_row(run_dir, p, raw, passed))
            seen.add(key)

        time.sleep(max(0.5, poll_interval))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--run-dir", required=True, help="Directory containing benchmark outputs (e.g., tmp.benchmarks)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--timestamp", default=None, help="ISO timestamp for the run (optional)")
    ap.add_argument("--duration-seconds", type=float, default=None, help="Total benchmark duration seconds (optional)")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--out-task-csv", default=None, help="Optional per-task CSV output path")
    ap.add_argument(
        "--stream-task-csv",
        action="store_true",
        help="Watch run-dir and append one CSV row per completed task as .aider.results.json files appear",
    )
    ap.add_argument("--poll-interval", type=float, default=2.0, help="Poll interval seconds for --stream-task-csv")
    ap.add_argument("--stop-file", default=None, help="Optional stop-file path to terminate --stream-task-csv mode")
    args = ap.parse_args()

    run_dir = Path(args.run_dir)

    if args.stream_task_csv:
        if not args.out_task_csv:
            raise SystemExit("--stream-task-csv requires --out-task-csv")
        return stream_task_rows(
            run_dir=run_dir,
            out_task_csv=Path(args.out_task_csv),
            poll_interval=args.poll_interval,
            stop_file=Path(args.stop_file) if args.stop_file else None,
        )

    start = time.time()
    result_files = find_result_files(run_dir)

    passed = 0
    failed = 0
    llm_calls_total = 0
    codecarbon_energy_kwh_total = 0.0
    codecarbon_emissions_kg_total = 0.0
    per_task: List[Dict[str, Any]] = []
    task_rows: List[Dict[str, Any]] = []

    for p in result_files:
        ok, raw = parse_single_result(p)
        if ok:
            passed += 1
        else:
            failed += 1

        if isinstance(raw, dict):
            chat_hashes = raw.get("chat_hashes")
            if isinstance(chat_hashes, list):
                llm_calls_total += len(chat_hashes)
            try:
                energy = raw.get("codecarbon_energy_kwh")
                if energy is not None:
                    codecarbon_energy_kwh_total += float(energy)
            except Exception:
                pass
            try:
                emissions = raw.get("codecarbon_emissions_kg")
                if emissions is not None:
                    codecarbon_emissions_kg_total += float(emissions)
            except Exception:
                pass

        per_task.append(
            {
                "path": str(p.relative_to(run_dir)),
                "passed": ok,
                # Keep a small amount of raw info to help debugging.
                # TODO: expand if you need cost/tokens/duration fields.
                "raw_keys": sorted(list(raw.keys())) if isinstance(raw, dict) else [],
            }
        )
        if isinstance(raw, dict):
            task_rows.append(_task_row(run_dir, p, raw, ok))

    parse_duration = time.time() - start

    summary = RunSummary(
        run_name=args.run_name,
        model=args.model,
        timestamp=args.timestamp or iso_timestamp(),
        task_count=len(result_files),
        passed_count=passed,
        failed_count=failed,
        llm_calls_total=llm_calls_total,
        codecarbon_energy_kwh_total=codecarbon_energy_kwh_total,
        codecarbon_emissions_kg_total=codecarbon_emissions_kg_total,
        duration_seconds=float(args.duration_seconds) if args.duration_seconds is not None else parse_duration,
        output_path=str(run_dir.resolve()),
    )

    summary_obj: Dict[str, Any] = asdict(summary)
    summary_obj["per_task"] = per_task
    summary_obj["notes"] = {
        "parser": "collect_results.py",
        "todo": "If upstream outputs change, update find_result_files() and parse_single_result().",
        "parse_duration_seconds": parse_duration,
    }

    write_json(Path(args.out_json), summary_obj)
    write_csv(Path(args.out_csv), summary)
    if args.out_task_csv:
        write_task_rows(Path(args.out_task_csv), task_rows)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
