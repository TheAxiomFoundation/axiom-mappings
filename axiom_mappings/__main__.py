"""The harness, one command:

    python -m axiom_mappings validate   ...   schema, integrity, PolicyEngine, RuleSpec, bindings, coverage
    python -m axiom_mappings identity   ...   do the Axiom ids exist at the pinned RuleSpec commit
    python -m axiom_mappings verify parameters --corpus <rulespec-us> [--program P] [--as-of D] [--json] [--out F]
    python -m axiom_mappings readiness  ...   may a program serve Axiom's answer (D48)
    python -m axiom_mappings profiles   ...   slots the cut and facts profiles bind differently
    python -m axiom_mappings export policyengine-axiom|axiom-api --out DIR
    python -m axiom_mappings export oracles-populace --out FILE
    python -m axiom_mappings bindings set-status --program ID --variable PE_VAR --status off|shadow|on [--profile cut]
    python -m axiom_mappings propose parameters|outputs --corpus <rulespec-us> [--module PREFIX] [--evaluate]
    python -m axiom_mappings propose slots --program ID --catalog SLOTS.json [--profile cut] [--evaluate]
    python -m axiom_mappings investigate --finding PROGRAM:VARIABLE --corpus <rulespec-us> [--live] [--replay DIR]
    python -m axiom_mappings accept proposals/<kind>/<name>.yaml
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path


def verify_parameters(argv: list[str]) -> int:
    from . import load
    from .validate import policyengine_system
    from .verify.parameters import differences, file_findings, gaps, proposed_findings, verify

    ap = argparse.ArgumentParser(prog="axiom_mappings verify parameters")
    ap.add_argument("--country", default="us")
    ap.add_argument("--corpus", required=True, help="rulespec-us git checkout (read at the pin)")
    ap.add_argument("--ref", help="commit to read instead of the pin")
    ap.add_argument("--program", help="only mapping rows of this program (e.g. snap)")
    ap.add_argument("--as-of", help="values taking effect after this date are projections (default: today)")
    ap.add_argument("--json", action="store_true", help="print the whole report")
    ap.add_argument("--out", type=Path, help="write the whole report here")
    ap.add_argument("--file-findings", action="store_true",
                    help="append every unfiled current difference to findings.yaml as unclassified (D48)")
    args = ap.parse_args(argv)
    m = load(args.country)
    report = verify(m, args.corpus, policyengine_system(), as_of=args.as_of, program=args.program, ref=args.ref)
    report["proposed_findings"] = proposed_findings(report)
    if args.file_findings:
        added = file_findings(m, report)
        print(f"filed {len(added)} unclassified parameter finding(s) in findings.yaml", file=sys.stderr)
    if args.out:
        args.out.write_text(json.dumps(report, indent=1, default=str))
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 0
    print(f"rulespec-us {report['ref'][:9]}, map {report['map_release']}, as of {report['as_of']}: "
          f"{report['rows']} mapping rows -> {report['targets']} PolicyEngine cells compared")
    for verdict, n in sorted(report["verdicts"].items(), key=lambda kv: -kv[1]):
        print(f"  {verdict or 'none':16} {n}")
    print(f"  not checkable    {len(report['unchecked'])} rows: {dict(Counter(report['unchecked_reasons']).most_common(5))}")
    print(f"  unit mismatches  {len(report['unit_mismatches'])}")
    for r in (differences(report) + gaps(report))[:40]:
        s = next(s for s in r["segments"] if not s["projected"] and s["verdict"] == r["verdict"])
        hint = f"  [{s['hint']}]" if s.get("hint") else ""
        print(f"  {r['verdict']:15} {r['target']}: from {s['from']} Axiom {s['axiom']} (since {s['axiom_since']}) "
              f"vs PE {s['pe']} (since {s['pe_since']}){hint}")
    return 0


def bindings_set_status(argv: list[str]) -> int:
    from .bindings import set_status

    ap = argparse.ArgumentParser(prog="axiom_mappings bindings set-status")
    ap.add_argument("--program", required=True, help="program id in programs.yaml, e.g. us/ctc")
    ap.add_argument("--variable", required=True, help="the bound PolicyEngine variable")
    ap.add_argument("--status", required=True, choices=["off", "shadow", "on"])
    ap.add_argument("--profile", default="cut")
    args = ap.parse_args(argv)
    path = set_status(args.program, args.variable, args.status, profile=args.profile)
    print(f"{args.program}.{args.profile} {args.variable} -> {args.status} in {path}")
    return 0


def propose(argv: list[str]) -> int:
    from . import load
    from .propose import write
    from .validate import policyengine_system

    kind, rest = (argv[0], argv[1:]) if argv else ("", [])
    ap = argparse.ArgumentParser(prog=f"axiom_mappings propose {kind}")
    ap.add_argument("--country", default="us")
    ap.add_argument("--evaluate", action="store_true", help="replay the reviewed map and report how often it is recovered")
    ap.add_argument("--limit", type=int, default=3, help="candidates per proposal")
    ap.add_argument("--name", help="proposal file name (default: from the module prefix or program)")
    if kind in ("parameters", "outputs"):
        ap.add_argument("--corpus", required=True, help="rulespec-us git checkout (read at the pin)")
        ap.add_argument("--module", help="only Axiom modules whose id starts with this, e.g. us:statutes/26/24")
        if kind == "outputs":
            ap.add_argument("--include-prefixed", action="store_true",
                            help="also propose exact overrides for rules a prefix row classifies in bulk")
    elif kind == "slots":
        ap.add_argument("--program", required=not ("--evaluate" in rest))
        ap.add_argument("--catalog", type=Path, help="the program's slot catalog JSON")
        ap.add_argument("--profile", default="cut", choices=["cut", "facts"])
        ap.add_argument("--root-entity")
        ap.add_argument("--entity-map", help="Axiom=pe pairs, comma-separated (for a program with no bindings yet)")
    else:
        print("propose parameters | outputs | slots", file=sys.stderr)
        return 2
    args = ap.parse_args(rest)
    m, system = load(args.country), policyengine_system()
    if kind == "parameters":
        from .propose import parameters as mod
    elif kind == "outputs":
        from .propose import outputs as mod
    else:
        from .propose import slots as mod
    if args.evaluate:
        report = mod.evaluate(m, args.corpus, system) if kind != "slots" else mod.evaluate(m, system, profile=args.profile)
        print(json.dumps(report["outcome"], indent=1))
        return 0
    if kind == "slots":
        entity_map = dict(pair.split("=", 1) for pair in args.entity_map.split(",")) if args.entity_map else None
        report = mod.propose(m, system, args.program, json.loads(args.catalog.read_text()), profile=args.profile,
                             root_entity=args.root_entity, entity_map=entity_map, limit=args.limit)
        name = args.name or f"{args.program.replace('/', '__')}.{args.profile}"
    else:
        extra_args = {"include_prefixed": args.include_prefixed} if kind == "outputs" else {}
        report = mod.propose(m, args.corpus, system, modules=args.module, limit=args.limit, **extra_args)
        name = args.name or (args.module or "all").replace(":", "__").replace("/", "__")
    proposals = report.pop("proposals")
    path = write(kind, name, report, proposals)
    extra = {k: v for k, v in report.items() if k in ("skipped",)} | ({"unmatched": len(report["unmatched"])} if "unmatched" in report else {})
    print(f"{len(proposals)} {kind} proposal(s) -> {path} {extra}")
    return 0


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "verify" and rest[:1] == ["parameters"]:
        return verify_parameters(rest[1:])
    if command == "propose":
        return propose(rest)
    if command == "investigate":
        from .investigate import main as run
        return run(rest)
    if command == "accept":
        from .accept import main as run
        return run(rest)
    if command == "bindings" and rest[:1] == ["set-status"]:
        return bindings_set_status(rest[1:])
    if command == "export" and rest[:1] == ["oracles-populace"]:
        from .export.oracles_populace import main as run
        return run(rest[1:])
    if command == "export" and rest[:1] == ["axiom-api"]:
        from .export.axiom_api import main as run
        return run(rest[1:])
    if command == "export" and rest[:1] == ["policyengine-axiom"]:
        from .export.policyengine_axiom import main as run
        return run(rest[1:])
    modules = {"validate": "validate", "identity": "identity", "readiness": "readiness", "profiles": "profiles"}
    if command in modules:
        module = __import__(f"axiom_mappings.{modules[command]}", fromlist=["main"])
        return module.main(rest)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
