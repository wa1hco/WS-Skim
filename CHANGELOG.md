# Changelog

All notable changes to WS Skim (formerly MAP144) are recorded here.  Format roughly follows
[Keep a Changelog](https://keepachangelog.com/) — newest first, ham-readable.

Versions are bumped at noticeable-to-tester intervals, not per commit.
Each released version has a matching git tag (e.g. ``v0.1.1-alpha``).

## Unreleased

### Added

- **Source → SDRplay (SDRconnect): SDRplay radios such as the RSPduo.**
  WS Skim gets the raw IQ from SDRplay's SDRconnect program over its
  WebSocket server.  If SDRconnect is not running, WS Skim starts it in the
  background (or with its window, or never — your choice), opens the
  radio and starts streaming; an SDRconnect you already run is just
  connected to.  WS Skim tunes the chosen RSPduo tuner (1 or 2) to the
  band you pick when the band is outside what the tuner is receiving, and
  mixes and decimates the 2 MHz (or other) stream to WS Skim's 48 kHz in
  about a tenth of one CPU core.  The new View → SDRplay (SDRconnect)
  window holds the settings (host, port, start mode, RSPduo mode, tuner,
  LNA state, antenna, whether WS Skim tunes) and shows the connection
  state, tuner frequency, rate and data gaps; when SDRconnect cannot be
  reached the status says what to do.  Reconnects by itself if
  SDRconnect restarts.  Both RSPduo tuners feeding two bands at once is
  the next step.  Setup is in docs/sdrplay-setup.md.

- **View → Roster is a WS Hub-style browser window.**  The Qt decode table
  is replaced by a local web page that opens in a Chrome or Edge app
  window (Firefox or your usual browser if those are not installed), with
  no URL or port to remember.  Ctrl +/- zoom works, the look is light
  mode, and there is one row per station (call plus grid).  Filters match
  WS Hub: Show (All, Needed, Needed and CQ, Needed and CQ or 73), max age,
  band checkboxes, plus FT8 / FT4 / MSK144 mode checkboxes, and a columns
  menu.  Badges explain why a station is needed (grid, state, DXCC, CQ
  zone, ITU zone, continent, call, or unconfirmed) or "us" when they are
  calling you; hover for the reason in words.  On All, stations you do
  not need are dimmed.  Worked-before data comes from a discovered
  WSJT-X ``wsjtx_log.adi`` (including wrig instance folders) or a log you
  choose on the page; LoTW confirmations are read from GridTracker's file
  when it is present.  The default checklist is new DXCC and new DXCC on
  band.  Decode rates on the status page still come from an in-memory
  list.  Click-to-work is not in this step.

- **Main window shows program status only; decodes move to View → Roster.**
  The main window is now a status page in the style of WS Hub's: receiver,
  band and modes, centre frequency, IQ data rate (against the rate
  expected), audio to WSJT-X, decodes per minute for each mode, filtered
  count, WSJT-X UDP, PSKReporter, DX cluster, last error, UTC and the
  version.  Each row has a dot (green working, amber check, red not
  working, grey off) and a tooltip.  The decode list that filled the main
  window moved to **View → Roster**.  The Roster opens on the first start
  and remembers whether it is open; the main window keeps its old position
  but starts at the new, smaller size.  The roster itself is now the
  Hub-style browser page described above (filters and badges included).

- **Band Activity replaces the FT8 Activity window.**  **View → Band
  Activity** shows the whole band for every mode: a waterfall of the
  radio's full span (frequency across the top, UTC time scrolling down,
  newest at the top) made from the real signal, so activity nothing
  decodes shows too.  Each FT8, FT4 and MSK144 decode is marked at its
  frequency and period in its mode's colour (legend at the top), and each
  mode's WSJT-X sub-band is lightly shaded.  Callsigns are no longer
  printed on the plot; hover over a mark for call, grid, SNR, mode, UTC
  time and frequency.  Two check boxes, **Waterfall** and **Detections**,
  show either layer or both, and are remembered, as is **History** (now in
  minutes; the old period count carries over, as does the window's place
  on screen).  Filtered decodes are never shown.  The waterfall costs no
  CPU while the window is hidden or Waterfall is off.  The FT8/FT4
  **Show** box moved to **Diagnostics → FT8 Sync Map**.

