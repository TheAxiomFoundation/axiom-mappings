"""Validate the map: schema, integrity, vocabulary, PolicyEngine metadata, and coverage of Axiom programs.

    python -m axiom_mappings.validate --country us [--policyengine] [--corpus RULESPEC [--ref SHA]]
                                      [--artifact compiled.json ...] [--catalog slots.json ...] [--json]

Errors fail the run (exit 1); warnings are reported. ``--policyengine`` checks every mapped
PolicyEngine variable and parameter against the installed policyengine-us (pinned in pins.yaml).
``--artifact`` reads a compiled Axiom program and reports which of its input slots the rules
cover, which they miss, and which more than one rule matches. ``--corpus`` resolves every Axiom id
at the pinned rulespec-us commit (``identity.py``). Bindings (``bindings.py``) are checked for
structure always, for PE entity, period and type with ``--policyengine``, for Axiom names with
``--corpus``, and for slot coverage with ``--catalog`` (a program's slots, from policyengine-axiom's
``scripts/export_slot_catalog.py``).
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import ROOT, TABLES, Mappings, load, matches

COMPARISONS = {"money", "bool", "decision", "count", "rate"}
SCALE_COLUMNS = {"rates": "rate", "thresholds": "threshold", "amounts": "amount"}


@dataclass
class Finding:
    level: str  # error | warning
    code: str
    where: str
    message: str


def _schema_findings(country: str, root: Path) -> list[Finding]:
    import jsonschema

    out = []
    for table in TABLES:
        path = root / "data" / country / f"{table}.yaml"
        if not path.exists():
            out.append(Finding("error", "missing-table", str(path), f"{table}.yaml is missing"))
            continue
        schema = json.loads((root / "schema" / f"{table}.schema.json").read_text())
        validator = jsonschema.Draft202012Validator(schema)
        errors = sorted(validator.iter_errors(yaml.safe_load(path.read_text())), key=lambda e: list(e.absolute_path))
        for e in errors[:50]:
            out.append(Finding("error", "schema", f"{table}:{'/'.join(map(str, e.absolute_path))}", e.message[:300]))
        if len(errors) > 50:
            out.append(Finding("error", "schema", table, f"{len(errors) - 50} more schema errors"))
    taxonomy = root / "data" / "taxonomy.yaml"
    if taxonomy.exists():
        schema = json.loads((root / "schema" / "taxonomy.schema.json").read_text())
        for e in jsonschema.Draft202012Validator(schema).iter_errors(yaml.safe_load(taxonomy.read_text())):
            out.append(Finding("error", "schema", f"taxonomy:{'/'.join(map(str, e.absolute_path))}", e.message[:300]))
    return out


def _duplicates(values: Iterable[Any]) -> list[Any]:
    counts = collections.Counter(v for v in values if v is not None)
    return sorted(v for v, n in counts.items() if n > 1)


def _integrity_findings(m: Mappings) -> list[Finding]:
    out: list[Finding] = []
    for v in _duplicates(c["name"] for c in m.concepts.values()):
        out.append(Finding("error", "duplicate-concept-name", v, "two concepts share a name"))
    for key in ("id", "priority"):
        for v in _duplicates(r[key] for r in m.inputs):
            out.append(Finding("error", f"duplicate-input-{key}", str(v), f"two input rules share {key} {v}"))
    for r in m.inputs:
        src = r["source"]
        refs = [src["concept"]] if src["kind"] == "concept" else [
            f if isinstance(f, str) else f["concept"] for f in src.get("from_concepts", [])
        ]
        for ref in refs:
            if ref not in m.concepts:
                out.append(Finding("error", "unknown-concept", r["id"], f"reads concept {ref!r}, which is not defined"))
        for f in src.get("from_concepts", []):
            if isinstance(f, dict) and "scale" in f:
                out.append(Finding("warning", "scaled-concept", r["id"],
                                   f"scales {f['concept']} by {f['scale']}: a modelling assumption, state it in a note"))
    for r in m.inputs:
        presumption = r["source"].get("presumption")
        if presumption is None:
            continue
        if presumption not in m.presumptions:
            out.append(Finding("error", "unknown-presumption", r["id"], f"names presumption {presumption!r}, which is not defined"))
        elif m.presumptions[presumption].get("acceptable") is False:
            out.append(Finding("warning", "unacceptable-presumption", r["id"], f"uses {presumption}: replace it"))
    for s in m.supplied_parameters:
        if s["program"] not in m.programs:
            out.append(Finding("error", "unknown-program", s["name"], f"supplied for {s['program']!r}, not in programs.yaml"))
    registry = m.registry_programs
    for f in m.findings:
        key = f"{f['program']}:{f['variable']}:{f['counterpart']}"
        if f["program"] not in m.programs and f["program"] not in registry:
            out.append(Finding("error", "unknown-program", key, "finding for a program in neither programs.yaml nor the registry"))
        if f["cause"] not in m.causes:
            out.append(Finding("error", "unknown-cause", key, f"cause {f['cause']!r} is not defined"))
    for v in _duplicates((f["program"], f["variable"], f["counterpart"]) for f in m.findings):
        out.append(Finding("error", "duplicate-finding", ":".join(v), "recorded more than once"))
    for vocabulary, table in m.taxonomy.get("crosswalk", {}).items():
        for code, cause in table.items():
            if cause not in m.causes:
                out.append(Finding("error", "unknown-cause", f"{vocabulary}:{code}", f"maps to {cause!r}, not a defined cause"))
    for r in m.inputs:
        transform = r["source"].get("transform")
        if r["source"]["kind"] == "derived" and transform not in m.transforms:
            out.append(Finding("error", "unknown-transform", r["id"], f"transform {transform!r} is not in transforms.yaml"))
    harness = collections.Counter(m.harness_kind(r) for r in m.inputs if m.harness_kind(r))
    for kind, n in sorted(harness.items()):
        out.append(Finding("warning", kind.replace("_", "-"), "inputs",
                           f"{n} slot rules compute law or an assumption outside RuleSpec (transforms.yaml); they block readiness"))
    constants = [r for r in m.inputs if r["source"]["kind"] == "constant" and "presumption" not in r["source"]]
    if constants:
        out.append(Finding("warning", "undeclared-constants", "inputs",
                           f"{len(constants)} constant rules name no presumption policy"))
    for table, rows in (("outputs", m.outputs), ("parameters", m.parameters)):
        for v in _duplicates(r["axiom"] for r in rows):
            out.append(Finding("error", f"duplicate-{table}", v, f"{v} appears more than once in {table}"))
        for i, r in enumerate(rows):
            if not r.get("axiom"):
                note = r.get("type") == "not_comparable"  # a note covering several outputs; usable by no consumer
                out.append(Finding("warning" if note else "error", f"{table}-without-axiom-id", f"{table}[{i}]",
                                   f"no Axiom id ({r['program']})" + (": a group note, list its outputs" if note else "")))
    for r in m.renames:
        ids = {row["axiom"] for row in getattr(m, r["table"])}
        if r["to"] not in ids:
            out.append(Finding("error", "rename-target-unmapped", r["to"], f"renamed from {r['from']} but not in {r['table']}"))
        if r["from"] in ids:
            out.append(Finding("error", "rename-source-still-mapped", r["from"], f"renamed to {r['to']} but still in {r['table']}"))
    for r in m.outputs:
        comparison = r.get("comparison")
        if comparison and comparison not in COMPARISONS:
            out.append(Finding("warning", "comparison-vocabulary", r["axiom"] or "?",
                               f"comparison {comparison!r} is not one of {sorted(COMPARISONS)}"))
        if r["type"] == "direct_variable" and not (r.get("entity") and r.get("period")):
            out.append(Finding("warning", "incomplete-output", r["axiom"] or "?",
                               "direct_variable without entity or period"))
    return out


def policyengine_system():
    from policyengine_us import CountryTaxBenefitSystem

    return CountryTaxBenefitSystem()


def _policyengine_findings(m: Mappings, system) -> list[Finding]:
    import importlib.metadata

    from policyengine_core.parameters import get_parameter

    out: list[Finding] = []
    installed = importlib.metadata.version("policyengine-us")
    pinned = m.pins.get("policyengine_us")
    if pinned and installed != pinned:
        out.append(Finding("warning", "policyengine-version", "pins", f"installed {installed}, pinned {pinned}"))
    variables = system.variables

    for c in m.concepts.values():
        for pe in c["policyengine"]:
            var = variables.get(pe["variable"])
            where = f"{c['id']} -> {pe['variable']}"
            if var is None:
                out.append(Finding("error", "unknown-pe-variable", where, "no such PolicyEngine variable"))
                continue
            if var.entity.key != pe["entity"]:
                out.append(Finding("error", "pe-entity", where, f"declared {pe['entity']}, PolicyEngine has {var.entity.key}"))
            if var.formulas or getattr(var, "adds", None) or getattr(var, "subtracts", None):
                out.append(Finding("warning", "pe-computed-input", where,
                                   "a computed PolicyEngine variable, not a raw input: Axiom would not replace its chain"))

    for o in m.outputs:
        name = o.get("policyengine_variable")
        if not name or o["type"] == "not_comparable":
            continue
        var = variables.get(name)
        where = f"{o['axiom']} -> {name}"
        if var is None:
            out.append(Finding("error", "unknown-pe-variable", where, "no such PolicyEngine variable"))
            continue
        if o.get("entity") and o["entity"] != var.entity.key:
            if o.get("entity_projection"):
                out.append(Finding("warning", "pe-entity-projected", where,
                                   f"compared per {o['entity']}, PolicyEngine has {var.entity.key}: {o['entity_projection']}"))
            else:
                out.append(Finding("error", "pe-entity", where,
                                   f"declared {o['entity']}, PolicyEngine has {var.entity.key}, and no entity_projection"))
        if o.get("period") and o["period"] != var.definition_period:
            out.append(Finding("warning", "pe-period", where,
                               f"compared per {o['period']}, PolicyEngine defines it per {var.definition_period}"))

    for p in m.parameters:
        path = p["policyengine_parameter"]
        where = f"{p['axiom']} -> {path}"
        try:
            get_parameter(system.parameters, path)
            continue
        except Exception:
            pass
        # Bracket scales are addressed as <scale>.rates / .thresholds / .amounts plus an index
        # in parameter_key_path (the oracles convention).
        scale_path, _, column = path.rpartition(".")
        try:
            scale = get_parameter(system.parameters, scale_path) if column in SCALE_COLUMNS else None
        except Exception:
            scale = None
        if scale is None or type(scale).__name__ != "ParameterScale":
            out.append(Finding("error", "unknown-pe-parameter", where, "no such PolicyEngine parameter"))
            continue
        index = (p.get("parameter_key_path") or [None])[0]
        if not isinstance(index, int) or not 0 <= index < len(scale.brackets):
            out.append(Finding("error", "scale-index", where,
                               f"parameter_key_path {p.get('parameter_key_path')} is not a bracket of {scale_path} "
                               f"({len(scale.brackets)} brackets)"))
    return out


def slot_names(compiled: dict[str, Any]) -> dict[str, list[str]]:
    """slot -> every name the engine accepts for it (bare and qualified)."""
    catalog = compiled.get("metadata", {}).get("input_catalog", [])
    return {e["slot"]: sorted({e["slot"], e.get("canonical_request_name", e["slot"]), *e.get("request_names", [])})
            for e in catalog}


def coverage(m: Mappings, compiled: dict[str, Any]) -> dict[str, Any]:
    """Which of a compiled program's input slots the rules cover, miss, or match more than once."""
    covered, missing, ambiguous = {}, [], {}
    for slot, names in sorted(slot_names(compiled).items()):
        hits = [r for r in m.inputs if any(matches(r, n) for n in names)]
        if not hits:
            missing.append(slot)
            continue
        covered[slot] = hits[0]["id"]
        if len({json.dumps(h["source"], sort_keys=True) for h in hits}) > 1:
            ambiguous[slot] = [h["id"] for h in hits]
    kinds = collections.Counter(next(r for r in m.inputs if r["id"] == rid)["source"]["kind"] for rid in covered.values())
    return {"slots": len(covered) + len(missing), "covered": len(covered), "by_source": dict(kinds),
            "missing": missing, "ambiguous": ambiguous}


