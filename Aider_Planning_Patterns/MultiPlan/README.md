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
