"""Per-program bindings: import/export round trip, profiles, presumptions, types, coverage, readiness."""

import json
import shutil
from pathlib import Path

import pytest
import yaml

from axiom_mappings import load
from axiom_mappings.bindings import canonical, findings, grammar_errors
from axiom_mappings.export.policyengine_axiom import manifests, write
from axiom_mappings.profiles import differences
from axiom_mappings.readiness import readiness

from test_mappings import copy_root

FIXTURES = Path(__file__).parent / "fixtures"
CATALOGS = {json.loads(p.read_text())["program"]: json.loads(p.read_text()) for p in (FIXTURES / "catalogs").glob("*.json")}


@pytest.fixture(scope="module")
def m():
    return load("us")


@pytest.fixture(scope="module")
def system():
    pytest.importorskip("policyengine_us")
    from axiom_mappings.validate import policyengine_system

    return policyengine_system()


def codes(found, level="error"):
    return {f.code for f in found if f.level == level}


def with_bindings(tmp_path: Path, *docs) -> Path:
    """A copy of the map whose bindings are exactly ``docs``."""
    root = copy_root(tmp_path / "map")
    shutil.rmtree(root / "data/us/bindings")
    for doc in docs:
        path = root / "data/us/bindings" / f"{doc['program']}.{doc['profile']}.yaml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(doc, sort_keys=False))
    return root


def snap(profile="cut", **extra):
    """A small SNAP binding set on the spm_unit, for mutation."""
    doc = {"program": "us-co/snap-fy2026", "profile": profile, "consumers": ["policyengine-axiom"],
           "root_entity": "Household", "entity_map": {"Household": "spm_unit", "Person": "person"},
           "inputs": {}, "bindings": [{"pe_variable": "snap_max_allotment", "axiom_output": "snap_maximum_allotment",
                                        "status": "on"}]}
    doc.update(extra)
    return doc


# -------------------------------------------------------------------- round trip

def test_export_reproduces_every_policyengine_axiom_manifest_exactly(m):
    exported = manifests(m)
    snapshots = sorted((FIXTURES / "manifests").glob("*.json"))
    assert len(snapshots) == 5 and len(exported) == 5
    for path in snapshots:
        original = json.loads(path.read_text())
        # canonical JSON tells false, 0 and 0.0 apart: a constant's type is its engine column type
        assert canonical(exported[original["program"]]) == canonical(original), path.name


def test_write_keeps_the_consumers_file_names(m, tmp_path):
    shutil.copytree(FIXTURES / "manifests", tmp_path / "manifests")
    before = sorted(p.name for p in (tmp_path / "manifests").iterdir())
    write(m, tmp_path / "manifests")
    assert sorted(p.name for p in (tmp_path / "manifests").iterdir()) == before
    for path in (tmp_path / "manifests").iterdir():
        assert canonical(json.loads(path.read_text())) == canonical(json.loads((FIXTURES / "manifests" / path.name).read_text()))


def test_program_facts_come_from_programs_yaml(m):
    co = manifests(m)["us-co-snap-fy2026"]
    assert co["corpus_ref"] == m.programs["us-co/snap-fy2026"]["corpus_ref"][:9]
    assert co["effective"] == {"from": "2025-10-01", "to": "2026-09-30"}
    assert co["source"] == "composed/fy-2026-benefit-calculation.yaml"
    assert manifests(m)["us-oasdi-wage-tax"]["source"] == "us/statutes/26/3101/a.yaml"  # derived from the module id


def test_the_committed_bindings_are_clean(m, system):
    found = findings(m, system=system, catalogs=CATALOGS)
    assert codes(found) == set(), [f for f in found if f.level == "error"][:5]


def test_every_constant_is_declared_under_a_presumption(m):
    co = m.binding_set("us-co/snap-fy2026")
    presumed = {(where, slot): p for where, slot, p, _ in co.presumed()}
    assert len(presumed) == 297 + 81
    assert presumed[("relation member_of_household", "federal_minimum_wage")] == "law-supplied"
    assert presumed[("inputs", "farm_loss_period_months")] == "neutral-divisor"
    assert presumed[("relation member_of_household", "member_registered_for_work_or_registered_by_state")] == "procedural-eligibility"
    assert not any(isinstance(s, dict) and "const" in s for _, b in co.blocks for s in (b.get("inputs") or {}).values())


# -------------------------------------------------------------------- structure

def test_grammar():
    assert grammar_errors("employment_income", "x") == []
    assert grammar_errors({"sum_members_of": {"variable": "rent", "where": {"not_of": "is_child"}}}, "x") == []
    assert grammar_errors({"household_of": {"map_of": {"variable": "state_code", "values": {"CO": 1}, "default": 0}}}, "x") == []
    assert grammar_errors({"any_of": []}, "x")
    assert grammar_errors({"sum_of": ["a"], "not_of": "b"}, "x")
    assert grammar_errors({"map_of": {"variable": "x", "values": {}}}, "x")  # no default
    assert grammar_errors({"unknown": "x"}, "x")


