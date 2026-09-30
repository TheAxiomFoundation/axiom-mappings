"""Full-history parameter verification: identity from the mapping, values as the check, over time."""

import os
import subprocess
from pathlib import Path

import pytest

from axiom_mappings import load
from axiom_mappings.verify.parameters import (Target, Version, _cells, _axiom_versions, compare, differences, gate,
                                              literal, proposed_findings)

AS_OF = "2026-09-30"
CTC = "us:statutes/26/24/h#ctc_refundable_per_child_cap_under_subsection_h"


def target(*versions, comparison="money", path="gov.x", selector=()):
    t = Target(path, selector, "tax", comparison=comparison)
    for v in versions:
        t.versions.append(v)
        if v.source not in t.axiom_ids:
            t.axiom_ids.append(v.source)
    return t


def segments(result, verdict):
    return [s for s in result["segments"] if s["verdict"] == verdict]


def test_ctc_refundable_cap_is_stale_in_axiom_from_the_first_indexed_year():
    """26 USC 24(h)(5): the module encodes the 2018 literal 1,400; PolicyEngine indexes it (24(i)(1))."""
    pe = [("2018-01-01", 1400.0), ("2019-01-01", 1400.0), ("2020-01-01", 1400.0), ("2021-01-01", 1400.0),
          ("2022-01-01", 1500.0), ("2023-01-01", 1600.0), ("2024-01-01", 1700.0), ("2025-01-01", 1700.0),
          ("2026-01-01", 1700.0), ("2027-01-01", 1800.0)]
    r = compare(target(Version("2018-01-01", None, 1400.0, CTC)), pe, AS_OF)
    assert r["verdict"] == "stale-axiom"
    first = segments(r, "stale-axiom")[0]
    assert first["from"] == "2022-01-01" and first["axiom"] == 1400.0 and first["pe"] == 1500.0
    assert first["axiom_since"] == "2018-01-01" and first["pe_since"] == "2022-01-01" and first["axiom_id"] == CTC
    assert [s["projected"] for s in r["segments"]][-1] is True  # 2027 is a projection, not law yet
    assert segments(r, "match")[0]["to"] == "2022-01-01"  # 2018-2021 agree (restated equal values do not split)


def test_a_fiscal_year_axiom_does_not_encode_is_uncovered_never_disagree():
    """The FY 2026 table ends 2026-09-30; PolicyEngine has FY 2027. Incomplete, not wrong."""
    fy26 = Version("2025-10-01", "2026-10-01", 994.0, "us:policies/usda/snap/fy-2026-cola/maximum-allotments#table")
    pe = [("2024-10-01", 975.0), ("2025-10-01", 994.0), ("2026-10-01", 1017.0)]
    r = compare(target(fy26), pe, "2026-10-15")
    assert r["verdict"] == "uncovered-axiom"
    gap = segments(r, "uncovered-axiom")[-1]
    assert gap["from"] == "2026-10-01" and gap["axiom"] is None and gap["pe"] == 1017.0
    assert not segments(r, "disagree") and not segments(r, "stale-axiom")
    assert r["history_not_encoded"] == 1  # FY 2025, before Axiom began: history, not a gap


def test_axiom_history_is_assembled_across_fiscal_year_modules():
    fy24 = Version("2023-10-01", None, 973.0, "fy-2024#table")
    fy26 = Version("2025-10-01", None, 994.0, "fy-2026#table")
    pe = [("2023-10-01", 973.0), ("2024-10-01", 975.0), ("2025-10-01", 994.0)]
    r = compare(target(fy24, fy26), pe, AS_OF)
    stale = segments(r, "stale-axiom")
    assert [(s["from"], s["to"], s["axiom_id"]) for s in stale] == [("2024-10-01", "2025-10-01", "fy-2024#table")]
    assert segments(r, "match")[-1]["axiom_id"] == "fy-2026#table"


