"""Phase 3: keeping an appraisal with an engagement, with what was found on site.

What is held here: that a cause is chosen by a person from a short fixed list and the
product offers none; that words are kept only with "Other" and only a line of them; that
every finding says whose it is; that what is kept lives in the engagement's own folder,
survives a restart, is never rewritten or removed, and is never seen from another
engagement; that the saving shown beside it is always today's; and that only roles which
see records may read or write any of it.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from test_accounts_engagements import ANALYST, CLIENT, LEAD, OUTSIDER, Installation
from test_disclosure_ledger import restarted
from test_interventions import proposal
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.interventions import (
    AppraisalBook,
    KeptAppraisal,
    clean_text,
    finding_problems,
    load_causes,
)
from wri_engine.interventions.findings import CAUSES_FILE, FILE_NAME
from wri_engine.paths import CONFIG_DIR, PROJECT_ROOT, SYNTHETIC_DIR

ALLOWED = load_causes()
IDS = r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b"


# ---------------------------------------------------------------------------
# the list of causes
# ---------------------------------------------------------------------------
def test_the_causes_are_a_short_list_in_a_file_with_one_that_takes_words():
    """Decided by the owner on 2026-11-16: a short list to choose from, with "Other" and a
    line of words. The four named causes are the ones his program overview gives."""
    assert CAUSES_FILE == CONFIG_DIR / "interventions.yaml"
    assert [c.id for c in ALLOWED.causes] == [
        "commuting",
        "childcare",
        "training",
        "scheduling",
        "other",
    ]
    assert [c.id for c in ALLOWED.causes if c.takes_text] == ["other"]
    assert ALLOWED.other_text_max_length == 300
    for cause in ALLOWED.causes:
        assert cause.label.strip() and cause.description.strip()
    # Code reads the list and never redefines it.
    for path in (PROJECT_ROOT / "src" / "wri_engine").rglob("*.py"):
        code = path.read_text()
        assert "Commuting burden" not in code and "Inflexible scheduling" not in code, path.name


def test_a_file_with_no_causes_is_an_error_not_an_empty_list(tmp_path):
    empty = tmp_path / "interventions.yaml"
    empty.write_text("causes: []\nother_text_max_length: 300\n")
    with pytest.raises(ValueError, match="lists no causes"):
        load_causes(empty)


def test_what_makes_a_finding():
    ok = finding_problems
    assert ok(["commuting"], "", ALLOWED) == []
    assert ok(["commuting", "scheduling"], "", ALLOWED) == []
    assert ok(["other"], "The night shift has no supervisor on site", ALLOWED) == []
    assert ok(["training", "other"], "  New   radios,\nno instruction  ", ALLOWED) == []

    # The product suggests no cause, so none chosen is not a finding.
    assert ok([], "", ALLOWED) == [
        "say what the investigation found: choose at least one cause. An appraisal is kept "
        "as the record of a finding, and this product does not suggest one"
    ]
    assert ok(["other"], "   ", ALLOWED) == ['say what "Other" is, in a line']
    # Words are kept only with "Other".
    assert ok(["commuting"], "They all drive in from the coast", ALLOWED) == [
        'words are kept only with "Other": choose it, or clear the words'
    ]
    assert ok(["weather"], "", ALLOWED) == ["weather is not a cause on the list"]
    assert ok(["commuting", "commuting"], "", ALLOWED) == ["a cause can be chosen once"]
    long = ok(["other"], "x" * 301, ALLOWED)
    assert long == [
        '"Other" is a line, not a set of notes: at most 300 characters, and this is 301'
    ]
    assert ok(["other"], "x" * 300, ALLOWED) == []
    assert clean_text("  New   radios,\nno instruction  ") == "New radios, no instruction"


# ---------------------------------------------------------------------------
# the book: one file, only ever added to
# ---------------------------------------------------------------------------
def kept(appraisal_id: str = "a1", **changes) -> KeptAppraisal:
    values = {
        "id": appraisal_id,
        "row": "caseworker",
        "col": "North Office",
        "topic": "tardiness",
        "intervention": proposal(),
        "causes": ("scheduling", "other"),
        "other_cause": "The first bus arrives after the shift starts",
        "entered_by": "Dana Lead (lead@firm.example)",
        "entered_at": "2026-11-16T15:00:00+00:00",
        **changes,
    }
    return KeptAppraisal(**values)


def test_what_is_kept_comes_back_exactly(tmp_path):
    book = AppraisalBook(tmp_path / "interventions")
    assert book.everything() == [] and not (tmp_path / "interventions").exists()
    book.keep(kept("a1"))
    book.keep(kept("a2", topic="", causes=("commuting",), other_cause=""))
    again = AppraisalBook(tmp_path / "interventions")  # as after a restart
    assert again.current() == [
        kept("a1"),
        kept("a2", topic="", causes=("commuting",), other_cause=""),
    ]
    back = again.current()[0].intervention
    assert back == proposal() and isinstance(back.one_time_cost, Decimal)
    with pytest.raises(ValueError, match="already kept"):
        again.keep(kept("a1"))


def test_withdrawing_adds_a_line_and_removes_nothing(tmp_path):
    book = AppraisalBook(tmp_path)
    book.keep(kept("a1"))
    book.keep(kept("a2"))
    file = tmp_path / FILE_NAME
    before = file.read_text()
    assert book.withdraw("a1", by="Sam Analyst", at="2026-11-17T09:00:00+00:00") is True
    after = file.read_text()
    assert after.startswith(before), "what was written is never rewritten"
    lines = [json.loads(line) for line in after.splitlines()]
    assert [line["event"] for line in lines] == ["kept", "kept", "withdrawn"]
    assert [a.id for a in book.current()] == ["a2"]
    gone = book.everything()[0]
    assert gone.is_withdrawn and gone.withdrawn_by == "Sam Analyst"
    assert gone.other_cause == kept().other_cause, "the record of it stays"
    # There is nothing to withdraw twice, or at all.
    assert book.withdraw("a1", by="x", at="y") is False
    assert book.withdraw("nope", by="x", at="y") is False
    assert file.read_text() == after


def test_a_line_half_written_when_the_machine_stopped_is_skipped(tmp_path):
    book = AppraisalBook(tmp_path)
    book.keep(kept("a1"))
    with (tmp_path / FILE_NAME).open("a") as handle:
        handle.write('{"event": "kept", "id": "a2", "row": "case')
    assert [a.id for a in AppraisalBook(tmp_path).current()] == ["a1"]


def test_the_file_holds_no_person_and_no_money_from_the_records(tmp_path):
    """A unit, the chosen causes, the consultant's figures and who entered them. No
    employee, no action, and no figure from the cost baseline."""
    book = AppraisalBook(tmp_path)
    book.keep(kept("a1"))
    text = (tmp_path / FILE_NAME).read_text()
    assert not re.search(IDS, text)
    line = json.loads(text)
    assert set(line) == {
        "event",
        "id",
        "row",
        "col",
        "topic",
        "causes",
        "other_cause",
        "entered_by",
        "entered_at",
        "intervention",
    }
    assert "net" not in line["intervention"] and "saving" not in text


def test_without_a_folder_it_is_kept_in_memory_only():
    book = AppraisalBook()
    book.keep(kept("a1"))
    assert [a.id for a in book.current()] == ["a1"]
    assert book.withdraw("a1", by="x", at="y") and book.current() == []
    assert AppraisalBook().everything() == []


# ---------------------------------------------------------------------------
# the API, in the synthetic walkthrough
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    STATE.load(SYNTHETIC_DIR / "sample")
    STATE._appraisals = None
    yield TestClient(app)
    STATE._appraisals = None


def role(name: str) -> dict:
    return {"X-WRI-Role": name}


def body_for(**changes) -> dict:
    first = STATE.run(mode="base").action_costs[0].dimensions
    return {
        "role_family": first["role_family"],
        "work_location": first["work_location"],
        "misconduct_category": first["misconduct_category"],
        "name": "Flexible start times",
        "one_time_cost": "2000",
        "annual_cost_per_person": "50",
        "people_covered": 10,
        "years": 2,
        "reduction_low": "0.1",
        "reduction_expected": "0.3",
        "reduction_high": "0.5",
        "basis": "What the site visit found.",
        "causes": ["scheduling", "other"],
        "other_cause": "  The first bus arrives\nafter the shift starts ",
        **changes,
    }


def test_the_list_of_causes_is_served(client):
    served = client.get("/interventions/causes", headers=role("hr_analyst")).json()
    assert [c["id"] for c in served["causes"]] == [c.id for c in ALLOWED.causes]
    assert served["other_text_max_length"] == 300
    assert served["causes"][-1] == {
        "id": "other",
        "label": "Other",
        "description": ALLOWED.get("other").description,
        "takes_text": True,
    }


def test_an_appraisal_is_kept_with_whose_finding_it_is(client):
    admin = role("admin")
    assert client.get("/interventions", headers=admin).json()["kept"] == []
    made = client.post("/interventions", json=body_for(), headers=admin)
    assert made.status_code == 200, made.text
    one = made.json()
    assert one["finding"]["causes"] == [
        {"id": "scheduling", "label": "Inflexible scheduling"},
        {"id": "other", "label": "Other"},
    ]
    assert one["finding"]["other_cause"] == "The first bus arrives after the shift starts"
    assert one["finding"]["entered_by"] == "Walkthrough user (admin)"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", one["finding"]["entered_at"])
    assert "The product did not find it" in one["note"]
    # The saving beside it is the one the appraisal endpoint gives.
    plain = {k: v for k, v in body_for().items() if k not in ("causes", "other_cause")}
    worked = client.post("/interventions/appraise", json=plain, headers=admin).json()
    for part in ("unit", "intervention", "scenarios", "break_even"):
        assert one["appraisal"][part] == worked[part]

    listed = client.get("/interventions", headers=role("hr_analyst")).json()
    assert [k["id"] for k in listed["kept"]] == [one["id"]] and listed["withdrawn"] == 0
    assert listed["kept"][0]["finding"] == one["finding"]
    assert not re.search(IDS, json.dumps(listed))


def test_the_saving_shown_is_todays_not_the_day_it_was_kept(client):
    """Only the inputs are kept. Ask under the high assumptions and the discipline costs
    more, so the same kept appraisal saves more."""
    admin = role("admin")
    client.post("/interventions", json=body_for(), headers=admin)
    nets = {
        mode: Decimal(
            client.get("/interventions", params={"mode": mode}, headers=admin).json()["kept"][0][
                "appraisal"
            ]["scenarios"][1]["net_saving"]
        )
        for mode in ("low", "base", "high")
    }
    assert nets["low"] < nets["base"] < nets["high"]


def test_a_finding_that_cannot_stand_is_refused_with_every_reason(client):
    admin = role("admin")
    refused = client.post(
        "/interventions",
        json=body_for(causes=[], other_cause="Everyone drives in", basis="", years=0),
        headers=admin,
    )
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"] == [
        "look ahead between 1 and 10 years",
        "say what the shares of actions prevented are based on: this product has no figure "
        "of its own for how well an intervention works",
        "say what the investigation found: choose at least one cause. An appraisal is kept "
        "as the record of a finding, and this product does not suggest one",
        'words are kept only with "Other": choose it, or clear the words',
    ]
    missing = body_for()
    del missing["causes"]
    assert client.post("/interventions", json=missing, headers=admin).status_code == 422
    nowhere = client.post("/interventions", json=body_for(work_location="Nowhere"), headers=admin)
    assert nowhere.status_code == 404
    assert client.get("/interventions", headers=admin).json()["kept"] == []


def test_withdrawing_stops_it_being_shown_and_is_counted(client):
    admin = role("admin")
    first = client.post("/interventions", json=body_for(), headers=admin).json()["id"]
    second = client.post(
        "/interventions", json=body_for(causes=["training"], other_cause=""), headers=admin
    ).json()["id"]
    assert first != second
    assert client.delete(f"/interventions/{first}", headers=role("hr_analyst")).json() == {
        "id": first,
        "withdrawn": True,
    }
    listed = client.get("/interventions", headers=admin).json()
    assert [k["id"] for k in listed["kept"]] == [second] and listed["withdrawn"] == 1
    assert client.delete(f"/interventions/{first}", headers=admin).status_code == 404
    assert client.delete("/interventions/nope", headers=admin).status_code == 404


def test_only_roles_that_see_records_may_read_or_write(client):
    made = client.post("/interventions", json=body_for(), headers=role("admin")).json()
    reader = role("executive")
    assert client.get("/interventions", headers=reader).status_code == 403
    assert client.get("/interventions/causes", headers=reader).status_code == 403
    assert client.post("/interventions", json=body_for(), headers=reader).status_code == 403
    assert client.delete(f"/interventions/{made['id']}", headers=reader).status_code == 403
    assert len(client.get("/interventions", headers=role("admin")).json()["kept"]) == 1


def test_keeping_changes_nothing_in_the_baseline(client):
    admin = role("admin")
    before = client.get("/costs/summary", headers=admin).json()
    made = client.post("/interventions", json=body_for(), headers=admin).json()
    client.get("/interventions", headers=admin)
    client.delete(f"/interventions/{made['id']}", headers=admin)
    assert client.get("/costs/summary", headers=admin).json() == before


def test_the_audit_log_says_a_finding_was_kept_and_not_what_it_said(client):
    from wri_engine.api import main

    made = client.post("/interventions", json=body_for(), headers=role("admin")).json()
    log = main.AUDIT.path.read_text()
    assert made["id"] in log and '"causes": ["scheduling", "other"]' in log
    assert "first bus" not in log, "the words stay in the engagement's folder"
    assert "Flexible start times" not in log and "site visit" not in log


# ---------------------------------------------------------------------------
# a licensed installation: the engagement's own folder
# ---------------------------------------------------------------------------
@pytest.fixture
def site(tmp_path):
    site = Installation(tmp_path)
    for person in (LEAD, ANALYST, CLIENT, OUTSIDER):
        site.add_user(person)
    site.engagement = site.new_engagement("FY26", "Pine Valley County")
    site.add_member(site.engagement, LEAD, "admin")
    site.add_member(site.engagement, ANALYST, "hr_analyst")
    site.add_member(site.engagement, CLIENT, "executive")
    assert site.upload_sample(site.engagement, LEAD).status_code == 200
    return site


def licensed_body(site, **changes) -> dict:
    first = site.runtime.workspaces.get(site.engagement).run().action_costs[0].dimensions
    unit = {
        "role_family": first["role_family"],
        "work_location": first["work_location"],
        "misconduct_category": "",
    }
    return {**body_for(), **unit, **changes}


def test_it_is_kept_in_the_engagements_own_folder_and_survives_a_restart(site, tmp_path):
    e = site.engagement
    url = f"/engagements/{e}/interventions"
    made = site.http.post(url, json=licensed_body(site), headers=site.auth(LEAD))
    assert made.status_code == 200, made.text
    finding = made.json()["finding"]
    assert finding["entered_by"].endswith(f"({LEAD[0]})") or finding["entered_by"] == LEAD[0]

    file = tmp_path / "engagements" / e / "interventions" / FILE_NAME
    assert file.exists()
    assert "first bus arrives after the shift starts" in file.read_text()
    # The words are a client's material: beside the client's files, not in the database.
    assert b"first bus" not in (tmp_path / "wri.db").read_bytes()

    http, headers = restarted(tmp_path, ANALYST)
    after = http.get(url, headers=headers).json()
    assert [k["id"] for k in after["kept"]] == [made.json()["id"]]
    assert after["kept"][0]["finding"] == finding
    assert after["kept"][0]["appraisal"]["unit"]["topic"] == ""
    # Withdrawn by someone else, and the file only grew.
    before = file.read_text()
    assert http.delete(f"{url}/{made.json()['id']}", headers=headers).status_code == 200
    assert file.read_text().startswith(before) and ANALYST[0] in file.read_text()
    assert http.get(url, headers=headers).json() == {**after, "kept": [], "withdrawn": 1}


def test_one_engagement_never_sees_anothers_findings(site):
    other = site.new_engagement("FY27", "Cedar Ridge Schools")
    site.add_member(other, LEAD, "admin")
    assert site.upload_sample(other, LEAD).status_code == 200
    lead = site.auth(LEAD)
    made = site.http.post(
        f"/engagements/{site.engagement}/interventions", json=licensed_body(site), headers=lead
    ).json()
    theirs = site.http.get(f"/engagements/{other}/interventions", headers=lead).json()
    assert theirs["kept"] == [] and theirs["withdrawn"] == 0
    # Nor can it be withdrawn through the other engagement.
    assert (
        site.http.delete(
            f"/engagements/{other}/interventions/{made['id']}", headers=lead
        ).status_code
        == 404
    )
    mine = site.http.get(f"/engagements/{site.engagement}/interventions", headers=lead).json()
    assert [k["id"] for k in mine["kept"]] == [made["id"]]


def test_in_a_licensed_installation_it_follows_the_engagement_role(site):
    url = f"/engagements/{site.engagement}/interventions"
    body = licensed_body(site)
    assert site.http.post(url, json=body, headers=site.auth(ANALYST)).status_code == 200
    for method, path in (("GET", url), ("GET", f"{url}/causes"), ("POST", url)):
        kwargs = {"json": body} if method == "POST" else {}
        assert (
            site.http.request(method, path, headers=site.auth(CLIENT), **kwargs).status_code == 403
        )
        assert (
            site.http.request(method, path, headers=site.auth(OUTSIDER), **kwargs).status_code
            == 404
        )
        assert site.http.request(method, path, **kwargs).status_code == 401


def test_a_finding_outlives_a_corrected_file_that_empties_its_unit(site):
    """The visit happened. If a corrected file leaves the unit with no actions, the finding
    is still shown, with nothing to set the intervention against."""
    lead = site.auth(LEAD)
    url = f"/engagements/{site.engagement}/interventions"
    made = site.http.post(url, json=licensed_body(site), headers=lead).json()
    book = site.runtime.workspaces.get(site.engagement).appraisals
    line = json.loads(book.path.read_text())
    moved = {**line, "id": "moved", "col": "A location the new file does not have"}
    with book.path.open("a") as handle:
        handle.write(json.dumps(moved) + "\n")
        os.fsync(handle.fileno())
    listed = site.http.get(url, headers=lead).json()["kept"]
    assert [k["id"] for k in listed] == [made["id"], "moved"]
    assert listed[1]["appraisal"] is None
    assert listed[1]["finding"] == made["finding"]
    assert listed[1]["intervention"]["name"] == "Flexible start times"
