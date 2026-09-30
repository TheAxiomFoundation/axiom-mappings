"""Where consumers bind the same slot differently: the ``cut`` and ``facts`` profiles side by side.

    python -m axiom_mappings.profiles [--program ID] [--json]

A difference is allowed (a request has no PE computed variables, a live simulation does), but it is a
place where two consumers can give different answers for the same household, so it is listed for
review rather than left silent.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from . import Mappings, load
from .bindings import BindingSet, canonical


def _slots(bs: BindingSet) -> dict[str, Any]:
    """``slot`` or ``relation/slot`` -> a comparable description (constants carry their presumption)."""
    out: dict[str, Any] = {}
    for where, block in bs.blocks:
        if where.startswith("binding ") and block.get("status") == "off":
            continue
        prefix = where.removeprefix("relation ") + "/" if where.startswith("relation ") else ""
        for slot, spec in (block.get("inputs") or {}).items():
            out[prefix + slot] = spec
        for presumption, slots in (block.get("presumed") or {}).items():
            for slot, value in (slots or {}).items():
                out[prefix + slot] = {"presumed": presumption, "value": value}
    return out


def differences(m: Mappings, program: str) -> list[dict[str, Any]]:
    sets = {profile: _slots(bs) for (p, profile), bs in m.bindings.items() if p == program}
    if len(sets) < 2:
        return []
    rows = []
    for slot in sorted(set().union(*sets.values())):
        by_profile = {profile: slots.get(slot) for profile, slots in sorted(sets.items())}
        if len({canonical(v) for v in by_profile.values()}) > 1:
            rows.append({"program": program, "slot": slot, **by_profile})
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings.profiles")
    ap.add_argument("--country", default="us")
    ap.add_argument("--program", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    m = load(args.country)
    programs = args.program or sorted({p for p, _ in m.bindings})
    rows = [r for p in programs for r in differences(m, p)]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            sides = "; ".join(f"{k}: {json.dumps(v)}" for k, v in r.items() if k not in ("program", "slot"))
            print(f"{r['program']} {r['slot']}: {sides}")
        print(f"{len(rows)} slot(s) bound differently across profiles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
