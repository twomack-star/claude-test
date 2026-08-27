"""cpbond command line.

Designed to be picked up cold: `cpbond next` always says what to do next, both
on the hardware and at the shell.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .measure import Measurer, MeasurementError, Sample
from .report import render
from .runner import SubprocessRunner
from .steps import BY_KEY, STEPS, next_step, progress
from .store import DEFAULT_PATH, Session, load, save, utcnow
from .verdict import analyse

BOLD, DIM, RESET = "\033[1m", "\033[2m", "\033[0m"


def _c(text: str, code: str) -> str:
    return f"{code}{text}{RESET}" if sys.stdout.isatty() else text


def _print_step(step, session: Session, heading: str) -> None:
    done, total = progress(session)
    print()
    print(_c(f"{heading}  [{done}/{total} complete]", BOLD))
    print(_c(f"  step: {step.key}", DIM))
    print()
    print(f"  {_c(step.title, BOLD)}")
    if step.hardware:
        print()
        print("  On the hardware:")
        for line in step.hardware:
            print(f"    - {line}")
    if step.why:
        print()
        print(f"  Why: {step.why}")
    if step.command:
        print()
        print("  Then run:")
        print(f"    {_c(step.command, BOLD)}")
    print()


# -- commands ----------------------------------------------------------------


def cmd_init(args) -> int:
    session = load(args.session)
    session.server = args.server
    session.duration = args.duration
    session.mark("init")
    save(session, args.session)
    print(f"Session ready at {args.session}")
    print(f"  server:   {session.server}")
    print(f"  duration: {session.duration:g}s per run")
    return cmd_next(args)


def cmd_next(args) -> int:
    session = load(args.session)
    step = next_step(session)
    if step is None:
        print()
        print(_c("All steps complete.", BOLD))
        print("  Run: cpbond report -o bonding-results.md")
        print()
        return 0
    _print_step(step, session, "NEXT STEP")
    return 0


def cmd_status(args) -> int:
    session = load(args.session)
    done, total = progress(session)
    a = analyse(session)
    print()
    print(_c(f"cpbond session  ({done}/{total} steps)", BOLD))
    print(f"  server: {session.server or _c('not set — run cpbond init', DIM)}")
    print()
    upcoming = next_step(session)
    for step in STEPS:
        if step.is_done(session):
            mark, colour = "[x]", DIM
        elif upcoming and step.key == upcoming.key:
            mark, colour = "[>]", BOLD
        else:
            mark, colour = "[ ]", DIM
        print(f"  {_c(f'{mark} {step.key:<16} {step.title}', colour)}")
    print()
    print(f"  verdict: {_c(a.verdict, BOLD)}")
    for f in a.findings[:1]:
        print(f"           {f}")
    print()
    return 0


def _measure_set(m: Measurer, session: Session, label: str, kind: str,
                 multi_streams: int | None, pmtu_target: str | None) -> Sample:
    sample = Sample(label=label, kind=kind, timestamp=utcnow())
    server = session.server
    dur = session.duration

    print(f"  iperf3 single stream, uplink ({dur:g}s) ...", flush=True)
    sample.throughput.append(m.iperf3(server, dur, streams=1, reverse=False))

    print(f"  iperf3 single stream, downlink ({dur:g}s) ...", flush=True)
    sample.throughput.append(m.iperf3(server, dur, streams=1, reverse=True))

    if multi_streams:
        print(f"  iperf3 {multi_streams} streams, uplink ({dur:g}s) ...", flush=True)
        sample.throughput.append(
            m.iperf3(server, dur, streams=multi_streams, reverse=False)
        )

    print("  ping ...", flush=True)
    try:
        sample.latency = m.ping(pmtu_target or server, count=100)
    except MeasurementError as exc:
        print(f"    ping failed, continuing: {exc}", file=sys.stderr)

    if pmtu_target:
        print("  path MTU discovery ...", flush=True)
        sample.pmtu = m.discover_pmtu(pmtu_target)
        if sample.pmtu is None:
            print("    PMTU unknown (ICMP likely filtered) — measure from another "
                  "target before trusting MTU settings", file=sys.stderr)
    return sample


def _require_server(session: Session) -> bool:
    if not session.server:
        print("No iperf3 server set. Run: cpbond init --server <host>",
              file=sys.stderr)
        return False
    return True


def cmd_baseline(args) -> int:
    session = load(args.session)
    if not _require_server(session):
        return 2
    m = Measurer(SubprocessRunner())
    missing = m.preflight()
    if missing:
        print(f"Missing required tools: {', '.join(missing)}", file=sys.stderr)
        return 2

    print()
    print(_c(f"Baselining link '{args.label}' — confirm ONLY this link is active.", BOLD))
    print()
    try:
        sample = _measure_set(m, session, args.label, "link",
                              multi_streams=None, pmtu_target=args.pmtu_target)
    except MeasurementError as exc:
        print(f"Measurement failed: {exc}", file=sys.stderr)
        return 1

    sample.note = args.note or ""
    session.samples.append(sample)
    session.mark(f"baseline-{args.label}")
    save(session, args.session)

    single = sample.best_single_stream()
    print()
    print(f"  recorded: single-stream {single if single is not None else '--'} Mbps"
          f"{f', PMTU {sample.pmtu}' if sample.pmtu else ''}")
    return cmd_next(args)


def cmd_bonded(args) -> int:
    session = load(args.session)
    if not _require_server(session):
        return 2
    a = analyse(session)
    if len(a.link_bests) < 2:
        print("Refusing to run: fewer than two per-link baselines recorded.",
              file=sys.stderr)
        print("Baselines cannot be taken once the overlay is up, and without them "
              "a bonded number proves nothing.", file=sys.stderr)
        return 2

    m = Measurer(SubprocessRunner())
    missing = m.preflight()
    if missing:
        print(f"Missing required tools: {', '.join(missing)}", file=sys.stderr)
        return 2

    print()
    print(_c("Measuring the bonded interface — both WANs must be active.", BOLD))
    print(f"  target to beat: {a.best_single_link:.1f} Mbps "
          f"(best single link, {a.best_link_name})")
    print(f"  arithmetic sum: {a.arithmetic_sum:.1f} Mbps")
    print()
    try:
        sample = _measure_set(m, session, args.label, "bonded",
                              multi_streams=args.streams, pmtu_target=None)
    except MeasurementError as exc:
        print(f"Measurement failed: {exc}", file=sys.stderr)
        return 1

    sample.note = args.note or ""
    session.samples.append(sample)
    session.mark("bonded")
    save(session, args.session)

    result = analyse(session)
    print()
    print(_c(f"  VERDICT: {result.verdict}", BOLD))
    for f in result.findings:
        print(f"    - {f}")
    print()
    return 0 if result.verdict != "NOT_AGGREGATING" else 1


def cmd_confirm(args) -> int:
    session = load(args.session)
    if args.step not in BY_KEY:
        print(f"Unknown step '{args.step}'. Known steps: "
              f"{', '.join(BY_KEY)}", file=sys.stderr)
        return 2
    session.mark(args.step)
    if args.note:
        stamp = f"[{utcnow()}] {args.step}: {args.note}"
        session.notes = f"{session.notes}\n{stamp}".strip()
    save(session, args.session)
    print(f"Marked '{args.step}' complete.")
    return cmd_next(args)


def cmd_report(args) -> int:
    session = load(args.session)
    text = render(session)
    if args.output:
        Path(args.output).write_text(text)
        print(f"Wrote {args.output}")
    else:
        print(text)
    return 0


def cmd_show(args) -> int:
    a = analyse(load(args.session))
    print(a.verdict)
    for f in a.findings:
        print(f"  - {f}")
    return 0


# -- parser ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cpbond",
        description="Bench harness for validating Cradlepoint WAN bonding "
                    "(R1900 + CBA1250). Run 'cpbond next' at any time to see "
                    "what to do next.",
    )
    p.add_argument("--version", action="version", version=f"cpbond {__version__}")
    p.add_argument("--session", type=Path, default=DEFAULT_PATH,
                   help=f"session state file (default: {DEFAULT_PATH})")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="set the iperf3 server and start a session")
    s.add_argument("--server", required=True, help="iperf3 server hostname or IP")
    s.add_argument("--duration", type=float, default=60,
                   help="seconds per iperf3 run (default: 60)")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("next", help="show the next step to perform")
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("status", help="show all steps and the current verdict")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("baseline",
                       help="measure ONE link in isolation (other WAN disabled)")
    s.add_argument("label", help="link name, e.g. 5g or lte")
    s.add_argument("--pmtu-target", default=None,
                   help="host to probe for path MTU (e.g. 1.1.1.1)")
    s.add_argument("--note", default=None, help="free-text note for this sample")
    s.set_defaults(func=cmd_baseline)

    s = sub.add_parser("bonded", help="measure the bonded interface (both WANs up)")
    s.add_argument("--label", default="bonded")
    s.add_argument("--streams", type=int, default=8,
                   help="multi-stream count for reference (default: 8)")
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_bonded)

    s = sub.add_parser("confirm", help="mark a manual step complete")
    s.add_argument("step", help="step key, e.g. probes")
    s.add_argument("--note", default=None)
    s.set_defaults(func=cmd_confirm)

    s = sub.add_parser("report", help="render the markdown results report")
    s.add_argument("-o", "--output", default=None)
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("show", help="print just the verdict")
    s.set_defaults(func=cmd_show)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
