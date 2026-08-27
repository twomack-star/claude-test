# Bonding Two SIMs Into One Connection — R1900 + CBA1250

**Goal:** one logical connection whose throughput is the sum of both carriers.

**Hardware:** Cradlepoint R1900 (internal 5G modem, Carrier A) + CBA1250 (LTE,
Carrier B). Both up.

**Path:** Cradlepoint NetCloud Exchange SD-WAN, Intelligent Bonding.

> Exact NCM field names below are described by intent rather than transcribed —
> Cradlepoint's docs domain is unreachable from the environment this was written
> in. Confirm against:
> [Intelligent Bonding Overview](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Intelligent-Bonding-Overview) ·
> [Creating a Bonded WAN Interface](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Creating-a-Bonded-WAN-Interface) ·
> [Deploying the NCX Service Gateway](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Deploying-the-NetCloud-Exchange-Service-Gateway) ·
> [Validated Design for Mobile Using Intelligent Bonding](https://docs.cradlepoint.com/r/NetCloud-Validated-Design-for-Mobile-Using-Intelligent-Bonding-for-Resiliency/NCX-Service-Gateway-Configuration)

---

## What the build requires

Three components. You have two of them.

| Component | Status |
|---|---|
| Router with two independent WAN paths | R1900 + CBA1250 — have |
| Bonding-capable overlay on the router | NCOS + SD-WAN entitlement — licence needed |
| **Concentrator on the public side** | NCX Service Gateway — **must be provisioned** |

The concentrator is not optional and not a preference. Bonding stripes a single
flow's packets across both carriers, so those packets arrive at the far end with
two different source IPs. Something has to terminate both tunnels, resequence, and
emit one coherent flow. That is the Service Gateway. It is what turns two SIMs
into one fast connection — the two devices alone can only alternate or balance
between links, never sum a single flow.

---

## Step 1 — CBA1250 into IP passthrough

Put the CBA1250 in IP passthrough so it hands its carrier address directly to the
R1900's Ethernet WAN rather than NATing behind its own subnet.

The bonding scheduler decides how to stripe based on per-link loss and latency. An
extra NAT layer with its own buffers and session table distorts that signal, so
passthrough is the mode to build on.

- [ ] CBA1250 set to IP passthrough
- [ ] R1900's Ethernet WAN shows a carrier address, not an RFC1918 address from
      the CBA1250

## Step 2 — Ethernet port as a WAN on the R1900

- [ ] Ethernet port configured as a WAN device, not a LAN switch member
- [ ] Both WAN devices — internal 5G modem and Ethernet — show connected
- [ ] Both devices registered in NetCloud Manager, in a group you control

## Step 3 — Active connection monitoring on both WANs

Required for bonding, not just failover. The Ethernet link between the CBA1250 and
the R1900 stays physically up when the CBA1250's **cellular** side drops. Without
an active probe the router still counts that WAN as healthy, the scheduler keeps
striping packets into it, and the resequencer at the gateway stalls waiting for
packets that will never arrive. That stalls the whole bonded flow — worse than
simply losing a link.

- [ ] Active probe (ping or DNS) on the Ethernet WAN
- [ ] Active probe on the internal 5G WAN
- [ ] Probe targets off-net — not the CBA1250, not the R1900
- [ ] **A different target per WAN**, so one target going down cannot fail both
- [ ] Thresholds tuned to catch a dead link quickly without flapping on normal
      cellular jitter

## Step 4 — Path MTU per carrier

Measure before tunnel headers exist. A PMTU black hole under a bonded overlay
presents as "most things work, some transfers hang" — expensive to attribute
later.

```bash
ping -M do -s 1472 -c 3 1.1.1.1     # Linux  (payload 1472 => 1500 MTU)
ping -D  -s 1472 -c 3 1.1.1.1       # macOS
# Walk the payload down until it stops failing. PMTU = payload + 28.
```

Run it once per link, forcing traffic out one WAN at a time.

- [ ] 5G path PMTU: ________
- [ ] LTE path PMTU: ________
- [ ] Lower of the two recorded — that is the ceiling before encapsulation
- [ ] Tunnel MTU set explicitly and MSS clamped once the overlay is up

## Step 5 — Baseline each link alone

These numbers are the only way to later prove bonding is working. Take them before
the overlay exists. Disable the other WAN rather than deprioritizing it.

```bash
iperf3 -c <server> -t 60 -P 1        # single stream — the number that matters
iperf3 -c <server> -t 60 -P 1 -R     # downlink
ping  -c 100 <server>                # RTT and jitter
```

| Link | `-P 1` up | `-P 1` down | RTT avg | jitter | loss |
|---|---|---|---|---|---|
| 5G (R1900 internal) | | | | | |
| LTE (CBA1250) | | | | | |

- [ ] Both links baselined
- [ ] **Arithmetic sum recorded: ________** — the target to beat in Step 8

## Step 6 — Entitlement and Service Gateway

- [ ] **SD-WAN / Intelligent Bonding entitlement confirmed on the NetCloud
      account.** Verify with your Cradlepoint rep before build day — this is
      licensed separately and is the most common thing to find missing.
- [ ] Gateway deployment chosen: Ericsson cloud-hosted, or a self-hosted virtual
      instance in your own compute
- [ ] Gateway sized and uplinked **above** the arithmetic sum from Step 5, with
      headroom. The gateway caps the whole site; sizing it at the sum means it
      becomes the new bottleneck.
- [ ] Gateway reachable from both carriers
- [ ] Added RTT through the gateway measured. All bonded traffic hairpins through
      it, so a distant gateway raises latency on everything. Closer is faster.

Both carriers are almost certainly CGNAT, which is fine — the router initiates the
tunnels outbound. A useful side effect: the gateway gives you one stable egress
address instead of two carrier-assigned ones.

## Step 7 — Bonded WAN interface

1. Build the SD-WAN overlay from the R1900 to the Service Gateway.
2. Create a **bonded WAN interface** whose members are the two WAN devices: the
   internal 5G modem and the Ethernet WAN fed by the CBA1250.
3. Set the policy to **bandwidth aggregation as the default for all traffic.**
   This is the setting that produces one fast connection. Per-class policies
   (duplication, balancing) split traffic across different behaviors — useful for
   resiliency tuning, not for this goal. Aggregate everything, then carve out
   exceptions later only if you find a reason.
4. Confirm graceful degradation: with one member down the interface should fall
   back to the surviving link, not fail closed.

- [ ] Bonded interface up, both members active
- [ ] Aggregation set as default policy for all traffic
- [ ] Each member disabled in turn — connection survives, throughput degrades

## Step 8 — Verify aggregation is real

One test decides it: **single-stream throughput must exceed your better single
link.** Everything else can be satisfied by ordinary load balancing.

```bash
iperf3 -c <server> -t 60 -P 1        # THE test — must beat the best Step 5 number
iperf3 -c <server> -t 60 -P 1 -R     # downlink; schedulers often differ
iperf3 -c <server> -t 60 -P 8        # reference only — passes without bonding
ping  -c 100 <server>                # resequencing cost vs. Step 5
```

- [ ] `-P 1` uplink exceeds the better single link ⇒ **aggregation confirmed**
- [ ] `-P 1` downlink likewise
- [ ] Result compared against the Step 5 arithmetic sum
- [ ] Added RTT and jitter recorded

### What to expect

- **80–90% of the arithmetic sum**, not 100%. Encapsulation, keepalives, and
  resequencing all take a cut. Budget that before promising anyone "2×".
- **Added latency** from the resequencing buffer plus the gateway hairpin. This is
  the inherent cost of bonding; if a workload cares more about latency than
  throughput, exclude it from the bonded path.
- **Throughput will vary by time of day.** The two carriers congest independently.
  Re-test at a second time of day before treating any number as the spec.

### If `-P 1` stays capped at one link's speed

Aggregation is not actually engaged. In rough order of likelihood:

1. Entitlement missing or not applied to this device group — the overlay comes up
   and silently behaves as load balancing.
2. Policy set to balancing rather than aggregation.
3. One member not actually in the bonded interface (check WAN device names).
4. MTU/MSS wrong — the tunnel is up but large packets are dropping, so the
   scheduler can't fill both links.
5. A member link is unhealthy and the scheduler has excluded it. Check per-link
   state at the gateway, not just at the router.
6. Router forwarding limit: overlay traffic is encrypted, and a router's rated
   plaintext throughput is higher than its rated IPsec throughput. If both links
   sum above the R1900's encrypted rating, that rating is the cap.
