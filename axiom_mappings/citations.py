"""Legal citations, normalised so Axiom and PolicyEngine can be matched by the law they encode.

A citation is ``(kind, title, section, subsections)``: 26 USC 24(h)(5)(A) is
``Citation("usc", "26", "24", ("h", "5", "A"))``; 7 CFR 273.9(d)(1) is ``Citation("cfr", "7", "273.9",
("d", "1"))``; Rev. Proc. 2025-32 is ``Citation("revproc", "2025", "32")``. They are read from Axiom module
ids and rule ``source`` text, and from PolicyEngine reference titles and links (Cornell LII, eCFR, IRS).
Two citations of the same section relate as ``exact`` (same subsection path), ``within`` (one path
extends the other) or ``section`` (same section, different subsections).

Only federal USC, CFR and revenue procedures are read so far; state codes cite in too many forms to
normalise reliably, so a state rule simply has no citation and gets no citation-based proposal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

RELATIONS = ("exact", "within", "section")  # strongest first


@dataclass(frozen=True)
class Citation:
    kind: str
    title: str
    section: str
    subs: tuple[str, ...] = ()

    def __str__(self) -> str:
        path = "".join(f"({s})" for s in self.subs)
        if self.kind == "usc":
            return f"{self.title} USC {self.section}{path}"
        if self.kind == "cfr":
            return f"{self.title} CFR {self.section}{path}"
        return f"Rev. Proc. {self.title}-{self.section}"

    @property
    def key(self) -> tuple[str, str, str]:
        return self.kind, self.title, self.section

    def relation(self, other: Citation) -> str | None:
        if self.key != other.key:
            return None
        if self.subs == other.subs:
            return "exact"
        shorter, longer = sorted((self.subs, other.subs), key=len)
        return "within" if longer[:len(shorter)] == shorter else "section"


_SUBS = re.compile(r"\(\s*([A-Za-z0-9]{1,6})\s*\)")
_USC = re.compile(r"(\d+)\s*U\.?\s*S\.?\s*C(?:ode)?\.?\s*(?:[Ss]ec(?:tion)?\.?\s*|§+\s*)?(\d+[A-Za-z]{0,2}(?:-\d+)?)"
                  r"((?:\s*\(\s*[A-Za-z0-9]{1,6}\s*\))*)")
_CFR = re.compile(r"(\d+)\s*C\.?\s*F\.?\s*R\.?\s*(?:§+\s*|[Ss]ec(?:tion)?\.?\s*|[Pp]art\s+)?(\d+)(?:\.(\d+[A-Za-z]?))?"
                  r"((?:\s*\(\s*[A-Za-z0-9]{1,6}\s*\))*)")
_REVPROC = re.compile(r"Rev(?:enue)?\.?\s*Proc(?:edure)?\.?\s*(\d{2,4})\s*-\s*(\d+)")
_CORNELL_USC = re.compile(r"law\.cornell\.edu/uscode/text/(\d+)/([0-9A-Za-z-]+)(?:#([0-9A-Za-z_]+))?")
_CORNELL_CFR = re.compile(r"law\.cornell\.edu/cfr/text/(\d+)/(\d+)(?:\.(\d+[A-Za-z]?))?(?:#([0-9A-Za-z_]+))?")
_ECFR = re.compile(r"ecfr\.gov/\S*?title-(\d+)\S*?/section-(\d+)(?:\.(\d+[A-Za-z]?))?(?:#p-[\d.]+((?:\([A-Za-z0-9]+\))*))?")
_ECFR_PART = re.compile(r"ecfr\.gov/\S*?title-(\d+)\S*?/part-(\d+)(?:[/#?]|$)")
_IRS_RP = re.compile(r"irs\.gov/\S*?/rp-?(\d{2})-(\d+)\.pdf")
_MODULE_SUB = re.compile(r"^[0-9A-Za-z]{1,4}$")


def _year(y: str) -> str:
    return y if len(y) == 4 else f"20{y}"


def _subs(text: str) -> tuple[str, ...]:
    return tuple(_SUBS.findall(text or ""))


def from_text(text: str) -> list[Citation]:
    """Every citation stated in free text (a rule's ``source``, a reference title)."""
    out = []
    for title, section, subs in _USC.findall(text or ""):
        out.append(Citation("usc", title, section, _subs(subs)))
    for title, part, sec, subs in _CFR.findall(text or ""):
        out.append(Citation("cfr", title, f"{part}.{sec}" if sec else part, _subs(subs)))
    for year, number in _REVPROC.findall(text or ""):
        out.append(Citation("revproc", _year(year), number))
    return out


def from_link(href: str) -> list[Citation]:
    """The citation a link points at (Cornell LII, eCFR, an IRS revenue procedure PDF)."""
    out = []
    if m := _CORNELL_USC.search(href or ""):
        out.append(Citation("usc", m.group(1), m.group(2), tuple(p for p in (m.group(3) or "").split("_") if p)))
    if m := _CORNELL_CFR.search(href or ""):
        section = f"{m.group(2)}.{m.group(3)}" if m.group(3) else m.group(2)
        out.append(Citation("cfr", m.group(1), section, tuple(p for p in (m.group(4) or "").split("_") if p)))
    if m := _ECFR.search(href or ""):
        section = f"{m.group(2)}.{m.group(3)}" if m.group(3) else m.group(2)
        out.append(Citation("cfr", m.group(1), section, _subs(m.group(4) or "")))
    elif m := _ECFR_PART.search(href or ""):
        out.append(Citation("cfr", m.group(1), m.group(2)))
    if m := _IRS_RP.search(href or ""):
        out.append(Citation("revproc", _year(m.group(1)), m.group(2)))
    return out


def from_module(module_id: str) -> list[Citation]:
    """The citation an Axiom module id encodes: ``us:statutes/26/24/h``, ``us:regulations/7-cfr/273/9``,
    ``us:policies/irs/rev-proc-2025-32/...``. State modules give none."""
    jurisdiction, _, path = module_id.partition(":")
    if jurisdiction != "us":
        return []
    parts = path.split("/")
    if parts[0] == "statutes" and len(parts) >= 3 and parts[1].isdigit():
        subs = []
        for p in parts[3:]:
            if not _MODULE_SUB.match(p):
                break
            subs.append(p)
        return [Citation("usc", parts[1], parts[2], tuple(subs))]
    if parts[0] == "regulations" and len(parts) >= 4 and parts[1].endswith("-cfr"):
        subs = []
        for p in parts[4:]:
            if not _MODULE_SUB.match(p):
                break
            subs.append(p)
        return [Citation("cfr", parts[1].removesuffix("-cfr"), f"{parts[2]}.{parts[3]}", tuple(subs))]
    if m := re.search(r"rev-proc-(\d{4})-(\d+)", path):
        return [Citation("revproc", m.group(1), m.group(2))]
    return []


def of_rule(module_id: str, rule: dict[str, Any]) -> list[Citation]:
    """An Axiom rule's citations: its ``source`` text (the finer one) and its module id."""
    return list(dict.fromkeys(from_text(str(rule.get("source") or "")) + from_module(module_id)))


def references(metadata: Any) -> list[tuple[str, str]]:
    """(title, link) pairs from a PolicyEngine ``reference`` field in any of its shapes."""
    raw = metadata.get("reference") if isinstance(metadata, dict) else metadata
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else [raw]
    out = []
    for item in items:
        if isinstance(item, dict):
            title, href = item.get("title"), item.get("href")
            out.append((title if isinstance(title, str) else "", href if isinstance(href, str) else ""))
        elif isinstance(item, str):
            out.append(("", item) if item.startswith("http") else (item, ""))
    return out


def of_references(refs: Iterable[tuple[str, str]]) -> list[Citation]:
    out = []
    for title, href in refs:
        out += from_text(title) + from_link(href)
    return list(dict.fromkeys(out))


def best(axiom: Iterable[Citation], policyengine: Iterable[Citation]) -> tuple[str, Citation, Citation] | None:
    """The strongest relation between two citation sets, with the pair that makes it."""
    found = [(rel, a, p) for a in axiom for p in policyengine if (rel := a.relation(p))]
    return min(found, key=lambda t: RELATIONS.index(t[0]), default=None)
