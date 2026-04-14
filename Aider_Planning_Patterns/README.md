# AIDER Planning Patterns - Experiment Framework

A reproducible, local experiment framework for evaluating **five different planning patterns** on Aider-based code-editing tasks using single local LLM models (Ollama).

**Quick facts:**
- **Platform**: macOS / Linux with Docker & Ollama
- **Models**: Any Ollama model (default: Qwen 2.5 Coder 7B)
- **Patterns**: Baseline, Decomposition, MultiPlan, Reflection, RAG Memory
- **Scale**: Full factorial experiments (5 patterns × N models × K tasks)
- **Status**: Task-level tracking with incremental result writing
- **Reproducibility**: All results trackable in CSV with timestamps

---

## Quick Start (Experienced Users)

If you already have **Ollama running** with a model ready:

```bash
# 1. Install dependencies
bash shared/scripts/setup_env.sh

# 2. Setup benchmark repos (one time)
bash shared/scripts/setup_benchmark.sh

# 3. Run full experiment: 5 patterns × 2 models × 10 tasks
bash shared/scripts/run_experiment_orchestrator.sh \
  --patterns baseline,decomposition,multiplan,reflection,rag \
  --models qwen2.5-coder:7b-instruct,qwen2.5-coder:32b-instruct \
  --tasks 10

# 4. Check results
cat experiments/run_table.csv
```

---

## Full Installation Guide (Fresh Machine)

This section walks through setting up on a **brand new machine with nothing installed**.

### Step 1: Install Prerequisites

#### macOS
```bash
# 1a. Install Homebrew (if not already installed)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# 1b. Install required tools
brew install python@3.11 git docker

# 1c. Install Ollama (for local LLM)
# Download from: https://ollama.ai
# OR via Homebrew:
brew install ollama
```

#### Linux (Ubuntu/Debian)
```bash
# 1a. Install required tools
sudo apt-get update
sudo apt-get install -y python3 python3-pip git docker.io

# 1b. Install Ollama
curl -fsSL https://ollama.ai/install.sh | sh

# 1c. Start Docker (may need sudo)
sudo systemctl start docker
sudo usermod -aG docker $USER  # Add user to docker group
newgrp docker  # Apply group membership
```

#### Windows
```
This framework is optimized for macOS/Linux. 
For Windows, use WSL2 (Windows Subsystem for Linux) and follow Linux instructions.
```

### Step 2: Verify Prerequisites

```bash
# Run the prerequisite check
bash shared/scripts/check_prereqs.sh
```

This verifies:
- ✓ Python 3 installed
- ✓ Git available
- ✓ Docker available
- ✓ Ollama available

Fix any missing tools before proceeding.

### Step 3: Clone and Enter Project

```bash
# Clone this repository
git clone <REPO_URL> aider-planning-experiments
cd aider-planning-experiments/Aider_Planning_Patterns

# Verify folder structure
ls -la
# Should show: README.md, Baseline/, Reflection/, MultiPlan/, shared/, etc.
```

### Step 4: Start Ollama Service

**In a separate terminal** (this runs continuously):

```bash
# Start Ollama server
ollama serve
```

The server will listen on `http://127.0.0.1:11434` by default.

**In your main terminal**, verify connectivity:

```bash
# Test Ollama is responding (may take 10-20 seconds)
curl http://127.0.0.1:11434/api/tags
```

Expected output: `{"models":[...]}`

### Step 5: Pull Your Model

While keeping Ollama server running, **in another terminal**:

```bash
# Pull the default model (Qwen 2.5 Coder 7B - ~4.7GB)
ollama pull qwen2.5-coder:7b-instruct

# (Optional) Pull additional model for multi-model experiments
ollama pull qwen2.5-coder:32b-instruct
```

Verify models are available:

```bash
ollama list
# Should show: qwen2.5-coder:7b-instruct
```

### Step 6: Setup Python Environment

Back in your main terminal:

```bash
# Install Python dependencies
bash shared/scripts/setup_env.sh

# This creates:
# - .venv/ (virtual environment)
# - installs: pandas, csvq, other analysis tools
```

### Step 7: Setup Benchmark Repositories

```bash
# Clone benchmark repos (this fetches ~500MB)
bash shared/scripts/setup_benchmark.sh

# This clones:
# - Aider-AI/aider (the benchmark harness)
# - Aider-AI/polyglot-benchmark (exercise corpus)
# Into: benchmark/repos/
```

Verify:

```bash
ls benchmark/repos/
# Should show: aider/, polyglot-benchmark/
```

### Step 8: Configure Environment (Optional)

