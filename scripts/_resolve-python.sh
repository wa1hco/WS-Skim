# Finds Python 3.10, 3.11, or 3.12 for the other Linux start and install files.
# Sets PY. Shows a plain message and returns 1 when Python is missing or too new.

_ws_skim_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$_ws_skim_here/_say.sh"

_ws_skim_next="${WS_SKIM_NEXT_CLICK:-WS Skim}"

_ws_skim_ok_version() {
  "$1" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 12) else 1)' 2>/dev/null
}

PY=""
for _ws_skim_cand in python3.12 python3.11 python3.10 python3 python; do
  if command -v "$_ws_skim_cand" >/dev/null 2>&1; then
    if _ws_skim_ok_version "$_ws_skim_cand"; then
      PY="$_ws_skim_cand"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  if command -v python3 >/dev/null 2>&1 || command -v python >/dev/null 2>&1; then
    _ws_skim_probe="$(command -v python3 2>/dev/null || command -v python)"
    _ws_skim_ver="$("$_ws_skim_probe" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || echo "unknown")"
    ws_skim_say "WS Skim" "This PC has Python ${_ws_skim_ver}. WS Skim needs Python 3.10, 3.11, or 3.12.

Install Python 3.12, then double-click ${_ws_skim_next} again."
  else
    ws_skim_say "WS Skim" "Python was not found, so WS Skim cannot start.

Install Python 3.12 (the python3 package, version 3.10, 3.11, or 3.12).
Then double-click ${_ws_skim_next} again."
  fi
  return 1 2>/dev/null || exit 1
fi
