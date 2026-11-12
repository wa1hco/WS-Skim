#!/usr/bin/env bash
# run.sh — launch WS Skim from its venv (Linux / macOS).
# Re-runnable; passes any args straight through to map144.py.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"
if [ ! -x ".venv/bin/python" ]; then
    echo "ERROR: .venv not found.  Run ./scripts/install.sh first." >&2
    exit 1
fi
exec .venv/bin/python map144.py "$@"
