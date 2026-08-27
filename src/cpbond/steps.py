"""The ordered procedure.

Encoded as data so `cpbond next` can walk an operator through the bench work
without them having to read the whole runbook first. Each step names the
physical action to take on the hardware and the command that records it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .store import Session

LINK_A = "5g"
LINK_B = "lte"


@dataclass(frozen=True)
class Step:
    key: str
    title: str
    hardware: list[str] = field(default_factory=list)
    command: str = ""
    why: str = ""
    manual: bool = False

    def is_done(self, session: Session) -> bool:
        return session.done(self.key)


STEPS: list[Step] = [
    Step(
        key="init",
        title="Record the iperf3 server and test duration",
        hardware=[
            "Stand up an iperf3 server somewhere off-net with more headroom than "
            "both carriers combined, or use one you trust.",
            "A server slower than the links under test will cap every number and "
            "look exactly like a failed bond.",
        ],
        command="cpbond init --server <host> --duration 60",
        why="Every later step measures against this server; changing it mid-session "
            "invalidates the comparison.",
    ),
    Step(
        key="passthrough",
        title="Put the CBA1250 in IP passthrough",
        hardware=[
            "Set the CBA1250 to IP passthrough (not router mode).",
            "Confirm the R1900's Ethernet WAN shows a carrier-assigned address, "
            "not an RFC1918 address handed out by the CBA1250.",
        ],
        command="cpbond confirm passthrough",
        why="The bonding scheduler stripes based on per-link loss and latency. An "
            "extra NAT layer with its own buffers distorts that signal.",
        manual=True,
    ),
    Step(
        key="wan-setup",
        title="Define the Ethernet port as a WAN and register both devices",
        hardware=[
            "R1900: Ethernet port configured as a WAN device, not a LAN switch member.",
            "Both WAN devices (internal 5G modem, Ethernet) show connected.",
            "Both devices registered in NetCloud Manager in a group you control.",
        ],
        command="cpbond confirm wan-setup",
        manual=True,
    ),
    Step(
        key="probes",
        title="Enable active connection monitoring on BOTH WANs",
        hardware=[
            "Active probe (ping or DNS) on the Ethernet WAN.",
            "Active probe on the internal 5G WAN.",
            "Targets must be off-net -- not the CBA1250, not the R1900.",
            "Use a DIFFERENT target per WAN, so one target going down cannot "
            "fail both links at once.",
        ],
        command="cpbond confirm probes",
        why="The Ethernet link to the CBA1250 stays up when its CELLULAR side drops. "
            "Without an active probe the router keeps striping into a dead link and "
            "the resequencer stalls waiting for packets that never arrive -- which "
            "stalls the whole bonded flow, worse than losing a link outright.",
        manual=True,
    ),
    Step(
        key="baseline-5g",
        title=f"Baseline the 5G link alone",
        hardware=[
            "On the R1900, DISABLE the Ethernet WAN. Do not merely deprioritise it.",
            "Leave only the internal 5G modem connected.",
            "Verify only one WAN is active before running the command.",
        ],
        command=f"cpbond baseline {LINK_A}",
        why="This single-stream number is half of the target the bond has to beat. "
            "It cannot be recovered once the overlay is up.",
    ),
    Step(
        key="baseline-lte",
        title="Baseline the LTE link (CBA1250) alone",
        hardware=[
            "Re-enable the Ethernet WAN and DISABLE the internal 5G modem.",
            "Verify only one WAN is active before running the command.",
        ],
        command=f"cpbond baseline {LINK_B}",
        why="The other half of the target. Together these give the arithmetic sum.",
    ),
    Step(
        key="entitlement",
        title="Confirm the SD-WAN / Intelligent Bonding entitlement",
        hardware=[
            "Verify with your Cradlepoint rep that the SD-WAN / Intelligent Bonding "
            "entitlement is active on the NetCloud account AND applied to this "
            "device group.",
            "Provision the NCX Service Gateway (Ericsson-hosted or a self-hosted "
            "virtual instance).",
            "Size the gateway uplink ABOVE the arithmetic sum from the baselines, "
            "with headroom -- sized at the sum, the gateway becomes the bottleneck.",
        ],
        command="cpbond confirm entitlement",
        why="Bonding cannot work without a concentrator on the public side of both "
            "links. Missing entitlement is the most common build-day surprise, and "
            "it fails quietly: the overlay comes up and behaves as load balancing.",
        manual=True,
    ),
    Step(
        key="bonded-config",
        title="Create the bonded WAN interface with aggregation as default",
        hardware=[
            "Re-enable both WANs. Confirm both show connected.",
            "Build the SD-WAN overlay from the R1900 to the Service Gateway.",
            "Create a bonded WAN interface with both members: internal 5G modem "
            "and the Ethernet WAN fed by the CBA1250.",
            "Set policy to BANDWIDTH AGGREGATION as the default for all traffic. "
            "Not balancing, not duplication.",
            "Set the tunnel MTU explicitly and clamp MSS using the PMTU figures "
            "from the baseline steps.",
        ],
        command="cpbond confirm bonded-config",
        why="Aggregation is the setting that produces one fast connection. Balancing "
            "distributes whole flows and will not raise single-stream speed.",
        manual=True,
    ),
    Step(
        key="bonded",
        title="Measure the bonded interface",
        hardware=[
            "Both WANs enabled, bonded interface up, both members active.",
            "Run from a client behind the R1900.",
        ],
        command="cpbond bonded",
        why="Single-stream throughput above the best single link is the only "
            "measurement that distinguishes aggregation from load balancing.",
    ),
    Step(
        key="failover",
        title="Verify graceful degradation",
        hardware=[
            "Start a long single-stream transfer, then disable one member.",
            "The connection should degrade to the surviving link, not drop.",
            "Repeat for the other member.",
            "Re-enable both when done.",
        ],
        command="cpbond confirm failover --note '<what happened>'",
        manual=True,
    ),
    Step(
        key="report",
        title="Generate the report",
        hardware=["Nothing on the hardware -- this reads the recorded session."],
        command="cpbond report -o bonding-results.md",
    ),
]

BY_KEY = {s.key: s for s in STEPS}


def next_step(session: Session) -> Step | None:
    for step in STEPS:
        if not step.is_done(session):
            return step
    return None


def progress(session: Session) -> tuple[int, int]:
    done = sum(1 for s in STEPS if s.is_done(session))
    return done, len(STEPS)
