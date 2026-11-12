#!/usr/bin/env bash
# run.sh — launch WS Skim from its venv (Linux / macOS).
# Operators usually double-click the WS Skim desktop icon instead.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$HERE/start-ws-skim.sh"
