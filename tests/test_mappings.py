import shutil
from pathlib import Path

import pytest
import yaml

from axiom_mappings import ROOT, load, matches
from axiom_mappings.validate import coverage, validate


@pytest.fixture(scope="module")
def m():
    return load("us")


def copy_root(tmp_path: Path) -> Path:
    for name in ("data", "schema"):
        shutil.copytree(ROOT / name, tmp_path / name)
    shutil.copy(ROOT / "pins.yaml", tmp_path / "pins.yaml")
    return tmp_path


def edit(root: Path, table: str, fn) -> None:
    path = root / "data" / "us" / f"{table}.yaml"
    data = yaml.safe_load(path.read_text())
    fn(data[table])
    path.write_text(yaml.safe_dump(data, sort_keys=False))


def errors(findings):
    return {f.code for f in findings if f.level == "error"}


def test_seeded_tables_load(m):
    assert len(m.concepts) >= 30 and len(m.inputs) >= 200 and len(m.outputs) >= 3000 and len(m.parameters) >= 700
    assert [r["priority"] for r in m.inputs] == sorted(r["priority"] for r in m.inputs)
    assert m.pins["policyengine_us"] == "1.808.0"


def test_the_committed_data_has_no_errors():
    findings, _ = validate("us")
    assert errors(findings) == set()


def test_match_kinds():
    rule = lambda kind, value: {"match": {"kind": kind, "value": value}}
    assert matches(rule("exact", "household_size"), "household_size")
    assert not matches(rule("exact", "household_size"), "us:x#input.household_size")
    assert matches(rule("suffix", "household_size"), "us:x#input.household_size")
    assert matches(rule("substring", "wages"), "employee_wages_received")


def test_lowest_priority_wins(m):
    slot = "us-co:policies/x#input.snap_gross_monthly_earned_income"
    rules = m.rules_for_slot(slot)
    assert rules and m.rule_for_slot(slot) is rules[0]
    assert [r["priority"] for r in rules] == sorted(r["priority"] for r in rules)


def test_concept_policyengine_lookup_is_per_entity(m):
    rent = "axiom:housing/household#rent_paid"
    assert m.policyengine_variable(rent, "person") == "pre_subsidy_rent"
    assert m.policyengine_variable(rent, "spm_unit") == "housing_cost"
    assert m.policyengine_variable(rent, "tax_unit") is None


def test_validator_catches_duplicate_priority_and_unknown_concept(tmp_path):
    root = copy_root(tmp_path)

    def corrupt(rows):
        rows[1]["priority"] = rows[0]["priority"]
        concept_rule = next(r for r in rows if r["source"]["kind"] == "concept")
        concept_rule["source"]["concept"] = "axiom:nowhere/person#nothing"

    edit(root, "inputs", corrupt)
    found = errors(validate("us", root)[0])
    assert {"duplicate-input-priority", "unknown-concept"} <= found


def test_schema_requires_a_policyengine_variable_on_direct_mappings(tmp_path):
    root = copy_root(tmp_path)
    edit(root, "outputs", lambda rows: next(r for r in rows if r["type"] == "direct_variable").pop("policyengine_variable"))
    assert "schema" in errors(validate("us", root)[0])


def test_coverage_reports_covered_missing_and_ambiguous(m):
    compiled = {"metadata": {"input_catalog": [
        {"slot": "household_size", "request_names": ["household_size"]},
        {"slot": "no_rule_matches_this_slot_xyz", "request_names": []},
    ]}}
    report = coverage(m, compiled)
    assert report["slots"] == 2 and report["covered"] == 1
    assert report["missing"] == ["no_rule_matches_this_slot_xyz"]


@pytest.mark.skipif(not __import__("importlib").util.find_spec("policyengine_us"), reason="needs policyengine-us")
def test_policyengine_checks_pass_and_flag_unprojected_entity_mismatch(tmp_path):
    findings, _ = validate("us", policyengine=True)
    assert errors(findings) == set()
    root = copy_root(tmp_path)
    edit(root, "outputs", lambda rows: next(r for r in rows if r.get("entity_projection")).pop("entity_projection"))
    assert "pe-entity" in errors(validate("us", root, policyengine=True)[0])
