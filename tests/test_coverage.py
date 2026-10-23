"""The coverage map says where every PolicyEngine variable stands, and what blocks it.

`readiness` answers "may this program serve", one row per program. Coverage answers
"where is this variable", one row per variable, which is what a dashboard joins
against partner demand.
"""

import json

import pytest

from axiom_mappings import load
from axiom_mappings.export.coverage import STAGES, coverage, main


@pytest.fixture(scope="module")
def m():
    return load("us")


@pytest.fixture(scope="module")
def report(m):
    return coverage(m)


def test_every_variable_has_a_stage_from_the_ladder(report):
    assert report["schema"] == "axiom_mappings.coverage.v1"
    assert report["variables"], "the map knows variables"
    assert {v["stage"] for v in report["variables"]} <= set(STAGES)
    counted = sum(report["totals"]["by_stage"].values())
    assert counted == report["totals"]["variables"] == len(report["variables"])


def test_the_stage_is_the_furthest_a_variable_reached(m, report):
    """A variable bound in two programs takes the furthest status, not the first."""
    by_name = {v["policyengine"]: v for v in report["variables"]}
    for v in report["variables"]:
        statuses = {b["status"] for b in v["bindings"]}
        if "on" in statuses:
            assert v["stage"] == "on"
        elif "shadow" in statuses:
            assert v["stage"] == "shadow"
        elif v["bindings"]:
            assert v["stage"] == "bound"
        else:
            assert v["stage"] == "mapped"
    # A rule exists for far more variables than anyone has tried to bind. That
    # gap is the point of the map, so it must be visible, not rounded away.
    assert report["totals"]["by_stage"]["mapped"] > report["totals"]["by_stage"]["on"]


def test_a_mapped_variable_names_the_axiom_rule(report):
    mapped = [v for v in report["variables"] if v["stage"] == "mapped"]
    assert mapped, "some variables have a rule but no binding"
    assert all(v["axiom"] for v in mapped), "a mapped variable names the rule that would answer it"
    assert all(not v["bindings"] for v in mapped)


def test_a_blocking_finding_blocks_and_carries_its_issue(m, report):
    """The CTC refundable cap is a filed encoding defect, so it blocks and links the issue."""
    ctc = next(v for v in report["variables"] if v["policyengine"] == "ctc_refundable_maximum")
    assert ctc["stage"] == "shadow", "an unfixed encoding defect is not promoted"
    assert ctc["blocked_by"], "a blocking finding is reported"
    assert any("1343" in reason for reason in ctc["blocked_by"]), ctc["blocked_by"]


def test_a_promoted_variable_is_not_blocked(report):
    on = [v for v in report["variables"] if v["stage"] == "on"]
    assert on, "some bindings are promoted"
    for v in on:
        assert not any("finding" in reason for reason in v["blocked_by"]), (
            f"{v['policyengine']} is on, so no finding may block it: {v['blocked_by']}"
        )


def test_an_unresolved_axiom_id_blocks(m):
    """An id the pinned corpus no longer resolves cannot be bound, however the map names it."""
    report = coverage(m)
    unresolved = [v for v in report["variables"] if any("does not resolve" in r for r in v["blocked_by"])]
    for v in unresolved:
        assert v["axiom"], "the blocked id is named"
        assert any(a in m.unresolved for a in v["axiom"])


def test_readiness_flows_into_the_rollup(m):
    """A program readiness judges not ready marks its variables blocked."""
    not_ready = {"us-co/snap-fy2026": {"ready": False}}
    report = coverage(m, readiness_by_program=not_ready)
    snap = [v for v in report["variables"] if "us-co/snap-fy2026" in v["programs"]]
    assert snap, "the Colorado program has variables"
    assert all(any("not ready" in r for r in v["blocked_by"]) for v in snap)
    rollup = next(p for p in report["programs"] if p["program"] == "us-co/snap-fy2026")
    assert rollup["ready"] is False
    assert rollup["variables"] == len(snap)


def test_the_cli_writes_json_a_consumer_can_read(tmp_path, capsys):
    out = tmp_path / "coverage.json"
    assert main(["--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["schema"] == "axiom_mappings.coverage.v1"
    assert report["map_release"].startswith("us-")
    assert report["totals"]["variables"] == len(report["variables"])


def test_the_cli_can_list_one_stage(capsys):
    assert main(["--stage", "on"]) == 0
    out = capsys.readouterr().out
    assert "map release" in out
    assert "on " in out, "the listing names the stage of each row"
