"""Axiom-side identity: the map's ids are checked against RuleSpec at a commit, with a ratchet."""

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from axiom_mappings import load
from axiom_mappings.corpus import Corpus
from axiom_mappings.identity import check, findings, write_baseline
from axiom_mappings.validate import validate

from test_mappings import copy_root, edit, errors

CTC = "us:statutes/26/24/h"


def module(rules, deferred=()):
    return {
        "format": "rulespec/v1",
        "module": {"deferred_outputs": [{"output": f"{CTC}#{d}"} for d in deferred]} if deferred else {},
        "rules": [{"name": n, "kind": k} for n, k in rules],
    }


def write(root: Path, rel: str, doc) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(doc, sort_keys=False))


def git(root: Path, *args) -> str:
    return subprocess.check_output(["git", "-C", str(root), *args], text=True,
                                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}).strip()


@pytest.fixture
def corpus_repo(tmp_path):
    """A two-commit rulespec-us: the second renames the 24(h) rules and moves one to another module."""
    root = tmp_path / "rulespec-us"
    root.mkdir()
    git(root, "init", "-q")
    write(root, "us/statutes/26/24/h.yaml",
          module([("ctc_refundable_maximum", "derived"), ("ctc_joint_phase_out_threshold", "parameter")]))
    write(root, "us-co/policies/cdhs/snap/benefit.yaml", module([("snap_allotment", "derived")]))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "old names")
    old = git(root, "rev-parse", "HEAD")
    write(root, "us/statutes/26/24/h.yaml",
          module([("ctc_refundable_maximum_under_subsection_h", "derived")], deferred=["ctc_phase_in_under_subsection_h"]))
    write(root, "us/statutes/26/24/h/3.yaml", module([("ctc_joint_phase_out_threshold", "parameter")]))
    git(root, "add", "-A")
    git(root, "commit", "-qm", "rename and move")
    new = git(root, "rev-parse", "HEAD")
    return root, old, new


def test_a_ref_is_read_from_git_not_the_working_tree(corpus_repo):
    root, old, new = corpus_repo
    (root / "us/statutes/26/24/h.yaml").write_text("rules: []\n")  # uncommitted edit: ignored at a ref
    at_old, at_new = Corpus(root, old), Corpus(root, new)
    assert at_old.commit == old and at_new.commit == new
    assert at_old.resolve(f"{CTC}#ctc_refundable_maximum").status == "ok"
    assert at_old.resolve(f"{CTC}#ctc_refundable_maximum").kind == "derived"
    assert at_new.resolve(f"{CTC}#ctc_refundable_maximum_under_subsection_h").status == "ok"
    assert Corpus(root).resolve(f"{CTC}#ctc_refundable_maximum_under_subsection_h").status == "missing-rule"


def test_unresolved_ids_come_with_moved_and_renamed_candidates(corpus_repo):
    root, _, new = corpus_repo
    c = Corpus(root, new)
    renamed = c.resolve(f"{CTC}#ctc_refundable_maximum")
    assert renamed.status == "missing-rule"
    assert renamed.candidates == (f"{CTC}#ctc_refundable_maximum_under_subsection_h",)
    moved = c.resolve(f"{CTC}#ctc_joint_phase_out_threshold")
    assert moved.candidates == (f"{CTC}/3#ctc_joint_phase_out_threshold",)
    assert c.resolve(f"{CTC}#ctc_phase_in_under_subsection_h").status == "deferred"
    assert c.resolve("us:statutes/26/99#x").status == "missing-module"


def test_moves_are_only_suggested_within_a_jurisdiction(corpus_repo):
    root, _, new = corpus_repo
    assert Corpus(root, new).resolve("us-ca:policies/x#snap_allotment").candidates == ()


def test_prefix_rows_resolve_a_module_a_path_or_a_jurisdiction(corpus_repo):
    root, _, new = corpus_repo
    c = Corpus(root, new)
    assert c.resolve_prefix(f"{CTC}#").status == "ok"
    assert c.resolve_prefix("us:statutes/26/24").status == "ok"
    assert c.resolve_prefix("us-co:").status == "ok"
    assert c.resolve_prefix("us-ut:").status == "missing-prefix"
    assert c.resolve_prefix("us:statutes/26/2").status == "missing-prefix"  # a path prefix, not a string prefix
    assert c.resolve_prefix("us:statutes/26/99#").status == "missing-module"