def test_constants_need_a_known_presumption_and_one_home(tmp_path):
    doc = snap(inputs={"eligible_striker": {"const": False}},
               presumed={"no-such-presumption": {"a": 0}, "not-in-data": {"b": 0, "c": [1]}})
    doc["relations"] = {"member_of_household": {"inputs": {"b2": "age"}, "presumed": {"not-in-data": {"b2": 0}}}}
    found = findings(load("us", with_bindings(tmp_path, doc)))
    assert {"undeclared-constant", "unknown-presumption", "presumed-value", "slot-bound-twice"} <= codes(found)


def test_bindings_must_name_known_programs_consumers_and_paths(tmp_path):
    root = with_bindings(tmp_path, snap(program="us/nowhere", consumers=["someone"]), snap(profile="nope"))
    assert {"unknown-program", "unknown-profile"} <= codes(findings(load("us", root)))
    doc = snap(consumers=["axiom-api"], root_entity="Nothing")
    doc["bindings"].append(dict(doc["bindings"][0]))
    doc["bindings"][0]["status"] = "maybe"
    assert {"unknown-consumer", "root-entity", "bound-twice", "binding-status"} <= codes(findings(load("us", with_bindings(tmp_path / "b", doc))))


def test_a_binding_slot_must_agree_with_the_program_slot(tmp_path):
    doc = snap(inputs={"household_size": "spm_unit_size"})
    doc["bindings"][0]["inputs"] = {"household_size": "snap_unit_size"}
    assert "slot-conflict" in codes(findings(load("us", with_bindings(tmp_path, doc))))


# -------------------------------------------------------------------- PolicyEngine types and profiles

def test_profiles_facts_reads_only_policyengine_inputs(tmp_path, system):
    cut = snap(inputs={"household_size": "snap_unit_size"})
    facts = snap(profile="facts", consumers=[], inputs={"household_size": "snap_unit_size"})
    m = load("us", with_bindings(tmp_path, cut, facts))
    found = findings(m, system=system)
    assert {f.where for f in found if f.code == "facts-reads-computed"} == {"us-co/snap-fy2026.facts inputs household_size"}


@pytest.mark.parametrize("spec,code", [
    ({"per_month_of": "spm_unit_assets"}, "per-month-of-stock"),       # assets are a stock: never divide by 12
    ("employment_income", "pe-entity"),                                 # a person variable on the spm_unit slot
    ({"map_of": {"variable": "state_code", "values": {"CO": 1}, "default": 0}}, "pe-entity"),  # household, not spm_unit
    ({"household_of": {"map_of": {"variable": "state_code", "values": {"XX": 1}, "default": 0}}}, "map-of-unknown-value"),
    ({"household_of": {"map_of": {"variable": "state_fips", "values": {"8": 1}, "default": 0}}}, "map-of-not-enum"),
    ({"project_of": "housing_cost"}, "pe-entity"),                      # projects onto persons, the slot is spm_unit
    ({"sum_members_of": {"variable": "housing_cost"}}, "pe-entity"),    # sums a person variable
    ({"sum_of": ["is_disabled"]}, "sum-of-non-number"),
    ({"per_month_of": "snap_earned_income"}, "pe-period"),              # already monthly
    ("no_such_variable", "unknown-pe-variable"),
])
def test_types(tmp_path, system, spec, code):
    m = load("us", with_bindings(tmp_path, snap(inputs={"slot": spec})))
    assert code in codes(findings(m, system=system))


def test_level_of_a_flow_is_only_a_hint(tmp_path, system):
    found = findings(load("us", with_bindings(tmp_path, snap(inputs={"slot": {"level_of": "housing_cost"}}))), system=system)
    assert "level-of-flow" in codes(found, "warning") and "level-of-flow" not in codes(found)


def test_relation_slots_are_per_person_and_filters_boolean(tmp_path, system):
    doc = snap(relations={"member_of_household": {"filter": "age", "inputs": {"member_age": "age", "rent": "housing_cost"}}})
    found = findings(load("us", with_bindings(tmp_path, doc)), system=system)
    assert "filter-not-boolean" in codes(found)
    assert [f.where for f in found if f.code == "pe-entity"] == ["us-co/snap-fy2026.cut relation member_of_household rent"]


def test_parameters_and_bound_variables_must_exist(tmp_path, system):
    doc = snap()
    doc["bindings"][0].update(pe_variable="no_such_variable", parameters={"x": "gov.no.such.path"})
    assert {"unknown-pe-variable", "unknown-pe-parameter"} <= codes(findings(load("us", with_bindings(tmp_path, doc)), system=system))


# -------------------------------------------------------------------- coverage and corpus

