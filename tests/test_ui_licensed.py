"""The screen in a licensed installation, driven the way a person would use it.

A real licensed API runs in a background thread with its own temporary database and files;
the Streamlit app runs under Streamlit's test harness and talks to it over HTTP, exactly as in
production. The harness cannot type into an editable table, so where a person would fill in
the code translations, the test sets the same working mapping those tables write into.
"""

from __future__ import annotations

import csv
import io
import socket
import threading
import time
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")
uvicorn = pytest.importorskip("uvicorn")

from wri_engine.api.main import create_app  # noqa: E402
from wri_engine.settings import Settings  # noqa: E402

APP = str(Path(__file__).parents[1] / "app" / "streamlit_app.py")
OWNER = ("owner@firm.example", "owner-password-123")
LEAD = ("lead@firm.example", "lead-password-1234")
READER = ("cfo@client.example", "client-password-123")


# ---------------------------------------------------------------------------
# a licensed server, and a client export in a layout nobody planned for
# ---------------------------------------------------------------------------
def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server(tmp_path):
    app = create_app(Settings(auth_mode="local", data_dir=tmp_path))
    store = app.state.runtime.store
    store.create_user(*OWNER, name="Owner", is_firm_admin=True)
    for who in (LEAD, READER):
        store.create_user(*who)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", store
    srv.should_exit = True
    thread.join(timeout=5)


