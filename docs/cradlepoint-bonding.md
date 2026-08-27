# Cradlepoint WAN Bonding — Reference & Design Notes

Scope: what "bonding" actually means on Cradlepoint hardware, what the platform
does natively, what needs a headend, and how to choose between the options.
Section 8 applies all of it to the lab pair: an R1900 with a CBA1250 as a second
WAN.

> Feature names and licensing on the Cradlepoint/Ericsson side move fast. Treat the
> capability descriptions here as the shape of the problem and confirm exact
> NCOS/NetCloud feature availability and SKUs against current vendor docs before
> committing to a design.

---

## 1. The distinction that matters: load balancing vs. bonding

These get used interchangeably in marketing copy and they are not the same thing.

| | Per-flow load balancing | True bonding (link aggregation) |
|---|---|---|
| Unit of distribution | TCP/UDP flow | packet |
| Single large transfer speed | capped at one link | can exceed one link |
| Needs a headend/concentrator | no | **yes** |
| Survives a link drop mid-flow | no (flow resets) | yes |
| Added latency | none | resequencing buffer |
| Data cost | sum of what you send | can multiply (see duplication) |

**Why bonding requires a headend.** If packets of one TCP flow leave via two
different WAN links, they arrive at the destination with two different source IPs.
No ordinary internet server will reassemble that. Something has to sit on the
public side of both links, terminate both tunnels, resequence the packets, and
emit a single coherent flow. That "something" is the concentrator — a cloud
gateway or a VM you run. There is no way around this; any product claiming
per-packet bonding without a remote endpoint is doing per-flow balancing.

## 2. What Cradlepoint does natively (no headend)

NCOS on-device WAN management gives you, without any cloud service:

- **Failover by priority** — WAN devices ordered; traffic moves to the next
  healthy link when a probe fails. Existing flows break on switch.
- **Load balancing modes** — round-robin, rate/bandwidth-proportional, and
  spillover (fill link 1, overflow to link 2). Distribution is per-flow.
- **WAN affinity / policy rules** — pin traffic classes to specific WANs by
  source, destination, port, or protocol. This is the most underused feature and
  often removes the need for bonding entirely.
- **Connection monitoring** — active probes (ping/DNS/HTTP) per WAN, which is
  what makes failover decisions trustworthy. Passive-only monitoring will happily
  keep a dead-but-attached modem as the active WAN.

If your goal is *aggregate site throughput* across many concurrent flows (web
browsing, a dozen cameras, backups), per-flow balancing gets you there. You do
not need bonding.

## 3. What needs the SD-WAN overlay

Cradlepoint's own bonded-overlay offering (marketed as intelligent bonding /
link aggregation, delivered through the NetCloud SD-WAN overlay with a gateway
endpoint) provides three distinct behaviors that are worth naming separately,
because they solve different problems and have very different cost profiles:

1. **Bandwidth aggregation** — packets of one flow striped across links.
   Use for: a single large uplink stream (HD video contribution, big file push).
   Cost: normal — you pay for the bytes once, spread across carriers.
2. **Flow duplication** — every packet sent on *both* links, receiver keeps the
   first copy and discards the dupe. Zero-loss, zero-reconvergence failover.
   Use for: SCADA, telemetry, voice, anything where a 20-second failover gap is
   an outage. Cost: **2× data for that flow.** Reserve for low-bandwidth critical
   traffic only.
3. **Flow balancing** — smarter per-flow steering with overlay-wide visibility.
   Use for: general traffic, as the default class.

These compose. The realistic policy on a bonded pair is: duplicate the small
critical class, aggregate the one class that needs a fat single pipe, balance
everything else.

## 4. Design constraints that bite in practice

**Latency symmetry is the hard requirement.** Bonding works well across links
with similar RTT — two cellular modems on different carriers in the same location
is the ideal case. Bonding a 40 ms LTE link with a 600 ms GEO satellite link
produces head-of-line blocking: the resequencer holds every packet waiting for
the straggler, and single-flow throughput ends up *worse* than the fast link
alone. If link RTTs differ by more than roughly 2–3×, either exclude the slow
link from aggregation (leave it for failover/duplication only) or verify the
scheduler is genuinely latency-aware before you rely on it.

**Reordering looks like loss to TCP.** This is why the tunnel needs its own
sequence numbers and a resequencing buffer. Buffer sizing is the tradeoff: too
small and you leak reordering to the application, too large and you add latency
and hide real link degradation.

