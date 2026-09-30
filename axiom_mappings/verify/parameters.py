"""Every mapped parameter compared on both sides over its whole dated history.

    python -m axiom_mappings verify parameters --corpus <rulespec-us> [--program P] [--as-of DATE] [--json] [--out F]

Identity comes from the mapping row (the Axiom id, the PolicyEngine path and cell); values are only the
check. For each PolicyEngine cell, Axiom's history is assembled from every Axiom id mapped to it (one
module per fiscal year is common): each version holds from ``effective_from`` to its ``effective_to``,
or to the next version. PolicyEngine's history is the parameter's ``values_list``. The two step
functions are compared over every interval either side changes:

    match            the same value (to the cent for money)
    disagree         different values that took effect on the same date
    stale-axiom      different, and PolicyEngine's value is newer: Axiom has not encoded a change
    stale-pe         different, and Axiom's value is newer: PolicyEngine has not
    uncovered-axiom  PolicyEngine has a value and Axiom none (before Axiom's first version, or after one ends)
    uncovered-pe     Axiom has a value and PolicyEngine none

An interval whose value on either side took effect after ``--as-of`` (default: ``verify_as_of`` in
pins.yaml, so a run is reproducible; bump it deliberately) is ``projected``: PolicyEngine
uprates its parameters decades ahead, and a projection is not law yet. Nothing is matched or
classified by value: a difference is reported, and investigated under D48 (``findings.yaml``).
What cannot be checked without running a program is listed with the reason, never guessed: derived
Axiom rules, formulas that are not arithmetic on literals, and cells chosen by a run-time input.
"""

from __future__ import annotations

import ast
import datetime
import math
import operator
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .. import Mappings
from ..corpus import Corpus, split_id

SCALE_COLUMNS = {"rates": "rate", "thresholds": "threshold", "amounts": "amount"}
SEVERITY = ["disagree", "stale-axiom", "stale-pe", "uncovered-axiom", "uncovered-pe", "match", "no-overlap"]
MONEY_TOLERANCE = 0.005
UNITS = {"Money": "money", "USD": "money", "currency-USD": "money", "Rate": "rate", "/1": "rate"}

_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.USub: operator.neg, ast.UAdd: operator.pos}


