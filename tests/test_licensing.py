"""License keys and the usage log.

Every test makes its own signing key at run time. No signing key is kept in this repository,
and none may be: it is the one secret the licensor has.
"""

from __future__ import annotations

import json
import stat
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_accounts_engagements import ANALYST, CLIENT, LEAD, OUTSIDER, OWNER, SAMPLE, override
from wri_engine.api.main import create_app
from wri_engine.licensing import cli
from wri_engine.licensing.keys import (
    LicenseError,
    check_for_engagement,
    generate_keypair,
    issue,
    load_private_key,
    load_public_key,
    normalize_name,
    verify,
)
from wri_engine.licensing.usage import GENESIS, from_jsonl, to_jsonl, verify_chain
from wri_engine.settings import Settings
from wri_engine.store import Store

TODAY = date.today()
NEXT_YEAR = TODAY + timedelta(days=365)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(scope="module")
def keypair():
    private_pem, public_pem = generate_keypair()
    return load_private_key(private_pem), public_pem


def key_for(keypair, client="Pine Valley County", sector="public_sector", **claims) -> str:
    private, _ = keypair
    claims.setdefault("licensee", "Northgate Advisory LLP")
    claims.setdefault("issued_on", TODAY - timedelta(days=30))
    claims.setdefault("expires_on", NEXT_YEAR)
    return issue(private, client_name=client, sector=sector, **claims)


# ---------------------------------------------------------------------------
# the key itself
# ---------------------------------------------------------------------------
def test_a_key_says_what_it_was_issued_for(keypair):
    _, public_pem = keypair
    key = key_for(keypair, max_employees=500)
    assert key.startswith("WRI1.") and key.count(".") == 2
    license_ = verify(key, load_public_key(public_pem))
    assert (license_.licensee, license_.client_name) == (
        "Northgate Advisory LLP",
        "Pine Valley County",
    )
    assert (license_.sector, license_.expires_on, license_.max_employees) == (
        "public_sector",
        NEXT_YEAR,
        500,
    )
    assert len(license_.key_id) == 16


def test_a_key_survives_being_pasted_with_line_breaks_and_spaces(keypair):
    _, public_pem = keypair
    key = key_for(keypair)
    wrapped = "  " + key[:40] + "\n" + key[40:90] + " \r\n" + key[90:] + "\n"
    assert verify(wrapped, load_public_key(public_pem)).client_name == "Pine Valley County"


def test_a_key_cannot_be_altered(keypair):
    """Nobody without the signing key can extend a date or change a client. Changing any
    part of what a key says, or its signature, makes it fail."""
    import base64

    _, public_pem = keypair
    public = load_public_key(public_pem)
    prefix, body, signature = key_for(keypair).split(".")
    payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    payload["expires_on"] = "2099-12-31"
    forged = base64.urlsafe_b64encode(json.dumps(payload).encode()).rstrip(b"=").decode()
    with pytest.raises(LicenseError, match="signature does not check out"):
        verify(f"{prefix}.{forged}.{signature}", public)
    with pytest.raises(LicenseError, match="signature does not check out"):
        verify(f"{prefix}.{body}.{signature[:-4]}AAAA", public)


def test_a_key_signed_by_someone_else_is_refused(keypair):
    stranger_private, _ = generate_keypair()
    stray = issue(
        load_private_key(stranger_private),
        licensee="x",
        client_name="Pine Valley County",
        sector="public_sector",
        issued_on=TODAY,
        expires_on=NEXT_YEAR,
    )
    with pytest.raises(LicenseError, match="signature does not check out"):
        verify(stray, load_public_key(keypair[1]))


@pytest.mark.parametrize("text_", ["", "hello", "WRI1.only-two", "WRI2.a.b", "a.b.c.d"])
def test_text_that_is_not_a_key_says_so(keypair, text_):
    with pytest.raises(LicenseError, match="not a license key"):
        verify(text_, load_public_key(keypair[1]))


