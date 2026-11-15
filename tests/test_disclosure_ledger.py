"""The differencing guard's memory: kept per person, on disk, across restarts.

The attack this defends against: ask for a figure over one group of employees, ask again
over a slightly smaller group, subtract. The ledger refuses the second answer when the
difference is too small a group. These tests hold it to doing that when the two questions
are asked either side of a restart, which used to wipe it.
"""

from __future__ import annotations

import json
import stat
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from test_accounts_engagements import ANALYST, CLIENT, LEAD, OWNER, Installation, override
from wri_engine.aggregation.ledger_file import LedgerFile, basis_of, fingerprint_files
from wri_engine.aggregation.suppression import DisclosureLedger, SuppressionReason
from wri_engine.api.main import create_app
from wri_engine.settings import Settings

DIFFERENCING = SuppressionReason.DIFFERENCING


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def group(prefix: str, n: int) -> frozenset[str]:
    return frozenset(f"{prefix}{i:03d}" for i in range(n))


def test_a_ledger_file_gives_back_what_was_released_under_the_same_figures(tmp_path):
    first = LedgerFile(tmp_path, "user:7", basis="figures-a")
    first.append([group("E", 20), group("F", 6)])
    first.append([group("G", 9)])
    assert first.path == tmp_path / "user-7.jsonl"
    # Readable by the product's own account only: it lists the client's employee ids.
    assert stat.S_IMODE(first.path.stat().st_mode) == 0o600
    assert LedgerFile(tmp_path, "user:7", "figures-a").load() == [
        group("E", 20),
        group("F", 6),
        group("G", 9),
    ]
    # Figures worked out another way start afresh, and the earlier lines are left in place.
    other = LedgerFile(tmp_path, "user:7", "figures-b")
    assert other.load() == []
    other.append([group("H", 5)])
    assert LedgerFile(tmp_path, "user:7", "figures-a").load()[-1] == group("G", 9)
    assert LedgerFile(tmp_path, "user:8", "figures-a").load() == []


def test_a_line_cut_short_by_a_crash_is_skipped(tmp_path):
    ledger = LedgerFile(tmp_path, "user:1", "b")
    ledger.append([group("E", 10)])
    with ledger.path.open("a") as handle:
        handle.write('{"basis":"b","cohort":["E0')
    assert LedgerFile(tmp_path, "user:1", "b").load() == [group("E", 10)]


def test_the_file_holds_employee_ids_and_nothing_else(tmp_path):
    LedgerFile(tmp_path, "user:3", "b").append([group("E", 6)])
    entry = json.loads((tmp_path / "user-3.jsonl").read_text())
    assert set(entry) == {"basis", "cohort"}
    assert entry["cohort"] == sorted(group("E", 6))


@pytest.mark.parametrize("person", ["../x", "user:1/../../y", "", "a" * 80, "user 1"])
def test_a_ledger_name_cannot_point_outside_its_folder(tmp_path, person):
    with pytest.raises(ValueError, match="not a usable ledger name"):
        LedgerFile(tmp_path, person, "b")


def test_the_basis_follows_the_files_and_the_threshold_only():
    base = dict(data_fingerprint="d1", sector="public_sector", min_cell_size=5)
    assert basis_of(**base) == basis_of(**dict(base))
    for changed in ({"data_fingerprint": "d2"}, {"sector": "private_sector"}, {"min_cell_size": 7}):
        assert basis_of(**{**base, **changed}) != basis_of(**base), changed
    # Client-specific values and the occupation group are not part of it, by decision.
    with pytest.raises(TypeError):
        basis_of(**base, overrides={"x": 1})
    with pytest.raises(TypeError):
        basis_of(**base, occupation_group="sales")


def test_a_file_fingerprint_follows_the_bytes(tmp_path):
    (tmp_path / "a.csv").write_text("x\n1\n")
    first = fingerprint_files(tmp_path)
    assert fingerprint_files(tmp_path) == first
    (tmp_path / "a.csv").write_text("x\n2\n")
    assert fingerprint_files(tmp_path) != first


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------
def test_a_response_is_kept_in_one_write_before_it_leaves():
    writes = []
    ledger = DisclosureLedger(min_cell_size=5, keep=writes.append)
    with ledger.releasing():
        ledger.record(group("E", 20))
        ledger.record(group("F", 20))
        ledger.record(group("E", 20))  # the same group twice is one entry
        assert writes == []
    assert writes == [[group("E", 20), group("F", 20)]]
    ledger.record(group("G", 20))
    assert writes[-1] == [group("G", 20)]
    with ledger.releasing():
        pass
    assert len(writes) == 2


