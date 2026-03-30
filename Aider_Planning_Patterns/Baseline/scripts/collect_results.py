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
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass
class RunSummary:
    run_name: str
    model: str
    timestamp: str
    task_count: int
    passed_count: int
    failed_count: int
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-name", required=True)
    ap.add_argument("--run-dir", required=True, help="Directory containing benchmark outputs (e.g., tmp.benchmarks)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--timestamp", default=None, help="ISO timestamp for the run (optional)")
    ap.add_argument("--duration-seconds", type=float, default=None, help="Total benchmark duration seconds (optional)")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()

    start = time.time()

    run_dir = Path(args.run_dir)
    result_files = find_result_files(run_dir)

    passed = 0
    failed = 0
    per_task: List[Dict[str, Any]] = []

    for p in result_files:
        ok, raw = parse_single_result(p)
        if ok:
            passed += 1
        else:
            failed += 1

        per_task.append(
            {
                "path": str(p.relative_to(run_dir)),
                "passed": ok,
                # Keep a small amount of raw info to help debugging.
                # TODO: expand if you need cost/tokens/duration fields.
                "raw_keys": sorted(list(raw.keys())) if isinstance(raw, dict) else [],
            }
        )

    parse_duration = time.time() - start

    summary = RunSummary(
        run_name=args.run_name,
        model=args.model,
        timestamp=args.timestamp or iso_timestamp(),
        task_count=len(result_files),
        passed_count=passed,
        failed_count=failed,
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

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