- **FT8 and FT4 at the same time.**  The **Mode** menu now has FT8 and FT4
  as separate check marks, and both can be on; MSK144 is still on its own
  (checking it turns FT8 and FT4 off).  Each mode keeps its own period
  clock (15 s / 7.5 s), its own jt9 run and its own WS Hub regions, so on
  20 m both 14.074 (`SKIM-FLEX-20m-FT8-74`) and 14.080
  (`SKIM-FLEX-20m-FT4-80`) are heard from the one receiver.  FT4 regions
  use the band's FT4 dial; an idle FT4 region closes after 1 minute.  The
  choice is remembered; an install that was on FT8 or FT4 comes up with
  both checked.  The status bar shows decodes per minute for each mode,
  and the Activity window had a **Show** box to pick which mode it (and the
  Sync Map) draws (now in the Sync Map; see Band Activity above).

- **False FT8/FT4 decodes are filtered out.**  jt9 still decodes at Deep
  (depth 3), but each period's decodes are checked before they are shown in
  the Activity window or sent to WS Hub, and the ones that cannot be real
  are dropped:
  - *ap* - a priori decodes that assume a QSO partner (jt9 now runs with no
    MyCall/DxCall, so it no longer forces noise into messages to its
    default K1ABC / W9XYZ; anything naming those calls is dropped too).
    CQ a priori decodes are kept.
  - *call* - a callsign not shaped like one, or a sender whose prefix is not
    allocated (checked against a bundled `cty.dat` country file by AD1C,
    see NOTICE; works offline).
  - *grid* - a grid far from the sender's country (larger allowance for big
    countries; /MM, Antarctica and US calls in KH6/KL7 etc. are handled).
  - *dup* - the same message twice in one period from overlapping jt9
    sections; the best SNR is kept.

  On 1 day of 40 m / 20 m decodes this drops 0.1% (126 of 126,666), all
  junk on inspection.  The status bar shows how many were filtered (hover
  for the breakdown), the log has one `filtered N: ap a, call c, grid g,
  dup d` line per period that had any, and the dropped decodes go to
  `rejected.jsonl` next to `decodes.jsonl` for review.

### Fixed

- **FT8 Activity time axis no longer zooms.**  A mouse-wheel zoom over the
  plot used to stretch time too, so the ticks bunched up on the left and the
  newest periods fell off screen.  Time is now locked to the History setting
  with the newest period at the right edge; the wheel and drag zoom
  frequency only.

- **FT8 periods around your own transmissions are timed right again.**
  While a Flex radio transmits, WS Skim throws away its IQ.  The sample
  clock used to stop with it, so after each transmission the IQ was glued
  onto the period before: the period before a transmission was searched
  ~14 s late, and the transmit period was "decoded" from the previous
  period's audio, 1 s early (DT about −1.0 s) — repeat spots of stations
  already reported, with the wrong time.  In FT8/FT4 the clock now keeps
  running over the discarded IQ (and over any IQ that never arrives, by
  arrival time), the period before a transmission is searched on time,
  and a period that is mostly transmit is skipped.

### Changed

- **MSK144 is offered on 6 m, 2 m and 1.25 m only.**  The Mode menu greys
  out MSK144 (with a tooltip saying why) on other bands, and 70 cm
  (432.360) is gone from the MSK144 band list.  Choosing MSK144 while on
  6 m or 2 m FT8 stays on that band (50.260 / 144.150).  An install saved
  in MSK144 on a band where it is no longer offered starts in FT8 + FT4.
  10 m (28.145) is not offered: it is not in WSJT-X's frequency list.

- **View → Waterfall is gone.**  Band Activity's waterfall replaces it.

