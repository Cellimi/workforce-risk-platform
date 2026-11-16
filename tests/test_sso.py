"""Single sign-on, against a stand-in identity provider.

Three things are held here. That a person the provider vouches for, who has an account,
gets a session. That nothing the provider (or someone pretending to be it) sends is believed
until it has been checked: every forgery and every stale or misdirected token is refused.
And that the sign-in belongs to the browser that started it.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from oidc_provider import Person, Provider
from test_accounts_engagements import LEAD, OWNER
from wri_engine import admin_cli
from wri_engine.api.main import create_app
from wri_engine.settings import Settings
from wri_engine.sso import OidcClient, SsoConfig, SsoError
from wri_engine.store import Store

PAT = Person(subject="idp-0001", email="pat@firm.example", name="Pat Lead")
SAM = Person(subject="idp-0002", email="sam@firm.example", name="Sam Analyst")


def sso_settings(tmp_path, provider: Provider, **changes) -> Settings:
    fields = {
        "auth_mode": "local",
        "data_dir": tmp_path,
        "sso_issuer": provider.issuer,
        "sso_client_id": provider.client_id,
        "sso_client_secret": provider.client_secret,
        "sso_redirect_url": provider.redirect_url,
        "sso_name": "Firm sign-in",
    }
    return Settings(**{**fields, **changes})


class Site:
    """A licensed installation with single sign-on pointed at the stand-in provider."""

    def __init__(self, tmp_path, provider: Provider, **changes):
        self.provider = provider
        self.settings = sso_settings(tmp_path, provider, **changes)
        self.app = create_app(self.settings)
        self.runtime = self.app.state.runtime
        # Reach the stand-in provider in-process. Nothing else about the client changes.
        self.runtime.sso = OidcClient(self.settings.sso, http=provider.http)
        self.store: Store = self.runtime.store
        self.http = TestClient(self.app)
        self.store.create_user(*OWNER, name="Owner", is_firm_admin=True)

    def owner(self) -> dict:
        token = self.http.post(
            "/auth/login", json={"email": OWNER[0], "password": OWNER[1]}
        ).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    def start(self) -> dict:
        response = self.http.post("/auth/sso/start")
        assert response.status_code == 200, response.text
        return response.json()

    def at_the_provider(self, authorization_url: str, person: Person) -> dict:
        """What the browser does: go to the provider, sign in, and be sent back with a code."""
        self.provider.signing_in_as = person
        sent_back = self.provider.http.get(authorization_url)
        assert sent_back.status_code == 302, sent_back.text
        back = urlsplit(sent_back.headers["location"])
        assert f"{back.scheme}://{back.netloc}{back.path}" == self.provider.redirect_url
        query = parse_qs(back.query)
        return {"code": query["code"][0], "state": query["state"][0]}

    def sign_in(self, person: Person):
        started = self.start()
        returned = self.at_the_provider(started["authorization_url"], person)
        return self.http.post(
            "/auth/sso/complete", json={**returned, "binding": started["binding"]}
        )


@pytest.fixture
def provider():
    return Provider()


@pytest.fixture
def site(tmp_path, provider):
    site = Site(tmp_path, provider)
    site.store.create_user(PAT.email, None, name="Pat")
    return site


# ---------------------------------------------------------------------------
# signing in
# ---------------------------------------------------------------------------
def test_someone_with_an_account_signs_in_through_the_provider(site):
    assert site.http.get("/auth/sso").json() == {
        "enabled": True,
        "name": "Firm sign-in",
        "password_sign_in": True,
    }
    response = site.sign_in(PAT)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user"]["email"] == PAT.email and body["user"]["sso_linked"] is True
    me = site.http.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["user"]["email"] == PAT.email
    # The product never saw a password, and the account has none.
    assert site.store.get_user_by_email(PAT.email).has_password is False


def test_the_request_to_the_provider_is_the_one_the_standard_asks_for(site):
    started = site.start()
    url = urlsplit(started["authorization_url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == "https://idp.test/authorize"
    assert query["response_type"] == "code" and query["client_id"] == "wri-client"
    assert query["redirect_uri"] == site.provider.redirect_url
    assert query["scope"] == "openid email profile"
    assert query["code_challenge_method"] == "S256" and len(query["code_challenge"]) == 43
    assert len(query["state"]) >= 40 and len(query["nonce"]) >= 40
    # The client's secret, the nonce's answer and the PKCE secret never go to the browser.
    assert site.provider.client_secret not in started["authorization_url"]
    assert set(started) == {"authorization_url", "binding"}


def test_being_known_to_the_provider_is_not_an_account(site):
    """Switching single sign-on on must not give everyone at the firm an account."""
    refused = site.sign_in(SAM)
    assert refused.status_code == 403
    assert "no account here for sam@firm.example" in refused.json()["detail"]
    assert site.store.get_user_by_email(SAM.email) is None


def test_a_deactivated_account_is_refused(site):
    assert site.sign_in(PAT).status_code == 200
    site.store.set_user_active(site.store.get_user_by_email(PAT.email).id, False)
    refused = site.sign_in(PAT)
    assert refused.status_code == 403 and "deactivated" in refused.json()["detail"]


def test_an_account_answers_to_one_identity_only(site):
    """An email address can pass to a new starter. The account does not pass with it: it is
    tied to the provider's permanent id for the first person, until an administrator says
    otherwise."""
    assert site.sign_in(PAT).status_code == 200
    newcomer = Person(subject="idp-9999", email=PAT.email, name="A different Pat")
    refused = site.sign_in(newcomer)
    assert refused.status_code == 403
    assert "belongs to a different identity" in refused.json()["detail"]

    user = site.store.get_user_by_email(PAT.email)
    owner = site.owner()
    assert site.http.delete(f"/users/{user.id}/sso-link").status_code == 401
    reset = site.http.delete(f"/users/{user.id}/sso-link", headers=owner)
    assert reset.status_code == 200 and reset.json()["sso_linked"] is False
    assert site.http.delete("/users/99999/sso-link", headers=owner).status_code == 404
    assert site.sign_in(newcomer).status_code == 200
    # And the first person is now the one refused.
    assert site.sign_in(PAT).status_code == 403


def test_the_person_is_followed_by_identity_when_their_email_changes(site):
    assert site.sign_in(PAT).status_code == 200
    married = Person(subject=PAT.subject, email="pat.newname@firm.example")
    response = site.sign_in(married)
    assert response.status_code == 200
    assert response.json()["user"]["email"] == PAT.email  # the same account


def test_signing_in_through_the_provider_ends_the_accounts_password(site):
    """Otherwise someone removed at the provider could still walk in with the old password."""
    site.store.create_user(*LEAD)
    lead = Person(subject="idp-0003", email=LEAD[0])
    login = {"email": LEAD[0], "password": LEAD[1]}
    assert site.http.post("/auth/login", json=login).status_code == 200
    assert site.sign_in(lead).status_code == 200
    assert site.http.post("/auth/login", json=login).status_code == 401
    # An account that never uses the provider keeps its password: the way back in if the
    # provider is down.
    assert (
        site.http.post("/auth/login", json={"email": OWNER[0], "password": OWNER[1]}).status_code
        == 200
    )


# ---------------------------------------------------------------------------
# the sign-in belongs to the browser that started it
# ---------------------------------------------------------------------------
def test_a_sign_in_cannot_be_finished_from_another_browser(site):
    """Someone starts a sign-in as themselves and sends the half-finished link to a
    colleague, hoping they will work unawares in the sender's account. The colleague's
    browser does not hold what the sender's was given, so it gets nowhere."""
    senders = site.start()
    returned = site.at_the_provider(senders["authorization_url"], PAT)
    victims = site.start()
    refused = site.http.post("/auth/sso/complete", json={**returned, "binding": victims["binding"]})
    assert (
        refused.status_code == 401 and "not started from this browser" in refused.json()["detail"]
    )
    # And the attempt used the sign-in up: the sender cannot finish it now either.
    again = site.http.post("/auth/sso/complete", json={**returned, "binding": senders["binding"]})
    assert again.status_code == 401


