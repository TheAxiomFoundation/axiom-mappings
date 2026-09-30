Test fixtures, each a snapshot from another repository:

- `manifests/`: policyengine-axiom `manifests/*.json` at 3a9222d, the manifests the `cut` bindings
  were imported from. The export must reproduce them exactly (types included).
- `catalogs/`: each policyengine-axiom program's input slots as the engine compiles it at corpus
  d58cc0ce6, from policyengine-axiom `scripts/export_slot_catalog.py`.

Refresh both when policyengine-axiom's manifests or corpus change.
