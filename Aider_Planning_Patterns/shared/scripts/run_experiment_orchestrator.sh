#!/bin/bash
set -euo pipefail

# Experiment Orchestrator for AIDER Planning Patterns
# Runs a full experiment across patterns, model sizes, and task counts
# with proper model management, energy tracking, and run deduplication

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
source "$ROOT_DIR/shared/scripts/lib/common.sh"

load_env
ensure_venv

cd "$ROOT_DIR"

# --- Parse command-line arguments ---
PATTERNS=()
MODELS=()
TASK_COUNT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --patterns)
      IFS=',' read -ra PATTERNS <<< "$2"
      for i in "${!PATTERNS[@]}"; do
        PATTERNS[$i]=$(echo "${PATTERNS[$i]}" | xargs)
      done
      shift 2
      ;;
    --models)
      IFS=',' read -ra MODELS <<< "$2"
      for i in "${!MODELS[@]}"; do
        MODELS[$i]=$(echo "${MODELS[$i]}" | xargs)
      done
      shift 2
      ;;
    --tasks)
      TASK_COUNT="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

# --- Validation ---
if [[ ${#PATTERNS[@]} -eq 0 ]]; then
  echo "ERROR: No patterns specified. Use --patterns baseline,decomposition,multiplan,reflection,rag" >&2
  exit 1
fi

if [[ ${#MODELS[@]} -eq 0 ]]; then
  echo "ERROR: No models specified. Use --models 'qwen2.5-coder:7b-instruct,qwen2.5-coder:32b-instruct'" >&2
  exit 1
fi

if [[ $TASK_COUNT -le 0 ]]; then
  echo "ERROR: Task count must be > 0. Use --tasks 10" >&2
  exit 1
fi

# --- Setup experiment directory and run table ---
EXPERIMENTS_DIR="$ROOT_DIR/experiments"
mkdir -p "$EXPERIMENTS_DIR"

TIMESTAMP=$(date +"%Y%m%d-%H%M%S")
EXPERIMENT_ID="${TIMESTAMP}--experiment"
RUN_TABLE="$EXPERIMENTS_DIR/run_table.csv"

# Initialize run table if it doesn't exist
if [[ ! -f "$RUN_TABLE" ]]; then
  echo "run_id,experiment_id,pattern,model,task_name,status,llm_calls,prompt_tokens,completion_tokens,planner_calls,executor_calls,duration_seconds,energy_kwh,pass_rate,timestamp" > "$RUN_TABLE"
fi

# --- Helper functions ---

# Health check: verify Ollama API is responsive (not full generation)
check_model_health() {
  local model="$1"
  local max_attempts=5
  local attempt=0
  
  echo "[$(date '+%H:%M:%S')] Checking Ollama health for model: $model"
  
  while [[ $attempt -lt $max_attempts ]]; do
    attempt=$((attempt + 1))
    
    # Simple check: can we list models? (fast,  not a full generation)
    local response
    response=$(curl -s --connect-timeout 3 --max-time 5 "$OLLAMA_API_BASE/api/tags" 2>&1)
    
    if echo "$response" | grep -q "\"models\"" 2>/dev/null; then
      echo "[$(date '+%H:%M:%S')] ✓ Ollama is responsive"
      return 0
    fi
    
    if [[ $attempt -lt $max_attempts ]]; then
      echo "[$(date '+%H:%M:%S')] Ollama not yet responsive... retrying (attempt $attempt/$max_attempts)"
      sleep 3
    fi
  done
  
  echo "[$(date '+%H:%M:%S')] ✗ Ollama failed health check after $max_attempts attempts"
  echo "[$(date '+%H:%M:%S')] Last response: $response"
  return 1
}

# Stop current model and start a new one
switch_model() {
  local new_model="$1"
  
  # If we have a current model running and it's different, stop it first
  if [[ -n "${CURRENT_MODEL:-}" ]] && [[ "$CURRENT_MODEL" != "$new_model" ]]; then
    echo "[$(date '+%H:%M:%S')] Switching model: $CURRENT_MODEL → $new_model"
    echo "[$(date '+%H:%M:%S')] Stopping Ollama..."
    pkill -f "ollama serve" || true
    sleep 3
    echo "[$(date '+%H:%M:%S')] Cooldown for model switch: 1 minute"
    sleep 60
  fi
  
  # Ensure model is pulled
  if ! ollama show "$new_model" >/dev/null 2>&1; then
    echo "[$(date '+%H:%M:%S')] Pulling model: $new_model"
    ollama pull "$new_model" || {
      echo "[$(date '+%H:%M:%S')] ✗ Failed to pull model: $new_model" >&2
      return 1
    }
  else
    echo "[$(date '+%H:%M:%S')] ✓ Model $new_model already available"
  fi
  
  # Start Ollama if not running
  if ! pgrep -f "ollama serve" >/dev/null; then
    echo "[$(date '+%H:%M:%S')] Starting Ollama server..."
    ollama serve > /tmp/ollama.log 2>&1 &
    OLLAMA_PID=$!
    echo "[$(date '+%H:%M:%S')] Ollama PID: $OLLAMA_PID, waiting for startup..."
    sleep 10  # Increased from 5 to give Ollama time to start
  else
    echo "[$(date '+%H:%M:%S')] Ollama already running"
  fi
  
  # Health check with retries
  if ! check_model_health "$new_model"; then
    echo "[$(date '+%H:%M:%S')] ✗ Model health check failed" >&2
    tail -20 /tmp/ollama.log 2>/dev/null | head -10
    return 1
  fi
  
  CURRENT_MODEL="$new_model"
  export CURRENT_MODEL
  export OLLAMA_MODEL="$new_model"
}

# Verify which model is actually loaded in Ollama by querying the API
verify_model_loaded() {
  local expected_model="$1"
  local api_base="${OLLAMA_API_BASE:-http://127.0.0.1:11434}"
  
  echo "[$(date '+%H:%M:%S')] Verifying model: $expected_model"
  
  # Make a quick API call to get the actual model name from response
  local response=$(curl -s --max-time 10 "$api_base/api/generate" \
    -H "Content-Type: application/json" \
    -d "{\"model\": \"$expected_model\", \"prompt\": \"test\", \"stream\": false}" 2>/dev/null)
  
  if [[ -z "$response" ]]; then
    echo "[$(date '+%H:%M:%S')] ✗ Could not reach Ollama API at $api_base" >&2
    return 1
  fi
  
  local actual_model=$(echo "$response" | grep -o '"model":"[^"]*"' | cut -d'"' -f4)
  
  if [[ -z "$actual_model" ]]; then
    echo "[$(date '+%H:%M:%S')] ✗ Could not determine loaded model from API response" >&2
    echo "[$(date '+%H:%M:%S')] Response snippet: $(echo "$response" | head -c 200)" >&2
    return 1
  fi
  
  echo "[$(date '+%H:%M:%S')] ✓ API reports model: $actual_model"
  
  # Check if the model matches what we requested
  if [[ "$actual_model" != "$expected_model" ]] && [[ "ollama/$actual_model" != "$expected_model" ]] && [[ "$actual_model" != "ollama/$expected_model" ]]; then
    echo "[$(date '+%H:%M:%S')] ✗ Model mismatch! Expected: $expected_model, Got: $actual_model" >&2
    return 1
  fi
  
  echo "[$(date '+%H:%M:%S')] ✓ Model verified: $actual_model"
  return 0
}

# Check if a (pattern, model) combo has been run before
run_combo_exists() {
  local pattern="$1"
  local model="$2"
  
  # Check if any task for this pattern/model combo exists
  if grep -q "^[^,]*,$EXPERIMENT_ID,$pattern,$model,[0-9]*," "$RUN_TABLE" 2>/dev/null; then
    return 0
  fi
  return 1
}

# Add task-level run entry to tracking table
add_task_run_to_table() {
  local run_id="$1"
  local pattern="$2"
  local model="$3"
  local task_name="$4"
  local status="$5"
  local llm_calls="$6"
  local prompt_tokens="$7"
  local completion_tokens="$8"
  local planner_calls="$9"
  local executor_calls="${10}"
  local duration="${11}"
  local energy="${12}"
  local pass_rate="${13}"
  
  echo "$run_id,$EXPERIMENT_ID,$pattern,$model,${task_name},$status,$llm_calls,$prompt_tokens,$completion_tokens,$planner_calls,$executor_calls,$duration,$energy,$pass_rate,$(date -u '+%Y-%m-%dT%H:%M:%SZ')" >> "$RUN_TABLE"
}

# Extract result metrics from harness output/results
extract_metrics() {
  local pattern="$1"
  local model="$2"
  local run_id="$3"
  local energy="0"
  local pass_rate="0"
  
  # Try to extract from results if available
  # This is a placeholder - actual extraction depends on where results are stored
  
  echo "$energy|$pass_rate"
}

# Map pattern name to script path
get_harness_script() {
  local pattern="$1"
  
  # Patterns are stored like: Baseline/, Decomposition/, MultiPlan/, Reflection/, Memory/RAG/
  case "$pattern" in
    baseline)
      echo "$ROOT_DIR/Baseline/scripts/baseline_harness.py"
      ;;
    decomposition)
      echo "$ROOT_DIR/Decomposition/scripts/decomposition_harness.py"
      ;;
    multiplan)
      echo "$ROOT_DIR/MultiPlan/scripts/multiplan_harness.py"
      ;;
    reflection)
      echo "$ROOT_DIR/Reflection/scripts/reflection_harness.py"
      ;;
    rag)
      echo "$ROOT_DIR/Memory/RAG/scripts/rag_harness.py"
      ;;
    *)
      echo ""
      ;;
  esac
}

