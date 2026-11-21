# SDRplay radios (RSPduo and others) with WS Skim

WS Skim gets IQ from SDRplay radios through **SDRconnect**, SDRplay's own
program.  SDRconnect owns the radio; WS Skim asks it for the raw IQ over
SDRconnect's WebSocket server and picks out the band it needs.

## One-time setup

1. Install SDRconnect from [sdrplay.com/sdrconnect](https://www.sdrplay.com/sdrconnect/)
   and plug in the radio.
2. In WS Skim choose **Source → SDRplay (SDRconnect)**.

That is all.  If SDRconnect is not running, WS Skim starts it in the
background (no window), opens the RSPduo and starts streaming.

## Using the SDRconnect window as well

If you would rather see SDRconnect's own waterfall, start SDRconnect
yourself first:

1. Open SDRconnect, select the RSPduo (single or dual tuner) and press
   Play (▶).
2. Open the **⋯** menu at the right end of the toolbar and turn on
   **WebSocket Server** (port 5454).
3. In WS Skim choose **Source → SDRplay (SDRconnect)**.  WS Skim connects
   to the running SDRconnect and does not start another one.

While SDRconnect's window has the radio, WS Skim tuning the radio moves
what you see there too.  If WS Skim cannot reach SDRconnect, its status
says: "SDRconnect not reachable — start SDRconnect, press Play, and turn
on WebSocket Server (⋯ menu)".

## The SDRplay (SDRconnect) window

Open it from **View → SDRplay (SDRconnect)**.  Change a setting and press
**Apply and Reconnect**.

- **Start SDRconnect**: in the background (default), with its window, or
  never.  A background SDRconnect that WS Skim started is closed again
  when you switch source or quit, because it holds the radio.  An
  SDRconnect window that WS Skim opened, or one you started yourself, is
  left running.
- **RSPduo Mode**: single tuner (up to 10 MHz wide) or dual tuner (two
  tuners, 2 MHz each, about 1.5 MHz flat).  Used only when WS Skim opens
  the radio itself.
- **Tuner**: which RSPduo tuner WS Skim listens to.  Tuner 2 needs dual
  tuner mode.
- **LNA State**: RF gain reduction.  0 is the most gain; higher numbers
  add attenuation.  "Leave as set" keeps SDRconnect's setting.
- **Antenna**: RSPduo Tuner 1 has a 50 ohm port and a Hi-Z port.
- **WS Skim tunes the radio to the band**: when the band you pick in WS
  Skim is outside what the tuner is receiving, WS Skim moves the tuner to
  100 kHz below the band's frequency.  Untick it to do all tuning in
  SDRconnect yourself.

The window also shows the connection state, the device, the tuner
frequency, the sample rate, the LNA state and any data gaps.
