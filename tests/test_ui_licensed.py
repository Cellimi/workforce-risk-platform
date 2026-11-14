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
    assert "Sales and office - x1.3977" in picker.options
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
