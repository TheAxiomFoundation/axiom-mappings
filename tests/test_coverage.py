"""The coverage map says where every PolicyEngine variable stands, per door, and what blocks it.

`readiness` answers "may this program serve", one row per program. Coverage answers
"where is this variable", one row per variable, which is what a dashboard joins
against partner demand. Door 1 is PolicyEngine with policyengine-axiom underneath
(`cut` bindings); Door 2 is axiom-api's compat endpoint (`facts` bindings).

None of these tests reads policyengine-axiom's ledger from disk: CI has no such
checkout, so the ledger each test needs is built here.
"""

import datetime
import json

import pytest

from axiom_mappings import load
from axiom_mappings.export.coverage import DOORS, SCHEMA, STAGES, coverage, main, window_state

AS_OF = datetime.date(2026, 11, 6)  # after the Colorado FY 2026 window closed on 2026-09-30
IN_WINDOW = datetime.date(2026, 6, 1)


@pytest.fixture(scope="module")
def m():
    return load("us")


def _declared_on(m):
    """(program, variable) for every cut binding whose status says on."""
    return [
        (program, block["pe_variable"])
        for (program, profile), bs in m.bindings.items()
        if profile == "cut"
        for block in bs.doc.get("bindings") or []
        if block.get("status") == "on"
    ]


def _ledger(m, certified=True):
    """A ledger, keyed as `agreement.read_ledger` keys it, that certifies every declared on."""
    entries = {}
    for program, variable in _declared_on(m):
        entries.setdefault(variable, {})[program.replace("/", "-")] = {
            "certified": certified,
            "passes": certified,
            "report_at": "2026-09-18T22:18:40+00:00",
        }
    return entries, {"path": "fixture"}


@pytest.fixture(scope="module")
def report(m):
    entries, source = _ledger(m)
    return coverage(m, ledger=entries, ledger_source=source, as_of=AS_OF)


def _row(report, name):
    return next(v for v in report["variables"] if v["policyengine"] == name)


def test_every_variable_has_a_stage_per_door(report):
    assert report["schema"] == SCHEMA == "axiom_mappings.coverage.v2"
    assert report["variables"], "the map knows variables"
    for v in report["variables"]:
        assert v["stage"] in STAGES
        assert set(v["doors"]) == set(DOORS)
        assert all(v["doors"][d]["stage"] in STAGES for d in DOORS)
    n = report["totals"]["variables"]
    assert n == len(report["variables"]) == sum(report["totals"]["by_stage"].values())
    for door in DOORS:
        assert sum(report["totals"]["by_door"][door].values()) == n


def test_the_stage_is_the_furthest_either_door_reached(report):
    for v in report["variables"]:
        furthest = max((v["doors"][d]["stage"] for d in DOORS), key=STAGES.index)
        assert STAGES.index(v["stage"]) >= STAGES.index(furthest)
        if v["bindings"]:
            assert v["stage"] == furthest
    # A rule exists for far more variables than anyone has tried to bind. That
    # gap is the point of the map, so it must be visible, not rounded away.
    assert report["totals"]["by_stage"]["mapped"] > report["totals"]["by_stage"]["on"]


def test_door_2_serves_what_the_facts_bindings_name(report):
    """The variables axiom-api serves read `on` for Door 2, not `mapped` (the v1 export missed them)."""
    for name in ("snap", "standard_deduction", "additional_medicare_tax", "net_investment_income_tax"):
        door = _row(report, name)["doors"]["axiom_api"]
        assert door["stage"] == "on", name
        assert door["kind"] == "rule", name
    for name in ("age", "state_code", "state_code_str"):
        door = _row(report, name)["doors"]["axiom_api"]
        assert door["stage"] == "on" and door["kind"] == "echo", name
    # A Door 1 binding is not a Door 2 answer.
    assert _row(report, "ctc_maximum")["doors"]["axiom_api"]["stage"] != "on"


def test_a_certified_binding_inside_its_window_is_on(m, report):
    on = [v for v in report["variables"] if v["doors"]["policyengine"]["stage"] == "on"]
    assert on, "the fixture ledger certifies the declared bindings"
    for v in on:
        door = v["doors"]["policyengine"]
        assert not door["overclaimed"]
        assert any(b["certified"] and b["stage"] == "on" for b in door["bindings"])
        assert all(b["window_state"] in (None, "open") for b in door["bindings"] if b["stage"] == "on")


def test_on_needs_the_ledger(m):
    """With no ledger read, a status of on is a claim with no evidence: it runs as shadow, overclaimed."""
    report = coverage(m, as_of=AS_OF)
    assert report["evidence"]["policyengine_axiom_ledger"].get("error")
    assert report["totals"]["by_door"]["policyengine"]["on"] == 0
    claimed = _row(report, "ctc_maximum")["doors"]["policyengine"]
    assert claimed["declared"] == "on" and claimed["stage"] == "shadow" and claimed["overclaimed"]
    assert any("no policyengine-axiom ledger" in r for r in claimed["blocked_by"])


def test_an_uncertified_ledger_entry_is_shadow(m):
    entries, source = _ledger(m, certified=False)
    report = coverage(m, ledger=entries, ledger_source=source, as_of=AS_OF)
    door = _row(report, "ctc_maximum")["doors"]["policyengine"]
    assert door["stage"] == "shadow" and door["overclaimed"]
    assert any("does not certify" in r for r in door["blocked_by"])


