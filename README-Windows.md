# WS Skim on Windows

Windows is supported but has had **less soak testing** than Linux.

Product overview and radio sources: see the top-level [README.md](README.md).

## Install and run

Follow [INSTALL.md](INSTALL.md). Extract the folder to `C:\WS-Skim`, open `scripts\windows`, and double-click **Install-WsSkim.cmd**. Click **Yes** if Windows asks for permission. Then double-click **WS Skim**.

You will need [WSJT-X](https://wsjt.sourceforge.io/) for its jt9 decoder. If it is not installed yet, the installer warns you and still finishes. Prefer the official Python installer from python.org, not the Microsoft Store stub. The WS Skim installer will find or install official Python 3.12 for you. Python 3.10, 3.11, or 3.12 works.

FlexRadio and WAV playback are the best-exercised Windows paths. USRP / Airspy / RTL-SDR are primarily Linux-oriented. UHD / B210 is a separate optional step; see [INSTALL.md](INSTALL.md#usrp-b210-optional).

## Reporting setup

Open **View → Reporting**:

- **My Station** — callsign and grid (required)
- **WSJT-X UDP** — GridTracker / N1MM / JTAlert (port 2237)
- **PSKReporter** — IPFIX to `report.pskreporter.info:4739`
- **DX Cluster** — telnet (default `dxc.ve7cc.net:7373`)

## Developer

Typed commands are for people changing WS Skim or installing from a shell. See [INSTALL.md](INSTALL.md#developer).

The default install does not include Ettus UHD. Use `tools\Install-UhdWindows.ps1` and `tools\install-b210.bat` only if you need a B210.

Test signals:

```powershell
.venv\Scripts\python.exe tools\dev\generate_msk144.py --count 10
```

Output under `MSK144\simulations\`. Load with **Source → WAV File**.

The first start may pause for about ten seconds while the program prepares itself.

Linux: [README-Linux.md](README-Linux.md).
