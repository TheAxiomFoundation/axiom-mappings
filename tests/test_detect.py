"""`detect` reports what changed since the pins, which is not the same as what is unmapped.

Most rules in the corpus have never been mapped. That backlog is `export coverage`.
Only a two-ref diff says what *changed*, and a cycle acts on the diff.
"""

import json

import pytest

from axiom_mappings import load
from axiom_mappings.detect import _mapped_axiom_ids, detect, main


@pytest.fixture(scope="module")
def m():
    return load("us")


def test_without_a_move_nothing_is_added_or_removed(m, tmp_path):
    """Checking the pin against itself: the diff is empty, and unmapped is still reported.

    An empty `added` here is the honest answer. Reporting every unmapped rule as
    "added" would send a cycle at the whole corpus.
    """
    report = detect(m, _corpus_root(), ref=m.pins["rulespec_us"], policyengine=False)
    assert report["moved"] is False
    assert report["axiom"]["added"] == []
    assert report["axiom"]["removed"] == []
    assert report["axiom"]["unmapped_total"] > 0, "the backlog is still counted"


def test_a_move_reports_only_the_difference(m):
    """Against a later ref, `added` is a small diff, never the whole corpus."""
    report = detect(m, _corpus_root(), ref="origin/main", policyengine=False)
    if not report["moved"]:
        pytest.skip("the local corpus is at the pin, so there is no diff to show")
    axiom = report["axiom"]
    assert len(axiom["added"]) < axiom["unmapped_total"], (
        "added is a diff between refs, not everything the map has not mapped"
    )
    assert set(axiom["added_and_unmapped"]) <= set(axiom["added"])
    assert set(axiom["removed_but_mapped"]) <= set(axiom["removed"])


def test_a_mapped_id_absent_at_the_ref_is_reported(m):
    """A mapping whose rule is gone is stale, and `identity` suggests the rename."""
    report = detect(m, _corpus_root(), ref="origin/main", policyengine=False)
    absent = report["axiom"]["mapped_but_absent"]
    mapped = _mapped_axiom_ids(m)
    assert all(a in mapped for a in absent), "only mapped ids can be reported as absent"
    assert all("#" in a for a in absent), "a prefix row is not an id, so it is not reported here"


def test_generated_state_variables_do_not_swamp_the_policyengine_list(m):
    """policyengine-us generates one variable per state code. They are geography, not policy."""
    report = detect(m, _corpus_root(), ref=m.pins["rulespec_us"])
    pe = report["policyengine"]
    if "error" in pe:
        pytest.skip(f"policyengine-us unavailable: {pe['error']}")
    assert pe["generated_state_variables"] >= 50
    assert not any(len(v) == 2 and v.isupper() for v in pe["unmapped"]), (
        "two-letter state codes are counted separately, not listed as work"
    )


def test_the_cli_writes_json_a_cycle_can_read(tmp_path):
    out = tmp_path / "detect.json"
    assert main(["--corpus", str(_corpus_root()), "--ref", "origin/main",
                 "--no-policyengine", "--out", str(out)]) == 0
    report = json.loads(out.read_text())
    assert report["schema"] == "axiom_mappings.detect.v1"
    assert set(report["axiom"]) >= {"added", "removed", "added_and_unmapped", "mapped_but_absent"}


def _corpus_root():
    from pathlib import Path

    root = Path.home() / "rulespec-us"
    if not (root / ".git").exists():
        pytest.skip("no rulespec-us checkout to read")
    return root
