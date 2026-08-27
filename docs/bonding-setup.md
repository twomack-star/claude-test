# Bonding Setup — R1900 + CBA1250

Working setup guide. Background and the load-balancing-vs-bonding distinction are
in [cradlepoint-bonding.md](./cradlepoint-bonding.md).

Hardware: Cradlepoint R1900 (internal 5G modem) + CBA1250 (LTE) on two different
carriers, both up.

---

## Phase 0 — Prerequisites (path-independent)

Do all of this before choosing a bonding stack. Every item is needed whichever
headend you end up with, and several of them will silently sabotage a bonded
overlay if skipped.

### 0.1 CBA1250 into IP passthrough

Put the CBA1250 in IP passthrough so it hands its carrier address to the R1900's
Ethernet WAN instead of NATing behind its own subnet.

Why it matters for bonding specifically: a bonding scheduler needs to attribute
loss and latency to a *link*. An extra NAT layer with its own buffering and
session table blurs that signal and gives you a second place for sessions to time
out. Router mode will function, but debug it once and you'll wish you hadn't.

- [ ] CBA1250 in IP passthrough
- [ ] R1900 Ethernet WAN shows a carrier-assigned address, not an RFC1918 address
      handed out by the CBA1250

### 0.2 Ethernet port defined as a WAN on the R1900

- [ ] Ethernet port configured as WAN (not a LAN switch member)
- [ ] Both WAN devices — internal 5G modem and Ethernet — show connected
- [ ] Both visible in NetCloud Manager

### 0.3 Active connection monitoring on both WANs

**The highest-priority item in this phase.** The Ethernet link between the
CBA1250 and the R1900 stays physically up when the CBA1250's cellular side drops.
Passive monitoring therefore reports a dead WAN as healthy, and the R1900 keeps
scheduling traffic onto it. Under a bonded overlay this is worse than under plain
failover: the scheduler keeps striping packets into a black hole and the
resequencer stalls waiting for them.

- [ ] Active probe (ping or DNS) enabled on the Ethernet WAN
- [ ] Active probe enabled on the internal 5G WAN
- [ ] Probe targets are **off-net** — not the CBA1250, not the R1900
- [ ] **Different probe target per WAN.** A single shared target that goes down
      marks both WANs failed simultaneously. Use two unrelated anycast resolvers
      or two hosts you control.
- [ ] Failure threshold and interval tuned: aggressive enough to catch a dead
      link before the overlay does, not so aggressive that normal cellular jitter
      flaps the WAN

### 0.4 Path MTU per carrier

Find the real per-carrier path MTU *before* adding tunnel headers, or you will
spend a day blaming the bonding stack for a PMTUD black hole. Symptom of getting
this wrong is not an outage — it's "most things work, some sites and some file
transfers hang", which is much harder to attribute.

From a host behind the R1900, forcing traffic out one WAN at a time:

```bash
# Linux/macOS: -M do / -D sets DF; payload 1472 = 1500 MTU with headers.
ping -M do -s 1472 -c 3 1.1.1.1     # Linux
ping -D -s 1472 -c 3 1.1.1.1        # macOS
# Walk the payload down until it stops failing; PMTU = payload + 28.
```

- [ ] PMTU recorded for the 5G path: ________
- [ ] PMTU recorded for the LTE path: ________
- [ ] Note the lower of the two — that's your ceiling before encapsulation

### 0.5 Confirm CGNAT status

Assume both carriers are CGNAT. Confirm, because it determines whether a headend
can ever be reached inbound and whether a static-IP plan is needed.

```bash
curl -s ifconfig.me      # address the internet sees
```

Compare against the WAN address in the R1900's status page. Different ⇒ CGNAT.

- [ ] 5G path: WAN IP ________  external IP ________  CGNAT? ____
- [ ] LTE path: WAN IP ________  external IP ________  CGNAT? ____

If both are CGNAT (expected), every option in §5 of the reference doc still works
— they all initiate tunnels outbound from the router. It only rules out designs
where the headend calls in.

### 0.6 Firmware

- [ ] NCOS current on the R1900
- [ ] NCOS current on the CBA1250
- [ ] Versions recorded below — bonding and WAN-scheduler behavior changes across
      releases, so a working baseline is worth writing down

### 0.7 Baselines — do not skip

These are the numbers that later prove whether bonding did anything. Take them
now, while the bench is quiet and nothing is tunneled. Disable the other WAN
rather than deprioritizing it, so you know which link you're measuring.

