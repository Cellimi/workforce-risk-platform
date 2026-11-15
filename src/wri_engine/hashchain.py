"""A log whose entries are chained, so that editing it afterwards shows.

Each entry carries a hash of its own contents **and of the entry before it**. Change an
entry, remove one, insert one or swap two, and every hash from that point on stops matching.
`verify` walks the entries and says where the chain breaks.

Two logs use this: the access audit log (`access/audit.py`) and the license usage log
(`licensing/usage.py`).

What that gives, stated plainly
-------------------------------
It makes editing **evident**, not impossible. Someone who can write the file can rebuild the
whole chain from the first entry onward, and nothing inside the same machine can stop that.
What they cannot do is alter or remove an entry in the middle and leave the rest standing.
Removing entries from the very end also leaves a valid chain; that is caught only by
comparing with a fingerprint of the log's last entry ("the head") taken earlier and kept
somewhere else.
"""

from __future__ import annotations

import hashlib
import json

GENESIS = "0" * 64


def entry_hash(previous_hash: str, entry: dict) -> str:
    """The hash of one entry, bound to the one before it.

    `entry` is everything about the entry except its own two hash fields, written the same
    way every time -- keys sorted, no spare whitespace -- so whoever checks the log and
    whoever wrote it agree byte for byte.
    """
    canonical = json.dumps(
        entry, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(f"{previous_hash}\n{canonical}".encode()).hexdigest()


def body_of(record: dict) -> dict:
    """A record without its hash fields: the part the hash is taken over."""
    return {k: v for k, v in record.items() if k not in ("previous_hash", "hash")}


def seal(previous_hash: str, seq: int, entry: dict) -> dict:
    """An entry ready to be written: numbered, linked to the one before, and hashed."""
    body = {**entry, "seq": seq, "previous_hash": previous_hash}
    return {**body, "hash": entry_hash(previous_hash, body_of(body))}


def verify(records: list[dict], *, start_hash: str = GENESIS, start_seq: int = 1) -> list[str]:
    """Every place a run of entries fails to hold together. Empty means it is intact.

    Checks that each entry's hash is the hash of its contents and of the entry before, that
    the first entry follows `start_hash`, and that the sequence numbers run without a gap.
    """
    problems: list[str] = []
    previous = start_hash
    expected_seq = start_seq
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