def _csv(rows: list[dict]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


ROSTER = _csv(
    [
        {
            "Worker #": f"W{n}",
            "Org Unit": "Roads",
            "Position Category": family,
            "Base Pay": "52,000",
            "Pay Type": "N",
            "Yard": "North Yard",
            "Start Dt": "02/27/2011",
            "Term Dt": "07/30/2024" if n == 0 else "",
            "Term Reason": "REM" if n == 0 else "",
        }
        for n, family in enumerate(["Mechanic", "Mechanic", "Office Admin", "Office Admin"])
    ]
)
LOG = _csv(
    [
        {
            "Case No": f"DC-{n}",
            "Worker #": worker,
            "Date of Decision": date,
            "Offense": offense,
            "Penalty": penalty,
        }
        for n, (worker, date, offense, penalty) in enumerate(
            [
                ("W0", "07/27/2024", "ATT", "TM"),
                ("W1", "08/25/2024", "ATT", "WR"),
                ("W2", "09/03/2024", "CON", "WR"),
                ("W3", "10/14/2024", "ATT", "WR"),
            ]
        )
    ]
)
#: What a consultant would choose in the code tables.
TRANSLATIONS = {
    "employees": {
        "flsa_status": {"N": "non_exempt"},
        "separation_reason": {"REM": "removal_for_cause"},
        "is_sworn": {"Mechanic": "N", "Office Admin": "N"},
        "minimum_staffing_role": {"Mechanic": "N", "Office Admin": "N"},
        "work_schedule": {"Mechanic": "weekday_8", "Office Admin": "weekday_8"},
        "turnover_profile": {"Mechanic": "civilian_skilled", "Office Admin": "civilian_standard"},
    },
    "actions": {
        "action_type": {"TM": "removal", "WR": "written_reprimand"},
        "misconduct_category": {"ATT": "attendance_awol", "CON": "conduct_unbecoming"},
    },
}


# ---------------------------------------------------------------------------
# driving the app
# ---------------------------------------------------------------------------
def open_app(url: str):
    at = st_testing.AppTest.from_file(APP, default_timeout=60)
    at.session_state["api_url"] = url
    return at.run()


def labelled(widgets, label: str):
    matches = [w for w in widgets if w.label == label]
    assert matches, f"no widget labelled {label!r}; have {[w.label for w in widgets]}"
    return matches[0]


def texts(at) -> str:
    parts = [
        e.value
        for kind in ("markdown", "caption", "info", "warning", "error", "success")
        for e in getattr(at, kind)
    ]
    parts += [t.value for t in at.title] + [s.value for s in at.subheader]
    return "\n".join(str(p) for p in parts)


def expanders(at) -> list[str]:
    return [e.label for e in at.expander]


def sign_in(at, who):
    labelled(at.text_input, "Email").input(who[0])
    labelled(at.text_input, "Password").input(who[1])
    labelled(at.button, "Sign in").click()
    return at.run()


def go(at, page: str):
    at.sidebar.radio[0].set_value(page)
    return at.run()


def metric(at, label: str) -> str:
    return labelled(at.metric, label).value


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
def test_a_licensed_server_asks_for_sign_in_and_offers_no_role_picker(server):
    url, _ = server
    at = open_app(url)
    assert not at.exception
    assert "Sign in" in [b.label for b in at.button]
    assert all(s.label != "Role" for s in at.sidebar.selectbox)
    assert "Harlow" not in texts(at)


def test_a_wrong_password_is_refused_on_screen(server):
    url, _ = server
    at = sign_in(open_app(url), (OWNER[0], "not-the-password"))
    assert "email or password is incorrect" in texts(at)
    assert "token" not in at.session_state


def test_a_new_firm_starts_on_the_engagement_page(server):
    url, _ = server
    at = sign_in(open_app(url), OWNER)
    assert not at.exception
    assert [o for o in at.sidebar.radio[0].options] == ["Engagement"]
    assert "no engagements yet" in texts(at)


def test_end_to_end_a_consultant_loads_a_client_export_and_sees_the_cost(server):
    url, store = server
    at = sign_in(open_app(url), OWNER)

    # Open an engagement from the screen.
    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.button, "Open engagement").click()
    at.run()
    assert not at.exception
    assert at.sidebar.markdown[0].value == "### Pine Valley County"
    assert "Load data" in at.sidebar.radio[0].options

    # Opening an engagement goes straight to loading its data.
    assert at.sidebar.radio[0].value == "Load data"

    # The results pages say there is nothing yet, rather than failing.
    at = go(at, "Executive summary")
    assert "No data has been loaded for this engagement yet" in texts(at)

    # Upload the client's files as they arrived.
    at = go(at, "Load data")
    uploader = at.file_uploader[0]
    uploader.set_value(
        [("Staff_Roster.csv", ROSTER, "text/csv"), ("Discipline_Log.csv", LOG, "text/csv")]
    )
    at.run()
    labelled(at.button, "Upload these files").click()
    at.run()
    assert not at.exception, at.exception

    # The draft found the columns and the date format, and lists the codes to translate.
    assert labelled(at.selectbox, "How the client writes dates").value == "%m/%d/%Y"
    assert "Still needed" not in texts(at)
    code_tables = {e.key for e in at.data_editor if e.key and "codes_" in e.key}
    assert any("action_type" in k for k in code_tables)
    assert any("misconduct_category" in k for k in code_tables)
    assert any("family_employees" in (e.key or "") for e in at.data_editor)
    assert "Nothing usable would load yet" in texts(at)

    # Fill in the translations, as a person would in those tables, and check again.
    engagement_id = at.session_state["engagement_id"]
    mapping = at.session_state[f"mapping_{engagement_id}"]
    for table, fields in TRANSLATIONS.items():
        for field, codes in fields.items():
            mapping["tables"][table]["values"].setdefault(field, {}).update(codes)
    labelled(at.button, "Check the mapping").click()
    at.run()
    assert not at.exception, at.exception
    assert "Every code in the client's files is understood" in texts(at)
    assert metric(at, "Actions excluded") == "0"
    assert metric(at, "Employees") == "4"

    labelled(at.button, "Commit as this engagement's data").click()
    at.run()
    assert "Loaded 4 employees and 4 actions" in texts(at)

    # The results pages now show the client's data, labelled as the client's.
    at = go(at, "Executive summary")
    assert not at.exception, at.exception
    assert "What discipline costs Pine Valley County" in texts(at)
    assert "Synthetic data" not in texts(at)

    # Every page renders with the client's data, without an error.
    for page in at.sidebar.radio[0].options:
        at = go(at, page)
        assert not at.exception, (page, at.exception)
        assert not at.error, (page, [e.value for e in at.error])