def test_a_closed_window_answers_nothing(m):
    """Colorado SNAP FY 2026 closed on 2026-09-30: after it, PE answers and nothing is compared (D40, D50)."""
    colorado = [v for p, v in _declared_on(m) if p == "us-co/snap-fy2026"]
    assert colorado, "the Colorado program declares bindings on"
    entries, source = _ledger(m)
    after = coverage(m, ledger=entries, ledger_source=source, as_of=AS_OF)
    during = coverage(m, ledger=entries, ledger_source=source, as_of=IN_WINDOW)
    for name in colorado:
        closed = next(b for b in _row(after, name)["doors"]["policyengine"]["bindings"]
                      if b["program"] == "us-co/snap-fy2026")
        assert closed["window_state"] == "expired" and closed["stage"] == "bound"
        assert any("time window closed on 2026-09-30" in r for r in _row(after, name)["blocked_by"])
        opened = next(b for b in _row(during, name)["doors"]["policyengine"]["bindings"]
                      if b["program"] == "us-co/snap-fy2026")
        assert opened["window_state"] == "open" and opened["stage"] == "on"
    assert after["totals"]["overclaimed"] >= len(colorado)


def test_window_state():
    window = ["2025-10-01", "2026-09-30"]
    assert window_state(None, AS_OF) is None
    assert window_state(window, datetime.date(2025, 9, 30)) == "future"
    assert window_state(window, datetime.date(2026, 9, 30)) == "open"
    assert window_state(window, datetime.date(2026, 10, 1)) == "expired"


def test_a_mapped_variable_names_the_axiom_rule(report):
    mapped = [v for v in report["variables"] if v["stage"] == "mapped"]
    assert mapped, "some variables have a rule but no binding"
    assert all(v["axiom"] for v in mapped), "a mapped variable names the rule that would answer it"
    assert all(not v["bindings"] for v in mapped)


def test_a_blocking_finding_blocks_and_carries_its_issue(report):
    """The CTC refundable cap is a filed encoding defect, so it blocks and links the issue."""
    ctc = _row(report, "ctc_refundable_maximum")
    assert ctc["doors"]["policyengine"]["stage"] == "shadow", "an unfixed encoding defect is not promoted"
    assert ctc["blocked_by"], "a blocking finding is reported"
    assert any("1343" in reason for reason in ctc["blocked_by"]), ctc["blocked_by"]


def test_a_promoted_variable_is_not_blocked_by_a_finding(report):
    on = [v for v in report["variables"] if v["doors"]["policyengine"]["stage"] == "on"]
    for v in on:
        assert not any("finding" in reason for reason in v["doors"]["policyengine"]["blocked_by"]), (
            f"{v['policyengine']} is on, so no finding may block it: {v['blocked_by']}"
        )


def test_an_unresolved_axiom_id_blocks(m, report):
    """An id the pinned corpus no longer resolves cannot be bound, however the map names it."""
    unresolved = [v for v in report["variables"] if any("does not resolve" in r for r in v["blocked_by"])]
    for v in unresolved:
        assert v["axiom"], "the blocked id is named"
        assert any(a in m.unresolved for a in v["axiom"])


def test_readiness_flows_into_the_rollup(m):
    """A program readiness judges not ready marks its variables blocked."""
    not_ready = {"us-co/snap-fy2026": {"ready": False}}
    report = coverage(m, readiness_by_program=not_ready, as_of=AS_OF)
    snap = [v for v in report["variables"] if "us-co/snap-fy2026" in v["programs"]]
    assert snap, "the Colorado program has variables"
    assert all(any("not ready" in r for r in v["blocked_by"]) for v in snap)
    rollup = next(p for p in report["programs"] if p["program"] == "us-co/snap-fy2026")
    assert rollup["ready"] is False
    assert rollup["variables"] == len(snap)
    assert rollup["window_state"] == "expired" and rollup["doors"] == ["policyengine"]


def test_unverified_readiness_is_an_unknown_not_a_blocker(m):
    """Without a slot catalog readiness can only say "unverified"; that is not a reason to stop serving."""
    unverified = {"us/federal-tax": {"ready": False, "blockers": [
        "facts: slot coverage unverified (give the program's slot catalog)"]}}
    report = coverage(m, readiness_by_program=unverified, as_of=AS_OF)
    row = _row(report, "standard_deduction")
    assert not any("us/federal-tax" in r for r in row["blocked_by"])
    assert row["unverified"] == ["program us/federal-tax: readiness unverified (no slot catalog given)"]

    refused = {"us/snap": {"ready": False, "blockers": [
        "facts: 1 slot(s) under the unacceptable presumption claimed-expense-qualifies",
        "facts: slot coverage unverified (give the program's slot catalog)"]}}
    report = coverage(m, readiness_by_program=refused, as_of=AS_OF)
    snap = _row(report, "snap")
    assert any("us/snap is not ready: facts: 1 slot(s) under the unacceptable presumption" in r
               for r in snap["doors"]["axiom_api"]["blocked_by"])
    assert not any("unverified" in r for r in snap["blocked_by"])


def test_the_cli_writes_json_a_consumer_can_read(tmp_path, capsys):
    out = tmp_path / "coverage.json"
    assert main(["--out", str(out), "--as-of", "2026-11-06"]) == 0
    report = json.loads(out.read_text())
    assert report["schema"] == SCHEMA
    assert report["as_of"] == "2026-11-06"
    assert report["map_release"].startswith("us-")
    assert report["totals"]["variables"] == len(report["variables"])
    assert "policyengine_axiom_ledger" in report["evidence"]


def test_the_cli_can_list_one_stage(capsys):
    assert main(["--stage", "on"]) == 0
    out = capsys.readouterr().out
    assert "map release" in out
    assert "axiom_api=on" in out, "the listing names each door's stage"
