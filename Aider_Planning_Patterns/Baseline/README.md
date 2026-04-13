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

## Operationalization: Baseline Pattern

**Purpose:** Gold-standard reference condition. Establishes the performance floor without any planning augmentations.

### Model Configuration
- **Mode:** AIDER single-model code mode (NOT architect/editor dual-model)
- **Models used:** 1 (e.g., `ollama/qwen2.5-coder:7b-instruct`)
- **Architect role:** N/A (not used)
- **Editor role:** N/A (not used)
- **Rationale:** Single model ensures direct comparability with all other conditions

### Prompt Strategy
Plain task instructions with inline error feedback:

1. **Initial prompt** (per task):
   ```
   <task instructions from .docs/>
   ####
   Use the above instructions to modify the supplied files: <file_list>
   Don't change the names of existing functions or classes...
   Only use standard libraries...
   ```

2. **Error feedback** (on test failure):
   ```
   ####
   See the testing errors above.
   The tests are correct, don't try and change them.
   Fix the code in <file_list> to resolve the errors.
   ```

3. **No reflection, memory, or decomposition prompts** are added

### Model Invocation Pattern

```
Attempt 1:
  [coder.run(with_message=instructions)]
  → Generate code solution
  → Run unit tests
  → PASS? → Done
  → FAIL? → Extract error output

Attempt 2 (if attempt 1 failed):
  [coder.run(with_message=error_output + TEST_FAILURES.format())]
  → Human feedback: error trace + instructions
  → Generate revised solution
  → Run unit tests
  → PASS? → Done
  → FAIL? → Timeout or max retries (default: 2 attempts)
```

**Total model invocations per task:** 1-2 (1 if pass, 2 if fail once)

### Orchestration Logic

```python
for attempt in range(tries):  # tries=2 by default
    remaining_seconds = _seconds_left(task_deadline_ts)
    if remaining_seconds <= 0:
        task_timed_out = True
        break
    
    # Single model call per attempt
    response = coder.run(with_message=current_instructions, preproc=False)
    
    # Evaluate via unit tests
    errors = run_unit_tests(...)
    if errors:
        test_outcomes.append(False)
        # Set next attempt's prompt to error output
        current_instructions = errors + TEST_FAILURES.format(...)
    else:
        test_outcomes.append(True)
        break  # Exit on first success
```

### Relationship to AIDER's Native Architecture

**Key fact:** Baseline does NOT use AIDER's architect/editor mode.

- AIDER's default supports both single-model and dual-model configurations
- Architect/editor mode creates an implicit planning pattern (architect generates plan, editor executes)
- **We deliberately avoid this** to isolate our experimental planning patterns
- This ensures fair comparison: each condition adds planning logic on top of identical AIDER base

### Token and Energy Implications

- **Tokens per task:** Minimal (only failed attempts trigger extra tokens)
- **Model invocations:** 1-2 per task (linear with attempts)
- **Energy:** Baseline (reference point for other conditions)
- **Reasoning overhead:** None (no reasoning models used)

### Reproducibility Checklist

- [ ] Single model specified in `.env` (e.g., `OLLAMA_MODEL=qwen2.5-coder:7b-instruct`)
- [ ] Single model only; no separate architect/editor models configured
- [ ] AIDER invoked in single-model code mode (no architect/editor flags passed)
- [ ] Task timeout set (default: 900s = 15 min per task)
- [ ] Attempt limit set (default: 2 attempts)
- [ ] No decomposition, reflection, or RAG memory augmentation enabled
- [ ] Non-shuffled task order for reproducibility: `AIDER_BENCH_SHUFFLE_TASKS=0`