def test_client_specific_values_are_set_and_removed_on_screen(server):
    url, store = server
    at = sign_in(open_app(url), OWNER)
    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.button, "Open engagement").click()
    at.run()
    at = go(at, "Assumptions")
    assert "None yet. Every figure uses the approved default." in texts(at)

    labelled(at.selectbox, "Assumption").set_value("c4_outside_counsel_hourly_rate")
    at.run()
    labelled(at.text_input, "Client value").input("$450")
    labelled(at.text_input, "Where the figure comes from").input("2026 panel counsel rates")
    labelled(at.button, "Save client value").click()
    at.run()
    assert not at.exception, at.exception
    overrides = at.dataframe[-1].value
    assert list(overrides["Assumption"]) == ["c4_outside_counsel_hourly_rate"]
    registry = at.dataframe[0].value
    marked = registry[registry["Client value"] == "yes"]
    assert list(marked["Assumption"]) == ["c4_outside_counsel_hourly_rate"]
    assert list(marked["Value"]) == ["450"]
    assert list(overrides["Client value"]) == ["450"]
    assert list(overrides["Set by"]) == [OWNER[0]]

    labelled(at.button, "Use the default").click()
    at.run()
    assert "None yet. Every figure uses the approved default." in texts(at)


def test_a_client_reader_sees_only_what_their_role_allows(server):
    url, store = server
    owner = store.get_user_by_email(OWNER[0])
    engagement = store.create_engagement(
        name="FY26 review",
        client_name="Pine Valley County",
        sector="public_sector",
        created_by=owner.id,
    )
    from wri_engine.access.roles import Role

    store.set_member(engagement.id, store.get_user_by_email(READER[0]).id, Role.EXECUTIVE)
    at = sign_in(open_app(url), READER)
    assert not at.exception
    options = at.sidebar.radio[0].options
    assert "Drill-down" not in options
    assert "Load data" not in options
    assert "Your role: Client reader" in [c.value for c in at.sidebar.caption]
    at = go(at, "Executive summary")
    assert "The engagement lead loads it" in texts(at)


def test_a_member_is_added_from_the_engagement_page(server):
    url, store = server
    at = sign_in(open_app(url), OWNER)
    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.button, "Open engagement").click()
    at.run()
    at = go(at, "Engagement")
    labelled(at.text_input, "Email of an existing user").input(LEAD[0])
    labelled(at.selectbox, "Role").set_value("admin")
    labelled(at.button, "Save member").click()
    at.run()
    assert not at.exception, at.exception
    members = at.dataframe[0].value
    assert list(members["Email"]) == [LEAD[0]]
    assert list(members["Role"]) == ["Engagement lead (admin)"]


def test_signing_out_returns_to_the_sign_in_screen(server):
    url, _ = server
    at = sign_in(open_app(url), OWNER)
    labelled(at.sidebar.button, "Sign out").click()
    at.run()
    assert "Sign in" in [b.label for b in at.button]
    assert "token" not in at.session_state


def test_the_demo_still_offers_the_role_picker(tmp_path):
    app = create_app(Settings(auth_mode="demo", data_dir=tmp_path))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    try:
        at = open_app(f"http://127.0.0.1:{port}")
        assert not at.exception, at.exception
        assert "Role" in [s.label for s in at.sidebar.selectbox]
        assert "Harlow County" in texts(at)
        for page in at.sidebar.radio[0].options:
            at.sidebar.radio[0].set_value(page)
            at.run()
            assert not at.exception, (page, at.exception)
        assert "Load data" not in at.sidebar.radio[0].options
    finally:
        srv.should_exit = True
        thread.join(timeout=5)


