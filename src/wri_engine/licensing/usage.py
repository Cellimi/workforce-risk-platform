"""The usage log: what an installation did under its license keys, in a form that shows
whether it has been edited.

The licensor and a licensee need to agree how many engagements were opened and what was
done in them. The installation keeps a log, the licensee exports it, and the licensor reads
it. For that to mean anything the licensor has to be able to tell an export that was sent as
written from one that was trimmed first.

So each entry carries a hash of itself **and of the entry before it**. Remove an entry,
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

import hashlib
import json

GENESIS = "0" * 64

#: The events an installation records.
EVENTS = (
    "engagement_opened",
    "key_attached",
    "dataset_loaded",
    "report_generated",
)


def entry_hash(previous_hash: str, entry: dict) -> str:
    """The hash of one entry, bound to the one before it.

    `entry` is everything about the entry except its own two hash fields. It is written the
    same way every time -- keys sorted, no spare whitespace -- so the licensor's check and
    the installation's record agree byte for byte.
    """
    canonical = json.dumps(entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(f"{previous_hash}\n{canonical}".encode()).hexdigest()


def body_of(record: dict) -> dict:
    """An exported record without its hash fields: the part the hash is taken over."""
    return {k: v for k, v in record.items() if k not in ("previous_hash", "hash")}


def verify_chain(records: list[dict]) -> list[str]:
    """Every place an exported log fails to hold together. An empty list means it is intact.

    Checks that each entry's hash is the hash of its contents and of the entry before, that
    the first entry starts from the fixed beginning, and that the sequence numbers run
    without a gap.
    """
    problems: list[str] = []
    previous = GENESIS
    expected_seq = 1
    for position, record in enumerate(records, start=1):
        seq = record.get("seq")
        if seq != expected_seq:
            problems.append(
                f"entry {position}: sequence number is {seq}, expected {expected_seq} "
                f"(an entry is missing or out of order)"
            )
            expected_seq = seq if isinstance(seq, int) else expected_seq
        if record.get("previous_hash") != previous:
            problems.append(f"entry {position} (seq {seq}): does not follow the entry before it")
        if record.get("hash") != entry_hash(record.get("previous_hash", ""), body_of(record)):
            problems.append(f"entry {position} (seq {seq}): its contents have been changed")
        previous = record.get("hash", "")
        expected_seq += 1
    return problems


def to_jsonl(records: list[dict]) -> str:
    """The export format: one entry per line, in order."""
    return "".join(json.dumps(r, sort_keys=True) + "\n" for r in records)


def from_jsonl(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]
