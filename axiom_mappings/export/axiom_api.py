"""axiom-api's serving maps (``axiom-pe-variable-mapping/1``) from the ``facts`` bindings.

    python -m axiom_mappings export axiom-api --out <axiom-api>/data/serving-map

The binding set is already in axiom-api's grammar; the export adds the consumer's program name and
turns the tagged presumptions back into axiom-api's flat, ordered map (their reasons stay here).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .. import Mappings, load
from ..facts import slug

CONSUMER = "axiom-api"
ORDER = ("format", "program", "description", "targets", "variables", "inputs", "presumptions", "evidence", "overrides")


def serving_map(m: Mappings, doc: dict[str, Any]) -> dict[str, Any]:
    program = m.programs[doc["program"]]["consumers"][CONSUMER]
    out: dict[str, Any] = {}
    for key in ORDER:
        if key == "program":
            out[key] = program
        elif key == "presumptions" and key in doc:
            out[key] = {slot: p["value"] for slot, p in doc[key].items()}
        elif key in doc:
            out[key] = doc[key]
    return out


def serving_maps(m: Mappings) -> dict[str, dict[str, Any]]:
    """File name -> serving map, for every facts binding set axiom-api reads."""
    out = {}
    for (_program, profile), bs in sorted(m.bindings.items()):
        if profile == "facts" and CONSUMER in (bs.doc.get("consumers") or []):
            doc = serving_map(m, bs.doc)
            out[slug(doc["program"])] = doc
    return out


def write(m: Mappings, directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name, doc in serving_maps(m).items():
        path = directory / name
        path.write_text(json.dumps(doc, indent=2) + "\n")  # ASCII escapes, as axiom-api writes them
        written.append(path)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings export axiom-api")
    ap.add_argument("--country", default="us")
    ap.add_argument("--out", type=Path, required=True, help="directory for the serving maps (axiom-api/data/serving-map)")
    args = ap.parse_args(argv)
    for path in write(load(args.country), args.out):
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
