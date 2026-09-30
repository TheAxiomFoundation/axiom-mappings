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
axiom_mappings/data/us/bindings/<program>.<profile>.yaml  how one consumer profile feeds a program and binds
                          its outputs (cut: live PE simulation, policyengine-axiom; facts: request facts, axiom-api)
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
- **Bindings are per program and profile.** A `cut` binding may read any PE variable (it runs inside a
  PE simulation); a `facts` binding only PE inputs. Slots use policyengine-axiom's adapter grammar
  (`bindings.py`), and every constant sits under a presumption (`presumed: {<presumption>: {slot: value}}`).
  The validator type-checks each slot against policyengine-us (entity, period, enum values, stock vs
  flow), resolves output and parameter names in the program's import closure at its `corpus_ref`, and
  with a slot catalog checks that every slot the compiled program reads is bound and nothing else is.
  `python -m axiom_mappings.profiles` lists slots two profiles bind differently.
- **Parameters are verified over their whole history** (`python -m axiom_mappings verify parameters
  --corpus <rulespec-us>`). The mapping row gives identity (Axiom id, PE path and cell); values are
  only the check. Axiom's history for a PE cell is assembled from every id mapped to it (one module per
  fiscal year), each version holding until its `effective_to` or the next version, and compared with
  PE's `values_list` interval by interval: `match`, `disagree`, `stale-axiom` / `stale-pe` (one side's
  value is newer: the other has not encoded a change), `uncovered-axiom` / `uncovered-pe` (a gap after a
  side began: incomplete, never "disagrees"). History before a side began is not a gap, and intervals
  after `verify_as_of` (pins.yaml) are PE's projections, reported apart. Hints flag patterns worth
  checking (a ratio of 12 or 100, sub-cent or rounded rates). Derived rules, non-literal formulas and
  cells chosen at run time are listed as not checkable, with the reason. Every current difference is
  filed in findings.yaml (`kind: parameter`, unclassified until investigated, D48); the validator fails
  on an unfiled one and on a filed one that no longer differs.
- **Proposals are reviewed before they enter the map** (`python -m axiom_mappings propose parameters|outputs|slots`,
  then `accept`). Proposers write `proposals/<kind>/<name>.yaml` outside the package (gitignored), so nothing
  proposed ships. Identity admits a candidate, strongest first: a reviewed binding already pairs them; the
  two cite the same law (`citations.py`: USC, CFR and revenue procedures from Axiom module ids and rule
  sources, and from PolicyEngine reference titles and links); a reviewed sibling rule maps into the same
  PolicyEngine subtree (parameters); or the names match (outputs, slots). Units, shapes (a table needs a node
  with its rows) and value types must fit. Name overlap and the value history (`verify`) only rank what
  identity admitted, and every candidate carries its evidence. A reviewer sets `accept:` to a candidate's
  index, fixes `program` / `rationale` / table keys, and `accept` writes that row or binding.
  `--evaluate` replays the reviewed map: the proposers put the reviewer's choice first for 278 parameters
  and 107 outputs (top three: 307 and 122); rules citing state codes, which are not read yet, get none.
- **Future years** (`pins.yaml`): Axiom's values through its last encoded date, then PolicyEngine's
  indexing, flagged in provenance.

## Readiness, releases and findings

- **Release:** `load(country).release` is `<country>-<sha12>` over every table, the taxonomy and
  the pins. Every report from a consumer cites it, so a number can be traced to the exact map.
- **Readiness** (`python -m axiom_mappings.readiness --all --catalog ...`): a program serves Axiom's
  answer only when every input slot is bound or presumed under an acceptable presumption (verified
  against its slot catalog), no parameter or value of law is supplied by a consumer, and no filed
  disagreement has a blocking cause (`blocks: true` in the taxonomy: unclassified,
  axiom-encoding-wrong, mapping-wrong). Readiness is shown next to parity wherever parity is shown.
- **Findings:** every disagreement is investigated; neither side is presumed right. It is recorded once, in `schema/finding.schema.json` form, with one
  cause from `taxonomy.yaml`. `Mappings.classify()` turns a consumer's own reason code into it.

## Consumers

- `axiom_mappings.export.oracles_registry(m)` rebuilds axiom-oracles' registry payload exactly
  (tested round trip), so oracles can read this map in place of `bridges/mappings/us.yaml`.
- `python -m axiom_mappings export axiom-api --out <axiom-api>/data/serving-map` writes axiom-api's serving
  maps from the `facts` bindings, byte for byte. Facts bindings are in axiom-api's own grammar (`facts.py`):
  a request carries fields and nothing is computed. `facts.py` also holds a Python port of axiom-api's
  interpreter (`household-compat.ts`), and `conformance/facts.json` holds cases whose `expected` values come
  from running the TypeScript. The Python must reproduce them, and axiom-api runs the same file, so one
  grammar keeps one semantics. `programs.yaml` gives Colorado SNAP the family `us/snap` (axiom-api serves
  every state's package through it), so `profiles` compares the two consumers slot by slot.
- `python -m axiom_mappings.export.policyengine_axiom --out <policyengine-axiom>/manifests` writes
  policyengine-axiom's binding manifests from the `cut` bindings (tested equal to the files, types included);
  policyengine-axiom builds its manifests from the map by default (`PE_AXIOM_MANIFESTS=files` reads the
  export instead) and reads the map for its provenance report, findings and map release. Its `promote` /
  `revoke` set a binding's status here (`python -m axiom_mappings bindings set-status`, which changes that
  one line) and regenerate its `manifests/`.

## Use

```sh
uv venv --python 3.13 .venv && uv pip install --python .venv/bin/python -e '.[policyengine,test]'
.venv/bin/python -m axiom_mappings.validate --country us --policyengine --corpus ~/rulespec-us --artifact compiled.json
.venv/bin/python -m axiom_mappings.identity --corpus ~/rulespec-us --ref origin/main   # drift since the pin
.venv/bin/python -m axiom_mappings verify parameters --corpus ~/rulespec-us [--program snap] [--file-findings]
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