def literal(formula: Any) -> float | None:
    """The number a formula states, if it is arithmetic on literals (``2200``, ``1 / 2``, ``0.062``)."""
    if isinstance(formula, bool):
        return float(formula)
    if isinstance(formula, (int, float)):
        return float(formula)
    text = str(formula).strip().replace("_", "").replace(",", "")
    if text.lower() in ("true", "false"):
        return float(text.lower() == "true")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError:
        return None

    def ev(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return float(node.value)
        if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
            return _OPS[type(node.op)](ev(node.operand))
        raise ValueError

    try:
        return ev(tree.body)
    except (ValueError, ZeroDivisionError):
        return None


@dataclass
class Version:
    start: str
    end: str | None  # exclusive; None is open-ended
    value: float
    source: str  # the Axiom id


@dataclass
class Target:
    """One PolicyEngine cell and every Axiom id mapped to it."""
    path: str
    selector: tuple  # key path below ``path``, or ("calc", x)
    program: str
    axiom_ids: list[str] = field(default_factory=list)
    versions: list[Version] = field(default_factory=list)
    comparison: str | None = None

    @property
    def name(self) -> str:
        if not self.selector:
            return self.path
        if self.selector[0] == "calc":
            return f"{self.path}.calc({self.selector[1]})"
        return self.path + "".join(f"[{s}]" for s in self.selector)


def _axiom_versions(rule: dict[str, Any], axiom_id: str, table_key: Any = None) -> list[Version] | str:
    out = []
    for v in rule.get("versions") or []:
        start = str(v.get("effective_from"))
        end = str(v["effective_to"]) if v.get("effective_to") else None
        if table_key is not None:
            values = v.get("values") or {}
            raw = values.get(table_key, values.get(str(table_key)))
            if raw is None:
                continue  # this version's table has no such row: uncovered, not a value
            value = literal(raw)
        elif "values" in v and v.get("formula") is None:
            return "Mapping: Axiom has a table, but the mapping picks no row"
        else:
            value = literal(v.get("formula"))
        if value is None:
            return f"Axiom: the formula is not arithmetic on literals: {str(v.get('formula'))[:60]!r}"
        out.append(Version(start, end, value, axiom_id))
    return out


def _cells(row: dict[str, Any], rule: dict[str, Any]) -> list[tuple[tuple, Any]] | str:
    """(PolicyEngine selector, Axiom table key or None) for every cell a mapping row compares."""
    table = any("values" in (v or {}) for v in rule.get("versions") or [])
    rows = sorted({k for v in rule.get("versions") or [] for k in (v.get("values") or {})}, key=str) if table else []
    key_map = {str(k): v for k, v in (row.get("parameter_key_map") or {}).items()}
    if "parameter_calc_input" in row:
        return "Mapping: the scale is evaluated at a run-time input"
    if "parameter_calc_value" in row:
        return [(("calc", row["parameter_calc_value"]), None)]
    if "parameter_key" in row:
        return [((row["parameter_key"],), None)]
    if "parameter_keys" in row:
        return [((k,), None) for k in row["parameter_keys"]]
    if "parameter_key_path" in row:
        path = row["parameter_key_path"]
        dynamic = [p for p in path if isinstance(p, dict)]
        if not dynamic:
            return [(tuple(path), None)]
        if not table or len(dynamic) > 1:
            return "Mapping: the cell is chosen by a run-time input"
        part = dynamic[0]
        part_map = {str(k): v for k, v in (part.get("parameter_key_map") or part.get("key_map") or {}).items()}
        return [(tuple(part_map.get(str(k), str(k)) if isinstance(p, dict) else p for p in path), k) for k in rows]
    if "parameter_key_input" in row:
        if not table:
            return "Mapping: the cell is chosen by a run-time input"
        return [((key_map.get(str(k), str(k)),), k) for k in rows]
    if table:
        return "Mapping: Axiom has a table, but the mapping picks no cell"
    return [((), None)]


def _node(system, path: str):
    from policyengine_core.parameters import get_parameter

    try:
        return get_parameter(system.parameters, path), None
    except Exception:
        head, _, column = path.rpartition(".")
        if column in SCALE_COLUMNS:  # <scale>.rates|thresholds|amounts, then a bracket index
            return get_parameter(system.parameters, head), column
        raise


def _pe_series(system, path: str, selector: tuple) -> list[tuple[str, float | None]] | str:
    """PolicyEngine's dated values for one cell, oldest first."""
    try:
        node, column = _node(system, path)
    except Exception:
        return f"PolicyEngine: no parameter {path}"
    if selector and selector[0] == "calc":
        if type(node).__name__ != "ParameterScale":
            return f"PolicyEngine: {path} is not a scale; calc needs one"
        instants = sorted({p.instant_str for b in node.brackets for c in b.children.values() for p in c.values_list})
        out = []
        for instant in instants:
            try:
                out.append((instant, float(node.get_at_instant(instant).calc(selector[1]))))
            except Exception:
                out.append((instant, None))
        return out
    rest, index = list(selector), []
    while rest:
        part = rest.pop(0)
        kind = type(node).__name__
        if kind == "ParameterScale":
            if part in SCALE_COLUMNS:  # [rates|thresholds|amounts, i]
                column = part
                continue
            if part == "brackets":  # [brackets, i, amount|threshold|rate]
                continue
            if not isinstance(part, int) or not 0 <= part < len(node.brackets):
                return f"PolicyEngine: {path} has no bracket {part!r} ({len(node.brackets)} brackets)"
            node = node.brackets[part]
            if column is not None:
                rest.insert(0, SCALE_COLUMNS[column])
            continue
        if kind == "Parameter":  # the rest indexes into a list-valued parameter
            index = [part] + rest
            break
        children = getattr(node, "children", None) or {}
        if str(part) not in children:
            return f"PolicyEngine: {path} has no key {part!r} at {kind} (has {sorted(children)[:8]})"
        node = children[str(part)]
    if type(node).__name__ != "Parameter":
        return f"PolicyEngine: {path}{list(selector)} is a {type(node).__name__}, not one value; the mapping must pick a cell"
    series = []
    for p in sorted(node.values_list, key=lambda p: p.instant_str):
        v = p.value
        try:
            for i in index:
                v = v[i]
        except (IndexError, KeyError, TypeError):
            v = None  # a list value without this position at this date: no value then
        series.append((p.instant_str, None if v is None else float(v)))
    if index and all(v is None for _, v in series):
        return f"PolicyEngine: {path} has no value at index {index} on any date"
    return series


def _unit(system, row: dict[str, Any], rule: dict[str, Any]) -> str | None:
    """A unit mismatch between the two sides, if any (money vs rate); None when they agree or are unknown."""
    try:
        node, _ = _node(system, row["policyengine_parameter"])
    except Exception:
        return None
    pe = UNITS.get(str((getattr(node, "metadata", None) or {}).get("unit")))
    axiom = UNITS.get(str(rule.get("unit"))) or UNITS.get(str(rule.get("dtype")))
    if pe and axiom and pe != axiom:
        return f"Axiom {axiom}, PolicyEngine {pe}"
    return None


def _runs(series: list[tuple[str, float | None, str]]) -> list[tuple[str, float | None, str, str]]:
    """(date, value, since, source): ``since`` is when the value in effect first took that value."""
    out, since, last = [], None, object()
    for date, value, source in series:
        if value != last:
            since, last = date, value
        out.append((date, value, since, source))
    return out


def _axiom_timeline(versions: list[Version]) -> list[tuple[str, float | None, str]]:
    """Axiom's step function as (date, value or None, source), merging every id's versions."""
    points = sorted({v.start for v in versions} | {v.end for v in versions if v.end})
    out = []
    for date in points:
        active = [v for v in versions if v.start <= date and (v.end is None or date < v.end)]
        latest = max(active, key=lambda v: v.start, default=None)  # the newest version in force
        out.append((date, latest.value if latest else None, latest.source if latest else ""))
    return out


def _at(timeline: list[tuple[str, float | None, str, str]], date: str):
    current = (None, None, None)
    for d, value, since, source in timeline:
        if d > date:
            break
        current = (value, since, source)
    return current


def _same(a: float, b: float, money: bool) -> bool:
    return math.isclose(a, b, rel_tol=1e-9, abs_tol=MONEY_TOLERANCE if money else 1e-9)


def _hint(a: float | None, p: float | None, money: bool) -> str | None:
    """A pattern in a difference that points at a cause, for the investigator (never a verdict)."""
    if a is None or p is None or a == p:
        return None
    for ratio, text in ((12, "ratio 12 (Axiom larger): annual vs monthly?"), (1 / 12, "ratio 12 (PolicyEngine larger): annual vs monthly?"),
                        (100, "ratio 100 (Axiom larger): percent vs fraction?"), (0.01, "ratio 100 (PolicyEngine larger): percent vs fraction?")):
        if p and math.isclose(a / p, ratio, rel_tol=1e-6):
            return text
    if not money and abs(a - p) < 1e-4:
        return "differs below 0.0001: a rounded statement of the same rate?"
    if money and abs(a - p) < 1:
        return "differs by under a dollar: rounding?"
    return None


def compare(target: Target, pe_series: list[tuple[str, float | None]], as_of: str) -> dict[str, Any]:
    axiom = _runs(_axiom_timeline(target.versions))
    pe = _runs([(d, v, "") for d, v in pe_series])
    money = target.comparison == "money"
    conflicts = sorted({f"{a.start}: {a.source} vs {b.source}" for a in target.versions for b in target.versions
                        if a is not b and a.start == b.start and not _same(a.value, b.value, money)})
    points = sorted({d for d, *_ in axiom} | {d for d, *_ in pe})
    segments = []
    for i, date in enumerate(points):
        a, a_since, source = _at(axiom, date)
        p, p_since, _ = _at(pe, date)
        if a is None and p is None:
            continue
        if a is None:
            verdict = "uncovered-axiom"
        elif p is None:
            verdict = "uncovered-pe"
        elif _same(a, p, money):
            verdict = "match"
        elif p_since > a_since:
            verdict = "stale-axiom"
        elif a_since > p_since:
            verdict = "stale-pe"
        else:
            verdict = "disagree"
        projected = date > as_of or max(s for s in (a_since, p_since) if s) > as_of
        seg = {"from": date, "to": points[i + 1] if i + 1 < len(points) else None, "verdict": verdict,
               "axiom": a, "axiom_since": a_since, "axiom_id": source or None, "pe": p, "pe_since": p_since,
               "projected": projected}
        prev = segments[-1] if segments else None
        if prev and all(prev[k] == seg[k] for k in ("verdict", "axiom", "pe", "projected", "axiom_id")):
            prev["to"] = seg["to"]
        else:
            segments.append(seg)
    for seg in segments:
        seg["hint"] = _hint(seg["axiom"], seg["pe"], money) if seg["verdict"] not in ("match",) else None
    axiom_first = next((s["from"] for s in segments if s["axiom"] is not None), None)
    pe_first = next((s["from"] for s in segments if s["pe"] is not None), None)

    def counts(seg) -> bool:
        """Whether a segment speaks to the present: not a projection, and not history before a side began."""
        if seg["projected"]:
            return False
        if seg["verdict"] == "uncovered-axiom":
            return axiom_first is not None and seg["from"] > axiom_first  # a gap after Axiom began, not old history
        if seg["verdict"] == "uncovered-pe":
            return pe_first is not None and seg["from"] > pe_first
        return True

    current = [s for s in segments if counts(s)]
    worst = min((s["verdict"] for s in current), key=SEVERITY.index, default="no-overlap")
    covered = [s for s in segments if s["axiom"] is not None]
    return {
        "target": target.name, "program": target.program, "axiom_ids": target.axiom_ids,
        "verdict": worst, "conflicts": conflicts,
        "axiom_covers": [covered[0]["from"], covered[-1]["to"]] if covered else None,
        "current": dict(Counter(s["verdict"] for s in current)),
        "history_not_encoded": sum(1 for s in segments if not s["projected"] and not counts(s)),
        "projected": dict(Counter(s["verdict"] for s in segments if s["projected"])),
        "segments": segments,
    }


def verify(m: Mappings, corpus_root, system, *, as_of: str | None = None, program: str | None = None,
           ref: str | None = None) -> dict[str, Any]:
    as_of = as_of or m.pins.get("verify_as_of") or datetime.date.today().isoformat()
    corpus = Corpus(corpus_root, ref or m.pins.get("rulespec_us"))
    rows = [r for r in m.parameters if r.get("axiom") and (program is None or r.get("program") == program)]
    resolved = corpus.resolve_all([r["axiom"] for r in rows], suggest=False)
    targets: dict[tuple, Target] = {}
    unchecked: list[dict[str, Any]] = []
    units: list[dict[str, Any]] = []

    def skip(row, reason):
        unchecked.append({"axiom": row["axiom"], "policyengine_parameter": row["policyengine_parameter"], "reason": reason})

    for row in rows:
        res = resolved[row["axiom"]]
        if not res.resolved:
            skip(row, f"Axiom: id {res.status} at the pin (unresolved.yaml)")
            continue
        if res.status == "deferred":
            skip(row, "Axiom: a deferred output, not encoded yet")
            continue
        module, name = split_id(row["axiom"])
        rule = corpus.rules(module)[name]
        if rule.get("kind") != "parameter":
            skip(row, f"Axiom: a {rule.get('kind')} rule, whose value needs the program run (companion cases)")
            continue
        cells = _cells(row, rule)
        if isinstance(cells, str):
            skip(row, cells)
            continue
        mismatch = _unit(system, row, rule)
        if mismatch:
            units.append({"axiom": row["axiom"], "policyengine_parameter": row["policyengine_parameter"], "mismatch": mismatch})
        for selector, table_key in cells:
            versions = _axiom_versions(rule, row["axiom"], table_key)
            if isinstance(versions, str):
                skip(row, versions)
                break
            key = (row["policyengine_parameter"], tuple(selector))
            t = targets.setdefault(key, Target(row["policyengine_parameter"], tuple(selector), row.get("program", "")))
            t.comparison = t.comparison or row.get("comparison")
            if row["axiom"] not in t.axiom_ids:
                t.axiom_ids.append(row["axiom"])
            t.versions += versions
    results = []
    for t in targets.values():
        series = _pe_series(system, t.path, t.selector)
        if isinstance(series, str):
            for axiom_id in t.axiom_ids:
                unchecked.append({"axiom": axiom_id, "policyengine_parameter": t.name, "reason": series,
                                  "cause_candidate": "mapping-wrong"})
            continue
        results.append(compare(t, series, as_of))
    results.sort(key=lambda r: (SEVERITY.index(r["verdict"]), r["target"]))
    return {
        "ref": corpus.commit, "as_of": as_of, "map_release": m.release, "program": program,
        "rows": len(rows), "targets": len(results),
        "verdicts": dict(Counter(r["verdict"] for r in results)),
        "unchecked": unchecked, "unchecked_reasons": dict(Counter(u["reason"].split(":")[0] for u in unchecked)),
        "unit_mismatches": units, "results": results,
    }


def differences(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Targets whose current (not projected) history disagrees or is stale: each to be investigated (D48)."""
    return [r for r in report["results"] if r["verdict"] in ("disagree", "stale-axiom", "stale-pe") or r["conflicts"]]


def gaps(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Targets where one side stops before the present while the other goes on (incompleteness)."""
    return [r for r in report["results"] if r["verdict"] in ("uncovered-axiom", "uncovered-pe")]


def proposed_findings(report: dict[str, Any]) -> list[dict[str, Any]]:
    """One unclassified finding per current difference, with where it starts and what each side says."""
    out = []
    for r in differences(report):
        first = next((s for s in r["segments"] if not s["projected"] and s["verdict"] == r["verdict"]), r["segments"][0])
        out.append({
            "program": r["program"], "variable": r["target"], "counterpart": "policyengine", "cause": "unclassified",
            "kind": "parameter",
            "note": f"{first['verdict']} from {first['from']}: Axiom {first['axiom']} (since {first['axiom_since']}, "
                    f"{first['axiom_id']}), PolicyEngine {first['pe']} (since {first['pe_since']})."
                    + (f" Hint: {first['hint']}" if first.get("hint") else ""),
            "evidence": {"report": f"verify parameters at {report['ref'][:9]} as of {report['as_of']}",
                         "verdicts": r["current"], "axiom_ids": r["axiom_ids"]},
        })
    return out


def gate(m: Mappings, report: dict[str, Any]) -> list:
    """Every current difference is filed as a finding (unclassified until investigated, D48), and a
    filed parameter finding whose difference is gone is removed. The same ratchet as unresolved ids."""
    from ..validate import Finding

    filed = {(f["program"], f["variable"]) for f in m.findings if f.get("kind") == "parameter"}
    now = {(r["program"], r["target"]): r for r in differences(report)}
    checked = {(r["program"], r["target"]) for r in report["results"]}
    out = []
    for key, r in now.items():
        if key not in filed:
            out.append(Finding("error", "unfiled-parameter-difference", r["target"],
                               f"{r['verdict']} ({r['program']}); file it: verify parameters --file-findings"))
    for key in filed:
        if key in checked and key not in now:
            out.append(Finding("error", "resolved-parameter-finding", key[1], "no longer differs: remove the finding"))
    return out


def file_findings(m: Mappings, report: dict[str, Any], root=None) -> list[dict[str, Any]]:
    """Append every unfiled current difference to findings.yaml as unclassified; returns what was added."""
    import yaml

    from .. import ROOT

    filed = {(f["program"], f["variable"]) for f in m.findings}
    new = [f for f in proposed_findings(report) if (f["program"], f["variable"]) not in filed]
    if new:
        path = (root or ROOT) / "data" / m.country / "findings.yaml"
        text = path.read_text().rstrip("\n") + "\n"
        body = yaml.safe_dump(new, sort_keys=False, width=110, allow_unicode=True)
        path.write_text(text + body)  # items at the top level of the `findings:` list
    return new
