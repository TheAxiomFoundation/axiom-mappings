"""RuleSpec at one commit, so the map's Axiom ids can be checked against the corpus they name.

An Axiom id is ``<jurisdiction>:<module path>#<rule name>``. Its module is the file
``<jurisdiction>/<module path>.yaml`` in rulespec-us, and the rule is an entry of that module's
``rules`` (or a ``deferred_outputs`` entry, which names an output the module has not encoded yet).

``Corpus(root, ref)`` reads a git checkout at ``ref`` through ``git cat-file``, so the working tree
and branch are never touched and any commit can be read. Without ``ref`` it reads the files under
``root`` directly, which is what vendored test slices use.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Any, Iterable

import yaml

try:
    _Loader = yaml.CSafeLoader
except AttributeError:  # pragma: no cover - libyaml missing
    _Loader = yaml.SafeLoader

JURISDICTION = re.compile(r"^us(-[a-z]{2})?$")
RULE_NAME = re.compile(rb"^\s*-?\s*name:\s*['\"]?([A-Za-z_][A-Za-z0-9_]*)['\"]?\s*$", re.M)


def split_id(axiom_id: str) -> tuple[str, str]:
    """``us:statutes/26/24/h#name`` -> (``us:statutes/26/24/h``, ``name``)."""
    module, _, name = axiom_id.partition("#")
    return module, name


def module_path(module_id: str) -> str:
    jurisdiction, _, rest = module_id.partition(":")
    return f"{jurisdiction}/{rest}.yaml"


def module_id(path: str) -> str:
    jurisdiction, _, rest = path.partition("/")
    return f"{jurisdiction}:{rest.removesuffix('.yaml')}"


@dataclass(frozen=True)
class Resolution:
    axiom: str
    status: str  # ok | deferred | missing-module | missing-rule
    kind: str | None = None  # the rule's kind when it resolves: parameter, derived, ...
    candidates: tuple[str, ...] = ()  # suggestions for an unresolved id; never applied without review

    @property
    def resolved(self) -> bool:
        return self.status in ("ok", "deferred")


