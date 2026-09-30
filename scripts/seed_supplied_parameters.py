"""Seed data/us/supplied_parameters.yaml from the parameter rules oracles' tax projection injects.

    python scripts/seed_supplied_parameters.py --oracles ~/axiom-oracles --ref origin/main

Parses ``axiom_oracles/adapters/axiom/tax_projection.py`` with ``ast`` and records every
``_generated_parameter_rule(...)`` call with literal arguments. A call with a non-literal argument
is recorded with that argument's source text, so nothing is dropped silently.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent / "axiom_mappings"
PATH = "axiom_oracles/adapters/axiom/tax_projection.py"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--oracles", default=str(Path.home() / "axiom-oracles"))
    ap.add_argument("--ref", default="origin/main")
    args = ap.parse_args()
    commit = subprocess.check_output(["git", "-C", args.oracles, "rev-parse", args.ref], text=True).strip()
    source = subprocess.check_output(["git", "-C", args.oracles, "show", f"{commit}:{PATH}"], text=True)
    tree = ast.parse(source)

    rows = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "_generated_parameter_rule"):
            continue
        def value(n):
            try:
                return ast.literal_eval(n)
            except ValueError:
                return {"expression": ast.get_source_segment(source, n)}
        name = value(node.args[0]) if node.args else None
        kwargs = {k.arg: value(k.value) for k in node.keywords}
        formula = kwargs.get("formula")
        try:
            literal = ast.literal_eval(formula) if isinstance(formula, str) else formula
        except (ValueError, SyntaxError):
            literal = formula
        rows.append({
            "program": "us/federal-income-tax",
            "name": name,
            "value": literal,
            "effective_from": "2026-01-01",
            **({"unit": kwargs["unit"]} if kwargs.get("unit") else {}),
            "dtype": kwargs.get("dtype"),
            "source": kwargs.get("source"),
            "supplied_by": f"axiom-oracles@{commit[:12]} {PATH}:{node.lineno}",
            "reason": "encoding-gap",
        })
    rows.sort(key=lambda r: (str(r["name"]), r["supplied_by"]))
    header = ("# Parameter values a harness supplies because the Axiom encoding lacks them. Under D48 these are\n"
              "# not Axiom's truth: a program is not ready while it depends on any. Each should be encoded upstream\n"
              f"# and removed. Seeded from axiom-oracles@{commit[:12]} by scripts/seed_supplied_parameters.py.\n\n")
    (ROOT / "data" / "us" / "supplied_parameters.yaml").write_text(
        header + yaml.safe_dump({"supplied_parameters": rows}, sort_keys=False, allow_unicode=True, width=100))
    print(f"{len(rows)} supplied parameters from {commit[:12]}")


if __name__ == "__main__":
    main()
