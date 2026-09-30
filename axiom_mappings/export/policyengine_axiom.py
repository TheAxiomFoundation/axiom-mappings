"""policyengine-axiom binding manifests (``policyengine-axiom-manifest/0``) from the ``cut`` bindings.

    python -m axiom_mappings.export.policyengine_axiom --out <policyengine-axiom>/manifests

A manifest is the binding set plus what programs.yaml records once for every consumer: the
consumer's name for the program, the corpus commit and the effective window. Presumed constants
become ``{"const": value}`` slots, and ``notes`` (``slot_notes`` among the slots) become the
manifest's ``_``-prefixed keys.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .. import Mappings, load
from ..bindings import BindingSet, expand
from ..corpus import module_path

CONSUMER = "policyengine-axiom"
FORMAT = "policyengine-axiom-manifest/0"
SHORT_SHA = 9  # policyengine-axiom records git's short commit


def _notes(doc: dict[str, Any]) -> dict[str, Any]:
    return {f"_{k}": v for k, v in (doc.get("notes") or {}).items()}


def _slots(block: dict[str, Any]) -> dict[str, Any]:
    """The manifest ``inputs`` dict: specs, constants, and any notes kept among the slots."""
    return expand(block) | {f"_{k}": v for k, v in (block.get("slot_notes") or {}).items()}


def manifest(m: Mappings, bs: BindingSet) -> dict[str, Any]:
    program = m.programs[bs.program]
    doc = bs.doc
    out: dict[str, Any] = {
        "format": FORMAT,
        "program": program["consumers"][CONSUMER],
        "source": doc.get("source_path") or module_path(program["axiom"]),
    }
    if "composed" in doc:
        out["composed"] = doc["composed"]
    out["corpus_ref"] = (program.get("corpus_ref") or m.pins["rulespec_us"])[:SHORT_SHA]
    out["root_entity"] = doc["root_entity"]
    out["entity_map"] = dict(doc["entity_map"])
    for key in ("project", "alias"):
        if key in (doc.get("parameters") or {}):
            out[f"{key}_parameters"] = doc["parameters"][key]
    out.update(_notes(doc))
    if "scope" in doc:
        out["scope"] = doc["scope"]
    if program.get("window"):
        out["effective"] = {"from": program["window"][0], "to": program["window"][1]}
    if "inputs" in doc or "presumed" in doc or "slot_notes" in doc:
        out["inputs"] = _slots(doc)
    if "relations" in doc:
        out["relations"] = {}
        for name, rel in doc["relations"].items():
            r: dict[str, Any] = {}
            if "filter" in rel:
                r["filter"] = rel["filter"]
            r["inputs"] = _slots(rel)
            out["relations"][name] = r
    out["bindings"] = []
    for b in doc.get("bindings") or []:
        row = {"pe_variable": b["pe_variable"], "axiom_output": b["axiom_output"], "status": b["status"]}
        if "inputs" in b or "presumed" in b or "slot_notes" in b:
            row["inputs"] = _slots(b)
        for key in ("parameters", "reduce"):
            if key in b:
                row[key] = b[key]
        row.update(_notes(b))
        out["bindings"].append(row)
    if "expose" in doc:
        out["expose"] = list(doc["expose"])
    return out


def manifests(m: Mappings) -> dict[str, dict[str, Any]]:
    """The consumer's program name -> manifest, for every cut binding set policyengine-axiom reads."""
    out = {}
    for (program, profile), bs in sorted(m.bindings.items()):
        if profile == "cut" and CONSUMER in (bs.doc.get("consumers") or []):
            man = manifest(m, bs)
            out[man["program"]] = man
    return out


def write(m: Mappings, directory: Path) -> list[Path]:
    """Write every manifest as ``<program>.json``, replacing a file that already holds that program."""
    directory.mkdir(parents=True, exist_ok=True)
    existing = {}
    for path in directory.glob("*.json"):
        try:
            existing[json.loads(path.read_text()).get("program")] = path
        except ValueError:
            continue
    written = []
    for name, man in manifests(m).items():
        path = existing.get(name, directory / f"{name}.json")
        path.write_text(json.dumps(man, indent=2, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings.export.policyengine_axiom")
    ap.add_argument("--country", default="us")
    ap.add_argument("--out", type=Path, required=True, help="directory for the manifests (policyengine-axiom/manifests)")
    args = ap.parse_args(argv)
    for path in write(load(args.country), args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
