#!/usr/bin/env bash
set -euo pipefail

# Common helpers for scripts in this repo.

repo_root() {
  # Resolve to repo root (directory containing this file is shared/scripts/lib)
  local this_dir
  this_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  (cd "$this_dir/../../.." && pwd)
}

load_env() {
  local root
  root="$(repo_root)"

  # shellcheck disable=SC1091
  source "$root/shared/config/defaults.env"

  if [[ -f "$root/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$root/.env"
    set +a
  fi
}

need_cmd() {
  local cmd="$1"
  if ! command -v "$cmd" >/dev/null 2>&1; then
    return 1
  fi
}

now_timestamp() {
  date "+%Y%m%d-%H%M%S"
}

sanitize_for_path() {
  # Replace path-unfriendly chars with '-'
  local s="$1"
  s="${s//\//-}"
  s="${s//:/-}"
  s="${s// /-}"
  printf '%s' "$s"
}

python_venv_exec() {
  local root
  root="$(repo_root)"
  echo "$root/.venv/bin/python"
}

ensure_venv() {
  local root
  root="$(repo_root)"

  if [[ ! -x "$root/.venv/bin/python" ]]; then
    echo "ERROR: Python venv missing. Run: bash shared/scripts/setup_env.sh" >&2
    exit 2
  fi
}

http_get_json() {
  # Usage: http_get_json <url>
  local url="$1"
  python - "$url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
req = urllib.request.Request(url, headers={"Accept": "application/json"})
with urllib.request.urlopen(req, timeout=3) as resp:
    body = resp.read().decode("utf-8", errors="replace")
try:
    print(json.dumps(json.loads(body)))
except Exception:
    print(body)
PY
}

