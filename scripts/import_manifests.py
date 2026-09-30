"""Import policyengine-axiom's binding manifests as ``cut`` bindings (a one-off, then edit here).

    python scripts/import_manifests.py ~/policyengine-axiom/manifests

Each manifest becomes ``data/us/bindings/<program id>.cut.yaml``. Constants move under the
presumption that states why they hold (``PRESUMPTION`` below); a constant no rule covers stops
the import so it gets a decision rather than a default. What programs.yaml already records (the
consumer's name, corpus commit, effective window) is checked against the manifest, not copied.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from axiom_mappings import ROOT, load  # noqa: E402
from axiom_mappings.corpus import module_path  # noqa: E402

LAW_SUPPLIED = {"federal_minimum_wage", "federal_or_state_minimum_wage"}  # 29 USC 206: law, not a data gap
DATA_PERIOD = {"child_support_payment_history_months", "income_pay_frequency"}


def presumption(slot: str, value: Any) -> str:
    """Why a constant holds. The scaffold's rule was false / 0 for facts the data lacks, 1 for divisors."""
    if slot in LAW_SUPPLIED:
        return "law-supplied"
    if slot in DATA_PERIOD:
        return "data-period"
    if isinstance(value, bool):
        return "procedural-eligibility" if value else "not-in-data"  # every true constant is a compliance fact
    if value == 0:
        return "not-in-data"
    if value == 1:
        return "neutral-divisor"
    raise SystemExit(f"{slot} = {value!r}: no rule gives this constant a presumption; decide one")


class Flow(dict):
    """A spec, written inline."""


class Dumper(yaml.SafeDumper):
    pass


Dumper.add_representer(Flow, lambda d, v: d.represent_mapping("tag:yaml.org,2002:map", v, flow_style=True))


def flow(spec: Any) -> Any:
    return Flow(spec) if isinstance(spec, dict) else spec


def split(inputs: dict[str, Any]) -> dict[str, Any]:
    """A manifest ``inputs`` dict -> ``inputs`` (specs), ``presumed`` (constants by presumption) and
    ``slot_notes`` (the ``_``-prefixed notes some manifests keep among their slots)."""
    block: dict[str, Any] = {}
    slot_notes = {k[1:]: v for k, v in inputs.items() if k.startswith("_")}
    inputs = {k: v for k, v in inputs.items() if not k.startswith("_")}
    specs = {s: flow(v) for s, v in inputs.items() if not (isinstance(v, dict) and "const" in v)}
    presumed: dict[str, dict[str, Any]] = {}
    for slot, spec in inputs.items():
        if isinstance(spec, dict) and "const" in spec:
            presumed.setdefault(presumption(slot, spec["const"]), {})[slot] = spec["const"]
    if specs:
        block["inputs"] = specs
    if presumed:
        block["presumed"] = {k: presumed[k] for k in sorted(presumed)}
    if slot_notes:
        block["slot_notes"] = slot_notes
    return block


def notes(raw: dict[str, Any]) -> dict[str, Any]:
    return {k[1:]: v for k, v in raw.items() if k.startswith("_")}


def binding_set(m, raw: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    program = m.program_for("policyengine-axiom", raw["program"])
    if program is None:
        raise SystemExit(f"{raw['program']}: no program in programs.yaml names it for policyengine-axiom")
    if not (program.get("corpus_ref") or "").startswith(raw["corpus_ref"]):
        raise SystemExit(f"{raw['program']}: corpus_ref {raw['corpus_ref']} differs from programs.yaml")
    window = [raw["effective"]["from"], raw["effective"]["to"]] if raw.get("effective") else None
    if window != program.get("window"):
        raise SystemExit(f"{raw['program']}: effective {window} differs from the program window {program.get('window')}")
    doc: dict[str, Any] = {"program": program["id"], "profile": "cut", "consumers": ["policyengine-axiom"]}
    if raw["source"] != module_path(program["axiom"]):
        doc["source_path"] = raw["source"]
    if "composed" in raw:
        doc["composed"] = raw["composed"]
    doc["root_entity"] = raw["root_entity"]
    doc["entity_map"] = raw["entity_map"]
    params = {k: raw[f"{k}_parameters"] for k in ("project", "alias") if f"{k}_parameters" in raw}
    if params:
        doc["parameters"] = params
    if "scope" in raw:
        doc["scope"] = flow(raw["scope"])
    if notes(raw):
        doc["notes"] = notes(raw)
    doc.update(split(raw.get("inputs") or {}) if "inputs" in raw else {})
    if "inputs" in raw and not raw["inputs"]:
        doc["inputs"] = {}
    if "relations" in raw:
        doc["relations"] = {}
        for name, rel in raw["relations"].items():
            r: dict[str, Any] = {}
            if "filter" in rel:
                r["filter"] = flow(rel["filter"])
            r.update(split(rel.get("inputs") or {}))
            doc["relations"][name] = r
    doc["bindings"] = []
    for b in raw["bindings"]:
        row: dict[str, Any] = {"pe_variable": b["pe_variable"], "axiom_output": b["axiom_output"], "status": b["status"]}
        row.update(split(b.get("inputs") or {}))
        for key in ("parameters", "reduce"):
            if key in b:
                row[key] = b[key]
        if notes(b):
            row["notes"] = notes(b)
        doc["bindings"].append(row)
    if "expose" in raw:
        doc["expose"] = raw["expose"]
    return program["id"], doc


def main(argv=None) -> int:
    source = Path((argv or sys.argv[1:])[0]).expanduser()
    m = load("us")
    sha = subprocess.run(["git", "-C", str(source), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    for path in sorted(source.glob("*.json")):
        raw = json.loads(path.read_text())
        program_id, doc = binding_set(m, raw)
        out = ROOT / "data" / "us" / "bindings" / f"{program_id}.cut.yaml"
        out.parent.mkdir(parents=True, exist_ok=True)
        header = (f"# policyengine-axiom bindings for {program_id} (cut profile: read from a live PolicyEngine simulation).\n"
                  f"# Imported from policyengine-axiom manifests/{path.name} at {sha or 'unknown'}; edit here, then export with\n"
                  f"# python -m axiom_mappings.export.policyengine_axiom --out <policyengine-axiom>/manifests\n")
        out.write_text(header + yaml.dump(doc, Dumper=Dumper, sort_keys=False, allow_unicode=True, width=110))
        print(out.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
