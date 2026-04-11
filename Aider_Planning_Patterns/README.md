# Aider_Planning_Patterns

Local, reproducible experiment workspace for **Aider benchmark code-editing tasks only**, starting with the **original Baseline**.

This repo intentionally does **not** include SWE-bench, SWE-agent, or any other benchmarks.

## Folder layout

- `shared/` – common scripts/config used by all variants
- `benchmark/` – benchmark repos + run artifacts
- `Baseline/` – baseline runner + result summaries
- `Memory/`, `Decomposition/`, `Reflection/`, `MultiPlan/` – placeholders for future variants

## Prerequisites

Required tools:
- Python 3 (for local scripts)
- Git (to clone benchmark repos)
- Docker (Aider benchmark runs in Docker)
- Ollama (local model server)

Check them:

```bash
bash shared/scripts/check_prereqs.sh
```

## Install dependencies (local scripts)

```bash
bash shared/scripts/setup_env.sh
```

This creates `.venv/` and installs dependencies for result collection.

## Configure

1. Copy env template:

```bash
cp .env.example .env
```

2. Edit model + base URL:

- `OLLAMA_MODEL` (example: `qwen2.5-coder:14b`)
- `OLLAMA_API_BASE` (example: `http://127.0.0.1:11434`)

## Start Ollama

In a separate terminal:

```bash
ollama serve
```

Then pull your model (if needed):

```bash
ollama pull qwen2.5-coder:14b
```

## Benchmark setup

Clones the required repos into `benchmark/repos/`:

```bash
bash shared/scripts/setup_benchmark.sh
```

This will clone:
- `Aider-AI/aider` (contains the benchmark harness in `benchmark/`)
- `Aider-AI/polyglot-benchmark` (exercise corpus)

## Run Baseline experiment

Single command:

```bash
bash shared/scripts/run_baseline.sh
```

What it does:
1. Loads env vars (`shared/config/defaults.env` + optional `.env`)
2. Validates Ollama connectivity
3. Validates benchmark repos are present
4. Creates a timestamped run directory under `benchmark/runs/`
5. Runs the Baseline sidecar harness in Docker (using Ollama + upstream Aider package)
6. Captures stdout/stderr logs
7. Writes a summary to `Baseline/results/`

For deterministic (non-shuffled) task order:

```bash
AIDER_BENCH_SHUFFLE_TASKS=0 bash shared/scripts/run_baseline.sh
```

## Logs & results

- Run directory: `benchmark/runs/<timestamp>--baseline-<model>/`
  - `run.log` / `run.err.log`
  - `tmp.benchmarks/` (benchmark output tree)
- Summaries: `Baseline/results/<run_name>.json` and `.csv`

## Notes / TODOs

- Baseline and Decomposition both use sidecar harnesses in this repo and avoid patching upstream Aider.
- If upstream Aider internals change substantially, update:
  - `Baseline/scripts/baseline_harness.py`
  - `Decomposition/scripts/decomposition_harness.py`
  - `Baseline/scripts/collect_results.py`