Create `.env` for custom settings:

```bash
# Copy template
cp .env.example .env

# Edit .env to customize:
# - OLLAMA_MODEL (default: qwen2.5-coder:7b-instruct)
# - OLLAMA_API_BASE (default: http://127.0.0.1:11434)
# - TASK_TIMEOUT (default: 900 seconds = 15 minutes)
# - AIDER_BENCH_SHUFFLE_TASKS (default: 1 = shuffled)
```

### Step 9: Test Single Pattern (Baseline)

Run one quick test to verify everything works:

```bash
# Run baseline on 3 tasks (should complete in ~5-10 minutes)
AIDER_BENCH_NUM_TESTS=3 bash shared/scripts/run_baseline.sh

# Watch for output:
# ✓ Ollama connectivity confirmed
# ✓ Benchmark repos found
# ✓ Running baseline variant...
# ✓ Completed
```

Check results:

```bash
ls -lah Baseline/results/
# Should show: TIMESTAMP--baseline--qwen2.5-coder-7b-instruct.tasks.csv
```

---

## ▶️ Running Experiments

### Single Pattern, Single Model, 10 Tasks

```bash
# Baseline only
bash shared/scripts/run_baseline.sh

# Or any other pattern:
bash shared/scripts/run_reflection.sh
bash shared/scripts/run_decomposition.sh
bash shared/scripts/run_multiplan.sh
bash shared/scripts/run_rag.sh
```

**Expected time**: ~30-50 minutes (depends on model speed)

### Full Factorial Experiment (Recommended)

Run all 5 patterns, 2 model sizes, 10 tasks each = **100 tracked tasks**:

```bash
bash shared/scripts/run_experiment_orchestrator.sh \
  --patterns baseline,decomposition,multiplan,reflection,rag \
  --models qwen2.5-coder:7b-instruct,qwen2.5-coder:32b-instruct \
  --tasks 10
```

**Expected time**: ~8-12 hours (5 patterns × 2 models × ~50 min per model)

**What happens:**
1. Iterates through each (pattern, model) pair
2. Switches Ollama model with proper cooldowns
3. Runs benchmark with 10 tasks per pattern
4. Extracts individual task results from harness output
5. Logs each task as separate row in `experiments/run_table.csv` (100 total rows)
6. Skips any runs already completed (resumable on interruption)

### Custom Experiment Examples

**Small test** (2 patterns, 1 model, 5 tasks = 10 tracked tasks):
```bash
bash shared/scripts/run_experiment_orchestrator.sh \
  --patterns baseline,reflection \
  --models qwen2.5-coder:7b-instruct \
  --tasks 5
```

**Larger experiment** (5 patterns, 3 models, 20 tasks each = 300 tracked tasks):
```bash
bash shared/scripts/run_experiment_orchestrator.sh \
  --patterns baseline,decomposition,multiplan,reflection,rag \
  --models qwen2.5-coder:7b-instruct,qwen2.5-coder:32b-instruct,deepseek-coder:7b \
  --tasks 20
```

**All patterns, single model** (useful for model comparison later):
```bash
bash shared/scripts/run_experiment_orchestrator.sh \
  --patterns baseline,decomposition,multiplan,reflection,rag \
  --models qwen2.5-coder:7b-instruct \
  --tasks 10
```

---

## 📊 Results and Tracking

### Main Tracking File

All experiments write individual task results to:

```
experiments/run_table.csv
```

Structure:

```csv
run_id,experiment_id,pattern,model,task_name,status,energy_kwh,duration_seconds,pass_rate,timestamp
20260413-120500--baseline--qwen2.5-coder-7b-instruct,20260413-120500,baseline,qwen2.5-coder:7b-instruct,accumulate,COMPLETED,0.000206,14.596,1,2026-04-13T12:05:00Z
20260413-120500--baseline--qwen2.5-coder-7b-instruct,20260413-120500,baseline,qwen2.5-coder:7b-instruct,acronym,FAILED,0.000350,27.394,0,2026-04-13T12:06:00Z
...
```

**Columns:**
- `run_id`: Unique identifier for this pattern/model combination
- `experiment_id`: Groups multiple runs from same experiment session
- `pattern`: Which planning pattern (baseline, reflection, etc.)
- `model`: Which Ollama model used
- `task_name`: Individual task name (accumulate, acronym, etc.)
- `status`: COMPLETED, FAILED_SETUP, FAILED_EXECUTION, etc.
- `energy_kwh`: Energy consumed by this task (from CodeCarbon)
- `duration_seconds`: Wall-clock time for this task
- `pass_rate`: 1 if passed, 0 if failed
- `timestamp`: UTC timestamp when logged

