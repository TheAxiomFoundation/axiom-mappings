"""What changed since the map's pins, on either side.

    python -m axiom_mappings detect --corpus ~/rulespec-us [--ref SHA] [--json]

The map pins a RuleSpec commit and a policyengine-us version. Work arrives by
those pins moving: a new rule is encoded, a rule is renamed, a PolicyEngine
variable appears. Nothing here changes the map. It reports what a later stage
should act on, so a scheduled cycle has something to propose from.

Three questions, answered separately because each has a different owner:

* **added** — a rule at the checked ref that the pinned ref does not have.
  These are genuinely new encodings, and the `propose outputs` candidates a
  cycle should act on first.
* **removed** — a rule the pinned ref has and the checked ref does not. If the
  map names it, the mapping is now stale; `identity` suggests renames.
* **new PolicyEngine variables** — in the installed policyengine-us and named
  by no mapping.

"Added" is not "unmapped". Most rules in the corpus have never been mapped,
which is the backlog `export coverage` reports; only a two-ref diff says what
*changed*. Sending a cycle at the backlog is a decision; sending it at the
diff is the cycle working.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import Mappings, load
from .corpus import Corpus, split_id


def _mapped_axiom_ids(m: Mappings) -> set[str]:
    """Every Axiom id the map names, through any table, with renames applied."""
    ids: set[str] = set()
    for table in (m.outputs, m.parameters):
        for row in table:
            if row.get("axiom"):
                ids.add(m.current_id(row["axiom"]))
    for (_program, _profile), bs in m.bindings.items():
        for block in bs.doc.get("bindings") or []:
            if block.get("axiom_output"):
                ids.add(block["axiom_output"])
    return ids


def _rules_at(corpus: Corpus) -> dict[str, str]:
    """Every rule id the corpus defines at its ref -> the module that defines it."""
    rules: dict[str, str] = {}
    module_ids = corpus.module_ids  # a cached property, not a call
    corpus.load(module_ids)
    for module in module_ids:
        for name in (corpus.rules(module) or {}):
            rules[f"{module}#{name}"] = module
    return rules


def _policyengine_variables() -> set[str]:
    from policyengine_us import CountryTaxBenefitSystem

    return set(CountryTaxBenefitSystem().variables)


def detect(
    m: Mappings,
    corpus_root: str | Path,
    ref: str | None = None,
    *,
    policyengine: bool = True,
) -> dict[str, Any]:
    pinned_ref = m.pins.get("rulespec_us")
    ref = ref or pinned_ref
    corpus = Corpus(corpus_root, ref)
    present = _rules_at(corpus)
    mapped = _mapped_axiom_ids(m)
    checked_ref = corpus.commit or ref
    moved = checked_ref != pinned_ref

    # Only a two-ref diff says what changed. Without one, "added" is unknown
    # rather than "everything", which would send a cycle at the whole corpus.
    if moved:
        before = set(_rules_at(Corpus(corpus_root, pinned_ref)))
        added = sorted(set(present) - before)
        removed = sorted(before - set(present))
    else:
        added = []
        removed = []

    report: dict[str, Any] = {
        "schema": "axiom_mappings.detect.v1",
        "map_release": m.release,
        "pinned_ref": pinned_ref,
        "checked_ref": checked_ref,
        "moved": moved,
        "axiom": {
            "rules_at_ref": len(present),
            "mapped": len(mapped),
            "added": added,
            "removed": removed,
            "added_and_unmapped": sorted(set(added) - mapped),
            "removed_but_mapped": sorted(set(removed) & mapped),
            "unmapped_total": len(set(present) - mapped),
            "mapped_but_absent": sorted(a for a in mapped if a not in present and "#" in a),
        },
    }

    if policyengine:
        pinned_version = m.pins.get("policyengine_us")
        try:
            import importlib.metadata

            installed = importlib.metadata.version("policyengine-us")
            variables = _policyengine_variables()
        except Exception as error:  # noqa: BLE001 - absent or broken install is a reportable state
            report["policyengine"] = {"error": f"{type(error).__name__}: {error}"}
        else:
            named = {row["policyengine_variable"] for row in m.outputs if row.get("policyengine_variable")}
            # policyengine-us generates one variable per state code; they are
            # geography, not policy, and swamp every list they appear in.
            generated = {v for v in variables if len(v) == 2 and v.isupper()}
            real = variables - generated
            report["policyengine"] = {
                "pinned_version": pinned_version,
                "installed_version": installed,
                "moved": installed != pinned_version,
                "variables": len(real),
                "generated_state_variables": len(generated),
                "named_by_the_map": len(named & real),
                "unmapped": sorted(real - named),
                "named_but_absent": sorted(named - variables),
            }
    return report


def _summarise(report: dict[str, Any]) -> list[str]:
    """One line per thing a later stage should act on."""
    lines = []
    axiom = report["axiom"]
    if report["moved"]:
        lines.append(f"RuleSpec moved: pinned {report['pinned_ref'][:9]} -> checked {str(report['checked_ref'])[:9]}")
    if report["moved"]:
        lines.append(f"Axiom: {len(axiom['added'])} rules added, {len(axiom['removed'])} removed "
                     f"({len(axiom['added_and_unmapped'])} added and unmapped, "
                     f"{len(axiom['removed_but_mapped'])} removed but mapped)")
    lines.append(f"Axiom: {axiom['rules_at_ref']} rules at the ref, {axiom['mapped']} mapped, "
                 f"{axiom['unmapped_total']} unmapped, {len(axiom['mapped_but_absent'])} mapped but absent")
    pe = report.get("policyengine")
    if pe and "error" not in pe:
        moved = " (moved)" if pe["moved"] else ""
        lines.append(f"PolicyEngine {pe['installed_version']}{moved}: {pe['variables']} variables "
                     f"(+{pe['generated_state_variables']} generated state codes), "
                     f"{pe['named_by_the_map']} named by the map, {len(pe['unmapped'])} unmapped, "
                     f"{len(pe['named_but_absent'])} named but absent")
    elif pe:
        lines.append(f"PolicyEngine: {pe['error']}")
    return lines


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings detect", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--country", default="us")
    ap.add_argument("--corpus", required=True, help="a rulespec-us checkout, read with git only")
    ap.add_argument("--ref", default=None, help="the commit to check; defaults to the map's pin")
    ap.add_argument("--no-policyengine", action="store_true", help="skip the PolicyEngine side")
    ap.add_argument("--limit", type=int, default=15, help="examples to print per list")
    ap.add_argument("--out", type=Path, help="write the full JSON here")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    m = load(args.country)
    report = detect(m, args.corpus, args.ref, policyengine=not args.no_policyengine)

    if args.out:
        args.out.write_text(json.dumps(report, indent=2) + "\n")
    if args.json:
        print(json.dumps(report, indent=2))
        return 0

    for line in _summarise(report):
        print(line)
    for label, names in (
        ("Axiom rules added since the pin, and unmapped", report["axiom"]["added_and_unmapped"]),
        ("Axiom rules removed since the pin, but still mapped", report["axiom"]["removed_but_mapped"]),
        ("mapped Axiom ids absent at the ref", report["axiom"]["mapped_but_absent"]),
        ("PolicyEngine variables named by the map but absent", (report.get("policyengine") or {}).get("named_but_absent", [])),
    ):
        if not names:
            continue
        print(f"\n{label} ({len(names)}):")
        for name in names[: args.limit]:
            print(f"  {name}")
        if len(names) > args.limit:
            print(f"  ... {len(names) - args.limit} more")
    if args.out:
        print(f"\n{args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
