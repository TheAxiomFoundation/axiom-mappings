Test fixtures, each a snapshot from another repository:

- `manifests/`: policyengine-axiom `manifests/*.json` at 3a9222d, the manifests the `cut` bindings
  were imported from. The export must reproduce them exactly (types included).
- `catalogs/`: each policyengine-axiom program's input slots as the engine compiles it at corpus
  d58cc0ce6, from policyengine-axiom `scripts/export_slot_catalog.py`.

Refresh both when policyengine-axiom's manifests or corpus change.
- `oracles/populace_input_mapping.yaml`: axiom-oracles' populace input table at 94769e176, which
  inputs.yaml was seeded from (comments included). The oracles-populace export must reproduce its
  entries and keep every comment.
- `serving-map/`: axiom-api's serving maps at c8e2d42; the axiom-api export must reproduce them byte for byte.
