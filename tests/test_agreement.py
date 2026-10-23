"""Two harnesses, one binding: the join must be scoped, and "attributed" is not "explained".

The bug these tests exist for: joined on the concept fragment alone, every
state's SNAP suite disputes the same federal `#snap_eligible`, so Alabama's 150
findings read as evidence about a Colorado binding. Scope is not a detail here
— without it the stage reports a difference that no one measured.
"""

import json

import pytest
import yaml

from axiom_mappings import load
from axiom_mappings.agreement import (
    FLAGGED,
    _in_scope,
    _suite_scope,
    _verdict,
    agreement,
    read_dispositions,
    read_ledger,
)

CO = "us-co/snap-fy2026"


@pytest.fixture(scope="module")
def m():
    return load("us")


def _sources(tmp_path, ledger_entries, disposition_suites):
    """A ledger and a dispositions directory in the real file shapes."""
    ledger = tmp_path / "ledger.json"
    ledger.write_text(json.dumps({"format": "test", "entries": ledger_entries}))
    root = tmp_path / "dispositions"
    root.mkdir()
    for suite, entries in disposition_suites.items():
        (root / f"{suite}.yaml").write_text(yaml.safe_dump({
            "schema": "axiom_oracles.dispositions.v1", "suite": suite, "entries": entries,
        }))
    return ledger, root


def _row(report, variable):
    return next(r for r in report["bindings"] if r["policyengine_variable"] == variable)


def test_another_states_suite_is_not_evidence_about_this_binding(m, tmp_path):
    """Alabama's SNAP finding and Colorado's binding dispute the same federal concept."""
    ledger, root = _sources(
        tmp_path,
        {"us-co-snap-fy2026:is_snap_eligible": {"passes": False, "tolerance_failures": 9}},
        {"al-snap-ecps": [{"id": "al-bbce", "concept": "us:statutes/7/2014/o#snap_eligible",
                           "disposition": "axiom_encoding_gap",
                           "case_selector": {"case_ids": ["ecps-1"]}}]},
    )
    row = _row(agreement(m, ledger_path=ledger, dispositions_root=root), "is_snap_eligible")
    assert row["against_policyengine"] == [], "an Alabama waiver says nothing about Colorado"
    assert row["verdict"] == "noted_in_the_map", "no oracle evidence; the map's own note stands"


def test_a_suite_for_this_state_is_evidence(m, tmp_path):
    ledger, root = _sources(
        tmp_path,
        {"us-co-snap-fy2026:is_snap_eligible": {"passes": False, "tolerance_failures": 9}},
        {"co-snap-ecps": [{"id": "co-residual", "concept": "us:statutes/7/2014/o#snap_eligible",
                           "disposition": "explained_residual"}]},
    )
    row = _row(agreement(m, ledger_path=ledger, dispositions_root=root), "is_snap_eligible")
    assert [e["suite"] for e in row["against_policyengine"]] == ["co-snap-ecps"]
    assert row["verdict"] == "explained"


def test_an_encoding_gap_is_attributed_but_not_explained(m, tmp_path):
    """axiom-oracles' own table: an `axiom_encoding_gap` never counts as explained."""
    ledger, root = _sources(
        tmp_path,
        {"us-co-snap-fy2026:snap_net_income": {"passes": False, "tolerance_failures": 774}},
        {"co-snap-ecps": [{"id": "co-gap", "concept": "us:statutes/7/2014/d#snap_net_income",
                           "disposition": "axiom_encoding_gap"}]},
    )
    row = _row(agreement(m, ledger_path=ledger, dispositions_root=root), "snap_net_income")
    assert row["verdict"] == "attributed_not_explained"
    assert row["verdict"] not in FLAGGED, "it has a name and an owner upstream"


def test_a_passing_run_with_a_filed_difference_is_flagged(m, tmp_path):
    """The two harnesses do not tell the same story. Scope before concluding; flag either way."""
    ledger, root = _sources(
        tmp_path,
        {"us-ctc-subsection-h:ctc_maximum": {"passes": True, "compared_rows": 87519}},
        {"us-ctc-grid": [{"id": "ctc-amount",
                          "concept": "us:statutes/26/24/h#ctc_maximum_before_phase_out_under_subsection_h",
                          "disposition": "amount_difference",
                          "case_selector": {"case_ids": ["g-1", "g-2"]}}]},
    )
    row = _row(agreement(m, ledger_path=ledger, dispositions_root=root), "ctc_maximum")
    assert row["verdict"] == "divergent_evidence"
    assert row["against_policyengine"][0]["cases"] == 2, "the case count shows the scope"


