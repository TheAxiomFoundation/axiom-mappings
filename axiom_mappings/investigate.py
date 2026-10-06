"""Draft a cause for an unclassified finding: evidence gathered deterministically, a model's reading of it,
and a deterministic check before anything is proposed (D48: every disagreement is investigated).

    python -m axiom_mappings investigate --finding PROGRAM:VARIABLE --corpus <rulespec-us> [--live] [--replay DIR]
    python -m axiom_mappings investigate --unclassified --limit N --corpus <rulespec-us> [--live]

1. **Evidence** (no model): for a parameter finding, where the two histories part (``verify``), the hint,
   each Axiom rule's source, proof atoms and dated values, PolicyEngine's references and the values
   around the first difference, and the mapping row's rationale. For a variable finding, the binding
   and its notes.
2. **Reading** (Claude, structured output): one cause from the taxonomy, the mechanism, the arithmetic
   that reconciles the observed numbers, citations, and what to do next. Neither side is presumed
   right; ``unclassified`` with what is missing is a valid answer.
3. **Check** (no model): the cause is in the taxonomy, and a classified answer's arithmetic evaluates to
   its stated result, which must be one of the observed numbers (either side's value, or their
   difference) at the first difference. An answer that fails is recorded but not proposed.

Every request and response is written to ``investigations/<finding>/`` (gitignored) so a run can be
replayed exactly (``--replay``) and tested without the API. Without ``--live`` nothing is sent: the request
is written for inspection. Accepted proposals update findings.yaml through ``accept``. Advisory, like
axiom-encode's judges: a reviewer decides.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ROOT, Mappings, load
from .verify.parameters import literal

INVESTIGATIONS = ROOT.parent / "investigations"
DEFAULT_MODEL = "claude-opus-5-5"
TOLERANCE = 0.005


# ------------------------------------------------------------------ evidence

def _finding(m: Mappings, key: str) -> dict[str, Any]:
    program, _, variable = key.partition(":")
    found = next((f for f in m.findings if f["program"] == program and f["variable"] == variable), None)
    if found is None:
        raise KeyError(f"no finding {key} in findings.yaml")
    return found


def _first_difference(result: dict[str, Any]) -> dict[str, Any] | None:
    return next((s for s in result["segments"] if not s["projected"] and s["verdict"] == result["verdict"]), None)


def parameter_evidence(m: Mappings, finding: dict[str, Any], corpus_root, system,
                       report: dict[str, Any] | None = None) -> dict[str, Any]:
    from .corpus import Corpus, split_id
    from .verify.parameters import _node, verify

    target = finding["variable"]
    path = re.split(r"[\[]|\.calc\(", target)[0]
    report = report or verify(m, corpus_root, system)
    result = next((r for r in report["results"] if r["target"] == target), None)
    corpus = Corpus(corpus_root, m.pins.get("rulespec_us"))
    axiom = []
    for axiom_id in (result or finding.get("evidence", {})).get("axiom_ids", []):
        module, name = split_id(axiom_id)
        rule = (corpus.rules(module) or {}).get(name) or {}
        atoms = [(a.get("source") or {}).get("text") or (a.get("source") or {}).get("excerpt")  # encoders write either
                 for a in (rule.get("metadata") or {}).get("proof", {}).get("atoms", [])]
        axiom.append({"id": axiom_id, "source": rule.get("source"), "unit": rule.get("unit"), "dtype": rule.get("dtype"),
                      "versions": rule.get("versions"), "proof_text": [a for a in atoms if a],
                      "mapping_rationale": next((r.get("rationale") for r in m.parameters if r.get("axiom") == axiom_id), None)})
    try:
        node, _ = _node(system, path)
        meta = getattr(node, "metadata", None) or {}
        pe = {"path": path, "description": getattr(node, "description", None), "unit": meta.get("unit"),
              "period": meta.get("period"), "reference": meta.get("reference")}
    except Exception as e:  # the mapping may name something PolicyEngine no longer has
        pe = {"path": path, "error": str(e)}
    ids = set((result or finding.get("evidence", {})).get("axiom_ids", []))
    modules = {split_id(i)[0] for i in ids}
    prior = [{"table": table, **{k: r.get(k) for k in ("axiom", "type", "policyengine_parameter", "parameter_key",
                                                         "result_multiplier", "rationale") if r.get(k) is not None}}
             for table, rows in (("outputs", m.outputs), ("parameters", m.parameters)) for r in rows
             if r.get("axiom") and (r["axiom"] in ids or (split_id(r["axiom"])[0] in modules and table == "outputs")
                                    or r.get("policyengine_parameter") == path)]
    first = _first_difference(result) if result else None
    window = [s for s in (result or {}).get("segments", []) if not s["projected"]][-8:]
    return {"kind": "parameter", "finding": finding, "verdict": (result or {}).get("verdict"),
            "first_difference": first, "recent_segments": window, "axiom": axiom, "policyengine": pe,
            "reviewed_rows": prior,  # what reviewers already decided about these ids, their module, and this PE path
            "as_of": report["as_of"], "rulespec_us": report["ref"]}


def variable_evidence(m: Mappings, finding: dict[str, Any]) -> dict[str, Any]:
    bindings = []
    for (program, profile), bs in m.bindings.items():
        for b in bs.doc.get("bindings") or []:
            if b.get("pe_variable") == finding["variable"]:
                bindings.append({"program": program, "profile": profile, **b})
    return {"kind": "variable", "finding": finding, "bindings": bindings, "first_difference": None}


def evidence(m: Mappings, key: str, corpus_root=None, system=None, report: dict[str, Any] | None = None) -> dict[str, Any]:
    finding = _finding(m, key)
    if finding.get("kind") == "parameter":
        return parameter_evidence(m, finding, corpus_root, system, report)
    return variable_evidence(m, finding)


# ------------------------------------------------------------------ the request

SYSTEM = """You investigate a disagreement between Axiom (law encoded from its source, as RuleSpec) and \
PolicyEngine (a microsimulation model) for one mapped value. Neither side is presumed right: the \
investigation may find PolicyEngine differs from the law, the Axiom encoding is wrong or out of date, \
the mapping between them is wrong (wrong cell, unit, or period), or that both state the same law under \
a different convention (rounding, period, representation). Read the evidence, decide the most likely \
cause, and show the arithmetic that reconciles the numbers you were given. If the evidence does not \
settle it, answer `unclassified` and say what would. Cite the law or source you rely on; do not invent \
citations."""


def causes(m: Mappings) -> list[dict[str, Any]]:
    return [{"id": c["id"], "meaning": c["meaning"]} for c in m.taxonomy.get("causes", [])]


def schema(m: Mappings) -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["cause", "confidence", "mechanism", "arithmetic", "citations", "next_action"],
        "properties": {
            "cause": {"type": "string", "enum": [c["id"] for c in causes(m)]},
            "confidence": {"type": "number"},
            "mechanism": {"type": "string"},
            "arithmetic": {
                "type": "object", "additionalProperties": False, "required": ["expression", "equals"],
                "properties": {"expression": {"type": "string"}, "equals": {"type": "number"}},
            },
            "citations": {"type": "array", "items": {"type": "string"}},
            "next_action": {"type": "string"},
        },
    }


def request(m: Mappings, bundle: dict[str, Any], model: str = DEFAULT_MODEL) -> dict[str, Any]:
    user = ("Causes (pick one):\n" + json.dumps(causes(m), indent=1) +
            "\n\nArithmetic: an expression of numbers and + - * / that evaluates to `equals`, where `equals` is one "
            "of the observed numbers at the first difference (Axiom's value, PolicyEngine's, or their difference). "
            "Use an empty expression and 0 only when answering unclassified.\n\nEvidence:\n"
            + json.dumps(bundle, indent=1, default=str))
    return {"model": model, "system": SYSTEM, "user": user, "schema": schema(m)}


# ------------------------------------------------------------------ the model call (fail-closed)

@dataclass
class Call:
    payload: dict[str, Any] | None
    model: str
    raw_text: str | None = None
    stop_reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def call(req: dict[str, Any], client=None) -> Call:
    """One structured-output request. Any failure (no SDK, no credentials, API error, refusal, bad JSON)
    returns an error rather than an answer: fail-open is not allowed."""
    try:
        if client is None:
            import anthropic

            client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=req["model"],
            max_tokens=16000,
            system=req["system"],
            messages=[{"role": "user", "content": req["user"]}],
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": req["schema"]}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # a declined request is re-run on a fallback model in the same call
        )
    except Exception as e:  # SDK missing, credentials missing, API or network error
        return Call(None, req["model"], error=f"{type(e).__name__}: {e}")
    usage = getattr(response, "usage", None)
    usage = {k: getattr(usage, k, None) for k in ("input_tokens", "output_tokens")} if usage is not None else {}
    if response.stop_reason == "refusal":
        return Call(None, response.model, stop_reason="refusal", usage=usage, error="the model declined")
    text = next((b.text for b in response.content if getattr(b, "type", None) == "text"), None)
    try:
        payload = json.loads(text or "")
    except ValueError as e:
        return Call(None, response.model, text, response.stop_reason, usage, f"not JSON: {e}")
    return Call(payload, response.model, text, response.stop_reason, usage)


# ------------------------------------------------------------------ the check (no model)

def check(m: Mappings, bundle: dict[str, Any], answer: dict[str, Any]) -> list[str]:
    """Why an answer cannot be proposed (empty when it can)."""
    problems = []
    if answer.get("cause") not in m.causes:
        problems.append(f"cause {answer.get('cause')!r} is not in the taxonomy")
    confidence = answer.get("confidence")
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        problems.append("confidence must be between 0 and 1")
    if answer.get("cause") == "unclassified":
        return problems
    arithmetic = answer.get("arithmetic") or {}
    value = literal(arithmetic.get("expression") or "")
    if value is None:
        problems.append("a classified answer needs arithmetic that evaluates")
        return problems
    equals = arithmetic.get("equals")
    if not isinstance(equals, (int, float)) or not math.isclose(value, equals, abs_tol=TOLERANCE, rel_tol=1e-9):
        problems.append(f"{arithmetic.get('expression')} = {value}, not {equals}")
    first = bundle.get("first_difference")
    if first:  # the result must be a number the evidence shows
        a, p = first.get("axiom"), first.get("pe")
        observed = [x for x in (a, p, abs(a - p) if a is not None and p is not None else None) if x is not None]
        if isinstance(equals, (int, float)) and not any(math.isclose(equals, o, abs_tol=TOLERANCE, rel_tol=1e-9) for o in observed):
            problems.append(f"the arithmetic's result {equals} is none of the observed numbers {observed}")
    return problems


# ------------------------------------------------------------------ one investigation

def slug(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", key)[:150]


def investigate(m: Mappings, key: str, *, corpus_root=None, system=None, live: bool = False, replay: Path | None = None,
                model: str = DEFAULT_MODEL, client=None, out: Path | None = None,
                report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Gather, (optionally) ask, check, record. Returns the record (and a proposal when it passes)."""
    directory = (out or INVESTIGATIONS) / slug(key)
    directory.mkdir(parents=True, exist_ok=True)
    bundle = evidence(m, key, corpus_root, system, report)
    req = request(m, bundle, model)
    (directory / "request.json").write_text(json.dumps(req, indent=1, default=str))
    if replay is not None:
        recorded = json.loads((Path(replay) / slug(key) / "response.json").read_text())
        result = Call(recorded.get("payload"), recorded.get("model", model), recorded.get("raw_text"),
                      recorded.get("stop_reason"), recorded.get("usage") or {}, recorded.get("error"))
    elif live:
        result = call(req, client)
    else:
        return {"key": key, "status": "dry-run", "request": str(directory / "request.json")}
    response = {"payload": result.payload, "model": result.model, "raw_text": result.raw_text,
                "stop_reason": result.stop_reason, "usage": result.usage, "error": result.error,
                "map_release": m.release, "date": datetime.date.today().isoformat()}
    (directory / "response.json").write_text(json.dumps(response, indent=1))
    if result.error or result.payload is None:
        return {"key": key, "status": "error", "error": result.error}
    problems = check(m, bundle, result.payload)
    (directory / "check.json").write_text(json.dumps({"problems": problems}, indent=1))
    record = {"key": key, "status": "rejected" if problems else "proposed", "problems": problems, "answer": result.payload,
              "investigation": str(directory.relative_to(directory.parents[1]) if len(directory.parents) > 1 else directory),
              "model": result.model}
    return record


