"""Where a person's disclosure ledger is kept between restarts.

The differencing guard (see `suppression.py`) works only if it remembers every group of
employees a person has already been shown a figure for. Held only in memory, that record was
lost whenever the product restarted, and two overlapping views taken either side of a
restart could be subtracted to isolate a small group. This file keeps it.

What is kept, and where
-----------------------
One file per person per engagement, in the engagement's own folder:

    <data>/engagements/<engagement id>/disclosure/user-<user id>.jsonl

Each line is one group of employees released to that person: the client's own employee ids,
and nothing else. No money, no names, no other field from the client's records. It is kept
beside the client's files rather than in the database, because a list of employee ids is
client data (CLAUDE.md rule 12), and so a backup of the engagement carries it.

Each line also records the **basis** it was released under: a fingerprint of the client's
files, the sector and the minimum cell size. Only lines with the current basis are read back.
Lines from an earlier basis are left in place, not deleted; if the engagement returns to that
basis, they apply again.

What is deliberately NOT in the basis: the engagement's client-specific assumption values and
its occupation group. Changing either moves some figures and leaves others exactly as they
were (an outside counsel rate moves only the counsel fees), so a person shown figures before
and after the change could still subtract the parts that did not move. The record therefore
carries across those changes. Decided by the owner on 2026-11-15.

Writing is done before the figures leave the server, and is flushed to disk. If it fails, the
request fails and nothing is released. A line half-written when the machine stopped is
skipped when the file is read; the response it belonged to was never sent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

_SAFE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def basis_of(*, data_fingerprint: str, sector: str, min_cell_size: int) -> str:
    """A short fingerprint of what a released group is only meaningful against: the
    client's files, the sector and the minimum cell size. Client-specific values and the
    occupation group are left out on purpose; see the module docstring."""
    body = json.dumps(
        {"data": data_fingerprint, "sector": sector, "min_cell_size": min_cell_size},
        sort_keys=True,
    )
    return hashlib.sha256(body.encode()).hexdigest()[:24]


def fingerprint_files(folder: Path) -> str:
    """A fingerprint of the files an engagement's figures were read from."""
    digest = hashlib.sha256()
    for path in sorted(p for p in Path(folder).iterdir() if p.is_file()):
        digest.update(path.name.encode() + b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


class LedgerFile:
    """One person's released groups for one engagement, under one basis."""

    def __init__(self, folder: Path, person: str, basis: str):
        name = person.replace(":", "-")
        if not _SAFE.match(name):
            raise ValueError(f"not a usable ledger name: {person!r}")
        self.path = Path(folder) / f"{name}.jsonl"
        self.basis = basis

    def load(self) -> list[frozenset[str]]:
        """Every group released under this basis, in the order released."""
        if not self.path.exists():
            return []
        groups = []
        for line in self.path.read_text().splitlines():
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line cut short when the machine stopped
            if isinstance(entry, dict) and entry.get("basis") == self.basis:
                groups.append(frozenset(str(i) for i in entry.get("cohort", [])))
        return groups

    def append(self, cohorts: Iterable[frozenset[str]]) -> None:
        """Add groups, and make sure they are on disk before returning."""
        lines = "".join(
            json.dumps({"basis": self.basis, "cohort": sorted(c)}, separators=(",", ":")) + "\n"
            for c in cohorts
        )
        if not lines:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a") as handle:
            handle.write(lines)
            handle.flush()
            os.fsync(handle.fileno())
