# claude-test — Cradlepoint bonding

Validating WAN bonding on a Cradlepoint R1900 + CBA1250 pair (two carriers, one
aggregated connection).

**Starting work on this? Read [HANDOFF.md](HANDOFF.md).**

| | |
|---|---|
| [HANDOFF.md](HANDOFF.md) | Step-by-step, written for someone starting cold |
| [docs/cradlepoint-bonding.md](docs/cradlepoint-bonding.md) | Background: bonding vs. load balancing, options, constraints |
| [docs/bonding-setup.md](docs/bonding-setup.md) | Full build procedure for the NetCloud Exchange SD-WAN path |
| `src/cpbond/` | `cpbond` — the bench harness |

## cpbond

Guides the bench work and decides whether bonding is actually aggregating.

```bash
pip install -e .
cpbond init --server <iperf3-host>
cpbond next          # says what to do next, at every stage
```

Needs `iperf3` and `ping`; no Python dependencies.

The verdict rests on one measurement: **single-stream throughput above the best
individual link.** Parallel-stream throughput rises under ordinary per-flow load
balancing, so it cannot distinguish bonding from balancing — which is the mistake
this tool exists to prevent.

```bash
python3 -m pytest tests/ -q     # 37 tests, no network or hardware needed
```
