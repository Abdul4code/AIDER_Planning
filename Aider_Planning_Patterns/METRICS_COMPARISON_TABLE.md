# Detailed Metrics Comparison: Baseline vs MultiPlan

## Per-Task LLM Call Distribution

| Task | Baseline (calls) | MultiPlan (calls) | Baseline (passed) | MultiPlan (passed) |
|------|------------------|-------------------|-------------------|-------------------|
| accumulate | 1 | 4 | ✓ | ✓ |
| acronym | 1 | 4 | ✗ | ✗ |
| armstrong-numbers | 1 | 4 | ✓ | ✓ |
| binary-search | 1 | 4 | ✗ | ✗ |
| difference-of-squares | 1 | 4 | ✗ | ✗ |
| dining-philosophers | 1 | 4 | ✓ | ✓ |
| flatten-array | 1 | 4 | ✓ | ✓ |
| food-chain | 1 | 4 | ✓ | ✓ |
| grep | 1 | 4 | ✓ | ✓ |
| largest-series-product | 1 | 4 | ✗ | ✗ |
| **TOTAL** | **10** | **40** | **6/10** | **6/10** |

**Note**: Results shown are from per-task result aggregation. MultiPlan shows 4 executor_calls per task because each candidate plan executes independently.

---

## Aggregated Summary Statistics

### Baseline Run
- **Run ID**: 20260412-133011
- **Model**: qwen2.5-coder:7b-instruct  
- **Total Tasks**: 10
- **Passed**: 6 (60%)
- **Failed**: 4 (40%)
- **Total LLM Calls**: 16
- **Total Duration**: 754 seconds (12m 34s)
- **Avg Duration/Task**: 75.4 seconds
- **Total Tokens Sent**: ~45,000
- **Total Tokens Received**: ~18,000

### MultiPlan Run
- **Run ID**: 20260412-134245
- **Model**: qwen2.5-coder:7b-instruct
- **Total Tasks**: 10
- **Passed**: 6 (60%)
- **Failed**: 4 (40%)
- **Total LLM Calls**: 41 (40 from plans + 1 overhead)
- **Total Duration**: 1945 seconds (32m 25s)
- **Avg Duration/Task**: 194.5 seconds  
- **Total Tokens Sent**: ~180,000+ (4× baseline)
- **Total Tokens Received**: ~72,000+ (4× baseline)

---

## Key Metrics

### Accuracy Comparison
```
Baseline: 60% (6/10)
MultiPlan: 60% (6/10)
Difference: 0% (equal)
```

### Computational Cost Comparison
```
LLM Calls:
  Baseline: 16
  MultiPlan: 41
  Increase: 2.56× (25 additional calls)

Runtime:
  Baseline: 754s (12m 34s)
  MultiPlan: 1945s (32m 25s)
  Increase: 2.58× (1191 additional seconds)

Per-Task Average:
  Baseline: 75.4s / 1.6 calls
  MultiPlan: 194.5s / 4.1 calls
```

### Efficiency Metrics
```
Calls per Passing Task:
  Baseline: 16 calls / 6 tasks = 2.67 calls/task
  MultiPlan: 41 calls / 6 tasks = 6.83 calls/task
  Increase: 2.56×

Time per Passing Task:
  Baseline: 754s / 6 tasks = 125.7s/task
  MultiPlan: 1945s / 6 tasks = 324.2s/task
  Increase: 2.58×
```

---

## Success Pattern Analysis

### Tasks That Both Solved (6 tasks)
1. **accumulate**: Both passed on first attempt
2. **armstrong-numbers**: Both passed on first attempt  
3. **dining-philosophers**: Both passed on first attempt
4. **flatten-array**: Both passed on first attempt
5. **food-chain**: Both passed on first attempt
6. **grep**: Both passed on first attempt

**Insight**: For tasks that are solvable, the baseline single-pass approach is sufficient. MultiPlan provides 4 different solution attempts, but all 4 likely succeed when any succeeds.

### Tasks That Both Failed (4 tasks)
1. **acronym**: Neither approach passed
2. **binary-search**: Neither approach passed
3. **difference-of-squares**: Neither approach passed
4. **largest-series-product**: Neither approach passed

**Insight**: For tasks that are difficult, having 4 attempts with different temperatures doesn't improve outcomes. Suggests that task difficulty is fundamental, not addressable by temperature sampling alone.

---

## The Temperature Sampling Profile (MultiPlan Only)

For each task, MultiPlan attempts solutions with:

| Plan | Temperature | Strategy |
|------|-------------|----------|
| 0 | 0.3 | Deterministic (most coherent, least creative) |
| 1 | 0.7 | Moderately random (balanced) |
| 2 | 1.0 | Standard random (default behavior) |
| 3 | 1.5 | Very creative (most random) |

### Example: Armstrong Numbers Task
```
Plan 0 (T=0.3): ✓ passed, 20.6s, prompt=1162, completion=195
Plan 1 (T=0.7): ✓ passed, 72.2s, prompt=2341, completion=476
Plan 2 (T=1.0): ✓ passed, 14.9s, prompt=1099, completion=201  ← SELECTED
Plan 3 (T=1.5): ✓ passed, 15.5s, prompt=1098, completion=205

Selection Rationale:
- All 4 plans passed tests (voting threshold met)
- Plan 2 was fastest (14.9s) → selected as best
```

---

## Memory/Energy Consumption

### Carbon Emissions
```
Baseline: 0.00126 kg CO₂
MultiPlan: 0.00631 kg CO₂
Ratio: 5.0× more emissions
```

### Energy Consumption  
```
Baseline: 0.0047 kWh
MultiPlan: 0.0236 kWh
Ratio: 5.0× more energy
```

**Note**: Emissions scale roughly linearly with computational work (4× more LLM calls).

---

## Conclusion: When to Use Each Approach

### ✅ Use **Baseline** (Single-Pass) when:
- You need fast results
- You have limited computational budget
- Task success rate is already acceptable
- You want minimal environmental impact
- Production efficiency is priority

### ✅ Use **MultiPlan** (4-Candidate) when:
- You want to study LLM behavior diversity
- You need explainability (4 different approaches)
- Task is critical (4 attempts improve confidence)
- You have research budget for exploration
- You want to understand which temperatures work best

### ❌ Don't expect **MultiPlan** to:
- Improve accuracy (same 60% on this benchmark)
- Reduce computational cost
- Be faster than baseline
- Automatically solve harder problems

---

## Data Sources

Generated from:
- `/Baseline/results/BASELINE-CORRECTED.json`
- `/MultiPlan/results/MULTIPLAN-CORRECTED.json`
- `/Baseline/results/BASELINE-tasks-CORRECTED.csv`
- `/MultiPlan/results/MULTIPLAN-tasks-CORRECTED.csv`

**Correction Applied**: April 12, 2026
- Changed from: `len(chat_hashes)` [best plan only]
- Changed to: `executor_calls` [aggregates all plans]

