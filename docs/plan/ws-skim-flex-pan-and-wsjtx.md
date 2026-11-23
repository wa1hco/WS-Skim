# WS Skim on the Flex: own panadapter, own slice, Skim WSJT-X

Status: Stage 1 built (flexclient/owned_pan.py, ws_skim/flex_pan.py; option in View → Flex Radio, default off).  Stage 2 checks done Nov 23 (below).  Stage 3 built (ws_skim/skim_wsjtx.py, skim_link.py, click_to_work.py; Set up WSJT-X button; roster double-click), not yet run end to end on the air.
Live check on the 8600, Nov 22: own pan 0x40000001 + muted slice, DAX IQ ch 2, 96,000 samples/s (48,004 after switching to 48 kHz), centre 7.06235 confirmed by the radio, 52 FT8 and 6 FT4 decodes from one 15 s capture, dragged pan steered back, 20 m and back, crash leftover removed at the next start, radio left with only the operator's pan and stream.

## The scenario

The operator is working SSB or CW on the FLEX-8600 (or WSJT-X on another
band).  WS Skim is running on its own panadapter.  The operator clicks a
decode in the Roster, and a WSJT-X copy dedicated to WS Skim works that
contact on WS Skim's slice while SSB/CW mostly continues.

The radio has one transmitter.  While the Skim WSJT-X sends a period, the
SSB/CW slice can only listen, so an FT8 QSO takes every other 15 s period
until it ends.  The operator keying on SSB/CW always wins.

## What the 8600 does (tested Nov 22, 2026, SmartSDR v4.3.19, non-GUI TCP client)

- `display pan c freq=<MHz>` creates a panadapter.  Reply `R|0|<pan>,<waterfall>`.
  The radio assigns it to the GUI client (front panel SmartSDR-M), so it
  shows there, and it opens a new slice with the last-used mode/antenna.
- `display pan set <pan> center=<MHz>` moves the center **only while the pan
  has a slice**.  With no slice the command returns OK and is ignored.
- `autocenter=0` / `auto_center=0` are rejected.
- `dax iq set <ch> pan=<pan>`, then `stream create daxiq=<ch> ip=<pc> port=<udp>`.
- `stream set <stream> daxiq_rate=96000|192000` works (measured 96,044 and
  192,044 samples/s).  `dax iq set <ch> rate=` is rejected.
- `daxiq_capacity=16`: 48 kHz uses 2, 96 kHz 4, 192 kHz 8.
- Our own `display pan set` / `slice tune` produce NO status line; the reply
  carries the applied centre and `sub pan all` re-sends the full status (how
  the centre is checked).  Right after `display pan c` the radio moves the
  centre to where the new slice lands, so set it again.
- The pan and its slice survive our TCP connection closing; the stream does
  not.  Ids are kept in flex_owned_pan.json and a verified leftover is removed
  at the next start.
- Packet class code 0x02E4 = 48 kHz, 0x02E5 = 96 kHz (measured).
- If the new slice is the only slice the radio makes it the TX slice (tx=1).
- Earlier "replies stop after a rate change" was the test script: its socket
  had a 5 s timeout and its reader exited on the first quiet 5 s.
- Status reports `payload_endian=little`.  VITA TSI is typed UTC but always
  0; TSF is picoseconds within the second.
- `display pan r <pan>` and `stream remove <stream>` clean up.

## Stage 1: WS Skim owns its panadapter and a parked slice

- Option in the Flex window: "WS Skim makes its own panadapter" (default on
  for new installs once proven).
- Create the pan on the chosen band, keep its slice, mute it, park it at the
  pan center.  Only ever change or remove objects whose ids came back from
  our own create commands; abort if the radio snapshot is empty.
- Steer the center to the band plan (FT8 and FT4 both in view; DC kept
  away from the sub-bands), re-steer if anything moves it.
- DAX IQ at 96 kHz (192 kHz when wanted and capacity allows); MSK144 stays
  48 kHz as on the SDRplay source.
- Remove the pan and stream on exit; recover cleanly after a restart.
- Result: FT8 + FT4 together on the Flex, no dependence on the GUI pan.

## Stage 2: tests on the 8600 before any transmit work

1. Do our pan and slice survive WS Skim exiting, crashing, restarting?
   Can SmartSDR close them?  What does the radio do when we reconnect?
2. Can WSJT-X rig control on Linux (Hamlib Flex 6xxx, nCAT) be pinned to a
   specific slice letter, not just the active slice?
3. Transmit handoff: making the Skim slice the TX slice for its periods and
   giving TX back to the SSB/CW slice afterwards; what happens when the
   operator keys SSB/CW mid-QSO (interlock state, `sub tx all`).
