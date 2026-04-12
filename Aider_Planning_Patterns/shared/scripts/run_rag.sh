#!/usr/bin/env bash
set -euo pipefail

# Runs RAG-based memory augmented planning using an external sidecar harness.
# Retrieves past experiences and augments task instructions with relevant examples.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/shared/scripts/lib/common.sh"

load_env
ensure_venv

cd "$ROOT_DIR"

RAG_PROMPT=$'Use RAG (Retrieval Augmented Generation) memory to enhance planning.\n\nMemory-augmented approach:\n1) Retrieve relevant past experiences from memory pool\n2) Use successful solutions as examples to guide current task planning\n3) Apply same architectural patterns that worked in past similar tasks\n4) Execute with augmented instructions informed by relevant memories\n\nContract-first requirements (must follow):\n1) Preserve exact public API expected by tests: function/class names, constants, method signatures, argument names, and return types.\n2) Use existing tests and starter file as the source of truth for payload/schema/field names and response format.\n3) Do not invent alternative APIs, endpoints, key names, or output formats.\n4) If input payload is JSON text, parse it before indexing fields. If output is expected as JSON text, return JSON text.\n5) Keep all required exports/symbols present; do not remove or rename them.'

# --- Validate prerequisites ---
for cmd in docker ollama git; do
	if ! command -v "$cmd" >/dev/null 2>&1; then
		echo "ERROR: Missing $cmd. Run: bash shared/scripts/check_prereqs.sh" >&2
		exit 2
	fi
done

PY="$(python_venv_exec)"

# --- Validate Ollama connectivity (host) ---
"$PY" - <<PY
import os
import sys
import urllib.request

base = os.environ.get("OLLAMA_API_BASE", "").rstrip("/")
if not base:
		print("ERROR: OLLAMA_API_BASE is empty. Set it in .env (see .env.example)", file=sys.stderr)
		sys.exit(2)

url = f"{base}/api/version"
try:
		with urllib.request.urlopen(url, timeout=3) as resp:
				if resp.status != 200:
						raise RuntimeError(f"HTTP {resp.status}")
except Exception as e:
		print(f"ERROR: Cannot reach Ollama at {url}: {e}", file=sys.stderr)
		print("Start Ollama with: ollama serve", file=sys.stderr)
		sys.exit(3)
print("OK: Ollama reachable")
PY

if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
	echo "WARN: Ollama model '$OLLAMA_MODEL' not found locally." >&2
	echo "      Run: ollama pull $OLLAMA_MODEL" >&2
fi

# --- Validate repos and harness ---
AIDER_DIR="benchmark/repos/aider"
POLYGLOT_DIR="benchmark/repos/polyglot-benchmark"
HARNESS_SCRIPT="Memory/RAG/scripts/rag_harness.py"

if [[ ! -f "$AIDER_DIR/benchmark/Dockerfile" ]]; then
	echo "ERROR: Aider benchmark repo missing Dockerfile. Run: bash shared/scripts/setup_benchmark.sh" >&2
	exit 4
fi
if [[ ! -d "$POLYGLOT_DIR" ]]; then
	echo "ERROR: polyglot-benchmark missing. Run: bash shared/scripts/setup_benchmark.sh" >&2
	exit 4
fi
if [[ ! -f "$HARNESS_SCRIPT" ]]; then
	echo "ERROR: RAG harness missing: $HARNESS_SCRIPT" >&2
	exit 4
fi

# --- Prepare run directory ---
ts="$(now_timestamp)"
model_safe="$(sanitize_for_path "$OLLAMA_MODEL")"
run_variant_name="rag"
results_dir="Memory/RAG/results"
run_name="${ts}--${run_variant_name}--${model_safe}"
run_dir="benchmark/runs/${run_name}"

mkdir -p "$run_dir"
mkdir -p "$run_dir/tmp.benchmarks"
mkdir -p "$results_dir"

# --- Normalize exercises layout for old code-editing mode ---
EXERCISES_MOUNT_SRC="$POLYGLOT_DIR"
if [[ -d "$POLYGLOT_DIR/exercises/practice" && ! -d "$POLYGLOT_DIR/python/exercises/practice" ]]; then
	shim_dir="$run_dir/exercises-shim"
	mkdir -p "$shim_dir/python"
	rm -rf "$shim_dir/python/exercises"
	cp -R "$POLYGLOT_DIR/exercises" "$shim_dir/python/exercises"
	EXERCISES_MOUNT_SRC="$shim_dir"