```bash
iperf3 -c <server> -t 60 -P 1      # single stream — the number that matters
iperf3 -c <server> -t 60 -P 8      # parallel — for reference only
iperf3 -c <server> -t 60 -P 1 -R   # reverse; downlink schedulers differ
ping -c 100 <server>               # RTT and jitter, per link
```

Take them at **two different times of day.** The carriers congest
independently, so a single afternoon sample is not a baseline.

| Link | Time | `-P 1` up | `-P 1` down | `-P 8` up | RTT avg | jitter | loss |
|---|---|---|---|---|---|---|---|
| 5G (internal) | | | | | | | |
| 5G (internal) | | | | | | | |
| LTE (CBA1250) | | | | | | | |
| LTE (CBA1250) | | | | | | | |
| Both, balanced | | | | | | | |

**Acceptance for Phase 0:** both WANs up with independent active probes, PMTU
known per carrier, CGNAT status known, and single-stream baselines recorded for
each link at two times of day.

---

## Phase 1 — Choose the headend

Bonding cannot be finished on the bench hardware alone. Something on the public
side of both links has to terminate two tunnels, resequence, and emit one flow.
Options, and what each costs to stand up:

### Option A — Cradlepoint NetCloud SD-WAN overlay

Native. The R1900 builds the overlay to a Cradlepoint-side gateway; bonding
policy (aggregation / duplication / balancing) is configured per traffic class in
NetCloud Manager alongside the rest of the fleet.

- **Needs:** the SD-WAN/bonding entitlement on the account and a gateway endpoint.
- **Effort:** low. Config, not infrastructure.
- **Good when:** this is heading for production, or anyone other than you will
  have to operate it.

### Option B — DIY headend on a VPS

