#!/usr/bin/env bash
set -euo pipefail

# Creates a local Python venv for helper scripts (result collection, validation).

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/shared/scripts/lib/common.sh"

cd "$ROOT_DIR"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found. Run: bash shared/scripts/check_prereqs.sh" >&2
  exit 2
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Creating venv at .venv/"
  rm -rf .venv
  python3 -m venv .venv
fi

PY="$(python_venv_exec)"

"$PY" -m pip install --upgrade pip >/dev/null
"$PY" -m pip install -r shared/python/requirements.txt

echo "OK: venv ready at .venv/"
