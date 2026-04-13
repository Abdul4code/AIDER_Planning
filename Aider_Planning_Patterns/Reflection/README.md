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
