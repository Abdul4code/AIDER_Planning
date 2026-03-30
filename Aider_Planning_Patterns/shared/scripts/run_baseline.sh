#!/usr/bin/env bash
set -euo pipefail

# Runs the Aider benchmark baseline using Ollama locally.
# This uses Docker because the Aider benchmark harness is designed to run in Docker.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/shared/scripts/lib/common.sh"

load_env
ensure_venv

cd "$ROOT_DIR"

# --- Validate prerequisites ---
for cmd in docker ollama git; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "ERROR: Missing $cmd. Run: bash shared/scripts/check_prereqs.sh" >&2
    exit 2
  fi
done

# --- Validate Ollama connectivity (host) ---
PY="$(python_venv_exec)"

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

# Optional: validate model exists locally (best-effort)
if ! ollama show "$OLLAMA_MODEL" >/dev/null 2>&1; then
  echo "WARN: Ollama model '$OLLAMA_MODEL' not found locally." >&2
  echo "      Run: ollama pull $OLLAMA_MODEL" >&2
fi

# --- Validate benchmark repos present ---
AIDER_DIR="benchmark/repos/aider"
POLYGLOT_DIR="benchmark/repos/polyglot-benchmark"

if [[ ! -f "$AIDER_DIR/benchmark/benchmark.py" ]]; then
  echo "ERROR: Aider benchmark harness missing. Run: bash shared/scripts/setup_benchmark.sh" >&2
  exit 4
fi
if [[ ! -d "$POLYGLOT_DIR" ]]; then
  echo "ERROR: polyglot-benchmark missing. Run: bash shared/scripts/setup_benchmark.sh" >&2
  exit 4
fi

# --- Prepare run directory ---
ts="$(now_timestamp)"
model_safe="$(sanitize_for_path "$OLLAMA_MODEL")"
run_name="${ts}--baseline--${model_safe}"
run_dir="benchmark/runs/${run_name}"

mkdir -p "$run_dir"
mkdir -p "$run_dir/tmp.benchmarks"

# Symlink the harness' expected tmp.benchmarks location to our run dir for reproducibility.
# (Upstream scripts commonly use ./tmp.benchmarks relative to the aider repo.)
if [[ -e "$AIDER_DIR/tmp.benchmarks" || -L "$AIDER_DIR/tmp.benchmarks" ]]; then
  rm -rf "$AIDER_DIR/tmp.benchmarks"
fi
# From benchmark/repos/aider -> ../../runs/<run_name>/tmp.benchmarks
ln -s "../../runs/${run_name}/tmp.benchmarks" "$AIDER_DIR/tmp.benchmarks"

# --- Build benchmark docker image (idempotent) ---
if ! docker image inspect "$AIDER_BENCH_DOCKER_IMAGE" >/dev/null 2>&1; then
  echo "Building Docker image $AIDER_BENCH_DOCKER_IMAGE from $AIDER_DIR/benchmark/Dockerfile"
  docker build -t "$AIDER_BENCH_DOCKER_IMAGE" -f "$AIDER_DIR/benchmark/Dockerfile" "$AIDER_DIR" | tee "$run_dir/docker_build.log"
else
  echo "OK: Docker image exists: $AIDER_BENCH_DOCKER_IMAGE"
fi

# --- Run benchmark in container ---
# IMPORTANT:
# - Container must reach the host's Ollama.
# - On macOS Docker supports host.docker.internal.
# We rewrite OLLAMA_API_BASE for the container.

host_ollama_base="$OLLAMA_API_BASE"
container_ollama_base="$host_ollama_base"
if [[ "$host_ollama_base" == http://127.0.0.1:* || "$host_ollama_base" == http://localhost:* ]]; then
  container_ollama_base="http://host.docker.internal:${host_ollama_base##*:}"
fi

model_arg="${AIDER_BENCH_MODEL_PREFIX}${OLLAMA_MODEL}"

# Exercise dir path passed to benchmark.py is relative to AIDER_BENCHMARK_DIR (/benchmarks)
# We mount the polyglot repo under /benchmarks/polyglot-benchmark
exercises_dir="$AIDER_BENCH_EXERCISES_SUBDIR"

# TODO: If upstream benchmark CLI changes, update this command.
bench_cmd=(
  python benchmark/benchmark.py "$run_name"
  --model "$model_arg"
  --edit-format "$AIDER_BENCH_EDIT_FORMAT"
  --threads "$AIDER_BENCH_THREADS"
  --exercises-dir "$exercises_dir"
)

echo "== Running benchmark =="
echo "Run: $run_name"
echo "Model: $model_arg"
echo "Ollama (host):      $host_ollama_base"
echo "Ollama (container): $container_ollama_base"

bench_start_epoch="$(date +%s)"

docker run --rm \
  --add-host host.docker.internal:host-gateway \
  -e AIDER_DOCKER=1 \
  -e AIDER_BENCHMARK_DIR=/benchmarks \
  -e OLLAMA_API_BASE="$container_ollama_base" \
  -v "$(cd "$AIDER_DIR" && pwd)":/aider \
  -v "$(cd "$run_dir/tmp.benchmarks" && pwd)":/benchmarks \
  -v "$(cd "$POLYGLOT_DIR" && pwd)":/benchmarks/polyglot-benchmark \
  -w /aider \
  "$AIDER_BENCH_DOCKER_IMAGE" \
  bash -lc "pip install -e .[dev] >/dev/null && ${bench_cmd[*]}" \
  1>"$run_dir/run.log" \
  2>"$run_dir/run.err.log" || {
    echo "ERROR: Benchmark run failed. See logs:" >&2
    echo "- $run_dir/run.log" >&2
    echo "- $run_dir/run.err.log" >&2
    exit 5
  }

bench_end_epoch="$(date +%s)"
bench_duration_seconds="$((bench_end_epoch - bench_start_epoch))"

echo "OK: Benchmark run completed. Logs at $run_dir"

# --- Collect summary ---
summary_json="Baseline/results/${run_name}.json"
summary_csv="Baseline/results/${run_name}.csv"

"$PY" Baseline/scripts/collect_results.py \
  --run-name "$run_name" \
  --run-dir "$run_dir/tmp.benchmarks" \
  --model "$model_arg" \
  --timestamp "$(date -u "+%Y-%m-%dT%H:%M:%SZ")" \
  --duration-seconds "$bench_duration_seconds" \
  --out-json "$summary_json" \
  --out-csv "$summary_csv" \
  1>"$run_dir/collect.log" \
  2>"$run_dir/collect.err.log" || {
    echo "ERROR: Result collection failed. See $run_dir/collect.err.log" >&2
    exit 6
  }

echo "OK: Summary written: $summary_json and $summary_csv"
