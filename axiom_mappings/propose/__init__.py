"""Proposals: candidate mappings for review, never part of a release.

A proposer reads RuleSpec (at the pin) and PolicyEngine and writes ``proposals/<kind>/<name>.yaml`` at the
repository root, outside the packaged map, so nothing proposed can ship before a reviewer accepts it
(``python -m axiom_mappings accept``). Identity comes first: a candidate needs a shared legal citation
(parameters, outputs) or a reviewed binding or slot rule (slots). Values and names only rank
candidates that identity already admitted, and each candidate carries the evidence for it.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import Any

import yaml

from .. import ROOT

PROPOSALS = ROOT.parent / "proposals"
_STOP = {"of", "the", "and", "or", "for", "a", "an", "to", "in", "on", "by", "under", "subsection", "section", "gov",
         "amount", "value", "rate", "usc", "cfr"}


def _stem(t: str) -> str:
    return t[:-1] if len(t) > 3 and t.endswith("s") and not t.endswith("ss") else t  # deductions -> deduction


def tokens(name: str) -> set[str]:
    return {_stem(t) for t in re.split(r"[^a-z0-9]+", name.lower()) if len(t) > 1 and t not in _STOP and not t.isdigit()}


def similarity(a: str, b: str) -> float:
    """How far two names name the same thing, a ranking signal only: token Jaccard, averaged with
    containment when the shorter name has two or more tokens (``ctc_maximum`` inside
    ``ctc_maximum_before_phase_out``)."""
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    jaccard = len(ta & tb) / len(ta | tb)
    shorter = min(len(ta), len(tb))
    return jaccard if shorter < 2 else (jaccard + len(ta & tb) / shorter) / 2


def bound(m, corpus) -> dict[str, dict[str, list[tuple[str, str]]]]:
    """What reviewed bindings already pair, by kind and Axiom rule name: {"output"|"parameter": {name:
    [(PolicyEngine name, program), ...]}}, each with the modules its program reads (``closures``)."""
    out: dict[str, dict] = {"output": {}, "parameter": {}, "closures": {}}
    for (program, _profile), bs in m.bindings.items():
        entry = m.programs.get(program) or {}
        if not entry or entry.get("assembled_by"):
            continue
        out["closures"][program] = set(corpus.closure(entry["axiom"]))
        for b in bs.doc.get("bindings") or []:
            out["output"].setdefault(b["axiom_output"], []).append((b["pe_variable"], program))
            for param, path in (b.get("parameters") or {}).items():
                out["parameter"].setdefault(param, []).append((path, program))
    return out


def bound_to(reviewed: dict, kind: str, module: str, name: str) -> list[tuple[str, str]]:
    """The PolicyEngine names reviewed bindings pair with this rule (its program must read its module)."""
    return [(pe, program) for pe, program in reviewed[kind].get(name, []) if module in reviewed["closures"].get(program, ())]


class Dumper(yaml.SafeDumper):
    pass


def write(kind: str, name: str, meta: dict[str, Any], proposals: list[dict[str, Any]], root: Path | None = None) -> Path:
    path = (root or PROPOSALS) / kind / f"{name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    header = (f"# {kind} proposals: candidates for review, not part of the map. To accept one, set `accept:` to the\n"
              "# candidate's index (and fix `program` / `rationale` if needed), then run\n"
              f"# python -m axiom_mappings accept {path.relative_to(path.parents[2]) if len(path.parents) > 2 else path}\n")
    doc = {**meta, "generated": datetime.date.today().isoformat(), "proposals": proposals}
    path.write_text(header + yaml.dump(doc, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=110))
    return path
