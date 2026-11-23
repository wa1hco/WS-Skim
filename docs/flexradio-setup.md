# FlexRadio setup

WS Skim receives IQ from a FlexRadio 6000/8000-series radio over DAX IQ.

## Before you start

1. Power on the radio and make sure it is on the same network as the
   computer running WS Skim.
2. In SmartSDR, set the panadapter center and bandwidth you want.
   SmartSDR owns pan tuning; WS Skim follows it.
3. Choose the FlexRadio source in WS Skim's **Source** menu. WS Skim
   tries to assign DAX IQ channel 1 to the panadapter itself. If SmartSDR
   is controlling that panadapter and the assignment is refused, enable
   it by hand: right-click slice A and enable DAX IQ channel 1.

## WS Skim's own panadapter (FT8 and FT4 at 96 kHz)

Open **View → Flex Radio** and tick **WS Skim makes its own panadapter**.
WS Skim then:

- opens a panadapter of its own on the radio. It shows in SmartSDR and on
  the front panel, with a slice that WS Skim mutes and parks at the pan
  centre. Leave that pan and slice alone; WS Skim removes both when it
  stops (or the next time it starts, if it was closed by a crash or power
  cut);
- keeps that pan on the band picked in WS Skim's Band pulldown, with the
  FT8 and FT4 frequencies inside it (40 m: FT8 7.074 and FT4 7.0475) and
  the pan centre between them. If you drag the pan somewhere else, WS Skim
  moves it back within a few seconds;
- uses a free DAX IQ channel (2, 3 or 4) for it and receives 96 kHz,
  or 48 kHz while MSK144 is on.

Your own panadapters, slices, DAX channels and transmit settings are not
changed. Your other programs on DAX IQ channel 1 keep running.

Band memory: when a new panadapter opens, the radio can move a DAX IQ
channel onto it from one of your panadapters (seen with channel 1). WS
Skim never changes your panadapters, so it does not put the channel back
itself; it takes its own free channel, and View → Flex Radio tells you
which panadapter lost which channel. If you use that channel, set it
again in SmartSDR (WS Skim in follow mode sets it by itself).

Note: if WS Skim's slice is the only slice on the radio, SmartSDR makes it
the transmit slice. WS Skim never transmits, but open a slice of your own
before you transmit.

With the box unticked WS Skim follows your SmartSDR panadapter as before,
at 48 kHz. Changing the box reconnects to the radio.

Note: the radio's front panel (SmartSDR-M) shows at most two
panadapters. With WS Skim's pan open, a third pan opened from a program
is closed by the front panel right away, so keep to one pan of your own.

## Work a station from the roster (WSJT-X for WS Skim)

WS Skim can hand a station you see in its roster to a WSJT-X of its own,
"WSJT-X for WS Skim", which works it on WS Skim's slice while you carry
on with SSB, CW or your usual WSJT-X on your own slice. Linux only for
now. It needs:

- **WS Skim makes its own panadapter** ticked (above);
- the nCAT and nDAX helpers running for WS Skim's slice letter (usually
  B), the same way you run them for your own slices.

Then:

1. In **View → Flex Radio**, press **Set up WSJT-X**. WS Skim finds the
   helpers for its slice, copies your Flex WSJT-X settings (call, grid,
   colours, log) into a separate copy, and points that copy at WS Skim's
   slice, its DAX audio and the same network group as your other WSJT-X.
   It also adds a **WSJT-X for WS Skim** launcher to your menu and
   desktop. Your own WSJT-X settings are not changed. Close WSJT-X for
   WS Skim before pressing the button again; WS Skim won't change its
   settings while it runs.
2. Start **WSJT-X for WS Skim** from its launcher.
3. In the roster, double-click a station (FT8 or FT4). WS Skim tunes its
   slice to that station's frequency and, when WSJT-X for WS Skim decodes
   the station (usually the next period), tells it to call, just as if
   you had double-clicked the station in WSJT-X. WSJT-X for WS Skim then
   runs the contact and logs it.

The line under the button (and above the roster) shows what WSJT-X for
WS Skim is doing.

Transmitting: the radio has one transmitter and one transmit slice.
When WSJT-X for WS Skim transmits, the transmit slice moves to WS Skim's
slice. As soon as that transmission ends, WS Skim gives transmit back to
the slice that had it before (normally your slice A), so your microphone,
key and your own WSJT-X work as usual between WS Skim's periods. That is
the only time WS Skim moves transmit, and WS Skim itself never keys the
radio. If the slice that had transmit is gone by then, transmit stays on
WS Skim's slice and the roster shows a warning: click TX on your own
slice before you talk or send CW.

Do not key your own radio while WSJT-X for WS Skim is sending: it does
not stop by itself. Untick Enable Tx (or press Halt Tx) in WSJT-X for WS
Skim first.

Change bands with WS Skim's Band pulldown, not in WSJT-X for WS Skim:
WS Skim keeps its pan, and the slice with it, on the band it is
decoding.

## How it connects

- **Discovery:** finds the radio with a UDP broadcast.
- **Control:** TCP connection to the radio on port 4992.
- **Data:** IQ arrives over UDP on a local port chosen at startup.

## If no data appears

1. Check that DAX IQ channel 1 is enabled on slice A in SmartSDR.
2. Check that the radio and the computer are on the same network.
3. Check that a firewall is not blocking incoming UDP to the local
   listen port.

When the radio accepts the client bind but rejects a control command,
it usually means the radio's ownership or policy rules do not allow
that operation in the current firmware or context.

Technical background on the Flex API is in
[flex-daxiq-interface.md](flex-daxiq-interface.md).
