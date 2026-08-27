"""Bench harness for validating Cradlepoint WAN bonding.

The question this tool exists to answer: is the bonded interface actually
aggregating, or is it just load balancing? Only one measurement decides that --
single-stream throughput above the best individual link -- so the harness is
built around capturing per-link baselines and comparing a bonded run to them.
"""

__version__ = "0.1.0"