def _bindings_schema_findings(country: str, root: Path) -> list[Finding]:
    import jsonschema

    validators = {profile: jsonschema.Draft202012Validator(json.loads((root / "schema" / name).read_text()))
                  for profile, name in (("cut", "bindings.schema.json"), ("facts", "bindings.facts.schema.json"))}
    out = []
    for path in sorted((root / "data" / country / "bindings").rglob("*.yaml")):
        doc = yaml.safe_load(path.read_text())
        validator = validators["facts" if path.name.endswith(".facts.yaml") else "cut"]
        for e in validator.iter_errors(doc):
            out.append(Finding("error", "schema", f"bindings/{path.name}:{'/'.join(map(str, e.absolute_path))}", e.message[:300]))
    return out


def validate(country: str = "us", root: Path | None = None, policyengine: bool | Any = False,
             artifacts: Iterable[Path] = (), corpus: Path | None = None, ref: str | None = None,
             catalogs: Iterable[Path] = ()) -> tuple[list[Finding], dict[str, Any]]:
    """``policyengine`` may be True (build the installed policyengine-us system) or a system to reuse."""
    from . import bindings

    root = root or ROOT
    findings = _schema_findings(country, root) + _bindings_schema_findings(country, root)
    m = load(country, root)
    findings += _integrity_findings(m)
    system = None
    if policyengine is not False and policyengine is not None:
        system = policyengine_system() if policyengine is True else policyengine
        findings += _policyengine_findings(m, system)
    catalog_docs = {}
    for path in catalogs:
        doc = json.loads(Path(path).read_text())
        catalog_docs[doc["program"]] = doc
    findings += bindings.findings(m, system=system, corpus=corpus, catalogs=catalog_docs)
    reports = {}
    if corpus is not None and system is not None:  # both sides' values: every current difference must be filed
        from .verify import parameters as verify_parameters

        verified = verify_parameters.verify(m, corpus, system, ref=ref)
        findings += verify_parameters.gate(m, verified)
        reports["parameters"] = {"as_of": verified["as_of"], "targets": verified["targets"], "verdicts": verified["verdicts"],
                                 "unchecked": len(verified["unchecked"])}
    if corpus is not None:
        from . import identity

        report = identity.check(m, corpus, ref)
        findings += identity.findings(m, report)
        reports["identity"] = {k: v for k, v in report.items() if k != "unresolved"} | {"unresolved": len(report["unresolved"])}
    for path in artifacts:
        report = coverage(m, json.loads(Path(path).read_text()))
        reports[str(path)] = report
        for slot, rule_ids in report["ambiguous"].items():
            findings.append(Finding("warning", "ambiguous-slot", slot, f"matched by {rule_ids}; {rule_ids[0]} wins"))
    return findings, reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings.validate")
    ap.add_argument("--country", default="us")
    ap.add_argument("--policyengine", action="store_true", help="check against the installed policyengine-us")
    ap.add_argument("--artifact", action="append", default=[], help="compiled Axiom program JSON (repeatable)")
    ap.add_argument("--corpus", type=Path, help="rulespec-us git checkout: resolve every Axiom id at the pin")
    ap.add_argument("--ref", help="with --corpus: check at this commit instead of the pin")
    ap.add_argument("--catalog", action="append", default=[], help="a program's slot catalog JSON (repeatable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    findings, reports = validate(args.country, policyengine=args.policyengine, artifacts=[Path(a) for a in args.artifact],
                                 corpus=args.corpus, ref=args.ref, catalogs=[Path(c) for c in args.catalog])
    errors = [f for f in findings if f.level == "error"]
    if args.json:
        print(json.dumps({"findings": [asdict(f) for f in findings], "coverage": reports}, indent=2))
    else:
        by_code = collections.Counter((f.level, f.code) for f in findings)
        for (level, code), n in sorted(by_code.items()):
            print(f"{level:7} {code:28} {n}")
        for f in errors[:40]:
            print(f"  ERROR {f.code}: {f.where}: {f.message}")
        params = reports.pop("parameters", None)
        if params:
            print(f"\nparameters as of {params['as_of']}: {params['targets']} cells {params['verdicts']}, "
                  f"{params['unchecked']} rows not checkable without a run")
        identity = reports.pop("identity", None)
        if identity:
            print(f"\nrulespec-us {identity['ref'][:9]}: {identity['ok']} ok, {identity['deferred']} deferred, "
                  f"{identity['unresolved']} unresolved of {identity['checked']} Axiom ids")
        for path, r in reports.items():
            print(f"\n{Path(path).name}: {r['covered']}/{r['slots']} slots covered {r['by_source']}, "
                  f"{len(r['missing'])} missing, {len(r['ambiguous'])} ambiguous")
        print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
