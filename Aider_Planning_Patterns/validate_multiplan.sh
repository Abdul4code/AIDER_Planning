#!/usr/bin/env bash
# Implementation validation script
# Checks all components are in place and ready for testing

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "=========================================="
echo "MultiPlan Implementation Validation"
echo "=========================================="
echo ""

# Check files exist
echo "Checking files..."
files_ok=true

check_file() {
    if [[ -f "$1" ]]; then
        echo "✓ $1"
        return 0
    else
        echo "✗ MISSING: $1"
        files_ok=false
        return 1
    fi
}

check_dir() {
    if [[ -d "$1" ]]; then
        echo "✓ $1/"
        return 0
    else
        echo "✗ MISSING: $1/"
        files_ok=false
        return 1
    fi
}

# Harness
check_file "MultiPlan/scripts/multiplan_harness.py"

# Scripts
check_file "shared/scripts/run_multiplan.sh"

# Results directory
check_dir "MultiPlan/results"

# Documentation
check_file "MultiPlan/README.md"
check_file "MULTIPLAN_IMPLEMENTATION.md"
check_file "compare_multiplan_results.py"

# Test script
check_file "test_multiplan.sh"

echo ""
echo "Checking Python syntax..."
if python3 -m py_compile MultiPlan/scripts/multiplan_harness.py >/dev/null 2>&1; then
    echo "✓ multiplan_harness.py: syntax OK"
else
    echo "✗ multiplan_harness.py: syntax ERROR"
    files_ok=false
fi

if python3 -m py_compile compare_multiplan_results.py >/dev/null 2>&1; then
    echo "✓ compare_multiplan_results.py: syntax OK"
else
    echo "✗ compare_multiplan_results.py: syntax ERROR"
    files_ok=false
fi

echo ""
echo "Checking shell scripts..."
if bash -n shared/scripts/run_multiplan.sh >/dev/null 2>&1; then
    echo "✓ run_multiplan.sh: syntax OK"
else
    echo "✗ run_multiplan.sh: syntax ERROR"
    files_ok=false
fi

if bash -n test_multiplan.sh >/dev/null 2>&1; then
    echo "✓ test_multiplan.sh: syntax OK"
else
    echo "✗ test_multiplan.sh: syntax ERROR"
    files_ok=false
fi

echo ""
echo "Verifying key implementation details..."

# Check for key functions
if grep -q "def run_single_plan" MultiPlan/scripts/multiplan_harness.py; then
    echo "✓ run_single_plan() function found"
else
    echo "✗ run_single_plan() function missing"
    files_ok=false
fi

if grep -q "def select_best_plan" MultiPlan/scripts/multiplan_harness.py; then
    echo "✓ select_best_plan() function found"
else
    echo "✗ select_best_plan() function missing"
    files_ok=false
fi

if grep -q "def run_single_task_multiplan" MultiPlan/scripts/multiplan_harness.py; then
    echo "✓ run_single_task_multiplan() function found"
else
    echo "✗ run_single_task_multiplan() function missing"
    files_ok=false
fi

# Check for temperature sampling
if grep -q "temperatures = \[0.3, 0.7, 1.0, 1.5\]" MultiPlan/scripts/multiplan_harness.py; then
    echo "✓ Temperature sampling strategy found"
else
    echo "✗ Temperature sampling strategy missing"
    files_ok=false
fi

# Check for voting logic
if grep -q "passing_plans = \[r for r in plan_results if any(r.get" MultiPlan/scripts/multiplan_harness.py; then
    echo "✓ Majority vote logic found"
else
    echo "✗ Majority vote logic missing"
    files_ok=false
fi

echo ""
echo "=========================================="

if [[ "$files_ok" == "true" ]]; then
    echo "✓ All validation checks passed!"
    echo ""
    echo "Next steps:"
    echo "1. Configure .env: cp .env.example .env && nano .env"
    echo "2. Start Ollama: ollama serve"
    echo "3. Run quick test: bash test_multiplan.sh"
    echo "4. Full benchmark: bash shared/scripts/run_multiplan.sh"
    echo ""
    exit 0
else
    echo "✗ Some validation checks failed"
    echo "Please review the errors above"
    exit 1
fi
