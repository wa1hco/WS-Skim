# WS Skim plan: two receivers in one WS Skim (RSPduo dual tuner)

Status: **approved 21 Nov 2026 ("go"). Phases A–C and the stacked Band
Activity pane from phase D are built for v0.1.4-test.22.**
Baseline: `main` at 720d425 (v0.1.4-test.21, single-tuner SDRplay source).

## Decisions (Jeff, 21 Nov 2026)

- **Option A**: one WS Skim, two engines, one merged roster.
- **UDP ids**: always `SKIM-RSP1-…` / `SKIM-RSP2-…`. This is a one-time
  change from test.21's `SKIM-RSP-…`.
- **Band Activity**: stacked panes on a shared UTC time axis.
- **Controls are WSJT-X-centric**: for each tuner the operator only
  picks a band, plus Tuner 2 on or off.
  - The mode frequencies come from WS Skim's FT8/FT4/MSK144 band tables.
  - Tuner 2 uses Tuner 1's modes (Mode menu), so the main window has no
    per-tuner mode or antenna controls.
  - The Tuner 2 checkbox and band pulldown sit in the toolbar next to
    "Band (Tuner 1)". They only show for the SDRplay source.
- **Release**: one release (test.22) once the GUI band pickers are in.
- **Decoder bandwidth**: Jeff asked for about 96 kHz per tuner, because
  48 kHz on the Flex feels cramped. That change reaches further than a
  contained fix (see "96 kHz per tuner" below), so test.22 shipped two
  tuners at 48 kHz each; test.23 adds 96 kHz for FT8/FT4.

## As built (test.22)

- **`ws_skim/receivers.py`**: `SecondReceiver` is a bare `Engine`, a
  tuner-B `SDRconnectSource` and an `rx2-drain` thread.
  - The GUI sets `main._rx2_enabled` and `main._rx2_band_mhz`.
  - Twice a second the radio thread calls `reconcile()`, which starts,
    stops, retunes and mode-syncs receiver 2 from those two values.
- **`ws_skim/rx2_controls.py`**: the toolbar's Tuner 2 checkbox and band
  pulldown.
  - QSettings keys: `rx2_enabled` and `rx2_dial_freq_mhz_<mode>`.
  - The default Tuner 2 band is the next band up from Tuner 1's.
- **Decode drain**: the GUI drain tags receiver 2's decodes with `rx: 2`,
  `source: "sdrconnect2"` and its calling frequency. They then take the
  same path as receiver 1's: Roster, Band Activity and Reporter.
- **Reporter**: it takes the source from each decode and keeps receiver
  2's passband (`set_receiver_context`). A receiver 1 context refresh
  therefore does not close the `SKIM-RSP2-…` instances.
- **`SDRconnectSource`**:
  - In dual mode the usable span is ±0.70 MHz minus a guard, and the
    sample rate is never set.
  - On tuner 2 it reports "single-tuner mode" when SDRconnect is in
    single mode.
  - When Tuner 2 is ticked, an SDRconnect that WS Skim launched is
    switched to the Dual device (`set_device_mode`). The user's own
    SDRconnect window is never switched.
  - Tuner 2's client never launches SDRconnect and stops before tuner
    1's, so a headless SDRconnect stops only after the last receiver.
- **`channelizer.PARALLEL_KERNEL_LOCK`**: serialises the
  `@njit(parallel=True)` kernels. CI runs a four-thread stress test on
  Linux and Windows.
- **`ft8_engine.JSONL_LOCK`**: serialises appends to `decodes.jsonl`,
  `rejected.jsonl` and `launches.jsonl`. FT8/FT4 lines in
  `decodes.jsonl` carry `rx`.
- **Status page**: a "Tuner 2" row with band, modes, IQ rate, decodes per
  minute and any SDRconnect problems. It is hidden unless Tuner 2 is on.
  The SDRplay window gets a "Tuner 2" status line.
