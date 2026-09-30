"""Proposals: identity by citation, reviewed bindings, neighbourhood or name; evidence attached; review to accept."""

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from axiom_mappings import ROOT, load
from axiom_mappings.accept import accept
from axiom_mappings.citations import Citation, from_link, from_module, from_text, of_rule
from axiom_mappings.propose import similarity, tokens, write

from test_mappings import copy_root


def test_citations_from_text_links_and_modules():
    assert from_text("26 U.S. Code § 24(h)(5)(A) - Child tax credit") == [Citation("usc", "26", "24", ("h", "5", "A"))]
    assert from_text("7 CFR 273.9(d)(1)") == [Citation("cfr", "7", "273.9", ("d", "1"))]
    assert from_text("Rev. Proc. 2025-32 section 3.10") == [Citation("revproc", "2025", "32")]
    assert from_link("https://www.law.cornell.edu/uscode/text/26/24#h_5_A") == [Citation("usc", "26", "24", ("h", "5", "A"))]
    assert from_link("https://www.ecfr.gov/current/title-42/part-457/section-457.560") == [Citation("cfr", "42", "457.560")]
    assert from_link("https://www.ecfr.gov/current/title-29/subtitle-B/chapter-V/part-541") == [Citation("cfr", "29", "541")]
    assert from_link("https://www.irs.gov/pub/irs-drop/rp-25-32.pdf#page=13") == [Citation("revproc", "2025", "32")]
    assert from_module("us:statutes/7/2014/e/2/B") == [Citation("usc", "7", "2014", ("e", "2", "B"))]
    assert from_module("us:regulations/42-cfr/435/120/ssi-mandatory-group") == [Citation("cfr", "42", "435.120")]
    assert from_module("us-co:regulations/10-ccr-2506-1/4.407.31") == []  # state codes are not read yet
    rule = of_rule("us:statutes/26/24/h", {"source": "26 USC 24(h)(5)"})
    assert rule == [Citation("usc", "26", "24", ("h", "5")), Citation("usc", "26", "24", ("h",))]


def test_citation_relations():
    a = Citation("usc", "26", "24", ("h", "5"))
    assert a.relation(Citation("usc", "26", "24", ("h", "5"))) == "exact"
    assert a.relation(Citation("usc", "26", "24", ("h", "5", "A"))) == "within"
    assert a.relation(Citation("usc", "26", "24", ("a",))) == "section"
    assert a.relation(Citation("usc", "26", "32", ("h",))) is None


def test_name_similarity_is_a_ranking_signal():
    assert tokens("snap_standard_deductions_table") == {"snap", "standard", "deduction", "table"}
    assert similarity("ctc_maximum_before_phase_out_under_subsection_h", "ctc_maximum") > \
        similarity("ctc_maximum_before_phase_out_under_subsection_h", "ctc_refundable_maximum")
    assert similarity("x", "y") == 0.0


# ---------------------------------------------------------------- against RuleSpec and PolicyEngine

RULESPEC = Path(os.environ.get("RULESPEC_US", Path.home() / "rulespec-us"))


@pytest.fixture(scope="module")
def world():
    pytest.importorskip("policyengine_us")
    m = load("us")
    pin = m.pins["rulespec_us"]
    if not (RULESPEC / ".git").exists() or subprocess.run(["git", "-C", str(RULESPEC), "cat-file", "-e", pin]).returncode:
        pytest.skip(f"needs rulespec-us with commit {pin} (set RULESPEC_US)")
    from axiom_mappings.corpus import Corpus
    from axiom_mappings.propose import bound
    from axiom_mappings.propose.outputs import PEVariables
    from axiom_mappings.propose.parameters import PEIndex
    from axiom_mappings.validate import policyengine_system

    system = policyengine_system()
    corpus = Corpus(RULESPEC, pin)
    return {"m": m, "system": system, "corpus": corpus, "index": PEIndex.build(system), "pe": PEVariables(system),
            "bound": bound(m, corpus)}


def param_candidates(world, axiom_id, rule=None):
    from axiom_mappings.propose.parameters import candidates

    module, name = axiom_id.split("#")
    rule = rule or world["corpus"].rules(module)[name]
    return candidates(world["index"], world["m"], module, name, rule, "2026-09-30", exclude=axiom_id, reviewed=world["bound"])


def test_a_parameter_is_proposed_by_the_law_it_cites_with_its_value_history(world):
    found, why = param_candidates(world, "us:statutes/26/24/h#ctc_refundable_per_child_cap_under_subsection_h")
    first = found[0]
    assert why is None and first["policyengine_parameter"] == "gov.irs.credits.ctc.refundable.individual_max"
    assert first["citation"] == "within" and first["values"] == "stale-axiom"  # identity by law; values are evidence
    assert "26 USC 24(h)(5)" in first["evidence"][0] and "24(h)(5)(A)" in first["evidence"][1]


def test_a_reviewed_binding_is_the_strongest_identity(world):
    found, _ = param_candidates(world, "us:statutes/26/3101/a#oasdi_wage_tax_rate")
    assert found[0]["policyengine_parameter"] == "gov.irs.payroll.social_security.rate.employee"
    assert found[0]["citation"] == "binding" and found[0]["values"] == "match"