def test_the_occupation_group_is_selected_on_screen_and_can_be_changed(server):
    """A private-sector user picks the kind of work when opening the engagement, sees the
    multiplier each choice brings, and can change the selection afterwards."""
    url, store = server
    at = sign_in(open_app(url), OWNER)
    # Public sector is what the chooser opens on, and it offers no occupation group.
    assert labelled(at.selectbox, "Sector").value == "public_sector"
    assert "Client's occupation group" not in [s.label for s in at.selectbox]
    assert "A government employer" in texts(at)

    labelled(at.selectbox, "Sector").set_value("private_sector")
    at.run()
    # The description and the groups both follow the sector as soon as it is chosen.
    assert "A company." in texts(at) and "A government employer" not in texts(at)
    picker = labelled(at.selectbox, "Client's occupation group")
    assert picker.options[0].startswith("Not selected (all-occupations average) - x1.4287")
    assert "Sales and office (a mix of both) - x1.3977" in picker.options
    assert "Sales - x1.3283" in picker.options
    assert "Office and administrative support - x1.4564" in picker.options
    assert "Construction, maintenance and natural resources - x1.4538" in picker.options
    picker.set_value("construction_maintenance")
    at.run()
    labelled(at.text_input, "Engagement name").input("Site discipline review")
    labelled(at.text_input, "Client name").input("Ridgeline Builders")
    labelled(at.button, "Open engagement").click()
    at.run()
    assert not at.exception, at.exception
    owner = store.get_user_by_email(OWNER[0])
    (engagement, _role) = store.engagements_for(owner)[0]
    assert (engagement.sector, engagement.occupation_group) == (
        "private_sector",
        "construction_maintenance",
    )

    at = go(at, "Engagement")
    current = labelled(at.selectbox, "Occupation group")
    assert current.value == "construction_maintenance"
    current.set_value("sales_office")
    labelled(at.button, "Save").click()
    at.run()
    assert not at.exception, at.exception
    assert store.get_engagement(engagement.id).occupation_group == "sales_office"
    assert labelled(at.selectbox, "Occupation group").value == "sales_office"


def test_a_public_engagement_shows_no_occupation_group(server):
    url, store = server
    at = sign_in(open_app(url), OWNER)
    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.button, "Open engagement").click()
    at.run()
    assert not at.exception, at.exception
    owner = store.get_user_by_email(OWNER[0])
    (engagement, _role) = store.engagements_for(owner)[0]
    # A user who touches neither chooser gets what they always got.
    assert (engagement.sector, engagement.occupation_group) == ("public_sector", None)
    at = go(at, "Engagement")
    assert "Occupation group" not in [s.label for s in at.selectbox]


