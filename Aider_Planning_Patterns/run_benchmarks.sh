#!/usr/bin/env bash
set -euo pipefail

# Comprehensive benchmark runner for Baseline vs MultiPlan
# Executes both variants and generates comparative analysis

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "================================================================================"
echo "MULTIPLAN IMPLEMENTATION - EXPERIMENTAL EVALUATION"
echo "================================================================================"
echo ""
echo "This script will:"
echo "  1. Validate environment setup"
echo "  2. Run Baseline benchmark (reference)"
echo "  3. Run MultiPlan benchmark (4 candidate plans)"
echo "  4. Compare results against evaluation criteria"
echo ""
echo "Total estimated runtime: 30-40 minutes (for ~5-10 exercises)"
echo "Each task: ≤15 minutes (hard requirement)"
echo ""

# Step 1: Environment check
echo "================================================================================"
echo "STEP 1: ENVIRONMENT VALIDATION"
echo "================================================================================"
echo ""

if [[ ! -f ".env" ]]; then
    echo "ERROR: .env file not found!"
    echo "Please create it: cp .env.example .env"
    echo "Then configure OLLAMA_MODEL and OLLAMA_API_BASE"
    exit 1
fi

# Load environment
set -a
source .env
set +a

echo "✓ .env configuration loaded"
echo "  Model: $OLLAMA_MODEL"
echo "  Ollama: $OLLAMA_API_BASE"
echo ""

# Check venv
if [[ ! -f ".venv/bin/python" ]]; then
    echo "Setting up Python virtual environment..."
    bash shared/scripts/setup_env.sh
fi
echo "✓ Python environment ready"
echo ""

# Check benchmarks are cloned
if [[ ! -d "benchmark/repos/aider" ]]; then
    echo "Setting up benchmark repositories..."
    bash shared/scripts/setup_benchmark.sh
fi
echo "✓ Benchmark repositories ready"
echo ""

# Step 2: Verify Ollama connectivity
echo "================================================================================"
echo "STEP 2: OLLAMA CONNECTIVITY CHECK"
echo "================================================================================"
echo ""

PY=".venv/bin/python"
"$PY" - <<PY
import os
import urllib.request
import sys

base = os.environ.get("OLLAMA_API_BASE", "").rstrip("/")
url = f"{base}/api/version"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        if resp.status == 200:
            print("✓ Ollama is reachable and healthy")
            sys.exit(0)
except Exception as e:
    print(f"✗ Cannot reach Ollama at {url}")
    print(f"  Error: {e}")
    print("\nTo fix:")
    print("  1. Make sure Ollama is running: ollama serve")
    print("  2. Check OLLAMA_API_BASE is correct in .env")
    sys.exit(1)
PY

echo ""

# Step 3: Run Baseline
echo "================================================================================"
echo "STEP 3: RUNNING BASELINE BENCHMARK (REFERENCE)"
echo "================================================================================"
echo ""
echo "Configuration:"
echo "  - Tasks: $AIDER_BENCH_NUM_TESTS (or all if not set)"
echo "  - Language: ${AIDER_BENCH_LANGUAGES:-all}"
echo "  - Tries: ${AIDER_BENCH_TRIES:-2}"
echo "  - Threads: ${AIDER_BENCH_THREADS:-1}"
echo "  - Task timeout: ${AIDER_BENCH_TASK_TIMEOUT_SECONDS:-900}s (15 min)"
echo ""
echo "Starting baseline run..."
echo ""

BASELINE_START=$(date +%s)

if bash shared/scripts/run_baseline.sh 2>&1 | tee baseline_run.log; then
    BASELINE_STATUS="✓ COMPLETED"
else
    BASELINE_STATUS="✗ FAILED"
    echo "WARNING: Baseline run reported non-zero exit"
fi

BASELINE_END=$(date +%s)
BASELINE_DURATION=$((BASELINE_END - BASELINE_START))

echo ""
echo "Baseline run ${BASELINE_STATUS}"
echo "Duration: $(printf '%02d:%02d:%02d' $((BASELINE_DURATION/3600)) $((BASELINE_DURATION%3600/60)) $((BASELINE_DURATION%60)))"
echo ""