def map_on(tmp_path, repo_root, ref, outputs=(), parameters=(), prefixes=(), programs=()):
    """A copy of the map whose Axiom-side rows are replaced by the given ones, pinned at ``ref``."""
    root = copy_root(tmp_path / "map")
    row = lambda a: {"axiom": a, "program": "tax", "type": "not_comparable", "rationale": "t"}
    edit(root, "outputs", lambda rows: rows.__setitem__(slice(None), [row(a) for a in outputs]))
    edit(root, "parameters", lambda rows: rows.__setitem__(slice(None), [
        {"axiom": a, "program": "tax", "policyengine_parameter": "gov.x"} for a in parameters]))
    edit(root, "prefixes", lambda rows: rows.__setitem__(slice(None), [
        {"axiom_prefix": a, "program": "tax", "type": "not_comparable", "rationale": "t"} for a in prefixes]))
    edit(root, "programs", lambda rows: rows.__setitem__(slice(None), list(programs)))
    edit(root, "supplied_parameters", lambda rows: rows.clear())
    edit(root, "findings", lambda rows: rows.clear())
    edit(root, "renames", lambda rows: rows.clear())
    (root / "data/us/unresolved.yaml").write_text(yaml.safe_dump({"ref": ref, "unresolved": []}))
    pins = yaml.safe_load((root / "pins.yaml").read_text())
    pins["us"]["rulespec_us"] = ref
    (root / "pins.yaml").write_text(yaml.safe_dump(pins))
    return root


def test_ratchet_new_unresolved_is_an_error_listed_is_a_warning_fixed_is_stale(tmp_path, corpus_repo):
    repo, _, new = corpus_repo
    root = map_on(tmp_path, repo, new, outputs=[f"{CTC}#ctc_refundable_maximum", f"{CTC}#ctc_refundable_maximum_under_subsection_h"])
    m = load("us", root)
    report = check(m, repo)
    assert report["ref"] == new and report["ok"] == 1
    assert {f.code for f in findings(m, report) if f.level == "error"} == {"unknown-axiom-id"}

    write_baseline(m, report, root)
    m = load("us", root)
    found = findings(m, check(m, repo))
    assert errors(found) == set() and {f.code for f in found} == {"unresolved-axiom-id"}
    assert "ctc_refundable_maximum_under_subsection_h" in found[0].message  # the candidate is shown

    edit(root, "outputs", lambda rows: rows.pop(0))  # fixed: the listed id is no longer mapped
    m = load("us", root)
    assert errors(findings(m, check(m, repo))) == {"stale-unresolved-entry"}


def test_programs_are_read_at_their_own_ref(tmp_path, corpus_repo):
    repo, old, new = corpus_repo
    program = {"id": "us/ctc", "jurisdiction": "us", "axiom": "us:statutes/26/24/h/3", "consumers": {}}
    root = map_on(tmp_path, repo, old, programs=[program])
    m = load("us", root)
    assert errors(findings(m, check(m, repo))) == {"unknown-program-module"}  # not there at the pin
    edit(root, "programs", lambda rows: rows[0].__setitem__("corpus_ref", new))
    m = load("us", root)
    assert errors(findings(m, check(m, repo))) == set()
    assigned = {"id": "us/tax", "jurisdiction": "us", "axiom": "us:tax/x", "assembled_by": "oracles", "consumers": {}}
    edit(root, "programs", lambda rows: rows.append(assigned))
    m = load("us", root)
    assert {f.code for f in findings(m, check(m, repo))} == {"program-outside-rulespec"}


def test_validator_runs_the_identity_check_with_corpus(tmp_path, corpus_repo):
    repo, _, new = corpus_repo
    root = map_on(tmp_path, repo, new, outputs=[f"{CTC}#gone"], prefixes=["us-co:", f"{CTC}#"])
    found, reports = validate("us", root, corpus=repo)
    assert "unknown-axiom-id" in errors(found)
    assert reports["identity"]["unresolved"] == 1 and reports["identity"]["ok"] == 2


def test_renames_must_move_the_row(tmp_path):
    root = copy_root(tmp_path)
    frm, to = "us:statutes/26/24/h#ctc_refundable_maximum", "us:statutes/26/24/h#ctc_refundable_maximum_under_subsection_h"
    assert load("us", root).current_id(frm) == to
    edit(root, "outputs", lambda rows: next(r for r in rows if r["axiom"] == to).__setitem__("axiom", frm))
    found, _ = validate("us", root)
    assert {"rename-target-unmapped", "rename-source-still-mapped"} <= errors(found)


RULESPEC = Path(os.environ.get("RULESPEC_US", Path.home() / "rulespec-us"))


def test_the_committed_map_resolves_at_its_pin():
    m = load("us")
    pin = m.pins["rulespec_us"]
    if not (RULESPEC / ".git").exists() or subprocess.run(["git", "-C", str(RULESPEC), "cat-file", "-e", pin]).returncode:
        pytest.skip(f"needs rulespec-us with commit {pin} (set RULESPEC_US)")
    report = check(m, RULESPEC)
    assert errors(findings(m, report)) == set()
    assert m.unresolved_ref == pin
    assert {p["id"]: p["found"] for p in report["programs"]}["us-co/snap-fy2026"] is True
    assert f"{CTC}#ctc_refundable_maximum_under_subsection_h" not in {u["axiom"] for u in report["unresolved"]}
