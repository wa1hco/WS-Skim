#!/usr/bin/env bash
# install.sh — WS Skim install / update for Linux (and macOS, untested).
#
# The Linux installer (ws-skim-linux-install.sh) runs this for you.  From a
# git checkout, run it from inside the tree:
#
#     git clone https://github.com/wa1hco/WS-Skim.git ws-skim
#     cd ws-skim
#     ./scripts/install.sh
#
# Re-runnable: existing venv is reused, requirements re-resolved.
# Sets up:
#   - .venv/ — Python virtual environment with pinned deps
#   - jt9 discovery verified
#   - scripts/run.sh ready to launch
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

# Find a Python WS Skim supports: 3.10 .. 3.13 (3.12 preferred).
#
# NOT 3.14: requirements pin numpy==1.26.4 (UHD's bindings are built against
# it), and numpy 1.26 on Python 3.14 silently overwrites arrays in place
# ("temporary elision" trusts a reference count that 3.14 no longer keeps
# the same way).  Seen on a 3.14 box as FT8 audio scrambled before jt9 read
# it: 0 decodes where the same IQ decodes 50 stations.  If the system Python
# is 3.14 and no 3.10-3.13 is installed, fetch a private Python 3.12 with uv
# (no root, nothing system-wide changes).
py_ok() {  # $1 = python command; true if it exists and is 3.10..3.13
    command -v "$1" >/dev/null 2>&1 &&
        "$1" -c 'import sys; sys.exit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)' 2>/dev/null
}
PYTHON_CMD="${WS_SKIM_PYTHON:-${MAP144_PYTHON:-}}"
if [ -n "$PYTHON_CMD" ]; then
    if ! py_ok "$PYTHON_CMD"; then
        echo "ERROR: WS_SKIM_PYTHON='$PYTHON_CMD' is not Python 3.10-3.13." >&2
        exit 1
    fi
else
    for c in python3.12 python3.13 python3.11 python3.10 python3; do
        if py_ok "$c"; then PYTHON_CMD="$c"; break; fi
    done
fi
if [ -z "$PYTHON_CMD" ]; then
    echo "This computer's Python is not one WS Skim supports; setting up a private Python 3.12 ..."
    UV="$(command -v uv || true)"
    [ -z "$UV" ] && [ -x "$HOME/.local/bin/uv" ] && UV="$HOME/.local/bin/uv"
    if [ -z "$UV" ]; then
        if command -v curl >/dev/null 2>&1; then
            curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
        else
            wget -qO- https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh >/dev/null
        fi
        UV="$HOME/.local/bin/uv"
    fi
    "$UV" python install 3.12 >/dev/null
    PYTHON_CMD="$("$UV" python find 3.12)"
    if ! py_ok "$PYTHON_CMD"; then
        echo "ERROR: could not set up Python 3.12." >&2
        echo "  Install Python 3.12 from your distro, then run the installer again." >&2
        exit 1
    fi
fi
"$PYTHON_CMD" -c 'import sys; print("Python %d.%d.%d OK" % sys.version_info[:3])'

# An existing venv on an unsupported Python (e.g. 3.14 from an earlier
# install) is rebuilt; the old one is kept beside it until the next update.
if [ -x "$REPO_DIR/.venv/bin/python" ] && ! py_ok "$REPO_DIR/.venv/bin/python"; then
    echo "Rebuilding the WS Skim environment on a supported Python ..."
    rm -rf "$REPO_DIR/.venv.old"
    mv "$REPO_DIR/.venv" "$REPO_DIR/.venv.old"
fi

# Debian/Ubuntu ship python3 without venv/ensurepip; catch that here with a
# clear fix instead of a confusing "ensurepip is not available" traceback.
if [ ! -d "$REPO_DIR/.venv" ] && ! "$PYTHON_CMD" -c 'import ensurepip, venv' >/dev/null 2>&1; then
    PYVER="$("$PYTHON_CMD" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    echo "ERROR: Python's venv module is missing." >&2
    echo "  On Ubuntu/Debian:  sudo apt install python3-venv python${PYVER}-venv" >&2
    echo "  Then run the installer again." >&2
    exit 1
fi

# Create venv if missing.  Idempotent: re-runs reuse the existing one.
#
# --system-site-packages is REQUIRED for the USRP/B210 source: the UHD Python
# bindings ship only as a system package (apt python3-uhd, in
# /usr/lib/python3/dist-packages/uhd) and have no pip wheel.  A plain isolated
# venv cannot see them, so map144 reports "UHD Python package not found" even
# though it's installed.  Exposing system site-packages lets the venv import
# uhd while pip-installed deps (numpy<2 etc.) still shadow their system copies.
if [ ! -d "$REPO_DIR/.venv" ]; then
    echo "Creating venv at $REPO_DIR/.venv (with --system-site-packages for UHD) ..."
    "$PYTHON_CMD" -m venv --system-site-packages "$REPO_DIR/.venv"
