#!/usr/bin/env bash
set -euo pipefail

# Clones the Aider repo (benchmark harness) and polyglot-benchmark (exercise corpus).

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/shared/scripts/lib/common.sh"

load_env

cd "$ROOT_DIR"

if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git not found. Run: bash shared/scripts/check_prereqs.sh" >&2
  exit 2
fi

mkdir -p benchmark/repos

AIDER_DIR="benchmark/repos/aider"
POLYGLOT_DIR="benchmark/repos/polyglot-benchmark"

clone_or_update() {
  local url="$1"
  local dir="$2"
  local ref="${3:-}"

  if [[ -d "$dir/.git" ]]; then
    echo "Updating $dir"
    git -C "$dir" fetch --all --tags
  else
    echo "Cloning $url -> $dir"
    git clone "$url" "$dir"
  fi

  if [[ -n "$ref" ]]; then
    echo "Checking out $dir at ref: $ref"
    git -C "$dir" checkout "$ref"
  fi
}

clone_or_update "$AIDER_REPO_URL" "$AIDER_DIR" "${AIDER_REPO_REF:-}"
clone_or_update "$POLYGLOT_REPO_URL" "$POLYGLOT_DIR" "${POLYGLOT_REPO_REF:-}"

echo
if [[ ! -f "$AIDER_DIR/benchmark/benchmark.py" ]]; then
  cat >&2 <<'MSG'
ERROR: Expected benchmark harness at benchmark/repos/aider/benchmark/benchmark.py but it was not found.
TODO: Upstream repo layout may have changed. Inspect the cloned repo and update this script.
MSG
  exit 3
fi

echo "OK: Benchmark repos ready."
echo "- Aider harness: $AIDER_DIR"
echo "- Exercises:    $POLYGLOT_DIR"