4. If WSJT-X tunes the slice near the pan edge, does the pan re-center?
5. DAX audio channel for the slice (nDAX) alongside the existing WSJT-X copies.

## Stage 3: "Set up WSJT-X" and click to work

- A **Set up WSJT-X** button writes a separate named WSJT-X setup (for
  example "Skim 40m"), never touching the user's existing WSJT-X configs,
  and creates a desktop launcher for it.  It writes the settings only while
  that WSJT-X is not running, because WSJT-X rewrites them on exit.
- The setup points WSJT-X at the Skim slice (rig control) and its DAX audio
  channel, and at WS Skim's UDP for control.
- Click a Roster decode: WS Skim tunes its slice to the station and tells the
  Skim WSJT-X to call it (WSJT-X UDP Reply).  WSJT-X finishes and logs the
  QSO; WS Skim shows its TX state in the status bar.
- TX inhibit rules match the user's other WSJT-X copies; operator keying wins.

## Stage 2 findings (FLEX-8600M, SmartSDR v4.3.19, Nov 23, 2026)

Read-mostly from desktop-jeff.  No tx set, no keying; only a test pan this
check created was ever changed or removed.

1. **Slice pinning (Linux rig control).**  Two routes exist:
   - **nCAT** (kc2g-flex-tools, already running here): `-slice <letter>`
     binds to the GUI client's slice with that `index_letter` once, at
     start-up, and keeps its slice index.  Live: nCAT :4532 reads 7.255000
     LSB (slice A, the operator's), nCAT :4533 reads 24.911000 LSB = WS
     Skim's own slice (index 1, letter B, pan 0x40000001).  So WSJT-X with
     "Hamlib NET rigctl" to localhost:4533 controls WS Skim's slice and
     nothing else.
   - **Hamlib "SmartSDR Slice A..H"** (models 23005-23012, Hamlib 4.6.5
     and 4.7.2 here) talk straight to the radio on TCP 4992, slice chosen by
     NUMBER (A=0..H=7), tune with `autopan=1`, PTT = `slice set <n> tx=1`
     then `xmit`.  Not used (nCAT is already set up and binds by letter).
   - SmartSDR CAT is Windows only.
2. **DAX audio for the slice.**  nDAX `-slice B -daxch 2` sends
   `slice set 1 dax=2` and `dax audio set 2 slice=1` once at start and
   creates the PipeWire source `flex.sliceB.rx` (s16be mono 24 kHz).  Live:
   slice 1 `dax=2 dax_clients=1`, stream `dax_rx dax_channel=2 slice=B`,
   PipeWire source flex.sliceB.rx RUNNING.  TX audio: one shared sink
   `flex.tx` from the slice-A nDAX; the radio sends it from whichever slice
   transmits.  A test slice could not be used for `slice set <n> dax=` (see
   4), so WS Skim only re-applies the channel nDAX already uses, on its own
   slice, and only while that slice has the letter the setup was made for.
3. **Transmit handoff (from code and API reading only, nothing keyed).**
   nCAT `set_ptt`: if its slice is not the TX slice it sends
   `slice set <n> tx=1`, then `xmit 1`; on release only `xmit 0`.  Hamlib
   smartsdr does the same.  SmartSDR has one TX slice; making one TX clears
   the others.  Nothing hands TX back, so after WSJT-X for WS Skim sends,
   the operator's mic/CW key would transmit on WS Skim's slice until TX is
   clicked on the operator's slice.  `transmit dax=1` is set (by nDAX A).
   Jeff's WSJT-X 3.3.0-beta8-inhibit supports UDP TX Inhibit (types 17/18)
   but it filters only RTS/DTR PTT, not CAT PTT, so it can't enforce
   "operator keying wins" with nCAT.  **Needs Jeff's decision** (see
   "Decisions for Jeff").
