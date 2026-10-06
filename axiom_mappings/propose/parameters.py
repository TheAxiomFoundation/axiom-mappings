"""Propose PolicyEngine parameters for Axiom parameter rules that have no mapping yet.

    python -m axiom_mappings propose parameters --corpus <rulespec-us> [--module PREFIX] [--evaluate]

A candidate is admitted by identity: it cites the same law as the Axiom rule (``citations.py``: the
rule's ``source`` and module id against the parameter's own or inherited ``reference``), or it sits in
the PolicyEngine subtree where reviewed sibling rules are mapped and the names overlap (the two sides
often cite different authorities for one fact: a statute against the bill that amended it). It must
have a compatible unit (money, rate) and the same shape: a scalar rule proposes a PolicyEngine value, a
table rule a node whose keys are the table's rows. Candidates are scored by identity strength, name
overlap, and how the two value histories compare (``verify.parameters``), which is evidence only.

``--evaluate`` replays the reviewed map: for every mapped scalar or table parameter, would the proposer
have offered the PolicyEngine parameter a reviewer chose? It reports how often it is first and in the top three.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from .. import Mappings
from ..citations import RELATIONS, Citation, of_references, of_rule, references
from ..corpus import Corpus, split_id
from ..verify.parameters import Target, _axiom_versions, _pe_series, compare
from . import bound, bound_to, similarity

UNIT = {"Money": "money", "USD": "money", "currency-USD": "money", "Rate": "rate", "/1": "rate"}
VALUE_RANK = {"match": 0, "stale-axiom": 1, "stale-pe": 1, "uncovered-axiom": 2, "uncovered-pe": 2, "no-overlap": 2, "disagree": 3}


@dataclass
class PEIndex:
    """Every PolicyEngine parameter and node by the law it cites (its own reference, else its nearest ancestor's)."""
    system: Any
    objects: dict[str, Any] = field(default_factory=dict)
    cites: dict[str, list[Citation]] = field(default_factory=dict)
    by_key: dict[tuple, list[str]] = field(default_factory=lambda: defaultdict(list))

    @classmethod
    def build(cls, system) -> PEIndex:
        index = cls(system)
        own: dict[str, list[Citation]] = {}
        for obj in system.parameters.get_descendants():
            name = obj.name
            index.objects[name] = obj
            own[name] = of_references(references(getattr(obj, "metadata", None) or {}))
        for name in index.objects:
            parts = name.split(".")
            cites: list[Citation] = []
            for i in range(len(parts), 0, -1):  # nearest ancestor that cites anything
                cites = own.get(".".join(parts[:i])) or []
                if cites:
                    break
            index.cites[name] = cites
            for c in {c.key for c in cites}:
                index.by_key[c].append(name)
        return index

    def names_under(self, prefix: str) -> list[str]:
        """Every parameter and node whose path starts with ``prefix``."""
        import bisect

        if not hasattr(self, "_sorted"):
            self._sorted = sorted(self.objects)
        i = bisect.bisect_left(self._sorted, prefix)
        out = []
        while i < len(self._sorted) and self._sorted[i].startswith(prefix):
            out.append(self._sorted[i])
            i += 1
        return out

    def boolean(self, name: str) -> bool:
        obj = self.objects.get(name)
        values = getattr(obj, "values_list", None) or []
        return bool(values) and isinstance(values[0].value, bool)

    def unit(self, name: str) -> str | None:
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            obj = self.objects.get(".".join(parts[:i]))
            unit = (getattr(obj, "metadata", None) or {}).get("unit") if obj is not None else None
            if unit:
                return UNIT.get(str(unit), "other")
        return None


def _shape(rule: dict[str, Any]) -> tuple[str, list]:
    rows = sorted({k for v in rule.get("versions") or [] for k in (v.get("values") or {})}, key=str)
    return ("table", rows) if rows else ("scalar", [])


def _fits(index: PEIndex, name: str, shape: str, rows: list) -> bool:
    obj = index.objects[name]
    kind = type(obj).__name__
    if shape == "scalar":
        return kind == "Parameter"
    children = getattr(obj, "children", None) or {}
    return kind == "ParameterNode" and bool(children) and {str(r) for r in rows} <= set(children)


def _values(index: PEIndex, axiom_id: str, rule: dict[str, Any], name: str, shape: str, rows: list, as_of: str):
    """How the two value histories compare (evidence for a reviewer, never identity)."""
    cells = [(None, ())] if shape == "scalar" else [(r, (str(r),)) for r in rows]
    verdicts = Counter()
    for key, selector in cells:
        versions = _axiom_versions(rule, axiom_id, key)
        series = _pe_series(index.system, name, selector)
        if isinstance(versions, str) or isinstance(series, str):
            return None
        t = Target(name, selector, "", comparison="money" if index.unit(name) == "money" else None)
        t.versions = versions
        verdicts[compare(t, series, as_of)["verdict"]] += 1
    return min(verdicts, key=lambda v: -VALUE_RANK.get(v, 2)) if verdicts else None  # the worst cell speaks


def _program(m: Mappings, path: str) -> str | None:
    """The registry program of the mapped parameter sharing the longest path prefix with ``path``."""
    best, depth = None, 0
    for row in m.parameters:
        other = row.get("policyengine_parameter", "")
        common = 0
        for a, b in zip(other.split("."), path.split(".")):
            if a != b:
                break
            common += 1
        if common > depth:
            best, depth = row.get("program"), common
    return best if depth >= 3 else None


WEIGHT = {"binding": 4.0, "exact": 3.0, "within": 2.5, "neighbourhood": 1.5, "section": 1.0}
VALUE_BONUS = {"match": 0.5, "stale-axiom": 0.2, "stale-pe": 0.2, "disagree": -1.0}  # same date, other value: doubt


def _pe_name(path: str) -> str:
    return ".".join(path.split(".")[-3:])  # the specific end of the path, not the shared agency prefix


def _neighbourhood(m: Mappings, index: PEIndex, module: str, exclude: str | None) -> dict[str, str]:
    """PolicyEngine subtrees that reviewed siblings of ``module`` map into: subtree -> the sibling row."""
    parts = module.split("/")
    for depth in range(len(parts), 1, -1):  # the closest reviewed siblings: same module, then same directory...
        stem = "/".join(parts[:depth])
        rows = [r for r in m.parameters if r.get("axiom") and r["axiom"] != exclude
                and (r["axiom"].split("#")[0] == stem or r["axiom"].startswith(stem + "/"))]
        if rows:
            out = {}
            for r in rows:  # the parent: where the sibling's value (or table) sits among its peers
                out.setdefault(r["policyengine_parameter"].rpartition(".")[0], r["axiom"])
            return out
    return {}


def candidates(index: PEIndex, m: Mappings, module: str, name: str, rule: dict[str, Any], as_of: str,
               limit: int = 3, exclude: str | None = None, reviewed: dict | None = None) -> tuple[list[dict[str, Any]], str | None]:
    """Ranked candidates for one Axiom parameter rule, or ([], why none).

    Identity admits a candidate: a shared citation (exact, within, or the same section), or a
    neighbourhood (a reviewed sibling rule is mapped into the candidate's PolicyEngine subtree, and the
    names overlap). Score = identity weight + 2 x name overlap + a value bonus (evidence only).
    """
    cites = of_rule(module, rule)
    shape, rows = _shape(rule)
    unit = UNIT.get(str(rule.get("unit"))) or UNIT.get(str(rule.get("dtype")))
    found: dict[str, dict[str, Any]] = {}

    def admit(pe: str, relation: str, evidence: list[str]) -> None:
        if pe in found and WEIGHT[found[pe]["citation"]] >= WEIGHT[relation]:
            return
        if not _fits(index, pe, shape, rows):
            return
        pe_unit = index.unit(pe)
        if unit and pe_unit in ("money", "rate") and pe_unit != unit:
            return  # money against a rate is never the same law
        if unit and index.boolean(pe):
            return  # an amount or rate is not a switch
        overlap = round(similarity(name, _pe_name(pe)), 2)
        if relation == "section" and overlap < 0.15:
            return  # the same section with nothing else in common is noise
        found[pe] = {"policyengine_parameter": pe, "citation": relation, "unit": pe_unit or "unknown",
                     "name_overlap": overlap, "evidence": evidence}

    for pe, program in bound_to(reviewed, "parameter", module, name) if reviewed else []:
        if pe in index.objects:
            admit(pe, "binding", [f"the reviewed {program} binding projects this parameter onto {pe}"])
    for key in {c.key for c in cites}:
        for pe in index.by_key.get(key, []):
            rel = min((r for a in cites for p in index.cites[pe] if (r := a.relation(p))), key=RELATIONS.index, default=None)
            if rel:
                a, p = next((a, p) for a in cites for p in index.cites[pe] if a.relation(p) == rel)
                admit(pe, rel, [f"Axiom cites {a}", f"PolicyEngine cites {p}"])
    neighbourhood = _neighbourhood(m, index, module, exclude)
    for subtree, sibling in neighbourhood.items():
        prefix = subtree + "."
        for pe in index.names_under(prefix):
            if similarity(name, _pe_name(pe)) >= 0.25:
                admit(pe, "neighbourhood", [f"reviewed sibling {sibling} is mapped under {subtree}"])
    if not found:
        if not cites and not neighbourhood:
            return [], "no citation this proposer reads, and no reviewed sibling"
        return [], "no PolicyEngine parameter of this shape and unit cites the same law or sits where siblings map"
    ranked = sorted(found.values(), key=lambda c: -(WEIGHT[c["citation"]] + 2 * c["name_overlap"]))[: max(limit * 3, 8)]
    axiom_id = f"{module}#{name}"
    for c in ranked:
        c["values"] = _values(index, axiom_id, rule, c["policyengine_parameter"], shape, rows, as_of)
        if shape == "table":
            c["parameter_key_input"] = "<the input keying the table>"
        c["score"] = round(WEIGHT[c["citation"]] + 2 * c["name_overlap"] + VALUE_BONUS.get(c["values"], 0.0), 2)
    ranked.sort(key=lambda c: -c["score"])
    return ranked[:limit], None


def _as_breakdown_cell(index: PEIndex, candidate: dict[str, Any]) -> dict[str, Any]:
    """Write a breakdown child the way the map does: the node plus ``parameter_key`` (``exemption`` +
    ``SINGLE``, not ``exemption.SINGLE``), so the row compares as the same cell as its siblings' rows."""
    path = candidate["policyengine_parameter"]
    parent, _, key = path.rpartition(".")
    node = index.objects.get(parent)
    if "parameter_key_input" in candidate or node is None or not (getattr(node, "metadata", None) or {}).get("breakdown"):
        return candidate
    return {**candidate, "policyengine_parameter": parent, "parameter_key": key}


def propose(m: Mappings, corpus_root, system, *, modules: str | None = None, as_of: str | None = None,
            limit: int = 3, index: PEIndex | None = None) -> dict[str, Any]:
    as_of = as_of or m.pins.get("verify_as_of")
    corpus = Corpus(corpus_root, m.pins.get("rulespec_us"))
    index = index or PEIndex.build(system)
    # reviewed already: mapped as a parameter, or classified in outputs.yaml (not comparable included)
    mapped = {r["axiom"] for r in m.parameters + m.outputs if r.get("axiom")}
    wanted = [mid for mid in corpus.module_ids if modules is None or mid.startswith(modules)]
    corpus.load(wanted)
    bindings = bound(m, corpus)
    proposals, skipped = [], Counter()
    for module in wanted:
        for name, rule in (corpus.rules(module) or {}).items():
            if rule.get("kind") != "parameter" or f"{module}#{name}" in mapped:
                continue
            found, why = candidates(index, m, module, name, rule, as_of, limit, reviewed=bindings)
            if not found:
                skipped[why] += 1
                continue
            found = [_as_breakdown_cell(index, c) for c in found]
            proposals.append({"axiom": f"{module}#{name}", "program": _program(m, found[0]["policyengine_parameter"]),
                              "source": rule.get("source"), "candidates": found, "accept": None,
                              "rationale": "; ".join(found[0]["evidence"]) + "."})
    return {"generator": "axiom_mappings.propose.parameters", "rulespec_us": corpus.commit, "map_release": m.release,
            "as_of": as_of, "modules": modules, "proposed": len(proposals), "skipped": dict(skipped), "proposals": proposals}


def _reviewed_cells(row: dict[str, Any]) -> set[str] | None:
    """The PolicyEngine names (as ``PEIndex`` spells them) of the cell(s) a reviewed row picked."""
    path = row["policyengine_parameter"]
    if "parameter_key" in row:
        return {f"{path}.{row['parameter_key']}"}
    if "parameter_keys" in row:
        return {f"{path}.{k}" for k in row["parameter_keys"]}
    if "parameter_key_path" in row:
        parts = row["parameter_key_path"]
        if any(isinstance(p, dict) for p in parts):
            return {path}  # a table keyed at run time: the node is the candidate
        head, _, column = path.rpartition(".")
        singular = {"rates": "rate", "thresholds": "threshold", "amounts": "amount"}
        if column in singular and len(parts) == 1:  # <scale>.rates + [i]
            return {f"{head}[{parts[0]}].{singular[column]}"}
        index = next((p for p in parts if isinstance(p, int)), None)
        col = next((singular.get(p, p) for p in parts if isinstance(p, str) and p != "brackets"), None)
        if index is not None and col:
            return {f"{path}[{index}].{col}"}
        return {".".join([path, *map(str, parts)])}
    if "parameter_calc_input" in row or "parameter_calc_value" in row:
        return None
    return {path}


def evaluate(m: Mappings, corpus_root, system, *, index: PEIndex | None = None, as_of: str | None = None) -> dict[str, Any]:
    """Replay the reviewed map: would the proposer have offered the parameter each reviewer chose?"""
    as_of = as_of or m.pins.get("verify_as_of")
    corpus = Corpus(corpus_root, m.pins.get("rulespec_us"))
    index = index or PEIndex.build(system)
    rows = [r for r in m.parameters if r.get("axiom")]
    corpus.load(split_id(r["axiom"])[0] for r in rows)
    bindings = bound(m, corpus)
    outcome, misses = Counter(), []
    for row in rows:
        module, name = split_id(row["axiom"])
        rule = (corpus.rules(module) or {}).get(name)
        if rule is None or rule.get("kind") != "parameter":
            outcome["not a parameter rule at the pin"] += 1
            continue
        chosen = _reviewed_cells(row)
        if chosen is None:
            outcome["reviewed cell chosen at run time"] += 1
            continue
        found, why = candidates(index, m, module, name, rule, as_of, limit=10, exclude=row["axiom"], reviewed=bindings)
        paths = [c["policyengine_parameter"] for c in found]
        if set(paths[:1]) & chosen:
            outcome["first"] += 1
        elif set(paths[:3]) & chosen:
            outcome["top three"] += 1
        elif set(paths) & chosen:
            outcome["top ten"] += 1
        else:
            outcome["missed: " + (why or "not among the candidates")] += 1
            misses.append({"axiom": row["axiom"], "chosen": chosen, "offered": paths[:3], "why": why})
    return {"rows": len(rows), "outcome": dict(outcome), "misses": misses}
