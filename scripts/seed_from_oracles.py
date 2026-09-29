"""Seed data/us/ from an axiom-oracles commit. Run once; afterwards the data here is the source.

    python scripts/seed_from_oracles.py --oracles ~/axiom-oracles --ref origin/main

Reads the oracles files with ``git show <ref>:<path>`` (the working tree is never touched):

- ``axiom_oracles/core/case.py``                 concept ids (class Concepts)
- ``axiom_oracles/adapters/policyengine/runner.py`` concept -> PolicyEngine variable
- ``axiom_oracles/data/populace_input_mapping.yaml`` Axiom input slot rules
- ``axiom_oracles/bridges/mappings/us.yaml``      Axiom output and parameter registry

Writes concepts.yaml, inputs.yaml, outputs.yaml and parameters.yaml, and records the
oracles commit in pins.yaml.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "us"

# Concepts are the shared input facts. Each carries its entity, measure and unit, and the
# PolicyEngine input variable for each PolicyEngine entity that holds it. The PolicyEngine
# variables are the ones oracles' runner feeds (runner.py _*_CONCEPT_TO_PE tables and the
# person_inputs literal); None means oracles never feeds this concept to PolicyEngine.
# measure: flow (amount per period, sums over time), stock (level at a point in time),
# flag (boolean), level (a number that is neither, e.g. age), category (a code).
CONCEPT_META = {
    "PERSON_AGE": ("person", "level", "years", [("person", "age")]),
    "HOUSEHOLD_RELATION": ("person", "category", None, []),
    "YEARLY_EARNED_INCOME": ("person", "flow", "USD/year", [("person", "employment_income")]),
    "PREGNANT": ("person", "flag", None, [("person", "is_pregnant")]),
    "BLIND": ("person", "flag", None, [("person", "is_blind")]),
    "DISABLED": ("person", "flag", None, [("person", "is_disabled")]),
    "VETERAN": ("person", "flag", None, []),
    "BENEFITS_MEDICAID": ("person", "flag", None, []),
    "BENEFITS_MEDICAID_DISABILITY": ("person", "flag", None, []),
    "LIVING_RENTING": ("household", "flag", None, []),
    "LIVING_OWNER": ("household", "flag", None, []),
    "CASH_ON_HAND": ("household", "stock", "USD", []),
    "LOCALE": ("case", "category", None, []),
    "GEOGRAPHY_SCOPE": ("case", "category", None, []),
    "STATE_CODE": ("household", "category", None, [("household", "state_code")]),
    "DIVIDEND_INCOME": ("person", "flow", "USD/year", None),
    "QUALIFIED_DIVIDEND_INCOME": ("person", "flow", "USD/year", None),
    "INTEREST_INCOME": ("person", "flow", "USD/year", None),
    "SHORT_TERM_CAPITAL_GAINS": ("person", "flow", "USD/year", None),
    "LONG_TERM_CAPITAL_GAINS": ("person", "flow", "USD/year", None),
    "PENSION_INCOME": ("person", "flow", "USD/year", None),
    "TANF_BENEFITS": ("person", "flow", "USD/year", []),
    "SSI_BENEFITS": ("person", "flow", "USD/year", None),
    "SOCIAL_SECURITY_BENEFITS": ("person", "flow", "USD/year", None),
    "UNEMPLOYMENT_INSURANCE_INCOME": ("person", "flow", "USD/year", None),
    "RENTAL_INCOME": ("person", "flow", "USD/year", None),
    "SELF_EMPLOYMENT_INCOME": ("person", "flow", "USD/year", None),
    "SSI_COUNTABLE_RESOURCES": ("person", "stock", "USD", None),
    "PROPERTY_TAX_PAID": ("household", "flow", "USD/year", None),
    "MORTGAGE_INTEREST_PAID": ("household", "flow", "USD/year", None),
    "ITEMIZED_DEDUCTIONS_OTHER": ("household", "flow", "USD/year", None),
    "RENT_PAID": ("household", "flow", "USD/year", None),
    "CHILDCARE_EXPENSES": ("household", "flow", "USD/year", None),
}
# `None` above means: take the PolicyEngine variables from runner.py's tables.
RUNNER_TABLES = {
    "_PERSON_INCOME_CONCEPT_TO_PE": "person",
    "_PERSON_CASE_CONCEPT_TO_PE": "person",
    "_TAX_UNIT_CONCEPT_TO_PE": "tax_unit",
    "_SPM_UNIT_CASE_CONCEPT_TO_PE": "spm_unit",
}


def git_show(repo: Path, ref: str, path: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), "show", f"{ref}:{path}"], text=True)


def concept_ids(case_py: str) -> dict[str, str]:
    tree = ast.parse(case_py)
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Concepts":
            return {
                t.id: stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)
                for t in stmt.targets if isinstance(t, ast.Name)
            }
    raise SystemExit("class Concepts not found in case.py")


def runner_tables(runner_py: str) -> dict[str, list[tuple[str, str]]]:
    """concept attribute name -> [(pe_entity, pe_variable)] from runner.py's dict literals."""
    out: dict[str, list[tuple[str, str]]] = {}
    for table, entity in RUNNER_TABLES.items():
        m = re.search(rf"^{table} = \{{(.*?)^\}}", runner_py, re.S | re.M)
        if not m:
            raise SystemExit(f"{table} not found in runner.py")
        for attr, var in re.findall(r'Concepts\.([A-Z_]+):\s*"([^"]+)"', m.group(1)):
            out.setdefault(attr, []).append((entity, var))
    return out


