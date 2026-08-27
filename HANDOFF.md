# Handoff — Cradlepoint Bonding Bench Validation

You are picking this up cold. Everything you need is here.

**The job:** bond two SIMs (Cradlepoint R1900 5G + CBA1250 LTE, different
carriers) into one connection whose throughput is the sum of both, and prove it.

**The one thing to understand before you start:** load balancing and bonding are
different. Cradlepoint's on-device WAN load balancing distributes whole *flows*
across links, so a single transfer never exceeds one modem. Real bonding stripes
one flow's *packets* across both, which means a concentrator on the public side
has to resequence them — the NCX Service Gateway. Two devices on a bench cannot
bond by themselves. If you take away one thing: **the only proof of bonding is
single-stream throughput above the best individual link.** Parallel-stream
numbers rise under plain load balancing too, so they prove nothing.

Background: [docs/cradlepoint-bonding.md](docs/cradlepoint-bonding.md).
Full procedure: [docs/bonding-setup.md](docs/bonding-setup.md).

---

## Step 1 — Install the harness

```bash
git clone <this repo> && cd claude-test
pip install -e .
cpbond --version
```

Requires Python 3.10+, plus `iperf3` and `ping` on the machine you test from
(`apt install iperf3` / `brew install iperf3`). No Python dependencies.

## Step 2 — Get an iperf3 server

You need a server **off-net** with more headroom than both carriers combined.

```bash
# On a VPS with a fast uplink:
iperf3 -s
```

A server slower than the links under test caps every measurement and looks
exactly like a failed bond. This trips people up — verify the server can outrun
your expected sum before trusting any number.

## Step 3 — Let the tool drive

```bash
cpbond init --server <your-iperf3-host> --duration 60
```

From here, **run `cpbond next` whenever you don't know what to do.** It prints the
physical action to take on the hardware and the exact command to run afterwards.
`cpbond status` shows the whole checklist and the current verdict.

The session is stored in `cpbond-session.json` in your working directory. Commit
it or copy it if you hand off again — it holds the baselines, which cannot be
re-measured once the overlay is up.

## Step 4 — Work the steps

The order matters. Abbreviated; `cpbond next` gives the full text:

| # | Step | What you do |
|---|---|---|
| 1 | `init` | Set the iperf3 server |
| 2 | `passthrough` | CBA1250 → IP passthrough |
| 3 | `wan-setup` | R1900 Ethernet port → WAN; both devices in NCM |
| 4 | `probes` | **Active probes on both WANs, different target each** |
| 5 | `baseline-5g` | Disable Ethernet WAN, measure 5G alone |
| 6 | `baseline-lte` | Disable 5G modem, measure LTE alone |
| 7 | `entitlement` | Confirm SD-WAN licence + provision Service Gateway |
| 8 | `bonded-config` | Bonded interface, **aggregation** as default policy |
| 9 | `bonded` | Measure with both links up → verdict |
| 10 | `failover` | Disable each member in turn, confirm survival |
| 11 | `report` | `cpbond report -o bonding-results.md` |

### Three places this goes wrong

**Step 4, the probes.** The Ethernet cable between the CBA1250 and the R1900 stays
electrically up when the CBA1250's *cellular* side drops. Without an active probe
to an off-net target, the router believes that WAN is healthy, keeps striping
packets into it, and the gateway's resequencer stalls waiting for packets that
will never arrive — stalling the entire bonded flow. Use a **different** target
per WAN, or one target going down fails both links at once.

**Steps 5 and 6, the baselines.** Take them before the overlay exists. Disable the
other WAN — don't just lower its priority, or you're measuring both. These two
numbers are the target the bond has to beat, and there is no way to recover them
afterwards. `cpbond bonded` refuses to run without them, deliberately.

**Step 7, the entitlement.** SD-WAN / Intelligent Bonding is licensed separately
and must be applied to *this device group*. When it's missing the failure is
quiet: the overlay comes up and behaves as load balancing, and you'll spend a day
chasing MTU. Confirm with the Cradlepoint rep before build day.

## Step 5 — Read the verdict

```bash
cpbond report -o bonding-results.md
```

| Verdict | Meaning |
|---|---|
| `AGGREGATING` | Single-stream beat the best link. Bonding works. |
| `NOT_AGGREGATING` | Single-stream capped at one link. Report lists causes in order. |
| `PENDING_BONDED` | Baselines in place, bonded run outstanding. |
| `NO_BASELINE` | Fewer than two links baselined. Do steps 5–6. |

Expect **80–90% of the arithmetic sum**, not 100% — encapsulation, keepalives and
resequencing take a cut. Below ~80% while still aggregating usually means MTU/MSS
or one member carrying less than its share; the report says so.

Also expect **added latency** (resequencing buffer plus the gateway hairpin), and
**variation by time of day** — the two carriers congest independently, so re-test
at a second time before treating any figure as the spec.

## Reference

```bash
cpbond next                     # what to do now — start here
cpbond status                   # full checklist + verdict
cpbond baseline 5g --pmtu-target 1.1.1.1
cpbond bonded                   # both links up; exits non-zero if not aggregating
cpbond confirm probes --note "probed 1.1.1.1 and 9.9.9.9"
cpbond report -o results.md
cpbond show                     # verdict only
```

`cpbond bonded` exits non-zero on `NOT_AGGREGATING`, so it can gate a CI job or a
sign-off script.

## Open items for whoever takes this

- [ ] NCM field names in `docs/bonding-setup.md` were written from the vendor
      docs' structure, not transcribed — the docs domain was unreachable from the
      environment this was authored in. Verify against
      [Creating a Bonded WAN Interface](https://docs.cradlepoint.com/r/Configuring-NetCloud-Exchange-SD-WAN/Creating-a-Bonded-WAN-Interface)
      and the
      [Validated Design for Mobile Using Intelligent Bonding](https://docs.cradlepoint.com/r/NetCloud-Validated-Design-for-Mobile-Using-Intelligent-Bonding-for-Resiliency/NCX-Service-Gateway-Configuration).
- [ ] Confirm the R1900's rated **encrypted** throughput exceeds the arithmetic
      sum of both carriers. A router's plaintext rating is higher than its
      IPsec/SD-WAN rating; if the sum exceeds it, the router is the ceiling and no
      bonding policy changes that.
- [ ] `cpbond` measures; it does not configure. Link enable/disable between
      baselines is manual in NCM. An NCM API driver could automate it — not built.
- [ ] The live `iperf3`/`ping` paths have not been exercised against real
      hardware. Parsing, PMTU search and verdict logic are unit-tested (37 tests,
      `python3 -m pytest tests/ -q`), but the first bench run is also the first
      real exercise of the subprocess layer. Budget a few minutes for that.