def proposal(m: Mappings, record: dict[str, Any]) -> dict[str, Any]:
    finding = _finding(m, record["key"])
    answer = record["answer"]
    return {"program": finding["program"], "variable": finding["variable"], "current_cause": finding["cause"],
            "candidates": [{"cause": answer["cause"], "mechanism": answer["mechanism"],
                            "arithmetic": answer["arithmetic"], "citations": answer["citations"],
                            "confidence": answer["confidence"], "next_action": answer["next_action"],
                            "investigation": record["investigation"], "model": record["model"]}],
            "accept": None}


def main(argv=None) -> int:
    from .propose import write

    ap = argparse.ArgumentParser(prog="axiom_mappings investigate")
    ap.add_argument("--country", default="us")
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--finding", action="append", help="PROGRAM:VARIABLE (repeatable)")
    group.add_argument("--unclassified", action="store_true", help="every unclassified finding")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--corpus", help="rulespec-us git checkout (needed for parameter findings)")
    ap.add_argument("--live", action="store_true", help="send the requests (Claude API); otherwise only write them")
    ap.add_argument("--replay", type=Path, help="a directory of recorded responses to use instead of the API")
    ap.add_argument("--model", default=os.environ.get("AXIOM_INVESTIGATE_MODEL", DEFAULT_MODEL))
    ap.add_argument("--name", default=None, help="proposal file name")
    args = ap.parse_args(argv)
    m = load(args.country)
    keys = args.finding or [f"{f['program']}:{f['variable']}" for f in m.findings if f["cause"] == "unclassified"]
    keys = keys[: args.limit] if args.limit else keys
    system = report = None
    if any(_finding(m, k).get("kind") == "parameter" for k in keys):
        from .validate import policyengine_system
        from .verify.parameters import verify

        if not args.corpus:
            ap.error("parameter findings need --corpus")
        system = policyengine_system()
        report = verify(m, args.corpus, system)  # once, for every finding
    records = [investigate(m, k, corpus_root=args.corpus, system=system, live=args.live, replay=args.replay,
                           model=args.model, report=report) for k in keys]
    proposals = [proposal(m, r) for r in records if r["status"] == "proposed"]
    for r in records:
        print(f"{r['status']:9} {r['key']}" + (f"  {r.get('error') or '; '.join(r.get('problems', []))}"
                                              if r["status"] in ("error", "rejected") else ""))
    if proposals:
        path = write("findings", args.name or datetime.date.today().isoformat(),
                     {"generator": "axiom_mappings.investigate", "kind": "findings", "model": args.model,
                      "map_release": m.release}, proposals)
        print(f"{len(proposals)} finding proposal(s) -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
