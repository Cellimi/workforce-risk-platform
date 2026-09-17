"""Append-only access log.

Every request that reaches the engine is written here before the response is built: who
asked (role), what they asked for (endpoint and filters), when, and whether it was allowed.
One JSON object per line, opened in append mode and never rewritten.

This is the demo's version of an audit trail. A production deployment would ship these
records to a store the application cannot edit.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from wri_engine.paths import AUDIT_LOG_FILE

_LOCK = threading.Lock()


@dataclass(frozen=True)
class AuditEntry:
    timestamp: str
    role: str
    endpoint: str
    outcome: str
    filters: dict = field(default_factory=dict)
    detail: dict = field(default_factory=dict)


class AuditLog:
    def __init__(self, path: Path | None = None):
        self.path = Path(path or AUDIT_LOG_FILE)

    def record(
        self,
        *,
        role: str,
        endpoint: str,
        outcome: str = "allowed",
        filters: dict | None = None,
        detail: dict | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            timestamp=datetime.now(UTC).isoformat(),
            role=str(role),
            endpoint=endpoint,
            outcome=outcome,
            filters=filters or {},
            detail=detail or {},
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(asdict(entry), sort_keys=True, default=str)
        with _LOCK, open(self.path, "a") as fh:
            fh.write(line + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def tail(self, limit: int = 50) -> list[dict]:
        if not self.path.exists():
            return []
        with open(self.path) as fh:
            lines = fh.readlines()[-limit:]
        return [json.loads(line) for line in lines]
