"""Markdown report rendering."""

from __future__ import annotations

from .store import Session
from .steps import STEPS, progress
from .verdict import Analysis, analyse

VERDICT_LINE = {
    "AGGREGATING": "**AGGREGATING** -- bonding is working.",
    "NOT_AGGREGATING": "**NOT AGGREGATING** -- behaving as load balancing.",
    "PENDING_BONDED": "**INCOMPLETE** -- baselines recorded, bonded run outstanding.",
    "NO_BASELINE": "**INCOMPLETE** -- per-link baselines missing.",
}


def _fmt(value: float | None, suffix: str = "") -> str:
    return f"{value:.1f}{suffix}" if isinstance(value, (int, float)) else "--"


def render(session: Session, analysis: Analysis | None = None) -> str:
    a = analysis or analyse(session)
    done, total = progress(session)
    out: list[str] = []

    out.append("# Cradlepoint Bonding Results — R1900 + CBA1250")
    out.append("")
    out.append(f"- Session created: {session.created}")
    out.append(f"- iperf3 server: `{session.server or 'not set'}`")
    out.append(f"- Test duration: {session.duration:g}s per run")
    out.append(f"- Steps complete: {done}/{total}")
    out.append("")
    out.append(f"## Verdict: {VERDICT_LINE.get(a.verdict, a.verdict)}")
    out.append("")

    out.append("| Metric | Value |")
    out.append("|---|---|")
    out.append(f"| Best single link | {_fmt(a.best_single_link, ' Mbps')}"
               f"{f' ({a.best_link_name})' if a.best_link_name else ''} |")
    out.append(f"| Arithmetic sum of links | {_fmt(a.arithmetic_sum, ' Mbps')} |")
    out.append(f"| Bonded, single stream | {_fmt(a.bonded_single, ' Mbps')} |")
    out.append(f"| Bonded, multi stream | {_fmt(a.bonded_multi, ' Mbps')} |")
    ratio = f"{a.ratio_vs_best_link:.2f}x" if a.ratio_vs_best_link else "--"
    out.append(f"| Bonded vs. best link | {ratio} |")
    eff = f"{a.efficiency * 100:.0f}%" if a.efficiency else "--"
    out.append(f"| Efficiency vs. sum | {eff} |")
    out.append("")

    if a.findings:
        out.append("## Findings")
        out.append("")
        for f in a.findings:
            out.append(f"- {f}")
        out.append("")

    out.append("## Per-link baselines")
    out.append("")
    out.append("| Link | Single-stream up | Single-stream down | RTT avg | Jitter | Loss | PMTU |")
    out.append("|---|---|---|---|---|---|---|")
    for label in session.links():
        for s in session.samples_for(label):
            up = next((t.up_mbps for t in s.throughput
                       if t.streams == 1 and not t.reverse), None)
            down = next((t.down_mbps for t in s.throughput
                         if t.streams == 1 and t.reverse), None)
            lat = s.latency
            out.append(
                f"| {label} | {_fmt(up, ' Mbps')} | {_fmt(down, ' Mbps')} | "
                f"{_fmt(lat.avg_ms if lat else None, ' ms')} | "
                f"{_fmt(lat.jitter_ms if lat else None, ' ms')} | "
                f"{_fmt(lat.loss_pct if lat else None, '%')} | "
                f"{s.pmtu or '--'} |"
            )
    out.append("")

    bonded = session.bonded_samples()
    if bonded:
        out.append("## Bonded runs")
        out.append("")
        out.append("| Timestamp | Streams | Direction | Mbps | Retransmits |")
        out.append("|---|---|---|---|---|")
        for s in bonded:
            for t in s.throughput:
                direction = "down" if t.reverse else "up"
                out.append(
                    f"| {s.timestamp} | {t.streams} | {direction} | "
                    f"{_fmt(t.primary_mbps)} | {t.retransmits if t.retransmits is not None else '--'} |"
                )
        out.append("")

    out.append("## Step checklist")
    out.append("")
    for step in STEPS:
        box = "x" if step.is_done(session) else " "
        out.append(f"- [{box}] `{step.key}` — {step.title}")
    out.append("")

    if session.notes:
        out.append("## Notes")
        out.append("")
        out.append(session.notes)
        out.append("")

    out.append("---")
    out.append("")
    out.append("Single-stream throughput above the best individual link is the only "
               "evidence of per-packet aggregation. Multi-stream figures rise under "
               "plain per-flow load balancing too, so they cannot confirm bonding.")
    return "\n".join(out) + "\n"
