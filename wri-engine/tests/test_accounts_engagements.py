"""The licensed (signed-in) mode: accounts, engagements, isolation and persistence.

Each test builds its own installation in a temporary folder -- its own database, its own
engagement files -- so nothing here touches the synthetic demo or another test.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from wri_engine.api.main import create_app
from wri_engine.api.state import SOURCE_MARKER
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.settings import Settings
from wri_engine.store import AuthError, Store, hash_password, verify_password

SAMPLE = SYNTHETIC_DIR / "sample"
OWNER = ("owner@firm.example", "owner-password-123")
LEAD = ("lead@firm.example", "lead-password-1234")
ANALYST = ("analyst@firm.example", "analyst-password-12")
CLIENT = ("cfo@client.example", "client-password-123")
OUTSIDER = ("other@firm.example", "outsider-password-1")


class Installation:
    def __init__(self, tmp_path):
        self.settings = Settings(auth_mode="local", data_dir=tmp_path)
        self.app = create_app(self.settings)
        self.runtime = self.app.state.runtime
        self.http = TestClient(self.app)
        self.store: Store = self.runtime.store
        self.store.create_user(*OWNER, name="Owner", is_firm_admin=True)
        self._tokens: dict[str, str] = {}

    def auth(self, who: tuple[str, str]) -> dict:
        email, password = who
        if email not in self._tokens:
            response = self.http.post("/auth/login", json={"email": email, "password": password})
            assert response.status_code == 200, response.text
            self._tokens[email] = response.json()["token"]
        return {"Authorization": f"Bearer {self._tokens[email]}"}

    def add_user(self, who: tuple[str, str]) -> None:
        response = self.http.post(
            "/users", json={"email": who[0], "password": who[1]}, headers=self.auth(OWNER)
        )
        assert response.status_code == 201, response.text

    def new_engagement(self, name: str, client_name: str) -> str:
        response = self.http.post(
            "/engagements",
            json={"name": name, "client_name": client_name},
            headers=self.auth(OWNER),
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def add_member(self, engagement_id: str, who: tuple[str, str], role: str) -> None:
        response = self.http.put(
            f"/engagements/{engagement_id}/members",
            json={"email": who[0], "role": role},
            headers=self.auth(OWNER),
        )
        assert response.status_code == 200, response.text

    def upload_sample(self, engagement_id: str, who: tuple[str, str] = OWNER):
        files = [
            ("files", (p.name, p.read_bytes(), "text/csv"))
            for p in sorted(SAMPLE.iterdir())
            if p.is_file()
        ]
        return self.http.post(
            f"/engagements/{engagement_id}/datasets/upload", files=files, headers=self.auth(who)
        )

    def net(self, engagement_id: str, who: tuple[str, str] = OWNER) -> Decimal:
        body = self.http.get(
            f"/engagements/{engagement_id}/costs/summary", headers=self.auth(who)
        ).json()
        return Decimal(body["total_net"])


@pytest.fixture
def site(tmp_path):
    if not (SAMPLE / "hr_empl_master.csv").exists():
        pytest.skip("sample export missing; run `make generate`")
    return Installation(tmp_path)


@pytest.fixture
def staffed(site):
    """One engagement with a lead, an analyst and a client reader, plus an outsider."""
    for person in (LEAD, ANALYST, CLIENT, OUTSIDER):
        site.add_user(person)
    site.engagement = site.new_engagement("FY26 discipline cost review", "Harlow County")
    site.add_member(site.engagement, LEAD, "admin")
    site.add_member(site.engagement, ANALYST, "hr_analyst")
    site.add_member(site.engagement, CLIENT, "executive")
    assert site.upload_sample(site.engagement, LEAD).status_code == 200
    return site


# ---------------------------------------------------------------------------
# passwords and sign-in
# ---------------------------------------------------------------------------
def test_passwords_are_hashed_and_short_ones_refused():
    stored = hash_password("a-long-enough-password")
    assert "a-long-enough-password" not in stored
    assert verify_password("a-long-enough-password", stored)
    assert not verify_password("a-long-enough-passwore", stored)
    assert not verify_password("anything", "not-a-hash")
    with pytest.raises(ValueError, match="at least 12"):
        hash_password("short")


def test_nothing_is_served_without_signing_in(site):
    engagement = site.new_engagement("E", "Client")
    for path in (
        "/auth/me",
        "/engagements",
        "/users",
        f"/engagements/{engagement}/costs/summary",
        f"/engagements/{engagement}/costs/matrix",
        f"/engagements/{engagement}/assumptions",
    ):
        assert site.http.get(path).status_code == 401, path
    assert site.http.get("/health").status_code == 200


def test_the_role_header_grants_nothing(staffed):
    """In demo mode `X-WRI-Role: admin` is all it takes. Here it is ignored."""
    e = staffed.engagement
    assert (
        staffed.http.get(
            f"/engagements/{e}/costs/actions", headers={"X-WRI-Role": "admin"}
        ).status_code
        == 401
    )
    headers = {**staffed.auth(CLIENT), "X-WRI-Role": "admin"}
    assert staffed.http.get(f"/engagements/{e}/costs/actions", headers=headers).status_code == 403


def test_the_unprefixed_demo_routes_do_not_exist(staffed):
    for path in ("/costs/summary", "/costs/matrix", "/assumptions", "/meta"):
        assert staffed.http.get(path, headers=staffed.auth(OWNER)).status_code == 404, path
    response = staffed.http.post("/datasets/load", json={}, headers=staffed.auth(OWNER))
    assert response.status_code == 404


def test_wrong_password_and_unknown_email_look_the_same(site):
    wrong = site.http.post("/auth/login", json={"email": OWNER[0], "password": "x" * 14})
    unknown = site.http.post(
        "/auth/login", json={"email": "nobody@x.example", "password": "x" * 14}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_repeated_failures_lock_the_account(site):
    site.add_user(ANALYST)
    for _ in range(5):
        with pytest.raises(AuthError, match="incorrect"):
            site.store.sign_in(ANALYST[0], "definitely-wrong-password")
    with pytest.raises(AuthError, match="locked"):
        site.store.sign_in(*ANALYST)


def test_sign_out_and_deactivation_end_the_session(staffed):
    headers = dict(staffed.auth(ANALYST))
    assert staffed.http.get("/auth/me", headers=headers).status_code == 200
    assert staffed.http.post("/auth/logout", headers=headers).status_code == 200
    assert staffed.http.get("/auth/me", headers=headers).status_code == 401

    client_headers = dict(staffed.auth(CLIENT))
    client_id = staffed.store.get_user_by_email(CLIENT[0]).id
    response = staffed.http.put(
        f"/users/{client_id}/active", json={"is_active": False}, headers=staffed.auth(OWNER)
    )
    assert response.status_code == 200
    assert staffed.http.get("/auth/me", headers=client_headers).status_code == 401
    assert (
        staffed.http.post(
            "/auth/login", json={"email": CLIENT[0], "password": CLIENT[1]}
        ).status_code
        == 401
    )


def test_tokens_are_stored_hashed(site):
    token = site.auth(OWNER)["Authorization"].split()[1]
    raw = (site.settings.data_dir / "wri.db").read_bytes()
    assert token.encode() not in raw
    assert OWNER[1].encode() not in raw


# ---------------------------------------------------------------------------
# who may do what
# ---------------------------------------------------------------------------
def test_only_a_firm_administrator_creates_users_and_engagements(staffed):
    for who in (LEAD, ANALYST, CLIENT):
        headers = staffed.auth(who)
        assert (
            staffed.http.post(
                "/users", json={"email": "x@y.example", "password": "p" * 14}, headers=headers
            ).status_code
            == 403
        )
        assert (
            staffed.http.post(
                "/engagements", json={"name": "n", "client_name": "c"}, headers=headers
            ).status_code
            == 403
        )
        assert staffed.http.get("/users", headers=headers).status_code == 403


def test_roles_come_from_membership(staffed):
    e = staffed.engagement
    actions = f"/engagements/{e}/costs/actions"
    assert staffed.http.get(actions, headers=staffed.auth(CLIENT)).status_code == 403
    analyst = staffed.http.get(actions, headers=staffed.auth(ANALYST)).json()
    lead = staffed.http.get(actions, headers=staffed.auth(LEAD)).json()
    assert analyst["actions"][0]["employee"].startswith("EMP-")
    assert not lead["actions"][0]["employee"].startswith("EMP-")
    for path in ("costs/summary", "costs/matrix", "exports/matrix.csv", "assumptions"):
        assert (
            staffed.http.get(f"/engagements/{e}/{path}", headers=staffed.auth(CLIENT)).status_code
            == 200
        ), path


def test_only_the_engagement_admin_manages_data_overrides_and_members(staffed):
    e = staffed.engagement
    for who in (ANALYST, CLIENT):
        assert staffed.upload_sample(e, who).status_code == 403
        assert (
            staffed.http.post(
                f"/engagements/{e}/datasets/load", json={}, headers=staffed.auth(who)
            ).status_code
            == 403
        )
        assert (
            staffed.http.put(
                f"/engagements/{e}/assumptions/benefits_multiplier_sworn/override",
                json={"value": "1.9", "reason": "client actuarial report"},
                headers=staffed.auth(who),
            ).status_code
            == 403
        )
        assert (
            staffed.http.get(f"/engagements/{e}/members", headers=staffed.auth(who)).status_code
            == 403
        )
    assert (
        staffed.http.get(f"/engagements/{e}/members", headers=staffed.auth(LEAD)).status_code == 200
    )


def test_a_non_member_cannot_tell_the_engagement_exists(staffed):
    e = staffed.engagement
    headers = staffed.auth(OUTSIDER)
    real = staffed.http.get(f"/engagements/{e}/costs/summary", headers=headers)
    made_up = staffed.http.get("/engagements/0000000000000000/costs/summary", headers=headers)
    assert real.status_code == made_up.status_code == 404
    assert real.json() == made_up.json()
    assert staffed.http.get("/engagements", headers=headers).json()["engagements"] == []
    assert staffed.http.get(f"/engagements/{e}/members", headers=headers).status_code == 404


def test_a_removed_member_loses_access_at_once(staffed):
    e = staffed.engagement
    analyst_id = staffed.store.get_user_by_email(ANALYST[0]).id
    assert (
        staffed.http.get(
            f"/engagements/{e}/costs/summary", headers=staffed.auth(ANALYST)
        ).status_code
        == 200
    )
    staffed.http.delete(f"/engagements/{e}/members/{analyst_id}", headers=staffed.auth(LEAD))
    assert (
        staffed.http.get(
            f"/engagements/{e}/costs/summary", headers=staffed.auth(ANALYST)
        ).status_code
        == 404
    )


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------
def test_an_empty_engagement_says_so(site):
    e = site.new_engagement("Empty", "Client")
    response = site.http.get(f"/engagements/{e}/costs/summary", headers=site.auth(OWNER))
    assert response.status_code == 409
    assert "no dataset" in response.json()["detail"]


def test_upload_loads_the_dataset_and_drops_the_synthetic_label(staffed):
    e = staffed.engagement
    meta = staffed.http.get(f"/engagements/{e}/meta", headers=staffed.auth(LEAD)).json()
    assert meta["synthetic"] is False
    assert meta["agency"] == "Harlow County"
    assert meta["engagement"]["id"] == e
    summary = staffed.http.get(f"/engagements/{e}/costs/summary", headers=staffed.auth(LEAD)).json()
    assert "Synthetic" not in summary["caption"]
    assert Decimal(summary["total_net"]) > 0
    assert "Synthetic" not in summary["agency_note"]


def test_the_loader_cannot_be_pointed_at_a_server_path(staffed):
    e = staffed.engagement
    for path in ("/etc", str(SAMPLE), "../../.."):
        response = staffed.http.post(
            f"/engagements/{e}/datasets/load", json={"path": path}, headers=staffed.auth(LEAD)
        )
        assert response.status_code == 400, path


@pytest.mark.parametrize(
    "name", ["../evil.csv", "..\\\\evil.csv", "/etc/cron.d/x.csv", "notes.txt", ".hidden.csv", ""]
)
def test_upload_refuses_unsafe_file_names(staffed, name):
    e = staffed.engagement
    before = staffed.net(e)
    response = staffed.http.post(
        f"/engagements/{e}/datasets/upload",
        files=[("files", (name or "x", b"a,b\n1,2\n", "text/csv"))]
        if name
        else [("files", ("bad name.csv", b"a,b\n1,2\n", "text/csv"))],
        headers=staffed.auth(LEAD),
    )
    assert response.status_code == 400
    assert staffed.net(e) == before
    stray = [p for p in staffed.settings.data_dir.rglob("*evil*")]
    assert stray == []


def test_a_bad_upload_leaves_the_existing_dataset_alone(staffed):
    e = staffed.engagement
    before = staffed.net(e)
    response = staffed.http.post(
        f"/engagements/{e}/datasets/upload",
        files=[("files", ("hr_empl_master.csv", b"this,is,not\nthe,right,file\n", "text/csv"))],
        headers=staffed.auth(LEAD),
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "nothing was changed" in detail["message"]
    assert detail["counts_by_rule"]["missing_source_file"] >= 1
    assert staffed.net(e) == before
    source = staffed.runtime.workspaces.source_dir(e)
    kept = {p.name for p in source.iterdir()} - {SOURCE_MARKER}
    assert kept == {p.name for p in SAMPLE.iterdir() if p.is_file()}
    assert not (source.parent / "incoming").exists()


def test_an_oversized_upload_is_refused(tmp_path):
    site = Installation(tmp_path)
    object.__setattr__(site.settings, "max_upload_mb", 0)
    e = site.new_engagement("E", "Client")
    response = site.http.post(
        f"/engagements/{e}/datasets/upload",
        files=[("files", ("hr_empl_master.csv", b"x" * 10, "text/csv"))],
        headers=site.auth(OWNER),
    )
    assert response.status_code == 413


def test_engagements_keep_their_data_apart(staffed):
    first = staffed.engagement
    second = staffed.new_engagement("Second client", "Other County")
    assert (
        staffed.http.get(
            f"/engagements/{second}/costs/summary", headers=staffed.auth(OWNER)
        ).status_code
        == 409
    )
    # The lead belongs to the first engagement only.
    assert (
        staffed.http.get(
            f"/engagements/{second}/costs/summary", headers=staffed.auth(LEAD)
        ).status_code
        == 404
    )
    assert staffed.upload_sample(second).status_code == 200
    first_dir = staffed.runtime.workspaces.source_dir(first)
    second_dir = staffed.runtime.workspaces.source_dir(second)
    assert first_dir != second_dir
    assert {p.name for p in first_dir.iterdir()} == {p.name for p in second_dir.iterdir()}


# ---------------------------------------------------------------------------
# assumption overrides
# ---------------------------------------------------------------------------
def override(site, engagement, assumption_id, value, who=LEAD, reason="client's own figure"):
    return site.http.put(
        f"/engagements/{engagement}/assumptions/{assumption_id}/override",
        json={"value": value, "reason": reason},
        headers=site.auth(who),
    )


def test_an_override_changes_this_engagement_only(staffed):
    first = staffed.engagement
    second = staffed.new_engagement("Second client", "Other County")
    staffed.upload_sample(second)
    baseline = staffed.net(first)
    assert staffed.net(second) == baseline

    assert override(staffed, first, "benefits_multiplier_sworn", "2.05").status_code == 200
    assert staffed.net(first) > baseline
    assert staffed.net(second) == baseline

    listed = staffed.http.get(
        f"/engagements/{first}/assumptions", headers=staffed.auth(CLIENT)
    ).json()
    row = next(a for a in listed["assumptions"] if a["id"] == "benefits_multiplier_sworn")
    assert listed["override_count"] == 1
    assert row["overridden"] is True
    assert Decimal(row["effective_value"]) == Decimal("2.05")
    assert Decimal(row["value"]) == Decimal("1.75"), "the default in the registry is untouched"

    other = staffed.http.get(
        f"/engagements/{second}/assumptions", headers=staffed.auth(OWNER)
    ).json()
    assert other["override_count"] == 0


def test_an_override_records_who_and_why_and_can_be_cleared(staffed):
    e = staffed.engagement
    baseline = staffed.net(e)
    override(staffed, e, "c4_outside_counsel_hourly_rate", "450", reason="2026 panel counsel rates")
    listed = staffed.http.get(
        f"/engagements/{e}/assumptions/overrides", headers=staffed.auth(CLIENT)
    ).json()["overrides"]
    assert listed == [
        {
            "assumption_id": "c4_outside_counsel_hourly_rate",
            "value": "450",
            "default_value": "325",
            "unit": "usd_per_hour",
            "reason": "2026 panel counsel rates",
            "set_by": LEAD[0],
            "set_at": listed[0]["set_at"],
        }
    ]
    cleared = staffed.http.delete(
        f"/engagements/{e}/assumptions/c4_outside_counsel_hourly_rate/override",
        headers=staffed.auth(LEAD),
    )
    assert cleared.status_code == 200
    assert staffed.net(e) == baseline


def test_bad_overrides_are_refused(staffed):
    e = staffed.engagement
    assert override(staffed, e, "not_an_assumption", "1").status_code == 404
    assert override(staffed, e, "benefits_multiplier_sworn", "-1").status_code == 400
    assert override(staffed, e, "benefits_multiplier_sworn", "NaN").status_code in (400, 422)
    assert override(staffed, e, "benefits_multiplier_sworn", "2", reason="").status_code == 422


def test_a_scenario_is_compared_against_the_engagement_baseline(staffed):
    e = staffed.engagement
    override(staffed, e, "benefits_multiplier_sworn", "2.05")
    with_override = staffed.net(e)
    body = staffed.http.post(
        f"/engagements/{e}/scenarios/run",
        json={"overrides": {"c4_outside_counsel_hourly_rate": 550}},
        headers=staffed.auth(ANALYST),
    ).json()
    assert Decimal(body["baseline_net"]) == with_override
    assert Decimal(body["scenario_net"]) > with_override
    assert staffed.net(e) == with_override, "a scenario changes nothing that is stored"


# ---------------------------------------------------------------------------
# persistence, differencing, audit
# ---------------------------------------------------------------------------
def test_nothing_is_lost_on_restart(staffed, tmp_path):
    e = staffed.engagement
    override(staffed, e, "benefits_multiplier_sworn", "2.05")
    before = staffed.net(e)

    restarted = TestClient(create_app(Settings(auth_mode="local", data_dir=tmp_path)))
    token = restarted.post("/auth/login", json={"email": CLIENT[0], "password": CLIENT[1]}).json()[
        "token"
    ]
    headers = {"Authorization": f"Bearer {token}"}
    summary = restarted.get(f"/engagements/{e}/costs/summary", headers=headers)
    assert summary.status_code == 200
    assert Decimal(summary.json()["total_net"]) == before
    # A session opened before the restart is still good, because it lives in the database.
    assert restarted.get("/auth/me", headers=staffed.auth(LEAD)).status_code == 200


def test_the_differencing_guard_cannot_be_reset_by_the_caller(staffed):
    """Two overlapping queries can expose a small group by subtraction. The ledger that stops
    this is keyed to the signed-in user; a client-chosen session header must not reset it."""
    e = staffed.engagement

    def matrix(filters, session=None):
        headers = dict(staffed.auth(CLIENT))
        if session:
            headers["X-WRI-Session"] = session
        return staffed.http.get(
            f"/engagements/{e}/costs/matrix",
            params={"rows": "department", "cols": "year", "filters": json.dumps(filters)},
            headers=headers,
        ).json()

    first = matrix({})
    key = staffed.runtime.workspaces.get(e)._ledgers
    assert list(key) == [f"user:{staffed.store.get_user_by_email(CLIENT[0]).id}"]
    matrix({}, session="a-fresh-session")
    matrix({}, session="another")
    assert list(key) == [f"user:{staffed.store.get_user_by_email(CLIENT[0]).id}"]
    assert first["suppression"]["reconciles"] is True


def test_the_audit_log_names_the_user_and_the_engagement(staffed, tmp_path):
    from wri_engine.access.audit import AuditLog
    from wri_engine.api import main

    original = main.AUDIT
    main.AUDIT = AuditLog(tmp_path / "audit.jsonl")
    try:
        e = staffed.engagement
        staffed.http.get(f"/engagements/{e}/costs/actions", headers=staffed.auth(CLIENT))
        staffed.http.get(f"/engagements/{e}/costs/summary", headers=staffed.auth(ANALYST))
        override(staffed, e, "c4_outside_counsel_hourly_rate", "450")
        entries = main.AUDIT.tail(10)
    finally:
        main.AUDIT = original
    denied = next(x for x in entries if x["outcome"] == "denied")
    assert (denied["user"], denied["role"], denied["engagement"]) == (CLIENT[0], "executive", e)
    allowed = next(x for x in entries if x["endpoint"] == "/costs/summary")
    assert (allowed["user"], allowed["role"]) == (ANALYST[0], "hr_analyst")
    change = next(x for x in entries if "override" in x["endpoint"])
    assert change["detail"]["value"] == "450"
    assert change["user"] == LEAD[0]


def test_a_private_sector_engagement_can_be_opened(site):
    """The private-sector cost model is installed, so the server accepts it -- and the
    engagement carries the sector it was opened for."""
    response = site.http.post(
        "/engagements",
        json={"name": "n", "client_name": "c", "sector": "private_sector"},
        headers=site.auth(OWNER),
    )
    assert response.status_code == 201
    assert response.json()["sector"] == "private_sector"


def test_a_sector_with_no_cost_model_is_refused(site):
    """A sector the engine cannot cost must be refused at the door, naming what is
    installed, rather than accepted and priced under the wrong model."""
    response = site.http.post(
        "/engagements",
        json={"name": "n", "client_name": "c", "sector": "nonprofit_sector"},
        headers=site.auth(OWNER),
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "not available" in detail
    assert "public_sector" in detail and "private_sector" in detail


def test_the_sector_list_is_served_from_the_installed_cost_models(site):
    """The UI must not carry its own sector list. Everything the server will accept is
    offered here, with the components that sector can produce."""
    response = site.http.get("/sectors", headers=site.auth(OWNER))
    assert response.status_code == 200
    body = response.json()
    by_id = {s["id"]: s for s in body["sectors"]}
    assert set(by_id) == {"public_sector", "private_sector"}
    assert body["default"] == "public_sector"
    assert by_id["public_sector"]["components"] == [
        "C1",
        "C2",
        "C3",
        "C3-offset",
        "C4",
        "C5",
        "C5-offset",
    ]
    assert "C6" in by_id["private_sector"]["components"]
    assert "C7" in by_id["private_sector"]["components"]


def test_the_sector_list_needs_a_signed_in_user(site):
    assert site.http.get("/sectors").status_code == 401


def test_an_engagement_is_priced_under_its_own_sector(site):
    """Two engagements in one installation, two cost models. The workspace takes the sector
    from the engagement record, so neither is priced under the other's."""
    private = site.http.post(
        "/engagements",
        json={"name": "p", "client_name": "c", "sector": "private_sector"},
        headers=site.auth(OWNER),
    ).json()["id"]
    public = site.http.post(
        "/engagements",
        json={"name": "q", "client_name": "c", "sector": "public_sector"},
        headers=site.auth(OWNER),
    ).json()["id"]
    workspaces = site.runtime.workspaces
    assert workspaces.get(private).sector == "private_sector"
    assert workspaces.get(public).sector == "public_sector"
