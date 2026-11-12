# Plan: FT8/FT4 decoding and WSJT-X UDP to WIMS

Status: plan. Prerequisites 1, 2 and 4 are done (PR #4); the feature
work has not started. Repository: `wa1hco/WS-Skim` (renamed from
`map144`, then `ws-skimmer`; default branch `main`).

Goal: a wideband FT8/FT4 (and existing MSK144) skimmer for the Flex 8600
(DAX IQ) and B210 that decodes the whole passband, including nonstandard
frequencies, and sends WSJT-X-style UDP Status/Decode packets to WIMS as a
receive-only spot source. WIMS never auto-transmits from a skimmer spot;
clicking a spot retunes the radio over CAT, then Configure/Reply uses the
instance's own decode.

Related: this repo's copy of [docs/flex-daxiq-interface.md](../flex-daxiq-interface.md)
(imported from [wa1hco/ws-skimmer-placeholder#1](https://github.com/wa1hco/ws-skimmer-placeholder/pull/1));
WIMS-side design in [WIMS PR #39](https://github.com/wa1hco/WIMS/blob/docs/wideband-skimmer/docs/plan/wims_wideband_skimmer.md).
Installer and release plan: [docs/plan/installer-and-release.md](installer-and-release.md).

## Prerequisites (separate PRs, before feature work)

1. ~~**GPLv3 `LICENSE` file.**~~ Done in PR #4 (`LICENSE`, `NOTICE`).
2. ~~**WSJT-X attribution headers.**~~ Done in PR #4: the files in
   `fortran_bp_test/` and `ws_skim/msk144_decode.py` carry WSJT-X
   copyright and GPLv3 notices; `vendor/wdsp` already had them.
3. **Tag the current MSK144 baseline** on `main` before the mode-abstraction
   branch, so the last MSK144-only tree is recoverable. The latest GitHub
   release is still the prerelease `v0.1.3-alpha` (2026-09-07); `main` is
   many commits ahead of it. Proposed tag: `v0.1.4` (see the
   [installer plan](installer-and-release.md)). Still open.
4. ~~**CI workflow.**~~ Done in PR #4: `.github/workflows/ci.yml` runs
   pytest on Python 3.10 and 3.12. Keep it green before merging mode or
   decoder changes.

## Current code (facts to change against)

- `ws_skim/channelizer.py`: 48 channels × 1 kHz at 12 kHz, an
  MSK144-specific grid. Reusable as a mix/decimate building block, but a
  ~3 kHz FT8/FT4 section is a different design, not a drop-in of that grid.
- `ws_skim/detection.py` `extract_and_decode`: the jt9 wrapper. Current
  `JT9_BASE_ARGS` are `--msk144 -L 1450 -H 1550 -F 50 -d 2`. It writes a
  short 12 kHz mono burst WAV, runs jt9, and **keeps only the first
  non-empty stdout line** that does not start with `<` or `EOF`. There is
  no `--ft8` / `--ft4` path and no period-length WAV writer.
- `ws_skim/reporting.py` hardcodes `"MSK144"` in `build_status`
  (approximately L148–179) and `build_decode` (approximately L214–237).
- `ws_skim/timebase.py` `TimeBase(period_secs=15.0)` is fixed at 15 s.
- Live code ignores radio packet timestamps and uses `time.time()`. That
  is a known UTC period-alignment gotcha for both MSK144 and FT8/FT4.

MSK144 remains the default. Existing tests, corpus, and parity fixtures
must keep passing while the abstraction is introduced.

## Work plan

### 1. Mode abstraction (MSK144 default)

Introduce a mode object (name, WSJT-X mode string, T/R period, audio
bandwidth, jt9 flags, reporting fields) with **MSK144 as the default**.
Add this on a feature branch, not `main`. Keep current tests and
fixtures green; do not require a mode flag for today's MSK144 path.

### 2. Parametrize `reporting.py`

Replace the hardcoded `"MSK144"` strings with the active mode's name and
T/R period. Status and Decode fields (mode, dial frequency, period,
transmit-enable) must be filled from that object.

- **Dial frequency** in both Status and Decode is the **section base
  frequency**, not the 48 kHz IQ center and not the audio offset inside
  the 12 kHz WAV. WIMS adds the audio offset to the dial to get RF.
- **Receive-only instance IDs**, for example `SKIM-B210-2m` or
  `SKIM-FLEX-2m`, identify the skimmer as a spot source. Status must
  advertise that the instance does not transmit (`tx_enabled` false).
  A skimmer Decode alone cannot be "Replied" to by WSJT-X.

### 3. `TimeBase` period parameter

Parameterize `TimeBase(period_secs=...)` (15.0 s FT8/MSK144, **7.5 s
FT4**). The live path should eventually honor radio/IQ timestamps rather
than only `time.time()`, or document and bound the residual UTC skew.

### 4. Period-aligned 12 kHz WAV writer

New work. Today's wrapper writes short burst WAVs for MSK144, not a
full UTC period. For FT8/FT4, write a 12 kHz, 16-bit mono WAV covering
exactly one T/R period, aligned to the UTC period boundary, after
shift/decimate of each ~3 kHz section.

The installed jt9 (see below) **rejects any sample rate other than
12 kHz** for FT8/FT4 (`jt9.f90`: `nfsample /= 12000` → stop 2).

UTC in the Decode line comes from the WAV file name
(`lib/jt9_input_validation.f90`, `parse_wav_filename_nutc`): if the
stem ends in six digits those are `HHMMSS`; else a stem ending in
`_HHMM` uses those four digits; otherwise `nutc=0`. Name files so that
field is the period start, for example `section_144174_122100.wav`.

### 5. Wideband Costas search, sectioning, and jt9

Once per UTC period (15 s FT8, 7.5 s FT4):

1. Cheap Costas sync search over the 48 kHz IQ.
2. Group hits into ~3 kHz sections.
3. Shift/decimate each hit section to a 12 kHz period-aligned WAV
   (section baseband at a known audio center).
4. Invoke jt9 with `--ft8` or `--ft4`, `-p` set to the mode period, and
   `-L`/`-H` limited to the hits in that section (plus the other flags
   below).
5. Parse **every** decode line (not just the first) and emit a Decode
   packet per message, with dial = section base frequency.

The 1 kHz MSK144 channelizer grid is not the FT8/FT4 sectioner.

**Status (5.1, 5.2 started):** `ws_skim/costas_search.py` has the
wideband sync search and the section grouping, as a standalone DSP
module with synthetic tests (`tests/test_costas_search.py`) and a
developer scan tool (`tools/dev/costas_scan.py`). Not wired into the
live engine yet. Measured on synthetic white noise at 48 kHz IQ:

| Mode | Search time / period | Noise candidates / period (±24 kHz) | Found by the search |
|------|----------------------|--------------------------------------|---------------------|
| FT8 (threshold 1.7) | ~0.25 s | ~2.6 | 10/10 at -20 dB, ~7/10 at -22 dB |
| FT4 (threshold 1.5) | ~0.1 s | ~30 | 10/10 at -10 dB, ~7/10 at -16 dB |

FT4 is about 2 dB short of jt9's FT4 limit (~-17.5 dB) because it has
6.4 dB less sync energy than FT8; coherent correlation within each
4-symbol Costas array (as WSJT-X `sync4d` does) is the planned fix.
The FT4 DT reference (`nominal_start_s = 0.5`) still needs checking
against jt9 on a real FT4 capture.

### 6. Alternative decoder (cross-check)

Borrow [ka9q/ft8_lib](https://github.com/ka9q/ft8_lib) `decode_ft8`
(MIT; used in ka9q-radio's whole-band Costas search) as an alternative
and cross-check to jt9, not as a silent replacement in the first cut.
License compatibility (MIT into GPLv3) is acceptable; keep provenance
in a `SOURCE.md` (or equivalent) when vendored.

### 7. Zero-knowledge UX

Users configure and start the skimmer from the **GUI settings and a
desktop launcher only**. No required CLI flags, environment variables,
or `python -m` in user-facing docs or launchers. Internal developer
scripts may still take flags. This matches the standing zero-knowledge
UX rule and the draft [PR #1](https://github.com/wa1hco/WS-Skim/pull/1).
The old platform READMEs with CLI/env instructions have moved to
`docs/dev/` as developer notes; the launchers are tracked in the
[installer plan](installer-and-release.md).

## Verified jt9 flags and output format

**Primary source: the author's installed binary**, not a guessed or
stock-2.7 usage string.

- Binary: `/usr/local/bin/jt9` on desktop-jeff
- Version: `jt9 -v` → `jt9 3.3.0-beta4 (revision f33bdb)` (2026-10-27)
- Built from `/home/jeff/ham/wsjtx-inhibit-3.3b4` at `f33bdbe0c`
  ("Merge pull request #9 from wa1hco/rebase-v3.3.0-beta4"), installed
  the same afternoon (binary mtime 2026-10-27 14:34, America/New_York).
- Usage: captured from `jt9 -h` on that binary (2026-11-10).
- Line formats and period behavior: the matching tree's
  `lib/decoder_callbacks.f90`, `lib/decode_completion.f90`,
  `lib/jt9.f90`, and `lib/jt9_input_validation.f90`.

Flags below are from that `jt9 -h` output. A stock SourceForge `master`
`jt9` (2.7-era) is **not** the same: it lacks `-v`, `-r`/`--share-path`,
`--multithreadft8` and the `-M/-C/-U/-R/-N/-E/-D/-Z` family, its `-q`
was QRA64 rather than `--quiet`, and its `-H` default was 4007 (here
**4000**). Pin the skimmer to this 3.3.0-beta4 binary, or re-verify
if the installed jt9 is upgraded (a 3.3.0-beta5 tree already exists
locally but is not what `/usr/local/bin/jt9` is).

### Flags (from `jt9 -h` on 3.3.0-beta4)

`Usage: jt9 [OPTIONS] file1 [file2 ...]` then `OPTIONS:`. Relevant
entries, wording copied from the help text:

| Short | Long | Argument | Help text |
| ----- | ---- | -------- | --------- |
| `-8` | `--ft8` | (none) | FT8 mode |
| `-5` | `--ft4` | (none) | FT4 mode |
| `-k` | `--msk144` | (none) | MSK144 mode |
| `-p` | `--tr-period` | `SECONDS` | Tx/Rx period, default SECONDS=**60** |
| `-L` | `--lowest` | `HERTZ` | Lowest frequency decoded (JT65), default HERTZ=**200** |
| `-H` | `--highest` | `HERTZ` | Highest frequency decoded, default HERTZ=**4000** |
| `-f` | `--rx-frequency` | `HERTZ` | Receive frequency offset, default HERTZ=**1500** |
| `-F` | `--freq-tolerance` | `HERTZ` | Receive frequency tolerance, default HERTZ=**20** |
| `-d` | `--depth` | `DEPTH` | Decoding depth (1-3), default DEPTH=**1** |
| `-a` | `--data-path` | `PATH` | Location of writeable data files, default PATH="." |
| `-t` | `--temp-path` | `PATH` | Temporary files path, default PATH="." |
| `-q` | `--quiet` | (none) | Quiet mode, no `<DecodeFinished>` |

`-L`/`-H` are still labeled "(JT65)" in the help, but they populate
`flow`/`fhigh` and are passed through as `nfa`/`nfb` for FT8/FT4 as
well. Use them as the hit-window limits.

**`-p` on the WAV path we will use:** the help default is 60 s.
`jt9.f90` auto-replaces 60 with 15 s (FT8) or 7.5 s (FT4) only in
`--stream` mode, not when reading WAV files. On the WAV path,
`npts=TRperiod*12000` and `params%ntr=int(TRperiod)`, so **pass
`-p 15` for FT8 and `-p 7.5` for FT4** rather than relying on the
default. Independently, `--ft8` still stops the read loop at 179712
samples (~15.0 s at 12 kHz). `--ft4` then overwrites `npts` to
`21*3456` (72576 samples = 6.048 s at 12 kHz), which is the FT4
decode window; a 7.5 s WAV is therefore truncated to 6.048 s of
samples, matching this jt9. Still pass `-p 7.5` so `ntr` is correct.

Optional later: `-M` / `--multithreadft8` and its `-C/-U/-R/-N/-E/-D/-Z`
controls. First cut can stay single-threaded.

### Stdout format (from `lib/decoder_callbacks.f90` in the same tree)

Each successful decode is one line (legacy renderer; used when not in
jt9 stream mode):

- FT8 (format `1001`):
  `i6.6,i4,f5.1,i5,' ~ ',1x,a37,1x,a2`
  → `HHMMSS`, SNR (dB, `i4`), DT (s, `f5.1`), audio frequency (Hz, `i5`),
  literal ` ~ `, the 37-character message, and an optional AP annotation
  (`aN`, or blank). If AP quality is poor (`qual<0.17`), the message's
  last character may be `?`.
- FT4 (format `1001`): the same layout with literal ` + ` instead of
  ` ~ `.

Example shape (spacing from the format; message padded to 37 chars):

```
122100 -15  0.2 1363 ~  CQ WA1HCO FN42
122100 -11  0.4 2105 ~  WA1HCO K1ABC FN42
```

**Multi-decode:** one line per message in the period. After decoding,
`lib/decode_completion.f90` writes a terminator (unless `-q`):

```
<DecodeFinished> nsynced ndecoded navg0
```

(`format('<DecodeFinished>',2i4,i9)`, optionally with ` gen=N`). The
parser must:

- accept every `~` (FT8) or `+` (FT4) decode line, not only the first;
- ignore `<DecodeFinished>...`, any line starting with `<`, and
  `cannot read WAV file` / `EOF` diagnostics;
- dedupe messages (FT8 from disk can still run early/late passes; the
  wrapper should dedupe even if in-source `ldupe` already suppresses
  most repeats).

Today's MSK144 parser already skips `<` and `EOF` prefixes but **stops
at the first remaining line**, which is not sufficient for FT8/FT4.

## Suggested implementation order

1. ~~`LICENSE` + WSJT-X attribution headers (prerequisite PR).~~ Done.
2. Tag the current MSK144 baseline (no code change).
3. ~~Add CI and keep it green (prerequisite PR).~~ Done; keep it green.
4. Mode abstraction on a branch, MSK144 default, tests still passing.
5. Parametrize `reporting.py` and `TimeBase` period.
6. Period-aligned 12 kHz WAV writer, with tests.
7. FT8 path: Costas search, ~3 kHz sections, jt9 `--ft8 -p 15` with
   `-L`/`-H`, multi-line parse, UDP Decode/Status with dial = section
   base, receive-only instance id.
8. Cross-check with `decode_ft8`.
9. FT4 path (`-p 7.5`).
10. GUI mode/settings and launcher-only UX (user-facing README
    CLI/env instructions already moved to `docs/dev/`).

## Open questions

- Tag name for the MSK144 baseline (and whether to keep `v0.1.3-alpha`
  as a prerelease only).
- Primary decoder for v1: jt9, `decode_ft8`, or jt9 with `decode_ft8`
  as a comparison only.
- Instance-id scheme (`SKIM-B210-2m` / `SKIM-FLEX-2m` / other).
- Whether the Flex path stays MAP144 `flexclient/` or later adopts the
  ka9q-radio `flex.c` approach described in
  [docs/flex-daxiq-interface.md](../flex-daxiq-interface.md) (that note
  is copied as-is from the placeholder; it predates the rename-and-generalize
  decision and still talks about a new `flex.c`).
- Whether to pin the skimmer to the installed 3.3.0-beta4 jt9 or move
  to the already-checked-out 3.3.0-beta5 tree before feature work.
- Whether the first FT8 cut should use `-M` (multithread FT8).
