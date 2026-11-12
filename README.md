# ws-skimmer

Wideband weak-signal skimmer for amateur radio. Today it is a working
MSK144 meteor-scatter decoder (formerly map144). It is being extended
with FT8/FT4 decoding and a WSJT-X-style UDP spot feed to WIMS, the
planned "WS HUB" console. It is part of the wa1hco WS series.

## Install

Windows and Linux are supported. There is no macOS installer yet.

There is no ws-skimmer release yet. The
[Releases](https://github.com/wa1hco/ws-skimmer/releases) page currently
has only the older map144 prerelease. A ws-skimmer test prerelease is
planned. When it appears, download the Windows or Linux installer from
that page and run the file (on Windows, double-click `install.ps1` or
choose Run with PowerShell; on Linux, run `install.sh`). Start the app
with the launcher the install provides. The test prerelease is intended
to place a desktop icon, plus a Start-menu entry on Windows.

Until then, if you already have a copy of the repository, run
`install.ps1` on Windows or `install.sh` on Linux, then start the app
with `run.bat` or `run.sh`.

You will need [WSJT-X](https://wsjt.sourceforge.io/) (it provides the
`jt9` decoder) and Python 3.10 or newer. On Windows, use the installer
from [python.org](https://www.python.org/downloads/). Linux is the
best-tested path (Ubuntu 22.04 / 24.04); Windows works but has had
less soak testing. Radio source and sample rate are chosen in the app
under the **Source** menu. Reporting is set up under **View → Reporting**.

## Status

### What works today

- Real-time MSK144 detection and decoding around the calling frequency
  (±20 kHz) on 48 kHz IQ, with a 48-channel polyphase channelizer,
  paired-tone detection, a coincidence gate, an optional noise
  blanker, and a GUI (live spectrogram, detection heatmap, decode
  panel). A 15-second IQ window is passed to the decoder when a
  channel exceeds the threshold. Saved IQ files can be replayed from
  the **Source** menu.
- Reporting of decodes to PSKReporter, GridTracker, N1MM Logger+,
  JTAlert, and DX cluster nodes via the WSJT-X UDP protocol (plus
  PSKReporter IPFIX and DX cluster telnet).
- FlexRadio DAX IQ and USRP B210 radio sources.
- A B210 audio/IQ router (`run-router.sh` / `run-router.bat`) that
  feeds selected WSJT-X dials as virtual audio, with optional MAP65 /
  QMAP IQ export. MAP65 I/Q export is also available in the GUI
  (**View → MAP65 Export**); see [CHANGELOG.md](CHANGELOG.md)
  (Unreleased).
- Analysis and comparison tools, including `compare_decoders.py` and
  the scripts under `tools/`.

The latest published tag is still the map144 prerelease
[v0.1.3-alpha](https://github.com/wa1hco/ws-skimmer/releases/tag/v0.1.3-alpha)
(7 Sep 2026). `main` is ahead of that tag.

### In progress and planned

The plans live on `main`. The feature work itself is not done yet.

- FT8 and FT4 modes, and a receive-only WSJT-X-style UDP Status/Decode
  feed to WS Hub (formerly WIMS)
  ([plan](docs/plan/ft8-ft4-wims-udp.md))
- An installer with a desktop-icon launcher; only a version tag will
  update what users install
  ([plan](docs/plan/installer-and-release.md))
- Moving launchers into `scripts/` and developer tools into `tools/dev/`

## Supported hardware

| Source | Interface | Notes |
| ------ | --------- | ----- |
| FlexRadio | DAX IQ via SmartSDR (TCP/UDP) | Linux and Windows. Documented for the 6000 series. |
| USRP B210 (Ettus) | UHD | Primarily Linux. |
| IQ WAV file | stereo float32, left=I, right=Q | Linux and Windows, for testing. |

There is no macOS support for the main application.

## Project layout

Install and launch files currently sit at the repository root (they
are planned to move into `scripts/`): `install.sh` / `install.ps1`,
`run.sh` / `run.bat` (the GUI, `map144.py`), `run-router.sh` /
`run-router.bat` and `router_app.py` (B210 router), and `run-b210.bat`.

- `map144_app/` — decoder, GUI, reporting, and router library
- `flexclient/` — FlexRadio client
- `tests/` — automated tests
- `docs/` — design notes, alpha notes, and router guides
- `vendor/` — vendored third-party code (including WDSP)
- `fortran_bp_test/` — WSJT-X-derived Fortran and C++ sources

Developer and analysis scripts live at the repository root
(`analyze_*.py`, `compare_decoders.py`, `generate_msk144.py`, and
others) and under `tools/` (including B210/UHD install helpers). The
plan is to gather developer scripts under `tools/dev/`.

Older platform notes are in [README-Linux.md](README-Linux.md),
[README-Windows.md](README-Windows.md), and
[README_GUI.md](README_GUI.md). They still use the map144 name and
describe command-line flags and environment variables; those
instructions are being removed from user-facing docs. See also
[CHANGELOG.md](CHANGELOG.md) and
[docs/ALPHA_NOTES.md](docs/ALPHA_NOTES.md).

## License

WS Skim is released under the GNU General Public License, version 3
(GPL-3.0). The license text is [LICENSE](LICENSE). Third-party
copyrights and provenance are in [NOTICE](NOTICE).

Copyright © 2026 Jeff Millar, WA1HCO \<wa1hco@gmail.com\>