def test_a_sign_in_is_used_once_and_does_not_keep(site, monkeypatch):
    started = site.start()
    returned = site.at_the_provider(started["authorization_url"], PAT)
    payload = {**returned, "binding": started["binding"]}
    assert site.http.post("/auth/sso/complete", json=payload).status_code == 200
    assert site.http.post("/auth/sso/complete", json=payload).status_code == 401

    late = site.start()
    returned = site.at_the_provider(late["authorization_url"], PAT)
    from datetime import timedelta

    from wri_engine.store import db

    real_now = db._now
    monkeypatch.setattr(db, "_now", lambda: real_now() + timedelta(minutes=11))
    expired = site.http.post("/auth/sso/complete", json={**returned, "binding": late["binding"]})
    assert expired.status_code == 401 and "took too long" in expired.json()["detail"]


def test_a_made_up_state_or_code_gets_nowhere(site):
    started = site.start()
    returned = site.at_the_provider(started["authorization_url"], PAT)
    wrong_state = {"code": returned["code"], "state": "made-up", "binding": started["binding"]}
    assert site.http.post("/auth/sso/complete", json=wrong_state).status_code == 401
    started = site.start()
    site.at_the_provider(started["authorization_url"], PAT)
    state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
    wrong_code = {"code": "made-up", "state": state, "binding": started["binding"]}
    refused = site.http.post("/auth/sso/complete", json=wrong_code)
    assert refused.status_code == 401 and "did not complete" in refused.json()["detail"]


