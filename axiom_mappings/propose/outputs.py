"""Propose PolicyEngine variables for Axiom outputs (derived rules) that nobody has classified yet.

    python -m axiom_mappings propose outputs --corpus <rulespec-us> [--module PREFIX] [--evaluate]

A rule already in outputs.yaml, as a mapping or as not comparable, or covered by a prefix row, has been
reviewed and gets no proposal. A candidate is admitted by identity: it cites the same law (the
variable's ``reference`` against the rule's ``source`` and module), or it names the same concept (the
names overlap by half or more, or are equal). Its value type must fit (a judgment is a boolean, money a
number) and its entity should (another entity needs an ``entity_projection``, so it only scores lower).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .. import Mappings
from ..citations import RELATIONS, of_references, of_rule, references
from ..corpus import Corpus, split_id
from . import bound, bound_to, similarity, tokens

ENTITY = {"Person": {"person"}, "TaxUnit": {"tax_unit"}, "Household": {"spm_unit", "household"}, "SPMUnit": {"spm_unit"},
          "SpmUnit": {"spm_unit"}, "Family": {"family"}, "TanfUnit": {"spm_unit"}, "MaritalUnit": {"marital_unit"}}
KIND = {"Judgment": "bool", "Boolean": "bool", "Money": "number", "Rate": "number", "Decimal": "number",
        "Integer": "number", "Count": "number", "Text": "text"}
COMPARISON = {"Judgment": "decision", "Boolean": "bool", "Money": "money", "Rate": "rate", "Count": "count", "Integer": "count"}
WEIGHT = {"binding": 4.0, "exact": 3.0, "within": 2.5, "name": 2.0, "section": 1.0}


class PEVariables:
    def __init__(self, system):
        self.variables = system.variables
        self.cites = {n: of_references(references({"reference": v.reference})) for n, v in self.variables.items()}
        self.by_key: dict[tuple, list[str]] = defaultdict(list)
        self.by_token: dict[str, list[str]] = defaultdict(list)
        for name, cites in self.cites.items():
            for key in {c.key for c in cites}:
                self.by_key[key].append(name)
            for t in tokens(name):
                self.by_token[t].append(name)

    def kind(self, name: str) -> str:
        vt = self.variables[name].value_type
        if vt is bool:
            return "bool"
        if vt is str or getattr(self.variables[name], "possible_values", None) is not None:
            return "text"
        return "number"


def _reviewed(m: Mappings) -> tuple[set[str], list[str]]:
    ids = {o["axiom"] for o in m.outputs if o.get("axiom")}
    prefixes = [p["axiom_prefix"] for p in m.prefixes if p.get("axiom_prefix")]
    prefixes += [o["axiom"] for o in m.outputs if o.get("axiom") and (o.get("keyed_by_prefix_string") or o["axiom"].endswith("#"))]
    return ids, prefixes


def candidates(pe: PEVariables, module: str, name: str, rule: dict[str, Any], limit: int = 3,
               reviewed: dict | None = None) -> tuple[list[dict[str, Any]], str | None]:
    kind = KIND.get(str(rule.get("dtype")))
    entities = ENTITY.get(str(rule.get("entity")), set())
    cites = of_rule(module, rule)
    found: dict[str, dict[str, Any]] = {}

    def admit(var: str, relation: str, evidence: list[str]) -> None:
        if var in found and WEIGHT[found[var]["identity"]] >= WEIGHT[relation]:
            return
        if kind and pe.kind(var) != kind:
            return
        overlap = round(similarity(name, var), 2)
        if relation == "section" and overlap < 0.2:
            return
        entity = pe.variables[var].entity.key
        found[var] = {"policyengine_variable": var, "identity": relation, "name_overlap": overlap,
                      "entity": entity, "entity_matches": not entities or entity in entities,
                      "period": pe.variables[var].definition_period, "evidence": evidence}

    for var, program in bound_to(reviewed, "output", module, name) if reviewed else []:
        if var in pe.variables:
            admit(var, "binding", [f"the reviewed {program} binding binds {var} to this output"])
    for key in {c.key for c in cites}:
        for var in pe.by_key.get(key, []):
            rel = min((r for a in cites for p in pe.cites[var] if (r := a.relation(p))), key=RELATIONS.index, default=None)
            if rel:
                a, p = next((a, p) for a in cites for p in pe.cites[var] if a.relation(p) == rel)
                admit(var, rel, [f"Axiom cites {a}", f"PolicyEngine cites {p}"])
    for var in {v for t in tokens(name) for v in pe.by_token.get(t, [])}:
        overlap = similarity(name, var)
        if var == name or overlap >= 0.5:
            admit(var, "name", [f"names overlap {overlap:.2f}" + (" (equal)" if var == name else "")])
    if not found:
        return [], "no PolicyEngine variable cites the same law or shares the name" if cites else "no citation this proposer reads, and no variable shares the name"
    for c in found.values():
        c["score"] = round(WEIGHT[c["identity"]] + 2 * c["name_overlap"] + (0.5 if c["entity_matches"] else -0.5), 2)
    return sorted(found.values(), key=lambda c: -c["score"])[:limit], None


def _row(module: str, name: str, rule: dict[str, Any], c: dict[str, Any]) -> dict[str, Any]:
    row = {"type": "direct_variable", "policyengine_variable": c["policyengine_variable"], "entity": c["entity"],
           "period": c["period"], "comparison": COMPARISON.get(str(rule.get("dtype")), "money")}
    if rule.get("unit") == "USD":
        row["unit"] = "USD"
    return row


def propose(m: Mappings, corpus_root, system, *, modules: str | None = None, limit: int = 3,
            pe: PEVariables | None = None, include_prefixed: bool = False) -> dict[str, Any]:
    """``include_prefixed`` also proposes for rules a prefix row classifies in bulk (prefix rows are
    "not comparable unless an exact mapping overrides"); only citation or equal-name identities then."""
    corpus = Corpus(corpus_root, m.pins.get("rulespec_us"))
    pe = pe or PEVariables(system)
    reviewed, prefixes = _reviewed(m)
    bindings = bound(m, corpus)
    wanted = [mid for mid in corpus.module_ids if modules is None or mid.startswith(modules)]
    corpus.load(wanted)
    proposals, skipped = [], Counter()
    for module in wanted:
        for name, rule in (corpus.rules(module) or {}).items():
            axiom_id = f"{module}#{name}"
            prefixed = any(axiom_id.startswith(p) for p in prefixes)
            if rule.get("kind") != "derived" or axiom_id in reviewed or (prefixed and not include_prefixed):
                continue
            found, why = candidates(pe, module, name, rule, limit, bindings)
            if prefixed:  # overriding a bulk classification needs a strong identity
                found = [c for c in found if c["identity"] in ("binding", "exact", "within") or c["name_overlap"] == 1.0]
                why = why or "classified by a prefix row, and no candidate strong enough to override it"
            if not found:
                skipped[why] += 1
                continue
            proposals.append({"axiom": axiom_id, "program": None, "source": rule.get("source"),
                              "entity": rule.get("entity"), "dtype": rule.get("dtype"), "period": rule.get("period"),
                              "candidates": [{**c, "row": _row(module, name, rule, c)} for c in found], "accept": None,
                              "rationale": "; ".join(found[0]["evidence"]) + "."})
    return {"generator": "axiom_mappings.propose.outputs", "rulespec_us": corpus.commit, "map_release": m.release,
            "modules": modules, "proposed": len(proposals), "skipped": dict(skipped), "proposals": proposals}


def evaluate(m: Mappings, corpus_root, system, *, pe: PEVariables | None = None) -> dict[str, Any]:
    """Replay the reviewed direct mappings: would the proposer have offered each chosen variable?"""
    corpus = Corpus(corpus_root, m.pins.get("rulespec_us"))
    pe = pe or PEVariables(system)
    rows = [o for o in m.outputs if o.get("axiom") and o.get("type") == "direct_variable" and o.get("policyengine_variable")]
    corpus.load(split_id(o["axiom"])[0] for o in rows)
    bindings = bound(m, corpus)
    outcome, misses = Counter(), []
    for row in rows:
        module, name = split_id(row["axiom"])
        rule = (corpus.rules(module) or {}).get(name)
        if rule is None:
            outcome["not a rule at the pin"] += 1
            continue
        found, why = candidates(pe, module, name, rule, limit=10, reviewed=bindings)
        names = [c["policyengine_variable"] for c in found]
        chosen = row["policyengine_variable"]
        if chosen in names[:1]:
            outcome["first"] += 1
        elif chosen in names[:3]:
            outcome["top three"] += 1
        elif chosen in names:
            outcome["top ten"] += 1
        else:
            outcome["missed: " + (why or "not among the candidates")] += 1
            misses.append({"axiom": row["axiom"], "chosen": chosen, "offered": names[:3], "why": why})
    return {"rows": len(rows), "outcome": dict(outcome), "misses": misses}
