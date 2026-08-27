import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


def iperf3_json(sent_mbps=None, recv_mbps=None, retransmits=3, error=None):
    if error:
        return json.dumps({"error": error})
    return json.dumps({
        "end": {
            "sum_sent": {
                "bits_per_second": (sent_mbps or 0) * 1_000_000,
                "retransmits": retransmits,
            },
            "sum_received": {"bits_per_second": (recv_mbps or 0) * 1_000_000},
        }
    })


PING_LINUX = """PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data.
64 bytes from 1.1.1.1: icmp_seq=1 ttl=57 time=24.3 ms

--- 1.1.1.1 ping statistics ---
100 packets transmitted, 99 received, 1% packet loss, time 99148ms
rtt min/avg/max/mdev = 20.114/25.312/40.221/3.887 ms
"""

PING_MACOS = """PING 1.1.1.1 (1.1.1.1): 56 data bytes
64 bytes from 1.1.1.1: icmp_seq=0 ttl=57 time=24.300 ms

--- 1.1.1.1 ping statistics ---
100 packets transmitted, 100 packets received, 0.0% packet loss
round-trip min/avg/max/stddev = 20.114/25.312/40.221/3.887 ms
"""
