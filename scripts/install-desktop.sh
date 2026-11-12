#!/usr/bin/env bash
# install-desktop.sh — add (or remove) the WS Skim launcher on Linux.
#
# Creates:
#   ~/.local/share/applications/ws-skim.desktop   (application menu)
#   <Desktop>/ws-skim.desktop                       (desktop icon, if a
#                                                    Desktop folder exists)
# Both launch scripts/run.sh from this install with the WS SKIM icon.
#
#     ./scripts/install-desktop.sh            # add
#     ./scripts/install-desktop.sh --remove   # remove
#
# The Linux installer runs this for you; run it by hand only from a git
# checkout.
set -euo pipefail

APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
NAME="ws-skim.desktop"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APPS_DIR="$DATA_HOME/applications"

desktop_dir() {
    local d=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        d="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    fi
    # xdg-user-dir returns $HOME when no Desktop is configured; ignore that.
    if [ -z "$d" ] || [ "$d" = "$HOME" ]; then
        d="$HOME/Desktop"
    fi
    printf '%s\n' "$d"
}

DESKTOP_DIR="$(desktop_dir)"

refresh_menu() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
    fi
}

if [ "${1:-}" = "--remove" ]; then
    rm -f "$APPS_DIR/$NAME" "$DESKTOP_DIR/$NAME"
    refresh_menu
    echo "Removed WS Skim launchers."
    exit 0
fi

# Desktop Entry spec: quote Exec arguments that contain spaces; inside the
# quotes, backslash, double quote, backtick and dollar must be escaped.
quote_exec() {
    local s="$1"
    s="${s//\\/\\\\\\\\}"
    s="${s//\"/\\\\\"}"
    s="${s//\`/\\\\\`}"
    s="${s//\$/\\\\\$}"
    printf '"%s"' "$s"
}

ICON="$APP_DIR/scripts/assets/ws-skim.png"
RUN="$APP_DIR/scripts/run.sh"
[ -x "$RUN" ] || { echo "ERROR: $RUN not found or not executable." >&2; exit 1; }

write_entry() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=WS Skim
GenericName=Weak-signal skimmer
Comment=Wideband MSK144 meteor-scatter skimmer
Exec=$(quote_exec "$RUN")
Path=$APP_DIR
Icon=$ICON
Terminal=false
Categories=HamRadio;Network;
StartupNotify=true
EOF
    chmod 755 "$1"
}

mkdir -p "$APPS_DIR"
write_entry "$APPS_DIR/$NAME"
refresh_menu
echo "Added WS Skim to the application menu."

if [ -d "$DESKTOP_DIR" ]; then
    write_entry "$DESKTOP_DIR/$NAME"
    # GNOME (Ubuntu) only launches desktop files marked trusted.
    if command -v gio >/dev/null 2>&1; then
        gio set "$DESKTOP_DIR/$NAME" metadata::trusted true >/dev/null 2>&1 || true
    fi
    echo "Added WS Skim icon to $DESKTOP_DIR."
else
    echo "No Desktop folder found; skipped the desktop icon."
fi
