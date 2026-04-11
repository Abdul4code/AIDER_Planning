#!/usr/bin/env bash
set -euo pipefail

# Runs decomposition using an external sidecar harness.
# This keeps upstream Aider code unmodified and still writes the same metrics schema.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/shared/scripts/lib/common.sh"

load_env
ensure_venv

cd "$ROOT_DIR"

DECOMP_PROMPT=$'Before writing code, use INTERLEAVED decomposition/planning.\n\nContract-first requirements (must follow):\n1) Preserve exact public API expected by tests: function/class names, constants, method signatures, argument names, and return types.\n2) Use existing tests and starter file as the source of truth for payload/schema/field names and response format.\n3) Do not invent alternative APIs, endpoints, key names, or output formats.\n4) If input payload is JSON text, parse it before indexing fields. If output is expected as JSON text, return JSON text.\n5) Keep all required exports/symbols present; do not remove or rename them.\n\nInterleaved process (required):\n1) Reveal only the next sub-goal(s) from the current state.\n2) Produce a short immediate sub-plan (1-2 actions).\n3) Execute only those actions.\n4) Reassess state and repeat decomposition/planning.\n5) Stop when no further sub-goals are needed.\n\nOutput behavior guidance:\n- Think step-by-step, but keep final emitted code clean.\n- Do not modify tests or metadata files.'

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
HARNESS_SCRIPT="Decomposition/scripts/decomposition_harness.py"

if [[ ! -f "$AIDER_DIR/benchmark/Dockerfile" ]]; then
	echo "ERROR: Aider benchmark repo missing Dockerfile. Run: bash shared/scripts/setup_benchmark.sh" >&2
	exit 4
fi
if [[ ! -d "$POLYGLOT_DIR" ]]; then
	echo "ERROR: polyglot-benchmark missing. Run: bash shared/scripts/setup_benchmark.sh" >&2
	exit 4
fi
if [[ ! -f "$HARNESS_SCRIPT" ]]; then
	echo "ERROR: decomposition harness missing: $HARNESS_SCRIPT" >&2
	exit 4
fi

# --- Prepare run directory ---
ts="$(now_timestamp)"
model_safe="$(sanitize_for_path "$OLLAMA_MODEL")"
run_variant_name="decomposition"
results_dir="Decomposition/results"
run_name="${ts}--${run_variant_name}--${model_safe}"
run_dir="benchmark/runs/${run_name}"

mkdir -p "$run_dir"
mkdir -p "$run_dir/tmp.benchmarks"

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

if ! docker image inspect "$AIDER_BENCH_DOCKER_IMAGE" >/dev/null 2>&1; then
	echo "Building Docker image $AIDER_BENCH_DOCKER_IMAGE from $AIDER_DIR/benchmark/Dockerfile"
	docker build -t "$AIDER_BENCH_DOCKER_IMAGE" -f "$AIDER_DIR/benchmark/Dockerfile" "$AIDER_DIR" | tee "$run_dir/docker_build.log"
else
	echo "OK: Docker image exists: $AIDER_BENCH_DOCKER_IMAGE"
fi

