"""The analysis that decides whether bonding is actually aggregating."""

from __future__ import annotations

from dataclasses import dataclass

from .store import Session

# Headroom over the best single link before a bonded result counts as
# aggregation. Cellular throughput varies run to run; without a margin, normal
# variance on a good 5G link reads as a successful bond.
AGGREGATION_MARGIN = 1.15

# Encapsulation, keepalives and resequencing all take a cut, so a healthy bond
# lands below the arithmetic sum. Under this fraction is working but suspect.
EXPECTED_EFFICIENCY = 0.80


@dataclass
class Analysis:
    link_bests: dict[str, float]
    best_single_link: float | None
    best_link_name: str | None
    arithmetic_sum: float | None
    bonded_single: float | None
    bonded_multi: float | None
    verdict: str
    efficiency: float | None
    ratio_vs_best_link: float | None
    findings: list[str]

    @property
    def aggregating(self) -> bool:
        return self.verdict == "AGGREGATING"


def analyse(session: Session) -> Analysis:
    link_bests: dict[str, float] = {}
    for label in session.links():
        values = [
            v
            for s in session.samples_for(label)
            if (v := s.best_single_stream()) is not None
        ]
        if values:
            link_bests[label] = max(values)

    best_link_name = max(link_bests, key=link_bests.__getitem__) if link_bests else None
    best_single_link = link_bests.get(best_link_name) if best_link_name else None
    arithmetic_sum = round(sum(link_bests.values()), 2) if link_bests else None

    bonded = session.bonded_samples()
    bonded_single = max(
        (v for s in bonded if (v := s.best_single_stream()) is not None),
        default=None,
    )
    bonded_multi = max(
        (v for s in bonded if (v := s.best_multi_stream()) is not None),
        default=None,
    )

    findings: list[str] = []
    efficiency = None
    ratio = None

    if not link_bests:
        verdict = "NO_BASELINE"
        findings.append(
            "No per-link single-stream baseline recorded. Without it there is "
            "nothing to compare a bonded run against."
        )
    elif len(link_bests) < 2:
        verdict = "NO_BASELINE"
        findings.append(
            f"Only one link baselined ({', '.join(link_bests)}). Baseline both "
            "links individually before testing the bond."
        )
    elif bonded_single is None:
        verdict = "PENDING_BONDED"
        findings.append(
            "Baselines are in place; no bonded single-stream run recorded yet."
        )
    else:
        ratio = round(bonded_single / best_single_link, 3) if best_single_link else None
        efficiency = (
            round(bonded_single / arithmetic_sum, 3)
            if arithmetic_sum
            else None
        )
        if ratio is not None and ratio >= AGGREGATION_MARGIN:
            verdict = "AGGREGATING"
            findings.append(
                f"Single-stream bonded throughput ({bonded_single:.1f} Mbps) exceeds "
                f"the best single link ({best_single_link:.1f} Mbps on "
                f"{best_link_name}) by {(ratio - 1) * 100:.0f}%. Only per-packet "
                "aggregation can do that."
            )
            if efficiency is not None and efficiency < EXPECTED_EFFICIENCY:
                findings.append(
                    f"Efficiency is {efficiency * 100:.0f}% of the arithmetic sum "
                    f"({arithmetic_sum:.1f} Mbps), below the ~{EXPECTED_EFFICIENCY * 100:.0f}% "
                    "a healthy bond reaches. Check MTU/MSS and per-link health at "
                    "the gateway -- one member may be carrying less than its share."
                )
        else:
            verdict = "NOT_AGGREGATING"
            findings.append(
                f"Single-stream bonded throughput ({bonded_single:.1f} Mbps) is not "
                f"meaningfully above the best single link ({best_single_link:.1f} Mbps "
                f"on {best_link_name}). This is what load balancing looks like: "
                "aggregation is not engaged."
            )
            if bonded_multi and best_single_link and bonded_multi > best_single_link * AGGREGATION_MARGIN:
                findings.append(
                    f"Multi-stream throughput ({bonded_multi:.1f} Mbps) does exceed one "
                    "link, which confirms both links carry traffic -- but multi-stream "
                    "passes with plain per-flow load balancing too, so it is not "
                    "evidence of bonding."
                )
            findings.append(
                "Check, in order: SD-WAN/Intelligent Bonding entitlement applied to "
                "this device group; policy set to aggregation rather than balancing; "
                "both WAN devices actually members of the bonded interface; MTU/MSS "
                "correct; neither member excluded as unhealthy at the gateway; and "
                "the router's rated encrypted throughput above the arithmetic sum."
            )

    return Analysis(
        link_bests=link_bests,
        best_single_link=best_single_link,
        best_link_name=best_link_name,
        arithmetic_sum=arithmetic_sum,
        bonded_single=bonded_single,
        bonded_multi=bonded_multi,
        verdict=verdict,
        efficiency=efficiency,
        ratio_vs_best_link=ratio,
        findings=findings,
    )
