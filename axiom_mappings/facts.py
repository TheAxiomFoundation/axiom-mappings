"""The ``facts`` profile: axiom-api's serving-map grammar, and a reference interpreter for it.

A facts binding reads a PolicyEngine-shaped request (``people``, ``spm_units``, ``tax_units`` ... with
period-keyed values), never a simulation. Each input slot names where its value comes from:

    from: <field>                        a field of the first unit record carrying it (spm_units,
                                         households, tax_units, families, marital_units), or of each person
    from: {sum: [fields], scope: S}      summed over people / tax_unit.members, or one unit record
    from: {count: people | spm_unit.members | tax_unit.members}
    from: {builtin: pe_filing_status | aged_or_blind_count}
    period: annual | monthly-from-annual | monthly     round: cents     map: {value: code}, map_default

The interpreter below is a line-by-line port of axiom-api's ``src/household-compat.ts`` (``annualValue``,
``pointValue``, ``deriveUnitInput``, ``derivePersonInput``, ``mapValue``, ``builtinValue``,
``extractFacts``). ``conformance/facts.json`` holds cases whose expected values come from running the
TypeScript; the Python must reproduce them, and axiom-api runs the same file, so the two stay one
semantics.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from . import ROOT

UNIT_GROUPS = ("spm_units", "households", "tax_units", "families", "marital_units")
PERSON_ONLY_SOURCES = {"age", "is_disabled", "is_blind", "is_pregnant"}
PERIODS = ("annual", "monthly-from-annual", "monthly")
ROUNDS = ("none", "cents", "whole-dollar")
SCOPES = ("people", "tax_unit.members", "spm_unit", "tax_unit", "household")
COUNTS = ("people", "spm_unit.members", "tax_unit.members")
BUILTINS = ("pe_filing_status", "aged_or_blind_count")
CONFORMANCE = ROOT / "conformance" / "facts.json"


# ------------------------------------------------------------------ grammar

def grammar_errors(spec: Any, where: str) -> list[str]:
    if not isinstance(spec, dict) or "from" not in spec:
        return [f"{where}: a facts input is an object with `from`"]
    out = []
    unknown = set(spec) - {"from", "period", "round", "map", "map_default", "notes"}
    if unknown:
        out.append(f"{where}: unknown keys {sorted(unknown)}")
    src = spec["from"]
    if isinstance(src, str):
        pass
    elif isinstance(src, dict) and set(src) <= {"sum", "scope"} and isinstance(src.get("sum"), list) and src["sum"]:
        if src.get("scope", "people") not in SCOPES:
            out.append(f"{where}: sum scope must be one of {SCOPES}")
    elif isinstance(src, dict) and set(src) == {"count"}:
        if src["count"] not in COUNTS:
            out.append(f"{where}: count must be one of {COUNTS}")
    elif isinstance(src, dict) and set(src) == {"builtin"}:
        if src["builtin"] not in BUILTINS:
            out.append(f"{where}: builtin must be one of {BUILTINS}")
    else:
        out.append(f"{where}: `from` is a field, {{sum, scope}}, {{count}} or {{builtin}}, got {src!r}")
    if spec.get("period") is not None and spec["period"] not in PERIODS:
        out.append(f"{where}: period must be one of {PERIODS}")
    if spec.get("round") is not None and spec["round"] not in ROUNDS:
        out.append(f"{where}: round must be one of {ROUNDS}")
    if "map_default" in spec and "map" not in spec:
        out.append(f"{where}: map_default without map")
    return out


def fields(spec: dict[str, Any]) -> list[str]:
    """The PolicyEngine fields an input reads."""
    src = spec["from"]
    if isinstance(src, str):
        return [src]
    if "sum" in src:
        return list(src["sum"])
    return []


# ------------------------------------------------------------------ reference interpreter (household-compat.ts)

def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def _js_round(value: float) -> float:
    return math.floor(value + 0.5)  # Math.round: halves go up


def annual_value(periods: Any, year: str) -> float:
    if not isinstance(periods, dict):
        return 0
    bare = _number(periods.get(year))
    if bare is not None:
        return bare
    monthly = [_number(v) for k, v in periods.items() if k.startswith(f"{year}-")]
    monthly = [v for v in monthly if v is not None]
    if not monthly:
        return 0
    if len(monthly) == 1:
        return monthly[0] * 12
    return sum(monthly)


def point_value(periods: Any, year: str) -> Any:
    if not isinstance(periods, dict):
        return None
    if periods.get(year) is not None:
        return periods[year]
    return next(iter(periods.values()), None)


def _round2(value: float) -> float:
    return _js_round(value * 100) / 100


def apply_period(annual: float, spec: dict[str, Any]) -> float:
    value = annual / 12 if spec.get("period", "annual") == "monthly-from-annual" else annual
    return _round2(value) if spec.get("round") == "cents" else value


def _first_record_with(household: dict, groups: tuple[str, ...], field: str) -> dict | None:
    fallback = None
    for group in groups:
        record = next(iter((household.get(group) or {}).values()), None)
        if not record:
            continue
        if field in record:
            return record
        fallback = fallback or record
    return fallback


def map_value(value: Any, spec: dict[str, Any]) -> Any:
    if "map" not in spec:
        return value
    if value is None:
        return spec.get("map_default")
    key = _js_string(value)
    if key in spec["map"]:
        return spec["map"][key]
    return spec.get("map_default")


def _js_string(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _tax_unit_members(household: dict) -> list[dict]:
    people = household.get("people") or {}
    unit = next(iter((household.get("tax_units") or {}).values()), None)
    members = unit.get("members") if isinstance(unit, dict) else None
    if not isinstance(members, list):
        return list(people.values())
    return [people[m] for m in members if isinstance(m, str) and m in people]


def _truthy(periods: Any, year: str) -> bool:
    return point_value(periods, year) is True


def _head_and_spouse(household: dict, year: str) -> list[dict]:
    members = _tax_unit_members(household)
    flagged = [p for p in members if _truthy(p.get("is_tax_unit_head"), year) or _truthy(p.get("is_tax_unit_spouse"), year)]
    if flagged:
        return flagged
    adults = [p for p in members if (a := _number(point_value(p.get("age"), year))) is None or a >= 18]
    return adults[:2]


def builtin_value(household: dict, builtin: str, year: str) -> Any:
    if builtin == "pe_filing_status":
        unit = next(iter((household.get("tax_units") or {}).values()), None)
        explicit = point_value(unit.get("filing_status"), year) if unit else None
        if isinstance(explicit, str) and explicit:
            return explicit.upper()
        members = _tax_unit_members(household)
        hs = _head_and_spouse(household, year)
        spouse_flagged = any(_truthy(p.get("is_tax_unit_spouse"), year) for p in members)
        married = spouse_flagged or (len(hs) == 2 and any(
            isinstance(mu.get("members"), list) and len(mu["members"]) == 2
            for mu in (household.get("marital_units") or {}).values()))
        if married:
            return "JOINT"
        dependents = [p for p in members if not any(p is h for h in hs) and (
            _truthy(p.get("is_tax_unit_dependent"), year)
            or (_number(point_value(p.get("age"), year)) if _number(point_value(p.get("age"), year)) is not None else 99) < 19)]
        return "HEAD_OF_HOUSEHOLD" if dependents else "SINGLE"
    if builtin == "aged_or_blind_count":
        count = 0
        for p in _head_and_spouse(household, year):
            age = _number(point_value(p.get("age"), year))
            if age is not None and age >= 65:
                count += 1
            if _truthy(p.get("is_blind"), year):
                count += 1
        return count
    return None


def derive_unit_input(household: dict, spec: dict[str, Any], year: str) -> Any:
    source = spec["from"]
    people = list((household.get("people") or {}).values())
    if isinstance(source, str):
        record = _first_record_with(household, UNIT_GROUPS, source)
        periods = record.get(source) if record else None
        if "map" in spec:
            return map_value(point_value(periods, year), spec)
        if spec.get("period") is None and _number(point_value(periods, year)) is None:
            return point_value(periods, year)
        return apply_period(annual_value(periods, year), spec)
    if "builtin" in source:
        value = builtin_value(household, source["builtin"], year)
        return map_value(value, spec) if "map" in spec else value
    if "count" in source:
        if source["count"] == "people":
            return max(1, len(people))
        group = "spm_units" if source["count"] == "spm_unit.members" else "tax_units"
        unit = next(iter((household.get(group) or {}).values()), None)
        members = [m for m in unit["members"] if isinstance(m, str)] if isinstance(unit, dict) and isinstance(unit.get("members"), list) else None
        return len(members or []) or len(people) or 1
    scope = source.get("scope", "people")
    annual = 0
    if scope in ("people", "tax_unit.members"):
        for person in (_tax_unit_members(household) if scope == "tax_unit.members" else people):
            for name in source["sum"]:
                annual += annual_value(person.get(name), year)
    else:
        group = {"spm_unit": "spm_units", "tax_unit": "tax_units"}.get(scope, "households")
        record = next(iter((household.get(group) or {}).values()), None) or {}
        for name in source["sum"]:
            annual += annual_value(record.get(name), year)
    return apply_period(annual, spec)


def derive_person_input(person: dict, spec: dict[str, Any], year: str) -> Any:
    source = spec["from"]
    if not isinstance(source, str):
        return None
    periods = person.get(source)
    if "map" in spec:
        return map_value(point_value(periods, year), spec)
    if spec.get("period") in ("annual", None):
        point = point_value(periods, year)
        if _number(point) is None:
            return point
        return point if spec.get("period") is None else apply_period(annual_value(periods, year), spec)
    return apply_period(annual_value(periods, year), spec)


def extract_facts(household: dict, inputs: dict[str, dict[str, Any]], year: str) -> dict[str, Any]:
    facts = {}
    for name, spec in inputs.items():
        if isinstance(spec["from"], str) and spec["from"] in PERSON_ONLY_SOURCES:
            continue
        value = derive_unit_input(household, spec, year)
        if value is not None:
            facts[name] = value
    return facts


# ------------------------------------------------------------------ conformance

def conformance_cases(path: Path = CONFORMANCE) -> list[dict[str, Any]]:
    return json.loads(path.read_text())["cases"]


def run_case(case: dict[str, Any]) -> Any:
    if case["kind"] == "unit":
        return extract_facts(case["household"], case["inputs"], case["year"])
    return {name: derive_person_input(case["person"], spec, case["year"]) for name, spec in case["inputs"].items()}


def slug(program: str) -> str:
    """axiom-api's serving-map file name for a program id: ``us/snap`` -> ``us-snap.json``."""
    return re.sub(r"[/]", "-", program) + ".json"


