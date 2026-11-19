# Plan: Bidirectional real-time decode stream

Status: plan. Documentation only; the feature work has not started.
Repository: `wa1hco/WS-Skim` (default branch `main`).

Goal: each WS Skim both publishes the decodes it makes and can
subscribe to other skimmers' streams over a proper web protocol.
Remote decodes are turned back into local WSJT-X UDP only as the last
hop on the LAN, so WS Hub sees a remote skimmer as just another
receive-only instance.

A WSJT-X decode is about 100 bytes, so a stream of decodes uses far
less bandwidth than audio (a busy band is only a few kilobytes per
period) and can be much closer to real time than today's batched
spot networks. UDP, including WSJT-X UDP, is not the wide-area
protocol: it is local, unauthenticated, and unencrypted.

Related: local UDP export in `ws_skim/reporting.py` and
`ws_skim/wsjtx_udp.py`; [docs/plan/ft8-ft4-wims-udp.md](ft8-ft4-wims-udp.md);
[docs/plan/installer-and-release.md](installer-and-release.md).

## Current code (facts to build on)

WS Skim already emits WSJT-X-faithful UDP to the WS Hub multicast
group `224.0.0.73:2237` by default:

- One receive-only instance per active region (source, band, mode,
  dial), with id `SKIM-{SOURCE}-{BAND}-{MODE}-{kHz in band}`: the
  whole-kHz dial modulo 1000 (`SKIM-FLEX-40m-FT8-74` for 7074 kHz,
  `SKIM-FLEX-6m-FT8-313` for 50313 kHz). FT8/FT4 region dials are whole
  kHz. A region is announced with its first decode and closed (Clear +
  Close) after 8 periods with no decodes (2 min FT8, 1 min FT4, 2 min
  MSK144) or when it leaves the passband.
- Heartbeat (type 0) every 15 s, Status (type 1) on change and with
  each heartbeat, Decode (type 2) with the WSJT-X mode character
  (`~` FT8, `+` FT4, `&` MSK144, …) and `delta_frequency = RF − dial`.
- Clear (type 3) and Close (type 6) on shutdown.
- The wire format is Qt QDataStream as in WSJT-X `NetworkMessage.hpp`
  (magic `0xADBCCBDA`, schema 3). Times on that wire are milliseconds
  since midnight UTC, not a full date, and there is no field for the
  source station's clock quality.

That UDP feed stays as the last local hop into WS Hub, N1MM, and
similar tools. It is not sent across the internet. Today's PSK
Reporter IPFIX/HTTPS uploads also stay as they are; this stream does
not replace them.

## Prior art

