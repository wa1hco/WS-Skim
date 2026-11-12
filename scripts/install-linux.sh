#!/usr/bin/env bash
# install-linux.sh — WS Skim install / update for Linux.
# Developer entry; operators double-click scripts/Install WS Skim.desktop.
# Same work as double-clicking Install WS Skim.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$HERE/add-ws-skim-to-desktop.sh"
