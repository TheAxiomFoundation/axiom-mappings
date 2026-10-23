"""Do the two harnesses tell the same story about a binding?

    python -m axiom_mappings agreement [--ledger PATH] [--dispositions DIR] [--json]

Two comparisons judge a binding, and they are not the same comparison:

* **policyengine-axiom** runs the Axiom rule *inside* PolicyEngine, as the
  provider behind a PE variable, and compares the provider's column against
  PE's own formula over a population. It files `ledger.json`: one entry per
  `{program}:{pe_variable}`, with `passes`, `max_abs_diff` and
  `tolerance_failures`.
* **axiom-oracles** runs the Axiom engine *against* PolicyEngine as a separate
  system, and where the two differ it files a disposition: a named, attributed
  reason, under `dispositions/*.yaml`, keyed by the Axiom concept id.

When both have looked at the same binding and tell the same story, the binding
is sound. When they do not, the interesting conclusion is about the **mapping**,
not about either rule: the same rule under two harnesses should not answer
twice. That is what this stage flags.

Nothing runs here. Each harness owns a corpus, a population of tens of
thousands of rows and its own credentials; a run is an hour, not a step in a
weekly pass. This reads what each one filed, joins it to the map, and reports:

* `aligned` — the in-PE run passes and no disposition records a difference.
* `explained` — the in-PE run fails and a disposition explains why. Only the
  kinds axiom-oracles itself counts as explained qualify.
* `attributed_not_explained` — the in-PE run fails and every disposition is an
  `axiom_encoding_gap` or an open `unexplained`. The cause has a name and an
  owner, and it is upstream in rulespec-us, not in this map.
* `noted_in_the_map` — the in-PE run fails, no disposition names it, but the
  binding's own `notes` do. A note is this map's claim about the difference, not
  a second harness confirming it, so it is reported with the note's keys.
* `unexplained` — the in-PE run fails and nothing names it: no disposition, no
  note. **The work.**
* `divergent_evidence` — the in-PE run passes, yet a disposition records a
  difference. Read the scope before concluding: a disposition may select a
  handful of cases that a population tolerance absorbs. The row carries the
  case count so a reviewer can see which it is.
* `pe_side_only` / `oracle_side_only` / `no_evidence` — one side or neither has
  looked. A binding at `on` with no evidence is the state promotion exists to
  prevent, so it is reported rather than left blank.

The join, and why it needs scoping. On the PolicyEngine side it is the variable
name: a ledger key is `{program}:{pe_variable}`, and the map's own unit is the
PE variable. On the Axiom side a disposition names a full concept id
(`us:statutes/7/2014/o#x`) while a binding names a local output (`x`), so the
fragment is the join key.

The fragment alone is far too loose. Every state's SNAP suite disputes the same
federal `#snap_eligible` concept, so an unscoped match made Alabama's findings
read as evidence about a Colorado binding — 150 entries on one row. Two filters
narrow it: the concept's jurisdiction must share the program's country root,
and a suite scoped to one state is evidence only about that state's program. A
suite with no sub-national scope (federal or topical) stays in scope for every
program of that country. A reviewer still confirms the module; two modules can
spell one local name.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from . import ROOT, Mappings, load

#: `aligned` first, then what needs reading, then absence of evidence.
VERDICTS = (
    "aligned",
    "explained",
    "attributed_not_explained",
    "noted_in_the_map",
    "unexplained",
    "divergent_evidence",
    "pe_side_only",
    "oracle_side_only",
    "no_evidence",
)

#: The verdicts a reviewer must act on.
FLAGGED = ("unexplained", "divergent_evidence")

#: Which disposition kinds axiom-oracles itself counts as explained
#: (dispositions/README.md). An `axiom_encoding_gap` is classified and owned,
#: but it is a real difference, so it never counts as explained.
EXPLAINS = frozenset({"explained_residual", "upstream_engine_gap", "bridge_artifact"})

#: Sub-national suite prefixes that are not a two-letter code.
SUITE_SCOPE_ALIASES = {"nyc": "ny"}


def _sibling(*parts: str) -> Path:
    """A checkout beside this one. The repos are peers, not dependencies."""
    return ROOT.parent.parent.joinpath(*parts)


def read_ledger(path: Path | str | None) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """policyengine-axiom's ledger, keyed by PE variable.

    One variable can be certified under more than one program. Keep every
    entry: a binding is per program, and collapsing them would hide the one
    that fails.
    """
    path = Path(path) if path else _sibling("policyengine-axiom", "ledger.json")
    source: dict[str, Any] = {"path": str(path)}
    if not path.exists():
        source["error"] = "no ledger: policyengine-axiom has filed nothing here"
        return {}, source
    doc = json.loads(path.read_text())
    entries: dict[str, dict[str, Any]] = {}
    for key, entry in (doc.get("entries") or {}).items():
        program, _, variable = key.rpartition(":")
        entries.setdefault(variable, {})[program] = entry
    source["format"] = doc.get("format")
    source["entries"] = len(doc.get("entries") or {})
    source["variables"] = len(entries)
    return entries, source


def read_dispositions(root: Path | str | None) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """axiom-oracles' dispositions, keyed by the concept id's local fragment."""
    root = Path(root) if root else _sibling("axiom-oracles", "dispositions")
    source: dict[str, Any] = {"path": str(root)}
    if not root.exists():
        source["error"] = "no dispositions: axiom-oracles has filed nothing here"
        return {}, source
    by_fragment: dict[str, list[dict[str, Any]]] = {}
    suites = 0
    total = 0
    for file in sorted(root.glob("*.yaml")):
        try:
            doc = yaml.safe_load(file.read_text()) or {}
        except yaml.YAMLError as error:  # noqa: PERF203 - one bad suite is reportable, not fatal
            source.setdefault("unreadable", []).append(f"{file.name}: {error.__class__.__name__}")
            continue
        suites += 1
        for entry in doc.get("entries") or []:
            concept = entry.get("concept") or ""
            if "#" not in concept:
                continue
            selector = entry.get("case_selector") or {}
            cases = selector.get("case_ids") or []
            total += 1
            by_fragment.setdefault(concept.rsplit("#", 1)[1], []).append({
                "suite": doc.get("suite") or file.stem,
                "id": entry.get("id"),
                "concept": concept,
                "kind": entry.get("kind"),
                "disposition": entry.get("disposition"),
                "cases": len(cases) if cases else None,
                "linked_issue": entry.get("linked_issue"),
                "expires_on_source_change": entry.get("expires_on_source_change"),
            })
    source["suites"] = suites
    source["entries"] = total
    source["concepts"] = len(by_fragment)
    return by_fragment, source


def _program_scope(program: str) -> tuple[str, str | None]:
    """A map program id -> its country root and its state, if it has one.

    `us-co/snap-fy2026` is Colorado's; `us/ctc` is federal.
    """
    jurisdiction = program.split("/", 1)[0]
    country, _, state = jurisdiction.partition("-")
    return country, (state or None)


def _suite_scope(suite: str) -> str | None:
    """The state a disposition suite is about, or None when it is not state-scoped.

    Within one country a two-letter leading token is a state code; `us-`,
    `medicaid-` and the other topical suites carry no sub-national scope and
    stay in scope for every program of that country.
    """
    token = suite.split("-", 1)[0].lower()
    if token in SUITE_SCOPE_ALIASES:
        return SUITE_SCOPE_ALIASES[token]
    return token if len(token) == 2 and token != "us" else None


def _in_scope(entry: dict[str, Any], country: str, state: str | None) -> bool:
    """Is this disposition evidence about *this* binding?"""
    concept_country = entry["concept"].split(":", 1)[0].split("-", 1)[0]
    if concept_country != country:
        return False
    suite_state = _suite_scope(entry["suite"])
    return suite_state is None or suite_state == state


def _verdict(ledger: dict[str, Any] | None, filed: list[dict[str, Any]],
             notes: dict[str, Any] | None = None) -> str:
    if ledger is None:
        return "oracle_side_only" if filed else "no_evidence"
    if ledger.get("passes") is None:
        return "pe_side_only"
    if not filed:
        if ledger["passes"]:
            return "aligned"
        # The map's own note names most of these. Calling that "unexplained"
        # would report work that is already recorded, and hide the one row
        # where nothing anywhere names the difference.
        return "noted_in_the_map" if notes else "unexplained"
    if ledger["passes"]:
        return "divergent_evidence"
    if any(entry["disposition"] in EXPLAINS for entry in filed):
        return "explained"
    return "attributed_not_explained"


def agreement(
    m: Mappings,
    *,
    ledger_path: Path | str | None = None,
    dispositions_root: Path | str | None = None,
) -> dict[str, Any]:
    ledger, ledger_source = read_ledger(ledger_path)
    filed, disposition_source = read_dispositions(dispositions_root)

    rows: list[dict[str, Any]] = []
    for (program, profile), bs in m.bindings.items():
        for block in bs.doc.get("bindings") or []:
            variable = block.get("pe_variable")
            output = block.get("axiom_output")
            if not variable:
                continue
            by_program = ledger.get(variable) or {}
            # A ledger program id is policyengine-axiom's manifest id, not the
            # map's program key, so prefer an entry whose id contains the map's
            # own program spelled with dashes, and fall back to the only entry.
            slug = program.replace("/", "-")
            entry = by_program.get(slug)
            if entry is None and len(by_program) == 1:
                entry = next(iter(by_program.values()))
            country, state = _program_scope(program)
            matched = [
                entry for entry in filed.get(output or "") or []
                if _in_scope(entry, country, state)
            ]
            rows.append({
                "program": program,
                "profile": profile,
                "policyengine_variable": variable,
                "axiom_output": output,
                "status": block.get("status"),
                "verdict": _verdict(entry, matched, block.get("notes")),
                "noted_in_the_map": sorted(block.get("notes") or {}),
                "in_policyengine": None if entry is None else {
                    "passes": entry.get("passes"),
                    "certified": entry.get("certified"),
                    "max_abs_diff": entry.get("max_abs_diff"),
                    "tolerance_failures": entry.get("tolerance_failures"),
                    "compared_rows": entry.get("compared_rows"),
                    "report_at": entry.get("report_at"),
                },
                "against_policyengine": matched,
            })

    # A disposition about a concept no binding names is not a gap in the map;
    # it is the oracle working on rules the map has not bound yet. Count it, so
    # the number is visible, and do not make a row of it.
    bound_outputs = {row["axiom_output"] for row in rows if row["axiom_output"]}
    unbound = sum(
        len(entries) for fragment, entries in filed.items() if fragment not in bound_outputs
    )

    rows.sort(key=lambda r: (VERDICTS.index(r["verdict"]), r["program"], r["policyengine_variable"]))
    by_verdict = {verdict: 0 for verdict in VERDICTS}
    for row in rows:
        by_verdict[row["verdict"]] += 1
    return {
        "schema": "axiom_mappings.agreement.v1",
        "map_release": m.release,
        "sources": {"in_policyengine": ledger_source, "against_policyengine": disposition_source},
        "totals": {
            "bindings": len(rows),
            "by_verdict": by_verdict,
            "flagged": sum(by_verdict[v] for v in FLAGGED),
            "dispositions_about_unbound_concepts": unbound,
        },
        "bindings": rows,
    }


def _one_line(row: dict[str, Any]) -> str:
    pe = row["in_policyengine"]
    inside = "no run" if pe is None else (
        f"passes, {pe['compared_rows']} rows" if pe.get("passes")
        else f"{pe.get('tolerance_failures')} row(s) over tolerance, max {pe.get('max_abs_diff')}"
    )
    # One line per kind, not per entry: a suite files dozens, and the kind plus
    # the entry count is what a reviewer reads first.
    kinds: dict[str, int] = {}
    for entry in row["against_policyengine"]:
        kinds[entry["disposition"]] = kinds.get(entry["disposition"], 0) + 1
    against = ", ".join(f"{kind} x{count}" for kind, count in sorted(kinds.items())) or "nothing filed"
    return f"  {row['policyengine_variable']} [{row['status']}] inside PE: {inside} | against PE: {against}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings agreement", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--country", default="us")
    ap.add_argument("--ledger", default=None, help="policyengine-axiom's ledger.json")
    ap.add_argument("--dispositions", default=None, help="axiom-oracles' dispositions directory")
    ap.add_argument("--out", type=Path, help="write the full JSON here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    report = agreement(load(args.country), ledger_path=args.ledger, dispositions_root=args.dispositions)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    for side, source in report["sources"].items():
        if source.get("error"):
            print(f"{side}: {source['error']} ({source['path']})")
        else:
            print(f"{side}: {source['path']}")
    totals = report["totals"]
    print(f"\n{totals['bindings']} binding(s), {totals['flagged']} flagged; "
          f"{totals['dispositions_about_unbound_concepts']} disposition(s) about concepts no binding names")
    for verdict in VERDICTS:
        count = totals["by_verdict"][verdict]
        if not count:
            continue
        print(f"\n{verdict} ({count}):")
        for row in report["bindings"]:
            if row["verdict"] == verdict:
                print(_one_line(row))
    return 0


if __name__ == "__main__":
    sys.exit(main())
