# Reflection and Refinement

Implements **plan-based reflection and refinement** following the exact academic definition in the research literature.

## Overview

The reflection pattern performs iterative refinement **before execution**, enabling the LLM to self-critique and improve code without immediate test feedback:

1. **Initial Plan Generation**: Generate initial solution (p₀)
2. **Plan Critique Cycles** (before execution): 
   - Reflect on current code for potential bugs (r_i)
   - Refine code based on critique (p_{i+1})
   - Repeat 2-3 times
3. **Execution**: Run tests on the refined plan
4. **Failure Reflection** (if tests fail):
   - Reflect on actual test failures
   - Refine code based on failure analysis

## Formula

Follows the academic formulation exactly:

$$p_0 = \text{plan}(E, g; \Theta, P)$$
$$r_i = \text{reflect}(E, g, p_i; \Theta, P)$$
$$p_{i+1} = \text{refine}(E, g, p_i, r_i; \Theta, P)$$

Where:
- **p₀**: Initial plan/code
- **r_i**: Reflection on current plan (critique for potential issues)
- **p_{i+1}**: Refined plan
- Repeat reflect-refine 2 times, THEN execute
- If execution fails, continue with failure-driven reflection

## Research References

Based on:
- **Self-refine** [Madaan et al., 2023]: generation → feedback → refinement
- **Reflexion** [Shinn et al., 2023]: self-reflections upon error detection  
- **CRITIC** [Gou et al., 2023]: external validation and self-correction
- **InteRecAgent** [Huang et al., 2023]: ReChain for self-correction
- **LEMA** [An et al., 2023]: error-driven learning with feedback

## Key Differences from Other Patterns

| Aspect | Baseline | Decomposition | Reflection |
|--------|----------|---------------|-----------|
| **When reflection occurs** | Never | During planning | Before & after execution |
| **Reflection trigger** | N/A | Interleaved planning | Plan critique, then test failures |
| **Feedback type** | Test errors only | Planning steps | Plan critique + test errors |
| **Pre-execution cycles** | 0 | Multiple planning steps | 2 pure plan critique cycles |
| **Total LLM calls** | 1-2 per task | 2-4 per cycle | 3-7 per task (plan critique + execution + failure analysis) |

## Configuration

- **tries**: Number of total attempts (default: 3)
- **plan_reflection_cycles**: Pure plan critique cycles before first execution (default: 2)
- **max_reflections**: Failure-driven reflection cycles after execution (tries - 1)

## Prompt Strategy

### Phase 1: Initial Code Generation
```
<task instructions from .docs/>
####
Use the above instructions to modify the supplied files: <file_list>
```

### Phase 2: Plan Critique (BEFORE execution, 2 cycles)
**LLM is asked to critique code WITHOUT running tests:**
```
## Critique Your Implementation Plan

Walk through your implementation line by line.
Identify potential issues:
- Does it handle all edge cases?
- Are there off-by-one errors?
- Does it parse input correctly?
- Are data types correct?
- Does algorithm match requirements?

List 3-5 concrete issues your code might have.
```

### Phase 3: Plan Refinement (BEFORE execution)
**LLM refines code based on its own critique:**
```
## Refine Your Code Based on Critique

Acknowledge the issues you found.
Plan the fixes for each issue.
Now update the code to implement all improvements.
```

### Phase 4: Execution & Testing
Tests are run. If they pass → done.

### Phase 5: Failure Reflection (IF tests fail)
**IF execution fails, reflect on actual test output:**
```
## Reflect on Test Failures

Your code did not pass the tests.
Identify the exact failure and root cause.
List the actual bugs in your code.
Propose concrete fixes.

Test errors: <actual test output>
```

### Phase 6: Failure-Driven Refinement (IF tests fail)
**Refine based on actual test failures:**
```
## Refine Your Code Based on Test Failures

Based on the test failures, update your code to fix the bugs.
```

## Implementation Details

**Attempt 0 (First Attempt):**
1. Generate initial code
2. Run 2 plan critique-refine cycles (NO test execution)
3. Execute tests on the refined code
4. If tests fail, optionally do failure-driven reflection

**Attempt 1+ (Subsequent Attempts):**
1. Refine based on previous failure/reflection
2. Execute tests
3. If tests fail again, optionally do failure-driven reflection

