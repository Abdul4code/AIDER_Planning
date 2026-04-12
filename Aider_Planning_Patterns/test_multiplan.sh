#!/usr/bin/env bash
set -euo pipefail

# Quick test script for MultiPlan implementation
# Runs baseline and multiplan on a small set of tasks (5 exercises)
# and compares results

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "=========================================="
echo "MultiPlan Implementation Test"
echo "=========================================="

# Check if .env exists
if [[ ! -f ".env" ]]; then
    echo "ERROR: .env file not found. Please create it from .env.example and configure:"
    echo "  cp .env.example .env"
    echo "  # Edit .env with your Ollama settings"
    exit 1
fi

# Check if Ollama is running
echo ""
echo "Checking Ollama connectivity..."
PY=".venv/bin/python3"
if [[ ! -f "$PY" ]]; then
    echo "Setting up venv..."
    bash shared/scripts/setup_env.sh
fi

"$PY" - <<PY
import os
import urllib.request
import sys

base = os.environ.get("OLLAMA_API_BASE", "").strip()
# Try loading from .env if not in environment
if not base:
    with open(".env") as f:
        for line in f:
            if line.startswith("OLLAMA_API_BASE="):
                base = line.split("=", 1)[1].strip().rstrip("/")
                break

if not base:
    print("ERROR: OLLAMA_API_BASE not configured in .env", file=sys.stderr)
    sys.exit(1)

url = f"{base}/api/version"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        if resp.status == 200:
            print(f"✓ Ollama is reachable at {base}")
        else:
            print(f"ERROR: HTTP {resp.status} from Ollama", file=sys.stderr)
            sys.exit(1)
except Exception as e:
    print(f"ERROR: Cannot reach Ollama at {url}: {e}", file=sys.stderr)
    print(f"Make sure Ollama is running: ollama serve", file=sys.stderr)
    sys.exit(1)
PY

# Load env
set -a
source .env
set +a

echo ""
echo "=========================================="
echo "Step 1: Quick validation run"
echo "=========================================="

# Quick test: just 5 Python exercises, no shuffling
export AIDER_BENCH_NUM_TESTS=5
export AIDER_BENCH_LANGUAGES=python
export AIDER_BENCH_SHUFFLE_TASKS=0
export AIDER_BENCH_THREADS=1

echo "Running 5 Python exercises with baseline harness..."
echo "(Timeout: 15 min per task, Total: ~75 mins for all tasks and both variants)"
echo ""

# Run baseline
echo "--- Baseline (single-pass) ---"
bash shared/scripts/run_baseline.sh 2>&1 | tail -20

echo ""
echo "--- MultiPlan (4 plans per task) ---"
export AIDER_BENCH_NUM_PLANS=4
bash shared/scripts/run_multiplan.sh 2>&1 | tail -20

echo ""
echo "=========================================="
echo "Test Complete!"
echo "=========================================="
echo ""
echo "Check results in:"
echo "  Baseline/results/"
echo "  MultiPlan/results/"
echo ""
echo "Compare the .csv files to see:"
echo "  - passed_count / failed_count"
echo "  - llm_calls_total"
echo "  - duration_seconds"
