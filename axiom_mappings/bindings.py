"""Per-program bindings: how one consumer feeds an Axiom program from PolicyEngine and binds its outputs.

One YAML file per program and profile, ``data/<country>/bindings/<program id>.<profile>.yaml``:

- ``cut``: evaluated inside a live PolicyEngine simulation, so a slot may read any PE variable,
  computed ones included (policyengine-axiom).
- ``facts``: evaluated from a request's facts alone (axiom-api), in axiom-api's own serving-map grammar
  (``facts.py``): a slot reads the fields a request carries and computes nothing.

The same slot may be bound differently under the two profiles; ``profiles.py`` reports where.

Every slot is either a spec in the adapter grammar below, or a constant under a named presumption
(``presumed: {<presumption>: {<slot>: <value>}}``), so no constant goes undeclared. The constant's
Python type (bool, int, float, str) is the engine column type. Output and parameter names are local to
the program's module and resolve in its import closure at the program's ``corpus_ref``.

The grammar is policyengine-axiom's, which its provider executes (``provider._gather``):

    name                               a PE variable on the slot's entity
    {any_of | all_of: [names]}         logical or / and of PE booleans
    {sum_of: [names]}                  sum
    {not_of: name}                     logical not
    {project_of: name}                 a group PE variable projected onto its member persons
    {map_of: {variable, values, default}}  a PE enum decoded to numbers
    {const: value}                     only through ``presumed``
    {per_month_of: name}               a yearly PE flow, divided by 12 at a monthly engine period
    {level_of: name}                   a yearly PE stock, read as-is at the month
    {positive_of: name}                true where a PE amount (summed over members if per person) is above zero
    {household_of: name | spec}        a household value read onto the root group through its first member
    {sum_members_of: {variable, where?, per_month?}}  a person amount summed over the root group's members
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator

import yaml

if TYPE_CHECKING:
    from . import Mappings
    from .validate import Finding

PROFILES = ("cut", "facts")
LIST_ADAPTERS = ("any_of", "all_of", "sum_of")
NAME_ADAPTERS = ("not_of", "project_of", "per_month_of", "level_of", "household_of", "positive_of")
ADAPTERS = LIST_ADAPTERS + NAME_ADAPTERS + ("map_of", "const", "sum_members_of")
STATUSES = ("off", "shadow", "on")
REDUCERS = ("any", "all", "sum", "first", "max", "min")
PERSON = "person"


@dataclass(frozen=True)
class BindingSet:
    program: str
    profile: str
    path: Path  # relative to the bindings directory
    doc: dict[str, Any]

    @property
    def blocks(self) -> Iterator[tuple[str, dict[str, Any]]]:
        """(where, block) for every place slots are bound: the program, each relation, each binding.
        A facts binding set has one block, its inputs (axiom-api's grammar, see ``facts.py``)."""
        yield "inputs", self.doc
        if self.profile == "facts":
            return
        for name, rel in (self.doc.get("relations") or {}).items():
            yield f"relation {name}", rel
        for b in self.doc.get("bindings") or []:
            yield f"binding {b.get('pe_variable')}", b

    def presumed(self) -> Iterator[tuple[str, str, str, Any]]:
        """(where, slot, presumption, value) for every constant."""
        if self.profile == "facts":  # a flat, ordered map: axiom-api discloses presumptions in order
            for slot, p in (self.doc.get("presumptions") or {}).items():
                yield "inputs", slot, p.get("presumption"), p.get("value")
            return
        for where, block in self.blocks:
            for presumption, slots in (block.get("presumed") or {}).items():
                for slot, value in (slots or {}).items():
                    yield where, slot, presumption, value

    def slots(self) -> dict[str, Any]:
        """Program-level slot -> spec, constants as ``{"const": value}``, merged with binding inputs."""
        if self.profile == "facts":
            return dict(self.doc.get("inputs") or {}) | {s: {"const": v} for _, s, _, v in self.presumed()}
        merged = expand(self.doc)
        for b in self.doc.get("bindings") or []:
            if b.get("status") != "off":
                merged.update(expand(b))
        return merged


def expand(block: dict[str, Any]) -> dict[str, Any]:
    """A block's slot -> spec, with its presumed constants written as ``{"const": value}``."""
    out = dict(block.get("inputs") or {})
    for slots in (block.get("presumed") or {}).values():
        for slot, value in (slots or {}).items():
            out[slot] = {"const": value}
    return out


def load_bindings(country: str, root: Path) -> dict[tuple[str, str], BindingSet]:
    base = root / "data" / country / "bindings"
    out = {}
    for path in sorted(base.rglob("*.yaml")) if base.exists() else []:
        doc = yaml.safe_load(path.read_text()) or {}
        out[(doc.get("program"), doc.get("profile"))] = BindingSet(doc.get("program"), doc.get("profile"),
                                                                    path.relative_to(base), doc)
    return out


def canonical(doc: Any) -> str:
    """JSON that tells ``false``, ``0`` and ``0.0`` apart: a constant's type is its engine column type."""
    return json.dumps(doc, sort_keys=True)


class _Flow(dict):
    """A spec, written inline."""


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(_Flow, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v, flow_style=True))


