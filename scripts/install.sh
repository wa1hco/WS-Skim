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

# Find a working Python ≥ 3.10.  Prefer ``python3`` since that's the
# convention every modern distro uses.
PYTHON_CMD="${WS_SKIM_PYTHON:-${MAP144_PYTHON:-python3}}"
if ! command -v "$PYTHON_CMD" >/dev/null 2>&1; then
    echo "ERROR: '$PYTHON_CMD' not found." >&2
    echo "  Install Python 3.10+ from your distro package manager." >&2
    echo "  Or set WS_SKIM_PYTHON to the full path of a python3 binary." >&2
    exit 1
fi

# Version check — bail early so we don't ship a confusing pip/wheel error.
"$PYTHON_CMD" -c '
import sys
if sys.version_info < (3, 10):
    sys.exit(f"Python {sys.version_info[:3]} found; WS Skim needs >= 3.10.")
print(f"Python {sys.version_info[0]}.{sys.version_info[1]}.{sys.version_info[2]} OK")
'

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
    mkdir -p "$GPU_LOG_DIR"
    GPU_LOG="$GPU_LOG_DIR/install-gpu.log"
    echo "Installing GPU extras (requirements-gpu.txt) ..."
    if "$VENV_PY" -m pip install -r requirements-gpu.txt >"$GPU_LOG" 2>&1; then
        echo "GPU extras installed."
    else
        echo "Note: GPU acceleration is not available on this computer, so WS Skim"
        echo "      will use the CPU instead. Everything else is installed."
        echo "      (Details: $GPU_LOG)"
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