def _upload_sample(url: str, store, engagement_id: str) -> None:
    """Load the synthetic sample into an engagement over HTTP, as a consultant's upload."""
    import httpx

    from wri_engine.paths import SYNTHETIC_DIR

    token = httpx.post(f"{url}/auth/login", json={"email": OWNER[0], "password": OWNER[1]}).json()[
        "token"
    ]
    files = [
        ("files", (p.name, p.read_bytes(), "text/csv"))
        for p in sorted((SYNTHETIC_DIR / "sample").iterdir())
        if p.is_file()
    ]
    response = httpx.post(
        f"{url}/engagements/{engagement_id}/datasets/upload",
        files=files,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    assert response.status_code == 200, response.text


def test_the_firm_sets_its_name_and_the_report_is_offered_for_download(server):
    """A firm administrator puts the firm's name on reports from the Engagement page, and
    the Executive summary offers the report and the workbook once they have been prepared."""
    url, store = server
    owner = store.get_user_by_email(OWNER[0])
    engagement = store.create_engagement(
        name="FY26 review",
        client_name="Pine Valley County",
        sector="public_sector",
        created_by=owner.id,
    )
    _upload_sample(url, store, engagement.id)

    at = sign_in(open_app(url), OWNER)
    at = go(at, "Engagement")
    labelled(at.text_input, "Firm name").input("Northgate Advisory LLP")
    labelled(at.button, "Save firm name").click()
    at.run()
    assert not at.exception, at.exception
    assert store.firm().name == "Northgate Advisory LLP"

    at = go(at, "Executive summary")
    assert not at.exception, at.exception
    downloads = [d.proto.label for d in at.get("download_button")]
    assert "Download the report (PDF)" not in downloads, "nothing is built until asked for"
    labelled(at.button, "Prepare the report").click()
    at.run()
    assert not at.exception, at.exception
    downloads = [d.proto.label for d in at.get("download_button")]
    assert "Download the report (PDF)" in downloads
    assert "Download the workbook (Excel)" in downloads
    prepared = at.session_state["report_files"]
    assert prepared["pdf"].startswith(b"%PDF") and prepared["xlsx"].startswith(b"PK")
    # The engagement team also gets the working papers, as a separate file, told apart
    # from what goes to the client.
    assert "Download the working papers (Excel, record level)" in downloads
    assert prepared["records"].startswith(b"PK") and prepared["records"] != prepared["xlsx"]
    assert "They are not the client's copy" in texts(at)


def test_a_client_reader_is_not_offered_the_working_papers(server):
    url, store = server
    owner = store.get_user_by_email(OWNER[0])
    engagement = store.create_engagement(
        name="FY26 review",
        client_name="Pine Valley County",
        sector="public_sector",
        created_by=owner.id,
    )
    _upload_sample(url, store, engagement.id)
    store.set_member(engagement.id, store.get_user_by_email(READER[0]).id, "executive")

    at = sign_in(open_app(url), READER)
    at = go(at, "Executive summary")
    labelled(at.button, "Prepare the report").click()
    at.run()
    assert not at.exception, at.exception
    downloads = [d.proto.label for d in at.get("download_button")]
    assert "Download the workbook (Excel)" in downloads
    assert "Download the working papers (Excel, record level)" not in downloads
    assert "records" not in at.session_state["report_files"]


# ---------------------------------------------------------------------------
# license keys on screen
# ---------------------------------------------------------------------------
@pytest.fixture
def licensed_server(tmp_path):
    """A licensed installation that has been given a verification key, with the signing
    key that goes with it. The pair is made here, at run time, and kept nowhere."""
    from wri_engine.licensing import generate_keypair, load_private_key

    private_pem, public_pem = generate_keypair()
    app = create_app(Settings(auth_mode="local", data_dir=tmp_path, license_public_key=public_pem))
    store = app.state.runtime.store
    store.create_user(*OWNER, name="Owner", is_firm_admin=True)
    store.create_user(*LEAD)
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", store, load_private_key(private_pem)
    srv.should_exit = True
    thread.join(timeout=5)


def _key(signing_key, client: str = "Pine Valley County", **claims) -> str:
    from datetime import date, timedelta

    from wri_engine.licensing import issue

    claims.setdefault("issued_on", date.today())
    claims.setdefault("expires_on", date.today() + timedelta(days=365))
    return issue(
        signing_key,
        licensee="Northgate Advisory LLP",
        client_name=client,
        sector="public_sector",
        **claims,
    )


def test_an_engagement_is_opened_with_its_license_key(licensed_server):
    """Where keys are enforced, the form asks for one, says plainly why a key was not
    accepted, and the Engagement page then shows where the engagement stands."""
    url, store, signing_key = licensed_server
    at = sign_in(open_app(url), OWNER)
    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.text_area, "License key").input(_key(signing_key, "Other County"))
    labelled(at.button, "Open engagement").click()
    at.run()
    assert not at.exception, at.exception
    assert "issued for the client 'Other County'" in texts(at)
    owner = store.get_user_by_email(OWNER[0])
    assert store.engagements_for(owner) == []

    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.text_area, "License key").input(_key(signing_key, max_employees=5000))
    labelled(at.button, "Open engagement").click()
    at.run()
    assert not at.exception, at.exception
    (engagement, _role) = store.engagements_for(owner)[0]
    assert engagement.license_key_id

    at = go(at, "Engagement")
    shown = texts(at)
    assert "License key" in shown
    assert "issued to **Northgate Advisory LLP**" in shown
    assert f"key {engagement.license_key_id}" in shown
    assert "covering up to 5,000 employees" in shown
    # The key itself is never put back on the screen.
    assert "WRI1." not in shown
    downloads = [d.proto.label for d in at.get("download_button")]
    assert "Download the usage log" in downloads


def test_a_missing_key_is_reported_as_a_license_matter_and_put_right_on_screen(licensed_server):
    """An engagement with no key says so in its own words, not as a role being refused.
    A firm administrator pastes a key on the Engagement page and the work opens."""
    url, store, signing_key = licensed_server
    owner = store.get_user_by_email(OWNER[0])
    engagement = store.create_engagement(
        name="Opened before keys",
        client_name="Pine Valley County",
        sector="public_sector",
        created_by=owner.id,
    )
    at = sign_in(open_app(url), OWNER)
    at = go(at, "Executive summary")
    assert not at.exception, at.exception
    shown = texts(at)
    assert "no license key" in shown
    assert "role is not permitted" not in shown

    at = go(at, "Engagement")
    assert not at.exception, at.exception
    assert "This engagement has no license key" in "\n".join(e.value for e in at.error)
    labelled(at.text_area, "Add or replace the key").input(_key(signing_key))
    labelled(at.button, "Save key").click()
    at.run()
    assert not at.exception, at.exception
    assert store.get_engagement(engagement.id).license_key_id
    assert "In date until" in texts(at)


