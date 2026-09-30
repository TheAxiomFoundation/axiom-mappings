"""Export the map in the formats its consumers already read, so they can switch without other changes.

``oracles_registry(m)`` rebuilds axiom-oracles' ``bridges/mappings/<country>.yaml`` payload exactly
(field names, the ``country`` field, prefixes). Fields oracles does not know, such as
``entity_projection``, are left out.

``policyengine_axiom.manifests(m)`` builds policyengine-axiom's binding manifests from the ``cut``
bindings.
"""

from __future__ import annotations

from typing import Any

from .. import Mappings

AXIOM_ONLY_FIELDS = {"entity_projection", "keyed_by_prefix_string", "comment"}


def _oracles_row(row: dict[str, Any], country: str, *, parameter: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in AXIOM_ONLY_FIELDS:
            continue
        if key == "axiom":
            out["legal_id_prefix" if row.get("keyed_by_prefix_string") else "legal_id"] = value
        elif key == "type":
            out["mapping_type"] = value
        elif key == "axiom_prefix":
            out["legal_id_prefix"] = value
        else:
            out[key] = value
        if key in ("axiom", "axiom_prefix") and "country" not in out:
            out["country"] = country
    if parameter:
        out["mapping_type"] = "parameter_value"
    return out


def oracles_registry(m: Mappings) -> dict[str, list[dict[str, Any]]]:
    rows = [_oracles_row(o, m.country, parameter=False) for o in m.outputs]
    rows += [_oracles_row(p, m.country, parameter=True) for p in m.parameters]
    prefixes = [_oracles_row(x, m.country, parameter=False) for x in m.prefixes]
    return {"mappings": rows, "prefixes": prefixes}
