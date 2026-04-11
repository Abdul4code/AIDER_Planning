# Baseline

Baseline = **plain single-pass execution** with a local Ollama model.
It is implemented as a sidecar harness in `Baseline/scripts/baseline_harness.py`
so upstream `benchmark/repos/aider` can be recloned without local code edits.

No memory, reflection, decomposition, or multi-plan logic.

Deterministic (non-shuffled) task order:

```bash
AIDER_BENCH_SHUFFLE_TASKS=0 bash shared/scripts/run_baseline.sh
```

Outputs:
- Raw benchmark run data: `benchmark/runs/<timestamp>--baseline-*/`
- Summaries: `Baseline/results/`
