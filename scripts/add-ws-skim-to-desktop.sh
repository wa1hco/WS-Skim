#!/usr/bin/env bash
# Install WS Skim: put the program in its place and the icon on the desktop.
# The desktop file "Install WS Skim" runs this with bash, so the executable
# bit is optional.
#
# A downloaded release (no .git) is copied to ~/.local/share/ws-skim
# ($XDG_DATA_HOME/ws-skim when set), replacing an older copy there, so the
# download can be deleted afterwards. A git checkout stays where it is.
set -eu
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Zip and some archive tools drop the executable bit. Put it back on the
# files a later double-click runs.
repair_ws_skim_exec_bits() {
  local f
  for f in \
    "$ROOT/scripts/Install WS Skim.desktop" \
    "$ROOT/scripts/add-ws-skim-to-desktop.sh" \
    "$ROOT/scripts/install-linux.sh" \
    "$ROOT/scripts/install.sh" \
    "$ROOT/scripts/start-ws-skim.sh" \
    "$ROOT/scripts/run.sh" \
    "$ROOT/scripts/run-router.sh" \
    "$ROOT/scripts/_resolve-python.sh" \
    "$ROOT/scripts/_say.sh"
  do
    if [ -f "$f" ]; then
      chmod a+x "$f" 2>/dev/null || chmod u+x "$f" || true
    fi
  done
}
repair_ws_skim_exec_bits

# shellcheck disable=SC1091
source "$ROOT/scripts/_say.sh"
export WS_SKIM_NEXT_CLICK="Install WS Skim"
# shellcheck disable=SC1091
source "$ROOT/scripts/_resolve-python.sh" || exit 1

if ! HOME_DIR="$("$PY" "$ROOT/map144_app/install_tree.py" "$ROOT")" \
    || [ -z "$HOME_DIR" ] || [ ! -f "$HOME_DIR/scripts/start-ws-skim.sh" ]; then
  ws_skim_say "WS Skim" "WS Skim could not copy itself into your home folder.

Check that there is free space, then double-click Install WS Skim again."
  exit 1
fi

# Create or update the virtual environment and write the desktop icons.
# WS_SKIM_SKIP_VENV=1 is a test hook; operators never set it.
POST_ARGS=("--shortcuts")
if [ "${WS_SKIM_SKIP_VENV:-}" != "1" ]; then
  POST_ARGS=("--venv" "--shortcuts")
fi
if ! POST_OUT="$("$PY" "$HOME_DIR/map144_app/desktop_install.py" "$HOME_DIR" "${POST_ARGS[@]}")"; then
  ws_skim_say "WS Skim" "WS Skim could not finish installing.

Check that there is free space and a working internet connection, then
double-click Install WS Skim again."
  exit 1
fi
printf '%s\n' "$POST_OUT"

if [ "$HOME_DIR" = "$ROOT" ]; then
  MSG="WS Skim is on the desktop and in the app menu.

Double-click WS Skim."
else
  MSG="WS Skim is installed, on the desktop, and in the app menu.

It lives in $HOME_DIR.
You can delete the downloaded folder now.

Double-click WS Skim."
fi

if printf '%s\n' "$POST_OUT" | grep -q '^JT9_MISSING:'; then
  HINT="$(printf '%s\n' "$POST_OUT" | sed -n 's/^JT9_MISSING://p')"
  MSG="$MSG

$HINT"
fi

ws_skim_say "WS Skim" "$MSG"