host_ollama_base="$OLLAMA_API_BASE"
container_ollama_base="$host_ollama_base"
if [[ "$host_ollama_base" == http://127.0.0.1:* || "$host_ollama_base" == http://localhost:* ]]; then
	container_ollama_base="http://host.docker.internal:${host_ollama_base##*:}"
fi

model_arg="${AIDER_BENCH_MODEL_PREFIX}${OLLAMA_MODEL}"
exercises_dir="$AIDER_BENCH_EXERCISES_SUBDIR"

harness_cmd=(
	/workspace/Decomposition/scripts/decomposition_harness.py
	--run-name "$run_name"
	--model "$model_arg"
	--edit-format "$AIDER_BENCH_EDIT_FORMAT"
	--threads "$AIDER_BENCH_THREADS"
	--tries "${AIDER_BENCH_TRIES:-2}"
	--exercises-dir "$exercises_dir"
	--arch-max-steps "${AIDER_BENCH_ARCH_MAX_STEPS:-3}"
	--shuffle-tasks "${AIDER_BENCH_SHUFFLE_TASKS:-1}"
)

if [[ -n "${AIDER_BENCH_LANGUAGES:-}" ]]; then
	harness_cmd+=(--languages "$AIDER_BENCH_LANGUAGES")
fi
if [[ -n "${AIDER_BENCH_KEYWORDS:-}" ]]; then
	harness_cmd+=(--keywords "$AIDER_BENCH_KEYWORDS")
fi
if [[ -n "${AIDER_BENCH_NUM_TESTS:-}" ]]; then
	harness_cmd+=(--num-tests "$AIDER_BENCH_NUM_TESTS")
fi
if [[ -n "${AIDER_BENCH_NUM_CTX:-}" ]]; then
	harness_cmd+=(--num-ctx "$AIDER_BENCH_NUM_CTX")
fi

planned_total="$($PY - <<PY
import os
from pathlib import Path

polyglot_dir = Path("$EXERCISES_MOUNT_SRC")
languages = os.environ.get("AIDER_BENCH_LANGUAGES", "").strip()
keywords = os.environ.get("AIDER_BENCH_KEYWORDS", "").strip()
num_tests_raw = os.environ.get("AIDER_BENCH_NUM_TESTS", "").strip()

lang_filter = set()
if languages:
	lang_filter = {lang.strip().lower() for lang in languages.split(",") if lang.strip()}

keyword_filter = []
if keywords:
	keyword_filter = [k.strip() for k in keywords.split(",") if k.strip()]

num_tests = -1
if num_tests_raw:
	try:
		num_tests = int(num_tests_raw)
	except ValueError:
		num_tests = -1

test_paths = []
if polyglot_dir.exists() and polyglot_dir.is_dir():
	for lang_dir in polyglot_dir.iterdir():
		if not lang_dir.is_dir():
			continue
		if lang_filter and lang_dir.name.lower() not in lang_filter:
			continue

		practice = lang_dir / "exercises" / "practice"
		if not practice.exists():
			continue

		for ex_dir in practice.iterdir():
			if not ex_dir.is_dir():
				continue
			rel = str(ex_dir.relative_to(polyglot_dir))
			if keyword_filter and not any(k in rel for k in keyword_filter):
				continue
			test_paths.append(rel)

total = len(test_paths)
if num_tests > 0:
	total = min(total, num_tests)

print(total)
PY
)"

echo "== Running decomposition benchmark (sidecar harness) =="
echo "Run: $run_name"
echo "Variant: $run_variant_name"
echo "Model: $model_arg"
echo "Planned tasks: $planned_total"
echo "Threads: $AIDER_BENCH_THREADS"
echo "Tries per task: ${AIDER_BENCH_TRIES:-2}"
if [[ -n "${AIDER_BENCH_NUM_CTX:-}" ]]; then
	echo "Context window override (--num-ctx): $AIDER_BENCH_NUM_CTX"
fi
if [[ -n "${AIDER_BENCH_LLM_TIMEOUT:-}" ]]; then
	echo "LLM API timeout override: ${AIDER_BENCH_LLM_TIMEOUT}s"
fi
echo "Ollama (host):      $host_ollama_base"
echo "Ollama (container): $container_ollama_base"
echo "Live logs: benchmark/runs/$run_name/run.log"
echo "Error logs: benchmark/runs/$run_name/run.err.log"

summary_json="${results_dir}/${run_name}.json"
summary_csv="${results_dir}/${run_name}.csv"
task_csv="${results_dir}/${run_name}.tasks.csv"
collect_stop_file="$run_dir/.collect.stop"
rm -f "$collect_stop_file"

"$PY" Baseline/scripts/collect_results.py \
	--run-name "$run_name" \
	--run-dir "$run_dir/tmp.benchmarks" \
	--model "$model_arg" \
	--out-json "$summary_json" \
	--out-csv "$summary_csv" \
	--out-task-csv "$task_csv" \
	--stream-task-csv \
	--poll-interval 2 \
	--stop-file "$collect_stop_file" \
	1>"$run_dir/collect_stream.log" \
	2>"$run_dir/collect_stream.err.log" &
collect_pid=$!

echo "Per-task CSV (live): $task_csv"

bench_start_epoch="$(date +%s)"

set +e
docker run --rm \
	--add-host host.docker.internal:host-gateway \
	-e AIDER_DOCKER=1 \
	-e AIDER_BENCHMARK_DIR=/benchmarks \
	-e AIDER_BENCH_EXTRA_INSTRUCTIONS="$DECOMP_PROMPT" \
	-e AIDER_BENCH_LLM_TIMEOUT="${AIDER_BENCH_LLM_TIMEOUT:-}" \
	-e AIDER_BENCH_RETRY_TIMEOUT="${AIDER_BENCH_RETRY_TIMEOUT:-}" \
	-e OLLAMA_API_BASE="$container_ollama_base" \
	-v "$(cd "$ROOT_DIR" && pwd)":/workspace \
	-v "$(cd "$AIDER_DIR" && pwd)":/aider \
	-v "$(cd "$run_dir/tmp.benchmarks" && pwd)":/benchmarks \
	-v "$(cd "$EXERCISES_MOUNT_SRC" && pwd)":/benchmarks/polyglot-benchmark \
	-w /aider \
	"$AIDER_BENCH_DOCKER_IMAGE" \
	bash -lc "pip install -e .[dev] codecarbon >/dev/null && pybin=\$(command -v python3 || command -v python) && \"\$pybin\" ${harness_cmd[*]}" \
	> >(tee "$run_dir/run.log") \
	2> >(tee "$run_dir/run.err.log" >&2)
docker_rc=$?
set -e

touch "$collect_stop_file"
wait "$collect_pid" >/dev/null 2>&1 || true

if [[ "$docker_rc" -ne 0 ]]; then
	echo "ERROR: Decomposition run failed. See logs:" >&2
	echo "- $run_dir/run.log" >&2
	echo "- $run_dir/run.err.log" >&2
	exit 5
fi

bench_end_epoch="$(date +%s)"
bench_duration_seconds="$((bench_end_epoch - bench_start_epoch))"

echo "OK: Decomposition run completed. Logs at $run_dir"

"$PY" Baseline/scripts/collect_results.py \
	--run-name "$run_name" \
	--run-dir "$run_dir/tmp.benchmarks" \
	--model "$model_arg" \
	--timestamp "$(date -u "+%Y-%m-%dT%H:%M:%SZ")" \
	--duration-seconds "$bench_duration_seconds" \
	--out-json "$summary_json" \
	--out-csv "$summary_csv" \
	--out-task-csv "$task_csv" \
	1>"$run_dir/collect.log" \
	2>"$run_dir/collect.err.log" || {
		echo "ERROR: Result collection failed. See $run_dir/collect.err.log" >&2
		exit 6
	}

echo "OK: Summary written: $summary_json, $summary_csv, and $task_csv"
