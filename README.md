# WS Skim

Wideband weak-signal skimmer for amateur radio. Today it is a working
MSK144 meteor-scatter decoder (formerly map144). It is being extended
with FT8/FT4 decoding and a WSJT-X-style UDP spot feed to WS Hub (formerly
WIMS). It is part of the wa1hco WS series.

## Install

Windows and Linux are supported. There is no macOS installer yet.

Install [WSJT-X](https://wsjt.sourceforge.io/) as well; WS Skim uses its
`jt9` decoder as a fallback. WS Skim needs Python 3.10 or newer: on
Windows the installer offers to download Python 3.12 for you if it is
missing; on Linux use your distribution's `python3`.

Then download the installer for your system from the
[Releases](https://github.com/wa1hco/WS-Skim/releases) page:

- **Windows:** download `WS-Skim-Setup.exe` and run it. Windows may warn
  that the publisher is unknown; choose **More info → Run anyway**.
- **Linux:** download `ws-skim-linux-install.sh` and run
  `bash ws-skim-linux-install.sh` in a terminal.

Both add a **WS Skim** icon to the desktop (and the Start menu or
application menu); start the app from there. On Ubuntu, if the desktop
icon shows a gear instead of the WS SKIM icon, right-click it and choose
**Allow Launching**. To update, run a newer installer the same way. No
administrator rights are needed.

Your settings and logs are kept in your user profile, not in the program
folder, so updating or reinstalling keeps them: `~/.config/ws-skim` and
`~/.local/state/ws-skim/logs` on Linux, `%APPDATA%\WS-Skim` and
`%LOCALAPPDATA%\WS-Skim\logs` on Windows. When reporting a problem,
attach the newest log from that folder.

The first WS Skim test prerelease is
[v0.1.4-test.1](https://github.com/wa1hco/WS-Skim/releases/tag/v0.1.4-test.1).
Prereleases are listed on the Releases page but are not "latest".

Linux is the best-tested path (Ubuntu 22.04 / 24.04); Windows works but has had
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
- A B210 audio/IQ router (`scripts/run-router.sh` / `scripts/windows/run-router.bat`) that
  feeds selected WSJT-X dials as virtual audio, with optional MAP65 /
  QMAP IQ export. MAP65 I/Q export is also available in the GUI
  (**View → MAP65 Export**); see [CHANGELOG.md](CHANGELOG.md)
  (Unreleased).
- Analysis and comparison tools, including `tools/dev/compare_decoders.py` and
  other scripts under `tools/` and `tools/dev/`.

The latest published tag is still the map144 prerelease
[v0.1.3-alpha](https://github.com/wa1hco/WS-Skim/releases/tag/v0.1.3-alpha)
(7 Sep 2026). `main` is ahead of that tag.

### In progress and planned

The plans live on `main`. The feature work itself is not done yet.

- FT8 and FT4 modes, and a receive-only WSJT-X-style UDP Status/Decode
  feed to WS Hub (formerly WIMS)
  ([plan](docs/plan/ft8-ft4-wims-udp.md))
- The first installer release. The installers and the release workflow
  are built and tested in CI; a version tag publishes them
  ([plan](docs/plan/installer-and-release.md),
  [releasing](docs/dev/releasing.md))

## Supported hardware

| Source | Interface | Notes |
| ------ | --------- | ----- |
| FlexRadio | DAX IQ via SmartSDR (TCP/UDP) | Linux and Windows. Documented for the 6000 series. |
| USRP B210 (Ettus) | UHD | Primarily Linux. |
| IQ WAV file | stereo float32, left=I, right=Q | Linux and Windows, for testing. |

There is no macOS support for the main application.

## Project layout

Install and launch files live under `scripts/` (Linux) and
`scripts/windows/` (Windows): `install.sh` / `install.ps1`,
`run.sh` / `run.bat` (the GUI, `ws_skim.py`), `run-router.sh` /
`run-router.bat` and `router_app.py` (B210 router), and `run-b210.bat`.

- `ws_skim/` — decoder, GUI, reporting, and router library
- `packaging/` — Windows (Inno Setup) and Linux installer builds
- `flexclient/` — FlexRadio client
- `tests/` — automated tests
- `docs/` — design notes, alpha notes, and router guides
- `vendor/` — vendored third-party code (including WDSP)
- `fortran_bp_test/` — WSJT-X-derived Fortran and C++ sources

Developer and analysis scripts live under `tools/dev/`
(`analyze_*.py`, `compare_decoders.py`, `generate_msk144.py`, and
others). B210/UHD install helpers stay under `tools/`.

FlexRadio setup is in [docs/flexradio-setup.md](docs/flexradio-setup.md).
Developer notes for running from a git checkout (command-line options,
environment variables, test signals) are in
[docs/dev/linux.md](docs/dev/linux.md) and
[docs/dev/windows.md](docs/dev/windows.md); building installers and
cutting a release is in [docs/dev/releasing.md](docs/dev/releasing.md). See also
[CHANGELOG.md](CHANGELOG.md) and [docs/ALPHA_NOTES.md](docs/ALPHA_NOTES.md).

## License

WS Skim is released under the GNU General Public License, version 3
(GPL-3.0). The license text is [LICENSE](LICENSE). Third-party
copyrights and provenance are in [NOTICE](NOTICE).

Copyright © 2026 Jeff Millar, WA1HCO \<wa1hco@gmail.com\>