def dump(doc: dict[str, Any]) -> str:
    """A binding set as YAML in the house layout: specs inline, one slot per line."""
    def flow(spec):
        return _Flow(spec) if isinstance(spec, dict) else spec

    def block(b: dict[str, Any]) -> dict[str, Any]:
        out = dict(b)
        if "inputs" in out:
            out["inputs"] = {k: flow(v) for k, v in out["inputs"].items()}
        for key in ("filter", "scope"):
            if key in out:
                out[key] = flow(out[key])
        return out

    doc = block(doc)
    if "relations" in doc:
        doc["relations"] = {k: block(v) for k, v in doc["relations"].items()}
    if "bindings" in doc:
        doc["bindings"] = [block(b) for b in doc["bindings"]]
    return yaml.dump(doc, Dumper=_Dumper, sort_keys=False, allow_unicode=True, width=110)


def set_status(program: str, pe_variable: str, status: str, *, profile: str = "cut", country: str = "us",
               root: Path | None = None) -> Path:
    """Set one binding's rollout status in its file, changing that one line and nothing else.

    The file is edited as text so comments and layout survive; the result is re-read and must differ
    from the original only in that status.
    """
    from . import ROOT

    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    base = Path(root or ROOT) / "data" / country / "bindings"
    path = base / f"{program}.{profile}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no {profile} bindings for {program} ({path})")
    lines = path.read_text().splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.strip() in (f"- pe_variable: {pe_variable}",
                                                                          f"- pe_variable: '{pe_variable}'")), None)
    if start is None:
        raise KeyError(f"{program}.{profile} binds no {pe_variable}")
    indent = len(lines[start]) - len(lines[start].lstrip()) + 2  # the binding's keys sit under its "- "
    end = next((i for i in range(start + 1, len(lines))
                if lines[i].strip() and len(lines[i]) - len(lines[i].lstrip()) < indent), len(lines))
    at = next((i for i in range(start + 1, end) if lines[i].startswith(" " * indent + "status:")), None)
    if at is None:
        raise KeyError(f"{program}.{profile} {pe_variable} has no status line")
    before = yaml.safe_load("".join(lines))
    lines[at] = " " * indent + f"status: {yaml.safe_dump(status).splitlines()[0]}\n"
    after = yaml.safe_load("".join(lines))
    expected = json.loads(json.dumps(before))
    next(b for b in expected["bindings"] if b["pe_variable"] == pe_variable)["status"] = status
    if canonical(after) != canonical(expected):
        raise RuntimeError(f"editing {path} changed more than the status of {pe_variable}")
    path.write_text("".join(lines))
    return path


# --------------------------------------------------------------------------
# grammar
# --------------------------------------------------------------------------

