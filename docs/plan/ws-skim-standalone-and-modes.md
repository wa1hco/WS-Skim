# WS Skim plan: standalone UI and modes by band

Status: approved Nov 20, 2026. Work proceeds in the order below, one step at a time.
Updated: Nov 20, 2026. Supersedes the Nov 18 decision that WS Skim is background-only.

## The two programs

### WS Skim: one radio, the whole band
WS Skim listens to an entire band from a single receiver (a Flex panadapter, a B210,
or another wideband source) and decodes every FT8, FT4 and MSK144 signal in it at
once, not just the slice a transceiver is tuned to. It shows a sortable roster of the
stations you need, filtered by your own criteria, and one click tunes your radio to
a station so you can work it. False decodes are filtered out before you see them.
It runs on its own for a single operator, and it can also send its decodes to
WS Hub or any program that understands WSJT-X UDP.

Short form: *WS Skim decodes a whole band from one radio and puts the stations you
need one click away.*

### WS Hub: many radios, many operators
WS Hub is the station console for multi-radio and multi-operator operating. It
gathers decodes from every WSJT-X instance and every WS Skim in the station, shows
them in one place, and coordinates who works whom so seats don't duplicate contacts.
It does no signal processing itself; it grows toward full multi-multi operation.

Short form: *WS Hub ties a station's radios and operators together into one
operating position.*

### Where they overlap, and where they don't
| | WS Skim | WS Hub |
|---|---|---|
| Radios | one | many |
| Operators | one | one or many |
| Signal processing | yes (wideband decoding) | none |
| Needed-station roster | yes, for this radio's band | yes, across the station |
| Click to work | tunes this radio | routes to a seat/radio |
| Runs alone | yes | yes (with WSJT-X instances) |
| Talks to the other | sends decodes over UDP | receives Skim decodes as SKIM spots |

Shared on purpose: the browser display style and the "needed" logic (worked-before,
new DXCC, new grid, and so on), kept in one shared library so the rules don't drift.
Not merged: Skim never manages other radios or operators; Hub never decodes audio.

## Part 1: Standalone UI

### 1. Browser-based display
- Same look and behavior as WS Hub: light theme, sortable columns, tooltips on
  every field that needs explaining.
- Opens from the desktop launcher; no URLs or ports for the user to remember.
- The current Qt windows (status, FT8 Activity, waterfall, Diagnostics) become views
  in the browser UI. Status and health stay visible: receiver, audio, decode rate
  per mode, filtered count, UDP output.

### 2. Needed-station roster
- Filters: band, mode, SNR, CQ only, needed DXCC, needed grid, needed call, continent.
- Worked-before data from the user's log (source to decide; see open questions).
- Sorted, with time since last heard and how many periods the station has been seen.
- Filtered (rejected) decodes never appear; a hover shows the filter breakdown.

### 3. Click to work
- Radio paired with WSJT-X: send WSJT-X the standard UDP Reply message, which tunes
  and calls the station the same as double-clicking a decode in WSJT-X.
- Flex: optionally move the slice directly.
- IC-9700 and others: through WSJT-X's rig control.

## Part 2: Modes by band

| Mode | Bands | Timing | Notes |
|---|---|---|---|
| FT8 | HF, 6 m, 2 m | 15 s periods | done |
| FT4 | HF, 6 m, 2 m | 7.5 s periods | done, concurrent with FT8 (test.14) |
| MSK144 | 6 m, 2 m | 15 s | done; exclusive today |
| Q65-60A | 6 m and 2 m EME | 60 s | Doppler tracking needed for EME |
| Q65 B-E | 432 MHz and up | 60 s (and 30 s variants) | wider submodes for Doppler spread |
| JT65B | 2 m EME | 60 s | legacy, still heard |
| JT4F/G | 10 GHz and up | 60 s | microwave |
| JTTY | HF (20 m main spot 14.090); list also has 50.160 and 144.160 | untimed, 1.888 s frames, about 127 Hz wide | WSJT-X 3.2 test builds only; continuous decoder |

Work each new mode needs:
- Per-band mode enable table (remembered automatically, no config files).
- A period clock per mode (JTTY: continuous streaming instead).
- A sync search per mode to find candidate sections across the wide span.
- Decoder: jt9 flags where jt9 supports the mode; JTTY decoder source and licensing
  to be checked (WSJT-X 3.2 is GPL).
- Doppler: EME and microwave need per-signal frequency drift handling.
- UDP: mode field and id SKIM-{SRC}-{BAND}-{MODE}-{kHz in band}; JTTY text is
  free-form, so how WS Hub shows it needs agreement with WS Hub.
- Roster and status: per-mode decode rate; new modes appear in the roster filters.

## Suggested order
1. Shared "needed" library with WS Hub (agree on it with WS Hub first).
2. Browser UI shell with status/health views.
3. Needed-station roster.
4. Click to work (WSJT-X Reply first, then Flex slice).
5. New modes, per Part 2.

## Open questions
- Source of worked-before data: WSJT-X ADIF log, LoTW, Club Log, or WS Hub's log.
- Reuse WS Hub's web code directly, or share only the style and the needed library.
- Click to work: WSJT-X Reply, direct Flex slice control, or both.
- Which bands get which modes by default.
- Whether JTTY is worth skimming before WSJT-X 3.2 is final.
