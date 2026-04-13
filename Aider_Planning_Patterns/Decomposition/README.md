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
- Gives the planner access to the same open solution files as the executor so sub-goals are grounded in the actual codebase.
- Keeps replan repair disabled by default for paper-faithful interleaving; enable it with `AIDER_BENCH_DECOMP_REPAIR=1` if needed.
- Compresses test-failure feedback to a short summary before the next attempt to prevent prompt bloat.
- Stops early after repeated no-op executor actions, with the threshold configurable via `AIDER_BENCH_DECOMP_NOOP_STOP_THRESHOLD` (default `3`).
- Supports deterministic task order with `AIDER_BENCH_SHUFFLE_TASKS=0`.
- Enforces a hard per-task timeout cap of 900 seconds (15 minutes), even if a larger timeout is configured.
- Enforces baseline accuracy parity by default after each run (`AIDER_BENCH_REQUIRE_BASELINE_PARITY=1`).
- Injects decomposition guidance into task instructions:
  - reveal only next sub-goal(s)
  - create immediate sub-plan (1-2 actions)
  - execute, reassess, and continue

## Accuracy Parity Gate

By default, decomposition runs fail if accuracy is below baseline.

- Overall gate: decomposition pass-rate must be >= baseline pass-rate.
- Matched-task gate: when both task CSV files are available, decomposition matched-task pass-rate must be >= baseline on overlapping task keys.
- Optional strict gate: set `AIDER_BENCH_REQUIRE_PER_TASK_PARITY=1` to fail on any individual task where baseline passed and decomposition failed.

Override baseline source files if needed:

- `AIDER_BENCH_BASELINE_SUMMARY_JSON=/path/to/baseline-summary.json`
- `AIDER_BENCH_BASELINE_TASK_CSV=/path/to/baseline.tasks.csv`

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

## Operationalization: Task Decomposition Pattern

**Purpose:** Implement interleaved planner-executor orchestration to solve tasks through structured subgoal decomposition.

**Academic Formula:** $\text{while } t < \text{MAX\_STEPS}: s_t = \text{plan}(E, g, \{p_i\}_{i<t}); p_t = \text{execute}(E, g, s_t)$

### Model Configuration
- **Mode:** AIDER single-model code mode (NOT architect/editor dual-model)
- **Models used:** 1 (e.g., `ollama/qwen2.5-coder:7b-instruct`)
- **Architect role:** N/A (not used; planner runs single model)
- **Editor role:** N/A (not used; executor runs same model)
- **Rationale:** Single model avoids confounding with dual-model native planning; planner and executor are roles, not separate models

### Prompt Strategy

**Two distinct prompt modes injected at alternating phases:**

#### Phase 1: Planner Prompt (extract next subgoal)
```
PLANNER_PROMPT_TEMPLATE:
"## Task Planning

Your task: <task_description>

Progress so far:
[Previous successful subgoals and actions]

Required files:
[Current file structure with line numbers]

Constraints:
- Reveal ONLY the next 1-2 immediate subgoals
- Do NOT plan the entire solution upfront
- Focus on what needs to happen NEXT
- Estimate concrete actions (modify files, run tests)
- Each action should take 1 planner call or 1-2 executor calls

What are the immediate next steps?"
```

#### Phase 2: Executor Prompt (execute subgoal)
```
EXECUTOR_PROMPT_TEMPLATE:
"## Implementation

Subgoal: <planner_output>

Your task: <task_description>
Current state: [Previous test failures if any]

Execute the subgoal by modifying files as needed:
<subgoal_from_planner>

Then run tests to validate the subgoal."
```

**Critical design:**
- Planner is **chat-only** (no file modifications)
- Executor is **coder.run()** (modifies files + runs tests)
- Planner sees test results from executor before next planning step
- Prompts evolve: planner gets updated context after each executor cycle

### Model Invocation Pattern

```
Initialize: current_state = None

FOR cycle IN 1..MAX_INTERLEAVED_CYCLES (default=3):
  
  Step 1: Plan subgoal(s)
    [coder.chat(PLANNER_PROMPT + current_state)]
    → LLM outputs: "Next: <subgoal_1>, <subgoal_2>"
    → Extract structured subgoal
    
    IF subgoal is empty or "done":
      Break (early termination)
    
  Step 2: Execute subgoal(s)
    [coder.run(EXECUTOR_PROMPT + extracted_subgoal)]
    → Execute code changes (file modifications)
    → Run unit tests
    → Collect test outcomes
    
    IF test_pass:
      current_state = "Success on subgoal X; continue"
    ELSE:
      current_state = "Failed subgoal X; test errors: <error_summary>"
    
  Step 3: Check for termination
    IF consecutive_no_op_cycles > NOOP_STOP_THRESHOLD (default=3):
      Break (prevent infinite loops)
      
    IF remaining_budget < minimum_planning_time:
      Break (timeout approaching)
```

