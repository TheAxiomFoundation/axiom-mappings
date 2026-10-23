"""One pass of the mapping cycle: detect, propose, report, and open a pull request.

    python -m axiom_mappings cycle --corpus ~/rulespec-us [--ref SHA] [--pr] [--out DIR]

The stages already exist as commands. This chains them, writes one report, and
offers it for review. It is deliberately the dullest part of the pipeline: it
decides nothing.

    1. detect     what moved since the pins
    2. identity   do the mapped ids still resolve
    3. propose    candidates for the rules that arrived, into `proposals/`
    4. coverage   where every variable stands, and what blocks it
    5. agreement  do both harnesses tell the same story about each binding
    6. report     one JSON and one Markdown summary
    7. pull request, when asked

**It proposes, it never promotes.** `accept` writes a proposal into the map and
`promote` moves a binding to `on`; both stay human, and promotion still needs
population evidence. A cycle that could promote would make the review that
guards a wrong number optional.

Two comparisons judge a binding, and they are not the same. axiom-oracles runs
the Axiom engine against PolicyEngine directly; policyengine-axiom runs it
inside PolicyEngine. Both should agree, and when they do not the mapping is
wrong rather than the rule. Neither runs here: each owns its own corpus and a
population of tens of thousands of rows, and a run is an hour. The `agreement`
stage reads what each filed, joins it to the map and flags the difference no
one has named; see `agreement.py`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import ROOT, load
from .agreement import FLAGGED, VERDICTS, agreement
from .detect import detect
from .export.coverage import STAGES, coverage
from .readiness import readiness

PROPOSALS = ROOT.parent / "proposals"


def _module_prefixes(ids: list[str], depth: int = 4) -> list[str]:
    """The module prefixes a set of new rule ids falls under, so propose runs once per area."""
    prefixes = set()
    for axiom_id in ids:
        module = axiom_id.split("#", 1)[0]
        jurisdiction, _, path = module.partition(":")
        parts = path.split("/")
        prefixes.add(f"{jurisdiction}:{'/'.join(parts[:depth])}")
    return sorted(prefixes)


def _propose(country: str, corpus: str, prefixes: list[str], limit: int) -> list[dict[str, Any]]:
    """Run `propose outputs` for each area, and report what it wrote."""
    from .__main__ import propose as run_propose

    written = []
    for prefix in prefixes:
        name = prefix.replace(":", "__").replace("/", "__")
        argv = ["outputs", "--country", country, "--corpus", corpus, "--module", prefix,
                "--limit", str(limit), "--name", f"cycle-{name}"]
        try:
            code = run_propose(argv)
        except Exception as error:  # noqa: BLE001 - one bad area must not end the pass
            written.append({"module": prefix, "error": f"{type(error).__name__}: {error}"})
            continue
        path = PROPOSALS / "outputs" / f"cycle-{name}.yaml"
        written.append({
            "module": prefix,
            "exit": code,
            "proposal": str(path.relative_to(ROOT.parent)) if path.exists() else None,
        })
    return written


def _alignment(report: dict[str, Any]) -> list[str]:
    """What the two harnesses filed, and what neither has named.

    A missing source is stated, not absorbed: no ledger must never read as
    "nothing failed".
    """
    lines = ["", "## Alignment", ""]
    missing = [side for side, source in report["sources"].items() if source.get("error")]
    if missing:
        for side in missing:
            lines.append(f"- **{side}: {report['sources'][side]['error']}.** "
                         "The verdicts below count it as no evidence, not as agreement.")
        lines.append("")
    totals = report["totals"]
    lines.append(f"{totals['bindings']} binding(s), **{totals['flagged']} flagged**. "
                 f"{totals['dispositions_about_unbound_concepts']} disposition(s) are about "
                 "concepts no binding names yet.")
    lines += ["", "| verdict | bindings |", "|---|---|"]
    lines += [f"| {verdict} | {totals['by_verdict'][verdict]} |" for verdict in VERDICTS
              if totals["by_verdict"][verdict]]
    noted = totals["by_verdict"]["noted_in_the_map"]
    if noted:
        lines += ["", f"{noted} further binding(s) differ with a note in the map and no second "
                  "harness confirming it. A note is a claim; a disposition is evidence."]
    flagged = [row for row in report["bindings"] if row["verdict"] in FLAGGED]
    if flagged:
        lines += ["", "| variable | program | status | verdict | inside PE |", "|---|---|---|---|---|"]
        for row in flagged:
            pe = row["in_policyengine"] or {}
            inside = (f"{pe.get('tolerance_failures')} row(s) over tolerance, "
                      f"max {pe.get('max_abs_diff')}") if pe else "no run"
            lines.append(f"| `{row['policyengine_variable']}` | `{row['program']}` | "
                         f"{row['status']} | {row['verdict']} | {inside} |")
    return lines


def _markdown(report: dict[str, Any]) -> str:
    axiom = report["detect"]["axiom"]
    cov = report["coverage"]["totals"]
    pe = report["detect"].get("policyengine") or {}
    lines = [
        f"# Mapping cycle, {report['ran_at'][:10]}",
        "",
        f"Map release `{report['map_release']}`. Nothing here is accepted or promoted; "
        "every proposal waits for a reviewer.",
        "",
        "## What moved",
        "",
        f"- RuleSpec: pinned `{report['detect']['pinned_ref'][:9]}`, checked "
        f"`{str(report['detect']['checked_ref'])[:9]}`"
        + ("" if report["detect"]["moved"] else " (unchanged)"),
        f"- Added {len(axiom['added'])} rules, removed {len(axiom['removed'])}; "
        f"{len(axiom['added_and_unmapped'])} added rules have no mapping",
        f"- {len(axiom['mapped_but_absent'])} mapped ids are absent at the checked ref",
    ]
    if pe and "error" not in pe:
        lines.append(
            f"- PolicyEngine {pe['installed_version']}: {pe['named_by_the_map']} of "
            f"{pe['variables']} variables named by the map"
        )
    lines += [
        "",
        "## Coverage",
        "",
        "| stage | variables |",
        "|---|---|",
    ]
    lines += [f"| {stage} | {cov['by_stage'][stage]} |" for stage in STAGES]
    lines += [f"| **blocked** | **{cov['blocked']}** |", "", "## Proposals", ""]
    if report["proposals"]:
        for entry in report["proposals"]:
            if entry.get("error"):
                lines.append(f"- `{entry['module']}`: {entry['error']}")
            else:
                lines.append(f"- `{entry['module']}` -> `{entry['proposal']}`")
    elif report["pin_is_behind"]:
        lines.append(
            f"Not proposed. {len(report['blocked_on_pin'])} rule(s) arrived after the map's pin "
            f"`{report['detect']['pinned_ref'][:9]}`, and `propose` reads the corpus at the pin, so a "
            "candidate for them would be a mapping `identity` rejects. **Bump `pins.yaml` to "
            f"`{str(report['detect']['checked_ref'])[:9]}` and run the cycle again.** Moving the pin "
            "changes the whole map's basis, so it is a reviewed act, not something a cycle does."
        )
    elif not report["proposed"]:
        # Say which it is. "None" alone reads as "nothing arrived", which is
        # the opposite of the truth when rules arrived and proposing was off.
        lines.append(f"Not run (`--no-propose`). {len(axiom['added_and_unmapped'])} added "
                     "rule(s) have no mapping and are waiting.")
    else:
        lines.append("None: no rule arrived that the map does not already name.")
    lines += _alignment(report["agreement"])
    blocked = [v for v in report["coverage"]["variables"] if v["blocked_by"]]
    if blocked:
        lines += ["", "## Blocked", "", "| variable | stage | blocked by |", "|---|---|---|"]
        for v in blocked[:25]:
            lines.append(f"| `{v['policyengine']}` | {v['stage']} | {'; '.join(v['blocked_by'])} |")
        if len(blocked) > 25:
            lines.append(f"| ... | | {len(blocked) - 25} more |")
    lines += ["", "## Review", "",
              "1. Read each proposal and set `accept:` on the candidate that is right, or none.",
              "2. `python -m axiom_mappings accept proposals/<kind>/<name>.yaml`.",
              "3. A binding moves to `on` only through `promote`, on population evidence."]
    return "\n".join(lines) + "\n"


def run(country: str, corpus: str, ref: str | None, *, limit: int = 3,
        propose_new: bool = True, ledger: str | None = None,
        dispositions: str | None = None) -> dict[str, Any]:
    m = load(country)
    found = detect(m, corpus, ref)
    ready = {}
    for program in m.programs:
        try:
            ready[program] = readiness(m, program, None, None)
        except Exception:  # noqa: BLE001 - readiness it cannot judge is simply unknown
            ready[program] = {}
    proposals: list[dict[str, Any]] = []
    new_ids = found["axiom"]["added_and_unmapped"]
    # `propose` reads the corpus at the map's pin, by design: a candidate for a
    # rule the pin does not have would be a mapping `identity` then rejects. So
    # a rule that arrived after the pin is not proposable until the pin moves,
    # and moving it is a reviewed act, because it changes the whole map's basis.
    pin_is_behind = found["moved"] and bool(new_ids)
    if propose_new and new_ids and not pin_is_behind:
        proposals = _propose(country, corpus, _module_prefixes(new_ids), limit)
    return {
        "schema": "axiom_mappings.cycle.v1",
        "ran_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "map_release": m.release,
        "detect": found,
        "coverage": coverage(m, readiness_by_program=ready),
        "agreement": agreement(m, ledger_path=ledger, dispositions_root=dispositions),
        "proposals": proposals,
        "proposed": propose_new,
        "pin_is_behind": pin_is_behind,
        "blocked_on_pin": new_ids if pin_is_behind else [],
    }


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def open_pull_request(report: dict[str, Any], body: str, *, branch: str | None = None) -> str | None:
    """Commit the report and any proposals on a branch, push, and open a PR for review."""
    repo = ROOT.parent
    branch = branch or f"cycle/{report['ran_at'][:10]}"
    if not _git(["status", "--porcelain"], repo):
        return None
    _git(["checkout", "-b", branch], repo)
    _git(["add", "-A"], repo)
    subject = (f"Mapping cycle {report['ran_at'][:10]}: "
               f"{len(report['detect']['axiom']['added'])} rules added, "
               f"{len(report['proposals'])} proposal file(s)")
    subprocess.run(["git", "commit", "-q", "-m", subject, "-m", body], cwd=repo, check=True)
    _git(["push", "-u", "origin", branch], repo)
    created = subprocess.run(
        ["gh", "pr", "create", "--base", "main", "--head", branch, "--title", subject, "--body", body],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()
    return created.splitlines()[-1] if created else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="axiom_mappings cycle", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--country", default="us")
    ap.add_argument("--corpus", required=True, help="a rulespec-us checkout, read with git only")
    ap.add_argument("--ref", default=None, help="the commit to check; defaults to the map's pin")
    ap.add_argument("--limit", type=int, default=3, help="candidates per proposal")
    ap.add_argument("--no-propose", action="store_true", help="report only; write no proposals")
    ap.add_argument("--out", type=Path, default=Path("reports"), help="directory for the report")
    ap.add_argument("--ledger", default=None, help="policyengine-axiom's ledger.json")
    ap.add_argument("--dispositions", default=None, help="axiom-oracles' dispositions directory")
    ap.add_argument("--pr", action="store_true", help="commit, push and open a pull request for review")
    args = ap.parse_args(argv)

    report = run(args.country, args.corpus, args.ref, limit=args.limit,
                 propose_new=not args.no_propose, ledger=args.ledger, dispositions=args.dispositions)
    body = _markdown(report)

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = report["ran_at"][:10]
    (args.out / f"cycle-{stamp}.json").write_text(json.dumps(report, indent=2) + "\n")
    (args.out / f"cycle-{stamp}.md").write_text(body)
    print(body)
    print(f"wrote {args.out / f'cycle-{stamp}.json'}")

    if args.pr:
        url = open_pull_request(report, body)
        print(f"pull request: {url}" if url else "nothing changed, so no pull request")
    return 0


if __name__ == "__main__":
    sys.exit(main())
