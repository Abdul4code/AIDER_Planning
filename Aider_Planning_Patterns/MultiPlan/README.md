# MultiPlan: Multi-Plan Selection

Research-grade implementation of multi-plan selection for Aider benchmark tasks, inspired by:
- **Self-Consistency**: Sampling multiple reasoning paths via temperature variations
- **Tree-of-Thought**: Explicit plan generation and voting on optimal plan
- **Optimal Plan Selection**: Majority vote based on test outcomes

## Strategy

### Multi-plan Generation
Generate multiple candidate plans using **temperature sampling** (Self-Consistency approach):
- Temperature 0.3: Deterministic, focused
- Temperature 0.7: Moderate variance
- Temperature 1.0: Balanced diversity
- Temperature 1.5: Highly exploratory

Each plan is executed independently with equal time budget.

### Optimal Plan Selection
**Majority vote strategy**:
1. Among passing plans: select the plan with shortest execution time
2. If no plan passes: select the plan with fewest tokens (cheapest)
3. Reports: best plan index and summary of all candidate plans

## Implementation

- **Harness**: `scripts/multiplan_harness.py`
  - Generates N candidate plans in parallel/sequential mode
  - Tracks individual plan metrics (temperature, passing status, duration, cost)
  - Selects best plan based on test outcomes

## Operationalization: MultiPlan Pattern

**Purpose:** Implement Self-Consistency + Tree-of-Thought via temperature sampling and majority voting.

**Academic Formula:** $P = \{p_1, p_2, ..., p_n\} = \text{plan}(E, g; \Theta, P); p^* = \text{select}(E, g, P; \Theta, F)$

### Model Configuration
- **Mode:** AIDER single-model code mode (NOT architect/editor dual-model)
- **Models used:** 1 (e.g., `ollama/qwen2.5-coder:7b-instruct`)
- **Architect role:** N/A (not used)
- **Editor role:** N/A (not used)
- **Rationale:** Single model; multi-plan generation via temperature, not model ensemble

### Prompt Strategy

**Identical task instructions, repeated 4 times with different temperature settings:**

```
Iteration 1 (temp=0.3):
  [coder.run(with_message=instructions, temperature=0.3)]
  → Generate Plan p₁ (deterministic)
  → Execute and test
  → Record outcome and metrics

Iteration 2 (temp=0.7):
  [coder.run(with_message=instructions, temperature=0.7)]
  → Generate Plan p₂ (moderate variance)
  → Execute and test
  → Record outcome and metrics

Iteration 3 (temp=1.0):
  [coder.run(with_message=instructions, temperature=1.0)]
  → Generate Plan p₃ (standard)
  → Execute and test
  → Record outcome and metrics

Iteration 4 (temp=1.5):
  [coder.run(with_message=instructions, temperature=1.5)]
  → Generate Plan p₄ (high exploration)
  → Execute and test
  → Record outcome and metrics

Selection Phase:
  p* = select(P = {p₁, p₂, p₃, p₄})
  → Majority vote on test outcomes
  → Return best plan index and winner
```

**No decomposition, reflection, or memory prompts are added** (same base instruction as Baseline).

### Model Invocation Pattern

```
Per task:
  For each temperature in [0.3, 0.7, 1.0, 1.5]:
    [coder.run(with_message=instructions, temperature=T)]
    → Generate code
    → Run unit tests
    → Record (plan_id, temperature, passed, duration, cost)

Selection:
  passed_plans = [p for p in plans if p.passed]
  if passed_plans:
    p* = min(passed_plans, key=lambda p: p.duration)  # Fastest passing plan
  else:
    p* = min(plans, key=lambda p: p.tokens)  # Cheapest failing plan
  
  return p*
```

**Total model invocations per task:** 4 (one per temperature)
- Sequential execution (not parallel) to avoid resource contention
- Each receives full time budget independently

### Orchestration Logic

```python
plan_results = []

# Generate 4 candidate plans
for temperature in [0.3, 0.7, 1.0, 1.5]:
    # Phase 1: Generation with this temperature
    response = coder.run(with_message=instructions, temperature=temperature)
    
    # Phase 2: Test evaluation
    errors = run_unit_tests(...)
    passed = (errors is None)
    
    # Record metrics
    plan_results.append({
        'temperature': temperature,
        'passed': passed,
        'duration': elapsed_time,
        'tokens': coder.total_tokens,
        'cost': coder.total_cost,
    })

# Phase 3: Selection (majority vote + tiebreaker)
best_idx, best_plan = select_best_plan(plan_results)

# Report best plan as final result
# (Do NOT retry: MultiPlan makes a single selection decision)
```

**Key**: Each plan is independent; selection is deterministic voting, no additional retries.

### Relationship to AIDER's Native Architecture

**Does NOT use architect/editor mode.**

- Architect/editor mode would add *another layer* of planning (top-level decomposition)
- That would confound this pattern's effect
- **We isolate to single-model temperature sampling instead**
- All 4 plans see identical instructions; variance comes purely from LLM's stochastic decoding

### Token and Energy Implications

- **Tokens per task:** 4x baseline (4 independent generations)
- **Model invocations:** 4 per task (linear with number of temperatures)
- **Time per task:** ~4x baseline (4 sequential full attempts)
- **Energy:** ~4x baseline (4 complete LLM passes + tests)
- **Reasoning overhead:** None (no reasoning models; temperature sampling is decoder-side)

### Reproducibility Checklist

- [ ] Single model specified in `.env`
- [ ] No architect/editor configuration
- [ ] Temperature values hardcoded as [0.3, 0.7, 1.0, 1.5]
- [ ] Selection strategy: passed test outcome → duration tiebreaker → token cost fallback
- [ ] All 4 plans receive equal time/token budgets
- [ ] No retries after selection (single fixed decision per task)
- [ ] Sequential execution (not parallel) to isolate temperature effects
- [ ] CodeCarbon tracks all 4 model invocations
  - Reports aggregated and per-plan statistics

- **Runner**: `$PROJECT_ROOT/shared/scripts/run_multiplan.sh`
  - Docker-based execution (like baseline)
  - Reuses Ollama connectivity
  - Streams results to CSV for live monitoring
  - Compares against baseline for parity check (optional)

## Execution

```bash
# Single machine with 4 plans (default)
bash shared/scripts/run_multiplan.sh

# Custom number of plans per task
AIDER_BENCH_NUM_PLANS=3 bash shared/scripts/run_multiplan.sh

# With baseline parity check
AIDER_BENCH_REQUIRE_BASELINE_PARITY=1 \
  AIDER_BENCH_BASELINE_SUMMARY_JSON="Baseline/results/<baseline-json>" \
  bash shared/scripts/run_multiplan.sh

# Run specific language only
AIDER_BENCH_LANGUAGES=python bash shared/scripts/run_multiplan.sh
```

## Results

Results are saved to `MultiPlan/results/<timestamp>--multiplan--<model>/` folder:
- `.json`: Summary statistics
- `.csv`: Single-row summary
- `.tasks.csv`: Per-task breakdown with candidate plan details

## Evaluation Criteria

✅ **Conformance**: Closely follows Self-Consistency + ToT + majority vote principles  
✅ **Performance**: Total wall-clock time per task ≤ 15 minutes  
✅ **Accuracy**: Pass rate ≥ baseline (or better)