def test_only_a_firm_administrator_is_offered_the_key_box(licensed_server):
    url, store, signing_key = licensed_server
    owner = store.get_user_by_email(OWNER[0])
    engagement = store.create_engagement(
        name="Opened before keys",
        client_name="Pine Valley County",
        sector="public_sector",
        created_by=owner.id,
    )
    store.set_member(engagement.id, store.get_user_by_email(LEAD[0]).id, "admin")
    at = sign_in(open_app(url), LEAD)
    at = go(at, "Engagement")
    assert not at.exception, at.exception
    assert "A firm administrator can add a key." in texts(at)
    assert "Add or replace the key" not in [t.label for t in at.text_area]
    assert "Download the usage log" not in [d.proto.label for d in at.get("download_button")]


def test_an_installation_without_a_verification_key_shows_nothing_about_keys(server):
    url, store = server
    at = sign_in(open_app(url), OWNER)
    assert "License key" not in [t.label for t in at.text_area]
    labelled(at.text_input, "Engagement name").input("FY26 review")
    labelled(at.text_input, "Client name").input("Pine Valley County")
    labelled(at.button, "Open engagement").click()
    at.run()
    assert not at.exception, at.exception
    at = go(at, "Engagement")
    assert "License key" not in [s.value for s in at.subheader]


def test_a_firm_administrator_sees_whether_the_audit_log_holds(server, monkeypatch, tmp_path):
    from wri_engine.access.audit import AuditLog
    from wri_engine.api import main

    monkeypatch.setattr(main, "AUDIT", AuditLog(tmp_path / "audit.jsonl"))
    url, store = server
    at = sign_in(open_app(url), OWNER)
    assert not at.exception, at.exception
    assert "Audit log" in [s.value for s in at.subheader]
    assert any(m.value.startswith("Intact:") for m in at.success)
    head = main.AUDIT.check()
    shown = [c.value for c in at.code]
    assert any(
        v.startswith(f"{head.head_seq - 1}:") or v.startswith(f"{head.head_seq}:") for v in shown
    )

    at = sign_in(open_app(url), LEAD)
    assert "Audit log" not in [s.value for s in at.subheader]


# ---------------------------------------------------------------------------
# single sign-on on screen
# ---------------------------------------------------------------------------
@pytest.fixture
def sso_server(tmp_path):
    """A licensed installation with single sign-on, and the stand-in provider behind it."""
    from oidc_provider import Person, Provider
    from wri_engine.sso import OidcClient

    provider = Provider()
    settings = Settings(
        auth_mode="local",
        data_dir=tmp_path,
        sso_issuer=provider.issuer,
        sso_client_id=provider.client_id,
        sso_client_secret=provider.client_secret,
        sso_redirect_url=provider.redirect_url,
        sso_name="Firm sign-in",
    )
    app = create_app(settings)
    app.state.runtime.sso = OidcClient(settings.sso, http=provider.http)
    store = app.state.runtime.store
    store.create_user(*OWNER, name="Owner", is_firm_admin=True)
    store.create_user("pat@firm.example", None, name="Pat")
    provider.signing_in_as = Person(subject="idp-0001", email="pat@firm.example")
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", store, provider, app
    srv.should_exit = True
    thread.join(timeout=5)


def _returned_from_provider(url: str, params: dict, cookie: str | None, monkeypatch):
    """The app as the browser finds it on coming back: a fresh session, the provider's
    answer in the address, and whatever cookie this browser holds."""
    import sys

    sys.path.insert(0, str(Path(APP).parent))
    import licensed

    monkeypatch.setattr(licensed, "browser_cookie", lambda name: cookie)
    at = st_testing.AppTest.from_file(APP, default_timeout=60)
    at.session_state["api_url"] = url
    for key, value in params.items():
        at.query_params[key] = value
    return at.run()


