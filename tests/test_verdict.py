from cpbond.measure import Sample, Throughput
from cpbond.store import Session
from cpbond.verdict import AGGREGATION_MARGIN, analyse


def link(label, mbps, streams=1, reverse=False, kind="link"):
    return Sample(label=label, kind=kind, timestamp="t", throughput=[
        Throughput(up_mbps=mbps if not reverse else None,
                   down_mbps=mbps if reverse else None,
                   retransmits=0, streams=streams, duration=60, reverse=reverse)
    ])


def session_with(*samples):
    return Session(server="h", samples=list(samples))


def test_no_baseline_at_all():
    assert analyse(Session()).verdict == "NO_BASELINE"


def test_single_link_baseline_is_not_enough():
    a = analyse(session_with(link("5g", 100)))
    assert a.verdict == "NO_BASELINE"
    assert "Only one link baselined" in a.findings[0]


def test_pending_when_baselines_present_but_no_bonded_run():
    a = analyse(session_with(link("5g", 100), link("lte", 50)))
    assert a.verdict == "PENDING_BONDED"
    assert a.arithmetic_sum == 150
    assert a.best_single_link == 100
    assert a.best_link_name == "5g"


def test_aggregating_when_single_stream_beats_best_link():
    a = analyse(session_with(
        link("5g", 100), link("lte", 50),
        link("bonded", 135, kind="bonded"),
    ))
    assert a.verdict == "AGGREGATING"
    assert a.aggregating
    assert a.ratio_vs_best_link == 1.35
    assert a.efficiency == 0.9


def test_not_aggregating_when_capped_at_one_link():
    a = analyse(session_with(
        link("5g", 100), link("lte", 50),
        link("bonded", 101, kind="bonded"),
    ))
    assert a.verdict == "NOT_AGGREGATING"
    assert "load balancing looks like" in " ".join(a.findings)


def test_margin_boundary_is_not_aggregating_just_below():
    just_under = 100 * AGGREGATION_MARGIN - 0.5
    a = analyse(session_with(
        link("5g", 100), link("lte", 50),
        link("bonded", just_under, kind="bonded"),
    ))
    assert a.verdict == "NOT_AGGREGATING"


def test_margin_boundary_is_aggregating_at_threshold():
    a = analyse(session_with(
        link("5g", 100), link("lte", 50),
        link("bonded", 100 * AGGREGATION_MARGIN, kind="bonded"),
    ))
    assert a.verdict == "AGGREGATING"


def test_multi_stream_alone_does_not_prove_bonding():
    """The trap this tool exists to catch: high parallel throughput with
    single-stream pinned to one link is load balancing, not bonding."""
    bonded = Sample(label="bonded", kind="bonded", timestamp="t", throughput=[
        Throughput(up_mbps=98, down_mbps=None, retransmits=0, streams=1,
                   duration=60, reverse=False),
        Throughput(up_mbps=145, down_mbps=None, retransmits=0, streams=8,
                   duration=60, reverse=False),
    ])
    a = analyse(session_with(link("5g", 100), link("lte", 50), bonded))
    assert a.verdict == "NOT_AGGREGATING"
    assert a.bonded_multi == 145
    joined = " ".join(a.findings)
    assert "not evidence of bonding" in joined


def test_low_efficiency_flagged_even_when_aggregating():
    # Beats the best link, but well short of the sum.
    a = analyse(session_with(
        link("5g", 100), link("lte", 100),
        link("bonded", 125, kind="bonded"),
    ))
    assert a.verdict == "AGGREGATING"
    assert any("below the ~80%" in f for f in a.findings)


def test_best_of_repeated_runs_is_used():
    a = analyse(session_with(
        link("5g", 80), link("5g", 110), link("lte", 50),
        link("bonded", 140, kind="bonded"),
    ))
    assert a.best_single_link == 110
    assert a.arithmetic_sum == 160


def test_reverse_direction_counted():
    a = analyse(session_with(
        link("5g", 100, reverse=True), link("lte", 60, reverse=True),
        link("bonded", 150, reverse=True, kind="bonded"),
    ))
    assert a.verdict == "AGGREGATING"
