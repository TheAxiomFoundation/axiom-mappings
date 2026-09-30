"""Import axiom-api's serving maps as ``facts`` bindings (a one-off, then edit here).

    python scripts/import_serving_maps.py ~/axiom-api [--ref origin/main]

Each ``data/serving-map/<name>.json`` at the ref (read from git, so the checkout is untouched) becomes
``data/us/bindings/<program>.facts.yaml``, in axiom-api's own grammar. Each presumption keeps its
place (axiom-api discloses them in order) and is tagged with the presumption that states why it holds;
one no rule covers stops the import.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axiom_mappings import ROOT, load  # noqa: E402
from axiom_mappings.bindings import dump  # noqa: E402

CLAIMED = {"dependent_care_expense_necessary_for_work_or_training"}  # switches a deduction on (D43)
SCOPE = {"is_individual"}


def presumption(slot: str, value: Any) -> str:
    if slot in CLAIMED:
        return "claimed-expense-qualifies"
    if slot in SCOPE:
        return "population-scope"
    if value is True:
        return "procedural-eligibility"  # citizenship, residency, work registration and compliance
    if value is False or value == 0:
        return "not-in-data"
    raise SystemExit(f"{slot} = {value!r}: no rule gives this presumption a reason; decide one")


def binding(m, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    program = m.program_for("axiom-api", raw["program"])
    if program is None:
        raise SystemExit(f"{raw['program']}: no program in programs.yaml names it for axiom-api")
    doc: dict[str, Any] = {"program": program["id"], "profile": "facts", "consumers": ["axiom-api"]}
    for key in ("format", "description", "targets", "variables", "inputs"):
        if key in raw:
            doc[key] = raw[key]
    if "presumptions" in raw:
        doc["presumptions"] = {slot: {"value": value, "presumption": presumption(slot, value)}
                               for slot, value in raw["presumptions"].items()}
    for key in ("evidence", "overrides"):
        if key in raw:
            doc[key] = raw[key]
    unknown = set(raw) - {"program", "format", "description", "targets", "variables", "inputs", "presumptions", "evidence", "overrides"}
    if unknown:
        raise SystemExit(f"{raw['program']}: keys this importer does not know: {sorted(unknown)}")
    return program["id"], doc


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", type=Path)
    ap.add_argument("--ref", default="origin/main")
    args = ap.parse_args(argv)
    git = ["git", "-C", str(args.repo.expanduser())]
    sha = subprocess.check_output(git + ["rev-parse", "--short", args.ref], text=True).strip()
    names = subprocess.check_output(git + ["ls-tree", "--name-only", args.ref, "data/serving-map/"], text=True).split()
    m = load("us")
    for name in names:
        raw = json.loads(subprocess.check_output(git + ["show", f"{args.ref}:{name}"], text=True))
        program_id, doc = binding(m, raw)
        out = ROOT / "data" / "us" / "bindings" / f"{program_id}.facts.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        header = (f"# axiom-api bindings for {program_id} (facts profile: read from a request, in axiom-api's serving-map\n"
                  f"# grammar). Imported from axiom-api {name} at {sha}; edit here, then export with\n"
                  "# python -m axiom_mappings export axiom-api --out <axiom-api>/data/serving-map\n")
        out.write_text(header + dump(doc))
        print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