def test_a_federal_suite_stays_in_scope_for_a_state_program(m, tmp_path):
    """`us-snap-abawd-grid` is not state-scoped, so it is evidence about Colorado too."""
    ledger, root = _sources(
        tmp_path,
        {"us-co-snap-fy2026:is_snap_eligible": {"passes": False, "tolerance_failures": 9}},
        {"us-snap-abawd-grid": [{"id": "abawd", "concept": "us:statutes/7/2014/o#snap_eligible",
                                 "disposition": "upstream_engine_gap"}]},
    )
    row = _row(agreement(m, ledger_path=ledger, dispositions_root=root), "is_snap_eligible")
    assert row["verdict"] == "explained"


def test_a_missing_source_is_an_error_not_agreement(m, tmp_path):
    """No ledger must never read as "nothing failed"."""
    report = agreement(m, ledger_path=tmp_path / "absent.json", dispositions_root=tmp_path / "none")
    assert "error" in report["sources"]["in_policyengine"]
    assert "error" in report["sources"]["against_policyengine"]
    assert report["totals"]["by_verdict"]["aligned"] == 0
    assert report["totals"]["by_verdict"]["no_evidence"] == report["totals"]["bindings"]


def test_california_and_canada_share_a_prefix_and_do_not_mix():
    """`ca-` is both. The concept's own jurisdiction separates them."""
    canadian = {"suite": "ca-gis", "concept": "ca:statutes/oas#gis", "disposition": "x"}
    californian = {"suite": "ca-snap-ecps", "concept": "us:statutes/7/2014/o#snap_eligible",
                   "disposition": "x"}
    assert not _in_scope(canadian, "us", "ca"), "a Canadian rule is not California's"
    assert _in_scope(californian, "us", "ca")
    assert not _in_scope(californian, "ca", None), "California's finding is not Canada's"


def test_suite_scope_reads_the_leading_token():
    assert _suite_scope("co-snap-ecps") == "co"
    assert _suite_scope("nyc-income-tax-gap") == "ny", "a city is inside its state"
    assert _suite_scope("us-qbid-grid") is None, "federal, so in scope everywhere"
    assert _suite_scope("medicaid-eligibility") is None, "topical, not sub-national"


def test_no_run_is_not_a_pass():
    assert _verdict(None, []) == "no_evidence"
    assert _verdict({"passes": None}, []) == "pe_side_only"
    assert _verdict({"passes": True}, []) == "aligned"
    assert _verdict({"passes": False}, []) == "unexplained"


def test_the_maps_own_note_is_not_corroboration_but_it_is_not_nothing():
    """7 of 8 failing bindings carry a `known_differences` note. Calling those
    "unexplained" would report work already recorded, and bury the one row
    where nothing anywhere names the difference."""
    assert _verdict({"passes": False}, [], {"known_differences": "..."}) == "noted_in_the_map"
    assert _verdict({"passes": False}, [], {}) == "unexplained"
    assert "noted_in_the_map" not in FLAGGED, "a recorded difference is not a surprise"


def test_only_a_difference_nothing_names_is_flagged(m):
    """Against the checked-out repos. The flag is narrow on purpose."""
    report = agreement(m)
    if any(s.get("error") for s in report["sources"].values()):
        pytest.skip("policyengine-axiom or axiom-oracles is not checked out beside this repo")
    for row in report["bindings"]:
        if row["verdict"] == "unexplained":
            assert row["noted_in_the_map"] == [], "a noted difference is not unexplained"
            assert row["against_policyengine"] == []
            assert row["in_policyengine"]["tolerance_failures"], "it really does differ"


def test_the_real_sources_join_without_a_crosswalk(m):
    """Against the checked-out repos: every flagged row is a failure no one has named."""
    ledger, ledger_source = read_ledger(None)
    filed, disposition_source = read_dispositions(None)
    if ledger_source.get("error") or disposition_source.get("error"):
        pytest.skip("policyengine-axiom or axiom-oracles is not checked out beside this repo")
    report = agreement(m)
    assert report["totals"]["bindings"] > 0
    for row in report["bindings"]:
        if row["verdict"] == "unexplained":
            assert row["in_policyengine"]["passes"] is False
            assert row["against_policyengine"] == []
    assert report["totals"]["dispositions_about_unbound_concepts"] > 0, (
        "the oracle works on rules the map has not bound; that number is the backlog"
    )