**MTU and MSS.** Encapsulation (GRE/IPsec/UDP) eats payload. Cellular paths are
often 1400 or lower before your tunnel header. Clamp MSS, set the tunnel MTU
explicitly, and test with DF-set large packets — silent PMTUD black holes on
cellular are common and present as "some sites hang" rather than as an outage.

**Overhead is real.** Budget 5–10% throughput loss to encapsulation and keepalives
before you promise anyone "two links = 2×". Aggregation efficiency on well-matched
cellular links is typically 80–90% of the arithmetic sum, not 100%.

**Metered data changes the math.** Aggregation splits bytes across carriers;
duplication doubles them. On a metered plan the duplication policy is a line item,
not a checkbox. Decide it with the bill in front of you.

## 5. Option comparison

| Approach | Headend | Notes |
|---|---|---|
| NCOS native LB/affinity | none | Free with the router. Per-flow only. Start here. |
| Cradlepoint NetCloud SD-WAN overlay | vendor-hosted gateway | Native, supported, single pane of glass with the rest of the fleet. Licensed. |
| Peplink SpeedFusion | FusionHub (self-host or hosted) | Mature bonding. Requires Peplink CPE; a Cradlepoint can only participate as a dumb modem via passthrough. |
| Bondix S.A.B. / Viprinet / Mushroom | vendor or self-host | Purpose-built bonding appliances/software, common in broadcast and mobile. |
| Speedify | vendor cloud | Per-host/software bonding. Fine for a laptop or a single server, not a site solution. |
| OpenMPTCProuter, MLVPN, glorytun, upstream Linux MPTCP | your own VPS | Full control, no license, real operational burden. You own the resequencer, the monitoring, and the 3 a.m. page. |

**Constraint on the DIY paths:** NCOS is a closed appliance OS — you cannot
install a bonding daemon on the Cradlepoint itself. To bond with an external CPE
you have to get each modem's traffic to that CPE independently. IP passthrough
hands a single WAN's address to a single LAN client, so the usual shape is *one
router per modem* feeding a Linux bonding host, not one multi-modem router.
Trying to policy-route N WANs out of one NCOS box into one bonding host is
fighting the platform. If you want bonding and you want to stay on Cradlepoint
hardware, the vendor overlay is the path of least resistance.

## 6. How to verify you actually got bonding

The single-stream test is the whole acid test. Everything else is theater.

```bash
# Baseline each link individually, then bonded.
iperf3 -c <server> -t 60 -P 1     # single stream — must exceed one link's cap
iperf3 -c <server> -t 60 -P 8     # parallel — passes even with mere load balancing
iperf3 -c <server> -t 60 -R       # reverse: downlink schedulers often differ
```

- `-P 1` above one link's capacity ⇒ real per-packet aggregation.
- `-P 8` high but `-P 1` capped at one link ⇒ per-flow load balancing. Fine, but
  don't call it bonding.
- Pull the antenna / disable one WAN mid-transfer. The stream should dip, not die.
  A reset connection means the flow was pinned to that link.
- Record added RTT and jitter bonded vs. best single link — that's the
  resequencing cost, and it's what breaks latency-sensitive apps.
- Re-run after any scheduler or firmware change. Bonding behavior is not stable
  across NCOS releases.

## 7. Recommended decision path

1. **Goal is uptime, not speed?** → NCOS failover with active probes, plus flow
   duplication for the critical class if you can afford the overlay. Skip bonding.
2. **Goal is aggregate site throughput, many flows?** → NCOS load balancing +
   WAN affinity. No headend, no license, no latency cost.
3. **Goal is one flow faster than one link?** → You need bonding and therefore a
   headend. Prefer the vendor overlay on Cradlepoint hardware; go DIY only if you
   want the control and can staff it.
4. **Links have wildly different latency?** → Do not aggregate across them.
   Use the slow one for failover or duplication only.

## 8. Applied: R1900 + CBA1250

This is the topology in the lab, so the generic advice above resolves to something
specific.

```
                 Carrier A (5G)                Carrier B (LTE)
                       |                              |
                 [R1900 internal modem]        [CBA1250 modem]
                       |                              |
                       +-------- R1900 --------- Ethernet WAN
                                   |
                                  LAN
```