def grammar_errors(spec: Any, where: str) -> list[str]:
    if isinstance(spec, str):
        return [] if spec else [f"{where}: empty PE variable name"]
    if not (isinstance(spec, dict) and len(spec) == 1):
        return [f"{where}: a spec is a PE variable name or one adapter of {ADAPTERS}, got {spec!r}"]
    (kind, arg), = spec.items()
    if kind in LIST_ADAPTERS and isinstance(arg, list) and arg and all(isinstance(a, str) for a in arg):
        return []
    if kind in NAME_ADAPTERS and isinstance(arg, str):
        return []
    if kind == "household_of" and isinstance(arg, dict):
        return grammar_errors(arg, f"{where} household_of")
    if kind == "map_of" and isinstance(arg, dict) and isinstance(arg.get("variable"), str) \
            and isinstance(arg.get("values"), dict) and "default" in arg and set(arg) == {"variable", "values", "default"}:
        return []
    if kind == "const" and isinstance(arg, (bool, int, float, str)):
        return []
    if kind == "sum_members_of" and isinstance(arg, dict) and isinstance(arg.get("variable"), str) \
            and set(arg) <= {"variable", "where", "per_month"}:
        return grammar_errors(arg["where"], f"{where} sum_members_of where") if "where" in arg else []
    return [f"{where}: a spec is a PE variable name or one adapter of {ADAPTERS}, got {spec!r}"]


def pe_names(spec: Any) -> Iterator[str]:
    """Every PE variable a spec reads."""
    if isinstance(spec, str):
        yield spec
        return
    (kind, arg), = spec.items()
    if kind in LIST_ADAPTERS:
        yield from arg
    elif kind == "household_of":
        yield from pe_names(arg)
    elif kind in NAME_ADAPTERS:
        yield arg
    elif kind == "map_of":
        yield arg["variable"]
    elif kind == "sum_members_of":
        yield arg["variable"]
        if "where" in arg:
            yield from pe_names(arg["where"])


# --------------------------------------------------------------------------
# checks
# --------------------------------------------------------------------------

def _structure(m: Mappings, bs: BindingSet) -> list[tuple[str, str, str, str]]:
    """(level, code, where, message) without PolicyEngine or RuleSpec: references, grammar, presumptions."""
    out = []
    where0 = f"{bs.program}.{bs.profile}"
    doc = bs.doc
    program = m.programs.get(bs.program)
    if program is None:
        out.append(("error", "unknown-program", where0, "not in programs.yaml"))
    if bs.profile not in PROFILES:
        out.append(("error", "unknown-profile", where0, f"profile must be one of {PROFILES}"))
    if str(bs.path) != f"{bs.program}.{bs.profile}.yaml":
        out.append(("error", "binding-path", where0, f"file {bs.path} should be {bs.program}.{bs.profile}.yaml"))
    for consumer in doc.get("consumers") or []:
        if program is not None and consumer not in program.get("consumers", {}):
            out.append(("error", "unknown-consumer", where0, f"{consumer} has no name for this program in programs.yaml"))
    if doc.get("root_entity") not in (doc.get("entity_map") or {}):
        out.append(("error", "root-entity", where0, f"root entity {doc.get('root_entity')} is not in entity_map"))
    for where, block in bs.blocks:
        at = f"{where0} {where}"
        for slot, spec in (block.get("inputs") or {}).items():
            for e in grammar_errors(spec, f"{at} {slot}"):
                out.append(("error", "spec-grammar", at, e))
            if isinstance(spec, dict) and "const" in spec:
                out.append(("error", "undeclared-constant", at, f"{slot} is a constant: put it under presumed:<presumption>"))
        seen: dict[str, str] = {}
        for presumption, slots in (block.get("presumed") or {}).items():
            if presumption not in m.presumptions:
                out.append(("error", "unknown-presumption", at, f"presumption {presumption!r} is not defined"))
            for slot, value in (slots or {}).items():
                if not isinstance(value, (bool, int, float, str)):
                    out.append(("error", "presumed-value", at, f"{slot}: a constant is a bool, number or text, got {value!r}"))
                if slot in (block.get("inputs") or {}):
                    out.append(("error", "slot-bound-twice", at, f"{slot} is both an input and presumed"))
                if slot in seen:
                    out.append(("error", "slot-bound-twice", at, f"{slot} is presumed under {seen[slot]} and {presumption}"))
                seen[slot] = presumption
    rel_filters = [(n, r.get("filter")) for n, r in (doc.get("relations") or {}).items() if r.get("filter") is not None]
    for name, spec in rel_filters + ([("scope", doc["scope"])] if doc.get("scope") is not None else []):
        for e in grammar_errors(spec, f"{where0} {name}"):
            out.append(("error", "spec-grammar", where0, e))
    program_slots = expand(doc)
    names = [b.get("pe_variable") for b in doc.get("bindings") or []]
    for n in sorted({n for n in names if names.count(n) > 1}):
        out.append(("error", "bound-twice", where0, f"{n} is bound more than once"))
    for b in doc.get("bindings") or []:
        at = f"{where0} binding {b.get('pe_variable')}"
        if b.get("status") not in STATUSES:
            out.append(("error", "binding-status", at, f"status must be one of {STATUSES}"))
        if b.get("reduce") is not None and b["reduce"] not in REDUCERS:
            out.append(("error", "binding-reduce", at, f"reduce must be one of {REDUCERS}"))
        for slot, spec in expand(b).items():
            if slot in program_slots and canonical(program_slots[slot]) != canonical(spec):
                out.append(("error", "slot-conflict", at, f"{slot} is bound differently here and at program level"))
    return out


