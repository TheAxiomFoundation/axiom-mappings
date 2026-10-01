"""axiom-oracles' populace input table (``axiom_oracles/data/populace_input_mapping.yaml``) from inputs.yaml.

    python -m axiom_mappings export oracles-populace --out <axiom-oracles>/axiom_oracles/data/populace_input_mapping.yaml

The global slot rules are oracles' own table, seeded from it: concepts go back to oracles' Case fact
names, file order comes from ``priority``, and the rationale kept as ``comment`` / ``comment_inside``
is written back as comments. Fields only the map has (ids, presumptions) are left out. The file stays
where it is in oracles, so the dispositions that cite it keep resolving.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import yaml

from .. import Mappings, load

AXIOM_ONLY = {"presumption"}
WIDTH = 100
HEADER = """\
# Populace-to-input mapping table: GENERATED from TheAxiomFoundation/axiom-mappings
# (axiom_mappings/data/us/inputs.yaml, release {release}). Edit there, then regenerate with
#   python -m axiom_mappings export oracles-populace --out axiom_oracles/data/populace_input_mapping.yaml
#
# Each entry declares how one input slot of a compiled program is populated from population facts on a
# Case. The generic projector (axiom_oracles.adapters.axiom.generic_inputs) reads this table, takes the
# first entry whose match fits a slot (match.kind exact | suffix | substring), and fills it from a Case
# fact (source.kind fact), a constant, or a named transform of facts (source.kind derived). What each
# transform computes, and which embed law that belongs in RuleSpec, is recorded in axiom-mappings'
# data/us/transforms.yaml.

"""


def _fact(m: Mappings, concept_id: str) -> str:
    return m.concepts[concept_id]["name"].upper()  # oracles' Concepts attribute name


def oracles_rule(m: Mappings, rule: dict[str, Any]) -> dict[str, Any]:
    src = {k: v for k, v in rule["source"].items() if k not in AXIOM_ONLY}
    kind = src.pop("kind")
    if kind == "concept":
        out = {"kind": "fact", "name": _fact(m, src.pop("concept")), **src}
    elif kind == "derived":
        out = {"kind": "derived"}
        for key, value in src.items():  # in place, so oracles' key order holds
            if key == "from_concepts":
                out["from_facts"] = [_fact(m, f) if isinstance(f, str) else
                                     {"fact": _fact(m, f["concept"]), **{k: v for k, v in f.items() if k != "concept"}}
                                     for f in value]
            elif key == "zero_concepts":
                out["zero_facts"] = [_fact(m, f) for f in value]
            else:
                out[key] = value
    else:
        out = {"kind": "constant", **src}
    return {"match": dict(rule["match"]), "scope": rule["scope"], "source": out}


def populace_table(m: Mappings) -> dict[str, list[dict[str, Any]]]:
    return {"mappings": [oracles_rule(m, r) for r in sorted(m.inputs, key=lambda r: r["priority"])]}


def _comment(text: str, indent: int) -> str:
    return "".join(" " * indent + ("# " + line if line else "#") + "\n" for line in text.split("\n"))


def _scalar(value: Any) -> str:
    return yaml.safe_dump(value, default_flow_style=True, width=10**6).strip().removesuffix("\n...").removesuffix("...").strip()


def _flow(value: Any) -> str:
    """A value inline, in oracles' style: ``{ key: value }`` and ``[a, b]``."""
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{_scalar(k)}: {_flow(v)}" for k, v in value.items()) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_flow(v) for v in value) + "]"
    return _scalar(value)


def _rule_lines(row: dict[str, Any]) -> list[str]:
    lines = [f"  - match: {_flow(row['match'])}", f"    scope: {row['scope']}"]
    src = row["source"]
    inline = f"    source: {_flow(src)}"
    if src["kind"] in ("constant", "fact") and all(not isinstance(v, (dict, list)) for v in src.values()) and len(inline) <= WIDTH:
        return lines + [inline]
    lines.append("    source:")
    for key, value in src.items():
        if isinstance(value, list):
            flat = f"      {key}: {_flow(value)}"
            if all(not isinstance(v, (dict, list)) for v in value) and len(flat) <= WIDTH:
                lines.append(flat)
            else:
                lines.append(f"      {key}:")
                lines += [f"        - {_flow(v)}" for v in value]
        else:
            lines.append(f"      {key}: {_flow(value)}")
    return lines


def render(m: Mappings) -> str:
    """The table as oracles lays it out: one entry per rule, rationale comments where they were."""
    parts = [HEADER.format(release=m.release), "mappings:\n"]
    for i, (rule, row) in enumerate(zip(sorted(m.inputs, key=lambda r: r["priority"]), populace_table(m)["mappings"])):
        lines = _rule_lines(row)
        if i and not rule.get("comment"):
            parts.append("\n")  # one blank line between entries
        if rule.get("comment_inside"):  # back above the line it annotated, else just under the match
            depth = 8 if (rule.get("comment_inside_above") or "").startswith("- ") else 6
            at = next((i for i, line in enumerate(lines) if line.strip() == rule.get("comment_inside_above")), 1)
            if at == 1:
                depth = 4
            lines[at:at] = _comment(rule["comment_inside"], depth).rstrip("\n").split("\n")
        if rule.get("comment"):
            parts.append("\n" + _comment(rule["comment"], 2))
        parts.append("\n".join(lines) + "\n")
    return "".join(parts)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings export oracles-populace")
    ap.add_argument("--country", default="us")
    ap.add_argument("--out", type=Path, required=True, help="the populace_input_mapping.yaml to write")
    args = ap.parse_args(argv)
    args.out.write_text(render(load(args.country)))
    print(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
