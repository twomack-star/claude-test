import json

import pytest

from cpbond.measure import Sample, Throughput
from cpbond.report import render
from cpbond.steps import STEPS, next_step, progress
from cpbond.store import Session, load, save


def test_roundtrip_preserves_samples(tmp_path):
    path = tmp_path / "s.json"
    s = Session(server="host", duration=30)
    s.samples.append(Sample(label="5g", kind="link", timestamp="t", pmtu=1458,
                            throughput=[Throughput(90.5, None, 4, 1, 30, False)]))
    s.mark("init")
    save(s, path)

    back = load(path)
    assert back.server == "host"
    assert back.duration == 30
    assert back.completed == ["init"]
    assert back.samples[0].pmtu == 1458
    assert back.samples[0].throughput[0].up_mbps == 90.5
    assert back.samples[0].best_single_stream() == 90.5


def test_load_missing_file_gives_empty_session(tmp_path):
    assert load(tmp_path / "absent.json").samples == []


def test_save_is_atomic_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "s.json"
    save(Session(server="h"), path)
    save(Session(server="h2"), path)
    assert load(path).server == "h2"
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]


def test_future_schema_is_refused(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"schema": 99}))
    with pytest.raises(ValueError, match="newer than this cpbond"):
        load(path)


def test_mark_is_idempotent():
    s = Session()
    s.mark("probes")
    s.mark("probes")
    assert s.completed == ["probes"]


def test_steps_advance_in_order():
    s = Session()
    assert next_step(s).key == "init"
    for step in STEPS:
        s.mark(step.key)
    assert next_step(s) is None
    assert progress(s) == (len(STEPS), len(STEPS))


def test_every_step_has_a_command_and_title():
    for step in STEPS:
        assert step.title
        assert step.command, f"{step.key} has no command"


def test_step_keys_unique():
    keys = [s.key for s in STEPS]
    assert len(keys) == len(set(keys))


def test_baseline_step_keys_match_cli_marking():
    """cmd_baseline marks 'baseline-<label>', so those step keys must line up
    with the labels the steps instruct the operator to use."""
    keys = {s.key for s in STEPS}
    assert "baseline-5g" in keys and "baseline-lte" in keys
    for step in STEPS:
        if step.key.startswith("baseline-"):
            assert step.command == f"cpbond baseline {step.key.split('-', 1)[1]}"


def test_report_renders_incomplete_session():
    out = render(Session(server="h"))
    assert "INCOMPLETE" in out
    assert "Step checklist" in out


def test_report_renders_aggregating_verdict():
    s = Session(server="h")
    for label, mbps, kind in (("5g", 100, "link"), ("lte", 50, "link"),
                              ("bonded", 140, "bonded")):
        s.samples.append(Sample(label=label, kind=kind, timestamp="t", throughput=[
            Throughput(mbps, None, 0, 1, 60, False)]))
    out = render(s)
    assert "AGGREGATING" in out
    assert "140.0 Mbps" in out
    assert "150.0 Mbps" in out  # arithmetic sum
