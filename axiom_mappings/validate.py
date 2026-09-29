"""Validate the map: schema, integrity, vocabulary, PolicyEngine metadata, and coverage of Axiom programs.

    python -m axiom_mappings.validate --country us [--policyengine] [--artifact compiled.json ...] [--json]

Errors fail the run (exit 1); warnings are reported. ``--policyengine`` checks every mapped
PolicyEngine variable and parameter against the installed policyengine-us (pinned in pins.yaml).
``--artifact`` reads a compiled Axiom program and reports which of its input slots the rules
cover, which they miss, and which more than one rule matches.
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
    for r in m.outputs:
        comparison = r.get("comparison")
        if comparison and comparison not in COMPARISONS:
            out.append(Finding("warning", "comparison-vocabulary", r["axiom"] or "?",
                               f"comparison {comparison!r} is not one of {sorted(COMPARISONS)}"))
        if r["type"] == "direct_variable" and not (r.get("entity") and r.get("period")):
            out.append(Finding("warning", "incomplete-output", r["axiom"] or "?",
                               "direct_variable without entity or period"))
    return out


def _policyengine_findings(m: Mappings) -> list[Finding]:
    import importlib.metadata

    from policyengine_core.parameters import get_parameter
    from policyengine_us import CountryTaxBenefitSystem

    out: list[Finding] = []
    installed = importlib.metadata.version("policyengine-us")
    pinned = m.pins.get("policyengine_us")
    if pinned and installed != pinned:
        out.append(Finding("warning", "policyengine-version", "pins", f"installed {installed}, pinned {pinned}"))
    system = CountryTaxBenefitSystem()
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


def validate(country: str = "us", root: Path | None = None, policyengine: bool = False,
             artifacts: Iterable[Path] = ()) -> tuple[list[Finding], dict[str, Any]]:
    root = root or ROOT
    findings = _schema_findings(country, root)
    m = load(country, root)
    findings += _integrity_findings(m)
    if policyengine:
        findings += _policyengine_findings(m)
    reports = {}
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
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    findings, reports = validate(args.country, policyengine=args.policyengine, artifacts=[Path(a) for a in args.artifact])
    errors = [f for f in findings if f.level == "error"]
    if args.json:
        print(json.dumps({"findings": [asdict(f) for f in findings], "coverage": reports}, indent=2))
    else:
        by_code = collections.Counter((f.level, f.code) for f in findings)
        for (level, code), n in sorted(by_code.items()):
            print(f"{level:7} {code:28} {n}")
        for f in errors[:40]:
            print(f"  ERROR {f.code}: {f.where}: {f.message}")
        for path, r in reports.items():
            print(f"\n{Path(path).name}: {r['covered']}/{r['slots']} slots covered {r['by_source']}, "
                  f"{len(r['missing'])} missing, {len(r['ambiguous'])} ambiguous")
        print(f"\n{len(errors)} error(s), {len(findings) - len(errors)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
