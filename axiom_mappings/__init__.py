"""The shared map between Axiom RuleSpec concepts and PolicyEngine variables and parameters.

``load("us")`` returns the concepts (shared input facts), the input slot rules, the output
registry and the parameter registry for one country, plus the pins they were checked against.
Consumers (axiom-oracles, policyengine-axiom, axiom-api) read this instead of keeping their own copy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SCHEMA = ROOT / "schema"
TABLES = ("concepts", "inputs", "outputs", "parameters")


def _read(country: str, table: str, root: Path) -> list[dict[str, Any]]:
    path = root / "data" / country / f"{table}.yaml"
    return (yaml.safe_load(path.read_text()) or {}).get(table, []) if path.exists() else []


def matches(rule: dict[str, Any], slot: str) -> bool:
    kind, value = rule["match"]["kind"], rule["match"]["value"]
    if kind == "exact":
        return slot == value
    if kind == "suffix":
        return slot.endswith(value)
    return value in slot


@dataclass(frozen=True)
class Mappings:
    country: str
    concepts: dict[str, dict[str, Any]]
    inputs: tuple[dict[str, Any], ...]  # sorted by priority, lowest first
    outputs: tuple[dict[str, Any], ...]
    parameters: tuple[dict[str, Any], ...]
    pins: dict[str, Any] = field(default_factory=dict)

    def rules_for_slot(self, slot: str) -> list[dict[str, Any]]:
        """Every rule matching ``slot``, winner first."""
        return [r for r in self.inputs if matches(r, slot)]

    def rule_for_slot(self, slot: str) -> dict[str, Any] | None:
        rules = self.rules_for_slot(slot)
        return rules[0] if rules else None

    def policyengine_variable(self, concept_id: str, entity: str) -> str | None:
        for pe in self.concepts[concept_id]["policyengine"]:
            if pe["entity"] == entity:
                return pe["variable"]
        return None

    def output(self, axiom_id: str) -> dict[str, Any] | None:
        return next((o for o in self.outputs if o["axiom"] == axiom_id), None)

    def parameter(self, axiom_id: str) -> dict[str, Any] | None:
        return next((p for p in self.parameters if p["axiom"] == axiom_id), None)

    def outputs_for_policyengine(self, variable: str) -> list[dict[str, Any]]:
        return [o for o in self.outputs if o.get("policyengine_variable") == variable and o["type"] != "not_comparable"]


def load(country: str = "us", root: Path | str | None = None) -> Mappings:
    root = Path(root) if root is not None else ROOT
    pins_path = root / "pins.yaml"
    pins = (yaml.safe_load(pins_path.read_text()) or {}).get(country, {}) if pins_path.exists() else {}
    return Mappings(
        country=country,
        concepts={c["id"]: c for c in _read(country, "concepts", root)},
        inputs=tuple(sorted(_read(country, "inputs", root), key=lambda r: r["priority"])),
        outputs=tuple(_read(country, "outputs", root)),
        parameters=tuple(_read(country, "parameters", root)),
        pins=pins,
    )


__all__ = ["Mappings", "load", "matches", "TABLES", "DATA", "SCHEMA"]