- **`decodes.jsonl` no longer grows without limit.**  At 64 MB it is renamed
  `decodes.jsonl.1` (replacing the older one) and a new file starts;
  `rejected.jsonl` does the same at 4 MB.

- **FT8/FT4 regions are named by their kHz in the band, and WS Hub only
  hears regions that are decoding.**  Each jt9 region now starts on a whole
  kHz (7059, 7074, …) instead of a floating 7059.8 or 7062.6, so the dial
  WS Hub gets matches what operators announce.  Regions are labelled by
  that kHz within the band, "74" for 7074 kHz (also 14074; "313" for
  50313), in the FT8 Activity view and the Sync Map, instead of 1, 2, 3.
  **WS Hub sees new instance names:** every instance now ends in its kHz,
  the standard dial included, e.g. `SKIM-FLEX-40m-FT8-74` (was
  `SKIM-FLEX-40m-FT8`) and `SKIM-FLEX-40m-FT8-59` (was
  `SKIM-FLEX-40m-FT8-7060`).  WS Skim no longer announces every standard
  dial in the passband: a region shows up in WS Hub with its first decode
  and is closed after 8 periods with nothing decoded (2 minutes of FT8,
  1 minute of FT4, 2 minutes for MSK144), as well as on a band change.

- **FT8 jt9 sections follow where FT8 is actually being decoded.**  On a
  busy band the Costas search also scores RTTY, SSB and other digital
  traffic, which opened 10–14 jt9 sections on 40 m (7056–7099 kHz) and
  forced jt9 down to depth 2.  Candidates more than 500 Hz from any decode
  in the last 8 periods now need a very strong sync to open a section, a
  section holding a single weak candidate away from activity is dropped,
  and every 4 minutes two periods (both even and odd) search the whole
  band so new activity is still found; start-up and band changes search
  everything until two periods have decoded.  On 40 periods of real 40 m
  IQ replayed through jt9: 11.7 → 4.9 sections per period (3.4 between
  full scans), jt9 depth 3 on 33 of 40 periods instead of none, and 2129
  decodes instead of 1982.  The Sync Map status line counts the set-aside
  candidates.  `tools/dev/ft8_section_replay.py` reruns that comparison
  on saved periods.

- **New FT8/FT4 Activity window; the Sync Map moves to Diagnostics.**
  View → FT8 Activity is now the window that opens in FT8 and FT4.  It is
  a sideways waterfall of who was heard: frequency runs up the side with
  the numbered ~3 kHz jt9 sections shaded in alternate bands, and UTC time
  runs along the bottom, one column per period, scrolling left as each
  period ends.  It shows 8 periods (2 minutes of FT8) by default; set
  *History* in the window to 2–40 periods, and it is remembered.  Each
  decoded station is a short tick in its period at its frequency,
  coloured by SNR (light blue = weak, red = strong, with an SNR colour
  bar).  Callsigns are not printed by default: hover over a tick for the
  call, SNR, DT, frequency and message, or tick *Show calls* to print them
  all.  *Show sync candidates* adds every sync hit as a faint grey mark.
  Your frequency zoom is kept from period to period.  The window is redrawn
  only when a period ends or its decodes arrive, and not at all while it
  is closed.  The FT8 Sync Map is unchanged and is now under
  Diagnostics → FT8 Sync Map, for checking the sync detector.

- **The Sync Map vertical axis is now DT, and mid-period noise is no longer marked as a candidate.**
  0 s is on time (the same DT as in the Decodes tab).  A faint line marks
  DT = 0, and a light band shows the preferred window (FT8: −0.5 to +1.0 s;
  FT4: −0.3 to +0.6 s).  Real signals almost all start in that window, so
  a peak outside it is kept only when it is clearly stronger than anything
  on time — a late station with a solid sync still shows up, but the
  scatter of mid-window noise bars is gone.

