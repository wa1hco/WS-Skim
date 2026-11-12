#!/usr/bin/env bash
# Developer helper. Operators double-click Install WS Skim.
# This file now runs the same installer as the desktop file.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
exec /bin/bash "$HERE/add-ws-skim-to-desktop.sh"