### Pattern-Specific Results

Each pattern also writes detailed results:

```
Baseline/results/TIMESTAMP--baseline--MODEL.tasks.csv
Decomposition/results/TIMESTAMP--decomposition--MODEL.tasks.csv
MultiPlan/results/TIMESTAMP--multiplan--MODEL.tasks.csv
Reflection/results/TIMESTAMP--reflection--MODEL.tasks.csv
Memory/RAG/results/TIMESTAMP--rag--MODEL.tasks.csv
```

Each `.tasks.csv` contains:
- Task name, pass/fail status
- Duration, token counts
- CodeCarbon energy metrics
- Per-LLM-call breakdown (pattern-specific)

### Analyzing Results

**View experiment summary:**

```bash
# Show all completed runs
head experiments/run_table.csv

# Count passes per pattern
tail -n +2 experiments/run_table.csv | cut -d',' -f3,9 | sort | uniq -c

# Pass rate by pattern+model
tail -n +2 experiments/run_table.csv | \
  awk -F',' '{print $3"/"$4": "$9}' | sort | paste -sd, - | column -t -s','
```

**Using csvq for advanced queries:**

```bash
# Total pass rate
csvq "SELECT SUM(pass_rate) / COUNT(*) as pass_rate FROM experiments/run_table.csv"

# Per-pattern performance
csvq "SELECT pattern, SUM(pass_rate) as passed, COUNT(*) as total FROM experiments/run_table.csv GROUP BY pattern"

# Hardest tasks
csvq "SELECT task_name, COUNT(*) as total, SUM(pass_rate) as passed FROM experiments/run_table.csv GROUP BY task_name ORDER BY passed ASC"

# Energy by pattern (only if > 0)
csvq "SELECT pattern, AVG(energy_kwh) as avg_energy FROM experiments/run_table.csv WHERE energy_kwh > 0 GROUP BY pattern"
```

---

## 📘 Planning Patterns Operationalization

All five patterns use **AIDER's SINGLE-MODEL CODE MODE** (not architect/editor dual-model) to isolate planning effects and ensure fair comparison.

| Pattern | Purpose | Key Technique | Model Calls | Token Cost | Implementation |
|---------|---------|--------------|------------|-----------|------------------|
| **Baseline** | Error-driven baseline | Plain instructions + test errors | 1-2 per task | 1× | `Baseline/scripts/baseline_harness.py` |
| **Decomposition** | Incremental sub-planning | "Plan next step" prompts interleaved with execution | 2-4 per cycle | 2-3× | `Decomposition/scripts/decomposition_harness.py` |
| **MultiPlan** | Self-consistent selection | Generate 4 plans (temp: 0.3, 0.7, 1.0, 1.5), majority vote | 4 planning + evals | 3-4× | `MultiPlan/scripts/multiplan_harness.py` |
| **Reflection** | Error analysis + refinement | Generate → Reflect on errors → Refine fixes → Retry | 3 per cycle (gen + reflect + refine) | 2-5× | `Reflection/scripts/reflection_harness.py` |
| **RAG Memory** | In-context learning | Retrieve 3 similar solutions, inject as examples | 1 + retrieval | 1.1× | `Memory/RAG/scripts/rag_harness.py` |

### Baseline Pattern

**operationalization**: Pure error-driven retry. No reflection, decomposition, or memory.

- **Prompt 1 (Initial)**: Task instructions + file list
- **Prompt 2+ (Errors)**: Previous code + test error output

**Example flow:**
```
try 1: Generate code → Test fails (syntax error)
        Receive error: "NameError: unfined variable X"
try 2: Refine code using error feedback → Test fails (logic error)
try 3: Final refinement attempt
```

**Config:** max 2 attempts (1 initial + 1 retry)

### Decomposition Pattern

**Operationalization**: Interleaved planning and execution. Breaks task into steps.

- **Planning phase**: "Plan the next step to <subtask>"
- **Execution phase**: "Implement the plan: <step_description>"
- **Cycle**: Generate plan → test → on error, generate new plan

**Example flow:**
```
cycle 1: Plan: "Parse input with regex"
         Execute: "Write parser code"
         Test → fails
cycle 2: Analyze error → Plan: "Add error handling"
         Execute: "Refactor parser"
         Test → passes
```

**Config:** max 4 planning/execution cycles

### MultiPlan Pattern

**Operationalization**: Generate multiple candidate plans with temperature sampling, select best via majority vote.