def build_concepts(ids: dict[str, str], tables: dict[str, list[tuple[str, str]]]) -> list[dict]:
    concepts = []
    for attr, (entity, measure, unit, pe) in CONCEPT_META.items():
        pairs = tables.get(attr, []) if pe is None else pe
        concepts.append({
            "id": ids[attr],
            "name": attr.lower(),
            "entity": entity,
            "measure": measure,
            **({"unit": unit} if unit else {}),
            "policyengine": [{"entity": e, "variable": v} for e, v in pairs],
        })
    return concepts


def build_inputs(rules: list[dict], ids: dict[str, str]) -> list[dict]:
    """Oracles resolves the first matching rule in file order; that order becomes `priority`."""
    def concept(name: str) -> str:
        if name not in ids:
            raise SystemExit(f"input rule reads unknown fact {name!r}")
        return ids[name]

    out = []
    for i, rule in enumerate(rules, start=1):
        src = dict(rule["source"])
        kind = src.pop("kind")
        if kind == "fact":
            new = {"kind": "concept", "concept": concept(src.pop("name")), **src}
        elif kind == "derived":
            new = {"kind": "derived", **src}
            if "from_facts" in new:
                new["from_concepts"] = [
                    concept(f) if isinstance(f, str) else {"concept": concept(f["fact"]), **{k: v for k, v in f.items() if k != "fact"}}
                    for f in new.pop("from_facts")
                ]
        else:
            new = {"kind": "constant", **src}
        out.append({
            "id": f"us-in-{i:04d}",
            "priority": i,
            "match": rule["match"],
            "scope": rule.get("scope", "household"),
            "source": new,
        })
    return out


OUTPUT_FIELDS = ["policyengine_variable", "entity", "period", "comparison", "unit", "result_multiplier",
                 "expression", "candidate_priority", "tested_by_legal_ids"]
PARAMETER_FIELDS = ["policyengine_parameter", "parameter_key", "parameter_keys", "parameter_key_path",
                    "parameter_key_input", "parameter_key_map", "period", "unit", "comparison",
                    "result_multiplier", "candidate_priority"]


def build_registry(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    outputs, parameters = [], []
    for e in entries:
        base = {"axiom": e.get("legal_id"), "program": e["program"], "type": e["mapping_type"]}
        if e["mapping_type"] == "parameter_value":
            row = {**base, **{k: e[k] for k in PARAMETER_FIELDS if k in e}}
            row.pop("type")
            parameters.append({**row, "rationale": e["rationale"]})
        else:
            outputs.append({**base, **{k: e[k] for k in OUTPUT_FIELDS if k in e}, "rationale": e["rationale"]})
    return outputs, parameters


def dump(path: Path, header: str, key: str, rows: list[dict]) -> None:
    body = yaml.safe_dump({key: rows}, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(header.rstrip() + "\n\n" + body)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracles", default=str(Path.home() / "axiom-oracles"))
    ap.add_argument("--ref", default="origin/main")
    args = ap.parse_args()
    repo = Path(args.oracles)
    commit = subprocess.check_output(["git", "-C", str(repo), "rev-parse", args.ref], text=True).strip()

    ids = concept_ids(git_show(repo, commit, "axiom_oracles/core/case.py"))
    tables = runner_tables(git_show(repo, commit, "axiom_oracles/adapters/policyengine/runner.py"))
    rules = yaml.safe_load(git_show(repo, commit, "axiom_oracles/data/populace_input_mapping.yaml"))["mappings"]
    registry = yaml.safe_load(git_show(repo, commit, "axiom_oracles/bridges/mappings/us.yaml"))["mappings"]

    DATA.mkdir(parents=True, exist_ok=True)
    seeded = f"Seeded from axiom-oracles@{commit[:12]} by scripts/seed_from_oracles.py; edit here from now on."
    dump(DATA / "concepts.yaml", f"# Shared input facts and their PolicyEngine input variables.\n# {seeded}", "concepts",
         build_concepts(ids, tables))
    dump(DATA / "inputs.yaml", "# Axiom input slot -> concept, constant or derived value. The lowest `priority` among the\n"
         f"# rules matching a slot wins; the validator reports every slot more than one rule matches.\n# {seeded}",
         "inputs", build_inputs(rules, ids))
    outputs, parameters = build_registry(registry)
    dump(DATA / "outputs.yaml", f"# Axiom output -> PolicyEngine variable (or why not comparable).\n# {seeded}", "outputs", outputs)
    dump(DATA / "parameters.yaml", f"# Axiom parameter -> PolicyEngine parameter path.\n# {seeded}", "parameters", parameters)

    pins_path = ROOT / "pins.yaml"
    pins = yaml.safe_load(pins_path.read_text()) if pins_path.exists() else {}
    pins.setdefault("us", {})["seeded_from"] = {"repo": "TheAxiomFoundation/axiom-oracles", "commit": commit}
    pins_path.write_text(yaml.safe_dump(pins, sort_keys=False))
    print(f"seeded from {commit[:12]}: {len(ids)} concept ids, {len(rules)} input rules, "
          f"{len(outputs)} outputs, {len(parameters)} parameters")


if __name__ == "__main__":
    main()
