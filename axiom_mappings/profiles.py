"""Where consumers bind the same slot differently: the ``cut`` and ``facts`` profiles side by side.

    python -m axiom_mappings profiles [--program ID] [--json]

A program's cut bindings (policyengine-axiom, in a live simulation) are compared with the facts
bindings of the program itself or of its family (axiom-api serves Colorado SNAP through ``us/snap``).
Specs are compared by what they do, not how they are written: a field read as-is, a yearly amount read
per month, an enum mapped to codes, a presumed value. A difference is allowed (a request has no
PolicyEngine computed variables, a simulation does), but it is where two consumers can answer the
same household differently, so it is listed for review rather than left silent.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import Mappings, load
from .bindings import BindingSet, canonical


def describe(spec: Any) -> tuple:
    """What a spec does, in either grammar."""
    if isinstance(spec, dict) and "presumed" in spec:
        return ("presumes", spec["value"])
    if isinstance(spec, str):
        return ("reads", spec, "as-is")
    (kind, arg), *_ = spec.items() if not ("from" in spec) else [("from", spec["from"])]
    if kind == "from":
        if isinstance(arg, str):
            if "map" in spec:
                return ("maps", arg, canonical({str(k): v for k, v in spec["map"].items()}), spec.get("map_default"))
            return ("reads", arg, "per month" if spec.get("period") == "monthly-from-annual" else "as-is")
        return ("other", canonical(spec))
    if kind in ("per_month_of",):
        return ("reads", arg, "per month")
    if kind == "level_of":
        return ("reads", arg, "as-is")
    if kind == "map_of":
        return ("maps", arg["variable"], canonical({str(k): v for k, v in arg["values"].items()}), arg["default"])
    return ("other", canonical(spec))


def _slots(bs: BindingSet) -> dict[str, Any]:
    """Bare slot name -> spec (constants as {"presumed", "value"}); relation slots lose their relation."""
    out: dict[str, Any] = {}
    presumed = {(where, slot): (p, v) for where, slot, p, v in bs.presumed()}
    for where, block in bs.blocks:
        if where.startswith("binding ") and block.get("status") == "off":
            continue
        for slot, spec in (block.get("inputs") or {}).items():
            out[slot] = spec
    for (_, slot), (p, v) in presumed.items():
        out[slot] = {"presumed": p, "value": v}
    return out


def _pairs(m: Mappings, program: str) -> list[tuple[BindingSet, BindingSet]]:
    """(cut, facts) binding-set pairs that serve the same law: the program's own, and its family's."""
    entry = m.programs.get(program) or {}
    family = entry.get("family")
    members = [p for p, e in m.programs.items() if e.get("family") == program]
    cuts = [m.binding_set(p, "cut") for p in [program, *members]]
    facts = [m.binding_set(p, "facts") for p in [program, *([family] if family else [])]]
    return [(c, f) for c in cuts if c for f in facts if f]


def differences(m: Mappings, program: str) -> list[dict[str, Any]]:
    rows = []
    for cut, facts in _pairs(m, program):
        a, b = _slots(cut), _slots(facts)
        for slot in sorted(set(a) & set(b)):
            if describe(a[slot]) != describe(b[slot]):
                rows.append({"slot": slot, "cut_program": cut.program, "facts_program": facts.program,
                             "cut": a[slot], "facts": b[slot]})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings.profiles")
    ap.add_argument("--country", default="us")
    ap.add_argument("--program", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    m = load(args.country)
    programs = args.program or sorted({p for p, profile in m.bindings if profile == "cut"})
    rows = [r for p in programs for r in differences(m, p)]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['slot']}: {r['cut_program']} (cut) {json.dumps(r['cut'])}  vs  {r['facts_program']} (facts) {json.dumps(r['facts'])}")
        print(f"{len(rows)} slot(s) bound differently across profiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