def same(a: Any, b: Any) -> bool:
    """Equal as JSON values in JavaScript: one number type (1 == 1.0), but a boolean is never a number."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


# ------------------------------------------------------------------ validation

SCOPE_ENTITY = {"people": "person", "tax_unit.members": "person", "spm_unit": "spm_unit", "tax_unit": "tax_unit",
                "household": "household"}


def checks(m, bs, *, system=None, corpus=None) -> list[tuple[str, str, str, str]]:
    """(level, code, where, message) for one facts binding set."""
    out = []
    doc = bs.doc
    base = f"{bs.program}.facts"
    program = m.programs.get(bs.program)
    if program is None:
        out.append(("error", "unknown-program", base, "not in programs.yaml"))
    if str(bs.path) != f"{bs.program}.facts.yaml":
        out.append(("error", "binding-path", base, f"file {bs.path} should be {bs.program}.facts.yaml"))
    for consumer in doc.get("consumers") or []:
        if program is not None and consumer not in program.get("consumers", {}):
            out.append(("error", "unknown-consumer", base, f"{consumer} has no name for this program in programs.yaml"))
    inputs = doc.get("inputs") or {}
    for slot, spec in inputs.items():
        for e in grammar_errors(spec, f"{base} inputs {slot}"):
            out.append(("error", "spec-grammar", f"{base} inputs", e))
    for slot, p in (doc.get("presumptions") or {}).items():
        if p.get("presumption") not in m.presumptions:
            out.append(("error", "unknown-presumption", f"{base} presumptions", f"{slot}: presumption {p.get('presumption')!r} is not defined"))
        if slot in inputs:
            out.append(("error", "slot-bound-twice", f"{base} presumptions", f"{slot} is both an input and presumed"))
    if system is not None:
        variables = system.variables
        for slot, spec in inputs.items():
            if grammar_errors(spec, slot):
                continue
            scope = spec["from"].get("scope", "people") if isinstance(spec["from"], dict) and "sum" in spec["from"] else None
            for name in fields(spec):
                v = variables.get(name)
                at = f"{base} inputs {slot}"
                if v is None:
                    out.append(("error", "unknown-pe-variable", at, f"no PolicyEngine variable {name}"))
                    continue
                if scope and v.entity.key != SCOPE_ENTITY[scope]:
                    out.append(("error", "pe-entity", at, f"sums {name} over {scope}, but it is on {v.entity.key}"))
        for name, entry in (doc.get("variables") or {}).items():
            v = variables.get(name)
            if v is None:
                out.append(("error", "unknown-pe-variable", f"{base} variables", f"no PolicyEngine variable {name}"))
            elif entry.get("entity") and entry["entity"] != v.entity.key:
                out.append(("error", "pe-entity", f"{base} variables {name}", f"declared {entry['entity']}, PolicyEngine has {v.entity.key}"))
        for name in doc.get("overrides") or {}:
            if name not in variables:
                out.append(("error", "unknown-pe-variable", f"{base} overrides", f"no PolicyEngine variable {name}"))
    if corpus is not None:
        from .corpus import Corpus

        at = Corpus(corpus, m.pins.get("rulespec_us"))
        for target in doc.get("targets") or []:
            if target.get("kind") == "root" and at.module(target["root"]) is None:
                out.append(("error", "unknown-target-root", f"{base} targets",
                            f"{target['root']} is not a module at {at.commit[:9] if at.commit else corpus}"))
    return out