def test_a_missing_year_between_modules_is_a_gap_naming_the_dates():
    fy24 = Version("2023-10-01", "2024-10-01", 973.0, "fy-2024#table")
    fy26 = Version("2025-10-01", None, 994.0, "fy-2026#table")
    pe = [("2023-10-01", 973.0), ("2024-10-01", 975.0), ("2025-10-01", 994.0)]
    r = compare(target(fy24, fy26), pe, AS_OF)
    assert r["verdict"] == "uncovered-axiom"
    assert [(s["from"], s["to"]) for s in segments(r, "uncovered-axiom")] == [("2024-10-01", "2025-10-01")]


def test_same_date_different_values_disagree_and_newer_axiom_is_stale_pe():
    r = compare(target(Version("2024-01-01", None, 0.075, "a#rate"), comparison="rate"),
                [("2024-01-01", 0.07)], AS_OF)
    assert r["verdict"] == "disagree"
    r = compare(target(Version("2025-01-01", None, 176100.0, "a#cap")), [("2024-01-01", 168600.0)], AS_OF)
    assert r["verdict"] == "stale-pe"


def test_projections_do_not_decide_the_verdict():
    r = compare(target(Version("2024-01-01", None, 100.0, "a#x")), [("2024-01-01", 100.0), ("2030-01-01", 130.0)], AS_OF)
    assert r["verdict"] == "match" and r["projected"] == {"stale-axiom": 1}


def test_conflicting_axiom_versions_are_reported():
    r = compare(target(Version("2024-01-01", None, 1.0, "a#x"), Version("2024-01-01", None, 2.0, "b#x")),
                [("2024-01-01", 1.0)], AS_OF)
    assert r["conflicts"] == ["2024-01-01: a#x vs b#x", "2024-01-01: b#x vs a#x"]


def test_money_agrees_to_the_cent_and_hints_point_at_causes():
    assert compare(target(Version("2024-01-01", None, 100.004, "a#x")), [("2024-01-01", 100.0)], AS_OF)["verdict"] == "match"
    r = compare(target(Version("2024-01-01", None, 780.0, "a#x")), [("2024-01-01", 65.0)], AS_OF)
    assert r["segments"][0]["hint"].startswith("ratio 12")  # SSI's $65 a month vs $780 a year
    r = compare(target(Version("2024-01-01", None, 1 / 3, "a#x"), comparison="rate"), [("2024-01-01", 0.3333)], AS_OF)
    assert r["verdict"] == "disagree" and "rounded" in r["segments"][0]["hint"]


def test_literals_are_arithmetic_only():
    assert literal("2200") == 2200 and literal("1 / 2") == 0.5 and literal("2_200") == 2200 and literal(0.062) == 0.062
    assert literal("-0.5") == -0.5 and literal("true") == 1.0
    assert literal("x + 1") is None and literal("max(1, 2)") is None and literal("1 / 0") is None


def test_cells_follow_the_mapping_conventions():
    scalar = {"versions": [{"effective_from": "2024-01-01", "formula": "1"}]}
    table = {"versions": [{"effective_from": "2024-01-01", "values": {1: 198, 2: 208}}]}
    assert _cells({"parameter_key": "SINGLE"}, scalar) == [(("SINGLE",), None)]
    assert _cells({"parameter_keys": ["AK_URBAN", "AK_RURAL_1"]}, scalar) == [(("AK_URBAN",), None), (("AK_RURAL_1",), None)]
    assert _cells({"parameter_key_path": ["thresholds", 1]}, scalar) == [(("thresholds", 1), None)]
    assert _cells({"parameter_key_input": "household_size"}, table) == [(("1",), 1), (("2",), 2)]
    assert _cells({"parameter_key_input": "size", "parameter_key_map": {"1": "ONE"}}, table)[0] == (("ONE",), 1)
    assert _cells({"parameter_key_path": [{"input": "size"}, "amount"]}, table)[1] == (("2", "amount"), 2)
    assert _cells({"parameter_calc_value": 0}, scalar) == [(("calc", 0), None)]
    assert _cells({"parameter_calc_input": "age"}, scalar).startswith("Mapping")
    assert _cells({"parameter_key_input": "size"}, scalar).startswith("Mapping")  # a scalar keyed at run time
    assert _cells({}, table).startswith("Mapping")


