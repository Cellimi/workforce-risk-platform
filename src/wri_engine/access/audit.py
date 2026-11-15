"""The access audit log: who asked for what, and whether it was allowed.

Every request that reaches the engine is written here before the response is built: who
asked (user and role), in which engagement, what they asked for (endpoint and filters),
when, and whether it was allowed. One JSON object per line, appended and flushed to disk,
never rewritten.

Each entry is chained to the one before it (`wri_engine.hashchain`): it carries a sequence
number, the hash of the entry before, and its own hash. Change a line, delete one, insert one
or swap two, and `check` says where. That makes editing **evident**; it does not make it
impossible. Someone who can write the file can rebuild the chain from the start, and removing
the last few entries leaves a valid chain. Both are caught by keeping the log's **head** --
its last sequence number and hash, printed by `wri-admin audit` -- somewhere the server's
administrators cannot change, and comparing later. The install guide says how.

A log written before entries were chained keeps those lines as they are. The first chained
entry is linked to a hash of everything before it, so those older lines cannot be changed
afterwards without that showing too.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from wri_engine import hashchain
from wri_engine.paths import AUDIT_LOG_FILE

_LOCK = threading.Lock()
#: Where each log file's chain has got to, by path. Shared by every `AuditLog` on the same
#: file in this process, so two of them can never write two entries with the same number.
_HEADS: dict[Path, tuple[int, str]] = {}


@dataclass(frozen=True)
class AuditEntry:
    timestamp: str
    role: str
    endpoint: str
    outcome: str
    user: str = ""
    engagement: str = ""
    filters: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


@dataclass(frozen=True)
class AuditCheck:
    """What `check` found."""

    entries: int
    #: Lines written before entries were chained, protected as one block.
    unchained: int
    problems: list[str]
    #: The last entry's sequence number and hash. Write these down somewhere else.
    head_seq: int
    head_hash: str

    @property
    def intact(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict:
        return {
            "intact": self.intact,
            "entries": self.entries,
            "unchained": self.unchained,
            "problems": self.problems,
            "head": {"seq": self.head_seq, "hash": self.head_hash},
        }


def _lines(path: Path) -> list[str]:
    return path.read_text().splitlines() if path.exists() else []


def _is_chained(line: str) -> bool:
    try:
        record = json.loads(line)
    except ValueError:
        return False
    return isinstance(record, dict) and "hash" in record and "seq" in record


def _prefix_hash(lines: list[str]) -> str:
    """The hash that links the first chained entry to everything written before it."""
    if not lines:
        return hashchain.GENESIS
    return hashlib.sha256("".join(line + "\n" for line in lines).encode()).hexdigest()


def _find_head(path: Path) -> tuple[int, str]:
    """Where the chain in a file has got to: the last entry's number and hash."""
    lines = _lines(path)
    for line in reversed(lines):
        if _is_chained(line):
            record = json.loads(line)
            return record["seq"], record["hash"]
    return len(lines), _prefix_hash(lines)


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or AUDIT_LOG_FILE)

    def record(
        self,
        *,
        role: str,
        endpoint: str,
        outcome: str = "allowed",
        user: str = "",
        engagement: str = "",
        filters: dict | None = None,
        detail: dict | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            timestamp=datetime.now(UTC).isoformat(),
            role=str(role),
            endpoint=endpoint,
            outcome=outcome,
            user=user,
            engagement=engagement,
            filters=filters or {},
            detail=detail or {},
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        key = self.path.resolve()
        with _LOCK:
            if key not in _HEADS:
                self._end_cleanly()
                _HEADS[key] = _find_head(self.path)
            seq, previous = _HEADS[key]
            sealed = hashchain.seal(
                previous, seq + 1, json.loads(json.dumps(asdict(entry), default=str))
            )
            line = json.dumps(sealed, sort_keys=True)
            with open(self.path, "a") as fh:
                fh.write(line + "\n")
                fh.flush()
                os.fsync(fh.fileno())
            _HEADS[key] = (sealed["seq"], sealed["hash"])
        return entry

    def _end_cleanly(self) -> None:
        """A line cut short when the machine stopped is left as it is, and a new line is
        started after it, so the next entry is never glued onto it. `check` reports it."""
        if self.path.exists() and self.path.stat().st_size:
            with open(self.path, "rb") as fh:
                fh.seek(-1, os.SEEK_END)
                last = fh.read(1)
            if last != b"\n":
                with open(self.path, "a") as fh:
                    fh.write("\n")

    def tail(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path) as fh:
            lines = fh.readlines()[-limit:]
        return [json.loads(line) for line in lines]

    def holds(self, seq: int, entry_hash: str) -> bool:
        """Whether the log still holds entry `seq` exactly as it was when its hash was
        noted. With the chain intact, that entry's hash vouches for every one before it."""
        for line in _lines(self.path):
            if _is_chained(line):
                record = json.loads(line)
                if record["seq"] == seq:
                    return record["hash"] == entry_hash
        return False

    def check(self) -> AuditCheck:
        """Read the whole log and say whether it still holds together."""
        lines = _lines(self.path)
        first = next((i for i, line in enumerate(lines) if _is_chained(line)), len(lines))
        unchained, chained_lines = lines[:first], lines[first:]
        problems: list[str] = []
        records = []
        for offset, line in enumerate(chained_lines, start=first + 1):
            try:
                record = json.loads(line)
            except ValueError:
                problems.append(f"line {offset}: is not a whole entry (cut short, or edited)")
                continue
            if not isinstance(record, dict) or "hash" not in record:
                problems.append(f"line {offset}: is not a chained entry")
                continue
            records.append(record)
        problems += hashchain.verify(
            records, start_hash=_prefix_hash(unchained), start_seq=len(unchained) + 1
        )
        head_seq, head_hash = (
            (records[-1].get("seq", 0), records[-1].get("hash", ""))
            if records
            else (len(unchained), _prefix_hash(unchained))
        )
        return AuditCheck(
            entries=len(records),
            unchained=len(unchained),
            problems=problems,
            head_seq=head_seq,
            head_hash=head_hash,
        )
