# Flex DAX IQ interface for ka9q-radio (design note)

Status: draft for review. Nothing here is implemented yet.
Related: [WIMS wideband skimmer brief](https://github.com/wa1hco/WIMS/blob/docs/wideband-skimmer/docs/plan/wims_wideband_skimmer.md)

## Purpose

Get wideband IQ from the FLEX-8600 into ka9q-radio on Linux, with no Windows PC
and no SmartSDR DAX app, so WS-Skimmer can decode FT8/FT4 across the window and
send spots to WIMS. The driver is a new ka9q-radio front end, `flex.c`, written
so it can be offered upstream (GPL-3.0).

## How it fits ka9q-radio

Verified in [ka9q-radio `src/`](https://github.com/ka9q/ka9q-radio/tree/main/src):

- `radiod` loads a hardware driver as a shared library named by `device=` in
  the hardware section of its config, then looks up `<device>_setup`,
  `<device>_startup`, `<device>_tune`, and the optional `_shutdown`, `_gain`
  and `_atten` symbols ([radio.c](https://github.com/ka9q/ka9q-radio/blob/main/src/radio.c)).
  So `flex.so` would export `flex_setup`, `flex_startup` and `flex_tune`.
- The driver fills `struct frontend` ([radio.h](https://github.com/ka9q/ka9q-radio/blob/main/src/radio.h)):
  `samprate`, `frequency`, `isreal=false`, `min_IF`/`max_IF`, `bitspersample`,
  `description`, and the `lock` flag, which stops clients from retuning it.
- Its receive thread writes complex floats to `frontend->in.input_write_pointer.c`,
  adds to `frontend->samples`, and calls `write_cfilter(&frontend->in, NULL, n)`,
  the same way [soapy.c](https://github.com/ka9q/ka9q-radio/blob/main/src/soapy.c)
  does (around lines 560–625). DAX IQ is already complex float32, so start with
  the same little-endian ±32768 scale MAP144 already uses on the 8600, not a
  format conversion.
- `tune()` returns the frequency actually set. For the Flex, that has to be
  the panadapter center the radio *reports*, not the value requested (see below).

## Flex API facts (verified from public docs)

- Control: TCP port 4992, text commands `C<seq>|<cmd>`, replies `R<seq>|<hex>|...`,
  status `S...`. Discovery is a VITA-49 broadcast on UDP 4992.
- DAX IQ channel setup, from the [`dax` API page](https://github.com/flexradio/smartsdr-api-docs/wiki/TCPIP-dax):
  `dax iq set <channel> [pan=<pan id>] [rate=<24|48|96|192>]`.
- Stream setup, from the [`stream` API page](https://github.com/flexradio/smartsdr-api-docs/wiki/TCPIP-stream):
  `stream create type=dax_iq daxiq_channel=<n>`, `stream set <id> daxiq_rate=<24|48|96|192>`,
  `stream remove <id>`.
- Panadapter control, from the [`display pan` API page](https://github.com/flexradio/smartsdr-api-docs/wiki/TCPIP-display-pan):
  `display pan c freq=<MHz>` creates one; `display pan s <pan id> center=<MHz>
  bandwidth=<MHz> autocenter=<0|1> daxiq_channel=<1-4> ...` changes it.
- VITA-49 IQ packets: IF Data with stream ID (type 1), FlexRadio OUI 0x001C2D,
  packet class 0x02E3/0x02E4/0x02E5/0x02E6 = 24/48/96/192 kHz, interleaved
  float32 I/Q. Source: FlexLib's `DAXIQStream.cs` as summarized in
  [AetherSDR issue #124](https://github.com/aethersdr/AetherSDR/issues/124)
  (I have not read FlexLib itself).
- The SmartSDR user guide says each DAX IQ channel can be on only one
  panadapter, and the radio has limited DAX IQ resources, so higher rates use
  more of them ([SmartSDR user guide v3.1.7, §35.1.7](https://edge.flexradio.com/www/uploads/20200818184951/SmartSDR-Software-User-Guide.pdf)).

## The key question: can the DAX IQ center be set directly?

**No. As of SmartSDR v4.3.16 (Oct 13, 2026) the DAX IQ center is still the center
of the panadapter the channel is assigned to.** There is no DAX IQ `center=`
parameter: the `dax iq set` and `stream` commands above take only channel, pan
and rate. Evidence:

- FlexRadio's own description from when DAX IQ was introduced: "the center of the
  panadapter will be the center of the IQ data … If you tune the panadapter, you
  will tune the DAXIQ channel also" ([community](https://community.flexradio.com/discussion/5883745/dax-control)).
- 2022, Flex 6400 feeding MAP65: the IQ center jumped when the slice was tuned near
  the pan edge. The workarounds offered were a dedicated panadapter with no slice,
  or `display pan s <pan> center=<MHz>` ([community](https://community.flexradio.com/discussion/8025962/how-to-set-daxiq-center-frequency)).
- Jan 2026, S53K: "the IQ stream center frequency is rigidly bound to the
  panorama center", with a request to decouple them ([community](https://community.flexradio.com/discussion/8033129/smartsdr-panorama-iq-center-coupling-issue)).
  I saw no reply saying it was changed.
- The [SmartSDR v4 changelog PDF](https://edge.flexradio.com/www/offload/20261012100241/SmartSDR-v4-Changelog.pdf)
  (v4.0.1 to v4.3.16) has DAX IQ entries, but none adds center control:
  SMART-11735 (IQ stream not ending after client disconnect), SMART-11918 (byte
  order of the *older* `stream create` command fixed, v4.0.1), SMART-12138 (MAC
  resolution for non-local IP), SMART-12367 (SmartLink restriction messages), and
  SMART-12224. v4.2.18's DAXv2 is a Windows driver rewrite and doesn't touch this.

**So the driver controls the center by owning the panadapter.** That means one
dedicated panadapter per DAX IQ channel, with no slice on it and `autocenter=0`,
moved with `display pan s <pan> center=<MHz>`.

## What MAP144 did (from the repo, not memory)

From [`flexclient/setup.py`](https://github.com/wa1hco/WS-Skim/blob/main/flexclient/setup.py)
and the [engine](https://github.com/wa1hco/WS-Skim/blob/main/ws_skim/engine.py):

- MAP144 never sets the IQ center. It treats SmartSDR as the owner of the
  panadapter ("GUI owns pan tuning", [flexradio-setup.md](flexradio-setup.md)),
  tries `dax iq set <ch> pan=<id>` and logs a warning if the radio rejects it,
  and only retunes when a slice is attached to the stream.
- It tracks the real center from `display pan ... center=` and `slice ...
  RF_frequency=` status lines. When the center moves (including when the radio
  reports a slightly different value than requested), it rebuilds the channelizer
  NCO with `offset = (calling_freq − pan_center) + 1500 Hz`
  ([displays.py](https://github.com/wa1hco/WS-Skim/blob/main/ws_skim/displays.py),
  `_rebuild_channelizer_state` in engine.py).
- A separate fix, v0.1.2-alpha ([CHANGELOG.md](https://github.com/wa1hco/WS-Skim/blob/main/CHANGELOG.md)):
  another client's DAX IQ stream broadcast silently hijacked MAP144's stream
  filter. MAP144 now remembers its own stream ID and tells streams apart by slice.
- MAP144 uses the older `stream create daxiq=<n> ip= port=` form and decodes the
  payload as little-endian float32 at ±32768 scale
  ([vita.py](https://github.com/wa1hco/WS-Skim/blob/main/flexclient/vita.py)).
  That format is proven working on the author's FLEX-8600 (user-confirmed,
  November 2026). The new driver should start by reusing exactly that format.
  FlexLib and the v4.0.1 note SMART-11918 describe big-endian samples scaled by
  1/2^15, so other firmware or the newer `type=dax_iq` stream might differ; a
  cheap sanity check (signal level or a known tone) is enough if something looks
  off. This is low priority.

I found no write-up in the repo of a specific "can't set the DAX IQ center" bug.
If the workaround you remember was something else, a sentence describing it would
let this note record it.

## Proposed flow for `flex.c`

1. **Find and connect.** Listen for the discovery broadcast (or use a saved IP),
   open TCP 4992, `client bind` to the station's GUI client, and subscribe to
   `slice`, `pan` and `stream` status.
2. **Own a panadapter.** `display pan c freq=<f>`, then `display pan s <pan>
   autocenter=0 daxiq_channel=<n>` and `dax iq set <n> pan=<pan> rate=48`.
3. **Stream.** `stream create type=dax_iq daxiq_channel=<n>`, keep the returned ID,
   and filter VITA packets on it. Accept only our own stream, as MAP144 learned.
4. **Receive.** A UDP thread parses the VITA header (sequence gaps, timestamps,
   class code gives rate), applies MAP144's little-endian ±32768 scale, and
   calls `write_cfilter`.
5. **Follow WSJT-X.** Watch the slice WSJT-X uses (the TX slice, or one picked in
   the GUI). When its dial plus the FT8/FT4 audio passband leaves the window minus
   a guard band, send `display pan s <pan> center=<new>`, and set
   `frontend->frequency` only when the status reports the new center. Mark the
   samples around the retune so the decoder drops that period.
6. **Clean up.** `stream remove`, close the panadapter, and handle a lost TCP
   connection by retrying.

`frontend->lock = true` while following WSJT-X, so ka9q clients can't fight
over the center.

## Zero-knowledge setup

No required flags or environment variables. The driver finds the radio itself,
picks a free DAX IQ channel, and saves what it learned in a small state file. A
`radiod` config section with only `device=flex` should work. A GUI and launcher
come later in WS-Skimmer.

## Reusable open source (study, don't copy blindly)

- MAP144 `flexclient/` (yours, GPL-3.0): a working Python reference for the whole
  sequence.
- [AetherSDR](https://github.com/aethersdr/AetherSDR) (GPL-3.0, Qt/C++, active):
  a native Linux Flex client with DAX IQ (`src/models/DaxIqModel.cpp`,
  `src/core/backends/flex/PanadapterStream.cpp`). This is the best C++ cross-check.
- [kc2g-flex-tools/flexclient](https://github.com/kc2g-flex-tools/flexclient) and
  [nDAX](https://github.com/kc2g-flex-tools/nDAX) (MIT, Go): API client and DAX
  audio bridge. MIT code can be reused in GPL work.
- FlexLib (C#) is FlexRadio's SDK under its own license, so read it for behavior
  only and don't copy it into a GPL driver. Not verified: check its license text.
- I found no SoapySDR Flex module or gr-flex.

## Open questions to test on the 8600

1. Can a non-GUI, bound client create and own a panadapter, and does
   `autocenter=0` keep it still when WSJT-X tunes the slice?
2. How long a `center=` change takes, and how many samples are lost or glitched.
3. Is there a VITA context packet carrying the RF center, or is TCP status the
   only source?
4. 8600 limits: how many DAX IQ channels at 48/96/192 kHz alongside WSJT-X's DAX
   audio, and how many panadapters a client can open.
5. Does the extra panadapter show up in SmartSDR and confuse the operator?

Low priority: other firmware or the newer `type=dax_iq` stream might differ
from MAP144's proven little-endian ±32768 format. A cheap signal-level or
known-tone check is enough if something looks off; start with what MAP144
already does.

## First milestone

Receive 48 kHz DAX IQ from the 8600 on Linux, decode VITA, write 15 s of complex
float WAV/raw aligned to UTC, and confirm a known FT8 signal lands at the expected
offset with the decoder (`decode_ft8`). Then load it into `radiod` as `flex.so`,
create one FT8 channel, and get decodes. Moving the panadapter comes after that.
