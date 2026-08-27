from conftest import PING_LINUX, PING_MACOS, iperf3_json

from cpbond.measure import (ICMP_IP_OVERHEAD, Measurer, MeasurementError,
                            Sample, Throughput, parse_iperf3, parse_ping)
from cpbond.runner import Completed, FakeRunner

import pytest


def test_parse_iperf3_forward_uses_sent_column():
    t = parse_iperf3(iperf3_json(sent_mbps=95.5, recv_mbps=94.0), 1, 60, reverse=False)
    assert t.up_mbps == 95.5
    assert t.primary_mbps == 95.5
    assert t.retransmits == 3


def test_parse_iperf3_reverse_uses_received_column():
    t = parse_iperf3(iperf3_json(sent_mbps=95.5, recv_mbps=180.0), 1, 60, reverse=True)
    assert t.primary_mbps == 180.0


def test_parse_iperf3_surfaces_inband_error():
    t = parse_iperf3(iperf3_json(error="unable to connect"), 1, 60, False)
    assert t.primary_mbps is None
    assert "unable to connect" in t.raw_error


def test_parse_iperf3_rejects_non_json():
    with pytest.raises(MeasurementError):
        parse_iperf3("iperf3: command not found", 1, 60, False)


@pytest.mark.parametrize("output", [PING_LINUX, PING_MACOS])
def test_parse_ping_both_platforms(output):
    lat = parse_ping(output)
    assert lat.avg_ms == 25.312
    assert lat.jitter_ms == 3.887
    assert lat.loss_pct in (1.0, 0.0)


def test_parse_ping_missing_summary_is_none_not_crash():
    lat = parse_ping("ping: unknown host")
    assert lat.avg_ms is None and lat.loss_pct is None


def test_iperf3_raises_on_empty_output():
    runner = FakeRunner({"iperf3": Completed(127, "", "not found")})
    with pytest.raises(MeasurementError):
        Measurer(runner).iperf3("host", 1)


def test_iperf3_reverse_flag_passed():
    runner = FakeRunner({"iperf3": Completed(0, iperf3_json(1, 2), "")})
    Measurer(runner).iperf3("host", 5, streams=1, reverse=True)
    assert "-R" in runner.calls[0]


def test_ping_bind_flag_differs_by_platform():
    resp = {"ping": Completed(0, PING_LINUX, "")}
    Measurer(FakeRunner(resp), system="Linux")  # sanity
    linux = FakeRunner(resp)
    Measurer(linux, system="Linux").ping("h", bind="eth0")
    assert "-I" in linux.calls[0]
    mac = FakeRunner(resp)
    Measurer(mac, system="Darwin").ping("h", bind="192.0.2.1")
    assert "-S" in mac.calls[0]


class PmtuRunner:
    """Accepts DF payloads up to a ceiling, mimicking a path MTU."""

    def __init__(self, ceiling):
        self.ceiling = ceiling

    def run(self, argv, timeout=None):
        payload = int(argv[argv.index("-s") + 1])
        if payload <= self.ceiling:
            return Completed(0, "64 bytes from 1.1.1.1: ttl=57 time=1 ms\n"
                                "0% packet loss\n", "")
        return Completed(1, "ping: local error: message too long\n"
                            "100% packet loss\n", "")

    def which(self, name):
        return f"/usr/bin/{name}"


def test_discover_pmtu_finds_boundary():
    # A 1430-byte payload ceiling means a 1458 path MTU.
    m = Measurer(PmtuRunner(1430))
    assert m.discover_pmtu("1.1.1.1", low=1200, high=1472) == 1430 + ICMP_IP_OVERHEAD


def test_discover_pmtu_full_size_path():
    m = Measurer(PmtuRunner(1472))
    assert m.discover_pmtu("1.1.1.1", low=1200, high=1472) == 1472 + ICMP_IP_OVERHEAD


def test_discover_pmtu_returns_none_when_icmp_filtered():
    # Nothing gets through, including the smallest probe: unknown, not tiny.
    m = Measurer(PmtuRunner(0))
    assert m.discover_pmtu("1.1.1.1") is None


def test_sample_best_single_stream_ignores_multi_stream():
    s = Sample(label="x", kind="link", timestamp="t", throughput=[
        Throughput(up_mbps=90, down_mbps=None, retransmits=0, streams=1,
                   duration=60, reverse=False),
        Throughput(up_mbps=400, down_mbps=None, retransmits=0, streams=8,
                   duration=60, reverse=False),
    ])
    assert s.best_single_stream() == 90
    assert s.best_multi_stream() == 400


def test_preflight_reports_missing_tools():
    m = Measurer(FakeRunner({}, available=["ping"]))
    assert m.preflight() == ["iperf3"]