def _entities(system) -> dict[str, Any]:
    return {e.key: e for e in system.entities}


def _computed(variable) -> bool:
    return bool(variable.formulas or getattr(variable, "adds", None) or getattr(variable, "subtracts", None))


def _value_kind(variable) -> str:
    if variable.value_type is bool:
        return "bool"
    if variable.value_type is str or getattr(variable, "possible_values", None) is not None:  # enums decode to text
        return "text"
    return "number"


def _const_kind(value: Any) -> str:
    return "bool" if isinstance(value, bool) else "text" if isinstance(value, str) else "number"


class _Typer:
    """Static entity, period and type checks that mirror what the provider enforces at run time."""

    def __init__(self, system, profile: str, where: str, out: list):
        self.system, self.profile, self.where, self.out = system, profile, where, out
        self.entities = _entities(system)

    def err(self, code: str, message: str, level: str = "error") -> None:
        self.out.append((level, code, self.where, message))

    def var(self, name: str):
        v = self.system.variables.get(name)
        if v is None:
            self.err("unknown-pe-variable", f"no PE variable {name}")
        return v

    def group(self, key: str) -> bool:
        e = self.entities.get(key)
        return e is not None and not e.is_person

    def on(self, v, name: str, entity: str) -> None:
        if v is not None and v.entity.key != entity:
            self.err("pe-entity", f"{name} is on {v.entity.key}, the slot is on {entity}")

    def yearly(self, v, name: str, adapter: str) -> None:
        if v is not None and v.definition_period != "year":
            self.err("pe-period", f"{adapter} {name}: the PE variable is defined per {v.definition_period}, not per year")

    def kind(self, spec: Any, entity: str) -> str | None:
        """Check ``spec`` evaluated on PE entity ``entity``; return bool | number | text (None if unknown)."""
        if isinstance(spec, str):
            v = self.var(spec)
            self.on(v, spec, entity)
            return _value_kind(v) if v is not None else None
        (kind, arg), = spec.items()
        if kind in ("any_of", "all_of"):
            for n in arg:
                if self.kind(n, entity) not in ("bool", None):
                    self.err("logic-of-non-boolean", f"{kind} reads {n}, which is not boolean", "warning")
            return "bool"
        if kind == "sum_of":
            for n in arg:
                if self.kind(n, entity) in ("bool", "text"):
                    self.err("sum-of-non-number", f"sum_of reads {n}, which is not a number")
            return "number"
        if kind == "not_of":
            if self.kind(arg, entity) not in ("bool", None):
                self.err("logic-of-non-boolean", f"not_of reads {arg}, which is not boolean", "warning")
            return "bool"
        if kind == "const":
            return _const_kind(arg)
        if kind in ("level_of", "per_month_of"):
            v = self.var(arg)
            self.on(v, arg, entity)
            self.yearly(v, arg, kind)
            quantity = getattr(v, "quantity_type", None)
            if kind == "level_of" and quantity == "flow":  # flow is PE's default, so this is only a hint
                self.err("level-of-flow", f"level_of {arg}: PE marks it a flow; a flow read as a level is a year's "
                         "total at every month", "warning")
            if kind == "per_month_of" and quantity == "stock":  # stock is set deliberately in PE, so trust it
                self.err("per-month-of-stock", f"per_month_of {arg}: a stock divided by 12 is not a monthly amount")
            return "number"
        if kind == "positive_of":
            v = self.var(arg)
            if v is not None and v.entity.key != entity and not (self.group(entity) and v.entity.key == PERSON):
                self.err("pe-entity", f"positive_of {arg}: {v.entity.key} is neither {entity} nor its member")
            return "bool"
        if kind == "household_of":
            if not self.group(entity):
                self.err("pe-entity", f"household_of on {entity}: the slot must be on a group entity")
            return self.kind(arg, "household")
        if kind == "sum_members_of":
            v = self.var(arg["variable"])
            if not self.group(entity):
                self.err("pe-entity", f"sum_members_of on {entity}: the slot must be on a group entity")
            elif v is not None and v.entity.key != PERSON:
                self.err("pe-entity", f"sum_members_of {arg['variable']}: the PE variable is on {v.entity.key}, not person")
            if arg.get("per_month"):
                self.yearly(v, arg["variable"], "sum_members_of per_month")
            if v is not None and _value_kind(v) != "number":
                self.err("sum-of-non-number", f"sum_members_of {arg['variable']} is not a number")
            if "where" in arg and self.kind(arg["where"], PERSON) not in ("bool", None):
                self.err("where-not-boolean", "sum_members_of where is not boolean")
            return "number"
        if kind == "project_of":
            v = self.var(arg)
            if v is not None and not (self.group(v.entity.key) and entity == PERSON):
                self.err("pe-entity", f"project_of {arg}: {v.entity.key} does not project onto {entity}")
            return _value_kind(v) if v is not None else None
        if kind == "map_of":
            v = self.var(arg["variable"])
            self.on(v, arg["variable"], entity)
            enum = getattr(v, "possible_values", None) if v is not None else None
            if v is not None and enum is None:
                self.err("map-of-not-enum", f"map_of {arg['variable']}: the PE variable is not an enum")
            elif enum is not None:
                unknown = sorted(set(arg["values"]) - {e.name for e in enum})
                if unknown:
                    self.err("map-of-unknown-value", f"map_of {arg['variable']}: {unknown} are not values of {enum.__name__}")
            return "number"
        return None


