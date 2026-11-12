#!/usr/bin/env bash
# WS Skim @VERSION@ installer for Linux.
#
#     bash ws-skim-linux-install.sh
#
# Installs (or updates) WS Skim in ~/.local/share/ws-skim, sets up its
# Python environment, and adds WS Skim to the application menu and the
# desktop. No administrator rights are needed. Needs Python 3.10+ and
# WSJT-X (for jt9).
#
# Options:
#   --dest DIR      install somewhere else
#   --no-desktop    skip the menu entry and desktop icon
#   --extract-only  unpack the files and stop (for testing)
#   --uninstall     remove the menu entry, desktop icon and install folder
#                   (including recordings in its MSK144 folder; settings
#                   and logs in ~/.config and ~/.local/state are kept)
set -euo pipefail

VERSION="@VERSION@"
DEST="$HOME/.local/share/ws-skim"
DESKTOP=1
EXTRACT_ONLY=0
UNINSTALL=0
MARKER=".ws-skim-install"

while [ $# -gt 0 ]; do
    case "$1" in
        --dest) DEST="$2"; shift 2 ;;
        --dest=*) DEST="${1#--dest=}"; shift ;;
        --no-desktop) DESKTOP=0; shift ;;
        --extract-only) EXTRACT_ONLY=1; shift ;;
        --uninstall) UNINSTALL=1; shift ;;
        -h|--help) sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
    esac
done

# Refuse to touch a folder we did not create, so a mistyped --dest can
# never wipe someone's files.
is_ours() { [ -f "$1/$MARKER" ]; }
is_empty_or_missing() { [ ! -e "$1" ] || [ -z "$(ls -A "$1" 2>/dev/null)" ]; }

if [ "$UNINSTALL" = 1 ]; then
    if ! is_ours "$DEST"; then
        echo "No WS Skim install found at $DEST." >&2
        exit 1
    fi
    "$DEST/scripts/install-desktop.sh" --remove || true
    rm -rf "$DEST"
    echo "WS Skim removed from $DEST."
    exit 0
fi

if ! is_ours "$DEST" && ! is_empty_or_missing "$DEST"; then
    echo "ERROR: $DEST exists and is not a WS Skim install. Choose another" >&2
    echo "       folder with --dest, or empty this one first." >&2
    exit 1
fi

echo "Installing WS Skim $VERSION into $DEST ..."
mkdir -p "$DEST"

# Update in place: replace the program files but keep the Python
# environment (.venv), the B210 kit env (env), and recordings/logs (MSK144).
find "$DEST" -mindepth 1 -maxdepth 1 \
    ! -name .venv ! -name env ! -name MSK144 ! -name "$MARKER" \
    -exec rm -rf {} +

PAYLOAD_LINE=$(awk '/^__WS_SKIM_PAYLOAD__$/ { print NR + 1; exit 0 }' "$0")
tail -n +"$PAYLOAD_LINE" "$0" | tar -xz -C "$DEST"
printf '%s\n' "$VERSION" > "$DEST/$MARKER"

if [ "$EXTRACT_ONLY" = 1 ]; then
    echo "Files unpacked into $DEST (--extract-only)."
    exit 0
fi

"$DEST/scripts/install.sh"

if [ "$DESKTOP" = 1 ]; then
    "$DEST/scripts/install-desktop.sh"
fi

cat <<EOF

WS Skim $VERSION is installed. Start it from the WS Skim icon on your
desktop or in the application menu.
To update later, run a newer installer the same way.
To remove it:  bash $(basename "$0") --uninstall
EOF
exit 0
__WS_SKIM_PAYLOAD__
