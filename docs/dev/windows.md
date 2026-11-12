# WS Skim on Windows — developer notes

These notes are for running WS Skim from a git checkout. Most users
should follow the install steps in the top-level [README](../../README.md)
instead. This page covers command-line options, environment variables
and installer details that are deliberately left out of the user-facing
docs.

Windows is supported but has had **less soak testing** than Linux.
FlexRadio and WAV playback are the best-exercised Windows paths.

## Requirements

- Python **3.10–3.14** from [python.org](https://www.python.org/downloads/)
  (not the Microsoft Store stub). Tick **Add python.exe to PATH**.
  - `install.ps1` tries 3.14 first, then 3.13 → 3.12 → 3.11 → 3.10.
  - `requirements.txt` pins **`numpy==1.26.4`** (UHD ABI). PyPI has no
    cp314 wheels for 1.26.4, so a 3.14 attempt usually fails and the
    installer falls back to 3.13/3.12 automatically.
- [WSJT-X](https://wsjt.sourceforge.io/) (provides `jt9.exe`)
- Git for Windows
- PowerShell 5.1+ (built into Windows 10/11)

## Install from a checkout

```powershell
git clone https://github.com/wa1hco/WS-Skim.git C:\ws-skim
cd C:\ws-skim
.\scripts\windows\install.ps1
```

If execution policy blocks the script, right-click it and choose
**Run with PowerShell**, or:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\windows\install.ps1
```

`install.ps1` and `run.bat` call `.venv\Scripts\python.exe` directly,
so there is no need to activate the venv or change execution policy
permanently.

What `install.ps1` does (re-runnable, so it is also the update step):

- Creates or reuses `.venv`
- Upgrades pip and installs `requirements.txt`
- Installs or verifies **Ettus UHD** (WinUSB driver and B210 FPGA/FX3
  firmware images under `C:\Program Files\UHD\share\uhd\images\`). The
  first run needs admin rights and internet (~233 MB). Pass `-SkipUhd`
  to skip.
- Verifies `jt9` discovery (warning only if missing)

B210 **Python** bindings (`import uhd`) are not installed into `.venv`
(there are no Windows pip wheels). For `run-router.bat` or a USRP
source, also use either the offline B210 kit `env\`
(`tools\install-b210.bat`) or `conda install -c conda-forge uhd`. The
firmware and driver from `install.ps1` are required either way.

## Update

```powershell
cd C:\ws-skim
git pull
.\scripts\windows\install.ps1
```

## Run

```powershell
.\scripts\windows\run.bat
```

or directly:

```powershell
.venv\Scripts\python.exe map144.py --bind-client-id <uuid> --log-level DEBUG
```

| Option | Default | Description |
|--------|---------|-------------|
| `--bind-client-id UUID` | — | FlexRadio `client bind` when required |
| `--bind-client UUID` | — | Deprecated alias |
| `--log-level LEVEL` | INFO | `DEBUG` … `CRITICAL` |

Also available: `scripts\windows\run-b210.bat` and
`scripts\windows\run-router.bat` (see [../router-windows.md](../router-windows.md)).
The first launch may take ~10 s while numba JIT-compiles hot paths.

## Environment variables

| Variable | Used by | Purpose |
|----------|---------|---------|
| `MAP144_JT9` | app | Full path to `jt9.exe` if it is not found automatically |

```powershell
$env:MAP144_JT9 = "C:\Path\To\jt9.exe"
.\scripts\windows\run.bat
```

## Test signals

```powershell
.venv\Scripts\python.exe tools\dev\generate_msk144.py --count 10
.venv\Scripts\python.exe tools\dev\generate_msk144.py --count 10 --callsigns
```

Output goes under `MSK144\simulations\`. Load it with
**Source → WAV File**.

## Common pitfalls

| Symptom | Fix |
|---------|-----|
| `python` opens the Microsoft Store | Install the python.org build; fix PATH so it wins over `WindowsApps` |
| pip "Building wheel for numpy" on 3.14 | Expected. Re-run `install.ps1` (it falls back to 3.13/3.12), or install Python 3.12 and delete `.venv` first |
| `install.ps1` "cannot be loaded" | `powershell -ExecutionPolicy Bypass -File .\scripts\windows\install.ps1` |
| Missing `jt9` | Install WSJT-X or set `MAP144_JT9` |
| First start looks hung | Wait for numba JIT (~10 s) |

See also [linux.md](linux.md).
