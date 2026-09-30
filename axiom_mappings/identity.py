"""Do the map's Axiom ids exist in RuleSpec? The Axiom side of every mapping, checked at a commit.

    python -m axiom_mappings.identity --corpus ~/rulespec-us [--ref SHA] [--json] [--write-baseline]
    python -m axiom_mappings.identity --refs    # the commits a check reads, for a shallow fetch

Every ``outputs``, ``parameters`` and ``prefixes`` id is resolved at the map's pinned ``rulespec_us`` commit
(``--ref`` overrides it), and each program's source module at the program's own ``corpus_ref``.
An id that does not resolve is reported with the harness's suggestions (the same rule name in
another module of the jurisdiction, or a longer or shorter name in its own module). Suggestions are
for a reviewer; a rename is a claim about meaning, so nothing is rewritten automatically.

``data/<country>/unresolved.yaml`` is a ratchet: it lists the ids known not to resolve at the pin.
A newly unresolved id is an error, and so is a listed id that resolves again or is no longer
mapped, so the list only shrinks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

from . import ROOT, Mappings, load
from .corpus import Corpus

TABLES = ("outputs", "parameters")  # plus prefixes.yaml, checked as prefixes


def _is_prefix(row: dict[str, Any]) -> bool:
    """A row keyed by a module prefix (``module#``) rather than one output."""
    return bool(row.get("keyed_by_prefix_string")) or row["axiom"].endswith("#")


def check(m: Mappings, corpus_root: str | Path, ref: str | None = None) -> dict[str, Any]:
    ref = ref or m.pins.get("rulespec_us")
    corpus = Corpus(corpus_root, ref)
    tables = {r["axiom"]: t for t in TABLES for r in getattr(m, t) if r.get("axiom") and not _is_prefix(r)}
    resolved = corpus.resolve_all(tables)
    prefixes = {r["axiom"]: t for t in TABLES for r in getattr(m, t) if r.get("axiom") and _is_prefix(r)}
    prefixes |= {r["axiom_prefix"]: "prefixes" for r in m.prefixes if r.get("axiom_prefix")}
    for prefix, table in prefixes.items():
        resolved[prefix] = corpus.resolve_prefix(prefix)
        tables[prefix] = table
    unresolved = [{"axiom": a, "table": tables[a], "status": r.status, "candidates": list(r.candidates)}
                  for a, r in resolved.items() if not r.resolved]
    programs = []
    for p in m.programs.values():
        if p.get("assembled_by"):
            programs.append({"id": p["id"], "axiom": p["axiom"], "ref": None, "found": None, "assembled_by": p["assembled_by"]})
            continue
        at = Corpus(corpus_root, p.get("corpus_ref") or ref)
        programs.append({"id": p["id"], "axiom": p["axiom"], "ref": at.commit, "found": at.module(p["axiom"]) is not None})
    return {
        "ref": corpus.commit,
        "checked": len(tables),
        "ok": sum(r.status == "ok" for r in resolved.values()),
        "deferred": sum(r.status == "deferred" for r in resolved.values()),
        "unresolved": unresolved,
        "programs": programs,
    }


def findings(m: Mappings, report: dict[str, Any]) -> list:
    from .validate import Finding

    out = []
    baseline = m.unresolved
    now = {u["axiom"]: u for u in report["unresolved"]}
    for axiom, u in now.items():
        hint = f"; candidates: {', '.join(u['candidates'])}" if u["candidates"] else ""
        if axiom in baseline:
            out.append(Finding("warning", "unresolved-axiom-id", axiom, f"{u['status']} (listed in unresolved.yaml){hint}"))
        else:
            out.append(Finding("error", "unknown-axiom-id", axiom, f"{u['status']} in {u['table']} at {report['ref']}{hint}"))
    for axiom in baseline:
        if axiom not in now:
            out.append(Finding("error", "stale-unresolved-entry", axiom,
                               "listed in unresolved.yaml but resolves (or is no longer mapped): remove it"))
    if m.unresolved_ref and report["ref"] and m.unresolved_ref != report["ref"]:
        out.append(Finding("warning", "unresolved-baseline-ref", "unresolved.yaml",
                           f"taken at {m.unresolved_ref[:9]}, checked at {report['ref'][:9]}"))
    for p in report["programs"]:
        if p.get("assembled_by"):
            out.append(Finding("warning", "program-outside-rulespec", p["id"],
                               f"assembled in {p['assembled_by']}, not a RuleSpec module: its identity cannot be checked"))
        elif not p["found"]:
            out.append(Finding("error", "unknown-program-module", p["id"], f"{p['axiom']} is not in RuleSpec at {p['ref']}"))
    return out


def write_baseline(m: Mappings, report: dict[str, Any], root: Path | None = None) -> Path:
    path = Path(root or ROOT) / "data" / m.country / "unresolved.yaml"
    header = ("# Axiom ids in outputs.yaml and parameters.yaml that do not resolve in rulespec-us at `ref`.\n"
              "# A ratchet: the validator fails on a new one and on a listed one that resolves again, so this\n"
              "# list only shrinks. `candidates` are the harness's suggestions for a reviewer, never applied\n"
              "# automatically. Regenerate with: python -m axiom_mappings.identity --corpus PATH --write-baseline\n")
    rows = sorted(report["unresolved"], key=lambda u: (u["table"], u["axiom"]))
    body = yaml.safe_dump({"ref": report["ref"], "unresolved": rows}, sort_keys=False, width=120)
    path.write_text(header + body)
    return path


def refs(m: Mappings) -> list[str]:
    """Every rulespec-us commit a check reads: the pin and each program's own corpus_ref."""
    pinned = [m.pins.get("rulespec_us")] + [p.get("corpus_ref") for p in m.programs.values()]
    return list(dict.fromkeys(r for r in pinned if r))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings.identity")
    ap.add_argument("--country", default="us")
    ap.add_argument("--refs", action="store_true", help="print the rulespec-us commits a check needs, and exit")
    ap.add_argument("--corpus", help="a rulespec-us git checkout (read at the ref, not the work tree)")
    ap.add_argument("--ref", help="commit to check at (default: the map's rulespec_us pin)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-baseline", action="store_true", help="rewrite unresolved.yaml from this run")
    args = ap.parse_args(argv)
    m = load(args.country)
    if args.refs:
        print("\n".join(refs(m)))
        return 0
    if not args.corpus:
        ap.error("--corpus is required")
    report = check(m, args.corpus, args.ref)
    if args.write_baseline:
        print(f"wrote {write_baseline(m, report)}", file=sys.stderr)
        m = load(args.country)
    found = findings(m, report)
    if args.json:
        print(json.dumps({**report, "findings": [f.__dict__ for f in found]}, indent=2))
    else:
        print(f"rulespec-us {report['ref']}: {report['ok']} ok, {report['deferred']} deferred, "
              f"{len(report['unresolved'])} unresolved of {report['checked']} ids")
        for p in report["programs"]:
            state = "assembled outside RuleSpec" if p.get("assembled_by") else ("ok" if p["found"] else "MISSING")
            print(f"  program {p['id']:40} {state} {(p['ref'] or '')[:9]}")
        for f in found:
            if f.level == "error":
                print(f"  ERROR {f.code}: {f.where}: {f.message}")
    return 1 if any(f.level == "error" for f in found) else 0


if __name__ == "__main__":
    sys.exit(main())