- **FT8 Sync Map marks sections, candidates, and decodes more clearly.**
  The ~3 kHz jt9 sections are outlined bands numbered across the top of the
  map.  Each sync candidate is a short horizontal bar as wide as the signal
  (~50 Hz in FT8), coloured by sync strength (white = weak, red = strong,
  with a Sync colour bar).  After decoding, each decoded station gets a
  ``CALL SNR`` label next to its bar; undecoded candidates stay unlabeled.
  Labels are a second, labels-only update and are cleared at the next period.

### Added

- **View → Waterfall**, an optional classic scrolling spectrogram of the
  incoming IQ (newest at the top, frequency in MHz).  Off by default; the
  window's size, position and visibility are remembered.  It is a separate
  window from the Sync Map and works in MSK144, FT8, FT4, and simulation
  (WAV) mode.  It reuses the existing overlap-FFT and does no extra work
  while hidden.

### Docs

- **Bidirectional decode stream plan.**  A design note at
  ``docs/plan/decode-stream.md`` describes a web protocol (live
  WebSocket plus an HTTPS history API) for publishing and subscribing
  to real-time decodes, with timing and a comparison to DX cluster,
  RBN, and PSK Reporter.  The first public relay is a home Proxmox
  LXC published as ``live.wa1hco.net``.  Remote spots reappear to
  WS Hub as local WSJT-X UDP instances.

### Fixed

- **FT8 Sync Map no longer jumps: one map update per period, before decoding, with fixed axes.**
  A later decode for the same period updates only the Decodes table and the
  status line.  The Y axis is the mode's fixed DT window, and a manual zoom
  is kept until the frequency, mode, or extent changes.
- **Graph axis labels and tick numbers are larger** (12 pt labels, 11 pt ticks)
  on the Sync Map and the other WS Skim plots, with more room so the text is
  not clipped.
- **Desktop and taskbar icon.** The running window now uses the WS Skim
  icon in the GNOME dock, Alt-Tab, and the Windows taskbar, instead of a
  generic or Python icon.  Existing Linux installs pick this up after
  re-running Install (so the launcher gets ``StartupWMClass=ws-skim``).

### WSJT-X UDP / WS Hub

- **Decodes are exported the way a real WSJT-X instance would.**  Each
  (radio, band, mode, dial) gets its own id, for example
  ``SKIM-FLEX-2m-FT8`` or ``SKIM-B210-6m-FT8-50323``, with its own
  Heartbeat and Status.  The Decode mode field is the WSJT-X character
  (``~`` FT8, ``+`` FT4, ``&`` MSK144), and the audio offset is
  ``signal − dial`` so WS Hub can add them to get RF.  Close is sent
  when the program stops.  The default destination is the WS Hub
  multicast group ``224.0.0.73:2237`` (the Host field still accepts
  ``127.0.0.1``).  Instance ids are shown read-only in Reporting.

### FT8 decoding fixes

- **Linux: WS Skim no longer runs on Python 3.14.**  With the numpy version
  WS Skim uses, Python 3.14 silently overwrote audio before jt9 read it, so
  FT8 gave 0-2 decodes where the same radio data decodes ~50 stations.  The
  installer now uses Python 3.10-3.13 (3.12 preferred).  If the computer only
  has 3.14 it sets up a private Python 3.12 for WS Skim (no admin rights, no
  system change) and rebuilds the WS Skim environment on the next update.
  On such computers the USRP B210 needs UHD for that Python.
- **The FT8 sub-band section follows the radio.**  If the band pulldown
  is on another band (e.g. still 6 m MSK144) the always-decoded section
  now uses the standard FT8/FT4 dial inside the tuned band (7.074 on 40 m).
- **Static bursts no longer flood the Sync Map with candidates.**  Each
  time slice of the search spectrogram is now scaled to its own noise
  level, so a burst of static weighs like any other moment.  On synthetic
  data one burst went from ~170 false candidates per period to none, and
  weak signals are found as before (-22 dB), and better during static.
