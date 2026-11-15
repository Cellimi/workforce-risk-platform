"""The access audit log shows when it has been edited.

What it must catch: a line changed, removed, inserted or moved. What it can catch only with
help: entries removed from the end, or a log rebuilt from scratch, which need a head noted
down earlier. What it must never do: report an edit that did not happen, whether after a
restart, under requests arriving together, or on a log written before entries were chained.
"""

from __future__ import annotations

import json
import threading

import pytest

from test_accounts_engagements import CLIENT, OWNER, Installation
from wri_engine import admin_cli, hashchain
from wri_engine.access import audit as audit_module
from wri_engine.access.audit import AuditLog
from wri_engine.settings import Settings


def write(log: AuditLog, n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        log.record(role="executive", endpoint=f"/costs/summary/{i}", user=f"u{i}@firm.example")


def lines(log: AuditLog) -> list[str]:
    return log.path.read_text().splitlines()


def rewrite(log: AuditLog, new_lines: list[str]) -> None:
    log.path.write_text("".join(line + "\n" for line in new_lines))


@pytest.fixture
def log(tmp_path):
    return AuditLog(tmp_path / "audit_log.jsonl")


# ---------------------------------------------------------------------------
# a log left alone holds together
# ---------------------------------------------------------------------------
def test_each_entry_is_numbered_and_tied_to_the_one_before(log):
    write(log, 5)
    records = [json.loads(line) for line in lines(log)]
    assert [r["seq"] for r in records] == [1, 2, 3, 4, 5]
    assert records[0]["previous_hash"] == hashchain.GENESIS
    assert all(b["previous_hash"] == a["hash"] for a, b in zip(records, records[1:], strict=False))
    found = log.check()
    assert found.intact and found.entries == 5 and found.unchained == 0
    assert (found.head_seq, found.head_hash) == (5, records[-1]["hash"])
    # What a request asked for is still all there, in plain sight.
    assert records[2]["endpoint"] == "/costs/summary/2" and records[2]["user"] == "u2@firm.example"


def test_a_restart_carries_on_the_same_chain(log):
    write(log, 3)
    audit_module._HEADS.clear()  # what a new process starts with
    write(AuditLog(log.path), 3, start=3)
    found = log.check()
    assert found.intact and found.head_seq == 6


def test_requests_arriving_together_never_break_the_chain(log):
    """Many threads, and two log objects on the same file, as the API and the account
    routes are: one chain, every number used once."""
    other = AuditLog(log.path)
    threads = [
        threading.Thread(target=write, args=(log if i % 2 else other, 25, i * 25)) for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    found = log.check()
    assert found.intact, found.problems[:3]
    assert sorted(json.loads(x)["seq"] for x in lines(log)) == list(range(1, 201))


def test_a_log_from_before_entries_were_chained_is_kept_and_protected(log):
    """An installation upgraded to this version keeps its older lines as they are. The first
    chained entry is tied to all of them, so they cannot be changed afterwards either."""
    old = [
        json.dumps({"timestamp": f"2026-01-0{i}", "endpoint": "/x", "role": "admin"})
        for i in range(1, 4)
    ]
    rewrite(log, old)
    write(log, 2)
    found = log.check()
    assert found.intact and (found.unchained, found.entries, found.head_seq) == (3, 2, 5)

    tampered = lines(log)
    tampered[1] = tampered[1].replace("/x", "/y")
    rewrite(log, tampered)
    assert any("does not follow the entry before it" in p for p in log.check().problems)


def test_a_line_cut_short_by_a_crash_is_reported_and_the_log_carries_on(log):
    write(log, 2)
    with log.path.open("a") as fh:
        fh.write('{"seq": 3, "endpoint": "/cut sh')
    audit_module._HEADS.clear()
    write(AuditLog(log.path), 1, start=2)
    found = log.check()
    assert found.problems == ["line 3: is not a whole entry (cut short, or edited)"]
    assert json.loads(lines(log)[-1])["seq"] == 3  # the next whole entry, on its own line


def test_an_empty_or_missing_log_is_intact(log):
    found = log.check()
    assert found.intact and found.entries == 0 and found.head_hash == hashchain.GENESIS


# ---------------------------------------------------------------------------
# every edit in the middle shows
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "edit, expected",
    [
        ("change", "its contents have been changed"),
        ("remove", "an entry is missing or out of order"),
        ("insert", "an entry is missing or out of order"),
        ("swap", "an entry is missing or out of order"),
        ("outcome", "its contents have been changed"),
    ],
)
def test_an_edit_in_the_middle_shows(log, edit, expected):
    write(log, 6)
    current = lines(log)
    if edit == "change":
        current[2] = current[2].replace("u2@firm.example", "someone-else@firm.example")
    elif edit == "remove":
        del current[3]
    elif edit == "insert":
        current.insert(2, current[2])
    elif edit == "swap":
        current[1], current[4] = current[4], current[1]
    elif edit == "outcome":
        record = json.loads(current[2])
        record["outcome"] = "denied"
        current[2] = json.dumps(record, sort_keys=True)
    rewrite(log, current)
    found = log.check()
    assert not found.intact
    assert any(expected in p for p in found.problems), found.problems


def test_rewriting_one_entry_with_a_fresh_hash_still_shows(log):
    """Recomputing the edited entry's own hash is not enough: the next one points at the old
    hash. Only rebuilding everything after it hides the edit, which the head catches."""
    write(log, 5)
    current = [json.loads(x) for x in lines(log)]
    forged = dict(current[1], user="someone-else@firm.example")
    current[1] = hashchain.seal(forged["previous_hash"], forged["seq"], hashchain.body_of(
        {k: v for k, v in forged.items() if k not in ("seq",)}))  # fmt: skip
    rewrite(log, [json.dumps(r, sort_keys=True) for r in current])
    assert any("does not follow the entry before it" in p for p in log.check().problems)


# ---------------------------------------------------------------------------
# the end of the log, and a rebuilt log, need a head noted earlier
# ---------------------------------------------------------------------------
def test_entries_removed_from_the_end_are_caught_by_an_earlier_head(log):
    write(log, 6)
    noted = log.check()
    assert log.holds(noted.head_seq, noted.head_hash)
    rewrite(log, lines(log)[:4])
    # The chain on its own still holds: this is the case the head is for.
    assert log.check().intact
    assert not log.holds(noted.head_seq, noted.head_hash)


def test_a_log_rebuilt_from_scratch_is_caught_by_an_earlier_head(log):
    write(log, 4)
    noted = log.check()
    records = [json.loads(x) for x in lines(log)]
    previous, rebuilt = hashchain.GENESIS, []
    for record in records:
        body = {k: v for k, v in hashchain.body_of(record).items() if k not in ("seq",)}
        if body["user"] == "u1@firm.example":
            body["outcome"] = "denied"
        sealed = hashchain.seal(previous, record["seq"], body)
        rebuilt.append(json.dumps(sealed, sort_keys=True))
        previous = sealed["hash"]
    rewrite(log, rebuilt)
    assert log.check().intact
    assert not log.holds(noted.head_seq, noted.head_hash)


# ---------------------------------------------------------------------------
# wri-admin
# ---------------------------------------------------------------------------
def run_admin(monkeypatch, settings, *argv):
    monkeypatch.setattr(admin_cli.Settings, "from_env", classmethod(lambda cls: settings))
    return admin_cli.main(list(argv))


def test_the_admin_tool_checks_the_log_and_an_earlier_head(monkeypatch, capsys, tmp_path):
    settings = Settings(auth_mode="local", data_dir=tmp_path)
    log = AuditLog(settings.audit_log_path)
    write(log, 4)
    assert run_admin(monkeypatch, settings, "audit") == 0
    out = capsys.readouterr().out
    head = log.check()
    assert f"head                4:{head.head_hash}" in out and "intact" in out

    write(log, 2, start=4)
    assert run_admin(monkeypatch, settings, "audit", "--expect-head", f"4:{head.head_hash}") == 0
    capsys.readouterr()
    rewrite(log, lines(log)[:3])
    assert run_admin(monkeypatch, settings, "audit", "--expect-head", f"4:{head.head_hash}") == 1
    said = capsys.readouterr()
    assert "no longer holds entry 4" in said.err and "DOES NOT HOLD TOGETHER" in said.out
    assert run_admin(monkeypatch, settings, "audit", "--expect-head", "four") == 2

    edited = lines(log)
    edited[1] = edited[1].replace("u1@", "x1@")
    rewrite(log, edited)
    assert run_admin(monkeypatch, settings, "audit") == 1
    assert "its contents have been changed" in capsys.readouterr().err


def test_status_reports_the_audit_log(monkeypatch, capsys, tmp_path):
    from wri_engine.store import Store

    settings = Settings(auth_mode="local", data_dir=tmp_path)
    store = Store(settings.database_url)
    store.create_user(*OWNER, is_firm_admin=True)
    store.close()
    log = AuditLog(settings.audit_log_path)
    write(log, 3)
    assert run_admin(monkeypatch, settings, "status") == 0
    assert "audit log           intact, 3 entries; head 3:" in capsys.readouterr().out

    edited = lines(log)
    edited[0] = edited[0].replace("u0@", "x0@")
    rewrite(log, edited)
    assert run_admin(monkeypatch, settings, "status") == 1
    assert "the audit log does not hold together" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------
def test_a_firm_administrator_can_see_whether_the_log_holds(monkeypatch, tmp_path):
    from wri_engine.api import main

    monkeypatch.setattr(main, "AUDIT", AuditLog(tmp_path / "audit.jsonl"))
    site = Installation(tmp_path / "site")
    site.add_user(CLIENT)
    site.http.get("/engagements", headers=site.auth(CLIENT))
    body = site.http.get("/audit/check", headers=site.auth(OWNER)).json()
    assert body["intact"] is True and body["entries"] >= 1
    assert len(body["head"]["hash"]) == 64
    assert site.http.get("/audit/check", headers=site.auth(CLIENT)).status_code == 403
    assert site.http.get("/audit/check").status_code == 401
    # Asking is itself recorded.
    assert json.loads(lines(main.AUDIT)[-1])["endpoint"] in ("/audit/check", "firm-admin action")
