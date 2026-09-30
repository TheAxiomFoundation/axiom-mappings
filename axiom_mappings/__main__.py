"""The harness, one command:

    python -m axiom_mappings validate   ...   schema, integrity, PolicyEngine, RuleSpec, bindings, coverage
    python -m axiom_mappings identity   ...   do the Axiom ids exist at the pinned RuleSpec commit
    python -m axiom_mappings verify parameters --corpus <rulespec-us> [--program P] [--as-of D] [--json] [--out F]
    python -m axiom_mappings readiness  ...   may a program serve Axiom's answer (D48)
    python -m axiom_mappings profiles   ...   slots the cut and facts profiles bind differently
    python -m axiom_mappings export policyengine-axiom --out DIR
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


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    command, rest = argv[0], argv[1:]
    if command == "verify" and rest[:1] == ["parameters"]:
        return verify_parameters(rest[1:])
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
