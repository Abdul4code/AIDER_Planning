# Reflection and Refinement

Implements iterative reflection-based planning following the academic definition of reflection and refinement for LLM-Agent planning.

## Overview

The reflection variant enhances fault tolerance and error correction through:
1. **Iterative Generation**: Generate initial solution attempt
2. **Evaluation**: Run tests to validate solution
3. **Reflection**: Generate self-reflection on why tests failed
4. **Refinement**: Generate actionable feedback based on reflection
5. **Retry**: Incorporate reflection feedback into next attempt

## Implementation

Based on research papers:
- **Self-refine** [Madaan et al., 2023]: generation → feedback → refinement
- **Reflexion** [Shinn et al., 2023]: self-reflections upon error detection  
- **CRITIC** [Gou et al., 2023]: external validation and self-correction
- **InteRecAgent** [Huang et al., 2023]: ReChain for self-correction
- **LEMA** [An et al., 2023]: error-driven learning with feedback

## Key Differences from Baseline

| Aspect | Baseline | Reflection |
|--------|----------|-----------|
| Error Handling | Raw test errors → direct retry | Test errors → reflection → refinement → retry |
| Feedback Type | Syntax/semantic errors only | Root cause analysis + strategic fixes |
| LLM Interaction | Single shot or error-driven | Explicit reflection phase before retry |
| Context Building | Error context only | Full task + error + reflection |

## Configuration

- **tries**: Number of attempts (default: 3, gives 2 reflection cycles)
- **max_reflections**: max_reflections = tries - 1 (reserve one final attempt)

## Usage

Run the reflection variant:
```bash
bash shared/scripts/run_reflection.sh
```

This runs:
- 10 Python tasks
- 3 total attempts per task (allowing 2 reflection cycles)
- Default model: qwen2.5-coder:7b-instruct
- Task timeout: 15 minutes

## Results Location

Results are saved to:
- CSV summary: `benchmark/runs/TIMESTAMP--reflection--MODEL.csv`
- JSON summary: `benchmark/runs/TIMESTAMP--reflection--MODEL.json`
- Per-task details: `benchmark/runs/TIMESTAMP--reflection--MODEL.tasks.csv`

## Performance Target

Must achieve > 60% pass rate (baseline: 6/10 tasks passing)

## Operationalization: Reflection & Refinement Pattern

**Purpose:** Implement iterative reflection + refinement cycle to improve error correction.

**Academic Formula:** $p_0 = \text{plan}(E, g; \Theta, P); r_i = \text{reflect}(E, g, p_i; \Theta, P); p_{i+1} = \text{refine}(E, g, p_i, r_i; \Theta, P)$

### Model Configuration
- **Mode:** AIDER single-model code mode (NOT architect/editor dual-model)
- **Models used:** 1 (e.g., `ollama/qwen2.5-coder:7b-instruct`)
- **Architect role:** N/A (not used)
- **Editor role:** N/A (not used)
- **Rationale:** Single model; reflection is added as middleware, not native mode

### Prompt Strategy

**Three distinct prompts injected at different phases:**

#### Phase 1: Initial Generation (same as Baseline)
```
<task instructions from .docs/>
####
Use the above instructions to modify the supplied files: <file_list>
```

#### Phase 2: Reflection (when tests fail)
```
REFLECTION_PROMPT:
"## Self-Reflection on Test Failures

You just attempted to solve a programming task but there were test failures. 
Analyze what went wrong and why.

Your task:
1. **Identify the exact failure**: What does the error message tell you?
2. **Trace the root cause**: Is it a logic error? Misunderstanding of requirements? Edge case?
3. **Specific bugs found**: List the actual bugs in your code
4. **Concrete fix strategy**: What specific code changes will fix each bug?

[Test error output inserted here]"
```

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
