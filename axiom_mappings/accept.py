"""Accept reviewed proposals into the map.

    python -m axiom_mappings accept proposals/<kind>/<name>.yaml

A reviewer accepts a proposal by setting its ``accept:`` to a candidate's index (0 is the first), after
checking the evidence and fixing ``program`` / ``rationale`` or, for a table, the key. Only accepted
proposals are written, and they leave the proposals file; the rest stay for later.

- parameters and outputs: a row appended to parameters.yaml / outputs.yaml, carrying the rationale;
- slots: the spec added to the program's binding set (created from the proposal's root entity and
  entity map if the program has none yet), as an input or under its presumption.

Run ``python -m axiom_mappings validate`` afterwards: acceptance is a review decision, the validator
still checks what it wrote.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import yaml

from . import ROOT
from .bindings import dump

PLACEHOLDER = "<"


def _append(table: str, rows: list[dict[str, Any]], country: str, root: Path) -> None:
    path = root / "data" / country / f"{table}.yaml"
    text = path.read_text().rstrip("\n") + "\n"
    path.write_text(text + yaml.safe_dump(rows, sort_keys=False, width=110, allow_unicode=True))


def _parameter_row(p: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    row = {"axiom": p["axiom"], "program": p["program"], "policyengine_parameter": c["policyengine_parameter"]}
    for key in ("parameter_key", "parameter_keys", "parameter_key_path", "parameter_key_input", "parameter_key_map", "period"):
        if key in c:
            if isinstance(c[key], str) and c[key].startswith(PLACEHOLDER):
                raise ValueError(f"{p['axiom']}: replace the placeholder {key} before accepting")
            row[key] = c[key]
    if c.get("unit") == "money":
        row["unit"], row["comparison"] = "USD", "money"
    elif c.get("unit") == "rate":
        row["comparison"] = "rate"
    row["rationale"] = p["rationale"]
    return row


def _slot(doc: dict[str, Any], proposal: dict[str, Any], spec: Any) -> None:
    if doc.get("profile") == "facts":  # axiom-api's grammar: flat inputs and ordered presumptions
        if isinstance(spec, dict) and "presumed" in spec:
            doc.setdefault("presumptions", {})[proposal["slot"]] = {"value": spec["value"], "presumption": spec["presumed"]}
        else:
            doc.setdefault("inputs", {})[proposal["slot"]] = spec
        return
    where = proposal["block"]
    block = doc if where == "inputs" else doc.setdefault("relations", {}).setdefault(where.removeprefix("relation "), {})
    slot = proposal["slot"]
    if isinstance(spec, dict) and "presumed" in spec:
        block.setdefault("presumed", {}).setdefault(spec["presumed"], {})[slot] = spec["value"]
    else:
        block.setdefault("inputs", {})[slot] = spec


def _update_finding(text: str, program: str, variable: str, candidate: dict[str, Any]) -> str:
    """Rewrite one finding's entry in findings.yaml (its cause, a note of the mechanism, and where the
    investigation is recorded); every other line, comments included, stays as it is."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.rstrip("\n") == f"- program: {program}"
                  and i + 1 < len(lines) and lines[i + 1].strip() in (f"variable: {variable}", f"variable: '{variable}'")), None)
    if start is None:
        raise KeyError(f"no finding {program}:{variable} in findings.yaml")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith(("- ", "#"))), len(lines))
    entry = yaml.safe_load("".join(lines[start:end]))[0]
    entry["cause"] = candidate["cause"]
    note = f"Investigated ({candidate.get('model')}): {candidate['mechanism']}"
    entry["note"] = f"{entry['note']} {note}" if entry.get("note") else note
    entry.setdefault("evidence", {})["investigation"] = {
        k: candidate[k] for k in ("investigation", "model", "confidence", "arithmetic", "citations") if k in candidate}
    lines[start:end] = [yaml.safe_dump([entry], sort_keys=False, width=110, allow_unicode=True)]
    return "".join(lines)


def accept(path: Path, *, country: str = "us", root: Path | None = None) -> list[str]:
    root = Path(root or ROOT)
    raw = path.read_text()
    header = "".join(line for line in raw.splitlines(keepends=True) if line.startswith("#"))
    doc = yaml.safe_load(raw)
    kind = doc.get("kind") or doc["generator"].rsplit(".", 1)[-1]  # another tool's proposals name their kind
    chosen = [(p, p["candidates"][p["accept"]]) for p in doc["proposals"] if p.get("accept") is not None]
    if not chosen:
        return []
    written = []
    if kind in ("parameters", "outputs"):
        rows = []
        for p, c in chosen:
            if not p.get("program"):
                raise ValueError(f"{p['axiom']}: set `program` (the registry program, e.g. snap or tax) before accepting")
            if not p.get("rationale"):
                raise ValueError(f"{p['axiom']}: a rationale is required")
            rows.append(_parameter_row(p, c) if kind == "parameters" else
                        {"axiom": p["axiom"], "program": p["program"], **c["row"], "rationale": p["rationale"]})
            written.append(p["axiom"])
        _append(kind, rows, country, root)
    elif kind == "slots":
        target = root / "data" / country / "bindings" / f"{doc['program']}.{doc['profile']}.yaml"
        if target.exists():
            text = target.read_text()
            file_header = "".join(line for line in text.splitlines(keepends=True) if line.startswith("#"))
            binding = yaml.safe_load(text)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            file_header = f"# {doc['profile']} bindings for {doc['program']}, started from accepted slot proposals.\n"
            binding = ({"program": doc["program"], "profile": "facts", "consumers": [], "format": "axiom-pe-variable-mapping/1"}
                       if doc["profile"] == "facts" else
                       {"program": doc["program"], "profile": doc["profile"], "consumers": [],
                        "root_entity": doc["root_entity"], "entity_map": doc["entity_map"], "bindings": []})
        for p, c in chosen:
            _slot(binding, p, c["spec"])
            written.append(f"{p['block']} {p['slot']}")
        target.write_text(file_header + dump(binding))
    elif kind == "findings":
        path_findings = root / "data" / country / "findings.yaml"
        text = path_findings.read_text()
        for p, c in chosen:
            text = _update_finding(text, p["program"], p["variable"], c)
            written.append(f"{p['program']}:{p['variable']}")
        path_findings.write_text(text)
    else:
        raise ValueError(f"unknown proposal kind {kind!r}")
    doc["proposals"] = [p for p in doc["proposals"] if p.get("accept") is None]
    path.write_text(header + yaml.safe_dump(doc, sort_keys=False, width=110, allow_unicode=True))
    return written


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__)
        return 2
    for name in args:
        for item in accept(Path(name)):
            print(f"accepted {item}")
    return 0