def test_started_sign_ins_cannot_pile_up_without_limit(site, monkeypatch):
    from wri_engine.store import db

    monkeypatch.setattr(db, "MAX_PENDING_SSO_REQUESTS", 3)
    for _ in range(3):
        site.start()
    refused = site.http.post("/auth/sso/start")
    assert refused.status_code == 429 and "too many sign-ins" in refused.json()["detail"]


# ---------------------------------------------------------------------------
# nothing the provider sends is believed until it is checked
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "forge",
    ["wrong_key", "none", "hs256"],
    ids=["signed by someone else", "not signed at all", "public key used as a shared secret"],
)
def test_a_forged_identity_token_is_refused(site, forge):
    site.provider.forge = forge
    refused = site.sign_in(PAT)
    assert refused.status_code == 401, refused.text
    assert refused.json()["detail"] == "Single sign-on did not complete. Please start again."
    assert site.store.get_user_by_email(PAT.email).sso_linked is False


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://someone-else.example"},
        {"aud": "another-application"},
        {"aud": ["wri-client", "another-application"]},
        {"exp": 1_000_000_000},
        {"nonce": "an-answer-to-a-different-sign-in"},
        {"nonce": None},
        {"sub": None},
        {"exp": None},
    ],
    ids=[
        "from another provider",
        "for another application",
        "for this and another application",
        "expired",
        "answers a different sign-in",
        "answers no sign-in",
        "names nobody",
        "never expires",
    ],
)
def test_a_token_that_is_stale_or_meant_for_someone_else_is_refused(site, claims):
    site.provider.alter_claims = claims
    refused = site.sign_in(PAT)
    assert refused.status_code == 401, refused.text
    assert site.store.get_user_by_email(PAT.email).sso_linked is False


def test_a_token_for_two_applications_is_accepted_only_if_it_says_it_is_for_this_one(site):
    site.provider.alter_claims = {"aud": ["wri-client", "another"], "azp": "wri-client"}
    assert site.sign_in(PAT).status_code == 200


def test_an_unconfirmed_email_is_refused_unless_the_firm_says_otherwise(tmp_path, provider):
    unconfirmed = Person(subject="idp-0001", email=PAT.email, email_verified=False)
    silent = Person(subject="idp-0001", email=PAT.email, email_verified=None)
    site = Site(tmp_path / "strict", provider)
    site.store.create_user(PAT.email, None)
    for person in (unconfirmed, silent):
        refused = site.sign_in(person)
        assert refused.status_code == 401
        assert "has not confirmed your email address" in refused.json()["detail"]
    # Some providers never say. The firm can accept that, knowingly.
    relaxed = Site(tmp_path / "relaxed", provider, sso_require_verified_email=False)
    relaxed.store.create_user(PAT.email, None)
    assert relaxed.sign_in(silent).status_code == 200


