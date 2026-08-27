"""iperf3, ping, and path-MTU measurement with parsing."""

from __future__ import annotations

import json
import platform
import re
from dataclasses import dataclass, field, asdict
from typing import Sequence

from .runner import Runner, SubprocessRunner

BITS_PER_MBIT = 1_000_000
# DF-ping payload plus 8 bytes ICMP header plus 20 bytes IPv4 header.
ICMP_IP_OVERHEAD = 28


class MeasurementError(RuntimeError):
    pass


@dataclass
class Throughput:
    """One iperf3 run."""

    up_mbps: float | None
    down_mbps: float | None
    retransmits: int | None
    streams: int
    duration: float
    reverse: bool
    raw_error: str | None = None

    @property
    def primary_mbps(self) -> float | None:
        """The direction this run was actually measuring.

        iperf3 in reverse mode sends server->client, so the meaningful figure
        moves from the sent to the received column.
        """
        return self.down_mbps if self.reverse else self.up_mbps

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Latency:
    min_ms: float | None
    avg_ms: float | None
    max_ms: float | None
    jitter_ms: float | None
    loss_pct: float | None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sample:
    """A labelled measurement of one link, or of the bonded interface."""

    label: str
    kind: str  # "link" | "bonded"
    timestamp: str
    throughput: list[Throughput] = field(default_factory=list)
    latency: Latency | None = None
    pmtu: int | None = None
    note: str = ""

    def best_single_stream(self) -> float | None:
        """Highest single-stream figure in this sample.

        Single-stream is the only number that separates aggregation from load
        balancing, so multi-stream runs are deliberately excluded here.
        """
        values = [
            t.primary_mbps
            for t in self.throughput
            if t.streams == 1 and t.primary_mbps is not None
        ]
        return max(values) if values else None

    def best_multi_stream(self) -> float | None:
        values = [
            t.primary_mbps
            for t in self.throughput
            if t.streams > 1 and t.primary_mbps is not None
        ]
        return max(values) if values else None

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "throughput": [t.to_dict() for t in self.throughput],
            "latency": self.latency.to_dict() if self.latency else None,
            "pmtu": self.pmtu,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Sample":
        lat = d.get("latency")
        return cls(
            label=d["label"],
            kind=d.get("kind", "link"),
            timestamp=d.get("timestamp", ""),
            throughput=[Throughput(**t) for t in d.get("throughput", [])],
            latency=Latency(**lat) if lat else None,
            pmtu=d.get("pmtu"),
            note=d.get("note", ""),
        )


def parse_iperf3(stdout: str, streams: int, duration: float, reverse: bool) -> Throughput:
    """Parse iperf3 --json output.

    iperf3 reports errors inside its JSON body rather than only via exit code,
    so the error field is checked before the throughput columns.
    """
    try:
        doc = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise MeasurementError(f"iperf3 output was not JSON: {exc}") from exc

    if doc.get("error"):
        return Throughput(
            up_mbps=None,
            down_mbps=None,
            retransmits=None,
            streams=streams,
            duration=duration,
            reverse=reverse,
            raw_error=str(doc["error"]),
        )

    end = doc.get("end", {})
    sent = end.get("sum_sent") or {}
    received = end.get("sum_received") or {}

    def mbps(section: dict) -> float | None:
        bps = section.get("bits_per_second")
        return round(bps / BITS_PER_MBIT, 2) if isinstance(bps, (int, float)) else None

    retrans = sent.get("retransmits")
    return Throughput(
        up_mbps=mbps(sent),
        down_mbps=mbps(received),
        retransmits=int(retrans) if isinstance(retrans, (int, float)) else None,
        streams=streams,
        duration=duration,
        reverse=reverse,
    )


_PING_RTT = re.compile(
    r"(?:rtt|round-trip)\s+min/avg/max/(?:mdev|stddev)\s*=\s*"
    r"([\d.]+)/([\d.]+)/([\d.]+)/([\d.]+)"
)
_PING_LOSS = re.compile(r"([\d.]+)%\s+packet loss")


