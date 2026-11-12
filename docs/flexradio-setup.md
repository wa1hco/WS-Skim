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
