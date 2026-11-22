# WS Skim on the Flex: own panadapter, own slice, Skim WSJT-X

Status: Stage 1 built (flexclient/owned_pan.py, ws_skim/flex_pan.py; option in View → Flex Radio, default off).  Stages 2 and 3 are plans.
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

## Open questions

- Is a parked slice the intended way to keep a client-created pan steerable?
  (asked on the SmartSDR API forum)
- WSJT-X settings key names across versions; which versions to support.
- Windows: SmartSDR CAT / DAX instead of nCAT / nDAX.
