"""Investigations: deterministic evidence, a fail-closed model call, a deterministic check, recorded for replay."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from axiom_mappings import load
from axiom_mappings.accept import accept
from axiom_mappings.investigate import call, check, investigate, proposal, request, schema, slug
from axiom_mappings.propose import write

from test_mappings import copy_root

VARIABLE = "us-co/snap-fy2026:snap_earned_income_deduction"


@pytest.fixture(scope="module")
def m():
    return load("us")


def answer(cause="mapping-wrong", expression="780 / 12", equals=65, confidence=0.8):
    return {"cause": cause, "confidence": confidence, "mechanism": "Axiom states the exclusion per year, PolicyEngine per month.",
            "arithmetic": {"expression": expression, "equals": equals}, "citations": ["42 USC 1382a(b)(4)(A)"],
            "next_action": "Map the annual Axiom value to the monthly parameter with a period conversion."}


BUNDLE = {"kind": "parameter", "first_difference": {"axiom": 780.0, "pe": 65.0, "verdict": "stale-axiom"}}


def test_the_check_needs_arithmetic_that_reconciles_the_observed_numbers(m):
    assert check(m, BUNDLE, answer()) == []
    assert "not 70" in check(m, BUNDLE, answer(equals=70))[0]  # the expression is not what it claims
    assert "none of the observed numbers" in check(m, BUNDLE, answer(expression="7 * 10", equals=70))[0]
    assert check(m, BUNDLE, answer(expression="780 - 65", equals=715)) == []  # the difference is observed too
    assert "needs arithmetic" in check(m, BUNDLE, answer(expression="", equals=0))[0]
    assert check(m, BUNDLE, answer(cause="unclassified", expression="", equals=0)) == []  # not settled: allowed
    assert "taxonomy" in check(m, BUNDLE, answer(cause="pe-is-wrong"))[0]
    assert "confidence" in check(m, BUNDLE, answer(confidence=3))[0]


def test_the_schema_offers_exactly_the_taxonomy(m):
    assert set(schema(m)["properties"]["cause"]["enum"]) == m.causes


def test_the_request_states_d48_and_carries_the_evidence(m):
    req = request(m, BUNDLE)
    assert req["model"] == "claude-opus-5-5"
    assert "Neither side is presumed right" in req["system"]
    assert '"axiom": 780.0' in req["user"] and "counterpart-differs-from-law" in req["user"]


# ---------------------------------------------------------------- the model call, without the network

def fake_client(respond):
    anthropic = pytest.importorskip("anthropic")
    import httpx2

    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return respond(request)

    client = anthropic.Anthropic(api_key="test-key-not-real", max_retries=0,
                                 http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    return client, seen


def message(text, stop_reason="end_turn"):
    import httpx2

    return httpx2.Response(200, json={"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                      "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
                                      "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 5}})


def test_a_structured_answer_comes_back_parsed(m):
    client, seen = fake_client(lambda r: message(json.dumps(answer())))
    result = call(request(m, BUNDLE), client)
    assert result.error is None and result.payload["cause"] == "mapping-wrong" and result.usage["output_tokens"] == 5
    body = seen[0]
    assert body["output_config"]["format"]["type"] == "json_schema" and body["fallbacks"] == "default"


@pytest.mark.parametrize("respond,error", [
    (lambda r: message("", stop_reason="refusal"), "declined"),
    (lambda r: message("not json"), "not JSON"),
    (lambda r: __import__("httpx2").Response(500, json={"type": "error", "error": {"type": "api_error", "message": "x"}}),
     "InternalServerError"),
])
def test_every_failure_is_an_error_never_an_answer(m, respond, error):
    client, _ = fake_client(respond)
    result = call(request(m, BUNDLE), client)
    assert result.payload is None and error in result.error


def test_no_sdk_is_an_error(m, monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    result = call(request(m, BUNDLE))
    assert result.payload is None and "anthropic" in result.error  # ModuleNotFoundError: fail closed


# ---------------------------------------------------------------- record, replay, propose, accept

def test_a_dry_run_sends_nothing(m, tmp_path):
    record = investigate(m, VARIABLE, out=tmp_path, client=object())  # a client that cannot be called
    assert record["status"] == "dry-run" and (tmp_path / slug(VARIABLE) / "request.json").exists()
    assert not (tmp_path / slug(VARIABLE) / "response.json").exists()


def recorded(tmp_path, key, payload):
    directory = tmp_path / "recorded" / slug(key)
    directory.mkdir(parents=True)
    (directory / "response.json").write_text(json.dumps({"payload": payload, "model": "claude-opus-5-5"}))
    return tmp_path / "recorded"


def test_a_replay_reproduces_the_record_and_a_failed_check_is_not_proposed(m, tmp_path):
    good = answer(cause="convention", expression="0", equals=0)  # a variable finding has no first difference
    record = investigate(m, VARIABLE, out=tmp_path / "runs", replay=recorded(tmp_path, VARIABLE, good))
    assert record["status"] == "proposed" and record["answer"]["cause"] == "convention"
    assert json.loads((tmp_path / "runs" / slug(VARIABLE) / "check.json").read_text()) == {"problems": []}
    bad = investigate(m, VARIABLE, out=tmp_path / "runs2",
                      replay=recorded(tmp_path / "b", VARIABLE, answer(cause="convention", expression="1 + 1", equals=3)))
    assert bad["status"] == "rejected" and bad["problems"]


def test_accepting_an_investigation_updates_one_finding_and_nothing_else(m, tmp_path):
    root = copy_root(tmp_path / "map")
    record = investigate(m, VARIABLE, out=tmp_path / "runs",
                         replay=recorded(tmp_path, VARIABLE, answer(cause="convention", expression="0", equals=0)))
    item = proposal(m, record)
    item["accept"] = 0
    path = write("findings", "t", {"generator": "axiom_mappings.investigate", "kind": "findings"}, [item],
                 root=tmp_path / "proposals")
    before = (root / "data/us/findings.yaml").read_text()
    assert accept(path, root=root) == [VARIABLE]
    after = (root / "data/us/findings.yaml").read_text()
    entry = next(f for f in load("us", root).findings if f"{f['program']}:{f['variable']}" == VARIABLE)
    assert entry["cause"] == "convention" and "Investigated (claude-opus-5-5)" in entry["note"]
    assert entry["evidence"]["investigation"]["arithmetic"] == {"expression": "0", "equals": 0}
    assert after.splitlines()[:3] == before.splitlines()[:3]  # the header comments stay
    others = lambda text: [f for f in yaml.safe_load(text)["findings"] if f"{f['program']}:{f['variable']}" != VARIABLE]
    assert others(after) == others(before)


# ---------------------------------------------------------------- real evidence

RULESPEC = Path(os.environ.get("RULESPEC_US", Path.home() / "rulespec-us"))


def test_parameter_evidence_shows_where_the_histories_part(m, tmp_path):
    pytest.importorskip("policyengine_us")
    pin = m.pins["rulespec_us"]
    if not (RULESPEC / ".git").exists() or subprocess.run(["git", "-C", str(RULESPEC), "cat-file", "-e", pin]).returncode:
        pytest.skip(f"needs rulespec-us with commit {pin} (set RULESPEC_US)")
    from axiom_mappings.investigate import evidence
    from axiom_mappings.validate import policyengine_system

    from axiom_mappings.verify.parameters import verify

    system = policyengine_system()
    report = verify(m, RULESPEC, system)
    bundle = evidence(m, "tax:gov.irs.unemployment_compensation.exemption.amount", RULESPEC, system, report)
    first = bundle["first_difference"]
    assert (first["from"], first["axiom"], first["pe"]) == ("2021-01-01", 10200.0, 0.0)  # the 2020-only rule, carried on
    assert bundle["axiom"][0]["source"] == "26 USC 85(c)(1)"
    assert bundle["policyengine"]["path"] == "gov.irs.unemployment_compensation.exemption.amount"
    assert any(r["table"] == "parameters" and r["axiom"].endswith("cap_per_taxpayer_or_spouse") for r in bundle["reviewed_rows"])
    assert check(m, bundle, answer(cause="axiom-encoding-wrong", expression="10200 - 0", equals=10200)) == []
    # proof atoms quote their source as `text` or `excerpt`; both reach the evidence
    az = evidence(m, "snap:gov.usda.snap.income.deductions.excess_medical_expense.standard[AZ]", RULESPEC, system, report)
    assert az["axiom"][0]["proof_text"] == ["The SMD net amount is $145"]