def test_a_key_is_for_one_client_one_sector_and_a_time(keypair):
    license_ = verify(key_for(keypair), load_public_key(keypair[1]))
    ok = {"client_name": "Pine Valley County", "sector": "public_sector", "today": TODAY}
    check_for_engagement(license_, **ok)
    # Case, spacing and punctuation in a client's name do not matter.
    check_for_engagement(license_, **{**ok, "client_name": "  pine valley  COUNTY. "})
    assert normalize_name("Ridgeline Builders, Inc.") == normalize_name("ridgeline builders inc")
    with pytest.raises(LicenseError, match="issued for the client"):
        check_for_engagement(license_, **{**ok, "client_name": "Other County"})
    with pytest.raises(LicenseError, match="public sector cost model"):
        check_for_engagement(license_, **{**ok, "sector": "private_sector"})
    # A key is good through its last day and not a day longer.
    check_for_engagement(license_, **{**ok, "today": NEXT_YEAR})
    with pytest.raises(LicenseError, match="expired on"):
        check_for_engagement(license_, **{**ok, "today": NEXT_YEAR + timedelta(days=1)})


def test_a_key_that_makes_no_sense_is_not_issued(keypair):
    private, _ = keypair
    base = {"licensee": "f", "client_name": "c", "sector": "public_sector", "issued_on": TODAY}
    with pytest.raises(LicenseError, match="before it is issued"):
        issue(private, **base, expires_on=YESTERDAY)
    with pytest.raises(LicenseError, match="max_employees"):
        issue(private, **base, expires_on=NEXT_YEAR, max_employees=0)
    with pytest.raises(LicenseError, match="licensee's name"):
        issue(private, **{**base, "licensee": " "}, expires_on=NEXT_YEAR)


# ---------------------------------------------------------------------------
# an installation that enforces licensing
# ---------------------------------------------------------------------------
class Licensed:
    """A licensed installation with the licensor's verification key installed."""

    def __init__(self, tmp_path, keypair):
        self.keypair = keypair
        self.settings = Settings(
            auth_mode="local", data_dir=tmp_path, license_public_key=keypair[1]
        )
        self.app = create_app(self.settings)
        self.runtime = self.app.state.runtime
        self.http = TestClient(self.app)
        self.store: Store = self.runtime.store
        self.store.create_user(*OWNER, name="Owner", is_firm_admin=True)
        self._tokens: dict[str, str] = {}

    def auth(self, who) -> dict:
        if who[0] not in self._tokens:
            response = self.http.post("/auth/login", json={"email": who[0], "password": who[1]})
            self._tokens[who[0]] = response.json()["token"]
        return {"Authorization": f"Bearer {self._tokens[who[0]]}"}

    def open(self, client="Pine Valley County", key=None, sector="public_sector"):
        return self.http.post(
            "/engagements",
            json={"name": "Review", "client_name": client, "sector": sector, "license_key": key},
            headers=self.auth(OWNER),
        )

    def upload(self, engagement_id, who=OWNER):
        files = [
            ("files", (p.name, p.read_bytes(), "text/csv"))
            for p in sorted(SAMPLE.iterdir())
            if p.is_file()
        ]
        return self.http.post(
            f"/engagements/{engagement_id}/datasets/upload", files=files, headers=self.auth(who)
        )

    def get(self, engagement_id, path, who=OWNER):
        return self.http.get(f"/engagements/{engagement_id}{path}", headers=self.auth(who))


@pytest.fixture
def site(tmp_path, keypair):
    if not (SAMPLE / "hr_empl_master.csv").exists():
        pytest.skip("sample export missing; run `make generate`")
    return Licensed(tmp_path, keypair)