- **Band Activity**: a Tuner 2 pane sits below Tuner 1's, in a splitter
  whose position is remembered. It shares the Waterfall, Detections and
  History controls and the time axis. With one tuner the window looks
  as before.
- **Not built yet**:
  - the Sync Map receiver picker;
  - the Roster "Rx" column (the band checkboxes already split the
    tuners);
  - a soak run (phase E).

## 96 kHz per tuner (built in test.23)

**Rule (`ws_skim/rates.py`):** a receiver runs at 96 kHz when MSK144 is
off and its source is SDRconnect; otherwise at 48 kHz. MSK144's
channelizer, TFMF and candidate-extract constants stay 48 kHz-only.

**How the rate changes at run time:**
- The radio thread (`runtime._reconcile_rate`) and `SecondReceiver.sync_modes`
  ask the SDRconnect client for the wanted rate (`set_target_rate`).
- The client's stream thread rebuilds its decimator before the next IQ
  frame (2 MS/s: ÷5 then ×6/25 for 96 kHz) and stamps the rate on every
  packet (`_SDRconnectPacket.rate`).
- The drain loop (receiver 1) and `SecondReceiver.feed` (receiver 2) call
  `Engine.set_sample_rate()` when a packet's rate differs, so queued
  old-rate samples are never processed at the new rate.
- `Engine.set_sample_rate()` rebuilds everything sized by the rate: the
  FFT (`fft_size` scales with the rate, so waterfall rows and bins keep
  their width), spectrogram and time-domain buffers, channelizer, the
  30 s IQ ring, the time base (re-anchored) and the FT8/FT4 period
  state. A period or two is lost on a switch.
- Every other source creates its client at 48 kHz, and the radio loop
  puts the engine back at 48 kHz when the source is not SDRconnect.

**Also in test.23:**
- The channelizer is skipped unless MSK144 runs at 48 kHz (or the B210
  WSJT-X audio bridge taps channel 0). FT8/FT4 never read it.
- With "WS Skim tunes the radio", the tuner LO is kept at least
  `rate/2 + 2 kHz` from the calling frequency, so the DC spur is outside
  the decoded band; otherwise the status line notes it.
- Section planning was already rate-general (`EDGE_FRACTION × fs`). At
  96 kHz centred on 7.074, `pick_dial_mhz("FT4")` picks 7.0475 and its
  dial section is always decoded. The UDP id is `SKIM-RSP1-40m-FT4-48`
  (the kHz-in-band rule rounds 7047.5 to 48).
- Band Activity follows the rate through `BandWaterfall.push(…, fs)`.

**CPU (box, 32 s of noise, FT8+FT4, Python only):** 2.8 s at 48 kHz and
4.6 s at 96 kHz, both with the channelizer skipped. jt9 cost grows with
the number of sections, which grows with the activity inside the wider
band.

**Not done:** 96 kHz for MSK144 (needs rate-derived channelizer and
TFMF constants plus a corpus rerun), and for the B210, Airspy, RTL-SDR
and SDRangel sources (their decimators are built once; switching needs
a source restart). Flex DAX IQ is fixed at 48 kHz.

## Goal

One WS Skim decodes both RSPduo tuners at once, for example 7.074 and
10.136 FT8, 20 m + 40 m, or 6 m + 2 m MSK144.  Each tuner is its own
**receiver**, with its own band, modes, decodes and WSJT-X UDP ids.  The
receivers are shown together in Band Activity, the FT8 Sync Map and the
Roster, and reported through the same WSJT-X UDP, PSKReporter and DX
cluster links.

