"""The coverage map: where every PolicyEngine variable the map knows stands, per door.

`readiness` answers "may this program serve", one row per program. This answers
"where is this variable", one row per PolicyEngine variable, which is what a
dashboard joins against partner demand. Demand lives in axiom-api, not here, so
this emits only the supply side and leaves the join to the consumer.

A variable reaches customers through two doors, and each door has its own stage:

* ``policyengine`` (Door 1): PolicyEngine with policyengine-axiom underneath,
  read from the ``cut`` bindings. Its ladder is

      unmapped    no output row names a PolicyEngine variable
      mapped      an output row names one, so a rule exists to bind
      bound       a binding set pairs them, but nothing runs: status off, or
                  outside the program's time window
      shadow      the binding runs beside PolicyEngine and the difference is recorded
      on          the binding answers: status on, certified in policyengine-axiom's
                  ledger, inside the program's time window

  A binding whose status says ``on`` without that certification runs as shadow
  (that is what the provider does with it), so its stage is ``shadow`` and the
  row is marked ``overclaimed``. The status is a claim; the ledger is evidence.

* ``axiom_api`` (Door 2): axiom-api's compat endpoint, read from the ``facts``
  bindings, which are exported as axiom-api's serving maps. A variable there is
  served (``on``); an echo (an input asked back as an output, such as ``age``)
  is served too but computes no rule, so it carries ``kind: echo``.

The row's own ``stage`` is the furthest either door reached. ``blocked_by`` is
why it has not gone further: an unresolved Axiom id, a finding with a blocking
cause, a program not ready, a declared ``on`` the ledger does not certify, or a
closed time window. A variable can be ``on`` and still carry a non-blocking
finding, so read ``stage`` and ``blocked_by`` together, never one alone.
``unverified`` lists what was not checked (a program's readiness without its
slot catalog), which is an unknown, not a blocker.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

from .. import Mappings

SCHEMA = "axiom_mappings.coverage.v2"
STAGES = ("unmapped", "mapped", "bound", "shadow", "on")
DOORS = ("policyengine", "axiom_api")
PROFILE_DOOR = {"cut": "policyengine", "facts": "axiom_api"}


def _rank(stage: str) -> int:
    return STAGES.index(stage)


def _furthest(stages: list[str], floor: str = "unmapped") -> str:
    return max([floor, *stages], key=_rank)


def _binding_rows(m: Mappings) -> dict[str, list[dict[str, Any]]]:
    """PolicyEngine variable -> the bindings that name it, across programs and both profiles.

    A ``cut`` set lists ``bindings:`` with a status each. A ``facts`` set maps
    ``variables:`` by PE name and has no status: what it names, axiom-api serves.
    """
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
        for variable, spec in sorted((bs.doc.get("variables") or {}).items()):
            spec = spec or {}
            echo = spec.get("kind") == "echo"
            rows.setdefault(variable, []).append(
                {
                    "program": program,
                    "profile": profile,
                    "axiom_output": None if echo else (spec.get("symbol") or spec.get("root")),
                    "status": "on",
                    "kind": "echo" if echo else "rule",
                    "note": spec.get("notes"),
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


def window_state(window: list[str] | None, as_of: datetime.date) -> str | None:
    """Where ``as_of`` sits against a program's ``[from, to]`` window (D40, D50).

    ``None`` when the program declares no window, else ``open``, ``expired`` or ``future``.
    """
    if not window:
        return None
    start, end = (datetime.date.fromisoformat(d) for d in window)
    if as_of < start:
        return "future"
    if as_of > end:
        return "expired"
    return "open"


def _ledger_entry(entries: dict[str, dict[str, Any]], program: str, variable: str) -> dict[str, Any] | None:
    """policyengine-axiom's entry for this binding, matched as `agreement` matches it.

    A ledger key is ``{manifest id}:{pe variable}``; the manifest id is the map's
    program spelled with dashes. With one entry for the variable, that entry.
    """
    by_program = entries.get(variable) or {}
    entry = by_program.get(program.replace("/", "-"))
    if entry is None and len(by_program) == 1:
        entry = next(iter(by_program.values()))
    return entry


def _door1(
    bound: list[dict[str, Any]],
    m: Mappings,
    variable: str,
    ledger: dict[str, dict[str, Any]],
    ledger_found: bool,
    as_of: datetime.date,
) -> dict[str, Any] | None:
    """The Door 1 state of one variable's ``cut`` bindings, or None if it has none."""
    cut = [b for b in bound if b["profile"] == "cut"]
    if not cut:
        return None
    rows: list[dict[str, Any]] = []
    reasons: list[str] = []
    for b in cut:
        program = b["program"]
        declared = {"on": "on", "shadow": "shadow"}.get(b["status"], "bound")
        window = (m.programs.get(program) or {}).get("window")
        state = window_state(window, as_of)
        entry = _ledger_entry(ledger, program, variable)
        certified = None if entry is None else bool(entry.get("certified"))
        effective = declared
        if declared != "bound" and state in ("expired", "future"):
            # Outside its window the provider answers with PE and compares nothing.
            effective = "bound"
            when = window[1] if state == "expired" else window[0]
            reasons.append(f"{program}: time window {'closed' if state == 'expired' else 'opens'} on {when}")
        elif declared == "on" and not certified:
            # The provider runs an uncertified `on` as shadow; so does this.
            effective = "shadow"
            reasons.append(
                f"{program}: declared on, but policyengine-axiom's ledger does not certify it"
                if ledger_found
                else f"{program}: declared on, but no policyengine-axiom ledger was read"
            )
        rows.append(
            {
                "program": program,
                "declared": declared,
                "stage": effective,
                "certified": certified,
                "report_at": None if entry is None else entry.get("report_at"),
                "dataset": None if entry is None else entry.get("dataset"),
                "window": window,
                "window_state": state,
            }
        )
    stage = _furthest([r["stage"] for r in rows], "bound")
    declared = _furthest([r["declared"] for r in rows], "bound")
    return {
        "stage": stage,
        "declared": declared,
        "overclaimed": _rank(declared) > _rank(stage),
        "bindings": rows,
        "blocked_by": reasons,
    }