def test_the_email_can_come_from_another_claim(tmp_path, provider):
    """Some providers put the sign-in name somewhere other than `email`."""
    site = Site(
        tmp_path, provider, sso_email_claim="preferred_username", sso_require_verified_email=False
    )
    site.store.create_user("pat.upn@firm.example", None)
    provider.alter_claims = {"email": None, "preferred_username": "Pat.UPN@firm.example"}
    response = site.sign_in(PAT)
    assert (
        response.status_code == 200 and response.json()["user"]["email"] == "pat.upn@firm.example"
    )
    provider.alter_claims = {"email": None, "preferred_username": None}
    refused = site.sign_in(SAM)
    assert (
        refused.status_code == 401
        and "did not say what your email address is" in refused.json()["detail"]
    )


def test_a_provider_that_is_not_who_it_was_configured_as_is_refused(site):
    site.provider.claimed_issuer = "https://someone-else.example"
    refused = site.http.post("/auth/sso/start")
    assert refused.status_code == 503
    assert "could not be reached" in refused.json()["detail"]


def test_the_provider_changing_its_signing_key_does_not_lock_people_out(site):
    assert site.sign_in(PAT).status_code == 200
    site.provider.rotate_key()
    assert site.sign_in(PAT).status_code == 200


def test_the_clients_secret_is_sent_the_way_the_provider_asks(tmp_path, provider):
    site = Site(tmp_path / "basic", provider)
    site.store.create_user(PAT.email, None)
    assert site.sign_in(PAT).status_code == 200
    assert provider.token_requests[-1]["via"] == "client_secret_basic"

    posting = Provider(token_auth_methods=["client_secret_post"])
    site = Site(tmp_path / "post", posting)
    site.store.create_user(PAT.email, None)
    assert site.sign_in(PAT).status_code == 200
    assert posting.token_requests[-1]["via"] == "client_secret_post"

    wrong = Site(tmp_path / "wrong", provider, sso_client_secret="not-the-secret")
    wrong.store.create_user(PAT.email, None)
    assert wrong.sign_in(PAT).status_code == 401


def test_a_provider_whose_issuer_ends_in_a_slash_works(tmp_path):
    slashed = Provider(issuer="https://idp.test")
    slashed.claimed_issuer = "https://idp.test/"
    slashed.alter_claims = {"iss": "https://idp.test/"}
    site = Site(tmp_path, slashed)
    site.store.create_user(PAT.email, None)
    assert site.sign_in(PAT).status_code == 200


def test_what_went_wrong_is_in_the_audit_log_and_not_on_the_screen(site, tmp_path, monkeypatch):
    import json

    from wri_engine.access.audit import AuditLog
    from wri_engine.api import main

    monkeypatch.setattr(main, "AUDIT", AuditLog(tmp_path / "audit.jsonl"))
    site.provider.forge = "wrong_key"
    refused = site.sign_in(PAT)
    assert "Signature" not in refused.text and "signature" not in refused.text
    entries = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    denied = [e for e in entries if e["endpoint"] == "/auth/sso/complete"][-1]
    assert denied["outcome"] == "denied"
    assert "not accepted" in denied["detail"]["reason"]
    site.provider.forge = ""
    assert site.sign_in(PAT).status_code == 200
    allowed = [json.loads(x) for x in (tmp_path / "audit.jsonl").read_text().splitlines()][-1]
    assert (allowed["user"], allowed["outcome"]) == (PAT.email, "allowed")


# ---------------------------------------------------------------------------
# passwords, accounts and settings
# ---------------------------------------------------------------------------
def test_passwords_can_be_switched_off_altogether(tmp_path, provider):
    site = Site(tmp_path, provider, password_sign_in=False)
    site.store.create_user(PAT.email, None, is_firm_admin=True)
    assert site.http.get("/auth/sso").json()["password_sign_in"] is False
    refused = site.http.post("/auth/login", json={"email": OWNER[0], "password": OWNER[1]})
    assert refused.status_code == 403
    assert refused.json()["detail"] == (
        "Passwords are switched off on this installation. Use Firm sign-in."
    )
    token = site.sign_in(PAT).json()["token"]
    admin = {"Authorization": f"Bearer {token}"}
    with_password = site.http.post(
        "/users", json={"email": "x@firm.example", "password": "a-long-password-1"}, headers=admin
    )
    assert with_password.status_code == 400
    without = site.http.post("/users", json={"email": SAM.email}, headers=admin)
    assert without.status_code == 201 and without.json()["has_password"] is False
    assert site.sign_in(SAM).status_code == 200