- **Temperature 0.3** (deterministic): Conservative plan
- **Temperature 0.7** (creative)
- **Temperature 1.0** (baseline)
- **Temperature 1.5** (diverse)

Each plan executed independently, winner = passes most tests

**Example flow:**
```
plan_0.3: Conservative approach → tests: 7/10 pass
plan_0.7: Try clever optimization → tests: 5/10 pass
plan_1.0: Balanced → tests: 7/10 pass
plan_1.5: Creative → tests: 3/10 pass
Result: Majority vote favors 0.3 or 1.0
```

**Config:** 4 temperature levels, all executed

### Reflection Pattern

**Operationalization**: Failed attempts trigger reflection phase. Analyze failures, generate fixes.

- **Phase 1**: Generate initial code
- **Phase 2** (on error): "Reflect: Why did these tests fail? Root causes?"
- **Phase 3**: "Refine: What specific fixes address each root cause?"
- **Phase 4**: Implement refined version

**Example flow:**
```
try 1: Generate → Test fails: "Error in line 23"
       Reflect: "Type mismatch between input parsing and calculation"
       Refine: "Add type casting before calculation"
try 2: Code with reflection → Test fails: "Edge case not handled"
try 3: Code with 2 reflections → Test passes
```

**Config:** max 3 attempts (allows 2 reflection cycles)

### RAG Memory Pattern

**Operationalization**: Retrieve similar solved tasks, inject as in-context examples.

- **Retrieval**: Find 3 most similar past solutions (by code structure/task description)
- **Injection**: Add as "Here are similar solved tasks:" examples
- **Execution**: Same as baseline, but with augmented context

**Example flow:**
```
Task: "Implement accumulate function"
Retrieve: Similar functions from past tasks
Inject: "Here's how sum() was implemented, pattern is similar"
Generate: Code learned from examples
```

**Config:** Top-3 retrieval, no additional model calls beyond generation

### Why Single-Model Code Mode Only?

All patterns deliberately use **AIDER's single-model code mode** (not architect/editor dual-model):

1. **Isolates planning effects**: Pattern innovation is the independent variable
2. **Fair comparison**: Same model, same task scope across all patterns
3. **Token budget control**: Can't blame model size for differences
4. **Reproducibility**: No hidden reasoning from architect model

---

## 🛠️ Troubleshooting

### Ollama Not Responding

```bash
# Check if Ollama service is running
curl http://127.0.0.1:11434/api/tags

# If hung or slow:
# 1. Kill the process
pkill -f "ollama serve"

# 2. Wait 10 seconds
sleep 10

# 3. Restart
ollama serve
```

### Benchmark Repos Not Found

```bash
# Re-setup benchmark repos
bash shared/scripts/setup_benchmark.sh

# Verify
ls benchmark/repos/aider/benchmark/
ls benchmark/repos/polyglot-benchmark/
```

### Docker Issues

```bash
# Ensure Docker is running
docker ps  # Should not error

# If error "Cannot connect to Docker daemon":
# macOS: Open Docker application
# Linux: sudo systemctl start docker

# If permission denied: 
# Linux: sudo usermod -aG docker $USER && newgrp docker
```

### Model Pulls Very Slowly

```bash
# Check model pull
ollama list

# If pull interrupted, resume:
ollama pull qwen2.5-coder:7b-instruct

# Try alternative model (faster):
ollama pull mistral:7b
```

### Task Timeout During Run

```bash
# Increase timeout in .env:
TASK_TIMEOUT=1800  # 30 minutes instead of 15

# Rerun
bash shared/scripts/run_baseline.sh
```

### Results CSV Not Updating

```bash
# Check if harness created output CSV
ls -la Baseline/results/

# If missing, check logs:
tail -50 benchmark/runs/*/run.log

# Ensure pattern-specific harness has write permissions:
chmod +x Baseline/scripts/baseline_harness.py
chmod +x Reflection/scripts/reflection_harness.py
```

### Experiment Orchestrator Hangs on Model Switch

```bash
# Current model may be stuck
# Kill Ollama and restart:
pkill -f ollama
sleep 5
ollama serve  # In separate terminal

# Resume experiment (it will skip completed runs)
bash shared/scripts/run_experiment_orchestrator.sh ...
```

---

## 📁 Project Structure

