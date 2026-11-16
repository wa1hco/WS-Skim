# Plan: Bidirectional real-time decode stream

Status: plan. Documentation only; the feature work has not started.
Repository: `wa1hco/WS-Skim` (default branch `main`).

Goal: each WS Skim both publishes the decodes it makes and can subscribe
to other skimmers' streams. Remote decodes come back out as local
WSJT-X UDP, so WS Hub sees a remote skimmer as just another
receive-only instance.

A WSJT-X decode is about 100 bytes, so a stream of decodes uses far
less bandwidth than audio (a busy band is only a few kilobytes per
period) and can be closer to real time than PSK Reporter's batched
spots.

Related: local UDP export in `ws_skim/reporting.py` and
`ws_skim/wsjtx_udp.py`; [docs/plan/ft8-ft4-wims-udp.md](ft8-ft4-wims-udp.md);
[docs/plan/installer-and-release.md](installer-and-release.md).

## Current code (facts to build on)

WS Skim already emits WSJT-X-faithful UDP to the WS Hub multicast
group `224.0.0.73:2237` by default:

- One receive-only instance per (source, band, mode, dial), with id
  `SKIM-{SOURCE}-{BAND}-{MODE}` when the dial is the standard one for
  that band and mode, otherwise the dial in kHz is appended
  (`SKIM-FLEX-6m-FT8-50323`).
- Heartbeat (type 0) every 15 s, Status (type 1) on change and with
  each heartbeat, Decode (type 2) with the WSJT-X mode character
  (`~` FT8, `+` FT4, `&` MSK144, …) and `delta_frequency = RF − dial`.
- Clear (type 3) and Close (type 6) on shutdown.
- The wire format is Qt QDataStream as in WSJT-X `NetworkMessage.hpp`
  (magic `0xADBCCBDA`, schema 3). Times on that wire are milliseconds
  since midnight UTC, not a full date.

WSJT-X UDP is a local protocol: multicast, with no authentication and
no encryption. It must not be sent raw across the internet. The stream
wraps the same fields in a secure transport, and the subscriber turns
them back into local UDP for WS Hub.

This stream does not replace today's PSK Reporter uploads or the local
UDP feed. Those stay as they are.

## Transport

| Option | Role | Notes |
| ------ | ---- | ----- |
| MQTT over TLS | Recommended stream protocol | Natural publish/subscribe model. TLS, username/password or a client key. A self-hosted broker (for example Mosquitto) for private stations, or a shared broker later. |
| WebSocket relay | Alternative | A custom server can do the same job. Useful later if a browser client or a single HTTPS port is wanted. Not required for the first cut. |
| Tailscale (or similar VPN) | Private site-to-site | The easy path between your own stations: run the MQTT broker (or the clients) on the overlay so the broker need not be on the public internet. The stream still uses MQTT; Tailscale is the network, not a second protocol. |
| PSK Reporter MQTT (`mqtt.pskreporter.info`) | Optional read-only source | A live worldwide spot feed, filterable by band, mode, and reporter. You get spots (who heard whom, SNR, frequency, time), not full decode text, and they arrive seconds to a minute late. WS Skim never publishes to this feed; existing PSK Reporter IPFIX/HTTPS uploads are unchanged. |

Recommendation: MQTT over TLS, with a private broker first. For Jeff's
own sites, put that broker on Tailscale. A public or shared broker, and
a WebSocket alternative, come later if they are needed.

Topic layout (first cut):

`ws-skim/v1/{origin_call}/{source}/{band}/{mode}`

Heartbeat and Status can use the same tree, or a sibling
`…/{origin_call}/status`. Subscribers can follow one station, one
band, or (with care) a wider prefix. The exact topic strings are
fixed in the schema work (phase 2) so publishers and subscribers
agree before any network code lands.

## Message format

JSON on the wire. The binary QDataStream format stays on the LAN, for
the UDP hop into WS Hub. JSON is easier to debug, version, and extend,
and it can carry fields that WSJT-X UDP does not: a full UTC date, the
source station's call and grid, and a unique message id.

Each message includes:

- A small schema version, so older and newer copies can coexist.
- `type`: `heartbeat`, `status`, `decode`, `clear`, or `close`.
- `origin_call` and `origin_grid` of the station that made the decode.
- `origin_source` (`FLEX`, `B210`, …), band, mode, and dial, matching
  the local instance.
- `message_id`, unique per published event, for de-duplication.
- `utc`, a full UTC timestamp (ISO 8601 with date), not only
  milliseconds since midnight.
- The corresponding WSJT-X fields, carried faithfully. For Decode
  that is: new, time, SNR, DT, delta_frequency, mode character,
  message text, low confidence, and off-air. For Status: dial, mode
  name, my call, my grid, and `tx_enabled` false. Heartbeat carries
  the WS Skim version and revision, as today.

The subscriber maps those fields back through the existing
`build_heartbeat` / `build_status` / `build_decode` / `build_clear` /
`build_close` helpers and sends ordinary UDP to the configured
destination (default `224.0.0.73:2237`).

## Remote instance naming, loops, and duplicates

Local ids are unchanged: `SKIM-{SOURCE}-{BAND}-{MODE}[-{dial_kHz}]`.

Remote ids include the originating call so they can never collide with
a local instance:

`SKIM-{CALL}-{SOURCE}-{BAND}-{MODE}[-{dial_kHz}]`

for example `SKIM-WA1HCO-FLEX-2m-FT8`. Optional PSK Reporter spots use
a distinct prefix, for example `SKIM-PSKR-{REPORTER}-{BAND}-{MODE}`,
so they are obviously not a WS Skim decoder.