def test_an_account_without_a_password_needs_single_sign_on_to_exist(tmp_path):
    app = create_app(Settings(auth_mode="local", data_dir=tmp_path))
    store = app.state.runtime.store
    store.create_user(*OWNER, is_firm_admin=True)
    http = TestClient(app)
    token = http.post("/auth/login", json={"email": OWNER[0], "password": OWNER[1]}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    refused = http.post("/users", json={"email": "x@firm.example"}, headers=headers)
    assert refused.status_code == 400 and "a password is required" in refused.json()["detail"]
    # Where it is not set up, nothing about it is offered.
    assert http.get("/auth/sso").json() == {"enabled": False, "name": "", "password_sign_in": True}
    assert http.post("/auth/sso/start").status_code == 404
    assert (
        http.post(
            "/auth/sso/complete", json={"code": "c", "state": "s", "binding": "b"}
        ).status_code
        == 404
    )


def test_an_account_with_no_password_cannot_be_signed_in_to_with_one(site):
    for guess in ("none", "", "x" * 12):
        refused = site.http.post("/auth/login", json={"email": PAT.email, "password": guess})
        assert refused.status_code in (401, 422)


def test_half_configured_sign_on_stops_the_installation_starting(tmp_path, provider):
    with pytest.raises(ValueError, match="WRI_SSO_CLIENT_ID is not set"):
        Settings(auth_mode="local", data_dir=tmp_path, sso_issuer=provider.issuer,
                 sso_redirect_url=provider.redirect_url)  # fmt: skip
    with pytest.raises(ValueError, match="WRI_SSO_REDIRECT_URL must be an https address"):
        sso_settings(tmp_path, provider, sso_redirect_url="http://wri.firm.example/")
    with pytest.raises(ValueError, match="WRI_SSO_ISSUER must be an https address"):
        sso_settings(tmp_path, provider, sso_issuer="http://idp.firm.example")
    with pytest.raises(ValueError, match="must include openid"):
        sso_settings(tmp_path, provider, sso_scopes="email profile")
    with pytest.raises(ValueError, match="nobody could sign in"):
        Settings(auth_mode="local", data_dir=tmp_path, password_sign_in=False)
    # On the machine itself, for testing, plain http is allowed.
    sso_settings(tmp_path, provider, sso_issuer="http://127.0.0.1:9000",
                 sso_redirect_url="http://localhost:8501/")  # fmt: skip


def test_the_settings_are_read_from_the_environment(monkeypatch, tmp_path):
    for name, value in {
        "WRI_AUTH_MODE": "local",
        "WRI_SSO_ISSUER": "https://login.firm.example/tenant",
        "WRI_SSO_CLIENT_ID": "abc",
        "WRI_SSO_CLIENT_SECRET": "s3cret",
        "WRI_SSO_REDIRECT_URL": "https://wri.firm.example/",
        "WRI_SSO_NAME": "Firm login",
        "WRI_SSO_EMAIL_CLAIM": "preferred_username",
        "WRI_SSO_REQUIRE_VERIFIED_EMAIL": "off",
        "WRI_PASSWORD_SIGN_IN": "off",
    }.items():
        monkeypatch.setenv(name, value)
    settings = Settings.from_env()
    assert settings.sso == SsoConfig(
        issuer="https://login.firm.example/tenant",
        client_id="abc",
        client_secret="s3cret",
        redirect_url="https://wri.firm.example/",
        name="Firm login",
        email_claim="preferred_username",
        require_verified_email=False,
    )
    assert settings.password_sign_in is False
    monkeypatch.setenv("WRI_PASSWORD_SIGN_IN", "maybe")
    with pytest.raises(ValueError, match="WRI_PASSWORD_SIGN_IN must be on or off"):
        Settings.from_env()


def test_a_database_from_before_single_sign_on_gains_what_it_needs(tmp_path):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'wri.db'}"
    store = Store(url)
    store.create_user(*OWNER, is_firm_admin=True)
    with store.engine.begin() as connection:
        connection.execute(text("ALTER TABLE users DROP COLUMN sso_subject"))
        connection.execute(text("ALTER TABLE users DROP COLUMN sso_issuer"))
        connection.execute(text("DROP TABLE sso_requests"))
    store.close()
    again = Store(url)
    user = again.get_user_by_email(OWNER[0])
    assert user.has_password is True and user.sso_linked is False
    again.begin_sso(state="s", binding="b", nonce="n", code_verifier="v")
    assert again.take_sso_request(state="s", binding="b") == ("n", "v")
    again.close()