def _policyengine(bs: BindingSet, system) -> list[tuple[str, str, str, str]]:
    from policyengine_core.parameters import get_parameter

    out: list = []
    doc = bs.doc
    root = (doc.get("entity_map") or {}).get(doc.get("root_entity"))
    base = f"{bs.program}.{bs.profile}"
    if root not in _entities(system):
        out.append(("error", "pe-entity", base, f"root entity maps to {root!r}, not a PE entity"))
        return out
    member = PERSON
    for where, block in bs.blocks:
        entity = member if where.startswith("relation ") else root
        for slot, spec in (block.get("inputs") or {}).items():
            if not grammar_errors(spec, slot):
                _Typer(system, bs.profile, f"{base} {where} {slot}", out).kind(spec, entity)
    for name, rel in (doc.get("relations") or {}).items():
        if rel.get("filter") is not None and not grammar_errors(rel["filter"], name):
            kind = _Typer(system, bs.profile, f"{base} relation {name} filter", out).kind(rel["filter"], member)
            if kind not in ("bool", None):
                out.append(("error", "filter-not-boolean", f"{base} relation {name}", "a relation filter must be boolean"))
    if doc.get("scope") is not None and not grammar_errors(doc["scope"], "scope"):
        _Typer(system, bs.profile, f"{base} scope", out).kind(doc["scope"], root)
    for b in doc.get("bindings") or []:
        at = f"{base} binding {b.get('pe_variable')}"
        if b.get("pe_variable") not in system.variables:
            out.append(("error", "unknown-pe-variable", at, "the bound PE variable does not exist"))
        for axiom_param, path in (b.get("parameters") or {}).items():
            try:
                get_parameter(system.parameters, path)
            except Exception:
                out.append(("error", "unknown-pe-parameter", at, f"{axiom_param} -> {path}: no such PE parameter"))
    return out