```
Aider_Planning_Patterns/
├── README.md                           ← This file
├── EXPERIMENT_ORCHESTRATOR.md          ← Detailed orchestrator reference
├── ORCHESTRATOR_REDESIGN_SUMMARY.md    ← Design notes (task-level tracking)
│
├── shared/                             ← Common utilities
│   ├── scripts/
│   │   ├── run_experiment_orchestrator.sh  ← Main entry point
│   │   ├── check_prereqs.sh                ← Verify dependencies
│   │   ├── setup_env.sh                    ← Setup .venv
│   │   ├── setup_benchmark.sh              ← Clone benchmark repos
│   │   └── lib/common.sh                   ← Shared functions
│   └── config/
│       ├── defaults.env                    ← Default settings
│       └── profiles/                       ← (Future) Named configs
│
├── Baseline/
│   ├── scripts/baseline_harness.py         ← Pattern implementation
│   ├── scripts/collect_results.py          ← Result aggregation
│   ├── README.md                           ← Detailed pattern docs
│   └── results/                            ← Output CSVs/JSONs
│
├── Reflection/
│   ├── scripts/reflection_harness.py       ← Reflection + refinement impl.
│   ├── README.md
│   └── results/
│
├── MultiPlan/
│   ├── scripts/multiplan_harness.py        ← Multi-plan selection impl.
│   ├── README.md
│   └── results/
│
├── Decomposition/
│   ├── scripts/decomposition_harness.py    ← Interleaved planning impl.
│   ├── README.md
│   └── results/
│
├── Memory/ (RAG)
│   ├── RAG/
│   │   ├── scripts/rag_harness.py          ← RAG memory impl.
│   │   ├── README.md
│   │   └── results/
│
├── benchmark/
│   ├── repos/                              ← Aider + polyglot-benchmark
│   │   ├── aider/
│   │   └── polyglot-benchmark/
│   └── runs/                               ← Run artifacts per execution
│
├── experiments/
│   └── run_table.csv                       ← Master tracking (all tasks)
│
└── .env.example                            ← Configuration template
```

---

## ❓ Frequently Asked Questions

**Q: Can I run multiple patterns in parallel?**
A: No, the orchestrator runs sequentially to keep energy tracking clean. This also prevents Ollama resource contention.

**Q: What if a run fails midway?**
A: The orchestrator tracks completed (pattern, model) pairs. Re-run the same command and it will skip completed runs and resume from the failure point.

**Q: How long does each pattern take?**
A: Roughly:
  - Baseline: 5-10 min per 10 tasks (simple loop)
  - Reflection: 15-25 min per 10 tasks (3 attempts with reflection)
  - Decomposition: 20-30 min per 10 tasks (multiple planning cycles)
  - MultiPlan: 30-40 min per 10 tasks (4 plans × execution)
  - RAG: 5-15 min per 10 tasks (baseline + retrieval overhead)

**Q: Can I use different models?**
A: Yes! Any Ollama model. Best paired models:
  - Small: `mistral:7b`, `qwen2.5-coder:7b`
  - Large: `llama2-uncensored:13b-q5_K_M`, `qwen2.5-coder:32b`

**Q: Are results reproducible?**
A: Mostly. Same model + same task order = mostly deterministic, but temperature-based sampling (multiplan) and reflection logic introduce minor variation. For true reproducibility, set `AIDER_BENCH_SHUFFLE_TASKS=0`.

**Q: What's the energy tracking?**
A: CodeCarbon integration (placeholder values for now). When fully integrated, `energy_kwh` column shows real consumption. Use for comparing pattern efficiency.

---

## 📚 More Information

- **Detailed Orchestrator Reference**: [EXPERIMENT_ORCHESTRATOR.md](EXPERIMENT_ORCHESTRATOR.md)
- **Task-Level Design Notes**: [ORCHESTRATOR_REDESIGN_SUMMARY.md](ORCHESTRATOR_REDESIGN_SUMMARY.md)
- **Pattern-Specific Details**: See `[Pattern]/README.md` for each planning pattern
- **Baseline/Reflection/MultiPlan**: Original implementations documented in each pattern folder

---

## 🤝 Contributing / Extending

To add a new planning pattern:

1. Create `NewPattern/scripts/newpattern_harness.py` (see baseline as template)
2. Add result aggregation to pattern harness
3. Create `NewPattern/README.md` with operationalization details
4. Add run script: `shared/scripts/run_newpattern.sh`
5. Update orchestrator: Add pattern to `get_run_script()` and `get_harness_script()` functions
6. Test: `bash shared/scripts/run_experiment_orchestrator.sh --patterns baseline,newpattern --models qwen2.5-coder:7b-instruct --tasks 3`

---

## 📄 License & Citation

[Add your license here]

---

**Last Updated**: April 13, 2026  
**Status**: Stable (task-level tracking, 5 patterns operationalized, full orchestrator)  
**Maintainer**: [Team Name]