def test_a_table_is_proposed_where_its_reviewed_sibling_maps(world):
    found, _ = param_candidates(world, "us:policies/usda/snap/fy-2026-cola/deductions#snap_standard_deduction_48_states_dc_table")
    assert found[0]["policyengine_parameter"] == "gov.usda.snap.income.deductions.standard.CONTIGUOUS_US"
    assert found[0]["citation"] == "neighbourhood"
    assert found[0]["evidence"][0].startswith("reviewed sibling us:policies/usda/snap/") and "deductions.standard" in found[0]["evidence"][0]
    assert found[0]["parameter_key_input"].startswith("<")  # the reviewer names the input keying the table


def test_units_and_switches_never_match_an_amount(world):
    money = {"kind": "parameter", "dtype": "Money", "unit": "USD", "source": "26 USC 3101(a)",
             "versions": [{"effective_from": "2024-01-01", "formula": "100"}]}
    found, _ = param_candidates(world, "us:statutes/26/3101/a#some_amount", money)
    assert all(world["index"].unit(c["policyengine_parameter"]) != "rate" for c in found)
    assert all(not world["index"].boolean(c["policyengine_parameter"]) for c in found)


def test_no_identity_no_proposal(world):
    rule = {"kind": "parameter", "dtype": "Money", "versions": [{"effective_from": "2024-01-01", "formula": "1"}]}
    found, why = param_candidates(world, "us-zz:policies/nowhere/x#y", rule)
    assert found == [] and why == "no citation this proposer reads, and no reviewed sibling"


def test_outputs_follow_reviewed_bindings_names_and_types(world):
    from axiom_mappings.propose.outputs import candidates

    corpus = world["corpus"]
    module = "us:statutes/26/24/h"
    rules = corpus.rules(module)
    found, _ = candidates(world["pe"], module, "ctc_maximum_before_phase_out_under_subsection_h",
                          rules["ctc_maximum_before_phase_out_under_subsection_h"], reviewed=world["bound"])
    assert (found[0]["policyengine_variable"], found[0]["identity"]) == ("ctc_maximum", "binding")
    found, _ = candidates(world["pe"], module, "ctc_qualifying_child_under_subsection_h",
                          rules["ctc_qualifying_child_under_subsection_h"], reviewed=world["bound"])
    assert found[0]["policyengine_variable"] == "ctc_qualifying_child"
    assert all(world["pe"].kind(c["policyengine_variable"]) == "bool" for c in found)  # a judgment is a boolean


def test_the_proposers_recover_most_of_the_reviewed_map(world):
    from axiom_mappings.propose import outputs, parameters

    p = parameters.evaluate(world["m"], RULESPEC, world["system"], index=world["index"])["outcome"]
    assert p["first"] >= 260 and p["first"] + p.get("top three", 0) >= 290
    o = outputs.evaluate(world["m"], RULESPEC, world["system"], pe=world["pe"])["outcome"]
    assert o["first"] >= 100 and o["first"] + o.get("top three", 0) >= 115


def test_slot_proposals_reuse_reviewed_bindings_and_surface_disagreements(world, tmp_path):
    from axiom_mappings.propose import slots

    m = world["m"]
    catalog = {"root_inputs": ["household_size", "age", "snap_gross_income_or_something"], "relations": {
        "member_of_household": ["member_age", "member_is_us_citizen", "member_is_pregnant"]}}
    report = slots.propose(m, world["system"], "us/snap-elderly-disabled-member", catalog)
    by_slot = {p["slot"]: p for p in report["proposals"]}
    citizen = by_slot["member_is_us_citizen"]["candidates"][0]
    assert citizen == {"spec": "is_snap_immigration_status_eligible", "identity": "binding",
                       "evidence": "bound this way in us-co/snap-fy2026.cut"}
    assert by_slot["member_age"]["candidates"][0]["spec"] == "age"
    assert "snap_gross_income_or_something" in {u["slot"] for u in report["unmatched"]}
    facts = slots.propose(m, world["system"], "us/snap-elderly-disabled-member", catalog, profile="facts",
                          root_entity="Household", entity_map={"Household": "spm_unit", "Person": "person"})
    specs = {p["slot"]: p["candidates"][0]["spec"] for p in facts["proposals"]}
    assert specs["member_age"] == {"from": "age", "period": "annual"}  # the reviewed axiom-api read, in its grammar
    assert specs["member_is_us_citizen"] == {"presumed": "procedural-eligibility", "value": True}  # axiom-api presumes
    assert not any("per_month_of" in json.dumps(c["spec"]) for p in facts["proposals"] for c in p["candidates"])
    evaluation = slots.evaluate(m, world["system"])
    pregnant = [d for d in evaluation["disagreements"] if d["slot"] == "member_is_pregnant"]
    assert pregnant and pregnant[0]["reviewed"] == {"presumed": "not-in-data", "value": False}  # oracles maps it; CO presumes