**Total cycles:**
- Attempt 0: 1 generation + 2 plan critiques + 1 refinement execution = up to 4 LLM calls before first test
- Attempt 1: 1 failure refinement + execution = 1-2 LLM calls
- Attempt 2: 1 error-driven refinement + execution = 1-2 LLM calls

This implementation:
- ✓ Prioritizes plan-based reflection (matches paper exactly for Phase 1-3)
- ✓ Falls back to failure-driven refinement only after execution
- ✓ Maximizes early self-critique before wasting test execution
- ✓ Leverages concrete test failure signals when plan critique isn't enough

## Usage

Run the reflection variant:

```bash
bash shared/scripts/run_reflection.sh
```

This runs:
- 10 tasks (configurable)
- 3 total attempts per task
- 2 plan critique cycles before first execution
- 1 failure-driven reflection cycle (if needed)
- Default model: qwen2.5-coder:7b-instruct
- Task timeout: 15 minutes

## Results Location

Results are saved to:
- CSV summary: `benchmark/runs/TIMESTAMP--reflection--MODEL.csv`
- JSON summary: `benchmark/runs/TIMESTAMP--reflection--MODEL.json`
- Per-task details: `benchmark/runs/TIMESTAMP--reflection--MODEL.tasks.csv`

## Example Execution Flow

```
Task: "Implement accumulate function"

ATTEMPT 1:
├─ Phase 1: Generate initial code (p₀)
├─ Phase 2: Critique plan (r₁)
│  └─ LLM: "Edge case: empty array not handled"
├─ Phase 3: Refine based on critique (p₁)
├─ Phase 2: Critique refined plan (r₂)
│  └─ LLM: "Off-by-one in loop, should be `range(len(xs))`"
├─ Phase 3: Refine again (p₂)
├─ Phase 4: Execute → Tests fail on edge case
├─ Phase 5: Reflect on actual failure
│  └─ LLM: "Sum starts at 0 but should accumulate correctly"
└─ Phase 6: Refine based on failure → RETRY

ATTEMPT 2:
└─ Generate improved code based on learned issues → Tests pass ✓
```

## Performance Target

Must achieve > 60% pass rate (baseline: 6/10 tasks passing)

## Operationalization: Reflection & Refinement Pattern

**Purpose:** Implement plan-based iterative reflection and refinement to improve code quality **before and after execution**.

### Model Configuration
- **Mode:** AIDER single-model code mode (NOT architect/editor dual-model)
- **Models used:** 1 (e.g., `ollama/qwen2.5-coder:7b-instruct`)
- **Architect role:** N/A (not used)
- **Editor role:** N/A (not used)
- **Rationale:** Single model; reflection is middleware injected via prompts

## Why Plan-Based Reflection Matters

Plan-based reflection (before execution) offers:
- **Early error detection**: Catch potential bugs before running tests
- **Self-improvement**: LLM learns from its own self-critique
- **Cost efficiency**: Fewer test executions due to pre-refined code
- **Aligned with research**: Matches the published academic definition
- **Deterministic cycles**: First attempt ALWAYS gets 2 critique cycles regardless of pass/fail

### Implementation Strategy

The code implements a hybrid approach that prioritizes the paper's plan-based definition:

**Phase 1 (Attempt 0 Only):** Plan-Based Critique Cycles
- LLM **does NOT** have access to test results
- LLM **critiques code** for logical flaws, edge cases, algorithm correctness
- LLM **refines code** based on self-critique
- Cycle repeats 2 times before any tests are run
- **Prompts used:** `PLAN_CRITIQUE_PROMPT`, `REFINEMENT_PROMPT`

**Phase 2:** Execution & Testing
- Tests run on the refined code
- If tests pass → task complete ✓
- If tests fail → proceed to Phase 3

**Phase 3 (Attempts 1+):** Failure-Driven Refinement (Fallback)
- Only triggered if Phase 2 tests failed
- LLM **now has access to actual test errors**
- LLM reflects on concrete failures and refines
- **Prompts used:** `REFLECTION_ON_FAILURE_PROMPT`, `REFINEMENT_PROMPT`
- Up to 1 failure refinement cycle per subsequent attempt

### Why We Also Include Failure-Driven Reflection