Three existing networks already move spots around the world. They
are worth learning from, and they do not agree on what a timestamp
means or how soon a decode should reach a subscriber. Facts below
are from the projects' own documentation or, for DXSpider defaults,
the current official source (G1TLH, mirrored at
[EA3CV/dxspider](https://github.com/EA3CV/dxspider)). Numbers that
are not documented are left out.

### DX cluster (DX Spider, AR-Cluster, and peers)

- **Transport.** Users connect by telnet (sometimes with a password)
  and receive a line-based stream. Nodes flood spots to each other
  over the PacketCluster PC protocol: legacy `PC11` for a DX spot,
  and `PC61` (from VE7CC) as its replacement, which allows a more
  precise frequency and carries the spotter's IP address. Modern
  nodes also use `PC9x` sentences with a timestamp that is seconds
  since midnight UTC, required to be unique and increasing, for
  de-duplication of routing and chat. Hop counts limit how far a
  sentence travels. This is a mesh, not a single server.
- **Message format.** The user-facing line is the longstanding
  cluster format, for example (from the AR-Cluster user manual,
  invented sample):
  `DX de W1AAA:     14025.0  ZL2BBB       CQ                       1832Z`.
  Fields are spotter, frequency in kHz, spotted call, a short
  comment, and `HHMMZ`. `PC11` /
  `PC61` add a date, the origin node, and a hop count (`PC61` also
  adds the spotter IP). Both human-typed spots and skimmer spots
  share this format; skimmer / RBN spotters are usually marked with
  a `-#` suffix. AR-Cluster 6.6 keeps RBN and other `-#` skimmer
  spots local to its users and does not forward them to cluster
  peers.
- **Timestamps.** The live user line is minute resolution (`1832Z`),
  with no seconds and no date. History queries can show a fuller
  date-time (AR-Cluster's `DTS` filter accepts values such as
  `2011-12-11 03:04:55`). The time is whatever the spotting station
  or node stamped, not a GPS-quality measurement, and there is no
  clock-source field.
- **Latency.** A human spot goes out when the operator types it.
  Node-to-node flooding is near real time, but the published time
  still only has one-minute resolution. No official end-to-end
  latency target is documented.
- **Filtering.** Users filter by band, call, continent, comment,
  spotter, and date-time, with syntax that differs by server family
  (DX Spider, AR-Cluster Boolean filters, CC Cluster).
- **De-duplication.** Because the same spot is flooded across many
  links, every node has to drop repeats. Current DXSpider defaults
  in `perl/Spot.pm`: hold a duplicate key for `$Spot::dupage` =
  10 minutes; use 15 characters of comment text (`$duplth`);
  normalize frequency to `$qrggranularity` = 1 kHz and time to
  `$timegranularity` = 60 s (the wiki still describes older 25 kHz /
  100 s comments; the source is what matters). Separate flood checks
  watch how often a call, a spotter, or an IP appears
  (`$do_call_check`, `$do_by_check`, `$do_ipaddr_check` on by
  default). Spots whose timestamp is older than `$spotage` =
  125 s are treated as too old to be live. PC9x sentences de-dupe
  on origin plus the increasing midnight-relative timestamp.
- **Trust and abuse.** Login is by callsign, sometimes with a
  password. `PC61` carries a spotter IP. DXSpider can verify or
  drop forged `PC11`/`PC61` (`$DXProt::senderverify`), isolate a
  neighbor (`set/isolate`) so traffic does not transit, and censor
  with a bad-word list. Forged and flooded spots are a recurring
  operational problem on the mesh; the protocol itself is still
  largely a trust-the-peer flood.
- **Borrow.** A compact, logger-friendly text form; per-user
  filters; origin-aware de-duplication; a unique increasing
  sequence so reconnects and loops are obvious. **Avoid.**
  Minute-only timestamps; a coarse dupe key that can collapse
  distinct QSOs; hop-count flooding as the main distribution path;
  sending skimmer traffic into a human mesh without a clear label
  and an off switch.

### Reverse Beacon Network

- **Transport.** Node operators run the official Windows Aggregator,
  which collects CW/RTTY spots from CW Skimmer / Skimmer Server over
  telnet, and FT4/FT8 spots from WSJT-X-style UDP (default
  `127.0.0.1:2237`, Aggregator's FT# tab). Aggregator forwards
  accepted spots to the RBN servers. Public output is two unfiltered
  telnet relays, documented at
  [Telnet servers](https://www.reversebeacon.net/pages/Telnet+servers+30):
  `telnet.reversebeacon.net:7000` for CW and RTTY, and port `7001`
  for FT8. Those relays have no per-user filtering; RBN's stated
  intent is that retail DX cluster nodes connect to them, and
  end-users connect to the retail nodes. Aggregator can also offer
  a local telnet stream for monitoring.
- **Message format.** The public feed uses the same `DX de … HHMMZ`
  line as a DX cluster, with the spotter call ending in `-#` so
  a skimmer is obvious. The comment typically carries mode and SNR
  (and WPM for CW). There is no separate structured record on the
  public telnet port.
- **Timestamps.** Same minute-resolution `HHMMZ` as the cluster
  line. The underlying decode may be much more precise, but that
  precision is not on the wire. Clock source is not advertised.
- **Latency.** Aggregator "process[es] these messages and send[s]
  spot data to the RBN as appropriate" (W3OA, *Send FT8 Spots to
  the RBN*, 2020-05-13). There is no documented numeric latency
  target. The public relays are built for throughput, not for
  filtering. When a DXSpider node redistributes RBN spots to its
  own users it can dwell and re-spot: `$RBN::respottime` defaults
  to 3 minutes and `$RBN::beacontime` to 5 minutes. That is
  cluster-side policy, not the raw RBN feed.
- **Filtering.** None on the official public relays. Retail
  clusters and loggers are expected to filter. FT8 was put on a
  separate port from CW/RTTY so operators could choose the volume
  they can handle (RBN FT8 announcement).
- **De-duplication.** Not provided on the public relays. Downstream
  clusters apply their usual dupe rules, including the RBN respot
  timers above. Two skimmers hearing the same station produce two
  lines, which is useful for propagation.
- **Trust and abuse.** Upload is through Aggregator, run by known
  node operators. The public feeds are unfiltered relays. RBN
  reserved the right, when FT8 was added, to shed that stream if
  the load threatened the CW/RTTY mission. Official rate-limit
  numbers for Aggregator uploads are not documented on the pages
  consulted.
- **Borrow.** One structured record per decode, forwarded promptly;
  a clear skimmer marker (`-#`, or in our case a distinct instance
  id); a high-volume digital feed that can be subscribed
  separately; do not force every listener to take the whole
  firehose. **Avoid.** Dropping timestamp precision to one minute;
  an unfiltered telnet port as the only official access; leaving
  de-duplication entirely to every downstream client.

### PSK Reporter

- **Transport (upload).** Official reporting is IPFIX over UDP to
  `report.pskreporter.info:4739`
  ([developer notes](https://pskreporter.info/pskdev.html)).
  Datagrams are sent at most once every five minutes, unless a
  packet becomes full. Timed flushes must not be synchronized to
  the system clock, and a flush based on the last signal should
  add randomization, so thousands of clients do not report at
  once. Templates are resent periodically because UDP is lossy.
  There is no authentication on this path.
- **Transport (query).** HTTPS GET or POST to
  `https://retrieve.pskreporter.info/query` returns an XML document
  of reception reports. Defaults are the last 100 records, and in
  any case no more than six hours. `flowStartSeconds` is a negative
  window of at most 24 hours. Other filters include sender or
  receiver call, mode, frequency range, and `lastseqno` for
  incremental fetch. The operator may rate-limit or block heavy
  users, and asks frequent clients to pass `appcontact=…`.
- **Transport (live MQTT).** [mqtt.pskreporter.info](https://www.mqtt.pskreporter.info/),
  operated by Tom M0LTE with permission from Philip Gladstone,
  redistributes the live feed of spots as they arrive at PSK
  Reporter. Ports: 1883 plain TCP, 1884 TLS, 1885 WebSockets, 1886
  WebSockets plus TLS. No account is required to subscribe. Topic
  hierarchy:
  `pskr/filter/v2/{band}/{mode}/{tx}/{rx}/{tx_grid}/{rx_grid}/{tx_dxcc}/{rx_dxcc}`
  (`+` and `#` wildcards). A `v2raw` tree is unfiltered; `v2raw_1pc`
  is a 1% sample. Receiver metadata is published as retained
  messages on `pskr/rxinfo/{callsign}`.
- **Message format.** IPFIX is self-describing binary (call, locator,
  frequency, mode, SNR, `flowStartSeconds`, software, and so on).
  The query API returns XML `receptionReport` records. Each MQTT
  message is one JSON spot: sequence `sq`, frequency Hz `f`, mode
  `md`, SNR `rp`, receiver-reported epoch `t`, optional derived
  transmit-start epoch `t_tx`, sender/receiver calls, locators,
  DXCC ids, and band.
- **Timestamps.** Official fields are Unix epoch **seconds**, not
  milliseconds. What `t` means depends on the reporting software:
  some versions stamp the start of the transmission, others the
  decode time (for FT8, typically about 11–14 s later). PSK
  Reporter publishes a per-version `time_start` flag at
  `pskreporter.info/api/characteristics`. The MQTT feed keeps `t`
  as received and, when it can, adds `t_tx`; if it cannot tell, it
  omits `t_tx` rather than guess. That ambiguity is the main timing
  lesson.
- **Latency.** The official uploader policy is the five-minute
  batch (and "each callsign should be reported no more than once
  per five minute period"; ideally only once per hour if nothing
  changed). The MQTT feed is real-time relative to arrival at PSK
  Reporter, so end-to-end delay is dominated by that upload
  batching, plus server processing. No official numeric latency
  for the MQTT hop itself is published. The query API is pull, not
  push.
- **Filtering.** Upload-side de-dupe of repeated callsigns. Query
  filters as above. MQTT filtering is the topic tree: band, mode,
  calls, grids, DXCC, with wildcards.
- **De-duplication.** Clients are told to eliminate duplicate
  callsigns before upload. The query API can resume with
  `lastseqno`. MQTT carries a sequence number per spot. Two
  receivers of the same transmission are two reports, on purpose.
- **Trust and abuse.** The IPFIX upload is unauthenticated UDP.
  The query API is rate-limited as needed. The MQTT subscribe path
  is open (no account). Publisher trust is whatever PSK Reporter
  itself applies to incoming IPFIX, which is not fully documented
  on the developer page.
- **Borrow.** Hierarchical topic filters; JSON with absolute
  frequency in Hz; sequence numbers and an HTTPS history query for
  catch-up; retained receiver/status info; never invent a transmit
  time you cannot justify. **Avoid.** Five-minute (or hourly)
  batching for a live stream; one-second timestamps when the
  decoder knows milliseconds; a time field whose meaning depends
  on the sender's software version; unauthenticated UDP as a
  publish path.

### Summary: what this protocol should do differently

| Topic | Existing networks | This protocol |
| ----- | ----------------- | ------------- |
| Wide-area transport | Telnet, UDP/IPFIX, MQTT | Secure WebSocket for the live feed; HTTPS for history. WSJT-X UDP only on the LAN. |
| When a decode is sent | Cluster/RBN: promptly, but often then coarsened; PSK Reporter: batched up to 5 minutes | Published at once, not batched |
| Timestamp | Minute (`HHMMZ`) or whole seconds, and sometimes ambiguous | Receiver UTC period start + DT, to the millisecond, plus clock source and quality; the server adds its own receive time |
| Target latency | Not stated, and in PSK Reporter's case bounded by the batch | Under 1 s from local decode to subscriber |
| Filters | Cluster command languages; RBN none on the official relays; MQTT topics | Subscribe filters in the web protocol, MQTT-style (band, mode, station, region, SNR) |
| Legacy clients | Universal `DX de` telnet | Optional DX-cluster-compatible telnet output, with the known minute-time limit called out |
| Publish trust | Mixed: callsign login, known node-ops, or no auth | Per-station key to publish; default off and private |

## Protocol

The wide-area protocol is a small web API designed for decodes, not
a tunnel for WSJT-X UDP.

### Transport

- **Live feed:** a secure WebSocket (`wss://`) for real-time push
  in both directions (a skimmer publishes, a listener subscribes).
  After connect, the client sends a hello and either publish
  credentials or a subscribe filter.
- **History:** a plain HTTPS API to catch up after a reconnect, or
  to look back over a time window, filtered the same way as the
  live feed and resumeable by sequence number (the idea behind PSK
  Reporter's `lastseqno`, with millisecond times).
- **Optional legacy telnet:** a DX-cluster-compatible text stream
  (`DX de … HHMMZ`, login by callsign) so existing loggers can
  consume the feed. This output is lossy: the line format only
  has minute-resolution time, so the exact `period + DT` stays in
  the comment or is simply truncated, and that limitation is
  documented in the GUI. It is an adapter, not the native
  protocol.
- **Optional read-only MQTT:** subscribe to PSK Reporter's live
  feed at `mqtt.pskreporter.info` and adapt those spots into
  `SKIM-PSKR-…` instances. WS Skim does not publish to that
  broker. The same topic-filter style is used in our own
  subscribe messages.
- **Private sites:** the same `wss://` and HTTPS API, reached over
  a LAN or a VPN such as Tailscale, so the server need not be on
  the public internet.

The first public deployment is a home relay on Jeff's Proxmox
server, published as `live.wa1hco.net` (see
[Home relay on Proxmox](#home-relay-on-proxmox)). The existing
[wa1hco.net](https://wa1hco.net) site stays on DreamHost Shared
Unlimited, which can serve static pages and docs but cannot run a
long-lived WebSocket service or open custom ports. The load for
one station with a handful of bands is tiny.

### Message format

Versioned JSON on the wire. The binary QDataStream format stays on
the LAN. JSON is easier to debug, version, and extend, and it can
carry fields WSJT-X UDP lacks.

Each message includes:

- A schema version, so older and newer copies can coexist.
- `type`: `hello`, `subscribe`, `heartbeat`, `status`, `decode`,
  `clear`, `close`, and server `ack` / `error`.
- `origin_call` and `origin_grid` of the station that made the
  decode.
- `origin_source` (`FLEX`, `B210`, `KIWI`, …), band, mode, and
  dial, matching the local instance.
- `message_id`, unique per published event, and a monotonic
  `seq` per publisher (and a server `seq` on the way out) so a
  subscriber can detect gaps and resume.
- **Timing fields,** described in the next section.
- Absolute RF frequency in Hz, plus dial and audio offset, so a
  client never has to guess the band from a kHz text field.
- The corresponding WSJT-X fields, carried faithfully. For
  Decode: new, SNR, DT, delta_frequency, mode character, message
  text, low confidence, and off-air. For Status: dial, mode name,
  my call, my grid, and `tx_enabled` false. Heartbeat carries the
  WS Skim version and revision, as today.

A compact cluster-style text line can be included as an optional
field, or produced by the telnet adapter, for legacy clients.
The JSON record is always the source of truth.

The subscriber maps JSON back through the existing
`build_heartbeat` / `build_status` / `build_decode` /
`build_clear` / `build_close` helpers and sends ordinary UDP to
the configured destination (default `224.0.0.73:2237`).

### Timing

This is the part the existing networks leave ambiguous, and it
must be explicit here.

- A decode's timestamp is the **receiver's UTC period start, plus
  the DT offset, to the millisecond**. Both values are sent:
  `period_utc` (the UTC instant the period began) and `dt_ms`
  (signed milliseconds of DT). Their sum is the implied event
  time. For MSK144 and other fast modes, the same rule uses the
  15 s window start plus the time of the ping inside that window.
- The publisher also states its **clock source**: `gps`, `ntp`,
  or `unknown`, and a **clock-quality** estimate (for example
  a reported NTP or GPS offset in milliseconds, or "unknown").
  A KiwiSDR in IQ mode can stamp `gps` from the radio; a PC
  synced only to pool.ntp.org is `ntp`. The subscriber does not
  silently rewrite another station's timestamps.
- The **server adds a receive timestamp** (`server_rx_utc`) when
  the message arrives, in UTC milliseconds. That lets us measure
  delay and discard absurdly stale frames without altering the
  receiver's own time.
- A decode is **published at once**, not batched, as soon as the
  local decoder produces it.
- **Target latency** is under 1 s from that local decode to a
  connected subscriber on a normal internet path (not counting
  the time the decoder itself spent inside the period).

Every publisher keeps its clock in sync with NTP or GPS. Residual
skew should stay well under one FT8 symbol (~0.16 s). The GUI
shows a plain-language warning if the clock looks wrong.

### Subscribe filters

A subscriber sends a filter rather than taking the whole firehose
(the lesson of RBN's unfiltered relays and of PSK Reporter's topic
tree). Filters can be combined:

- band, mode
- originating station (call)
- region (grid prefix, or later a DXCC / continent list)
- minimum SNR
- optionally a named source type (`KIWI`, `FLEX`, …)

Wildcards follow the MQTT convention (`+` one token, `#` the rest)
when the filter is written as a topic, for example
`ws-skim/v1/{origin_call}/{source}/{band}/{mode}`. The WebSocket
`subscribe` message can say the same thing as structured fields.
The HTTPS history API accepts the same filters.

### Remote instance naming, loops, and duplicates

Local ids are unchanged: `SKIM-{SOURCE}-{BAND}-{MODE}-{kHz in band}`.

Remote ids include the originating call so they can never collide
with a local instance:

`SKIM-{CALL}-{SOURCE}-{BAND}-{MODE}-{kHz in band}`

for example `SKIM-WA1HCO-FLEX-2m-FT8-174` or `SKIM-WA1HCO-KIWI-20m-FT8-74`.
Optional PSK Reporter spots use a distinct prefix, for example
`SKIM-PSKR-{REPORTER}-{BAND}-{MODE}`.

Loop and echo prevention:

- Only publish decodes this copy made from its own radio (or file)
  source. Never republish traffic that arrived from a subscription.
- Every published message carries `origin_call`, `message_id`, and
  `seq`.
- Drop any incoming message whose `origin_call` is this station's
  call, even if the topic or id looks different.
- Remote UDP instances always include the `CALL` token. A
  subscribed decode is never emitted under a local-style
  `SKIM-{SOURCE}-…` id.
- If the publisher is already on the same LAN multicast group, do
  not re-emit that source onto that group (no hairpin).

De-duplication:

- The same `message_id` or publisher `seq` seen twice (reconnect,
  at-least-once delivery) is dropped.
- The same RF decode heard by two remote skimmers is *not* a
  duplicate. WS Hub should show both instances; that is the point
  of a network. Cluster-style keys that round frequency to a
  kilohertz and time to a minute are too coarse for this feed.
- A later display filter may hide a call already shown from a more
  trusted source. That is optional and is not part of the first cut.

### Authentication, trust, and sharing

- The feature defaults to off. Private use (your own stations) is
  the first goal.
- Publish requires a per-station key, entered in settings and
  presented on the WebSocket hello. The connection is always TLS.
- Subscribe may be open for a public relay, or keyed, at the
  operator's choice. Public listen is fine; public unauthenticated
  publish is not.
- For site-to-site, prefer a relay that is not exposed on the
  public internet (Tailscale, a LAN, or a VPN) unless the intent
  really is a public source.
- A public relay (`live.wa1hco.net`, or any later host)
  authenticates publishers, rate-limits them, and may let
  subscribers trust only a list of named calls. Guarding against
  spam and fake spots is a server-and-key problem, not something
  the local UDP hop can fix.

### Contest use

Remote spots may count as outside assistance, depending on the
contest category. The GUI has a clear toggle that turns
subscription — and re-emission to local UDP and to the optional
telnet adapter — off, labeled so it is obvious that remote spots
are incoming assistance. Each remote instance id already names the
source call. When the toggle is off, subscribed traffic is not
sent to WS Hub, N1MM, or other local listeners. Local decodes
continue to publish only if Publish is on.

### GUI (zero-knowledge)

Users configure this only in the app, under Reporting or a Stream
panel, and start WS Skim from the desktop launcher. No CLI flags,
environment variables, or `python -m` in user-facing docs or
launchers. Developer notes may still mention those in `docs/dev/`.

Settings, all stored in the user profile like the rest of Reporting:

- Publish on/off (default off).
- Subscribe on/off, and a list of stations, bands, modes, or
  topic-style filters to follow.
- Relay URL (`wss://…`, with HTTPS history derived from it).
- Per-station publish key, and any subscribe key, entered in the
  settings panel.
- Contest / local-only toggle.
- Optional: DX-cluster telnet output on or off (and the reminder
  that its times are minutes, not milliseconds).
- Optional: subscribe to PSK Reporter's live MQTT feed (read-only).
- Connection state in the panel: connected, reconnecting, or a
  plain last-error sentence that says what to do next. Clock
  source and a simple quality hint are visible when Publish is on.

## KiwiSDR as an input (first public source)

A KiwiSDR is a good first public receiver for this stream: HF only
(0–30 MHz, so no 6 m or 2 m), four receive channels, or eight in
the extra-channels mode, each about 12 kHz wide — the same sample
rate jt9 already takes. In IQ mode each packet can carry a GPS
timestamp, which is a better period clock than the PC clock alone.

WS Skim should grow a first-party Kiwi client and a `KIWI` source
tag, so instances look like `SKIM-KIWI-20m-FT8-74` locally and
`SKIM-{CALL}-KIWI-20m-FT8-74` on the stream. Settings belong in the
GUI, like every other source.

We cannot borrow [DigiSkimmer](https://github.com/lazywalker/DigiSkimmer)
code: it is GPL-2.0-only, which cannot be mixed into WS Skim's
GPL-3.0 tree, and its Kiwi support is a 2020 snapshot of
[kiwiclient](https://github.com/jks-prv/kiwiclient). The official
client still has no license file (see
[kiwiclient#157](https://github.com/jks-prv/kiwiclient/issues/157)).
Writing our own client from the published protocol avoids both
problems. The work is about the size of the Airspy source plus a
protocol module of a few hundred lines.

The intended first public path is: the Kiwi (or a Flex over DAX
IQ) streams to a WS Skim instance, which publishes to the home
relay; the public and other sites subscribe at `live.wa1hco.net`,
and WS Hub takes the last hop as local UDP. A USB-attached radio
such as a B210 still needs WS Skim on the machine it is plugged
into; that copy publishes to the same relay over the LAN. The
same WS Skim can still upload to PSK Reporter as it does today.

## Home relay on Proxmox

Jeff's decision is to run the live relay at home on his Proxmox
server, not on DreamHost. A read-only look at wa1hco.net confirmed
it is DreamHost Shared Unlimited (`iad1-shared-b8-09`, Ubuntu
24.04, Python 3.12, Node 18). Shared hosting does not allow
persistent processes or custom ports, so it cannot run the
WebSocket relay. It can still host the public website: static
pages, a landing page, and docs that link to the live feed.

Two unprivileged LXC containers are enough (preferred over full
VMs). Both run Debian 12 or Ubuntu 24.04, with chrony or NTP so
receive timestamps and decode periods stay honest, and both are
covered by Proxmox snapshots and backups so an update can be
rolled back.

- **Relay container**, kept small: 1 vCPU, 512 MB–1 GB of RAM,
  about 8 GB of disk. It runs the relay as a systemd service, a
  ring-buffer of recent history (optional SQLite for a longer
  window), and `cloudflared` (Cloudflare Tunnel) so the feed is
  published as `https://live.wa1hco.net` with TLS. No ports need
  opening on the home router, and the home IP stays hidden.
- **WS Skim container**, sized for decoding, when the radio is
  on the network. A KiwiSDR and a Flex's DAX IQ already stream
  over IP, so WS Skim does not have to sit on the radio or Kiwi
  host. It can run in this always-on container next to the relay:
  Kiwi or Flex → WS Skim → relay, all on the LAN. For four to
  eight Kiwi channels, plan on 2–4 cores and enough RAM for
  several jt9 processes. A USB-attached radio such as a B210 is
  the exception: that copy of WS Skim has to run on the machine
  the radio is plugged into, and it publishes to the relay over
  the LAN (or, if that machine is away from home, through the
  tunnel).

WS Hub, at home or elsewhere, subscribes to the relay (locally
on the LAN, or at `live.wa1hco.net`) and keeps using WSJT-X UDP
as the last hop.

**DNS for `live.wa1hco.net`.** A Cloudflare Tunnel needs the
hostname to be in a Cloudflare zone so Cloudflare can issue the
certificate and route the tunnel. Two practical options, without
moving the existing wa1hco.net website off DreamHost:

- Add `wa1hco.net` to a Cloudflare account and point the domain's
  nameservers at Cloudflare (Full Setup). The apex and `www`
  records can still aim at the DreamHost shared server, and
  Cloudflare can then attach `live` to the tunnel automatically.
  This is the path Cloudflare documents as the smoothest for
  Tunnel.
- Keep DreamHost as the DNS host and use Cloudflare's Partial
  (CNAME) setup. DreamHost allows a custom CNAME on a Fully
  Hosted domain for a subdomain that does not already have an A
  record; the whole domain does not have to be switched to "DNS
  Only" just for `live`. The CNAME target in that arrangement is
  `live.wa1hco.net.cdn.cloudflare.net`, not a raw
  `<id>.cfargotunnel.com` name. A CNAME at DreamHost alone, with
  no Cloudflare zone, is not enough for a normal Tunnel hostname.

If neither is convenient, use a different public hostname that
is already on Cloudflare, and link to it from the wa1hco.net
pages. The exact DNS choice can wait until setup.

The downside of a home relay is that the public stream goes down
whenever home internet or power does. A later move to a VPS can
reuse the same container image; it is a fallback, not the first
cut.

This section is a plan, not an install guide. When the time
comes, bringing the containers up should be a one-click or
GUI-friendly script under `scripts/`, in the same zero-knowledge
style as the rest of WS Skim: no CLI flags, environment
variables, or `python -m` for Jeff to remember.

## Phased work

1. This plan (docs only).
2. JSON schema, timing fields, instance naming, loop-prevention
   and de-duplication helpers, with unit tests. No network yet.
3. WebSocket publish of local Heartbeat, Status, and Decode to a
   private relay (Clear and Close when an instance goes away), plus
   the HTTPS history stub for reconnect catch-up.
4. WebSocket subscribe, convert to local WSJT-X UDP with remote
   instance ids, using the existing builders in
   `ws_skim/wsjtx_udp.py`.
5. GUI settings and connection status (phases 3–4 can be exercised
   in tests without the GUI).
6. Contest / local-only toggle, reconnect with backoff, and the
   optional DX-cluster telnet adapter.
7. Optional PSK Reporter MQTT read-only subscribe, adapted into
   `SKIM-PSKR-…` instances.
8. Public relay in a small Proxmox LXC at home, published through
   a Cloudflare Tunnel as `live.wa1hco.net`, with publisher keys,
   rate limits, and a trust list. wa1hco.net on DreamHost keeps
   the landing page and docs.
9. KiwiSDR input, with WS Skim in its own Proxmox LXC next to the
   relay (or on the USB-radio host for a B210), so the public
   feed can be driven from Jeff's Kiwi as well as from a Flex.
   Tailscale remains a deployment choice, not a separate code
   path.

## Test plan

- Unit tests: JSON round-trip of every field, including
  `period_utc + dt_ms`, clock source, and server receive time;
  remote vs local ids; a message whose `origin_call` is ours is
  dropped; the same `message_id` / `seq` is dropped the second
  time; two origins with the same CQ stay two instances; a
  subscribed message is never marked publishable.
- Timing tests: a fixture decode at a known period and DT
  reconstructs the event time to the millisecond; a subscriber
  uses the publisher's period and DT, not its own midnight clock;
  a stale message older than a set bound is not emitted as live.
- Two processes (or a test double) on one computer, against a
  private relay: A publishes a fixture decode, B emits a UDP
  datagram whose id is `SKIM-{A's call}-…` and whose SNR, DT,
  delta_frequency, mode character, message text, and event time
  match, and the delay from publish to UDP is under 1 s on the
  local path.
- Reconnect: B disconnects, A publishes, B resumes by `seq` from
  the HTTPS history API and does not leave a gap or a duplicate.
- Loop test: B's publisher stays silent about A's decode; A does
  not re-emit its own message as a remote instance; a hairpin onto
  the same multicast group does not happen.
- Contest toggle off: B may receive the WebSocket feed but sends
  no remote UDP and no telnet spots.
- Optional telnet adapter: a logger-style client sees a well-formed
  `DX de` line; the test documents that `HHMMZ` is minute
  resolution.
- Relay down: the app stays up, shows a plain error, and
  reconnects without dropping local decoding or local UDP.
- Bandwidth smoke test: a busy FT8 period stays in the kilobytes,
  not megabytes.

## Open questions

- DNS for `live.wa1hco.net`: move the zone to Cloudflare, or keep
  DreamHost DNS and use a Partial CNAME. A different public
  hostname is the fallback.
- Whether `cloudflared` shares the relay container or gets its
  own.
- How finely the first GUI subscribe list is specified (whole
  station, or per band and mode).
- Whether a later public feed needs callsign verification beyond a
  per-station key (for example a registration step, or LoTW).
- Whether the optional telnet adapter lives on the relay, on each
  WS Skim, or both.
- Whether WS Hub should also speak the web protocol directly
  someday. The first cut only talks to WS Hub over local UDP, as
  it does today.
