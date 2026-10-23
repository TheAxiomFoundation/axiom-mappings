"""One pass of the cycle chains the existing commands, and decides nothing.

The guardrail under test: a cycle proposes, never accepts and never promotes.
It also refuses to propose for a rule that arrived after the map's pin, because
`propose` reads the corpus at the pin and such a candidate would be a mapping
`identity` then rejects.
"""

from pathlib import Path

import pytest

from axiom_mappings import load
from axiom_mappings.cycle import _markdown, _module_prefixes, run


def _corpus_root():
    root = Path.home() / "rulespec-us"
    if not (root / ".git").exists():
        pytest.skip("no rulespec-us checkout to read")
    return root


@pytest.fixture(scope="module")
def m():
    return load("us")


@pytest.fixture(scope="module")
def report():
    return run("us", str(_corpus_root()), "origin/main")


def test_the_pass_reports_every_stage(report):
    assert report["schema"] == "axiom_mappings.cycle.v1"
    assert report["detect"]["schema"] == "axiom_mappings.detect.v1"
    assert report["coverage"]["schema"] == "axiom_mappings.coverage.v1"
    assert report["map_release"] == report["coverage"]["map_release"]


def test_a_rule_newer_than_the_pin_is_not_proposed(report):
    """Proposing past the pin would make a mapping the map's own identity check rejects."""
    if not report["detect"]["moved"]:
        pytest.skip("the corpus is at the pin, so nothing is newer")
    if not report["detect"]["axiom"]["added_and_unmapped"]:
        pytest.skip("nothing arrived to be blocked")
    assert report["pin_is_behind"] is True
    assert report["proposals"] == [], "a cycle writes no proposal it knows identity would reject"
    assert report["blocked_on_pin"], "it says which rules are waiting on the pin"


def test_the_summary_says_why_nothing_was_proposed(report):
    """"None" alone reads as "nothing arrived", which is the opposite of the truth."""
    body = _markdown(report)
    if report["pin_is_behind"]:
        assert "arrived after the map's pin" in body
        assert "Bump `pins.yaml`" in body
        assert "reviewed act" in body


def test_the_summary_is_honest_about_what_is_blocked(report):
    body = _markdown(report)
    blocked = [v for v in report["coverage"]["variables"] if v["blocked_by"]]
    assert "## Coverage" in body and "## Blocked" in body if blocked else True
    assert "Nothing here is accepted or promoted" in body


def test_a_pass_writes_nothing_into_the_map(m, report):
    """The packaged map is unchanged by a pass: proposals live outside it."""
    after = load("us")
    assert after.release == m.release, "a cycle does not change the release"
    assert len(after.outputs) == len(m.outputs)
    assert set(after.bindings) == set(m.bindings)


def test_module_prefixes_group_rules_by_area():
    ids = [
        "us:policies/usda/snap/fy-2024-cola/income-eligibility-standards#a",
        "us:policies/usda/snap/fy-2024-cola/maximum-allotments#b",
        "us:statutes/26/24/h#c",
    ]
    assert _module_prefixes(ids) == [
        "us:policies/usda/snap/fy-2024-cola",
        "us:statutes/26/24/h",
    ]