# Get the run script for a pattern
get_run_script() {
  local pattern="$1"
  
  case "$pattern" in
    baseline)
      echo "$ROOT_DIR/shared/scripts/run_baseline.sh"
      ;;
    decomposition)
      echo "$ROOT_DIR/shared/scripts/run_decomposition.sh"
      ;;
    multiplan)
      echo "$ROOT_DIR/shared/scripts/run_multiplan.sh"
      ;;
    reflection)
      echo "$ROOT_DIR/shared/scripts/run_reflection.sh"
      ;;
    rag)
      echo "$ROOT_DIR/shared/scripts/run_rag.sh"
      ;;
    *)
      echo ""
      ;;
  esac
}

# Run a single pattern/model/task combination
run_benchmark() {
  local pattern="$1"
  local model="$2"
  local run_id="${TIMESTAMP}--${pattern}--${model//:/_}"
  
  # Check if run already exists
  if run_combo_exists "$pattern" "$model"; then
    echo "[$(date '+%H:%M:%S')] ⊘ Pattern/model combo already run: $pattern / $model"
    return 0
  fi
  
  echo ""
  echo "=========================================="
  echo "Running: $pattern / $model / $TASK_COUNT tasks"
  echo "Run ID: $run_id"
  echo "=========================================="
  
  # Switch to this model
  if ! switch_model "$model"; then
    echo "[$(date '+%H:%M:%S')] ✗ Failed to prepare model: $model" >&2
    add_task_run_to_table "$run_id" "$pattern" "$model" "task_init" "FAILED_MODEL_SETUP" "0" "0" "0" "0" "0" "0" "0" "0"
    return 1
  fi
  
  # Verify the model is actually loaded
  if ! verify_model_loaded "$model"; then
    echo "[$(date '+%H:%M:%S')] ✗ Failed to verify model is loaded: $model" >&2
    add_task_run_to_table "$run_id" "$pattern" "$model" "task_init" "FAILED_MODEL_VERIFY" "0" "0" "0" "0" "0" "0" "0" "0"
    return 1
  fi
  
  # Cooldown between runs (30 seconds)
  echo "[$(date '+%H:%M:%S')] Cooldown: 30 seconds before benchmarking"
  sleep 30
  
  # Fresh clone of benchmark repos for this pattern/model combo
  echo "[$(date '+%H:%M:%S')] Setting up fresh benchmark repos..."
  if [[ -d "$ROOT_DIR/benchmark/repos/aider" ]]; then
    rm -rf "$ROOT_DIR/benchmark/repos/aider"
  fi
  if [[ -d "$ROOT_DIR/benchmark/repos/polyglot-benchmark" ]]; then
    rm -rf "$ROOT_DIR/benchmark/repos/polyglot-benchmark"
  fi
  bash "$ROOT_DIR/shared/scripts/setup_benchmark.sh" > /dev/null 2>&1 || {
    echo "[$(date '+%H:%M:%S')] ✗ Failed to setup benchmark" >&2
    add_task_run_to_table "$run_id" "$pattern" "$model" "task_init" "FAILED_SETUP" "0" "0" "0" "0" "0" "0" "0" "0"
    return 1
  }
  
  # Get the run script for this pattern
  local run_script=$(get_run_script "$pattern")
  if [[ ! -f "$run_script" ]]; then
    echo "[$(date '+%H:%M:%S')] ✗ Run script not found: $run_script" >&2
    add_task_run_to_table "$run_id" "$pattern" "$model" "task_init" "FAILED_NO_SCRIPT" "0" "0" "0" "0" "0" "0" "0" "0"
    return 1
  fi
  
  echo "[$(date '+%H:%M:%S')] Executing: $run_script"
  echo "[$(date '+%H:%M:%S')] Environment: OLLAMA_MODEL=$model, OLLAMA_API_BASE=$OLLAMA_API_BASE, AIDER_BENCH_NUM_TESTS=$TASK_COUNT"
  
  # Record start time
  local start_epoch=$(date +%s)
  
  # Run the benchmark script with our configuration
  # IMPORTANT: Pass OLLAMA_API_BASE and OLLAMA_MODEL so they override .env defaults
  if OLLAMA_API_BASE="$OLLAMA_API_BASE" OLLAMA_MODEL="$model" AIDER_BENCH_NUM_TESTS="$TASK_COUNT" bash "$run_script" 2>&1 | tee "$EXPERIMENTS_DIR/${pattern}_${model//:/_}.log"; then
    local exit_code=0
  else
    local exit_code=$?
  fi
  
  # Record end time
  local end_epoch=$(date +%s)
  local duration=$((end_epoch - start_epoch))
  
  if [[ $exit_code -ne 0 ]]; then
    echo "[$(date '+%H:%M:%S')] ✗ Benchmark run failed with exit code $exit_code"
    add_task_run_to_table "$run_id" "$pattern" "$model" "task_init" "FAILED_EXECUTION" "0" "0" "0" "0" "0" "$duration" "0" "0"
    return 1
  fi
  
  # Extract task-level results from harness output
  echo "[$(date '+%H:%M:%S')] Extracting task-level results for $pattern"
  
  # Find the most recent results .tasks.csv file for this pattern
  # (the harness creates files with timestamps, so we use glob to find them)
  local results_csv=""
  local pattern_results_dir=""
  
  case "$pattern" in
    baseline)
      pattern_results_dir="$ROOT_DIR/Baseline/results"
      ;;
    decomposition)
      pattern_results_dir="$ROOT_DIR/Decomposition/results"
      ;;
    multiplan)
      pattern_results_dir="$ROOT_DIR/MultiPlan/results"
      ;;
    reflection)
      pattern_results_dir="$ROOT_DIR/Reflection/results"
      ;;
    rag)
      pattern_results_dir="$ROOT_DIR/Memory/RAG/results"
      ;;
  esac
  
  echo "[$(date '+%H:%M:%S')] Looking for results in: $pattern_results_dir"
  echo "[$(date '+%H:%M:%S')] Pattern: *--${pattern}--${model//:/-}.tasks.csv"
  
  # Find the most recent tasks.csv file for this pattern
  if [[ -d "$pattern_results_dir" ]]; then
    # List available files for debugging
    echo "[$(date '+%H:%M:%S')] Available .tasks.csv files:"
    ls -t "$pattern_results_dir"/*.tasks.csv 2>/dev/null | while read f; do echo "[$(date '+%H:%M:%S')]   $(basename "$f")"; done || echo "[$(date '+%H:%M:%S')]   (none)"
    
    # Use the most recent .tasks.csv file (sort by modification time, get newest)
    results_csv=$(ls -t "$pattern_results_dir"/*--${pattern}--${model//:/-}.tasks.csv 2>/dev/null | head -1)
    
    if [[ -z "$results_csv" ]]; then
      # If exact match not found, try alternate pattern  
      results_csv=$(ls -t "$pattern_results_dir"/*--${pattern}--*.tasks.csv 2>/dev/null | head -1)
    fi
  fi
  
  echo "[$(date '+%H:%M:%S')] Found results CSV: ${results_csv:-"(not found)"}"
  
  # Parse task results and add each to tracking table
  if [[ -n "$results_csv" ]] && [[ -f "$results_csv" ]]; then
    # Skip header and process each task
    # CSV columns: task_path,testcase,passed,...,duration_seconds,...,codecarbon_energy_kwh,...
    while IFS=',' read -r task_path testcase passed arch planner executor arch_steps tries_recorded tries_passed tries_failed llm_calls duration_seconds prompt_tokens completion_tokens num_errors malformed exhausted timeouts energy_kwh emissions; do
      # Skip header row
      [[ "$testcase" == "testcase" ]] && continue
      
      local status=$([ "$passed" = "True" ] && echo "COMPLETED" || echo "FAILED")
      local pass_rate=$([ "$passed" = "True" ] && echo "1" || echo "0")
      
      add_task_run_to_table "$run_id" "$pattern" "$model" "$testcase" "$status" "$llm_calls" "$prompt_tokens" "$completion_tokens" "$planner" "$executor" "$duration_seconds" "$energy_kwh" "$pass_rate"
    done < "$results_csv"
  else
    echo "[$(date '+%H:%M:%S')] ⚠ Results CSV not found in: $pattern_results_dir"
    echo "[$(date '+%H:%M:%S')] Adding placeholder entry for pattern/model combo"
    add_task_run_to_table "$run_id" "$pattern" "$model" "task_all" "COMPLETED" "0" "0" "0" "0" "0" "$duration" "0" "0"
  fi
  
  echo "[$(date '+%H:%M:%S')] ✓ Run completed in ${duration}s"
}

# --- Main execution ---
echo "=========================================="
echo "AIDER Experiment Orchestrator"
echo "=========================================="
echo "Experiment ID: $EXPERIMENT_ID"
echo "Patterns: ${PATTERNS[@]}"
echo "Models: ${MODELS[@]}"
echo "Task count: $TASK_COUNT"
echo "Total planned runs: $((${#PATTERNS[@]} * ${#MODELS[@]}))"
echo "Start time: $(date)"
echo "Run table: $RUN_TABLE"
echo "=========================================="
echo ""

CURRENT_MODEL=""
export CURRENT_MODEL

# Run all pattern/model combinations
completed_runs=0
total_runs=$((${#PATTERNS[@]} * ${#MODELS[@]}))

for pattern in "${PATTERNS[@]}"; do
  for model in "${MODELS[@]}"; do
    completed_runs=$((completed_runs + 1))
    
    echo "[$(date '+%H:%M:%S')] Progress: $completed_runs/$total_runs"
    
    if ! run_benchmark "$pattern" "$model"; then
      echo "[$(date '+%H:%M:%S')] ⚠ Run failed, continuing to next run..."
    fi
    
    echo ""
  done
done

# Cleanup Ollama
if pgrep -f "ollama serve" >/dev/null; then
  echo "[$(date '+%H:%M:%S')] Stopping Ollama..."
  pkill -f "ollama serve" || true
fi

echo "=========================================="
echo "EXPERIMENT COMPLETE"
echo "=========================================="
echo "Results saved to: $EXPERIMENTS_DIR"
echo "Run table: $RUN_TABLE"
echo "End time: $(date)"
echo ""
echo "Run summary:"
echo "============"
tail -n +2 "$RUN_TABLE" 2>/dev/null | nl || echo "No runs recorded"

exit 0

