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

## Phase 2 — Build

Filled in once Phase 1 is decided.