Practical execution shows:
- Concrete test failures provide the highest-quality signals
- Pure plan critique alone isn't always sufficient (LLMs hallucinate)
- Hybrid approach (plan + failure) maximizes pass rates
- Staying true to the spirit of reflection: "iterative improvement through feedback"


#### Phase 3: Refinement (plan final fixes)
```
REFINEMENT_PROMPT:
"## Refined Implementation Plan

Based on your reflection on the failures, create a detailed refinement plan:

Step 1: Restate the bugs you identified
Step 2: Plan the fix (describe exactly how to fix each bug)
Step 3: Implementation (apply all necessary fixes to the code)

Now update the code to implement all the fixes."
```

**Critical design**: Reflection is non-execution (chat-only), Refinement is execution (coder.run()).

### Model Invocation Pattern

```
Attempt 1:
  [coder.run(with_message=instructions)]
  → Generate code solution (p₀)
  → Run unit tests
  → PASS? → Done (no reflection needed)
  → FAIL? → Extract error output

  IF FAIL and reflection_count < max_reflections:
    [coder.chat(with_message=REFLECTION_PROMPT + error_output)]
    → LLM analyzes why it failed (rᵢ)
    → Extract reflection text (NO code changes)
    
    [coder.run(with_message=REFINEMENT_PROMPT + reflection + error_output)]
    → Generate refined solution (pᵢ₊₁)
    → Run unit tests
    → PASS? → Done
    → FAIL? → Loop to next attempt

Attempt 2-3 (if previous failed):
  Same cycle: test → reflect → refine → retry
```

**Invocation breakdown per task:**
- Base generation: 1 call
- Each failed attempt (up to max_reflections=2):
  - Reflection call: 1 (coder.chat, non-execution)
  - Refinement call: 1 (coder.run, execution + test)
- Total: 1 + 2×2 = 5 calls (worst case, all fails)
- Typical: 1-3 calls (early success or quick fail)

### Orchestration Logic

```python
reflection_count = 0
max_reflections = tries - 1  # tries=3, so max 2 reflections

for attempt in range(tries):  # Max 3 attempts
    # Phase 1: Generate
    response = coder.run(with_message=current_instructions, preproc=False)
    
    # Phase 2: Evaluate
    errors = run_unit_tests(...)
    
    if errors:
        test_outcomes.append(False)
        
        # Reflection phase: NON-EXECUTION semantic analysis
        if reflection_count < max_reflections:
            reflection_count += 1
            
            # Get LLM reflection (no code generation)
            reflect_prompt = REFLECTION_PROMPT + f"\n\n{errors}"
            reflection_response = coder.chat(with_message=reflect_prompt)
            
            # Generate refinement based on reflection
            refine_msg = REFINEMENT_PROMPT + reflection_response
            current_instructions = refine_msg
        else:
            # No more reflections; use simple error feedback
            current_instructions = errors + TEST_FAILURES.format(...)
    else:
        test_outcomes.append(True)
        break  # Success
```

### Relationship to AIDER's Native Architecture

**Does NOT use architect/editor mode.**

- Architect/editor would be an entirely different planning approach
- Reflection here is **supplementary error analysis**, not structural planning
- Single-model operation maintains comparability with Baseline
- Reflection happens *between* attempts, not in a separate stage

### Token and Energy Implications

- **Tokens per task:** 2-5× baseline
  - 1× for generation (same as baseline)
  - 1× per failed attempt for reflection (non-expensive chat)
  - 1× per failed attempt for refinement (full generation)
- **Model invocations:** 1-5 per task (varies with failures)
- **Time per task:** worst-case ~5× baseline (3 full attempts + reflections)
- **Energy:** ~2-5× baseline (multiple generations per task)
- **Reasoning overhead:** None (no reasoning models; pure error analysis)

### Reproducibility Checklist

- [ ] Single model specified in `.env`
- [ ] No architect/editor configuration
- [ ] REFLECTION_PROMPT hardcoded as above
- [ ] REFINEMENT_PROMPT hardcoded as above
- [ ] Reflection is chat-only (no code changes)
- [ ] Refinement is coder.run() (code changes allowed)
- [ ] max_reflections = tries - 1 (e.g., tries=3 → 2 reflections)
- [ ] After final attempt, task times out (no infinite loops)
- [ ] CodeCarbon tracks all generation + reflection + refinement calls
- [ ] Per-task timeout enforced (900s = 15 min max)
