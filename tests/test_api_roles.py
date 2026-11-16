"""API contract and role enforcement.

Role gating is asserted against the API, because that is where it is enforced. The UI's role
dropdown chooses a role; it cannot grant one.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from wri_engine.access.roles import pseudonymize
from wri_engine.api.main import app
from wri_engine.api.state import STATE


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    from wri_engine.paths import SYNTHETIC_DIR

    STATE.load(SYNTHETIC_DIR / "sample")
    return TestClient(app)


def headers(role: str, session: str | None = None) -> dict:
    out = {"X-WRI-Role": role}
    if session:
        out["X-WRI-Session"] = session
    return out


def first_action_id(client) -> str:
    body = client.get("/costs/actions", headers=headers("admin")).json()
    return body["actions"][0]["action_id"]


# ---------------------------------------------------------------------------
# role gating
# ---------------------------------------------------------------------------
def test_executive_cannot_drill_down(client):
    action_id = first_action_id(client)
    assert (
        client.get(f"/costs/actions/{action_id}", headers=headers("executive")).status_code == 403
    )
    assert client.get("/costs/actions", headers=headers("executive")).status_code == 403


def test_executive_can_see_aggregates(client):
    for path in ("/costs/matrix", "/costs/summary", "/exports/matrix.csv"):
        assert client.get(path, headers=headers("executive")).status_code == 200


def test_hr_analyst_sees_pseudonymized_identifiers(client):
    action_id = first_action_id(client)
    body = client.get(f"/costs/actions/{action_id}", headers=headers("hr_analyst")).json()
    assert body["employee"].startswith("EMP-")
    assert all(i["employee_id"].startswith("EMP-") for i in body["line_items"])


def test_admin_sees_real_identifiers(client):
    action_id = first_action_id(client)
    body = client.get(f"/costs/actions/{action_id}", headers=headers("admin")).json()
    assert not body["employee"].startswith("EMP-")
    assert pseudonymize(body["employee"]) != body["employee"]


def test_an_unknown_role_gets_the_weakest_one(client):
    action_id = first_action_id(client)
    assert (
        client.get(f"/costs/actions/{action_id}", headers=headers("superuser")).status_code == 403
    )
    assert client.get("/costs/matrix", headers=headers("superuser")).status_code == 200


def test_no_role_header_gets_the_weakest_one(client):
    action_id = first_action_id(client)
    assert client.get(f"/costs/actions/{action_id}").status_code == 403


def test_executive_cannot_run_scenarios(client):
    response = client.post("/scenarios/run", json={"overrides": {}}, headers=headers("executive"))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# contract
# ---------------------------------------------------------------------------
def test_openapi_documents_every_endpoint(client):
    paths = client.get("/openapi.json").json()["paths"]
    for expected in (
        "/datasets/load",
        "/costs/matrix",
        "/costs/summary",
        "/costs/actions/{action_id}",
        "/assumptions",
        "/scenarios/run",
        "/exports/matrix.csv",
        "/exports/summary.md",
    ):
        assert expected in paths


def test_every_response_carries_the_synthetic_data_caption(client):
    for path in ("/costs/matrix", "/costs/summary"):
        assert "Synthetic data" in client.get(path, headers=headers("executive")).json()["caption"]


def test_matrix_suppression_reconciles_over_the_api(client):
    body = client.get("/costs/matrix", headers=headers("executive", "reconcile")).json()
    assert body["suppression"]["reconciles"] is True
    for cell in body["cells"]:
        if cell["suppressed"]:
            assert "net" not in cell


def test_unknown_dimension_is_rejected(client):
    response = client.get("/costs/matrix?rows=favourite_colour", headers=headers("executive"))
    assert response.status_code == 400


def test_bad_filter_json_is_rejected(client):
    response = client.get("/costs/matrix?filters=notjson", headers=headers("executive"))
    assert response.status_code == 400


def test_scenario_override_changes_the_total(client):
    baseline = client.get("/costs/summary", headers=headers("admin")).json()["total_net"]
    body = client.post(
        "/scenarios/run",
        json={"overrides": {"c5_academy_tuition_sworn_deputy": 20000}},
        headers=headers("admin"),
    ).json()
    assert body["baseline_net"] == baseline
    assert body["scenario_net"] != baseline
    assert body["delta"] != "0"


def test_scenario_rejects_an_unknown_assumption(client):
    response = client.post(
        "/scenarios/run",
        json={"overrides": {"no_such_assumption": 1}},
        headers=headers("admin"),
    )
    assert response.status_code == 400


def test_assumptions_endpoint_reports_the_sign_off_state(client):
    body = client.get("/assumptions", headers=headers("admin")).json()
    # The owner has now reviewed the whole registry: the public-sector values on 2026-11-12
    # and the private-sector ones on 2026-11-13, so the review queue is empty.
    assert body["count"] > 150
    assert body["placeholder_count"] == 0
    assert body["editable"] is True
    assert client.get("/assumptions", headers=headers("executive")).json()["editable"] is False
    sample = next(a for a in body["assumptions"] if a["id"] == "benefits_multiplier_civilian")
    assert "Employer Costs for Employee Compensation" in sample["source"]
    assert sample["status"] == "researched"


def test_drill_down_lists_the_assumptions_behind_each_number(client):
    action_id = first_action_id(client)
    body = client.get(f"/costs/actions/{action_id}", headers=headers("admin")).json()
    assert body["assumptions_used"]
    cited = {a for item in body["line_items"] for a in item["assumption_ids"]}
    assert cited == {a["id"] for a in body["assumptions_used"]}
    for item in body["line_items"]:
        assert item["formula"]


def test_missing_action_is_404(client):
    assert client.get("/costs/actions/NOPE", headers=headers("admin")).status_code == 404


def test_validation_report_is_available(client):
    body = client.get("/datasets/validation", headers=headers("executive")).json()
    assert "counts_by_rule" in body
    assert "excluded_actions" in body
    assert body["actions_costed"] > 0


def test_csv_export_hides_suppressed_cells(client):
    text = client.get("/exports/matrix.csv", headers=headers("executive")).text
    assert "Synthetic data" in text
    assert "Other (suppressed)" in text
    assert "TOTAL" in text


def test_markdown_export_leads_with_the_headline(client):
    text = client.get("/exports/summary.md", headers=headers("executive")).text
    assert text.startswith("# Harlow County")
    assert "per year" in text
    # The export must not overstate what sign-off means. The standing sentence is read from
    # the registry, so it reports a blanket sign-off only while one actually holds -- and the
    # caveat that sign-off is not verification stands either way.
    assert "Sign-off is not verification" in text
    assert "signed off on every assumption" in text
    assert "TBD-MIKE" not in text


def test_audit_log_records_role_endpoint_and_filters(client, tmp_path):
    from wri_engine.access.audit import AuditLog
    from wri_engine.api import main

    original = main.AUDIT
    main.AUDIT = AuditLog(tmp_path / "audit.jsonl")
    try:
        filters = json.dumps({"year": ["2025"]})
        client.get(f"/costs/matrix?filters={filters}", headers=headers("hr_analyst"))
        action_id = first_action_id(client)
        client.get(f"/costs/actions/{action_id}", headers=headers("executive"))
        entries = main.AUDIT.tail(10)
    finally:
        main.AUDIT = original

    assert any(
        e["endpoint"] == "/costs/matrix"
        and e["role"] == "hr_analyst"
        and e["filters"] == {"year": ["2025"]}
        for e in entries
    )
    denied = [e for e in entries if e["outcome"] == "denied"]
    assert denied and denied[-1]["role"] == "executive"


# ---------------------------------------------------------------------------
# a filtered total is a figure about people
# ---------------------------------------------------------------------------
def test_a_client_reader_cannot_filter_their_way_to_one_persons_cost(client):
    """The cells of a one-person selection were always withheld. The grand total and the
    'Other (suppressed)' line beside them were not, on screen or in the download."""
    dims = ("role_family", "work_location", "misconduct_category", "year")
    seen: dict = {}
    for action in STATE.run(mode="base").action_costs:
        seen.setdefault(tuple(str(action.dimensions[d]) for d in dims), []).append(action)
    key, (only,) = next((k, v) for k, v in sorted(seen.items()) if len(v) == 1)
    params = {
        "rows": "role_family",
        "cols": "year",
        "filters": json.dumps({d: [v] for d, v in zip(dims, key, strict=True)}),
    }
    for who in ("executive", "hr_analyst", "admin"):
        on_screen = client.get("/costs/matrix", params=params, headers=headers(who, f"one-{who}"))
        assert on_screen.status_code == 200
        body = on_screen.json()
        assert body["totals"]["withheld"] is True and body["totals"]["net"] is None
        assert body["suppression"]["other_suppressed_net"] is None
        download = client.get(
            "/exports/matrix.csv", params=params, headers=headers(who, f"one-{who}")
        )
        assert download.status_code == 200
        assert "selection withheld" in download.text and "TOTAL" not in download.text
        for text in (on_screen.text, download.text):
            assert str(only.net) not in text and str(only.gross) not in text

    # An ordinary question is answered as before.
    whole = client.get("/costs/matrix", headers=headers("executive", "one-whole")).json()
    assert whole["totals"]["withheld"] is False and whole["totals"]["net"] is not None
    assert "TOTAL" in client.get("/exports/matrix.csv", headers=headers("executive")).text


def test_the_summary_lists_no_department_about_fewer_people_than_the_minimum(client):
    people: dict = {}
    for action in STATE.run(mode="base").action_costs:
        people.setdefault(str(action.dimensions["department"]), set()).add(action.employee_id)
    listed = client.get("/costs/summary", headers=headers("executive")).json()[
        "department_cost_per_fte_per_year"
    ]
    assert listed, "the sample has departments large enough to list"
    for row in listed:
        assert len(people[row["department"]]) >= 5, row
