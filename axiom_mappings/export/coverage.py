"""The coverage map: where every PolicyEngine variable the map knows stands.

`readiness` answers "may this program serve", one row per program. This answers
"where is this variable", one row per PolicyEngine variable, which is what a
dashboard joins against partner demand. Demand lives in axiom-api, not here, so
this emits only the supply side and leaves the join to the consumer.

A variable moves through these stages, in order, and `stage` is the furthest it
has reached:

    unmapped    no output row names a PolicyEngine variable
    mapped      an output row names one, so a rule exists to bind
    bound       a binding set pairs them, whatever its status
    shadow      the binding runs beside PolicyEngine and the difference is recorded
    on          the binding answers, under population evidence

`blocked_by` is why it has not gone further: an unresolved Axiom id, a finding
with a blocking cause, an unacceptable presumption, or a program not ready.
A variable can be `on` and still carry a non-blocking finding, so read `stage`
and `blocked_by` together, never one alone.
"""

from __future__ import annotations

from typing import Any

from .. import Mappings
from ..readiness import readiness

STAGES = ("unmapped", "mapped", "bound", "shadow", "on")


def _binding_rows(m: Mappings) -> dict[str, list[dict[str, Any]]]:
    """PolicyEngine variable -> the bindings that name it, across programs and profiles."""
    rows: dict[str, list[dict[str, Any]]] = {}
    for (program, profile), bs in sorted(m.bindings.items()):
        for block in bs.doc.get("bindings") or []:
            variable = block.get("pe_variable")
            if not variable:
                continue
            rows.setdefault(variable, []).append(
                {
                    "program": program,
                    "profile": profile,
                    "axiom_output": block.get("axiom_output"),
                    "status": block.get("status", "off"),
                    "note": (block.get("notes") or {}).get("note"),
                }
            )
    return rows


def _output_rows(m: Mappings) -> dict[str, list[dict[str, Any]]]:
    """PolicyEngine variable -> the output mappings that name it.

    Only `policyengine_variable` rows are a variable's supply. A row carrying
    `policyengine_parameter` maps a parameter, which `verify parameters`
    covers, and one carrying neither is a rule with no counterpart yet.
    """
    rows: dict[str, list[dict[str, Any]]] = {}
    for row in m.outputs:
        variable = row.get("policyengine_variable")
        if not variable:
            continue
        rows.setdefault(variable, []).append(row)
    return rows


def _stage(mapped: bool, bindings: list[dict[str, Any]]) -> str:
    if not mapped and not bindings:
        return "unmapped"
    statuses = {b["status"] for b in bindings}
    if "on" in statuses:
        return "on"
    if "shadow" in statuses:
        return "shadow"
    if bindings:
        return "bound"
    return "mapped"


def coverage(m: Mappings, *, readiness_by_program: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The whole supply-side coverage map, variables and a per-program rollup."""
    bindings = _binding_rows(m)
    outputs = _output_rows(m)
    ready = readiness_by_program or {}

    variables: list[dict[str, Any]] = []
    for variable in sorted(set(bindings) | set(outputs)):
        bound = bindings.get(variable, [])
        mapped = outputs.get(variable, [])
        # An id the pinned corpus no longer resolves cannot be bound, however
        # the map names it; `identity` keeps that list as a ratchet.
        unresolved = sorted(
            {row["axiom"] for row in mapped if row.get("axiom") in m.unresolved}
        )
        findings = [
            f
            for b in bound
            for f in m.findings
            if f.get("program") == b["program"] and f.get("variable") == variable
        ]
        blocking = [f for f in findings if f.get("cause") in m.blocking_causes]
        programs = sorted({b["program"] for b in bound})
        not_ready = [p for p in programs if ready.get(p, {}).get("ready") is False]

        blocked_by: list[str] = []
        if unresolved:
            blocked_by.append(f"Axiom id does not resolve at the pin: {', '.join(unresolved)}")
        for f in blocking:
            blocked_by.append(f"finding {f['cause']}" + (f" ({f['issue']})" if f.get("issue") else ""))
        for p in not_ready:
            blocked_by.append(f"program {p} is not ready")

        variables.append(
            {
                "policyengine": variable,
                "stage": _stage(bool(mapped), bound),
                "axiom": sorted({row["axiom"] for row in mapped if row.get("axiom")}),
                "programs": programs,
                "bindings": bound,
                "findings": [
                    {"cause": f.get("cause"), "issue": f.get("issue"), "note": f.get("note")} for f in findings
                ],
                "blocked_by": blocked_by,
            }
        )

    by_stage = {stage: sum(1 for v in variables if v["stage"] == stage) for stage in STAGES}
    programs_rollup = []
    for program in sorted(m.programs):
        rows = [v for v in variables if program in v["programs"]]
        programs_rollup.append(
            {
                "program": program,
                "variables": len(rows),
                "by_stage": {s: sum(1 for v in rows if v["stage"] == s) for s in STAGES},
                "blocked": sum(1 for v in rows if v["blocked_by"]),
                "ready": ready.get(program, {}).get("ready"),
            }
        )

    return {
        "schema": "axiom_mappings.coverage.v1",
        "map_release": m.release,
        "country": m.country,
        "unresolved_ref": m.unresolved_ref,
        "totals": {"variables": len(variables), "by_stage": by_stage,
                   "blocked": sum(1 for v in variables if v["blocked_by"])},
        "programs": programs_rollup,
        "variables": variables,
    }


def main(argv=None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    from .. import load
    from ..readiness import readiness as program_readiness

    ap = argparse.ArgumentParser(prog="axiom_mappings export coverage")
    ap.add_argument("--country", default="us")
    ap.add_argument("--out", type=Path, help="write JSON here; otherwise print a summary")
    ap.add_argument("--catalog", action="append", default=[], help="a program's slot catalog JSON (repeatable)")
    ap.add_argument("--stage", choices=STAGES, help="list the variables at one stage")
    ap.add_argument("--blocked", action="store_true", help="list only variables something blocks")
    args = ap.parse_args(argv)

    m = load(args.country)
    catalogs = {doc["program"]: doc for doc in (json.loads(Path(c).read_text()) for c in args.catalog)}
    ready = {}
    for program in m.programs:
        try:
            ready[program] = program_readiness(m, program, None, catalogs.get(program))
        except Exception:  # noqa: BLE001 - a program readiness cannot judge is simply unknown
            ready[program] = {}
    report = coverage(m, readiness_by_program=ready)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
        print(args.out)
        return 0

    rows = report["variables"]
    if args.stage:
        rows = [v for v in rows if v["stage"] == args.stage]
    if args.blocked:
        rows = [v for v in rows if v["blocked_by"]]
    totals = report["totals"]
    print(f"map release {report['map_release']}  ({totals['variables']} variables, {totals['blocked']} blocked)")
    print("  " + "  ".join(f"{stage}: {totals['by_stage'][stage]}" for stage in STAGES))
    if args.stage or args.blocked:
        for v in rows:
            why = f"  <- {'; '.join(v['blocked_by'])}" if v["blocked_by"] else ""
            print(f"  {v['stage']:8} {v['policyengine']:48} {', '.join(v['programs']) or '-'}{why}")
    else:
        for p in report["programs"]:
            stages = " ".join(f"{s}={p['by_stage'][s]}" for s in STAGES if p["by_stage"][s])
            ready_mark = {True: "ready", False: "not ready"}.get(p["ready"], "unknown")
            print(f"  {p['program']:42} {p['variables']:4} variables  {stages or '-':34} {ready_mark}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