class Corpus:
    def __init__(self, root: str | Path, ref: str | None = None):
        self.root = Path(root).expanduser()
        self._ref = ref
        self._modules: dict[str, dict[str, Any] | None] = {}

    @cached_property
    def commit(self) -> str | None:
        """The full commit sha read, or None for a plain directory."""
        if self._ref is None:
            return None
        out = subprocess.run(["git", "-C", str(self.root), "rev-parse", "--verify", f"{self._ref}^{{commit}}"],
                             capture_output=True, text=True)
        if out.returncode:
            raise ValueError(f"{self.root}: no commit {self._ref!r}: {out.stderr.strip()}")
        return out.stdout.strip()

    def _read(self, paths: list[str]) -> dict[str, bytes | None]:
        if not paths:
            return {}
        if self.commit is None:
            return {p: (self.root / p).read_bytes() if (self.root / p).is_file() else None for p in paths}
        proc = subprocess.run(["git", "-C", str(self.root), "cat-file", "--batch"], check=True, capture_output=True,
                              input="".join(f"{self.commit}:{p}\n" for p in paths).encode())
        data, i, out = proc.stdout, 0, {}
        for p in paths:
            end = data.index(b"\n", i)
            header, i = data[i:end].decode(), end + 1
            if header.endswith(" missing"):
                out[p] = None
                continue
            size = int(header.split()[2])
            out[p], i = data[i:i + size], i + size + 1
        return out

    @cached_property
    def module_ids(self) -> list[str]:
        return [module_id(p) for p in self._paths()]

    def _paths(self) -> list[str]:
        if self.commit is None:
            return sorted(str(p.relative_to(self.root)) for j in self.root.iterdir()
                          if j.is_dir() and JURISDICTION.match(j.name) for p in j.rglob("*.yaml"))
        out = subprocess.run(["git", "-C", str(self.root), "ls-tree", "-r", "--name-only", self.commit],
                             check=True, capture_output=True, text=True).stdout.split()
        return [p for p in out if p.endswith(".yaml") and JURISDICTION.match(p.partition("/")[0])]

    def load(self, module_ids: Iterable[str]) -> None:
        """Read and parse these modules in one pass (``module`` reads one at a time otherwise)."""
        wanted = sorted({m for m in module_ids if m not in self._modules})
        for path, body in self._read([module_path(m) for m in wanted]).items():
            doc = yaml.load(body, Loader=_Loader) if body is not None else None
            self._modules[module_id(path)] = doc if isinstance(doc, dict) else None  # a list is test data, not a module

    def module(self, module_id_: str) -> dict[str, Any] | None:
        if module_id_ not in self._modules:
            self.load([module_id_])
        return self._modules[module_id_]

    def rules(self, module_id_: str) -> dict[str, dict[str, Any]] | None:
        doc = self.module(module_id_)
        if doc is None:
            return None
        return {r["name"]: r for r in doc.get("rules") or [] if isinstance(r, dict) and r.get("name")}

    def closure(self, module_id_: str) -> list[str]:
        """The module and every module it imports, transitively, in first-seen order."""
        seen: dict[str, None] = {}
        todo = [module_id_]
        while todo:
            mid = todo.pop(0)
            if mid in seen:
                continue
            seen[mid] = None
            doc = self.module(mid) or {}
            todo += [i if isinstance(i, str) else i.get("module", "") for i in doc.get("imports") or []]
        return list(seen)

    def closure_names(self, module_id_: str) -> set[str]:
        """Every rule name a program can read: its module's and its imports'."""
        names: set[str] = set()
        for mid in self.closure(module_id_):
            names |= set(self.rules(mid) or {})
        return names

    def deferred(self, module_id_: str) -> set[str]:
        doc = self.module(module_id_) or {}
        return {split_id(d["output"])[1] for d in (doc.get("module") or {}).get("deferred_outputs") or []
                if isinstance(d, dict) and d.get("output")}

    @cached_property
    def name_index(self) -> dict[str, list[str]]:
        """rule name -> every module defining it, from a text scan of the whole corpus (for suggestions)."""
        paths = self._paths()
        index: dict[str, list[str]] = {}
        for path, body in self._read(paths).items():
            for name in dict.fromkeys(n.decode() for n in RULE_NAME.findall(body or b"")):
                index.setdefault(name, []).append(module_id(path))
        return index

    def candidates(self, axiom_id: str, limit: int = 5) -> tuple[str, ...]:
        """Where an unresolved id may have gone: the same name in another module of its jurisdiction
        (moved), or a name in its own module that extends or shortens it (renamed)."""
        module, name = split_id(axiom_id)
        jurisdiction = module.partition(":")[0]
        moved = [f"{m}#{name}" for m in self.name_index.get(name, []) if m != module and m.partition(":")[0] == jurisdiction]
        rules = self.rules(module) or {}
        renamed = [f"{module}#{n}" for n in rules if n != name and name and (n.startswith(name) or n.endswith(name) or
                                                                               name.startswith(n) or name.endswith(n))]
        return tuple(sorted(moved) + sorted(renamed, key=lambda c: (len(c), c)))[:limit]

    def resolve(self, axiom_id: str, suggest: bool = True) -> Resolution:
        module, name = split_id(axiom_id)
        rules = self.rules(module)
        if rules is not None and name in rules:
            return Resolution(axiom_id, "ok", rules[name].get("kind"))
        if rules is not None and name in self.deferred(module):
            return Resolution(axiom_id, "deferred")
        status = "missing-module" if rules is None else "missing-rule"
        return Resolution(axiom_id, status, candidates=self.candidates(axiom_id) if suggest else ())

    def resolve_prefix(self, prefix: str) -> Resolution:
        """A prefix row covers every output of a module (``module#``) or of a path (``us:statutes/42/426``)."""
        module, hash_, _ = prefix.partition("#")
        if hash_:
            return Resolution(prefix, "ok" if self.module(module) is not None else "missing-module")
        stem = prefix if prefix.endswith((":", "/")) else prefix + "/"  # `us-ut:` is a whole jurisdiction
        found = any(m == prefix or m.startswith(stem) for m in self.module_ids)
        return Resolution(prefix, "ok" if found else "missing-prefix")

    def resolve_all(self, axiom_ids: Iterable[str], suggest: bool = True) -> dict[str, Resolution]:
        ids = list(dict.fromkeys(axiom_ids))
        self.load(split_id(i)[0] for i in ids)
        return {i: self.resolve(i, suggest) for i in ids}