def test_a_table_row_missing_from_one_version_is_uncovered_there():
    rule = {"versions": [{"effective_from": "2023-10-01", "values": {1: 198, 6: 279}},
                         {"effective_from": "2025-10-01", "values": {1: 209}}]}
    assert [(v.start, v.value) for v in _axiom_versions(rule, "a#t", 6)] == [("2023-10-01", 279.0)]
    assert _axiom_versions({"versions": [{"effective_from": "2024-01-01", "formula": "rate * 2"}]}, "a#x").startswith("Axiom")


def report_with(*results):
    return {"ref": "0" * 40, "as_of": AS_OF, "results": list(results)}


def result(target_name, verdict, program="tax"):
    seg = {"from": "2025-01-01", "to": None, "verdict": verdict, "axiom": 1.0, "axiom_since": "2024-01-01", "axiom_id": "a#x",
           "pe": 2.0, "pe_since": "2025-01-01", "projected": False, "hint": None}
    return {"target": target_name, "program": program, "axiom_ids": ["a#x"], "verdict": verdict, "conflicts": [],
            "current": {verdict: 1}, "segments": [seg]}


def test_every_current_difference_must_be_filed_and_resolved_ones_removed(tmp_path):
    from test_mappings import copy_root, edit

    report = report_with(result("gov.a", "stale-axiom"), result("gov.b", "match"), result("gov.c", "uncovered-axiom"))
    root = copy_root(tmp_path)
    m = load("us", root)
    assert [f.code for f in gate(m, report)] == ["unfiled-parameter-difference"]  # gaps are coverage, not differences
    [proposal] = proposed_findings(report)
    assert proposal["cause"] == "unclassified" and proposal["kind"] == "parameter"
    assert proposal["note"].startswith("stale-axiom from 2025-01-01: Axiom 1.0 (since 2024-01-01, a#x), PolicyEngine 2.0")

    from axiom_mappings.verify.parameters import file_findings

    assert len(file_findings(m, report, root)) == 1 and file_findings(load("us", root), report, root) == []
    m = load("us", root)
    assert gate(m, report) == []
    edit(root, "findings", lambda rows: rows.append({"program": "tax", "variable": "gov.b", "counterpart": "policyengine",
                                                     "cause": "convention", "kind": "parameter"}))
    assert [f.code for f in gate(load("us", root), report)] == ["resolved-parameter-finding"]


def test_differences_are_only_mapped_targets():
    """No value is matched by coincidence: only what a mapping row names is compared."""
    report = report_with(result("gov.a", "stale-axiom"))
    assert [r["target"] for r in differences(report)] == ["gov.a"]


RULESPEC = Path(os.environ.get("RULESPEC_US", Path.home() / "rulespec-us"))


@pytest.fixture(scope="module")
def real_report():
    pytest.importorskip("policyengine_us")
    m = load("us")
    pin = m.pins["rulespec_us"]
    if not (RULESPEC / ".git").exists() or subprocess.run(["git", "-C", str(RULESPEC), "cat-file", "-e", pin]).returncode:
        pytest.skip(f"needs rulespec-us with commit {pin} (set RULESPEC_US)")
    from axiom_mappings.validate import policyengine_system
    from axiom_mappings.verify.parameters import verify

    return m, verify(m, RULESPEC, policyengine_system())


def test_the_real_map_verifies_end_to_end_and_every_difference_is_filed(real_report):
    m, report = real_report
    assert report["as_of"] == m.pins["verify_as_of"] and report["rows"] == len([p for p in m.parameters if p.get("axiom")])
    assert report["verdicts"].get("match", 0) > 300
    by_target = {r["target"]: r for r in report["results"]}
    assert by_target["gov.irs.payroll.social_security.cap"]["verdict"] == "stale-axiom"  # 2024's base, 2025 changed it
    assert all(u["reason"].split(":")[0] in ("Axiom", "Mapping", "PolicyEngine") for u in report["unchecked"])
    assert gate(m, report) == []
    assert by_target["gov.irs.credits.ctc.refundable.individual_max"]["verdict"] == "stale-axiom"