elif ! grep -q "include-system-site-packages = true" "$REPO_DIR/.venv/pyvenv.cfg" 2>/dev/null; then
    # Existing venv predates this requirement — flip it on in place so UHD
    # becomes visible without a full rebuild.
    echo "Enabling system-site-packages on existing venv (for UHD) ..."
    sed -i 's/include-system-site-packages = false/include-system-site-packages = true/' \
        "$REPO_DIR/.venv/pyvenv.cfg"
fi

# Use the venv's python directly — avoids needing to source activate.
VENV_PY="$REPO_DIR/.venv/bin/python"

echo "Upgrading pip ..."
"$VENV_PY" -m pip install --upgrade pip

echo "Installing requirements ..."
"$VENV_PY" -m pip install -r requirements.txt

# ── Optional GPU extras (CuPy → TFMF GPU path) ────────────────────────────
# Kept out of requirements.txt so GPU-less station boxes aren't burdened, but
# tracked here so a GPU box restores CuPy on every rebuild instead of silently
# losing it (the old "had cupy, rebuilt, lost it" trap).
#   explicit:  ./scripts/install.sh --gpu   or   WS_SKIM_GPU=1 ./scripts/install.sh   (0 disables)
#   auto:      an NVIDIA display GPU is present (lspci)
WS_SKIM_GPU="${WS_SKIM_GPU:-${MAP144_GPU:-}}"
if [ -n "${WS_SKIM_GPU:-}" ]; then
    WANT_GPU="$WS_SKIM_GPU"
else
    WANT_GPU=0
    for arg in "$@"; do [ "$arg" = "--gpu" ] && WANT_GPU=1; done
    if [ "$WANT_GPU" = "0" ] && command -v lspci >/dev/null 2>&1 \
       && lspci 2>/dev/null | grep -iE 'vga|3d|display' | grep -qi nvidia; then
        WANT_GPU=1
        echo "NVIDIA GPU detected — installing GPU extras (set WS_SKIM_GPU=0 to skip)."
    fi
fi
if [ "$WANT_GPU" = "1" ]; then
    # Optional, so pip's output goes to a log instead of the terminal: a
    # missing CuPy wheel (e.g. none yet for this Python) is not an install
    # failure and its ERROR lines read like one.
    GPU_LOG_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/ws-skim/logs"
    GPU_LOG="$GPU_LOG_DIR/install-gpu.log"
    mkdir -p "$GPU_LOG_DIR" 2>/dev/null || GPU_LOG=/dev/null   # never fatal under set -e
    echo "Installing GPU extras (requirements-gpu.txt) ..."
    if "$VENV_PY" -m pip install -r requirements-gpu.txt >"$GPU_LOG" 2>&1; then
        echo "GPU extras installed."
    else
        echo "Note: GPU acceleration is not available on this computer, so WS Skim"
        echo "      will use the CPU instead. Everything else is installed."
        [ "$GPU_LOG" = /dev/null ] || echo "      (Details: $GPU_LOG)"
    fi
fi

# Verify jt9 discovery.  Don't hard-fail — the operator can install
# WSJT-X afterwards (or, from a checkout, set WS_SKIM_JT9); a warning is enough.
echo "Verifying jt9 discovery ..."
JT9_PATH="$("$VENV_PY" -c 'from ws_skim.detection import find_jt9; p = find_jt9(); print(p if p else "")')"
if [ -z "$JT9_PATH" ]; then
    echo "WARNING: jt9 not found.  Install WSJT-X (https://wsjt.sourceforge.io/)," >&2
    echo "         which provides jt9, then start WS Skim again." >&2
else
    echo "jt9: $JT9_PATH"
fi

# Resolve the installed version from the package itself — single source
# of truth, no risk of the install banner drifting from what the app
# actually reports.
WS_SKIM_VERSION="$("$VENV_PY" -c 'from ws_skim.buildinfo import version_string; print(version_string())' 2>/dev/null || echo '???')"

cat <<EOF

WS Skim v$WS_SKIM_VERSION ready.

To run:
    ./scripts/run.sh

Or directly:
    $VENV_PY ws_skim.py

The first launch may take ~10 s while numba JIT-compiles its hot paths.
EOF