Loop and echo prevention:

- Only publish decodes this copy made from its own radio (or file)
  source. Never republish traffic that arrived from a subscription.
- Every published message carries `origin_call` and `message_id`.
- Drop any incoming message whose `origin_call` is this station's
  call, even if the topic or id looks different.
- Remote UDP instances always include the `CALL` token. A subscribed
  decode is never emitted under a local-style `SKIM-{SOURCE}-…` id.
- If the publisher is already on the same LAN multicast group, do not
  re-emit that source onto that group (no hairpin).

De-duplication:

- The same `message_id` seen twice (reconnect, at-least-once delivery)
  is dropped.
- The same RF decode heard by two remote skimmers is *not* a
  duplicate. WS Hub should show both instances; that is the point of
  a network.
- A later display filter may hide a call already shown from a more
  trusted source. That is optional and is not part of the first cut.

## Latency, time, and trust

Latency target: publish within a few hundred milliseconds of a local
decode; typical end-to-end well under two seconds on a normal internet
path. That is the advantage over PSK Reporter's five-minute batches
and even its live feed.

Time: each publisher keeps its clock in sync with NTP or GPS, so
decodes from different sites line up. Messages carry a full UTC
timestamp. Residual skew should stay well under one FT8 symbol
(~0.16 s). For the first cut, a plain-language warning in the GUI if
the clock looks wrong is enough; do not silently shift other stations'
timestamps.

Authentication and sharing:

- The feature defaults to off. Private use (your own stations) is the
  first goal.
- The broker connection is always over TLS. A username and password,
  or a client key, are entered in settings.
- For site-to-site, prefer a broker that is not exposed on the public
  internet (Tailscale, a LAN, or a VPN).
- A later public or shared broker must authenticate publishers, rate
  limit them, and let subscribers trust only a list of named calls.
  Unauthenticated public publish is out of scope. Guarding against
  spam and fake spots is then a broker-and-trust problem, not
  something the UDP hop can fix.

## Contest use

Remote spots may count as outside assistance, depending on the
contest category. The GUI has a clear toggle that turns subscription
— and re-emission to local UDP — off, labeled so it is obvious that
remote spots are incoming assistance. Each remote instance id already
names the source call, so the roster shows they are not local. When
the toggle is off, subscribed traffic is not sent to WS Hub, N1MM, or
other local UDP listeners. Local decodes continue to publish only if
Publish is on.

## GUI (zero-knowledge)

Users configure this only in the app, under Reporting or a Stream
panel, and start WS Skim from the desktop launcher. No CLI flags,
environment variables, or `python -m` in user-facing docs or
launchers. Developer notes may still mention those in `docs/dev/`.

Settings, all stored in the user profile like the rest of Reporting:

- Publish on/off (default off).
- Subscribe on/off, and a list of stations (or topics) to follow.
- Broker or server address, with a sensible default when one is
  bundled or documented for private use.
- Username and password, or a key, entered in the settings panel.
- Contest / local-only toggle (subscribed spots are not sent to local
  UDP).
- Optional: subscribe to PSK Reporter's live feed (read-only).
- Connection state in the panel: connected, reconnecting, or a plain
  last-error sentence that says what to do next.

## Phased work

1. This plan (docs only).
2. JSON schema, instance naming, loop-prevention and de-duplication
   helpers, with unit tests. No network yet.
3. MQTT publish of local Heartbeat, Status, and Decode to a private
   broker (Clear and Close when an instance goes away).
4. MQTT subscribe, convert to local WSJT-X UDP with remote instance
   ids, using the existing builders in `ws_skim/wsjtx_udp.py`.
5. GUI settings and connection status (phase 3–4 can be exercised in
   tests without the GUI).
6. Contest / local-only toggle, per-source labels already in the id,
   reconnect with backoff.
7. Optional PSK Reporter read-only subscribe, adapted into
   `SKIM-PSKR-…` instances.
8. Optional shared or public broker (authenticated publishers, rate
   limits, a trust list). Tailscale remains a deployment choice, not
   a separate code path. A WebSocket relay, if ever wanted, speaks
   the same JSON.

## Test plan

- Unit tests: JSON round-trip of every field; remote vs local ids; a
  message whose `origin_call` is ours is dropped; the same
  `message_id` is dropped the second time; two origins with the same
  CQ stay two instances; a subscribed message is never marked
  publishable.
- Two processes (or a test double) on one computer, against a private
  broker: A publishes a fixture decode, B emits a UDP datagram whose
  id is `SKIM-{A's call}-…` and whose SNR, DT, delta_frequency, mode
  character, message text, and UTC match.
- Loop test: B's publisher stays silent about A's decode; A does not
  re-emit its own message as a remote instance; a hairpin onto the
  same multicast group does not happen.
- Contest toggle off: B may receive MQTT but sends no remote UDP.
- Broker down: the app stays up, shows a plain error, and reconnects
  without dropping local decoding or local UDP.
- The subscriber uses the publisher's full UTC, not its own midnight
  clock, when building the UDP Decode.
- Bandwidth smoke test: a busy FT8 period stays in the kilobytes, not
  megabytes.

## Open questions

- First private broker: a Mosquitto instance on Tailscale, versus a
  hosted MQTT service with TLS.
- How finely the subscribe list is specified in the first GUI (whole
  station, or per band and mode).
- Whether a later public feed needs callsign verification (for
  example a registration step, or LoTW).
- Whether WS Hub should also subscribe directly someday. The first
  cut only talks to WS Hub over local UDP, as it does today.