# ---------------------------------------------------------------- review and accept

def proposals_file(tmp_path, generator, proposals, **meta):
    return write(generator.rsplit(".", 1)[-1], "t", {"generator": generator, **meta}, proposals, root=tmp_path / "proposals")


def test_accept_writes_only_what_a_reviewer_accepted(tmp_path):
    root = copy_root(tmp_path / "map")
    candidate = {"policyengine_parameter": "gov.irs.credits.ctc.refundable.individual_max", "unit": "money",
                 "citation": "within", "evidence": ["Axiom cites 26 USC 24(h)(5)", "PolicyEngine cites 26 USC 24(h)(5)(A)"]}
    path = proposals_file(tmp_path, "axiom_mappings.propose.parameters", [
        {"axiom": "us:statutes/26/24/h#accepted_one", "program": "tax", "candidates": [candidate], "accept": 0,
         "rationale": "Both cite 26 USC 24(h)(5)."},
        {"axiom": "us:statutes/26/24/h#left_for_later", "program": "tax", "candidates": [candidate], "accept": None,
         "rationale": "x"}])
    assert accept(path, root=root) == ["us:statutes/26/24/h#accepted_one"]
    rows = {r["axiom"]: r for r in load("us", root).parameters}
    assert rows["us:statutes/26/24/h#accepted_one"] == {
        "axiom": "us:statutes/26/24/h#accepted_one", "program": "tax",
        "policyengine_parameter": "gov.irs.credits.ctc.refundable.individual_max", "unit": "USD", "comparison": "money",
        "rationale": "Both cite 26 USC 24(h)(5)."}
    assert "us:statutes/26/24/h#left_for_later" not in rows
    left = yaml.safe_load(path.read_text())["proposals"]
    assert [p["axiom"] for p in left] == ["us:statutes/26/24/h#left_for_later"] and path.read_text().startswith("#")


def test_accept_refuses_unreviewed_details(tmp_path):
    root = copy_root(tmp_path / "map")
    table = {"policyengine_parameter": "gov.x", "parameter_key_input": "<the input keying the table>"}
    path = proposals_file(tmp_path, "axiom_mappings.propose.parameters",
                          [{"axiom": "a:b#c", "program": "snap", "candidates": [table], "accept": 0, "rationale": "r"}])
    with pytest.raises(ValueError, match="placeholder"):
        accept(path, root=root)
    path = proposals_file(tmp_path, "axiom_mappings.propose.outputs",
                          [{"axiom": "a:b#c", "program": None, "candidates": [{"row": {}}], "accept": 0, "rationale": "r"}])
    with pytest.raises(ValueError, match="program"):
        accept(path, root=root)


def test_accepting_slots_builds_a_binding_set_the_validator_checks(tmp_path):
    from axiom_mappings.bindings import findings

    root = copy_root(tmp_path / "map")
    path = proposals_file(tmp_path, "axiom_mappings.propose.slots", [
        {"block": "inputs", "slot": "wages", "candidates": [{"spec": "employment_income"}], "accept": 0},
        {"block": "inputs", "slot": "is_blind", "candidates": [{"spec": {"presumed": "not-in-data", "value": False}}], "accept": 0},
        {"block": "inputs", "slot": "later", "candidates": [{"spec": "age"}], "accept": None}],
        program="us/federal-income-tax", profile="cut", root_entity="Person", entity_map={"Person": "person"})
    assert accept(path, root=root) == ["inputs wages", "inputs is_blind"]
    bs = load("us", root).binding_set("us/federal-income-tax", "cut")
    assert bs.doc["inputs"] == {"wages": "employment_income"}
    assert bs.doc["presumed"] == {"not-in-data": {"is_blind": False}}
    assert not [f for f in findings(load("us", root)) if f.level == "error" and "federal-income-tax" in f.where]


def test_accepting_facts_slots_writes_axiom_apis_grammar(tmp_path):
    from axiom_mappings.bindings import findings

    root = copy_root(tmp_path / "map")
    path = proposals_file(tmp_path, "axiom_mappings.propose.slots", [
        {"block": "inputs", "slot": "wages", "candidates": [{"spec": {"from": "employment_income"}}], "accept": 0},
        {"block": "inputs", "slot": "is_citizen", "candidates": [{"spec": {"presumed": "procedural-eligibility", "value": True}}],
         "accept": 0}], program="us/federal-income-tax", profile="facts")
    accept(path, root=root)
    bs = load("us", root).binding_set("us/federal-income-tax", "facts")
    assert bs.doc["inputs"] == {"wages": {"from": "employment_income"}}
    assert bs.doc["presumptions"] == {"is_citizen": {"value": True, "presumption": "procedural-eligibility"}}
    assert not [f for f in findings(load("us", root)) if f.level == "error" and "federal-income-tax" in f.where]


def test_proposals_are_never_part_of_the_map():
    assert not (ROOT / "proposals").exists() and "proposals" not in {p.name for p in (ROOT / "data").iterdir()}
    assert "proposals/" in (ROOT.parent / ".gitignore").read_text()