You run the concentrator: a small cloud instance running a bonding daemon
(`glorytun-udp`, MLVPN, OpenMPTCProuter's stack, or upstream Linux MPTCP), with
the client side on a Linux box at the site.

**The topology constraint that decides the design:** NCOS is a closed appliance
OS, so the bonding client cannot run on the R1900. Two ways to give a Linux box
two independent paths:

- **B1 — dual passthrough (recommended for DIY).** R1900 in IP passthrough to
  Linux NIC1, CBA1250 in IP passthrough to Linux NIC2. Linux now holds two real
  WAN interfaces and any bonding stack works natively, with no dependence on NCOS
  routing behavior. Cost: the R1900 stops being your router — the Linux box
  becomes the CPE and owns firewall, DHCP, and Wi-Fi.
- **B2 — source-based steering.** Linux stays on the R1900's LAN with two
  addresses; WAN affinity rules on the R1900 pin source-IP-A to the 5G WAN and
  source-IP-B to the Ethernet WAN; the daemon binds one tunnel to each. Keeps the
  R1900 as router. Cost: depends on NCOS honoring source-based affinity strictly
  and never re-steering — verify before building on it, and re-verify after
  firmware updates.

- **Needs:** a VPS (with enough uplink to carry the aggregate), a Linux box at the
  site, and someone to own it.
- **Effort:** high, ongoing. You own the resequencer, the monitoring, and the
  3 a.m. page.
- **Good when:** you want full control, no per-site licensing, or you're
  characterizing bonding behavior rather than deploying it.

### Option C — Third-party appliance

Peplink SpeedFusion with a FusionHub headend, Bondix, Viprinet.

- **Needs:** vendor CPE at the site. A Cradlepoint can only participate as a
  modem via passthrough, so the R1900 is demoted the same way as in B1.
- **Effort:** medium.
- **Good when:** you already run one of these elsewhere.

### Deciding

- Goal is **seamless failover**, not more throughput → you may not need
  aggregation at all. Flow duplication (A) or well-tuned failover from Phase 0
  gets you there far cheaper. Re-read §7 of the reference doc before spending.
- Goal is **one flow faster than one link** → you need a real headend; pick by
  whether this is production (A) or characterization (B).
- **Latency check:** 5G ~25–40 ms and Cat-18 LTE ~40–70 ms are close enough that
  aggregation across this pair is sound. Confirm against your Phase 0 numbers —
  if measured RTTs differ by more than ~3×, aggregate only when both are healthy
  and leave the slow link for duplication or failover.

---

## Phase 2 — Build: NetCloud Exchange SD-WAN (selected)

Deployment target: both devices in a case, serving Wi-Fi clients, goal is to make
use of the combined speed of the two carriers.

> **Read this vendor doc first.** Cradlepoint publishes a validated design for
> exactly this shape of deployment:
> [NetCloud Validated Design for Mobile Using Intelligent Bonding for Resiliency](https://docs.cradlepoint.com/r/NetCloud-Validated-Design-for-Mobile-Using-Intelligent-Bonding-for-Resiliency/NCX-Service-Gateway-Configuration).
> Also: [Intelligent Bonding Overview](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Intelligent-Bonding-Overview),
> [Creating a Bonded WAN Interface](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Creating-a-Bonded-WAN-Interface),
> [Deploying the NCX Service Gateway](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Deploying-the-NetCloud-Exchange-Service-Gateway).
> Exact field names below are described by intent, not transcribed — confirm them
> against those pages.

### 2.1 Read the bottleneck before you build

For a Wi-Fi-served case, the bonded WAN is unlikely to be the narrowest point.
The chain is:

```
Wi-Fi client ← Wi-Fi radio ← R1900 forwarding+crypto ← bonded overlay ← 2 carriers
```

Delivered speed is the **minimum** of those, so identify which one binds before
spending effort on the WAN end:

- **Wi-Fi radio.** Airtime is shared across all clients, and a distant slow client
  consumes disproportionate airtime (rate anomaly), dragging down everyone. The
  R1900's radio is a router's radio, not a capacity AP. If the case has to serve
  more than a handful of users at speed, an external AP on the LAN will
  out-perform the built-in radio by a wide margin. Measure client-to-router
  throughput over Wi-Fi *first* — if that caps below the sum of your two links,
  bonding buys the clients nothing.
- **R1900 forwarding with crypto on.** Overlay traffic is encapsulated and
  encrypted, and bonding adds sequencing and resequencing work. A router's rated
  plaintext throughput is not its rated IPsec/SD-WAN throughput. Confirm the
  R1900's rated encrypted throughput against your expected aggregate; if the
  carriers can sum above it, the router is your ceiling and no bonding policy
  changes that.
- **The Service Gateway.** See 2.3 — its uplink and sizing cap the whole site.

Record the Wi-Fi and router-crypto numbers next to the Phase 0.7 link baselines.
Whichever is lowest is the thing to fix.

### 2.2 What bonding does and doesn't add for many Wi-Fi clients

Worth being precise, because it changes what to configure:

- Many clients each running their own sessions is the **many-flows** case.
  Per-flow load balancing (Phase 0, no license) already sums the two carriers
  across that population. If you speed-test from one laptop and see only one
  link's worth, that's the *single-flow* cap — not evidence that the site
  aggregate is limited.
- **Bandwidth aggregation** earns its place where one client's single flow needs
  more than one modem: a large upload, a video contribution feed, a big download.
  In a mixed Wi-Fi crowd, that's some traffic, not all of it.
- The genuine wins for a *mobile* case beyond raw sum: sessions survive a link
  dropping instead of resetting, and steering decisions use overlay-wide
  visibility rather than the router guessing locally. In a case that moves through
  varying coverage, that resilience is often worth more than the extra megabits.

**Do not apply flow duplication to general Wi-Fi traffic.** It sends every packet
down both links — double the data bill for the whole user population. Reserve it
for a small, explicitly-matched critical class, if anything.

### 2.3 Service Gateway placement and sizing

Bonded traffic must hairpin through the gateway. Three consequences:

- **Latency.** Every Wi-Fi user's traffic egresses at the gateway, not locally. A
  distant gateway adds RTT to everything, including traffic that had no reason to
  be bonded. If the case will be geographically far from the gateway, measure the
  added RTT before committing.
- **Capacity.** The gateway's uplink and instance sizing cap your aggregate. Size
  it above the sum of both carriers plus headroom, not at it.
- **Egress identity.** All client traffic appears to come from the gateway. That
  is usually a feature (a stable address despite CGNAT on both carriers), but
  check nothing downstream depends on a carrier-local address or geolocation.

Choose deployment: Ericsson's cloud-hosted gateway, or a self-hosted virtual
instance in your own compute. Self-hosting gets you placement control and
possibly lower latency; it also makes gateway uptime your problem.

- [ ] SD-WAN / Intelligent Bonding entitlement confirmed on the NetCloud account
      (verify with your Cradlepoint rep — this is licensed and is the most common
      thing to discover missing on build day)
- [ ] Gateway deployment model chosen and sized above expected aggregate
- [ ] Gateway reachable from both carriers; added RTT measured and acceptable

### 2.4 Bonded interface and policy

1. Register both devices in NetCloud Manager, in a group whose config you control.
2. Build the overlay from the R1900 to the gateway.
3. Create the **bonded WAN interface** over the two WAN devices — the internal 5G
   modem and the Ethernet WAN fed by the CBA1250.
4. Set per-class policy. A sane starting point for this deployment:

   | Traffic class | Mode | Why |
   |---|---|---|
   | Bulk / large transfers | Aggregation | The case that actually needs one fat pipe |
   | General Wi-Fi client traffic | Balancing | Sums across many flows without duplication cost |
   | Small critical (mgmt, telemetry) | Duplication | Only if something truly cannot drop |
   | Everything else | Balancing | Default |

5. Confirm which link is preferred when only one is healthy, and that the overlay
   degrades to single-link rather than failing closed.

- [ ] Bonded interface up, both members active
- [ ] Policy classes defined; duplication scoped narrowly or not used
- [ ] Single-link degradation tested by disabling each member in turn

### 2.5 Cost controls

Wi-Fi users on metered cellular will consume whatever you give them. Aggregation
doesn't multiply per-byte cost, but it does raise the ceiling on how fast the plan
drains.

- [ ] Per-client rate limits or QoS on the Wi-Fi side
- [ ] NetCloud data-usage alerts set per device, per carrier
- [ ] Someone owns the monthly bill review

---

## Phase 3 — The case build (RF, thermal, power)

This phase decides whether the combined speed is achievable at all. Two cellular
radios and a Wi-Fi radio inside one enclosure is an RF-engineering problem, and
getting it wrong costs more throughput than any bonding policy can recover.

### 3.1 Antennas — the highest-risk item

- **Internal/paddle antennas inside the case will not work.** A metal case is a
  Faraday cage; even plastic attenuates and detunes an antenna pressed against it.
  Plan on **external antennas mounted through the case wall** from the start.
- **MIMO needs spatial diversity.** The throughput that makes "combined speed"
  possible comes from multiple spatial streams. Antennas bunched together
  collapse MIMO rank and you lose multi-stream gain — undermining the exact goal.
  Separate the elements as much as the enclosure allows, and use cross-polarized
  elements where available.
- **Isolate the two cellular radios from each other.** The R1900 transmitting
  raises the noise floor at the CBA1250's receiver and vice versa (desense).
  Different carriers helps but does not guarantee band separation. Maximize
  physical separation between the two devices' antenna sets — as a rule of thumb
  aim for at least a half wavelength (~15 cm around 1 GHz, and more is better),
  and cross-polarize.
- **Keep Wi-Fi antennas away from cellular.** 2.4 GHz Wi-Fi sits close to LTE
  bands in the 2.3–2.7 GHz range, and 5 GHz to LAA/B46. This is a real
  coexistence problem in a shared enclosure, not a theoretical one.
- Practical answer for most case builds: a purpose-built external MIMO
  dome/puck antenna (5-in-1 or 7-in-1) per device, or discrete external antennas
  with maximum achievable separation.

### 3.2 Thermal

Two cellular radios at sustained high transmit power, plus a router SoC doing
encryption for a bonded overlay, inside a closed box. Modems throttle when hot,
and they throttle precisely when you are using them hard.

**Test method matters:** run sustained load for 30–60 minutes with the case
*closed*, and record throughput over time. A cold-start burst number proves
nothing. Throughput that decays over the first half hour is thermal, and it is the
most common way a case build passes on the bench and fails in the field.

- [ ] Ventilation, vents, or active airflow provided
- [ ] Sustained-load test run with the case closed; throughput-over-time recorded
- [ ] Ambient worst case considered (a closed case in sun is not room temperature)

### 3.3 Power

- [ ] Combined draw budgeted for both devices at **peak** cellular transmit, not idle
- [ ] Inrush handled at power-on
- [ ] If battery: runtime calculated at full transmit on both radios

---

## Phase 4 — Validation

Prove the combined speed reaches a Wi-Fi client, which is the actual requirement.

```bash
# From a Wi-Fi client, not from a wired LAN port.
iperf3 -c <server> -t 60 -P 1      # single flow — vs. Phase 0.7 single-link baseline
iperf3 -c <server> -t 60 -P 8      # many flows — the many-client proxy
iperf3 -c <server> -t 60 -P 1 -R   # downlink
```

- [ ] Single-flow throughput from a Wi-Fi client **exceeds the better single link**
      — this is the only proof that aggregation is real and reaching the client
- [ ] Multi-flow throughput approaches the sum, minus 10–20% encapsulation overhead
- [ ] Wi-Fi-only throughput measured separately, to confirm Wi-Fi isn't the cap
- [ ] Sustained 30–60 min run, case closed, no thermal decay
- [ ] Each link disabled in turn: flows survive, throughput degrades gracefully
- [ ] Added RTT through the gateway measured and acceptable
- [ ] Repeated at two times of day (carriers congest independently)
