# Changelog

All notable changes to WS Skim (formerly MAP144) are recorded here.  Format roughly follows
[Keep a Changelog](https://keepachangelog.com/) — newest first, ham-readable.

Versions are bumped at noticeable-to-tester intervals, not per commit.
Each released version has a matching git tag (e.g. ``v0.1.1-alpha``).

## Unreleased

### Project

- **Renamed to WS Skim** (repository `wa1hco/WS-Skim`, formerly `map144`).
  The application module and settings keep their `map144` names for now.
- **License.**  Added the GPLv3 `LICENSE` and a `NOTICE` with WSJT-X and
  other third-party provenance; WSJT-X-derived files carry attribution
  headers.  Version set to 0.1.4 in `pyproject.toml` and the app.
- **CI.**  GitHub Actions runs the test suite on Python 3.10 and 3.12 for
  every pull request and push to `main`.
- **Layout.**  Install and launch scripts moved to `scripts/` (Linux) and
  `scripts/windows/` (Windows); developer scripts moved to `tools/dev/`.
  Added the WS Skim icon set.
- **Docs.**  The old platform READMEs became developer notes in
  `docs/dev/`; FlexRadio setup moved to `docs/flexradio-setup.md`.  The
  obsolete `README_GUI.md` (for the removed `flex_gui.py`) was dropped.

### Added

- **Installers.**  `WS-Skim-Setup.exe` for Windows (per-user, no admin;
  desktop and Start-menu shortcuts; optional B210 UHD driver) and a
  single-file `ws-skim-linux-install.sh` for Linux (application-menu entry
  and desktop icon).  A version tag builds both, installs them on clean
  CI runners and publishes a GitHub release; see `docs/dev/releasing.md`.
- **Version with git hash.**  The startup log line and Help → About show
  e.g. `0.1.4 (51b9fd9)`; installed copies read it from a `BUILD` file.
- **Missing WSJT-X is not fatal.**  If `jt9` is not found at startup, a
  dialog explains how to install WSJT-X and the app keeps running (SPD is
  the primary decoder; jt9 is the fallback).  Before, it exited with a
  terminal-only message, invisible when started from an icon.
- **Settings and logs in the user profile** (from PR #6): `~/.config/ws-skim`
  and `~/.local/state/ws-skim/logs` on Linux, `%APPDATA%\WS-Skim` and
  `%LOCALAPPDATA%\WS-Skim\logs` on Windows.  Old settings and in-tree logs
  are copied over once on first start; nothing is deleted.
- **Windows installer installs Python if missing** (from PR #6): Python 3.12
  for the current user via winget, else the python.org 3.12.10 installer.
  Shortcuts start WS Skim with `pythonw.exe`, without a console window.

- **MAP65 I/Q export (View → "MAP65 Export").**  Tees a 96 kHz Linrad **timf2**
  I/Q stream off the USRP B210 to MAP65 over UDP (default `:50002`), so one
  radio feeds MAP65 (EME) while MSK144 detection keeps running unchanged.

  - **Frequency plan.**  The B210 hardware LO (and its DC artifact) is parked at
    `pan_center` via a new `USRPSource(pan_center_mhz=…)`; the MSK144 path's NCO
    then brings its centre to baseband, so the DC artifact is pushed out of both
    sub-bands.  The export taps the *raw* 192 kHz dual-pol IQ inside
    `_recv_loop` (the only place the full band exists) and does its own
    NCO + ÷2 decimate (192→96 kHz, exact — no rational resampling).  Recommended
    setup: single-pol RF0, B210 DC = MAP65 centre = 144.100 MHz, so MAP65's
    centre DC-blank coincides with the (off-band) artifact and the 144.110–
    144.140 EME activity is clear.

  - **Wire format (Linrad timf2).**  24-byte little-endian header
    (`passband_center`, `time`, `userx_freq`, `ptr`, `block_no`, `userx_no`,
    `passband_direction`) + interleaved int16 samples.  Two hard-won
    requirements: payload **must** be exactly `NET_MULTICAST_PAYLOAD` = 1392
    bytes/packet (else MAP65 mis-aligns its ring buffer → dense spurious lines),
    and `userx_no` = **+channel-count** (sign = int16/float, magnitude = channels;
    `0` → MAP65 shows no signal).  Single-pol = +1, dual H+V = +2.

  - **Safety/UX.**  Output is clamped to ±32766 — saturated full-scale int16
    (±32767) crashes MAP65's `INTEGER*2` `*_sync.f90` ("32767 out of range").
    Window settings (enable / IP / port / MAP65 centre / B210 DC centre / int16
    level) and geometry persist across restart; **Enter** applies in any field;
    export **auto-restores** on restart (re-attaches to the B210 at source
    creation).  QSettings keys: `map65_*`.

  - New modules `map144_app/map65_export.py`, `map144_app/map65_window.py`;
    tests in `tests/test_map65_export.py` (13 tests: timf2 header, ÷2 decimator
    + alias rejection, int16 clamp, NCO tone placement, single/dual interleave,
    fixed-payload guard).

- **Diagnostic tools.**  `tools/wsjtx_inject.py` (send a synthetic WSJT-X UDP
  decode to test the GridTracker→N1MM chain without a meteor ping) and
  `tools/udp_listen.ps1` (UDP arrival probe for the MAP65 export).

### Changed

- Window title, About box and install-script messages say **WS Skim**.
- Engine: block work is not started while background workers are stopping
  (from PR #6).
- `install.sh` checks for Python's `venv` module up front and tells
  Ubuntu/Debian users to install `python3-venv`.
- `USRPSource` gains `pan_center_mhz` (decouples the hardware LO / DC position
  from the MSK144 processing centre) and an optional `map65_exporter` tap; the
  NCO table build is refactored into `_build_nco_table()` so `retune()` keeps
  the LO fixed on `pan_center` in pan mode.

### Fixed

- **Selecting the "NR0V-Wideband" noise blanker crashed the radio loop on
  platforms without the WDSP `libnob` native library (e.g. Windows).**  The
  backend loaded `libnob.so` lazily on the first IQ chunk and raised, taking
  down the source thread (and the choice persisted in `nb_backend`, so it
  re-crashed every restart).  `noise_blanker.make()` now checks
  `Blanker.is_available()` and falls back to Linrad with a warning instead of
  crashing; the loader path is platform-aware (`libnob.dll` / `.dylib` / `.so`)
  so a native build is used where present.  Regression test in
  `tests/test_nb_fallback.py`.  `vendor/wdsp` now cross-builds: `comm.h` has a
  `_WIN32` branch using native Win32 APIs (`nob.c` is upstream Windows code),
  and the Makefile/README cover building `libnob.dll` (MinGW or MSVC) so the
  NR0V backend can be enabled on Windows.

## v0.1.3-alpha — 2026-05-19

### Fixed

- **Test pollution into production decode log.**  Two test suites
  (``test_engine_block_primary`` and ``test_parity_on_strong_multi_ping_sim``)
  were writing synthetic-test decodes — including phantom-format LDPC
  garbage from the parity_strong10 fixture and ``CQ K1JT FN20`` entries —
  to the operator's real ``MSK144/detections/decodes.jsonl`` and capture
  WAV directory.  An overnight bake on 2026-05-19 was confused by these
  leftover test entries, masking the real bake's true (near-zero) decode
  count.

  Root cause: ``map144_app/processing.py`` (legacy hop loop) and
  ``map144_app/engine.py`` (block-primary runtime) hardcoded the output
  directory to ``<repo>/MSK144/detections/``.  Any test instantiating the
  Engine or invoking the legacy pipeline wrote there.

  Fix: new ``MAP144_OUTPUT_DIR`` env var override.  Default unchanged —
  if unset, production writes go to ``MSK144/detections/``.  Tests set
  the env var to a TempDir in setUp / restore in tearDown.

### Added

- **``MAP144_OUTPUT_DIR`` env var.**  Operators with non-standard layouts
  (e.g., wanting decodes on a different drive) can now redirect WAV /
  ``decodes.jsonl`` / ``launches.jsonl`` writes by setting this variable
  before launching MAP144.

## v0.1.2-alpha — 2026-05-19

### Fixed

- **DAX-IQ stream silently stolen by other Flex clients.**  When WSJT-X
  or a second MAP144 instance created its own DAX-IQ stream on the same
  Flex radio, this MAP144's filter-watch code rewrote its filter to
  follow the foreign stream — and silently stopped receiving IQ (the
  GUI showed "initializing..." while the Flex stream-flow indicators
  for our client went to zero).  On shutdown the cleanup ``stream
  remove`` then targeted the foreign id and was rejected by the radio,
  leaving our own stream orphaned on the radio.

  Fix: ``flexclient/setup.py`` now remembers the stream-id it created
  (``self._own_stream_id``).  Stream-status broadcasts for a different
  stream-id are checked against our slice-id: same slice = radio-
  initiated reassignment (rare; update filter); different slice = a
  foreign client's stream (do NOT update filter; our state untouched).

  Operationally: MAP144 and WSJT-X (and a second MAP144) can now run
  side by side against the same Flex radio, each on its own slice.

## v0.1.1-alpha — 2026-05-19

### Fixed

- **DF column in decode list always showed 0 Hz.**  The block pipeline
  detector wasn't including the within-channel sub-bin frequency offset
  (``fc_offset``) when reporting ``fc_hz`` to the decoder.  As a side
  effect, decoded WAVs were being pre-mixed to channel-centre exactly,
  forcing jt9 to absorb any real signal offset via its FTOL search at
  the cost of decode probability.  Now matches the legacy detector
  formula — sub-Hz pre-correction via the sync-correlator's fine
  frequency-offset estimate is also wired in.

### Added

- **Version is now displayed**:
  - Main window title bar: ``map144 v0.1.1-alpha — 50.260 MHz``
  - Startup log line in ``MSK144/logs/map144_*.log``
  - Install-script "ready" banner

- **CHANGELOG.md** (this file) — versioned record of tester-visible changes.

## v0.1.0-alpha — 2026-05-18

Initial alpha release.  Distributed to the operator's private VHF group.

### Features

- Real-time MSK144 detect + decode pipeline (legacy DSP path is the default).
- 48-channel polyphase channelizer covering ±20 kHz around the MSK144 calling frequency.
- Two-detector design (squared-FFT tone pair + coherent sync-word correlator).
- Decodes reported to PSKReporter, WSJT-X UDP, GridTracker, DXcluster.
- Dual-polarisation support on dual-channel digital receivers (~3 dB advantage on 50 MHz Faraday-rotated pings).
- ``compare_decoders.py`` side-by-side comparison vs WSJT-X.
- Captures browser with substring filter + scrollable list.
- Cross-platform install scripts (``install.sh`` / ``install.ps1``); cross-platform launchers (``run.sh`` / ``run.bat``).
- Block-pipeline architecture available behind ``MAP144_USE_BLOCKS=1`` (opt-in only; not recommended for general use yet).

### Tested with

FlexRadio 6000 series (DAX-IQ), USRP B210, Airspy HF+, NESDR Smart.