def _door2(bound: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Door 2 state of one variable's ``facts`` bindings, or None if it has none."""
    facts = [b for b in bound if b["profile"] == "facts"]
    if not facts:
        return None
    kinds = {b.get("kind", "rule") for b in facts}
    return {
        "stage": "on",
        "kind": "rule" if "rule" in kinds else "echo",
        "programs": sorted({b["program"] for b in facts}),
        "blocked_by": [],
    }


def _readiness_reasons(program: str, report: dict[str, Any]) -> tuple[list[str], list[str]]:
    """A program's readiness as (blocking reasons, unverified notes).

    Without a slot catalog, readiness can only say coverage is unverified, and
    it then reports the program not ready. That is an unknown, not a refusal, so
    it is kept apart: a blocker says what must change, an unknown says what was
    not checked. A not-ready report with no reasons at all stays a blocker.
    """
    if report.get("ready") is not False:
        return [], []
    blockers = report.get("blockers")
    if blockers is None:
        return [f"program {program} is not ready"], []
    unknown = [b for b in blockers if "unverified" in b or "coverage unknown" in b]
    real = [b for b in blockers if b not in unknown]
    if real:
        return [f"program {program} is not ready: {'; '.join(real)}"], []
    return [], [f"program {program}: readiness unverified (no slot catalog given)"]


def _describe_ledger(source: dict[str, Any], entries: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """What was read as Door 1 evidence, fit for a checked-in file.

    The reader's absolute path names one machine, so only its last two parts are
    kept. The newest ``report_at`` says how old the evidence is.
    """
    out = {k: v for k, v in source.items() if k != "path"}
    if source.get("path"):
        out["file"] = "/".join(Path(source["path"]).parts[-2:])
    dates = [e.get("report_at") for by_program in entries.values() for e in by_program.values() if e.get("report_at")]
    if dates:
        out["latest_report_at"] = max(dates)
    return out


def _unbound(mapped: bool) -> dict[str, Any]:
    return {"stage": "mapped" if mapped else "unmapped", "blocked_by": []}


def coverage(
    m: Mappings,
    *,
    readiness_by_program: dict[str, dict[str, Any]] | None = None,
    ledger: dict[str, dict[str, Any]] | None = None,
    ledger_source: dict[str, Any] | None = None,
    as_of: datetime.date | None = None,
) -> dict[str, Any]:
    """The whole supply-side coverage map: variables, a per-door view, and a per-program rollup.

    ``ledger`` is policyengine-axiom's ledger keyed as `agreement.read_ledger`
    returns it. Without one, nothing is certified, so no Door 1 binding is ``on``:
    missing evidence must never read as evidence.
    """
    bindings = _binding_rows(m)
    outputs = _output_rows(m)
    ready = readiness_by_program or {}
    entries = ledger or {}
    source = _describe_ledger(ledger_source or {"error": "no ledger given"}, entries)
    ledger_found = ledger is not None and "error" not in source
    as_of = as_of or datetime.datetime.now(datetime.timezone.utc).date()

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
        judged = {p: _readiness_reasons(p, ready.get(p, {})) for p in programs}

        doors = {
            "policyengine": _door1(bound, m, variable, entries, ledger_found, as_of) or _unbound(bool(mapped)),
            "axiom_api": _door2(bound) or _unbound(bool(mapped)),
        }
        for door in DOORS:
            door_programs = {b["program"] for b in bound if PROFILE_DOOR.get(b["profile"]) == door}
            for f in blocking:
                if f.get("program") in door_programs:
                    doors[door]["blocked_by"].append(
                        f"finding {f['cause']}" + (f" ({f['issue']})" if f.get("issue") else "")
                    )
            for p in sorted(door_programs):
                doors[door]["blocked_by"].extend(judged[p][0])

        blocked_by: list[str] = []
        if unresolved:
            blocked_by.append(f"Axiom id does not resolve at the pin: {', '.join(unresolved)}")
        for door in DOORS:
            for reason in doors[door]["blocked_by"]:
                if reason not in blocked_by:
                    blocked_by.append(reason)

        variables.append(
            {
                "policyengine": variable,
                "stage": _furthest([doors[d]["stage"] for d in DOORS], "mapped" if mapped else "unmapped"),
                "doors": doors,
                "axiom": sorted({row["axiom"] for row in mapped if row.get("axiom")}),
                "programs": programs,
                "bindings": bound,
                "findings": [
                    {"cause": f.get("cause"), "issue": f.get("issue"), "note": f.get("note")} for f in findings
                ],
                "blocked_by": blocked_by,
                "unverified": [note for p in programs for note in judged[p][1]],
            }
        )

    def count(rows: list[dict[str, Any]], key) -> dict[str, int]:
        return {stage: sum(1 for v in rows if key(v) == stage) for stage in STAGES}

    by_stage = count(variables, lambda v: v["stage"])
    by_door = {door: count(variables, lambda v, d=door: v["doors"][d]["stage"]) for door in DOORS}
    programs_rollup = []
    for program in sorted(m.programs):
        rows = [v for v in variables if program in v["programs"]]
        profiles = sorted({profile for (p, profile) in m.bindings if p == program})
        window = (m.programs.get(program) or {}).get("window")
        programs_rollup.append(
            {
                "program": program,
                "doors": sorted({PROFILE_DOOR[p] for p in profiles if p in PROFILE_DOOR}),
                "variables": len(rows),
                "by_stage": count(rows, lambda v: v["stage"]),
                "blocked": sum(1 for v in rows if v["blocked_by"]),
                "ready": ready.get(program, {}).get("ready"),
                "readiness_blockers": ready.get(program, {}).get("blockers"),
                "window": window,
                "window_state": window_state(window, as_of),
            }
        )

    return {
        "schema": SCHEMA,
        "map_release": m.release,
        "country": m.country,
        "unresolved_ref": m.unresolved_ref,
        "as_of": as_of.isoformat(),
        "evidence": {"policyengine_axiom_ledger": source},
        "totals": {
            "variables": len(variables),
            "by_stage": by_stage,
            "by_door": by_door,
            "overclaimed": sum(1 for v in variables if v["doors"]["policyengine"].get("overclaimed")),
            "blocked": sum(1 for v in variables if v["blocked_by"]),
            "unverified": sum(1 for v in variables if v["unverified"]),
        },
        "programs": programs_rollup,
        "variables": variables,
    }


def main(argv=None) -> int:
    import argparse
    import json
    from pathlib import Path

    from .. import load
    from ..agreement import read_ledger
    from ..readiness import readiness as program_readiness

    ap = argparse.ArgumentParser(prog="axiom_mappings export coverage")
    ap.add_argument("--country", default="us")
    ap.add_argument("--out", type=Path, help="write JSON here; otherwise print a summary")
    ap.add_argument("--catalog", action="append", default=[], help="a program's slot catalog JSON (repeatable)")
    ap.add_argument("--ledger", default=None,
                    help="policyengine-axiom's ledger.json (default: the sibling checkout's)")
    ap.add_argument("--as-of", type=datetime.date.fromisoformat, default=None,
                    help="judge time windows on this date (default: today, UTC)")
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
    entries, source = read_ledger(args.ledger)
    report = coverage(m, readiness_by_program=ready, ledger=entries, ledger_source=source, as_of=args.as_of)

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
    print(f"map release {report['map_release']}  ({totals['variables']} variables, {totals['blocked']} blocked, "
          f"{totals['overclaimed']} overclaimed)  as of {report['as_of']}")
    ledger = report["evidence"]["policyengine_axiom_ledger"]
    print(f"  ledger: {ledger.get('error') or ledger.get('file')}"
          + (f", newest report {ledger['latest_report_at']}" if ledger.get("latest_report_at") else ""))
    print("  furthest:     " + "  ".join(f"{stage}: {totals['by_stage'][stage]}" for stage in STAGES))
    for door in DOORS:
        print(f"  {door + ':':13} " + "  ".join(f"{stage}: {totals['by_door'][door][stage]}" for stage in STAGES))
    if args.stage or args.blocked:
        for v in rows:
            why = f"  <- {'; '.join(v['blocked_by'])}" if v["blocked_by"] else ""
            doors = " ".join(f"{d}={v['doors'][d]['stage']}" for d in DOORS)
            print(f"  {v['stage']:8} {v['policyengine']:48} {doors:38} {', '.join(v['programs']) or '-'}{why}")
    else:
        for p in report["programs"]:
            stages = " ".join(f"{s}={p['by_stage'][s]}" for s in STAGES if p["by_stage"][s])
            ready_mark = {True: "ready", False: "not ready"}.get(p["ready"], "unknown")
            print(f"  {p['program']:42} {p['variables']:4} variables  {stages or '-':34} {ready_mark}")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