def _corpus(m: Mappings, bs: BindingSet, corpus_root: Path) -> list[tuple[str, str, str, str]]:
    from .corpus import Corpus

    program = m.programs.get(bs.program)
    if program is None or program.get("assembled_by"):
        return []
    corpus = Corpus(corpus_root, program.get("corpus_ref") or m.pins.get("rulespec_us"))
    names = corpus.closure_names(program["axiom"])
    at = corpus.commit[:9] if corpus.commit else str(corpus_root)
    out = []
    base = f"{bs.program}.{bs.profile}"
    wanted = [("output", b["axiom_output"], f"binding {b['pe_variable']}") for b in bs.doc.get("bindings") or []]
    wanted += [("output", n, "expose") for n in bs.doc.get("expose") or []]
    wanted += [("parameter", p, f"binding {b['pe_variable']}") for b in bs.doc.get("bindings") or []
               for p in (b.get("parameters") or {})]
    for what, name, where in wanted:
        if name not in names:
            out.append(("error", "unknown-axiom-name", f"{base} {where}",
                        f"{what} {name} is not in {program['axiom']} or its imports at {at}"))
    return out


def _catalog(bs: BindingSet, catalog: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    """Every slot the compiled program reads is bound, and nothing else is."""
    out = []
    base = f"{bs.program}.{bs.profile}"
    blocks = [("inputs", set(catalog.get("root_inputs", [])), set(bs.slots()))]
    for name, slots in (catalog.get("relations") or {}).items():
        rel = (bs.doc.get("relations") or {}).get(name)
        blocks.append((f"relation {name}", set(slots), set(expand(rel)) if rel else set()))
    for name in set(bs.doc.get("relations") or {}) - set(catalog.get("relations") or {}):
        out.append(("error", "stray-relation", base, f"relation {name} is not in the compiled program"))
    for where, expected, bound in blocks:
        for slot in sorted(expected - bound):
            out.append(("error", "unbound-slot", f"{base} {where}", f"{slot} is read by the program but not bound"))
        for slot in sorted(bound - expected):
            out.append(("error", "stray-slot", f"{base} {where}", f"{slot} is not an input of the compiled program"))
    return out


def findings(m: Mappings, *, system=None, corpus: Path | None = None,
             catalogs: dict[str, dict[str, Any]] | None = None) -> list[Finding]:
    from .validate import Finding

    from . import facts

    raw = []
    for bs in m.bindings.values():
        if bs.profile == "facts":
            raw += facts.checks(m, bs, system=system, corpus=corpus)
            continue
        raw += _structure(m, bs)
        if system is not None:
            raw += _policyengine(bs, system)
        if corpus is not None:
            raw += _corpus(m, bs, corpus)
        if catalogs and bs.program in catalogs:
            raw += _catalog(bs, catalogs[bs.program])
    return [Finding(*r) for r in raw]
