"""Propose bindings for a program's unbound input slots.

    python -m axiom_mappings propose slots --program ID --catalog SLOTS.json [--profile cut]
                                           [--root-entity E --entity-map Axiom=pe,...] [--evaluate]

Three identities admit a candidate, strongest first:

    binding    another program's reviewed binding set (same profile) binds a slot of this name
    slot-rule  the shared slot rules (inputs.yaml) map the slot to a concept, and the concept names a
               PolicyEngine variable on the slot's entity; or give it a constant under a presumption
    name       a PolicyEngine variable of the same name (for a relation slot, also without ``member_``)
               sits on the slot's entity

A ``facts`` binding may only read PolicyEngine inputs. Slot rules that derive a value in harness code
(``derived``) are not proposed: that is law outside RuleSpec. ``--evaluate`` blanks every reviewed
slot of every binding set in turn and reports how often the first candidate is what a reviewer bound.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from .. import Mappings
from ..bindings import BindingSet, canonical, expand

PERSON = "person"


def _reviewed_specs(m: Mappings, profile: str, exclude: str | None) -> dict[tuple[str, str], list[tuple[Any, str]]]:
    """(block kind, slot) -> [(spec, the binding set it comes from)] across reviewed binding sets."""
    out: dict[tuple[str, str], list[tuple[Any, str]]] = {}
    for (program, prof), bs in m.bindings.items():
        if prof != profile or program == exclude:
            continue
        for where, block in bs.blocks:
            kind = "relation" if where.startswith("relation ") else "root"
            for slot, spec in expand(block).items():
                presumption = next((p for p, s in (block.get("presumed") or {}).items() if slot in (s or {})), None)
                out.setdefault((kind, slot), []).append(({"presumed": presumption, "value": spec["const"]}
                                                         if presumption else spec, program))
    return out


def _is_input(system, name: str) -> bool:
    v = system.variables[name]
    return not (v.formulas or getattr(v, "adds", None) or getattr(v, "subtracts", None))


def candidates(m: Mappings, system, slot: str, kind: str, entity: str, profile: str,
               reviewed: dict, limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def admit(spec: Any, identity: str, evidence: str) -> None:
        key = canonical(spec)
        if key in seen:
            return
        if system is not None and isinstance(spec, str):
            v = system.variables.get(spec)
            if v is None or v.entity.key != entity or (profile == "facts" and not _is_input(system, spec)):
                return
        seen.add(key)
        out.append({"spec": spec, "identity": identity, "evidence": evidence})

    for spec, program in sorted(reviewed.get((kind, slot), []), key=lambda t: t[1]):
        admit(spec, "binding", f"bound this way in {program}.{profile}")
    for rule in m.rules_for_slot(slot):
        src = rule["source"]
        if src["kind"] == "concept":
            var = m.policyengine_variable(src["concept"], entity)
            if var:
                admit(var, "slot-rule", f"slot rule {rule['id']} -> concept {src['concept']} -> {var} on {entity}")
        elif src["kind"] == "constant" and src.get("presumption"):
            admit({"presumed": src["presumption"], "value": src["value"]}, "slot-rule",
                  f"slot rule {rule['id']}: constant under {src['presumption']}")
    if system is not None:
        for name in dict.fromkeys([slot, slot.removeprefix("member_")] if kind == "relation" else [slot]):
            if name in system.variables:
                admit(name, "name", f"PolicyEngine variable {name} on {entity}")
    return out[:limit]


def propose(m: Mappings, system, program: str, catalog: dict[str, Any], *, profile: str = "cut",
            root_entity: str | None = None, entity_map: dict[str, str] | None = None, limit: int = 3) -> dict[str, Any]:
    bs: BindingSet | None = m.binding_set(program, profile)
    doc = bs.doc if bs else {}
    root_entity = doc.get("root_entity") or root_entity
    entity_map = doc.get("entity_map") or entity_map or {}
    if root_entity not in entity_map:
        raise ValueError(f"{program}.{profile}: give --root-entity and --entity-map (no binding set to read them from)")
    root = entity_map[root_entity]
    bound_root = set(bs.slots()) if bs else set()
    reviewed = _reviewed_specs(m, profile, exclude=program)
    blocks = [("inputs", "root", root, catalog.get("root_inputs", []), bound_root)]
    for name, slots in (catalog.get("relations") or {}).items():
        rel = (doc.get("relations") or {}).get(name)
        blocks.append((f"relation {name}", "relation", PERSON, slots, set(expand(rel)) if rel else set()))
    proposals, unmatched = [], []
    for where, kind, entity, slots, bound in blocks:
        for slot in slots:
            if slot in bound:
                continue
            found = candidates(m, system, slot, kind, entity, profile, reviewed, limit)
            if found:
                proposals.append({"block": where, "slot": slot, "entity": entity, "candidates": found, "accept": None})
            else:
                unmatched.append({"block": where, "slot": slot, "entity": entity})
    return {"generator": "axiom_mappings.propose.slots", "program": program, "profile": profile, "map_release": m.release,
            "root_entity": root_entity, "entity_map": entity_map, "proposed": len(proposals),
            "unmatched": unmatched, "proposals": proposals}


def evaluate(m: Mappings, system, *, profile: str = "cut") -> dict[str, Any]:
    """Blank each reviewed slot in turn: is the first candidate what the reviewer bound?"""
    outcome: Counter = Counter()
    disagreements = []
    for (program, prof), bs in sorted(m.bindings.items()):
        if prof != profile:
            continue
        reviewed = _reviewed_specs(m, profile, exclude=program)
        root = bs.doc["entity_map"][bs.doc["root_entity"]]
        for where, block in bs.blocks:
            kind, entity = ("relation", PERSON) if where.startswith("relation ") else ("root", root)
            presumed = {s: p for p, slots in (block.get("presumed") or {}).items() for s in (slots or {})}
            for slot, spec in expand(block).items():
                truth = {"presumed": presumed[slot], "value": spec["const"]} if slot in presumed else spec
                found = candidates(m, system, slot, kind, entity, profile, reviewed)
                label = "presumed" if slot in presumed else "mapped"
                if not found:
                    outcome[f"{label}: no candidate"] += 1
                elif canonical(found[0]["spec"]) == canonical(truth):
                    outcome[f"{label}: first candidate agrees"] += 1
                elif any(canonical(c["spec"]) == canonical(truth) for c in found):
                    outcome[f"{label}: a later candidate agrees"] += 1
                else:
                    outcome[f"{label}: candidates differ"] += 1
                    disagreements.append({"program": program, "block": where, "slot": slot, "reviewed": truth,
                                          "first": found[0]["spec"], "identity": found[0]["identity"]})
    return {"outcome": dict(outcome), "disagreements": disagreements}
