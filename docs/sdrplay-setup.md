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

## Two bands at once (RSPduo)

The RSPduo has two tuners, so one WS Skim can decode two bands at once,
for example 40 m and 30 m FT8, or 6 m and 2 m.

1. Choose **Source → SDRplay (SDRconnect)**.
2. In the toolbar, next to **Band (Tuner 1)**, tick **Tuner 2** and pick
   its band.

That is all.  Tuner 2 decodes the same modes as Tuner 1 (Mode menu), and
each mode remembers its own Tuner 2 band.  WS Skim picks the frequencies
inside each band, as WSJT-X does.

- **SDRconnect started by WS Skim** opens the RSPduo in dual-tuner mode by
  itself.
- **Your own SDRconnect window** must be in dual-tuner mode: choose the
  "RSPduo (…) (Dual)" entry in its device list.  WS Skim never switches
  a radio you opened; if SDRconnect is in single-tuner mode, the Tuner 2
  line on the main window says so.
- In dual-tuner mode each tuner covers about 1.4 MHz cleanly.  With "WS
  Skim tunes the radio" ticked, WS Skim moves a tuner when its band is
  outside that.
- Leave **Tuner** in the SDRplay window on Tuner 1 (A) when Tuner 2 is on.
- For FT8 and FT4 each tuner decodes 96 kHz around its band (48 kHz
  while MSK144 is on).  On 40 m that covers FT8 at 7.074 and FT4 at
  7.0475 together: tick both FT8 and FT4 in the Mode menu.
- If you tune SDRconnect yourself, keep each tuner's centre frequency
  at least 50 kHz away from the band you decode, so its DC spur stays
  out of the way.  The SDRplay window says when it is too close.

Where the two bands show up:

- **Main window**: a **Tuner 2** row with its band, modes, data rate and
  decodes per minute.
- **View → Band Activity**: a second pane for Tuner 2 under Tuner 1's, on
  the same time axis.  Drag the divider to share the space.
- **View → Roster**: one list for both bands; the band checkboxes split
  them.
- **WSJT-X UDP**: Tuner 1's decodes come from `SKIM-RSP1-…` instances and
  Tuner 2's from `SKIM-RSP2-…` (for example `SKIM-RSP1-40m-FT8-74` and
  `SKIM-RSP2-30m-FT8-136`), so WS Hub, GridTracker and JTAlert see one
  WSJT-X per band and mode.  PSKReporter and the DX cluster get both.

The FT8 Sync Map and the MSK144 diagnostic windows show Tuner 1 only.
