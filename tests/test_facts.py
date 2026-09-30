"""The facts profile: axiom-api's grammar, one semantics in Python and TypeScript, an exact export."""

import json
from pathlib import Path

import pytest
import yaml

from axiom_mappings import load
from axiom_mappings.bindings import findings
from axiom_mappings.export.axiom_api import serving_maps, write
from axiom_mappings.facts import conformance_cases, derive_unit_input, grammar_errors, run_case, same
from axiom_mappings.readiness import readiness

from test_mappings import copy_root

FIXTURES = Path(__file__).parent / "fixtures" / "serving-map"


@pytest.fixture(scope="module")
def m():
    return load("us")


def test_the_export_is_axiom_apis_serving_maps_byte_for_byte(m, tmp_path):
    exported = serving_maps(m)
    assert sorted(exported) == sorted(p.name for p in FIXTURES.glob("*.json"))
    write(m, tmp_path)
    for path in FIXTURES.glob("*.json"):
        assert (tmp_path / path.name).read_bytes() == path.read_bytes(), path.name


def test_presumptions_keep_axiom_apis_order_and_carry_a_reason(m):
    snap = m.binding_set("us/snap", "facts")
    order = list(json.loads((FIXTURES / "us-snap.json").read_text())["presumptions"])
    assert list(snap.doc["presumptions"]) == order
    reasons = {slot: p for _, slot, p, _ in snap.presumed()}
    assert reasons["member_is_us_citizen"] == "procedural-eligibility"
    assert reasons["dependent_care_expense_necessary_for_work_or_training"] == "claimed-expense-qualifies"
    assert {slot: p for _, slot, p, _ in m.binding_set("us/federal-tax", "facts").presumed()} == {"is_individual": "population-scope"}


def test_python_and_typescript_agree_on_every_conformance_case():
    cases = conformance_cases()
    assert len(cases) >= 25 and all("expected" in c for c in cases)
    for case in cases:
        assert same(run_case(case), case["expected"]), case["name"]


def test_conformance_covers_the_known_gotchas():
    names = {c["name"] for c in conformance_cases()}
    assert {"monthly-passes-the-annual-value-through", "map-and-map-default", "filing-status-joint-by-marital-unit",
            "person-only-fields-are-not-unit-facts", "count-of-no-people-is-one"} <= names
    # the monthly gotcha as a unit test: period monthly does not divide
    household = {"spm_units": {"s": {"x": {"2026": 1200}}}}
    assert derive_unit_input(household, {"from": "x", "period": "monthly"}, "2026") == 1200
    assert derive_unit_input(household, {"from": "x", "period": "monthly-from-annual", "round": "cents"}, "2026") == 100


def test_same_follows_javascript():
    assert same(1, 1.0) and same({"a": [1, 2.0]}, {"a": [1.0, 2]})
    assert not same(True, 1) and not same(None, 0) and not same("1", 1)


def test_grammar():
    assert grammar_errors({"from": "housing_cost", "period": "monthly-from-annual", "round": "cents"}, "x") == []
    assert grammar_errors({"from": {"sum": ["a"], "scope": "tax_unit.members"}}, "x") == []
    assert grammar_errors({"from": {"count": "spm_unit.members"}}, "x") == []
    assert grammar_errors({"from": {"builtin": "pe_filing_status"}, "map": {"SINGLE": 0}, "map_default": 0}, "x") == []
    assert grammar_errors({"from": {"sum": ["a"], "scope": "planet"}}, "x")
    assert grammar_errors({"from": {"builtin": "astrology"}}, "x")
    assert grammar_errors({"from": "a", "period": "weekly"}, "x")
    assert grammar_errors({"from": "a", "map_default": 0}, "x")
    assert grammar_errors({"field": "a"}, "x")


def facts_root(tmp_path, edit):
    root = copy_root(tmp_path / "map")
    path = root / "data/us/bindings/us/snap.facts.yaml"
    doc = yaml.safe_load(path.read_text())
    edit(doc)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def codes(found, program="us/snap"):
    return {f.code for f in found if f.level == "error" and program in f.where}


def test_facts_checks(tmp_path):
    def edit(doc):
        doc["inputs"]["bad_scope"] = {"from": {"sum": ["age"], "scope": "spm_unit"}}
        doc["inputs"]["unknown"] = {"from": "no_such_field"}
        doc["inputs"]["member_is_us_citizen"] = {"from": "age"}
        doc["presumptions"]["x"] = {"value": True, "presumption": "because"}
        doc["inputs"]["broken"] = {"from": {"count": "cats"}}
    root = facts_root(tmp_path, edit)
    assert {"spec-grammar", "unknown-presumption", "slot-bound-twice"} <= codes(findings(load("us", root)))
    pytest.importorskip("policyengine_us")
    from axiom_mappings.validate import policyengine_system

    found = findings(load("us", root), system=policyengine_system())
    assert {"pe-entity", "unknown-pe-variable"} <= codes(found)


def test_target_roots_must_exist_at_the_pin(tmp_path):
    corpus = tmp_path / "rulespec-us"
    (corpus / "us/statutes/26").mkdir(parents=True)
    (corpus / "us/statutes/26/1411.yaml").write_text("rules: []\n")
    root = copy_root(tmp_path / "map")
    pins = yaml.safe_load((root / "pins.yaml").read_text())
    pins["us"]["rulespec_us"] = None
    (root / "pins.yaml").write_text(yaml.safe_dump(pins))
    programs = yaml.safe_load((root / "data/us/programs.yaml").read_text())
    for p in programs["programs"]:
        p.pop("corpus_ref", None)  # read the plain directory, not a commit
    (root / "data/us/programs.yaml").write_text(yaml.safe_dump(programs))
    found = findings(load("us", root), corpus=corpus)
    missing = {f.message.split()[0] for f in found if f.code == "unknown-target-root"}
    assert missing == {"us:policies/irs/rev-proc-2025-32/standard-deduction", "us:statutes/26/3101/b/2"}


def test_a_facts_family_is_not_ready_while_it_presumes_an_expense_qualifies(m):
    report = readiness(m, "us/snap")
    assert report["bindings"]["facts"]["unacceptable"] == {"claimed-expense-qualifies": 1}
    assert not report["ready"]


def test_release_covers_facts_bindings(m, tmp_path):
    root = facts_root(tmp_path, lambda doc: doc["inputs"]["member_age"].update(period="monthly-from-annual"))
    assert load("us", root).release != m.release
