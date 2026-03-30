#!/usr/bin/env bash
set -euo pipefail

# Checks required tools for local Aider benchmark experiments.

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT_DIR/shared/scripts/lib/common.sh"

missing=0

check() {
  local cmd="$1"
  local install_hint="$2"

  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "MISSING: $cmd" >&2
    echo "  Install guidance (placeholder): $install_hint" >&2
    missing=1
  else
    echo "OK: $cmd -> $(command -v "$cmd")"
  fi
}

echo "== Checking prerequisites =="
check "python3" "Install Python 3.11+ (macOS: https://www.python.org/downloads/ or via Homebrew: brew install python)"
check "git" "Install git (macOS: xcode-select --install or brew install git)"
check "docker" "Install Docker Desktop: https://www.docker.com/products/docker-desktop/"
check "ollama" "Install Ollama: https://ollama.com/download"

echo
if [[ "$missing" -ne 0 ]]; then
  echo "ERROR: Missing required tools. Install them and re-run." >&2
  exit 2
fi

echo "All required tools are present."
