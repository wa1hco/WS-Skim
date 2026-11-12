# WS Skim on Linux

Primary development and contest station platform (tested on **Ubuntu 22.04 / 24.04**).

Product overview and radio sources: see the top-level [README.md](README.md).

## Install and run

Follow [INSTALL.md](INSTALL.md). Open the **scripts** folder and double-click **Install WS Skim**, then double-click **WS Skim**.

You will need [WSJT-X](https://wsjt.sourceforge.io/) for its jt9 decoder. If it is not installed yet, the installer warns you and still finishes.

## Reporting setup

Open **View → Reporting**:

- **My Station** — callsign and grid (required)
- **WSJT-X UDP** — GridTracker / N1MM / JTAlert (port 2237)
- **PSKReporter** — IPFIX to `report.pskreporter.info:4739`
- **DX Cluster** — telnet (default `dxc.ve7cc.net:7373`)

## Developer

Typed commands are for people changing WS Skim or installing from a shell. See [INSTALL.md](INSTALL.md#developer).

```bash
git clone https://github.com/wa1hco/WS-Skim.git
cd WS-Skim
sudo apt install -y python3
scripts/install-linux.sh
```

Optional USRP B210: `sudo apt install python3-uhd`, then run **Install WS Skim** again so the venv can see the system package. Optional GPU extras: `requirements-gpu.txt` (not part of the default install).

Test signals:

```bash
.venv/bin/python tools/dev/generate_msk144.py --count 10
```

Output under `MSK144/simulations/`. Load with **Source → WAV File**.

Windows: [README-Windows.md](README-Windows.md).
