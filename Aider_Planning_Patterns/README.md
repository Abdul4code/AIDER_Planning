# Aider_Planning_Patterns

Local, reproducible experiment workspace for **Aider benchmark code-editing tasks only**, starting with the **original Baseline**.

This repo intentionally does **not** include SWE-bench, SWE-agent, or any other benchmarks.

## Folder layout

- `shared/` – common scripts/config used by all variants
- `benchmark/` – benchmark repos + run artifacts
- `Baseline/` – baseline runner + result summaries
- `MultiPlan/` – **multi-plan selection variant** (research-grade implementation)
- `Memory/`, `Decomposition/`, `Reflection/` – placeholders for future variants

## Operationalization Overview

**Five Planning Patterns operationalized with reproducible specifications:**

| Pattern | Operationalization | Model Config | Prompt Strategy | Invocations | Notes |
|---------|-------------------|--------------|-----------------|-------------|-------|
| **Baseline** | Single-model error-driven retry loop; no decomposition, memory, or reflection | 1 model | Plain instructions + test errors | 1 per attempt | Gold standard; **never uses AIDER architect/editor** |
| **Decomposition** | Interleaved planning cycles incrementally decompose task and execute 1-2 actions per cycle | 1 model | Explicit "plan this step" prompts | 2-4 per cycle | Architecture: Planning request → Execution request (alternating) |
| **MultiPlan** | Generate 4 candidate plans via temperature sampling, evaluate all, select best via majority vote | 1 model | Repeated planning with temperature [0.3, 0.7, 1.0, 1.5] | 4 planning + evaluations | Selection strategy: Passed tests (primary), token cost (tiebreaker) |
| **Reflection** | Failed solution triggers reflection phase (LLM analyzes failures), then refinement (concrete fixes) | 1 model | Dual-phase: REFLECTION_PROMPT → REFINEMENT_PROMPT | 3 per cycle (generation + reflection + refinement) | Full cycle: p₀ → evaluate → rᵢ → pᵢ₊₁ → retry |
| **RAG Memory** | Retrieval of relevant past solutions augments task instructions with concrete examples | 1 model | Retrieve top-3 similar solutions, inject as in-context examples | 1 + retrieval cost | No additional model calls; pure prompt augmentation |

**Critical Design Decision: All patterns use AIDER's SINGLE-MODEL CODE MODE, never dual-model architect/editor.**

This is essential because:
1. Isolates planning pattern effects from AIDER's native architecture
2. Makes all patterns directly comparable (same model, same task scope)
3. Keeps token/energy budget constant across conditions
4. Prevents confounding with encoder reasoning if using o1-style models

See detailed operationalization in each variant's README.

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

## Run MultiPlan experiment

**Research-grade multi-plan selection** using Self-Consistency sampling and majority voting:

```bash
bash shared/scripts/run_multiplan.sh
```

What it does:
1. Generates 4 candidate plans per task (temperature: 0.3, 0.7, 1.0, 1.5)
2. Executes each plan independently
3. Selects best plan using majority vote on test outcomes
4. Reports aggregated metrics + per-plan breakdown

Customize number of plans:

```bash
AIDER_BENCH_NUM_PLANS=3 bash shared/scripts/run_multiplan.sh
```

**Documentation**: See [MultiPlan/README.md](MultiPlan/README.md) and [MULTIPLAN_IMPLEMENTATION.md](MULTIPLAN_IMPLEMENTATION.md) for detailed design.

**Compare results**:

```bash
python3 compare_multiplan_results.py \
  "Baseline/results/<baseline.json>" \
  "MultiPlan/results/<multiplan.json>"
```

## Validate MultiPlan implementation

```bash
bash validate_multiplan.sh
```

Checks all components are in place (syntax, files, functions, strategies).

## Notes / TODOs

- Baseline, MultiPlan, and other variants all use sidecar harnesses in this repo to avoid patching upstream Aider.
- If upstream Aider internals change substantially, update:
  - `Baseline/scripts/baseline_harness.py`
  - `MultiPlan/scripts/multiplan_harness.py`
  - `Baseline/scripts/collect_results.py`