def test_without_a_verification_key_nothing_is_enforced(tmp_path, keypair):
    """The default. An installation that has not been given the licensor's key runs as it
    always did, and cannot be handed a key it has no way to check."""
    app = create_app(Settings(auth_mode="local", data_dir=tmp_path))
    store = app.state.runtime.store
    store.create_user(*OWNER, name="Owner", is_firm_admin=True)
    http = TestClient(app)
    token = http.post("/auth/login", json={"email": OWNER[0], "password": OWNER[1]}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert http.get("/license", headers=headers).json() == {"enforced": False}
    opened = http.post("/engagements", json={"name": "n", "client_name": "c"}, headers=headers)
    assert opened.status_code == 201
    assert opened.json()["license"] == {"status": "not_required", "problem": ""}
    refused = http.post(
        "/engagements",
        json={"name": "n", "client_name": "c", "license_key": key_for(keypair, "c")},
        headers=headers,
    )
    assert refused.status_code == 400 and "no verification key" in refused.json()["detail"]


def test_the_synthetic_walkthrough_is_never_licensed(tmp_path, keypair):
    settings = Settings(auth_mode="demo", data_dir=tmp_path, license_public_key=keypair[1])
    assert settings.licensing_enforced is False
    http = TestClient(create_app(settings))
    assert http.get("/costs/summary", headers={"X-WRI-Role": "executive"}).status_code == 200


def test_an_engagement_needs_a_key_issued_for_it(site, keypair):
    assert site.http.get("/license", headers=site.auth(OWNER)).json() == {"enforced": True}
    missing = site.open()
    assert missing.status_code == 400 and "license key is required" in missing.json()["detail"]
    assert site.open(key="WRI1.nonsense.nonsense").status_code == 400

    other_client = site.open(key=key_for(keypair, "Other County"))
    assert other_client.status_code == 400
    assert "issued for the client 'Other County'" in other_client.json()["detail"]
    wrong_sector = site.open(key=key_for(keypair, sector="private_sector"))
    assert "private sector cost model" in wrong_sector.json()["detail"]
    lapsed = site.open(key=key_for(keypair, issued_on=YESTERDAY, expires_on=YESTERDAY))
    assert "expired on" in lapsed.json()["detail"]
    assert site.store.engagements_for(site.store.get_user_by_email(OWNER[0])) == []

    opened = site.open(key=key_for(keypair, max_employees=5000))
    assert opened.status_code == 201, opened.text
    standing = opened.json()["license"]
    assert standing["status"] == "valid"
    assert standing["licensee"] == "Northgate Advisory LLP"
    assert (standing["expires_on"], standing["max_employees"]) == (NEXT_YEAR.isoformat(), 5000)
    # The key is kept, and is never sent back.
    assert "WRI1." not in opened.text


def test_one_key_opens_one_engagement(site, keypair):
    key = key_for(keypair)
    assert site.open(key=key).status_code == 201
    again = site.open(key=key)
    assert again.status_code == 400
    assert "already in use by another engagement" in again.json()["detail"]
    # A second engagement for the same client needs a second key.
    assert site.open(key=key_for(keypair)).status_code == 201


def test_an_engagement_with_no_key_is_closed_until_one_is_added(site, keypair):
    """An engagement opened before the installation was given its verification key has no
    key. Its results are closed, and it can still be managed and put right."""
    owner = site.store.get_user_by_email(OWNER[0])
    engagement = site.store.create_engagement(
        name="Old", client_name="Pine Valley County", sector="public_sector", created_by=owner.id
    )
    e = engagement.id
    for path in ("/meta", "/costs/summary", "/assumptions", "/exports/report.pdf"):
        refused = site.get(e, path)
        assert refused.status_code == 402, path
        assert "no license key" in refused.json()["detail"]
    assert site.upload(e).status_code == 402
    # Managing the engagement does not need a key, or it could never be given one.
    assert site.http.get(f"/engagements/{e}/members", headers=site.auth(OWNER)).status_code == 200
    listed = site.http.get("/engagements", headers=site.auth(OWNER)).json()["engagements"]
    assert listed[0]["license"]["status"] == "missing"

    url = f"/engagements/{e}/license"
    bad = site.http.put(
        url, json={"license_key": key_for(keypair, "Other County")}, headers=site.auth(OWNER)
    )
    assert bad.status_code == 400
    added = site.http.put(url, json={"license_key": key_for(keypair)}, headers=site.auth(OWNER))
    assert added.status_code == 200 and added.json()["license"]["status"] == "valid"
    assert site.upload(e).status_code == 200
    assert site.get(e, "/costs/summary").status_code == 200


def test_only_a_firm_administrator_adds_a_key(site, keypair):
    for person in (LEAD, ANALYST):
        site.http.post(
            "/users", json={"email": person[0], "password": person[1]}, headers=site.auth(OWNER)
        )
    e = site.open(key=key_for(keypair)).json()["id"]
    site.http.put(
        f"/engagements/{e}/members",
        json={"email": LEAD[0], "role": "admin"},
        headers=site.auth(OWNER),
    )
    for who in (LEAD, ANALYST):
        denied = site.http.put(
            f"/engagements/{e}/license",
            json={"license_key": key_for(keypair)},
            headers=site.auth(who),
        )
        assert denied.status_code == 403
        assert site.http.get("/license/usage", headers=site.auth(who)).status_code == 403
        assert site.http.get("/license/usage.jsonl", headers=site.auth(who)).status_code == 403
    assert site.http.put(f"/engagements/{e}/license", json={"license_key": "x"}).status_code == 401
    unknown = site.http.put(
        "/engagements/doesnotexist/license",
        json={"license_key": key_for(keypair)},
        headers=site.auth(OWNER),
    )
    assert unknown.status_code == 404


def test_someone_outside_an_engagement_learns_nothing_about_its_key(site, keypair):
    site.http.post(
        "/users", json={"email": OUTSIDER[0], "password": OUTSIDER[1]}, headers=site.auth(OWNER)
    )
    owner = site.store.get_user_by_email(OWNER[0])
    unkeyed = site.store.create_engagement(
        name="Old", client_name="c", sector="public_sector", created_by=owner.id
    )
    # Not 402: membership is settled first, so the answer is the same as for any engagement
    # they are not in.
    assert site.get(unkeyed.id, "/costs/summary", OUTSIDER).status_code == 404


def test_a_lapsed_key_leaves_the_work_readable_and_stops_new_work(site, keypair):
    """A firm that delivered a report can still open it after the key lapses. It cannot
    carry on working: no new data, no changed value, no scenario."""
    for person in (LEAD, CLIENT):
        site.http.post(
            "/users", json={"email": person[0], "password": person[1]}, headers=site.auth(OWNER)
        )
    e = site.open(key=key_for(keypair)).json()["id"]
    for person, role in ((LEAD, "admin"), (CLIENT, "executive")):
        site.http.put(
            f"/engagements/{e}/members",
            json={"email": person[0], "role": role},
            headers=site.auth(OWNER),
        )
    assert site.upload(e, LEAD).status_code == 200
    before = site.get(e, "/costs/summary", CLIENT).json()["total_net"]

    lapsed = key_for(keypair, issued_on=TODAY - timedelta(days=400), expires_on=YESTERDAY)
    license_ = verify(lapsed, load_public_key(keypair[1]))
    site.store.set_license(e, lapsed, license_.key_id)

    assert site.get(e, "/costs/summary", CLIENT).json()["total_net"] == before
    assert site.get(e, "/exports/report.pdf", CLIENT).status_code == 200
    assert site.get(e, "/meta", LEAD).json()["engagement"]["license"]["status"] == "expired"
    refused = site.upload(e, LEAD)
    assert refused.status_code == 402 and "expired on" in refused.json()["detail"]
    assert override(site, e, "c4_outside_counsel_hourly_rate", "450").status_code == 402
    scenario = site.http.post(
        f"/engagements/{e}/scenarios/run", json={"overrides": {}}, headers=site.auth(LEAD)
    )
    assert scenario.status_code == 402
    assert site.get(e, "/costs/summary", CLIENT).json()["total_net"] == before

    renewed = site.http.put(
        f"/engagements/{e}/license",
        json={"license_key": key_for(keypair)},
        headers=site.auth(OWNER),
    )
    assert renewed.json()["license"]["status"] == "valid"
    assert site.upload(e, LEAD).status_code == 200


def test_a_key_that_is_tampered_with_in_the_database_closes_the_engagement(site, keypair):
    e = site.open(key=key_for(keypair)).json()["id"]
    assert site.upload(e).status_code == 200
    stored = site.store.get_engagement(e).license_key
    site.store.set_license(e, stored[:-6] + "AAAAAA", "whatever")
    refused = site.get(e, "/costs/summary")
    assert refused.status_code == 402 and "signature does not check out" in refused.json()["detail"]


def test_a_dataset_larger_than_the_key_covers_changes_nothing(site, keypair):
    small = site.open(key=key_for(keypair, max_employees=10)).json()["id"]
    refused = site.upload(small)
    assert refused.status_code == 402
    assert "covers up to 10 employees" in refused.json()["detail"]
    # Nothing was loaded: there is still no dataset.
    assert site.get(small, "/costs/summary").status_code == 409
    roomy = site.open(key=key_for(keypair, max_employees=100000)).json()["id"]
    assert site.upload(roomy).status_code == 200


# ---------------------------------------------------------------------------
# the usage log
# ---------------------------------------------------------------------------
def test_the_usage_log_records_what_was_done_as_counts(site, keypair):
    e = site.open(key=key_for(keypair)).json()["id"]
    key_id = site.store.get_engagement(e).license_key_id
    assert site.upload(e).status_code == 200
    assert site.get(e, "/exports/report.pdf").status_code == 200
    assert site.get(e, "/exports/report.xlsx").status_code == 200

    log = site.http.get("/license/usage", headers=site.auth(OWNER)).json()
    assert log["intact"] is True and log["problems"] == []
    entries = log["entries"]
    assert [x["event"] for x in entries] == [
        "engagement_opened",
        "dataset_loaded",
        "report_generated",
        "report_generated",
    ]
    assert [x["seq"] for x in entries] == [1, 2, 3, 4]
    assert all(x["key_id"] == key_id and x["engagement_id"] == e for x in entries)
    assert entries[0]["previous_hash"] == GENESIS
    loaded = entries[1]["detail"]
    assert set(loaded) == {"employees", "actions"} and loaded["employees"] > 0
    assert entries[2]["detail"] == {"format": "pdf", "mode": "base"}
    # Counts and labels only: no employee, no action, no name from the client's file.
    dumped = json.dumps(entries)
    assert "Pine Valley" not in dumped and "E0" not in dumped


def test_an_exported_log_shows_whether_it_has_been_edited(site, keypair):
    e = site.open(key=key_for(keypair)).json()["id"]
    site.upload(e)
    site.get(e, "/exports/report.pdf")
    export = site.http.get("/license/usage.jsonl", headers=site.auth(OWNER))
    assert export.headers["content-disposition"] == 'attachment; filename="wri_usage_log.jsonl"'
    records = from_jsonl(export.text)
    assert len(records) == 3 and verify_chain(records) == []
    assert to_jsonl(records) == export.text

    removed = [records[0], records[2]]
    assert any("missing or out of order" in p for p in verify_chain(removed))
    changed = [dict(r) for r in records]
    changed[1]["detail"] = {"employees": 1, "actions": 1}
    assert any("contents have been changed" in p for p in verify_chain(changed))
    reordered = [records[1], records[0], records[2]]
    assert verify_chain(reordered)
    truncated_start = records[1:]
    assert verify_chain(truncated_start)


def test_editing_the_log_in_the_database_is_reported_by_the_installation_too(site, keypair):
    e = site.open(key=key_for(keypair)).json()["id"]
    site.upload(e)
    with site.store.engine.begin() as connection:
        connection.execute(text("UPDATE license_usage SET detail = '{}' WHERE seq = 2"))
    log = site.http.get("/license/usage", headers=site.auth(OWNER)).json()
    assert log["intact"] is False
    assert any("seq 2" in p and "changed" in p for p in log["problems"])


# ---------------------------------------------------------------------------
# the licensor's tool
# ---------------------------------------------------------------------------
def test_the_licensors_tool_makes_keys_issues_one_and_reads_a_log(tmp_path, capsys, site, keypair):
    folder = tmp_path / "keys"
    assert cli.main(["keygen", "--out-dir", str(folder)]) == 0
    signing = folder / cli.SIGNING_KEY_FILE
    public = folder / cli.VERIFICATION_KEY_FILE
    # The signing key is readable by its owner and nobody else.
    assert stat.S_IMODE(signing.stat().st_mode) == 0o600
    assert "PRIVATE KEY" in signing.read_text() and "PUBLIC KEY" in public.read_text()
    # Running it again must not replace the key every issued key depends on.
    with pytest.raises(SystemExit, match="already holds a key pair"):
        cli.main(["keygen", "--out-dir", str(folder)])
    capsys.readouterr()

    issued = cli.main(
        [
            "issue",
            "--signing-key", str(signing),
            "--licensee", "Northgate Advisory LLP",
            "--client", "Ridgeline Builders",
            "--sector", "private_sector",
            "--expires", NEXT_YEAR.isoformat(),
            "--max-employees", "250",
        ]
    )  # fmt: skip
    key = capsys.readouterr().out.strip()
    assert issued == 0 and key.startswith("WRI1.")
    license_ = verify(key, load_public_key(public.read_text()))
    assert (license_.client_name, license_.max_employees) == ("Ridgeline Builders", 250)

    assert cli.main(["inspect", key, "--verification-key", str(public)]) == 0
    shown = capsys.readouterr().out
    assert "Ridgeline Builders" in shown and "in date" in shown
    assert cli.main(["inspect", key[:-3] + "AAA", "--verification-key", str(public)]) == 2
    assert "signature does not check out" in capsys.readouterr().err

    e = site.open(key=key_for(keypair)).json()["id"]
    site.upload(e)
    log = tmp_path / "usage.jsonl"
    log.write_text(site.http.get("/license/usage.jsonl", headers=site.auth(OWNER)).text)
    assert cli.main(["verify-log", str(log)]) == 0
    assert "The chain is intact" in capsys.readouterr().out
    lines = log.read_text().splitlines()
    log.write_text(lines[1] + "\n")
    assert cli.main(["verify-log", str(log)]) == 1
    assert "DOES NOT HOLD TOGETHER" in capsys.readouterr().out


def test_no_signing_key_is_kept_in_the_repository():
    """The signing key is the licensor's one secret. If a private key of any kind ever
    lands in the source tree, this fails. A verification (public) key may be kept: it is
    what an installation checks keys against, and it cannot issue one."""
    from wri_engine.paths import PROJECT_ROOT

    marker = "PRIVATE " + "KEY-----"
    looked_at = {".pem", ".key", ".py", ".md", ".yaml", ".yml", ".toml", ".txt", ".json", ""}
    skipped = {".venv", ".git", "__pycache__", "data", ".pytest_cache", ".ruff_cache"}
    offenders = []
    for path in PROJECT_ROOT.rglob("*"):
        if skipped.intersection(path.parts) or not path.is_file():
            continue
        readable = path.suffix in looked_at and path.stat().st_size < 500_000
        if path.name == cli.SIGNING_KEY_FILE or (
            readable and marker in path.read_text(errors="ignore")
        ):
            offenders.append(str(path))
    assert offenders == []


def test_a_verification_key_that_cannot_be_read_stops_the_installation_starting(tmp_path):
    with pytest.raises(LicenseError, match="verification key could not be read"):
        create_app(Settings(auth_mode="local", data_dir=tmp_path, license_public_key="not a key"))