def test_the_admin_tool_reports_sign_on_and_makes_accounts_for_it(
    monkeypatch, capsys, tmp_path, provider
):
    settings = sso_settings(tmp_path, provider)
    monkeypatch.setattr(admin_cli.Settings, "from_env", classmethod(lambda cls: settings))
    assert admin_cli.main(["create-user", PAT.email, "--firm-admin", "--no-password"]) == 0
    assert "created firm administrator pat@firm.example" in capsys.readouterr().out
    assert Store(settings.database_url).get_user_by_email(PAT.email).has_password is False
    admin_cli.main(["status"])
    out = capsys.readouterr().out
    assert "single sign-on      on: Firm sign-in (https://idp.test)" in out
    assert "passwords           still accepted" in out

    plain = Settings(auth_mode="local", data_dir=tmp_path / "plain")
    monkeypatch.setattr(admin_cli.Settings, "from_env", classmethod(lambda cls: plain))
    with pytest.raises(SystemExit, match="needs single sign-on to be set up first"):
        admin_cli.main(["create-user", "x@firm.example", "--no-password"])
    (tmp_path / "plain").mkdir(exist_ok=True)
    admin_cli.main(["status"])
    assert "single sign-on      off" in capsys.readouterr().out


def test_a_mistake_in_the_settings_is_reported_in_one_line(monkeypatch, capsys):
    monkeypatch.setenv("WRI_AUTH_MODE", "local")
    monkeypatch.setenv("WRI_PASSWORD_SIGN_IN", "off")
    monkeypatch.delenv("WRI_SSO_ISSUER", raising=False)
    assert admin_cli.main(["status"]) == 2
    said = capsys.readouterr().err
    assert said.startswith("wri-admin: the settings are not usable: WRI_PASSWORD_SIGN_IN is off")
    assert "Traceback" not in said and said.count("\n") == 1


# ---------------------------------------------------------------------------
# the client on its own
# ---------------------------------------------------------------------------
def test_the_client_refuses_a_provider_address_that_is_not_https(provider):
    config = SsoConfig(
        issuer=provider.issuer, client_id=provider.client_id, redirect_url=provider.redirect_url
    )
    client = OidcClient(config, http=provider.http)
    assert client.begin().authorization_url.startswith("https://idp.test/authorize?")

    class Downgrading(Provider):
        pass

    plain = Provider()
    plain.app.router.routes.clear()

    @plain.app.get("/.well-known/openid-configuration")
    def discovery():
        return {
            "issuer": plain.issuer,
            "authorization_endpoint": "https://idp.test/authorize",
            "token_endpoint": "http://idp.test/token",
            "jwks_uri": "https://idp.test/jwks",
        }

    with pytest.raises(SsoError) as refused:
        OidcClient(config, http=plain.http).begin()
    assert "token_endpoint is missing or not https" in refused.value.detail


def test_the_providers_description_is_read_once_and_then_remembered(provider):
    config = SsoConfig(
        issuer=provider.issuer, client_id=provider.client_id, redirect_url=provider.redirect_url
    )
    calls = []
    original = provider.http.get

    def counting(url, **kwargs):
        calls.append(url)
        return original(url, **kwargs)

    provider.http.get = counting
    clock = [1000.0]
    client = OidcClient(config, http=provider.http, now=lambda: clock[0])
    client.begin()
    client.begin()
    assert len(calls) == 1
    clock[0] += 3601
    client.begin()
    assert len(calls) == 2