fi

if [[ -e "$AIDER_DIR/tmp.benchmarks" || -L "$AIDER_DIR/tmp.benchmarks" ]]; then
	rm -rf "$AIDER_DIR/tmp.benchmarks"
fi
ln -s "../../runs/${run_name}/tmp.benchmarks" "$AIDER_DIR/tmp.benchmarks"

# --- Docker image ---
docker_image="${AIDER_BENCH_DOCKER_IMAGE}"
if [[ ! -f "$AIDER_DIR/benchmark/Dockerfile" ]]; then
	echo "ERROR: Dockerfile not found: $AIDER_DIR/benchmark/Dockerfile" >&2
	exit 1
fi

docker build -t "$docker_image" -f "$AIDER_DIR/benchmark/Dockerfile" "$AIDER_DIR" >/dev/null 2>&1 || {
	echo "WARNING: Docker image build failed or image already exists (continuing anyway)" >&2
}

# --- Determine if we're inside Docker (CI environment) ---
DOCKER_HOST_IP="${DOCKER_HOST_IP:-host.docker.internal}"
# Rewrite API base for Docker container (so container can reach host's Ollama)
OLLAMA_API_FOR_DOCKER="${OLLAMA_API_BASE}"
if [[ "$OLLAMA_API_BASE" == http://127.0.0.1:* || "$OLLAMA_API_BASE" == http://localhost:* ]]; then
	OLLAMA_API_FOR_DOCKER="http://host.docker.internal:${OLLAMA_API_BASE##*:}"
fi

# --- Run the harness inside Docker with memory support ---
echo "Running RAG memory harness (max 15 min per task)..."
docker run --rm \
	--add-host host.docker.internal:host-gateway \
	-e AIDER_DOCKER=1 \
	-e AIDER_BENCHMARK_DIR=/benchmarks \
	-e OLLAMA_API_BASE="$OLLAMA_API_FOR_DOCKER" \
	-e OLLAMA_MODEL="$OLLAMA_MODEL" \
	-e AIDER_BENCH_TASK_TIMEOUT_SECONDS=900 \
	-e AIDER_BENCH_LLM_TIMEOUT=120 \
	-e AIDER_BENCH_EXTRA_INSTRUCTIONS="$RAG_PROMPT" \
	-v "$(cd "$ROOT_DIR" && pwd)":/workspace \
	-v "$(cd "$AIDER_DIR" && pwd)":/aider \
	-v "$(cd "$run_dir/tmp.benchmarks" && pwd)":/benchmarks \
	-v "$(cd "$EXERCISES_MOUNT_SRC" && pwd)":/benchmarks/polyglot-benchmark \
	-w /aider \
	"$docker_image" \
	bash -lc "pip install -e .[dev] codecarbon >/dev/null && pybin=\$(command -v python3 || command -v python) && \"\$pybin\" /workspace/RAG/scripts/rag_harness.py \
		--run-name=\"$run_name\" \
		--model=\"${AIDER_BENCH_MODEL_PREFIX}${OLLAMA_MODEL}\" \
		--edit-format=\"${AIDER_BENCH_EDIT_FORMAT}\" \
		--threads=\"${AIDER_BENCH_THREADS}\" \
		--tries=\"${AIDER_BENCH_TRIES}\" \
		--num-tests=10 \
		--shuffle-tasks=\"$AIDER_BENCH_SHUFFLE_TASKS\" \
		--api-base=\"$OLLAMA_API_FOR_DOCKER\""

# --- Collect results ---
"$PY" Baseline/scripts/collect_results.py \
	--run-name "$run_name" \
	--run-dir "$run_dir/tmp.benchmarks" \
	--model "${AIDER_BENCH_MODEL_PREFIX}${OLLAMA_MODEL}" \
	--out-json "$results_dir/${run_name}.json" \
	--out-csv "$results_dir/${run_name}.csv" \
	--out-task-csv "$results_dir/${run_name}.tasks.csv"

echo ""
echo "Results saved to: $results_dir/${run_name}.*"
