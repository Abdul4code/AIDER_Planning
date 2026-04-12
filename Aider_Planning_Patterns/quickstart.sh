#!/usr/bin/env bash
# Quick Start Guide for MultiPlan

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

echo "=========================================="
echo "MultiPlan Implementation - Quick Start"
echo "=========================================="
echo ""

# Step 1: Environment setup
echo "Step 1: Environment Setup"
echo "========================"
echo ""

if [[ ! -f ".env" ]]; then
    echo "Creating .env from template..."
    cp .env.example .env
    echo "⚠ IMPORTANT: Edit .env with your Ollama settings:"
    echo "  - OLLAMA_MODEL: your model name (e.g., qwen2.5-coder:7b-instruct)"
    echo "  - OLLAMA_API_BASE: your Ollama URL (e.g., http://127.0.0.1:11434)"
    echo ""
    read -p "Press Enter after editing .env..."
else
    echo "✓ .env already exists"
fi

# Step 2: Verify prerequisites
echo ""
echo "Step 2: Verifying Prerequisites"
echo "================================"

missing_tools=()
for cmd in python3 git docker ollama; do
    if command -v "$cmd" >/dev/null 2>&1; then
        echo "✓ $cmd available"
    else
        echo "✗ $cmd not found"
        missing_tools+=("$cmd")
    fi
done

if [[ ${#missing_tools[@]} -gt 0 ]]; then
    echo ""
    echo "ERROR: Missing tools: ${missing_tools[*]}"
    echo "Please install missing tools and try again"
    exit 1
fi

# Step 3: Validate implementation
echo ""
echo "Step 3: Validating MultiPlan Implementation"
echo "============================================"

if bash validate_multiplan.sh 2>&1 | tail -3; then
    :
fi

# Step 4: Setup venv
echo ""
echo "Step 4: Setting Up Python Virtual Environment"
echo "=============================================="

if [[ ! -f ".venv/bin/python" ]]; then
    echo "Creating virtual environment..."
    bash shared/scripts/setup_env.sh
else
    echo "✓ Virtual environment already exists"
fi

# Step 5: Benchmarks setup
echo ""
echo "Step 5: Setting Up Benchmark Repositories"
echo "=========================================="

if [[ ! -d "benchmark/repos/aider" ]]; then
    echo "Cloning benchmark repositories..."
    bash shared/scripts/setup_benchmark.sh
else
    echo "✓ Benchmark repositories already cloned"
fi

# Step 6: Test Ollama
echo ""
echo "Step 6: Testing Ollama Connectivity"
echo "===================================="

set -a
source .env
set +a

PY=".venv/bin/python"
"$PY" - <<PY 2>/dev/null
import os
import urllib.request
base = os.environ.get("OLLAMA_API_BASE", "").rstrip("/")
url = f"{base}/api/version"
try:
    with urllib.request.urlopen(url, timeout=3) as resp:
        if resp.status == 200:
            print("✓ Ollama is reachable")
except Exception as e:
    print(f"✗ Cannot reach Ollama: {e}")
    print("   Make sure Ollama is running: ollama serve")
    exit(1)
PY

# Step 7: Offer to run benchmarks
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo ""
echo "OPTION 1: Quick test (5 exercises, ~20 minutes)"
echo "  bash test_multiplan.sh"
echo ""
echo "OPTION 2: Full Baseline benchmark"
echo "  bash shared/scripts/run_baseline.sh"
echo ""
echo "OPTION 3: Full MultiPlan benchmark (4 plans per task)"
echo "  bash shared/scripts/run_multiplan.sh"
echo ""
echo "OPTION 4: Compare results after both runs"
echo "  python3 compare_multiplan_results.py \\"
echo '    "Baseline/results/<baseline-run>.json" \'
echo '    "MultiPlan/results/<multiplan-run>.json"'
echo ""
echo "For more information, see:"
echo "  - MultiPlan/README.md (architecture and usage)"
echo "  - MULTIPLAN_IMPLEMENTATION.md (technical details)"
echo "  - README.md (project overview)"
echo ""