**Why the CBA1250 is doing real work here.** The R1900's two SIM slots feed *one*
radio — they give you carrier redundancy by switching SIMs, not two simultaneous
cellular paths. The CBA1250 is what makes the second path concurrent: its LTE
modem terminates on its own carrier and hands the result to the R1900 as an
ordinary Ethernet WAN. So you have two genuinely independent WAN devices on the
R1900, which is the precondition for anything in this document.

Confirm the two SIMs are on **different carriers**. Two SIMs on one carrier gives
you two links and one failure domain, which is the expensive way to get nothing.

### Configuration order

1. **Put the CBA1250 in IP passthrough**, not router mode. Passthrough hands the
   carrier address straight to the R1900's Ethernet WAN: one NAT layer, and the
   R1900 sees the real public IP for policy and diagnostics. Router mode works if
   the plan or carrier won't cooperate, but you accept double NAT and you lose
   that visibility.
2. **Define the Ethernet port as a WAN** on the R1900 (not a LAN member), and set
   its priority relative to the internal 5G modem.
3. **Enable active connection monitoring on the Ethernet WAN. This one is not
   optional.** The Ethernet link between the CBA1250 and the R1900 stays
   physically up when the CBA1250's *cellular* side is down — so with
   passive-only monitoring the R1900 will keep marking a dead WAN as healthy and
   happily blackhole traffic into it. Configure an active probe (ping or DNS to
   an off-net target, not to the CBA1250 itself) so the R1900 tests the path end
   to end. This is the single most common failure in this exact topology.
4. **Set WAN affinity rules** before reaching for anything licensed. Pinning
   traffic classes to the 5G link and leaving the LTE link for bulk/backup often
   satisfies the actual requirement with no overlay at all.
5. **Monitor both devices in NCM.** The R1900 cannot see the CBA1250's signal
   quality, RSRP/SINR, band, or carrier state — as far as the R1900 is concerned
   that WAN is just an Ethernet port. Alerting has to cover the CBA1250 as its
   own device or you'll be debugging a "network problem" that is a -115 dBm
   problem.

### Bonding viability on this pair

Latency profiles are compatible: mid-band 5G typically lands ~25–40 ms and Cat-18
LTE ~40–70 ms. That's inside the 2–3× rule from §4, so per-packet aggregation
across these two is technically sound — unlike, say, bonding either of them with
GEO satellite.

What you still need for actual bonding is the headend (§1). Nothing in the pair of
boxes on the bench can resequence a striped flow; that requires the NetCloud
SD-WAN overlay with a gateway endpoint, or a third-party/DIY concentrator. The
hardware being up and connected gets you to steps 1–4 above, not to bonding.

### Test sequence for the bench

```bash
# 1. Baseline each link alone. Disable the other WAN, don't just deprioritize it.
iperf3 -c <server> -t 60 -P 1        # 5G alone
iperf3 -c <server> -t 60 -P 1        # LTE alone (CBA1250 path)

# 2. Both up, per-flow load balancing:
iperf3 -c <server> -t 60 -P 8        # should approach the sum
iperf3 -c <server> -t 60 -P 1        # will stay capped at one link — expected

# 3. Failover behavior — the important one:
#    start a long single-stream transfer, then pull the CBA1250's cellular
#    (airplane mode / eject SIM), and separately test pulling the Ethernet cable.
#    Time the recovery. Note whether the flow resets or survives.
```

Record the `-P 1` numbers per link now, while the bench is quiet. They are the
reference you'll compare against if you later add an overlay and need to prove
aggregation is real rather than assumed.

## Sources

- [Intelligent bonding in a wireless world — Cradlepoint/Ericsson](https://cradlepoint.com/resources/blog/intelligent-bonding-in-a-wireless-world/)
- [Intelligent Bonding for Better Application Resiliency — Cradlepoint/Ericsson](https://cradlepoint.com/solutions/intelligent-bonding-for-better-application-resiliency/)
- [What is Link Aggregation? Explaining WAN Bonding, Bandwidth, and Better Performance](https://cradlepoint.ericsson.com/blog/what-is-link-aggregation-explaining-wan-bonding-bandwidth-and-better-performance/)
- [Link Aggregation Brings Bandwidth, Efficiency, and Resilience into a New Era of SD-WAN](https://cradlepoint.ericsson.com/blog/link-aggregation-brings-bandwidth-efficiency-and-resilience-into-a-new-era-of-sd-wan/)
- [Mobile SD-WAN vs. Dual Modems in Vehicles](https://cradlepoint.com/resources/blog/mobile-sd-wan-vs-dual-modems-in-vehicles/)
