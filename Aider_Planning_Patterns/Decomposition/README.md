# Decomposition

Task decomposition variant for the same Aider benchmark pipeline used by Baseline.

This variant is implemented as a sidecar harness under `Decomposition/scripts/`.
It does not require editing upstream `benchmark/repos/aider/benchmark/benchmark.py`.
You can delete and reclone `benchmark/repos/aider` and decomposition still works.

This mode alternates between:
- planner call (reveal next sub-goal + immediate sub-plan)
- executor call(s) for only that immediate sub-plan
- repeat until done or max interleaved cycles reached

It still keeps:
- same model/provider setup
- same Docker execution environment
- same result collection flow (`Baseline/scripts/collect_results.py`)

The decomposition prompt is contract-first to reduce interface drift:
- preserve exact tested APIs/signatures/symbol names
- match payload keys and return format to tests
- avoid inventing alternate endpoints/schemas

## Run

```bash
bash shared/scripts/run_decomposition.sh
```

For a quick A/B check on the same small slice, run:

```bash
AIDER_BENCH_LANGUAGES=python \
AIDER_BENCH_KEYWORDS=book-store,go-counting,killer-sudoku-helper,matching-brackets,rest-api \
AIDER_BENCH_NUM_TESTS=5 \
bash shared/scripts/run_decomposition.sh
```

For deterministic (non-shuffled) task order:

```bash
AIDER_BENCH_SHUFFLE_TASKS=0 bash shared/scripts/run_decomposition.sh
```

## What it changes

- Uses sidecar runner `Decomposition/scripts/decomposition_harness.py` instead of patched upstream benchmark logic.
- Sets run variant name to `decomposition`.
- Writes summaries to `Decomposition/results/`.
- Runs interleaved planner and executor calls per task with max cycles from `AIDER_BENCH_ARCH_MAX_STEPS` (default `3`).
- Uses strict interleaved behavior: if the planner does not produce 1-2 actionable sub-steps, the cycle ends instead of falling back to broad execution.
- Adds one strict replan attempt when planner output is too generic/non-actionable.
- Compresses test-failure feedback to a short summary before the next attempt to prevent prompt bloat.
- Stops early after repeated no-op executor actions to avoid wasted cycles.
- Supports deterministic task order with `AIDER_BENCH_SHUFFLE_TASKS=0`.
- Injects decomposition guidance into task instructions:
  - reveal only next sub-goal(s)
  - create immediate sub-plan (1-2 actions)
  - execute, reassess, and continue

## Architectural Metrics

Each task result now includes:
- `arch_planning_enabled`
- `planner_calls`
- `executor_calls`
- `arch_plan_steps`
- `arch_interleaved_cycles`

## Outputs

- Run logs: `benchmark/runs/<timestamp>--decomposition--<model>/`
- Summaries: `Decomposition/results/<run_name>.json`, `.csv`, `.tasks.csv`

## Reclone Safety

If you reclone upstream Aider into `benchmark/repos/aider`, decomposition still runs as long as:
- `benchmark/repos/aider/benchmark/Dockerfile` exists
- `benchmark/repos/polyglot-benchmark` is present
- you invoke `bash shared/scripts/run_decomposition.sh`