4. **Survival / restart.**  From Stage 1: pan + slice survive our TCP
   closing, WS Skim removes them on exit and at the next start after a
   crash.  The radio gives a new slice the lowest free index (B when only
   the operator's A exists), so a restarted WS Skim gets B again and nCAT /
   nDAX (bound by index at their start) keep working; WS Skim re-applies
   `dax=` on its slice.  If the letter changes (operator opened another
   slice first), Set up WSJT-X must be pressed again and the helpers
   started for the new letter; click-to-work refuses and says so.
   New: **a third panadapter is closed at once by the front panel.**  With
   the operator's pan and WS Skim's pan open, `display pan c` returned
   0x40000002 with slice C, and within 70 ms client 0x6BBA9569
   (SmartSDR-M) sent `slice 2 in_use=0` and `display pan 0x40000002
   removed`.  Band persistence also moved DAX IQ channel 1 onto the new
   pan for that moment, which left the operator's pan 0x40000000 with
   `daxiq_channel=0` (nothing was streaming on channel 1; WS Skim's
   follow-mode sets it again when used).  Stage 1's create path could see
   the same band-persistence channel move; guarded in test.27 (below).
5. **Pan follows the slice.**  Not measurable with a test slice (see 4).
   Hamlib's `autopan=1` and Stage 1 ("a slice near the edge would drag the
   pan back") say the pan follows a slice tuned off-screen; WS Skim keeps
   click-to-work dials within 40 kHz of its pan centre and lets go of a
   held dial on a band change.

## Stage 3 as built

- **Set up WSJT-X** (View → Flex Radio): finds nCAT/nDAX for WS Skim's
  slice letter in /proc, copies the operator's Flex WSJT-X settings
  (`WSJT-X - flex.ini`, read only) into `WSJT-X - Skim.ini` (rig name
  `Skim`, UDP id `WSJT-X - Skim`): Hamlib NET rigctl localhost:<nCAT
  port>, PTT CAT, Data mode, audio in = nDAX source, out = TX sink, UDP =
  the template's multicast group (224.0.0.73:2237, same interfaces/TTL),
  Accept UDP requests on, band = WS Skim's band FT8 dial.  Refuses while
  that WSJT-X runs.  Writes menu + desktop launchers "WSJT-X for WS Skim"
  and links the shared wsjtx_log.adi when the template's log is a link.
- **UDP design:** WS Skim joins 224.0.0.73:2237 bound to the group
  address (so it never takes unicast meant for WS Hub on 127.0.0.1:2237),
  keeps Status/Decodes from id `WSJT-X - Skim` only, and sends Reply /
  Configure from a separate socket to the port WSJT-X sends from.  WS Hub
  and GridTracker see that WSJT-X like any other.
- **Click-to-work:** double-click a roster row (FT8/FT4) → WS Skim tunes
  ITS slice to the standard dial holding the station (kept through
  re-steers), sends Configure if WSJT-X is in the other mode, and when
  WSJT-X decodes the station (within 150 s; decodes up to 30 s old
  count) echoes that decode as Reply (type 4).  WSJT-X 3.3 accepts a
  Reply only if it matches one of its own decodes exactly.
- WS Skim never sets tx or keys; it shows "Transmit is on WS Skim's slice
  B. Click TX on your own slice before you talk or send CW." while that
  is so.

## TX hand-back and DAX IQ guard (test.27)

- **Hand-back.**  RadioState tracks the slice that last had `tx=1`; when
  tx moves to a slice it remembers where it came from (`tx_from`), and
  when the interlock goes PTT_REQUESTED / TRANSMITTING it marks the slices
  holding tx as having sent (`keyed`).  The owned-pan loop (woken on every
  interlock change) calls `OwnedPan.handback_tx()`: only when our slice
  holds tx, has sent since tx came to it, and the interlock is back to
  READY/RECEIVE, it sends exactly `slice set <earlier> tx=1` — only if
  that slice still exists and is not ours.  Otherwise it warns.  No `xmit`
  ever.  If the radio makes our slice tx without a transmission (operator
  clicked it, or ours is the only slice), nothing is sent.
- **DAX IQ guard.**  Safe design = detect and report, never restore.
  `create()` snapshots every pan's `daxiq_channel` before `display pan c`;
  if the new pan got channel c and a pan that is not ours lost c,
  `displaced_dax` is set, a warning is logged and View → Flex Radio shows
  it.  Restoring would mean `dax iq set c pan=<their pan>` — a change to a
  pan we do not own, and on the radio the channel can only be on one pan,
  so it could also fight the operator's DAX program.  `start_stream()`
  always picks its own free channel (2–4), so the moved channel is
  released from our pan; the operator's own program (SmartSDR DAX, WS Skim
  follow mode) sets it again when it is used.
- Not built (by decision): halting WSJT-X for WS Skim when the operator
  keys.

## Decisions for Jeff

- TX hand-back: **decided Nov 23 — hand TX back** (Jeff: "Have WS Skim
  hand TX back to my slice after each Skim transmission").  Built in
  test.27, see below.
- Operator keying mid-period: no automatic stop with CAT PTT.  Options:
  WS Skim sends Halt Tx (UDP type 8) when the interlock shows a transmit
  from another slice, or switch that WSJT-X to RTS/DTR PTT so TX Inhibit
  works.

## Open questions

- Is a parked slice the intended way to keep a client-created pan steerable?
  (asked on the SmartSDR API forum)
- WSJT-X settings key names across versions; which versions to support.
- Windows: SmartSDR CAT / DAX instead of nCAT / nDAX.