def test_catalog_catches_unbound_and_stray_slots(tmp_path, m):
    co = m.binding_set("us-co/snap-fy2026")
    doc = json.loads(json.dumps(co.doc))
    doc["presumed"]["not-in-data"].pop("eligible_striker")
    doc.setdefault("inputs", {})["not_a_slot"] = "spm_unit_size"
    doc["relations"]["member_of_household"]["presumed"]["not-in-data"]["not_a_member_slot"] = False
    found = findings(load("us", with_bindings(tmp_path, doc)), catalogs=CATALOGS)
    assert {(f.code, f.message.split()[0]) for f in found if f.level == "error"} == {
        ("unbound-slot", "eligible_striker"), ("stray-slot", "not_a_slot"), ("stray-slot", "not_a_member_slot")}


def test_corpus_names_resolve_in_the_import_closure(tmp_path):
    corpus = tmp_path / "rulespec-us"
    for rel, doc in {
        "us-co/policies/cdhs/snap/fy-2026-benefit-calculation.yaml": {"imports": ["us:statutes/7/2017/a"], "rules": [{"name": "snap_eligible"}]},
        "us/statutes/7/2017/a.yaml": {"rules": [{"name": "snap_maximum_allotment"}, {"name": "snap_maximum_allotment_table"}]},
    }.items():
        (corpus / rel).parent.mkdir(parents=True, exist_ok=True)
        (corpus / rel).write_text(yaml.safe_dump(doc))
    doc = snap(expose=["snap_eligible", "not_in_the_program"])
    doc["bindings"][0]["parameters"] = {"snap_maximum_allotment_table": "gov.usda.snap.max_allotment.main", "gone": "gov.x"}
    root = with_bindings(tmp_path, doc)
    programs = yaml.safe_load((root / "data/us/programs.yaml").read_text())
    for p in programs["programs"]:
        p.pop("corpus_ref", None)  # read the plain directory, not a commit
    (root / "data/us/programs.yaml").write_text(yaml.safe_dump(programs))
    pins = yaml.safe_load((root / "pins.yaml").read_text())
    pins["us"]["rulespec_us"] = None
    (root / "pins.yaml").write_text(yaml.safe_dump(pins))
    found = findings(load("us", root), corpus=corpus)
    assert sorted(f.message.split()[1] for f in found if f.code == "unknown-axiom-name") == ["gone", "not_in_the_program"]


# -------------------------------------------------------------------- profiles report, readiness, release

def test_profiles_report_lists_slots_bound_differently(tmp_path, m):
    cut = json.loads(json.dumps(m.binding_set("us-co/snap-fy2026").doc))
    facts = {**snap(profile="facts", consumers=[]), "relations": {"member_of_household": {
        "presumed": {"procedural-eligibility": {"member_is_us_citizen": True}},
        "inputs": {"member_age": "age"}}}}
    rows = differences(load("us", with_bindings(tmp_path, cut, facts)), "us-co/snap-fy2026")
    citizen = next(r for r in rows if r["slot"] == "member_of_household/member_is_us_citizen")
    assert citizen["cut"] == "is_snap_immigration_status_eligible"
    assert citizen["facts"] == {"presumed": "procedural-eligibility", "value": True}
    assert not any(r["slot"] == "member_of_household/member_age" for r in rows)  # same binding: not listed
    assert differences(m, "us-co/snap-fy2026") == []  # one profile: nothing to compare


def test_readiness_counts_bindings_and_applies_the_d48_gate(m, tmp_path):
    co = readiness(m, "us-co/snap-fy2026", catalog=CATALOGS["us-co/snap-fy2026"])
    assert co["bindings"]["cut"]["unacceptable"] == {"law-supplied": 2}
    assert co["bindings"]["cut"]["coverage"] == "complete"
    assert not co["ready"]
    oasdi = readiness(m, "us/oasdi-employee-tax", catalog=CATALOGS["us/oasdi-employee-tax"])
    assert oasdi["ready"], oasdi["blockers"]
    assert not readiness(m, "us/oasdi-employee-tax")["ready"]  # coverage unverified without the catalog

    root = copy_root(tmp_path)
    for cause, blocks in (("unclassified", True), ("axiom-encoding-wrong", True), ("mapping-wrong", True),
                          ("counterpart-differs-from-law", False), ("convention", False)):
        findings_path = root / "data/us/findings.yaml"
        data = yaml.safe_load(findings_path.read_text())
        data["findings"] = [f for f in data["findings"] if f["program"] != "us/oasdi-employee-tax"]
        data["findings"].append({"program": "us/oasdi-employee-tax", "variable": "employee_social_security_tax",
                                 "counterpart": "policyengine", "cause": cause})
        findings_path.write_text(yaml.safe_dump(data, sort_keys=False))
        report = readiness(load("us", root), "us/oasdi-employee-tax", catalog=CATALOGS["us/oasdi-employee-tax"])
        assert report["ready"] is not blocks, cause


def test_release_changes_when_a_binding_changes(m, tmp_path):
    root = copy_root(tmp_path)
    path = root / "data/us/bindings/us/oasdi-employee-tax.cut.yaml"
    path.write_text(path.read_text().replace("status: 'on'", "status: shadow"))
    assert load("us", root).release != m.release
