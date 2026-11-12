# Show a short message in a window when this PC has one.
# Prints the same words when it does not.
# Sourced by the other Linux start and install files. Not a program you double-click.

ws_skim_say() {
  _ws_skim_title=$1
  _ws_skim_text=$2
  if _ws_skim_try_dialog "$_ws_skim_title" "$_ws_skim_text"; then
    return 0
  fi
  printf '%s\n\n%s\n' "$_ws_skim_title" "$_ws_skim_text"
}

_ws_skim_have_display() {
  [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]
}

_ws_skim_try_dialog() {
  _ws_skim_title=$1
  _ws_skim_text=$2
  if ! _ws_skim_have_display; then
    return 1
  fi
  if command -v zenity >/dev/null 2>&1; then
    zenity --info --title="$_ws_skim_title" --text="$_ws_skim_text" --width=480 || true
    return 0
  fi
  if command -v kdialog >/dev/null 2>&1; then
    kdialog --title "$_ws_skim_title" --msgbox "$_ws_skim_text" || true
    return 0
  fi
  if command -v yad >/dev/null 2>&1; then
    yad --title="$_ws_skim_title" --text="$_ws_skim_text" --button=OK:0 --width=480 || true
    return 0
  fi
  if _ws_skim_tk_dialog "$_ws_skim_title" "$_ws_skim_text"; then
    return 0
  fi
  if command -v xmessage >/dev/null 2>&1; then
    xmessage -center -title "$_ws_skim_title" "$_ws_skim_text" || true
    return 0
  fi
  if command -v gxmessage >/dev/null 2>&1; then
    gxmessage -title "$_ws_skim_title" -center "$_ws_skim_text" || true
    return 0
  fi
  if _ws_skim_terminal_dialog "$_ws_skim_text"; then
    return 0
  fi
  return 1
}

_ws_skim_tk_dialog() {
  _ws_skim_title=$1
  _ws_skim_text=$2
  if ! command -v python3 >/dev/null 2>&1; then
    return 1
  fi
  WS_SKIM_SAY_TITLE=$_ws_skim_title
  WS_SKIM_SAY_TEXT=$_ws_skim_text
  export WS_SKIM_SAY_TITLE WS_SKIM_SAY_TEXT
  python3 -c '
import os, sys
try:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass
    messagebox.showinfo(
        os.environ.get("WS_SKIM_SAY_TITLE") or "WS Skim",
        os.environ.get("WS_SKIM_SAY_TEXT") or "",
    )
    root.destroy()
except Exception:
    sys.exit(1)
' && return 0
  return 1
}

_ws_skim_terminal_dialog() {
  _ws_skim_text=$1
  WS_SKIM_SAY_TEXT=$_ws_skim_text
  export WS_SKIM_SAY_TEXT
  _ws_skim_body='printf "%s\n\nPress Enter to close.\n" "$WS_SKIM_SAY_TEXT"; read _ws_skim_key || true'
  if command -v x-terminal-emulator >/dev/null 2>&1; then
    x-terminal-emulator -e /bin/sh -c "$_ws_skim_body" && return 0
  fi
  if command -v xfce4-terminal >/dev/null 2>&1; then
    xfce4-terminal -e "/bin/sh -c $_ws_skim_body" && return 0
  fi
  if command -v konsole >/dev/null 2>&1; then
    konsole -e /bin/sh -c "$_ws_skim_body" && return 0
  fi
  if command -v gnome-terminal >/dev/null 2>&1; then
    gnome-terminal -- /bin/sh -c "$_ws_skim_body" && return 0
  fi
  if command -v xterm >/dev/null 2>&1; then
    xterm -e /bin/sh -c "$_ws_skim_body" && return 0
  fi
  return 1
}