def test_if_the_record_cannot_be_kept_nothing_is_released():
    """Fail closed. A ledger that could not be written must not let the figures out, and the
    groups stay remembered in memory, which is the cautious side."""

    def full_disk(_cohorts):
        raise OSError("no space left on device")

    ledger = DisclosureLedger(min_cell_size=5, keep=full_disk)
    with pytest.raises(OSError, match="no space"), ledger.releasing():
        ledger.record(group("E", 20))
    assert ledger.would_expose(group("E", 22))


def test_two_requests_at_once_cannot_both_slip_through():
    """Each of these on its own is safe to release; together they differ by two people. The
    check and the record are one step, so exactly one gets through."""
    ledger = DisclosureLedger(min_cell_size=5)
    released = []
    start = threading.Barrier(2)

    def ask(cohort):
        start.wait()
        with ledger.releasing():
            if not ledger.would_expose(cohort):
                time.sleep(0.05)  # widen the window a race would need
                ledger.record(cohort)
                released.append(cohort)

    threads = [threading.Thread(target=ask, args=(c,)) for c in (group("E", 20), group("E", 22))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(released) == 1


# ---------------------------------------------------------------------------
# the attack, through the API, either side of a restart
# ---------------------------------------------------------------------------
@pytest.fixture
def site(tmp_path):
    site = Installation(tmp_path)
    for person in (LEAD, ANALYST, CLIENT):
        site.add_user(person)
    site.engagement = site.new_engagement("FY26", "Pine Valley County")
    site.add_member(site.engagement, LEAD, "admin")
    site.add_member(site.engagement, CLIENT, "executive")
    site.add_member(site.engagement, ANALYST, "hr_analyst")
    assert site.upload_sample(site.engagement, LEAD).status_code == 200
    return site


def ask(http, headers, engagement, years):
    response = http.get(
        f"/engagements/{engagement}/costs/matrix",
        params={
            "rows": "role_family",
            "cols": "misconduct_category",
            "filters": json.dumps({"year": years}),
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def blocked(matrix) -> set[tuple[str, str]]:
    return {
        (c["row"], c["col"])
        for c in matrix["cells"]
        if c.get("suppressed") and c.get("suppression_reason") == DIFFERENCING
    }


def years_of(site) -> list[str]:
    meta = site.http.get(f"/engagements/{site.engagement}/meta", headers=site.auth(OWNER))
    years = sorted({str(a.decision_date.year) for a in site.runtime.workspaces.get(
        site.engagement).dataset.actions})  # fmt: skip
    assert meta.status_code == 200 and len(years) >= 2
    return years


def restarted(tmp_path, who):
    http = TestClient(create_app(Settings(auth_mode="local", data_dir=tmp_path)))
    token = http.post("/auth/login", json={"email": who[0], "password": who[1]}).json()["token"]
    return http, {"Authorization": f"Bearer {token}"}


def test_a_restart_between_two_overlapping_questions_changes_nothing(site, tmp_path):
    """The same two questions, once in one sitting and once with a restart between them,
    must get exactly the same answers. Before this change the second sitting got what a
    person with no history gets, and the subtraction went through."""
    years = years_of(site)
    e = site.engagement
    # What someone with no history is shown for the narrow question. A cell can be held back
    # even then, because two cells in one table can differ by a person or two.
    fresh = ask(site.http, site.auth(LEAD), e, years[:-1])

    # Without a restart: the analyst asks wide, then narrow.
    ask(site.http, site.auth(ANALYST), e, years)
    in_one_sitting = ask(site.http, site.auth(ANALYST), e, years[:-1])
    assert blocked(in_one_sitting) > blocked(fresh), (
        "the sample must exercise the guard for this test to mean anything"
    )

    # With one: the client reader asks wide, the product restarts, then narrow.
    ask(site.http, site.auth(CLIENT), e, years)
    http, headers = restarted(tmp_path, CLIENT)
    after_restart = ask(http, headers, e, years[:-1])
    assert blocked(after_restart) == blocked(in_one_sitting)
    assert after_restart["cells"] == in_one_sitting["cells"]


def test_each_person_has_their_own_record(site, tmp_path):
    years = years_of(site)
    e = site.engagement
    ask(site.http, site.auth(CLIENT), e, years)
    folder = tmp_path / "engagements" / e / "disclosure"
    client_id = site.store.get_user_by_email(CLIENT[0]).id
    assert [p.name for p in folder.iterdir()] == [f"user-{client_id}.jsonl"]
    fresh = ask(site.http, site.auth(LEAD), e, years[:-1])
    # Someone who has asked nothing yet is not held back by what another was shown.
    http, headers = restarted(tmp_path, ANALYST)
    assert ask(http, headers, e, years[:-1])["cells"] == fresh["cells"]


def test_a_changed_client_value_does_not_reset_the_record(site, tmp_path):
    """A client-specific value moves some figures and leaves the rest as they were, so what a
    person was shown before the change still counts after it, across a restart too."""
    years = years_of(site)
    e = site.engagement
    ask(site.http, site.auth(CLIENT), e, years)

    assert override(site, e, "c4_outside_counsel_hourly_rate", "450").status_code == 200
    fresh_on_new_figures = ask(site.http, site.auth(LEAD), e, years[:-1])
    on_new_figures = ask(site.http, site.auth(CLIENT), e, years[:-1])
    assert blocked(on_new_figures) > blocked(fresh_on_new_figures)

    http, headers = restarted(tmp_path, CLIENT)
    assert blocked(ask(http, headers, e, years[:-1])) == blocked(on_new_figures)


def test_a_changed_occupation_group_does_not_reset_the_record(site):
    """The same holds for the occupation group, which moves the benefits multiplier and
    leaves every other cost where it was."""
    years = years_of(site)
    e = site.engagement
    ask(site.http, site.auth(CLIENT), e, years)
    fresh = ask(site.http, site.auth(LEAD), e, years[:-1])
    site.runtime.workspaces.get(e).set_occupation_group(None)  # re-reads the files
    assert blocked(ask(site.http, site.auth(CLIENT), e, years[:-1])) > blocked(fresh)


def test_a_new_client_file_starts_a_new_record(site, tmp_path):
    """Different files are different figures, and the record starts again. The earlier one
    is kept on disk."""
    years = years_of(site)
    e = site.engagement
    ask(site.http, site.auth(CLIENT), e, years)
    state = site.runtime.workspaces.get(e)
    before = state._data_fingerprint
    (state.default_source / "notes.txt").write_text("a file the consultant added\n")
    state.load()
    assert state._data_fingerprint != before
    fresh = ask(site.http, site.auth(LEAD), e, years[:-1])
    assert ask(site.http, site.auth(CLIENT), e, years[:-1])["cells"] == fresh["cells"]
    folder = tmp_path / "engagements" / e / "disclosure"
    client = site.store.get_user_by_email(CLIENT[0]).id
    bases = {
        json.loads(x)["basis"] for x in (folder / f"user-{client}.jsonl").read_text().splitlines()
    }
    assert len(bases) == 2


def test_the_record_is_in_the_engagement_folder_and_not_the_database(site, tmp_path):
    """A list of employee ids is client data. It stays beside the client's files."""
    years = years_of(site)
    ask(site.http, site.auth(CLIENT), site.engagement, years)
    folder = tmp_path / "engagements" / site.engagement / "disclosure"
    lines = [json.loads(x) for f in folder.iterdir() for x in f.read_text().splitlines()]
    ids = {i for line in lines for i in line["cohort"]}
    assert ids and all(i.startswith("E") for i in ids)
    database = (tmp_path / "wri.db").read_bytes()
    assert not any(i.encode() in database for i in list(ids)[:50])


def test_a_backup_carries_the_record(site, tmp_path):
    import tarfile

    from wri_engine.backup import create_backup

    years = years_of(site)
    ask(site.http, site.auth(CLIENT), site.engagement, years)
    out = tmp_path.parent / f"{tmp_path.name}-backup.tar.gz"
    from datetime import datetime

    create_backup(site.settings, out, now=datetime(2026, 11, 16))
    with tarfile.open(out) as archive:
        names = archive.getnames()
    client_id = site.store.get_user_by_email(CLIENT[0]).id
    assert f"engagements/{site.engagement}/disclosure/user-{client_id}.jsonl" in names
    out.unlink()


def test_the_walkthrough_keeps_its_ledgers_in_memory(tmp_path):
    from wri_engine.api.state import STATE

    assert STATE.ledger_dir is None
    assert STATE.ledger("executive").keep is None


def test_files_land_only_where_the_workspace_says(site, tmp_path):
    state = site.runtime.workspaces.get(site.engagement)
    assert (
        state.ledger_dir
        == Path(tmp_path / "engagements" / site.engagement / "disclosure").resolve()
    )
