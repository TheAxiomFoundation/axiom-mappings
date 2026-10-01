"""Is a program ready to serve Axiom as the truth (D48)?

    python -m axiom_mappings.readiness --program us-co/snap-fy2026 --artifact compiled.json [--json]
    python -m axiom_mappings.readiness --all [--catalog slots.json ...] [--json]

A program is ready when every input slot of its compiled Axiom program is either mapped to a
concept or derived from concepts, or is a constant under an acceptable, declared presumption, and
when no harness supplies any of its parameters. Without ``--artifact`` only the parameter side can
be checked, so the program is never reported ready.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import Mappings, load
from .validate import slot_names

COUNTERPART_MARKERS = ("PolicyEngine", "TAXSIM")


def _slot_kind(m: Mappings, names: list[str]) -> tuple[str, str | None]:
    rule = next((r for r in m.inputs if any(r in m.rules_for_slot(n) for n in names)), None)
    if rule is None:
        return "missing", None
    src = rule["source"]
    if src["kind"] == "concept":
        return "mapped", rule["id"]
    if src["kind"] == "derived":
        return m.harness_kind(rule) or "derived", rule["id"]
    presumption = src.get("presumption")
    if presumption is None:
        return "undeclared_constant", rule["id"]
    if m.presumptions.get(presumption, {}).get("acceptable") is False:
        return "fixture_default", rule["id"]
    return "presumed", rule["id"]


def _binding_inputs(m: Mappings, bs, catalog: dict[str, Any] | None) -> tuple[dict[str, Any], list[str]]:
    """Slot counts for one binding set, and what in them blocks serving."""
    from .bindings import _catalog

    counts: dict[str, Any] = {"mapped": 0, "presumed": 0, "unacceptable": {}}
    for _, block in bs.blocks:
        counts["mapped"] += len(block.get("inputs") or {})
    for _, slot, presumption, _ in bs.presumed():
        if m.presumptions.get(presumption, {}).get("acceptable") is False:
            counts["unacceptable"][presumption] = counts["unacceptable"].get(presumption, 0) + 1
        else:
            counts["presumed"] += 1
    blockers = [f"{bs.profile}: {n} slot(s) under the unacceptable presumption {p}" for p, n in sorted(counts["unacceptable"].items())]
    if catalog is None:
        counts["coverage"] = "unverified"
        blockers.append(f"{bs.profile}: slot coverage unverified (give the program's slot catalog)")
    else:
        gaps = _catalog(bs, catalog)
        counts["coverage"] = "complete" if not gaps else f"{len(gaps)} gap(s)"
        if gaps:
            blockers.append(f"{bs.profile}: {len(gaps)} slot(s) unbound or stray")
    counts["slots"] = counts["mapped"] + counts["presumed"] + sum(counts["unacceptable"].values())
    return counts, blockers


def readiness(m: Mappings, program_id: str, compiled: dict[str, Any] | None = None,
              catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """Whether ``program_id`` may serve Axiom's answer (D48): its inputs are all bound or acceptably
    presumed, no harness supplies its law, and no filed disagreement has a blocking cause.

    With binding sets the inputs are read from them (``catalog`` verifies coverage); without, from the
    global slot rules against a ``compiled`` artifact.
    """
    if program_id not in m.programs:
        raise KeyError(f"{program_id} is not in programs.yaml")
    supplied = m.supplied_for(program_id)
    report: dict[str, Any] = {
        "program": program_id,
        "map_release": m.release,
        "supplied_parameters": len(supplied),
        "supplied_matching_a_counterpart": sum(any(k in str(s.get("source")) for k in COUNTERPART_MARKERS) for s in supplied),
        "inputs": None,
        "bindings": {},
    }
    blockers = []
    if supplied:
        blockers.append(f"{len(supplied)} parameters supplied by a harness, not encoded")
    sets = {profile: bs for (program, profile), bs in m.bindings.items() if program == program_id}
    for profile, bs in sorted(sets.items()):
        report["bindings"][profile], found = _binding_inputs(m, bs, catalog)
        blockers += found
    if not sets and compiled is None:
        blockers.append("no bindings and no compiled program given: input coverage unknown")
    elif not sets:
        counts = {k: 0 for k in ("mapped", "derived", "presumed", "undeclared_constant", "fixture_default", "harness_law",
                                 "harness_assumption", "missing")}
        examples: dict[str, list[str]] = {}
        for slot, names in sorted(slot_names(compiled).items()):
            kind, _ = _slot_kind(m, names)
            counts[kind] += 1
            examples.setdefault(kind, []).append(slot)
        report["inputs"] = {"slots": sum(counts.values()), **counts,
                            "examples": {k: v[:5] for k, v in examples.items() if k in ("missing", "undeclared_constant",
                                                                                        "fixture_default", "harness_law",
                                                                                        "harness_assumption")}}
        if not counts or sum(counts.values()) == 0:
            blockers.append("the compiled program lists no input slots (metadata.input_catalog): coverage unknown")
        for kind, label in (("missing", "input slots no rule covers"), ("undeclared_constant", "constants with no presumption"),
                            ("fixture_default", "test-fixture defaults"),
                            ("harness_law", "slots computed by law outside RuleSpec (transforms.yaml)"),
                            ("harness_assumption", "slots computed by an undeclared assumption (transforms.yaml)")):
            if counts[kind]:
                blockers.append(f"{counts[kind]} {label}")
    disagreements = m.findings_for(program_id)
    blocking = [f for f in disagreements if f["cause"] in m.blocking_causes]
    report["findings"] = {"filed": len(disagreements), "blocking": [f"{f['variable']}: {f['cause']}" for f in blocking]}
    if blocking:
        causes = sorted({f["cause"] for f in blocking})
        blockers.append(f"{len(blocking)} filed disagreement(s) not resolved: {', '.join(causes)} (D48)")
    report["ready"] = not blockers
    report["blockers"] = blockers
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings.readiness")
    ap.add_argument("--country", default="us")
    ap.add_argument("--program", action="append", default=[])
    ap.add_argument("--artifact", action="append", default=[], help="compiled program JSON, paired with --program in order")
    ap.add_argument("--catalog", action="append", default=[], help="a program's slot catalog JSON (repeatable)")
    ap.add_argument("--all", action="store_true", help="every program in programs.yaml")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    m = load(args.country)
    programs = list(m.programs) if args.all else args.program
    artifacts = [json.loads(Path(a).read_text()) for a in args.artifact]
    catalogs = {doc["program"]: doc for doc in (json.loads(Path(c).read_text()) for c in args.catalog)}
    reports = [readiness(m, p, artifacts[i] if i < len(artifacts) else None, catalogs.get(p)) for i, p in enumerate(programs)]
    if args.json:
        print(json.dumps(reports, indent=2))
    else:
        print(f"map release {m.release}")
        for r in reports:
            status = "READY" if r["ready"] else "not ready"
            inputs = r["inputs"]
            for profile, b in r["bindings"].items():
                unacceptable = sum(b["unacceptable"].values())
                print(f"{r['program']:42} {profile}: {b['slots']} slots, {b['mapped']} mapped, {b['presumed']} presumed, "
                      f"{unacceptable} unacceptable, coverage {b['coverage']}")
            detail = "" if inputs is None else (
                f" | {inputs['slots']} slots: {inputs['mapped']} mapped, {inputs['derived']} derived, {inputs['presumed']} presumed, "
                f"{inputs['undeclared_constant']} undeclared constants, {inputs['fixture_default']} fixture, {inputs['missing']} missing")
            print(f"{r['program']:42} {status:9} supplied params {r['supplied_parameters']}{detail}")
            for b in r["blockers"]:
                print(f"    - {b}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
