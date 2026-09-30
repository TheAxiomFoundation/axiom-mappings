"""The shared map between Axiom RuleSpec concepts and PolicyEngine variables and parameters.

``load("us")`` returns every table for one country, the disagreement taxonomy, and the release id
(a hash of all of it). Consumers (axiom-oracles, policyengine-axiom, axiom-api) read this instead of
keeping their own copy, and cite ``release`` in every report.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent  # data/, schema/ and pins.yaml ship inside the package
DATA = ROOT / "data"
SCHEMA = ROOT / "schema"
TABLES = ("concepts", "inputs", "outputs", "parameters", "presumptions", "supplied_parameters", "programs", "findings", "prefixes",
          "unresolved", "renames")


def _yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text()) if path.exists() else None


def _read(country: str, table: str, root: Path) -> list[dict[str, Any]]:
    return (_yaml(root / "data" / country / f"{table}.yaml") or {}).get(table, [])


def _unresolved_ref(country: str, root: Path) -> str | None:
    return (_yaml(root / "data" / country / "unresolved.yaml") or {}).get("ref")


def matches(rule: dict[str, Any], slot: str) -> bool:
    kind, value = rule["match"]["kind"], rule["match"]["value"]
    if kind == "exact":
        return slot == value
    if kind == "suffix":
        return slot.endswith(value)
    return value in slot


def release_id(country: str, root: Path | None = None) -> str:
    """``<country>-<sha12>`` over every table, the taxonomy and the pins: the map a report used."""
    root = Path(root) if root is not None else ROOT
    content = {t: _read(country, t, root) for t in TABLES}
    content["unresolved_ref"] = _unresolved_ref(country, root)
    content["taxonomy"] = _yaml(root / "data" / "taxonomy.yaml")
    content["pins"] = (_yaml(root / "pins.yaml") or {}).get(country)
    digest = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    return f"{country}-{digest[:12]}"


@dataclass(frozen=True)
class Mappings:
    country: str
    release: str
    concepts: dict[str, dict[str, Any]]
    inputs: tuple[dict[str, Any], ...]  # sorted by priority, lowest first
    outputs: tuple[dict[str, Any], ...]
    parameters: tuple[dict[str, Any], ...]
    presumptions: dict[str, dict[str, Any]] = field(default_factory=dict)
    supplied_parameters: tuple[dict[str, Any], ...] = ()
    programs: dict[str, dict[str, Any]] = field(default_factory=dict)
    findings: tuple[dict[str, Any], ...] = ()
    prefixes: tuple[dict[str, Any], ...] = ()
    taxonomy: dict[str, Any] = field(default_factory=dict)
    pins: dict[str, Any] = field(default_factory=dict)
    unresolved: dict[str, dict[str, Any]] = field(default_factory=dict)  # Axiom ids known not to resolve (a ratchet)
    unresolved_ref: str | None = None  # the rulespec-us commit that list was taken at
    renames: tuple[dict[str, Any], ...] = ()  # reviewed Axiom id renames, oldest first

    def current_id(self, axiom_id: str) -> str:
        """The id the map uses now for ``axiom_id``, following reviewed renames."""
        for r in self.renames:
            if r["from"] == axiom_id:
                axiom_id = r["to"]
        return axiom_id

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

    def supplied_for(self, program: str) -> list[dict[str, Any]]:
        return [s for s in self.supplied_parameters if s["program"] == program]

    def program_for(self, consumer: str, name: str) -> dict[str, Any] | None:
        """The shared program entry a consumer calls ``name`` (e.g. policyengine-axiom's manifest name)."""
        return next((p for p in self.programs.values() if p.get("consumers", {}).get(consumer) == name), None)

    def known_finding(self, program: str, variable: str, counterpart: str = "policyengine") -> dict[str, Any] | None:
        """The recorded classification of a disagreement, if it has been filed."""
        return next((f for f in self.findings if (f["program"], f["variable"], f["counterpart"]) == (program, variable, counterpart)), None)

    def cause_of(self, program: str, variable: str, counterpart: str = "policyengine") -> str:
        found = self.known_finding(program, variable, counterpart)
        return found["cause"] if found else "unclassified"

    @property
    def causes(self) -> set[str]:
        return {c["id"] for c in self.taxonomy.get("causes", [])}

    def classify(self, vocabulary: str, value: str) -> str:
        """Map a consumer's own reason code onto a shared cause; unknown codes are unclassified."""
        return self.taxonomy.get("crosswalk", {}).get(vocabulary, {}).get(value, "unclassified")


def load(country: str = "us", root: Path | str | None = None) -> Mappings:
    root = Path(root) if root is not None else ROOT
    return Mappings(
        country=country,
        release=release_id(country, root),
        concepts={c["id"]: c for c in _read(country, "concepts", root)},
        inputs=tuple(sorted(_read(country, "inputs", root), key=lambda r: r["priority"])),
        outputs=tuple(_read(country, "outputs", root)),
        parameters=tuple(_read(country, "parameters", root)),
        presumptions={p["id"]: p for p in _read(country, "presumptions", root)},
        supplied_parameters=tuple(_read(country, "supplied_parameters", root)),
        programs={p["id"]: p for p in _read(country, "programs", root)},
        findings=tuple(_read(country, "findings", root)),
        prefixes=tuple(_read(country, "prefixes", root)),
        taxonomy=_yaml(root / "data" / "taxonomy.yaml") or {},
        pins=(_yaml(root / "pins.yaml") or {}).get(country, {}),
        unresolved={u["axiom"]: u for u in _read(country, "unresolved", root)},
        unresolved_ref=_unresolved_ref(country, root),
        renames=tuple(_read(country, "renames", root)),
    )


__all__ = ["Mappings", "load", "matches", "release_id", "TABLES", "DATA", "SCHEMA"]