def parse_ping(stdout: str) -> Latency:
    """Parse the summary block of ping output (Linux and macOS wordings)."""
    rtt = _PING_RTT.search(stdout)
    loss = _PING_LOSS.search(stdout)
    if rtt:
        lo, avg, hi, dev = (float(g) for g in rtt.groups())
    else:
        lo = avg = hi = dev = None
    return Latency(
        min_ms=lo,
        avg_ms=avg,
        max_ms=hi,
        jitter_ms=dev,
        loss_pct=float(loss.group(1)) if loss else None,
    )


class Measurer:
    def __init__(self, runner: Runner | None = None, system: str | None = None):
        self.runner = runner or SubprocessRunner()
        self.system = system or platform.system()

    # -- iperf3 ---------------------------------------------------------------

    def iperf3(
        self,
        server: str,
        duration: float = 60,
        streams: int = 1,
        reverse: bool = False,
        bind: str | None = None,
        port: int | None = None,
    ) -> Throughput:
        argv: list[str] = ["iperf3", "-c", server, "-t", str(int(duration)),
                           "-P", str(streams), "--json"]
        if reverse:
            argv.append("-R")
        if bind:
            argv += ["-B", bind]
        if port:
            argv += ["-p", str(port)]

        # Allow generous headroom over the test duration; a saturated cellular
        # link plus connection setup can overshoot the nominal time.
        res = self.runner.run(argv, timeout=duration + 60)
        if not res.stdout.strip():
            raise MeasurementError(
                f"iperf3 produced no output (exit {res.returncode}): "
                f"{res.stderr.strip() or 'no stderr'}"
            )
        return parse_iperf3(res.stdout, streams, duration, reverse)

    # -- ping -----------------------------------------------------------------

    def ping(self, target: str, count: int = 100, bind: str | None = None) -> Latency:
        argv = ["ping", "-c", str(count), target]
        if bind:
            # -I on Linux takes an interface or address; macOS uses -S for source.
            argv[1:1] = ["-S", bind] if self.system == "Darwin" else ["-I", bind]
        res = self.runner.run(argv, timeout=count * 1.5 + 30)
        if not res.stdout.strip():
            raise MeasurementError(f"ping produced no output: {res.stderr.strip()}")
        return parse_ping(res.stdout)

    # -- path MTU -------------------------------------------------------------

    def _df_ping_fits(self, target: str, payload: int, bind: str | None) -> bool:
        """One DF-set probe. True if a packet of this payload got through."""
        if self.system == "Darwin":
            argv = ["ping", "-D", "-s", str(payload), "-c", "1", "-t", "3", target]
        else:
            argv = ["ping", "-M", "do", "-s", str(payload), "-c", "1", "-W", "3", target]
        if bind:
            argv[1:1] = ["-S", bind] if self.system == "Darwin" else ["-I", bind]
        res = self.runner.run(argv, timeout=15)
        if not res.ok:
            return False
        # A reply line is the positive signal; fragmentation-needed and 100%
        # loss both mean this payload did not fit.
        out = res.stdout.lower()
        if "too long" in out or "frag" in out or "message too long" in out:
            return False
        loss = _PING_LOSS.search(res.stdout)
        if loss and float(loss.group(1)) >= 100.0:
            return False
        return "bytes from" in out or "ttl=" in out

    def discover_pmtu(
        self,
        target: str,
        low: int = 1200,
        high: int = 1472,
        bind: str | None = None,
    ) -> int | None:
        """Binary-search the largest DF payload that traverses the path.

        Returns the path MTU (payload + IP/ICMP overhead), or None if even the
        smallest probe fails -- which usually means ICMP is filtered rather than
        that the MTU is tiny, so it is reported as unknown instead of guessed.
        """
        if not self._df_ping_fits(target, low, bind):
            return None
        if self._df_ping_fits(target, high, bind):
            return high + ICMP_IP_OVERHEAD

        lo, hi = low, high
        # Invariant: lo fits, hi does not. Converge to the boundary.
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self._df_ping_fits(target, mid, bind):
                lo = mid
            else:
                hi = mid
        return lo + ICMP_IP_OVERHEAD

    def preflight(self) -> list[str]:
        """Report missing external tools rather than failing mid-run."""
        missing = [tool for tool in ("iperf3", "ping") if not self.runner.which(tool)]
        return missing
