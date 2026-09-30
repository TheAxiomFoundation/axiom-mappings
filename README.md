# axiom-mappings

The one shared map between Axiom RuleSpec concepts and PolicyEngine variables and parameters.
axiom-oracles, policyengine-axiom and axiom-api read it instead of keeping their own copies.
It lives in git so every change is reviewed and every run can pin the exact map it used; a
database can serve it later as a read model built from a release.

## Scope

The Axiom side of every comparison lives here: concepts, Axiom input rules, presumptions,
supplied parameters, the disagreement taxonomy, findings and readiness. So does the PolicyEngine
side, because three repos consume it. Counterpart-specific mappings with a single consumer
(TAXSIM, GETTSIM, EUROMOD, ACCESS NYC, ...) stay in axiom-oracles; a table moves here once a
second consumer needs it.

## Layout

```
axiom_mappings/data/us/concepts.yaml     shared input facts (age, wages, rent, ...) and their PolicyEngine input variable per entity
axiom_mappings/data/us/inputs.yaml       Axiom input slot -> concept, constant, or derived value
axiom_mappings/data/us/outputs.yaml      Axiom output -> PolicyEngine variable, or why it is not comparable
axiom_mappings/data/us/parameters.yaml   Axiom parameter -> PolicyEngine parameter path
axiom_mappings/data/us/prefixes.yaml     Axiom id prefixes classified together
axiom_mappings/data/us/presumptions.yaml what is presumed for facts the data lacks; constants name one
axiom_mappings/data/us/supplied_parameters.yaml  values a harness supplies because the encoding lacks them
axiom_mappings/data/us/programs.yaml     tracked programs, with each consumer's own name for them
axiom_mappings/data/taxonomy.yaml        why Axiom and a counterpart disagree (investigated causes), and crosswalks
                          from oracles' disposition kinds and axiom-api's known reasons
axiom_mappings/data/us/findings.yaml     known disagreements, each recorded once with its cause and issue
axiom_mappings/data/us/unresolved.yaml   Axiom ids that do not resolve in RuleSpec at the pin (a ratchet)
axiom_mappings/data/us/renames.yaml      reviewed Axiom id renames, with evidence
axiom_mappings/schema/finding.schema.json  the classified-disagreement record every consumer emits
axiom_mappings/schema/*.schema.json      JSON Schema for each table (usable from Python and TypeScript)
axiom_mappings/pins.yaml  what a release is valid against: policyengine-us, corpus, future-years rule
axiom_mappings/           loader (load, rules_for_slot, policyengine_variable, ...) and validator
```

## Rules

- **Precedence is explicit.** Several input rules can match one slot. The lowest `priority` wins,
  and the validator lists every slot that more than one rule matches.
- **Concepts are the edge.** A concept names the raw fact and the PolicyEngine input variable on
  each entity (`rent_paid` is `pre_subsidy_rent` per person and `housing_cost` per SPM unit). A
  concept mapped to a computed PolicyEngine variable is flagged: Axiom cannot replace that chain.
- **Entity mismatches must be declared.** An output compared on another entity than
  PolicyEngine's needs `entity_projection`.
- **Bracket scales** are addressed as `<scale>.rates|thresholds|amounts` with the bracket index in
  `parameter_key_path`.
- **Constants are presumptions.** They should name the presumption policy they apply; today none
  do, and the validator counts them.
- **Axiom ids must exist.** `python -m axiom_mappings.identity --corpus <rulespec-us>` resolves every
  id in outputs, parameters and prefixes at the pinned `rulespec_us` commit, and each program's module
  at its own `corpus_ref`, reading git objects so no checkout or branch is touched. An id that does not
  resolve comes with suggestions (same name moved within the jurisdiction, or a longer or shorter name
  in its module). `unresolved.yaml` lists the known ones; a new one fails, and a listed one that
  resolves again fails until removed, so the list only shrinks. A rename is a claim about meaning,
  so it goes through review into `renames.yaml` with evidence, never automatically.
- **Future years** (`pins.yaml`): Axiom's values through its last encoded date, then PolicyEngine's
  indexing, flagged in provenance.

## Readiness, releases and findings

- **Release:** `load(country).release` is `<country>-<sha12>` over every table, the taxonomy and
  the pins. Every report from a consumer cites it, so a number can be traced to the exact map.
- **Readiness** (`python -m axiom_mappings.readiness`): a program serves Axiom as the truth only
  when every input slot is mapped, derived, or presumed under an acceptable presumption, and no
  parameter is supplied by a harness. Readiness is shown next to parity wherever parity is shown.
- **Findings:** every disagreement is investigated; neither side is presumed right. It is recorded once, in `schema/finding.schema.json` form, with one
  cause from `taxonomy.yaml`. `Mappings.classify()` turns a consumer's own reason code into it.

## Consumers

- `axiom_mappings.export.oracles_registry(m)` rebuilds axiom-oracles' registry payload exactly
  (tested round trip), so oracles can read this map in place of `bridges/mappings/us.yaml`.
- policyengine-axiom reads it for its provenance report, findings and map release.

## Use

```sh
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -e '.[policyengine,test]'
.venv/bin/python -m axiom_mappings.validate --country us --policyengine --corpus ~/rulespec-us --artifact compiled.json
.venv/bin/python -m axiom_mappings.identity --corpus ~/rulespec-us --ref origin/main   # drift since the pin
.venv/bin/python -m pytest
```

```python
from axiom_mappings import load
m = load("us")
m.rule_for_slot("us-co:...#input.household_size")
m.policyengine_variable("axiom:housing/household#rent_paid", "spm_unit")   # "housing_cost"
```

## Origin

Seeded from axiom-oracles (commit in `axiom_mappings/pins.yaml`) by `scripts/seed_from_oracles.py`:
`core/case.py` concepts, `adapters/policyengine/runner.py` concept tables,
`data/populace_input_mapping.yaml` and `bridges/mappings/us.yaml`. Edit the data here from now on.

## First validation (policyengine-us 1.808.0)

No errors. Warnings to work through:
- 12 concepts map to computed PolicyEngine variables;
- 4 comparisons are per year where PolicyEngine defines the variable per month;
- 6 use nonstandard `comparison` vocabulary;
- 3 direct mappings lack an entity or period;
- 2 rules scale self-employment income by 0.6;
- 178 constants name no presumption;
- 1 group note has no Axiom id.

The generic rules cover 37 of the 681 input slots of the Colorado SNAP FY 2026 program; oracles
covers the rest in program-specific Python, which should move here as data.