- **Period timing follows the radio, not the CPU.**  The FT8/FT4 period
  start was stamped with the time the computer *processed* the samples.
  When processing fell behind the radio (the Flex queue holds up to ~10 s),
  every period was cut late by that backlog and jt9 saw the signals at the
  wrong time.  Flex packets are now stamped when they arrive and the period
  start uses that time.
- **The FT8 sub-band is always decoded.**  Besides the sections chosen from
  the Costas candidates, jt9 now always runs on the section that starts at
  the band's dial frequency (e.g. 7.074 MHz), so a busy sub-band is decoded
  even if stronger candidates elsewhere crowd it out.
- **Diagnostics.**  The log now shows, per period, the processing backlog,
  Flex queue depth, dropped/missed packets, where each jt9 section sits and
  the median DT of the decodes.  Diagnostics → Save FT8 period IQ keeps the
  last 10 minutes of periods in the output folder (`ft8_iq/`) for analysis.
- View → FT8 Sync Map is now the first entry in the View menu.

### Project

- **Renamed to WS Skim** (repository `wa1hco/WS-Skim`, formerly `map144`).
  The package is now `ws_skim` and the entry point `ws_skim.py`; log files
  are `ws-skim_*.log`; panel titles say WS Skim.  Environment variables
  are `WS_SKIM_*`, and the old `MAP144_*` names still work.  The WSJT-X
  UDP id, PSKReporter software name, audio sink names and router settings
  keep their `map144` names so station setups keep working.  The Windows
  installer removes the old `map144_app` folder and `map144.py` on update.
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

- **The Band pulldown follows the Mode menu.**  In FT8 or FT4 mode it lists
  the WSJT-X FT8/FT4 dial frequencies from 160 m to 70 cm (only the bands the
  current radio can reach are selectable), and the choice is remembered per
  mode.  The decode list's offset column is measured from that dial.  With
  the Flex the radio's pan still sets the frequency.

- **FT8 / FT4 decoding.**  After each period's Costas search, jt9 decodes
  every ~2.8 kHz section of the band (audio 200-3200 Hz around each).  The
  decodes appear in the main decode list and the FT8 Sync Map's new
  Decodes tab, are written to `decodes.jsonl` (with the mode), and are
  sent to WSJT-X UDP, PSKReporter and the DX cluster with mode FT8/FT4 and
  their own frequency.  jt9 runs at depth 3, or 2 when more than 8 sections
  are busy so the period still finishes in time.

- **Mode menu: MSK144 / FT8 / FT4.**  The choice is remembered.  In FT8 or
  FT4 mode the MSK144 detectors, sync search and decode do not run, the
  MSK144 panels are hidden, and the noise blanker defaults to Bypass (the
  blanker choice is saved separately for each mode).
- **FT8 Sync Map window** (View → FT8 Sync Map).  At the end of every UTC
  period the Costas sync search runs on the whole IQ band and the window
  shows the sync score by frequency and start time, jt9 sections outlined
  and numbered, each candidate as a bar coloured by sync strength, and a
  candidate table.  After decoding, callsign and SNR labels appear next
  to the bars that decoded.

- **Updates itself.**  About 15 s after it starts, an installed WS Skim asks
  GitHub for a newer release and offers **Update Now / Later / Skip This
  Version**.  Update Now downloads the installer for this OS, checks it
  against the release's `SHA256SUMS.txt`, closes WS Skim, installs over this
  copy and starts it again; settings and logs are kept.  **Help → Check for
  Updates** checks on demand.  A copy installed from a test release is
  offered test releases; one installed from a full release only full
  releases.  Installers now carry a `RELEASE` file naming their tag; copies
  without one (git checkouts, v0.1.4-test.1) never offer updates.

- **FT8/FT4 wideband Costas search (first slice).**  `map144_app/costas_search.py`
  finds FT8 and FT4 sync across a whole IQ period (or a 12 kHz audio WAV) and
  groups the hits into ~3 kHz sections for jt9.  Synthetic tests and a
  developer scan tool (`tools/dev/costas_scan.py`); not yet used by the live
  decoder.
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
