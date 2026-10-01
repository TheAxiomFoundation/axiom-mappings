"""oracles' populace input table comes from inputs.yaml; transforms that carry law are registered and block."""

import importlib.util
import json
from pathlib import Path

import pytest
import yaml

from axiom_mappings import ROOT, load
from axiom_mappings.export.oracles_populace import render
from axiom_mappings.readiness import readiness
from axiom_mappings.validate import validate

from test_mappings import copy_root, edit, errors

ORACLES = Path(__file__).parent / "fixtures" / "oracles" / "populace_input_mapping.yaml"


@pytest.fixture(scope="module")
def m():
    return load("us")


def test_the_export_is_oracles_table_with_every_comment(m):
    exported = render(m)
    assert json.dumps(yaml.safe_load(exported), sort_keys=True) == json.dumps(yaml.safe_load(ORACLES.read_text()), sort_keys=True)
    original = ORACLES.read_text().split("\nmappings:", 1)[1]
    comments = [line.strip()[1:].strip() for line in original.splitlines() if line.strip().startswith("#")]
    assert len(comments) == 219 and all(c in exported for c in comments if c)
    assert exported.startswith("# Populace-to-input mapping table: GENERATED from TheAxiomFoundation/axiom-mappings")


def test_seed_attaches_comments_before_and_inside_rules():
    spec = importlib.util.spec_from_file_location("seed", ROOT.parent / "scripts" / "seed_from_oracles.py")
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    raw = ("# header\nmappings:\n  # Section\n  - match: { kind: exact, value: a }\n    scope: household\n"
           "    source:\n      # inside a\n      kind: constant\n      value: 1\n\n  # about b\n"
           "  - match: { kind: exact, value: b }\n    scope: person\n    source: { kind: constant, value: 2 }\n")
    assert seed.rule_comments(raw) == [("Section", "inside a", "kind: constant"), ("about b", "", None)]


def test_every_derived_transform_is_registered(m):
    used = {r["source"]["transform"] for r in m.inputs if r["source"]["kind"] == "derived"}
    assert used == set(m.transforms)
    law = {t for t, e in m.transforms.items() if e["kind"] == "law-in-code"}
    assert law == {"elderly_or_disabled", "ssi_couple_countable_income", "abd_adult_with_abd_adult_partner"}
    assert m.transforms["ssi_couple_countable_income"]["values"]["earned_exclusion_annual"] == 780


def test_an_unregistered_transform_is_an_error(tmp_path):
    root = copy_root(tmp_path)
    edit(root, "inputs", lambda rows: rows[0]["source"].update(transform="astrology"))
    found, _ = validate("us", root)
    assert "unknown-transform" in errors(found)


def test_law_in_code_must_cite_its_law(tmp_path):
    root = copy_root(tmp_path)
    edit(root, "transforms", lambda rows: next(r for r in rows if r["id"] == "elderly_or_disabled").pop("law"))
    found, _ = validate("us", root)
    assert "schema" in errors(found)


def test_slots_computed_by_harness_law_or_assumptions_block_readiness(m):
    compiled = {"metadata": {"input_catalog": [
        {"slot": "snap_member_is_elderly_or_disabled", "request_names": []},   # elderly_or_disabled: law in code
        {"slot": "member_weekly_work_hours", "request_names": []},             # positive_to_constant: assumption
        {"slot": "household_size", "request_names": []},                        # hh_size: arithmetic
    ]}}
    report = readiness(m, "us/federal-income-tax", compiled)
    assert report["inputs"]["harness_law"] == 1 and report["inputs"]["harness_assumption"] == 1
    assert report["inputs"]["derived"] == 1
    assert any("law outside RuleSpec" in b for b in report["blockers"])
    assert any("undeclared assumption" in b for b in report["blockers"])


def test_a_compiled_program_without_slots_is_not_coverage(m):
    report = readiness(m, "us/federal-income-tax", {"metadata": {}})
    assert any("lists no input slots" in b for b in report["blockers"])
