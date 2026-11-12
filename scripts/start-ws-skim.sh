#!/usr/bin/env bash
# Open the WS Skim window. The desktop icon runs this file.
# Python and the program path are set here. The operator does not type them.

set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
# shellcheck disable=SC1091
source "$ROOT/scripts/_say.sh"

if [ "${1:-}" = "--install-shortcut" ]; then
  # shellcheck disable=SC1091
  source "$ROOT/scripts/_resolve-python.sh" || exit 1
  "$PY" "$ROOT/map144_app/desktop_install.py" "$ROOT" --shortcuts
  exit $?
fi

VENV_PY="$ROOT/.venv/bin/python"
if [ ! -x "$VENV_PY" ]; then
  ws_skim_say "WS Skim" "WS Skim is not installed yet.

Double-click Install WS Skim, then double-click WS Skim again."
  exit 1
fi

exec "$VENV_PY" "$ROOT/map144.py"
