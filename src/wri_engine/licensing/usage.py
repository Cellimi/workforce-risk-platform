"""The usage log: what an installation did under its license keys, in a form that shows
whether it has been edited.

The licensor and a licensee need to agree how many engagements were opened and what was
done in them. The installation keeps a log, the licensee exports it, and the licensor reads
it. For that to mean anything the licensor has to be able to tell an export that was sent as
written from one that was trimmed first.

So each entry carries a hash of itself **and of the entry before it** (see
`wri_engine.hashchain`). Remove an entry,
change one, or reorder two, and every hash from that point on stops matching. `verify_chain`
walks an export and says where the chain breaks.

What that does and does not give
--------------------------------
It makes editing **evident**, not impossible. Someone with the database can rebuild the
whole chain from the first entry, and nothing offline can stop that; what they cannot do is
alter the middle of a log and leave the end intact. Read alongside the keys that were
issued, a log that is short, restarts, or is missing a key the licensor knows about is
itself the finding.

The log holds counts and never content: how many employees a dataset had, not who they were.
No client HR data is in it (CLAUDE.md rule 12).
"""

from __future__ import annotations

import json

from wri_engine.hashchain import GENESIS, body_of, entry_hash
from wri_engine.hashchain import verify as _verify

__all__ = [
    "EVENTS",
    "GENESIS",
    "body_of",
    "entry_hash",
    "from_jsonl",
    "to_jsonl",
    "verify_chain",
]

#: The events an installation records.
EVENTS = (
    "engagement_opened",
    "key_attached",
    "dataset_loaded",
    "report_generated",
)


def verify_chain(records: list[dict]) -> list[str]:
    """Every place an exported log fails to hold together. An empty list means it is intact.
    The chain itself is `wri_engine.hashchain`, shared with the access audit log."""
    return _verify(records)


def to_jsonl(records: list[dict]) -> str:
    """The export format: one entry per line, in order."""
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)


def from_jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]
