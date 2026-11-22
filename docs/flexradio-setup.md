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

Note: if WS Skim's slice is the only slice on the radio, SmartSDR makes it
the transmit slice. WS Skim never transmits, but open a slice of your own
before you transmit.

With the box unticked WS Skim follows your SmartSDR panadapter as before,
at 48 kHz. Changing the box reconnects to the radio.

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