# Extract baseline result file
BASELINE_JSON=$(find "Baseline/results" -name "*.json" -type f | sort | tail -1)
if [[ -z "$BASELINE_JSON" ]]; then
    echo "ERROR: No baseline results found!"
    exit 1
fi
echo "Baseline results: $BASELINE_JSON"
echo ""

# Step 4: Run MultiPlan
echo "================================================================================"
echo "STEP 4: RUNNING MULTIPLAN BENCHMARK"
echo "================================================================================"
echo ""
echo "Configuration:"
echo "  - Tasks: $AIDER_BENCH_NUM_TESTS (or all if not set)"
echo "  - Language: ${AIDER_BENCH_LANGUAGES:-all}"
echo "  - Plans per task: 4 (temperature: 0.3, 0.7, 1.0, 1.5)"
echo "  - Threads: ${AIDER_BENCH_THREADS:-1}"
echo "  - Task timeout: ${AIDER_BENCH_TASK_TIMEOUT_SECONDS:-900}s (15 min total for all plans)"
echo ""
echo "Starting multiplan run..."
echo ""

MULTIPLAN_START=$(date +%s)

if bash shared/scripts/run_multiplan.sh 2>&1 | tee multiplan_run.log; then
    MULTIPLAN_STATUS="✓ COMPLETED"
else
    MULTIPLAN_STATUS="✗ FAILED"
    echo "WARNING: MultiPlan run reported non-zero exit"
fi

MULTIPLAN_END=$(date +%s)
MULTIPLAN_DURATION=$((MULTIPLAN_END - MULTIPLAN_START))

echo ""
echo "MultiPlan run ${MULTIPLAN_STATUS}"
echo "Duration: $(printf '%02d:%02d:%02d' $((MULTIPLAN_DURATION/3600)) $((MULTIPLAN_DURATION%3600/60)) $((MULTIPLAN_DURATION%60)))"
echo ""

# Extract multiplan result file
MULTIPLAN_JSON=$(find "MultiPlan/results" -name "*.json" -type f | sort | tail -1)
if [[ -z "$MULTIPLAN_JSON" ]]; then
    echo "ERROR: No multiplan results found!"
    exit 1
fi
echo "MultiPlan results: $MULTIPLAN_JSON"
echo ""

# Step 5: Compare Results
echo "================================================================================"
echo "STEP 5: COMPARATIVE ANALYSIS"
echo "================================================================================"
echo ""

python3 compare_multiplan_results.py "$BASELINE_JSON" "$MULTIPLAN_JSON"

COMPARE_RC=$?

echo ""
echo "================================================================================"
echo "SUMMARY"
echo "================================================================================"
echo ""
echo "Baseline Run:"
echo "  Duration: $(printf '%02d:%02d:%02d' $((BASELINE_DURATION/3600)) $((BASELINE_DURATION%3600/60)) $((BASELINE_DURATION%60)))"
echo "  Results: $BASELINE_JSON"
echo ""
echo "MultiPlan Run:"
echo "  Duration: $(printf '%02d:%02d:%02d' $((MULTIPLAN_DURATION/3600)) $((MULTIPLAN_DURATION%3600/60)) $((MULTIPLAN_DURATION%60)))"
echo "  Results: $MULTIPLAN_JSON"
echo ""

if [[ $COMPARE_RC -eq 0 ]]; then
    echo "✅ EVALUATION PASSED"
    echo "   All criteria met:"
    echo "   • Conformance: ~100% with research"
    echo "   • Performance: ≤15 min per task"
    echo "   • Accuracy: ≥ baseline"
else
    echo "⚠️  EVALUATION NEEDS REVIEW"
    echo "   Check results above for details"
fi

echo ""
echo "================================================================================"
echo "DETAILED LOGS"
echo "================================================================================"
echo ""
echo "Baseline logs:"
echo "  - Run details: baseline_run.log"
echo "  - Full results: $BASELINE_JSON"
echo ""
echo "MultiPlan logs:"
echo "  - Run details: multiplan_run.log"
echo "  - Full results: $MULTIPLAN_JSON"
echo ""
echo "To view detailed per-task results:"
echo "  python3 -m json.tool <results.json> | head -50"
echo ""

# Return appropriate exit code
exit $COMPARE_RC