**Invocation breakdown per task:**
- Planning calls: MAX_INTERLEAVED_CYCLES = 3 calls (chat-only)
- Execution calls: 1-3 per cycle = 3-9 calls (code generation + test)
- Total: 6-12 calls per task (high variance based on task complexity)
- Typical: 3-6 calls (1-2 successful cycles)

### Orchestration Logic

```python
max_cycles = int(os.getenv('AIDER_BENCH_ARCH_MAX_STEPS', '3'))
noop_stop_threshold = int(os.getenv('AIDER_BENCH_DECOMP_NOOP_STOP_THRESHOLD', '3'))
consecutive_no_ops = 0

for cycle_num in range(max_cycles):
    # Phase 1: PLANNER (chat-only)
    planner_prompt = PLANNER_PROMPT_TEMPLATE.format(
        task_description=task_description,
        previous_progress=previous_subgoals_and_actions,
        file_structure=current_file_structure
    )
    
    planner_response = coder.chat(with_message=planner_prompt)
    subgoals = parse_subgoals(planner_response)
    
    if not subgoals or "done" in subgoals.lower():
        break  # Early termination
    
    # Phase 2: EXECUTOR (coder.run with modifications)
    executor_prompt = EXECUTOR_PROMPT_TEMPLATE.format(
        subgoal=subgoals,
        task_description=task_description,
        test_failures=most_recent_test_errors
    )
    
    executor_response = coder.run(with_message=executor_prompt)
    test_results = run_unit_tests(...)
    
    # Track cycle outcomes
    if test_results.all_pass:
        previous_subgoals_and_actions += f"✓ {subgoals}"
        consecutive_no_ops = 0
    elif executor_response == "no_change_made":
        consecutive_no_ops += 1
        if consecutive_no_ops >= noop_stop_threshold:
            break
    else:
        previous_subgoals_and_actions += f"✗ {subgoals}: {test_results.errors}"
        consecutive_no_ops = 0

# Final test run
final_results = run_unit_tests(...)
return final_results
```

### Key Differences from Baseline

| Aspect | Baseline | Decomposition |
|--------|----------|---------------|
| Planning | None (error-driven) | Explicit interleaved planning |
| Prompt | Single static prompt | Two alternating prompts (planner/executor) |
| Execution | One monolithic generation | Multiple small targeted generations |
| Context | Full error output | Summarized progress + next steps |
| Model calls | 1-2 per task | 3-9 per task (planner + executor) |
| Adaptation | Post-failure only | After every executor step |

### Relationship to AIDER's Native Architecture

**Does NOT use architect/editor dual-model mode.**

- Architect/editor is AIDER's native dual-model planning (architect plans, editor implements)
- Decomposition here is **single-model interleaved planning** (same model alternates roles)
- Planner role is only chat output (no code generation)
- Executor role is code generation + testing
- Single model avoids confounding: isolates decomposition effect from dual-model confound

### Token and Energy Implications

- **Tokens per task:** 3-9× baseline
  - Multiple full planning prompts (3 calls)
  - Multiple executor invocations (3-9 calls)
  - Executor calls = code generation (expensive)
  - Planner calls = analysis only (cheaper chat)
- **Model invocations:** 6-12 per task (high variance)
- **Time per task:** 3-9× baseline (multiple generation + test cycles)
- **Energy:** 3-9× baseline
- **Reasoning overhead:** None (no reasoning models; decomposition is prompt engineering)

### Reproducibility Checklist

- [ ] Single model specified in `.env`
- [ ] No architect/editor dual-model configuration
- [ ] PLANNER_PROMPT_TEMPLATE hardcoded as above
- [ ] EXECUTOR_PROMPT_TEMPLATE hardcoded as above
- [ ] max_cycles configured in environment (default=3)
- [ ] Planner is chat-only (verify no file modifications occur)
- [ ] Executor is coder.run() (file modifications allowed)
- [ ] noop_stop_threshold enforced (default=3, prevents infinite loops)
- [ ] Test compression enabled (prevent prompt bloat)
- [ ] Per-task timeout enforced (900s = 15 min max)
- [ ] Decomposition run name recorded in results (variant="decomposition")
- [ ] Baseline parity gate checked (if enabled)
- [ ] CodeCarbon tracks all planning + execution calls
- [ ] arch_metrics recorded: planner_calls, executor_calls, arch_interleaved_cycles
