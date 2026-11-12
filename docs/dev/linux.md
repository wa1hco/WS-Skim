# WS Skim on Linux — developer notes

These notes are for running WS Skim from a git checkout. Most users
should follow the install steps in the top-level [README](../../README.md)
instead. This page covers command-line options and environment
variables that are deliberately left out of the user-facing docs.

Linux is the primary development and station platform (tested on
**Ubuntu 22.04 / 24.04**).

## Requirements

- Python **3.10+** (`python3` from your distro)
- [WSJT-X](https://wsjt.sourceforge.io/) (provides `jt9` / `msk144sim`)
- Git
- Optional: `python3-uhd` (apt) for USRP/B210. `scripts/install.sh`
  creates the venv with `--system-site-packages` so UHD is visible.
- Optional NVIDIA GPU: CuPy extras via `./scripts/install.sh --gpu`, or
  auto-detected.

## Install from a checkout

```bash
git clone https://github.com/wa1hco/WS-Skim.git ~/ws-skim
cd ~/ws-skim
./scripts/install.sh
```

What `scripts/install.sh` does (re-runnable, so it is also the update
step):

- Creates or reuses `.venv` (with system site-packages for UHD)
- `pip install -r requirements.txt`
- Installs GPU extras (`requirements-gpu.txt`) if `--gpu`,
  `WS_SKIM_GPU=1`, or an NVIDIA GPU is detected
- Verifies `jt9` discovery (warning only if missing)

Override the Python interpreter:
`WS_SKIM_PYTHON=/path/to/python3 ./scripts/install.sh`

To add the WS Skim menu entry and desktop icon for a checkout, run
`./scripts/install-desktop.sh` (`--remove` to take them away).

## Update

```bash
git pull
./scripts/install.sh
```

## Run

```bash
./scripts/run.sh
```

or directly:

```bash
.venv/bin/python ws_skim.py --bind-client-id <uuid> --log-level DEBUG
```

| Option | Default | Description |
|--------|---------|-------------|
| `--bind-client-id UUID` | — | FlexRadio `client bind` when required |
| `--bind-client UUID` | — | Deprecated alias |
| `--log-level LEVEL` | INFO | `DEBUG` … `CRITICAL` |

Sample rate and source type are chosen in the app (**Source** menu),
not on the command line. The first launch may take ~10 s while numba
JIT-compiles hot paths.

## Environment variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `WS_SKIM_PYTHON` | `install.sh` | Python interpreter to build the venv from |
| `WS_SKIM_GPU` | `install.sh` | `1` forces GPU extras, `0` skips them |
| `WS_SKIM_JT9` | app | Full path to `jt9` if it is not found automatically |

The older `MAP144_` spelling of each variable still works; when both
are set, `WS_SKIM_` wins. Other `WS_SKIM_*` variables in the code are
development and test switches (for example `WS_SKIM_USE_BLOCKS`,
`WS_SKIM_OUTPUT_DIR`).

## Test signals

```bash
.venv/bin/python tools/dev/generate_msk144.py --count 10
.venv/bin/python tools/dev/generate_msk144.py --count 10 --callsigns
```

Output goes under `MSK144/simulations/`. Load it with
**Source → WAV File**.

## jt9 not found

Install WSJT-X so `jt9` is on `PATH` (often `/usr/bin/jt9` or
`~/.local/bin/jt9`), or set `WS_SKIM_JT9=/full/path/to/jt9`.

## USRP / B210

Install the distro UHD bindings, for example on Debian/Ubuntu:

```bash
sudo apt install python3-uhd
./scripts/install.sh   # the venv must see system site-packages
```

See also [windows.md](windows.md) and [../router.md](../router.md).