def test_a_person_signs_in_through_the_identity_provider(sso_server, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    url, store, provider, _app = sso_server
    at = open_app(url)
    # Both ways in are offered: the provider first, the password form below it.
    assert "Email" in [t.label for t in at.text_input]
    labelled(at.button, "Sign in with Firm sign-in").click()
    at.run()
    assert not at.exception, at.exception
    pending = at.session_state["sso_pending"]
    link = next(m.value for m in at.markdown if "Continue to Firm sign-in" in m.value)
    assert 'target="_self"' in link and "https://idp.test/authorize?" in link
    assert provider.client_secret not in link

    # The browser goes to the provider and is sent back with a code.
    sent_back = provider.http.get(pending["authorization_url"])
    answer = {k: v[0] for k, v in parse_qs(urlsplit(sent_back.headers["location"]).query).items()}

    back = _returned_from_provider(url, answer, pending["binding"], monkeypatch)
    assert not back.exception, back.exception
    assert back.session_state["user"]["email"] == "pat@firm.example"
    assert "Signed in as" in texts(back)
    assert dict(back.query_params) == {}, "the one-time code does not stay in the address"
    assert store.get_user_by_email("pat@firm.example").sso_linked is True


def test_a_link_finished_in_another_browser_signs_nobody_in(sso_server, monkeypatch):
    from urllib.parse import parse_qs, urlsplit

    url, _store, provider, _app = sso_server
    at = open_app(url)
    labelled(at.button, "Sign in with Firm sign-in").click()
    at.run()
    pending = at.session_state["sso_pending"]
    sent_back = provider.http.get(pending["authorization_url"])
    answer = {k: v[0] for k, v in parse_qs(urlsplit(sent_back.headers["location"]).query).items()}

    for cookie in (None, "some-other-browsers-secret"):
        other = _returned_from_provider(url, answer, cookie, monkeypatch)
        assert not other.exception, other.exception
        assert "token" not in other.session_state
        assert "start again" in " ".join(e.value for e in other.error).lower()


def test_cancelling_at_the_provider_returns_to_the_sign_in_page(sso_server, monkeypatch):
    url, _store, _provider, _app = sso_server
    back = _returned_from_provider(url, {"error": "access_denied"}, None, monkeypatch)
    assert not back.exception, back.exception
    assert "cancelled" in " ".join(w.value for w in back.warning)
    assert "Sign in with Firm sign-in" in [b.label for b in back.button]


def test_with_passwords_off_only_the_provider_is_offered(tmp_path):
    from oidc_provider import Provider

    provider = Provider()
    app = create_app(
        Settings(
            auth_mode="local",
            data_dir=tmp_path,
            sso_issuer=provider.issuer,
            sso_client_id=provider.client_id,
            sso_redirect_url=provider.redirect_url,
            sso_name="Firm sign-in",
            password_sign_in=False,
        )
    )
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    try:
        at = open_app(f"http://127.0.0.1:{port}")
        assert not at.exception, at.exception
        assert "Sign in with Firm sign-in" in [b.label for b in at.button]
        assert [t.label for t in at.text_input] == []
    finally:
        srv.should_exit = True
        thread.join(timeout=5)


def test_without_single_sign_on_the_sign_in_page_is_as_it_was(server):
    url, _store = server
    at = open_app(url)
    assert [b.label for b in at.button] == ["Sign in"]
    assert [t.label for t in at.text_input] == ["Email", "Password"]


def test_a_firm_administrator_creates_an_account_with_no_password(sso_server):
    url, store, _provider, _app = sso_server
    at = sign_in(open_app(url), OWNER)
    labelled(at.text_input, "Email").input("sam@firm.example")
    labelled(at.button, "Create user").click()
    at.run()
    assert not at.exception, at.exception
    created = store.get_user_by_email("sam@firm.example")
    assert created is not None and created.has_password is False
    shown = at.dataframe[0].value
    methods = dict(zip(shown["Email"], shown["Signs in with"], strict=True))
    assert methods["sam@firm.example"] == "Firm sign-in (not yet used)"
    assert methods[OWNER[0]] == "password"


# ---------------------------------------------------------------------------
# patterns (Phase 2) on screen
# ---------------------------------------------------------------------------
def test_the_patterns_page_shows_the_unit_that_stands_out(tmp_path):
    """On the whole synthetic county, a role that sees records is shown the one planted
    unit by type of misconduct, what those actions cost, what the difference is estimated
    to cost and how that was worked out, and can look at every type together. A role that
    sees totals only is shown the flagged unit with its cost, and not the working."""
    from wri_engine.api.state import STATE
    from wri_engine.generator import generate
    from wri_engine.paths import SYNTHETIC_DIR

    county = tmp_path / "county"
    generate(seed=20260917).write(county)
    STATE.load(county)
    app = create_app(Settings(auth_mode="demo", data_dir=tmp_path))
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    for _ in range(200):
        if srv.started:
            break
        time.sleep(0.02)
    try:
        at = open_app(f"http://127.0.0.1:{port}")
        labelled(at.sidebar.selectbox, "Role").set_value("hr_analyst")
        at.run()
        at = go(at, "Patterns")
        assert not at.exception, at.exception
        shown = texts(at)
        hot = "Attendance / AWOL among Corrections Officer at Main Detention Center"
        assert hot in shown
        assert "It does not say why, and it says nothing about any one person." in shown
        assert "have not been reviewed yet" not in shown, "the owner reviewed them"
        assert "196 comparisons made" in shown
        assert metric(at, "Cost of these actions") == "$232,626"
        assert metric(at, "Estimated excess cost") == "$164,168"
        assert metric(at, "Times the rate elsewhere") == "3.40"
        assert "70 Attendance / AWOL actions among 64 people" in shown
        assert "an estimate, not part of the cost baseline" in shown
        assert "Assessed and not flagged (133)" in expanders(at)
        # Rising: the county plants a level and no rise, and the page says so.
        assert "Rising over the period" in shown
        assert "Earlier half: 2023-10-20 to 2025-04-03." in shown
        assert "21 comparisons made" in shown
        assert "Nothing is rising by more than the rest of the organization." in shown
        assert any(label.startswith("Rising: assessed and not flagged") for label in expanders(at))
        # Nobody is named, on this page of all pages.
        import re

        assert not re.search(r"\bE\d{5}\b|EMP-[0-9A-F]{8}", shown)

        labelled(at.radio, "Look at").set_value("All types together")
        at.run()
        assert not at.exception, at.exception
        shown = texts(at)
        assert "29 comparisons made" in shown
        assert metric(at, "Estimated excess cost") == "$554,786"
        assert metric(at, "Times the rate elsewhere") == "2.07"
        assert "160 actions among 126 people" in shown

        # Leadership sees the flag and what it costs, and not the working behind it.
        labelled(at.radio, "Look at").set_value("By type of misconduct")
        labelled(at.sidebar.selectbox, "Role").set_value("executive")
        at.run()
        assert not at.exception, at.exception
        shown = texts(at)
        assert hot in shown
        assert metric(at, "Cost of these actions") == "$232,626"
        assert not any("not flagged" in label for label in expanders(at))
        assert "decided on the server" in shown
        assert "Rising over the period" in shown
        assert not re.search(r"\bE\d{5}\b|EMP-[0-9A-F]{8}", shown)
    finally:
        srv.should_exit = True
        thread.join(timeout=5)
        STATE.load(SYNTHETIC_DIR / "sample")


def test_patterns_is_offered_to_everyone_and_the_working_only_to_record_roles(server):
    url, store = server
    owner = store.get_user_by_email(OWNER[0])
    engagement = store.create_engagement(
        name="FY26 review",
        client_name="Pine Valley County",
        sector="public_sector",
        created_by=owner.id,
    )
    _upload_sample(url, store, engagement.id)
    store.set_member(engagement.id, store.get_user_by_email(LEAD[0]).id, "hr_analyst")
    store.set_member(engagement.id, store.get_user_by_email(READER[0]).id, "executive")

    at = sign_in(open_app(url), LEAD)
    assert "Patterns" in at.sidebar.radio[0].options
    at = go(at, "Patterns")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert "comparisons made" in texts(at)
    assert any("Assessed and not flagged" in label for label in expanders(at))

    at = sign_in(open_app(url), READER)
    assert "Patterns" in at.sidebar.radio[0].options
    assert "Drill-down" not in at.sidebar.radio[0].options
    at = go(at, "Patterns")
    assert not at.exception, at.exception
    assert not at.error, [e.value for e in at.error]
    assert "comparisons made" in texts(at)
    assert not any("not flagged" in label for label in expanders(at))