This still fits the Nov 20 decision ("WS Skim: one radio, the whole
band").  It is one radio with two tuners.  Many radios and many operators
stay in WS Hub.

## What assumes one band today

| Area | Today | What changes |
| --- | --- | --- |
| `engine.Engine` | A mixin of the main window (`WSSkimVisualizer(Engine, QMainWindow)`).  It holds all DSP state on `self`: IQ ring, timebase, channelizer, MSK144 detector, FT8/FT4 period workers, `center_freq_mhz`, `calling_freq_mhz`, `mode`/`period_modes`, `_decode_queue`, `_ft8_results`. | `Engine` already works without Qt: tests (`test_ft8_mode.py`, `test_timebase_wiring.py`) and `analysis_engine` create bare `Engine(...)` objects and feed `process_iq_data`.  A second receiver can be a bare `Engine`. |
| `processing.py` | 262 `self.` uses.  The GUI bits are all `getattr(self, '_fast_graph_win', None)`-style, so a bare Engine just skips them. | Nothing for receiver 2.  The diagnostic windows stay on receiver 1. |
| `runtime.run_radio_source` | One radio thread with one `source_mode` branch.  It drains one source queue into `self.process_iq_data`. | A second drain loop (its own thread) for receiver 2's queue into receiver 2's Engine. |
| Source | `SDRconnectSource` is one tuner per WebSocket connection (`tuner=primary/secondary`; type 2 or 5 frames; secondary-enable filter). | Two `SDRconnectSource` objects, one per tuner.  Tested live (read-only) on Jeff's SDRconnect: **two clients can stream at once**, and `iq_stream_enable` is per connection.  Client A turning its stream off did not stop client B, and each got both tuners at 2 MS/s. |
| Band choice | One band combo in the main window, from `ui._bands(mode)`.  `on_band_changed` sets `calling_freq_mhz`/`center_freq_mhz` and retunes the active source. | One band combo per receiver (see GUI). |
| Mode | The Mode menu (MSK144, or FT8 and/or FT4) sets `mode`/`period_modes` on the one Engine. | Per receiver.  6 m MSK144 + 40 m FT8 is a real case. |
| `reporting.Reporter` | One instance with one `self.source`.  It already keeps one emulated WSJT-X instance per (source, band, mode, dial) region, using `instance_id(self.source, dial_hz, mode)`. | Take the source tag from the decode (`decode["source"]`) when present.  One Reporter serves both receivers.  PSKReporter and DX cluster stay single sessions, since spots carry their own frequency. |
| UDP ids | `SKIM-{SOURCE}-{band}-{mode}-{kHz}`, e.g. `SKIM-RSP-40m-FT8-74`. | Already unique per band.  Add the tuner number so the ids say which tuner heard it, and stay unique if both tuners ever share a band: `SKIM-RSP1-40m-FT8-74`, `SKIM-RSP2-30m-FT8-136`. |
| Band Activity | One `BandWaterfall` fed from the radio thread with the engine's `center_freq_mhz`, plus one `DetectionStore`. | One waterfall and store per receiver.  Stacked panes (below). |
| FT8 Sync Map | Draws `engine._ft8_results`, with a Show box to pick FT8 or FT4. | Add a receiver picker next to Show ("Tuner 1 40 m / Tuner 2 30 m"). |
| Roster | The model is already band-aware (`band` from `radio_khz`, live band checkboxes, band filter).  Records come from the decode queue via `add_queue_record(panel, result, calling_freq_mhz)`. | Feed both receivers' queues in.  Pass each receiver's calling frequency.  Add an optional "Rx" column.  The band checkboxes then split the two receivers for free. |
| Status page | One receiver row, band/modes, IQ rate and decodes per minute. | One "Receiver" and "IQ data" row per receiver.  Decode rates per mode summed, with a hover breakdown. |
| Output files | `decodes.jsonl`, `launches.jsonl` and MSK144 WAVs in the one output dir. | Add `rx` to every JSONL record.  One writer lock per file (two threads append).  WAV names get an `rx2_` prefix. |
| numba channelizer | `@njit(parallel=True)`.  The threading layer on the box is `omp`. | **Risk:** numba's fallback `workqueue` layer is not thread-safe if two threads call parallel kernels at once.  Wrap the parallel kernels in a process-wide lock, or check the layer at start-up.  See Risks. |

## Option A: two receivers in one process (recommended)

- **Receiver** = (source stream, band, modes, Engine, drain thread,
  Band Activity store).  Receiver 1 is the existing main-window Engine,
  unchanged.  Receiver 2 is a bare `Engine` with its own drain thread.
- **Threads:**
  - SDRconnect client 1 and its drain thread run receiver 1 (as today).
  - SDRconnect client 2 and a new `rx2` drain thread run receiver 2.
  - Each Engine keeps its own FT8/FT4 period workers and jt9 runs.
- **Decodes:** both Engines' `_decode_queue`s are drained by the GUI drain
  loop.  Each record is tagged with `rx`, `source` and the receiver's
  calling frequency, then goes to the Roster, Band Activity, the Reporter
  and the status counters.
- **jt9:** one subprocess per section, as today.  At FT8 period ends both
  receivers launch theirs together.  That is about 2× the jt9 burst, still
  short.

### CPU budget (measured on the box, 8 vCPU Xeon; desktop-jeff has 16 threads)

| Load | Measured | Notes |
| --- | --- | --- |
| SDRconnect stream, 2 MS/s → 48 kHz | 13–14% of one core per tuner | Live on desktop-jeff (test.21 check). |
| FT8 receiver, no jt9 | 0.32 core-s per audio-s | 33 s of noise through a bare Engine. |
| 2 FT8 receivers in two threads, no jt9 | Fed 33 s of IQ each in 1.2 s wall (vs 0.85 s for one) | ~28× real-time headroom, so GIL contention is small. |
| MSK144 receiver | ~1.1 core-s per audio-s (numba parallel) | |
| 2 MSK144 receivers in two threads | 42 core-s per 33 s, fed in 6.8 s wall | ~1.3 cores, 5× headroom. |
| jt9 | not measured here | It runs at FT8 period ends and when MSK144 fires.  WSJT-X itself uses ~0.3–1 core-s per period at depth 3. |

- **Two FT8 bands:** about 1–1.5 cores total including both streams and
  jt9.
- **6 m + 2 m MSK144:** about 2.5–3 cores.
- Both fit desktop-jeff easily.  A worker process per receiver is not
  needed now.  The receiver object is built so it could move into a child
  process later, since IQ in (384 kB/s) and decodes/PeriodResults out are
  small and picklable.

### Why not one process per receiver right away

It would avoid the GIL and numba thread-safety questions.  But Band
Activity rows, Sync Map PeriodResults and decodes would all need IPC, and
the numbers above show threads have plenty of headroom.  Keep it as the
fallback if phase B measurements disappoint.

## Option B: a second WS Skim instance with its own settings profile

How it would work:
- A second desktop launcher, "WS Skim — Tuner 2", starts WS Skim with a
  profile name passed by the launcher itself (no user-typed arguments).
- Settings move to `settings-<profile>.ini`, and the output dir to
  `…/<profile>/`.
- Each instance picks its tuner in its SDRplay window.
- SDRconnect already accepts two clients.

Pros:
- Least engine work (about 2–3 days).
- The engine stays exactly as it is.

Cons:
- Two of every window: two status pages, two Band Activity windows, two
  Sync Maps, and two browser Rosters on ports 8788/8791.
- No merged roster: the needed-station view is split, unlike WS Hub.
- Two DX cluster logins with the same call (most nodes drop the first),
  so DX cluster must be on in only one.
- Two PSKReporter sessions.
- "Launch SDRconnect" and "WS Skim tunes the radio" must be coordinated
  so the two don't fight over the device mode.
- Click-to-work later has to pick an instance.

**Recommendation: Option A.**  It gives what Jeff asked for: one WS Skim
with both bands side by side and one merged roster.  The engine is already
separable (bare `Engine` objects are tested), and the CPU headroom is
large.  Option B stays as the fallback if a numba or GIL problem shows up
in phase B and the worker-process route is not worth it.

## GUI (no CLI args or env vars)

### Picking bands per tuner

- **SDRplay (SDRconnect) window:** a **Receivers** group replaces the
  single Tuner box:
  - `[x] Tuner 1 (A)  Band [40m 7.074 ▾]  Modes [FT8 ☑ FT4 ☐ MSK144 ☐]  Antenna [50 Ω ▾]`
  - `[x] Tuner 2 (B)  Band [30m 10.136 ▾] Modes [FT8 ☑ FT4 ☐]            Antenna [50 Ω]`
- **Dual-tuner mode is automatic:** ticking both tuners means dual-tuner
  mode.  WS Skim selects "RSPduo (…) (Dual)" when it opens the device, and
  says "switch SDRconnect to Dual" if an SDRconnect window is open in
  Single.
- **Main window:**
  - The band combo becomes the Tuner 1 band, labelled "Band (Tuner 1)".
  - A second combo, "Band (Tuner 2)", appears only when two receivers are
    active.
  - The Mode menu applies to the receiver chosen in a small "Mode for:
    Tuner 1 / Tuner 2" submenu.  Alternatively, keep the Mode menu for
    Tuner 1 and set Tuner 2's modes in the SDRplay window (open question 2).
- **Persistence:** settings are saved per receiver in QSettings, e.g.
  `rx2/band_FT8`, `rx2/modes`, `rx2/enabled`.

### Band Activity with two non-contiguous bands

- **Stacked panes, not tabs:** Jeff wants to watch both at once.
- One pane per receiver, each with its own frequency axis (≈48 kHz around
  its dial, today's view) and its own title ("Tuner 2 — 30 m 10.136").
- A shared UTC time axis, scrolling together, with the time label on the
  left of the top pane only.
- The splitter between panes is remembered.
- The Waterfall and Detections check boxes apply to both panes.
- Mode shading is per pane.
- Hover shows the receiver too.
- With one receiver the window looks exactly as today.

### FT8 Sync Map

- Add a "Receiver" combo beside the existing Show (FT8/FT4) box.  The Sync
  Map is a diagnostic, so one at a time is fine.

### Roster

- One roster for both receivers.
- The existing band checkboxes and band column already separate them.
- Add an optional **Rx** column (1/2) in the columns menu, off by default.
- The worked-before and needed logic is unchanged: it is per band already.

### Status page

- **Receiver row:** "SDRplay RSPduo — Tuner 1 40 m FT8 · Tuner 2 30 m FT8",
  with one dot per receiver.
- **IQ data:** one row per receiver.
- **Decodes per minute:** summed per mode, with per-receiver numbers in
  the hover.

## UDP ids and reporting

- **Ids:** `SKIM-RSP1-40m-FT8-74` and `SKIM-RSP2-30m-FT8-136`.  The tuner
  number is in the source token, so `source_tag()` gets `RSP1`/`RSP2`.
- **Single-tuner use:** use `RSP1` for consistency.  The catch is that
  ids change once from test.21's `SKIM-RSP-…` (open question 3).
- **Heartbeats and Status:** each id gets its own heartbeat and Status,
  as today.  WS Hub sees two more WSJT-X instances and shows the band from
  the Status dial.
- **Reporter:** `_handle_decode` uses `decode.get("source") or self.source`.
  `center_hz` and `bandwidth_hz` become per receiver, used only for the
  dial fallback.
- **PSKReporter:** one session.  The spot frequency differs per receiver.
  The receiver-information antenna field could say "RSPduo T1/T2" later.
- **DX cluster:** one login.  Spots carry the frequency.

## SDRconnect dual-tuner setup

- **Device mode:**
  - When WS Skim opens the device itself (headless, fresh): send
    `selected_device_name` "RSPduo (<serial>) (Dual)".  This is already
    supported via the "RSPduo Mode" setting; ticking two receivers forces
    it.
  - Never switch an open device.  If SDRconnect is in Single and two
    receivers are ticked, receiver 2 shows "SDRconnect is in single-tuner
    mode — choose the RSPduo (Dual) entry in SDRconnect".
- **Per-tuner connection:**
  - Tuner 1 client: listens to type 2 frames.  Today's default filter
    already leaves the secondary off.
  - Tuner 2 client: sends `set_secondary_device_enable` true and
    `set_primary_device_enable` false, then takes type 5.  Already in
    test.21.
- **Tuning:** each client sets `device_center_frequency` with its own
  `device` field.  Jeff's live SDRconnect showed the tuners independent
  (14.000 and 7.000, later 7 and 10 MHz).
- **Usable span (needs a code change):**
  - Dual mode is Low-IF with 1.536 MHz IF bandwidth per tuner and 2 MS/s
    displayed.  Treat ±0.70 MHz as usable (flat), not today's ±0.97 MHz.
    The in-span test becomes `|offset| ≤ 0.70 MHz − 24 kHz` in dual mode.
  - The LO goes 100 kHz below the dial, which keeps the band well inside
    the flat part.  Single mode keeps today's rule.
- **Sample rate:** fixed at 2 MS/s per tuner in dual mode.  Never
  `set_property device_sample_rate` in dual mode.
- **Antennas:**
  - Tuner 1: 50 Ω or Hi-Z.
  - Tuner 2: 50 Ω only (Bias-T port).
  - Read the list from `valid_antennas` per tuner instead of the fixed list.
- **Stopping:** one client stopping must not stop the other.  Confirmed:
  `iq_stream_enable` is per connection.  Only the last receiver stops the
  headless SDRconnect WS Skim started.  This needs a small shared "owner"
  so the two clients share one launched process.

## Does it generalize?

Yes.  "Receiver" means any IQ stream plus a band.

| Source | Receivers | Notes |
| --- | --- | --- |
| RSPduo (SDRconnect) | 2 (dual-tuner mode), independent bands | This plan. |
| SDRplay single-tuner radios (RSP1B, RSPdx) | 1 | Unchanged. |
| FlexRadio | Up to 4 DAX IQ streams, one per panadapter, each on its own band | One `FlexDAXIQ` client per DAX IQ channel feeds one receiver each.  This needs Flex client work (DAX IQ channel 2–4 setup), so it is a later step. |
| USRP B210 | 2 RX channels, but one shared RX LO (AD9361) | Two bands only within one tuned span.  Today the two channels are H/V dual-pol on one band, which stays.  Two bands within a span would be "two receivers on one stream" (two Engines with different `calling_freq_mhz` fed from one wideband stream).  That is the router's domain. |
| SDRangel | N device sets | One receiver per device set, possible later. |

So the receiver list and the per-receiver Engine, drain thread and Band
Activity pane are source-agnostic.  Only the source-side setup differs.

## Phases

Each phase lands as its own commit to main and its own test.N release
after green CI.

| Phase | Content | Tests | Effort |
| --- | --- | --- | --- |
| **A. Receiver model + second Engine (no UI yet)** | A `Receiver` dataclass (id 1/2, source, band, modes, Engine, drain thread).  Receiver 1 wraps the window Engine.  Receiver 2 is a bare Engine with its own drain thread.  Decode tagging (`rx`, `source`, calling freq).  Reporter takes the source per decode.  `rx` in JSONL with writer locks; rx-prefixed WAV names.  numba kernel lock. | Two fake-SDRconnect tones on two tuners and two bands → decodes from both with the right ids (`SKIM-RSP1-40m-FT8-74`, `SKIM-RSP2-30m-FT8-136`) via a fake jt9.  Concurrent-numba stress test.  Shutdown with both running (SIGTERM, `timeout 30` rc 124, no Traceback). | 2–3 days |
| **B. SDRconnect dual setup** | Two clients with a shared launched-process owner.  Dual-mode span rule (±0.70 MHz).  No rate set in dual mode.  Per-tuner antenna list from `valid_antennas`.  Single/Dual mismatch message. | Fake server in dual mode: independent per-tuner tuning, second client stop doesn't stop the first, launched headless stops only after the last receiver.  **Live read-only check** on desktop-jeff in dual mode (7.074 + 10.136): CPU, gaps and decodes per tuner for 5 min. | 1–1.5 days |
| **C. GUI: receivers, bands and modes** | Receivers group in the SDRplay window, "Band (Tuner 2)" combo, per-receiver modes, status page rows. | Offscreen GUI tests: enabling Tuner 2 creates receiver 2; band changes retune the right tuner (fake server `device` field); settings persist. | 1.5–2 days |
| **D. Band Activity stacked panes + Sync Map receiver picker + Roster Rx column** | Per-receiver BandWaterfall/DetectionStore, splitter, shared time axis, hover with rx. | Offscreen: two panes appear, waterfall rows go to the right pane, one receiver looks as today.  Roster model test with two bands.  Screenshot for Jeff. | 2 days |
| **E. Soak + polish** | Jeff runs 7 + 10 MHz FT8 for an evening.  Compare decode counts against two WSJT-X copies if he has them. | Real-world: no gaps, CPU budget, WS Hub shows four instances (FT8/FT4 × 2 bands). | 0.5 day + Jeff's time |
| Later | Flex multi-DAX receivers; a receiver per process if ever needed; both tuners on one band for diversity (phase 3 of the RSPduo plan, via the existing H/V combine path). | — | — |

Total for A–D: about 7–8.5 working days of agent time, released in four
steps.

## Risks

- **numba thread-safety:** two receivers calling `@njit(parallel=True)`
  kernels at the same moment is fine on the `omp`/`tbb` layers but can
  abort on numba's fallback `workqueue` layer.  Windows installs may not
  have OpenMP.
  - Mitigation: a process-wide lock around the parallel channelizer kernels.
  - Or check `numba.threading_layer()` at start-up and fall back to
    serialized calls.
  - A stress test runs in CI on Linux and Windows.
- **GIL contention:** measured small for FT8 (28× headroom) and acceptable
  for MSK144 (5×).  If the soak shows late periods, move receiver 2 into a
  worker process (the receiver object is designed to allow it).
- **Single-band globals:** a few helpers read the window's
  `calling_freq_mhz` or `source_mode` directly, e.g. `add_queue_record`,
  status rows and the `_get_tuned_frequency_mhz` path for UDP Status.
  Phase A must route these per receiver.  Tests pin the ids.
- **Output-file collisions:** two threads appending to `decodes.jsonl`;
  WAV names in the same second.  Locks and an rx prefix fix this.
- **SDRconnect GUI in Single mode:** WS Skim must not switch it (Jeff's
  window).  It tells him instead.
- **UDP id change:** `SKIM-RSP-…` becomes `SKIM-RSP1-…`, so WS Hub sees new
  instances once.  It would not if open question 3 is answered "keep RSP
  for single".
- **Dual-mode passband:** a dial near the edge of the 2 MS/s span is in
  the IF filter's roll-off.  The ±0.70 MHz rule keeps WS Skim retuning
  when needed.  With "WS Skim tunes" off it reports out of span.
- **Click-to-work (future):** the RSPduo is receive-only.  Click-to-work
  goes through WSJT-X/the transceiver, which must be on that receiver's
  band.  The roster row already carries the band, so that's OK, but worth
  noting.

## Open questions for Jeff (answered: see Decisions at the top)

1. **Option A (one WS Skim, merged roster) vs Option B (a second WS Skim
   per tuner)?**  Recommended: A.
2. **Modes per tuner:** set Tuner 2's modes in the SDRplay window, or a
   "Mode for: Tuner 1 / Tuner 2" choice in the Mode menu?
3. **UDP ids:** `SKIM-RSP1-…`/`SKIM-RSP2-…` always (one id change from
   test.21's `SKIM-RSP-…`), or `RSP` in single-tuner use and RSP1/RSP2
   only in dual?
4. **Band Activity:** stacked panes (recommended), or side by side for
   wide screens?
5. **Release order:** release after each phase (A, B, C, D as test.22–25),
   or hold until C so the first two-tuner release is usable from the GUI?
