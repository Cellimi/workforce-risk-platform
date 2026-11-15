"""Signing in with the firm's identity provider: OpenID Connect, authorization code flow.

Any provider that speaks OpenID Connect works (Microsoft Entra ID, Google Workspace, Okta,
Keycloak, and so on). Nothing here is specific to one of them. The provider is described by
its **issuer** address; everything else is read from the discovery document it publishes.

How a sign-in goes
------------------
1. `begin` makes a one-time request: a `state` to recognise the answer by, a `nonce` the
   provider must sign back, and a PKCE secret that proves the party redeeming the code is
   the party that asked. It returns the address to send the person's browser to.
2. The person signs in **at the provider**. This product never sees their password, and
   whatever second factor the provider demands is the provider's business.
3. The browser comes back with a one-time `code`. `finish` exchanges it, server to server,
   for an identity token, and then believes nothing in that token until it has checked:

   * the signature, against the provider's published keys;
   * that it was issued by the configured provider, for this product, and has not expired;
   * that it carries the `nonce` this request made, so an old token cannot be replayed.

What comes out is an `Identity`: who the provider says this is. Deciding whether that person
has an account here is not this module's job; see `Store.sign_in_sso`.

What is refused on purpose
--------------------------
* Tokens signed with a shared secret (`HS256`) or not signed at all (`none`). Only public-key
  signatures are accepted, whatever the token's own header claims.
* A discovery document whose issuer is not exactly the one configured.
* Any provider address that is not `https`, except on the machine itself, for testing.
* An email the provider has not verified, unless the firm has said its provider does not
  report that (`require_verified_email=False`).
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

import httpx
import jwt

#: Public-key signatures only. A token is never trusted on the strength of its own header.
ALLOWED_ALGORITHMS = ("RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384")
#: Seconds of disagreement tolerated between this server's clock and the provider's.
CLOCK_LEEWAY = 60
#: How long the provider's description and keys are remembered before being read again.
CACHE_SECONDS = 3600
TIMEOUT = 10.0
LOOPBACK = ("127.0.0.1", "localhost", "::1")


class SsoError(Exception):
    """A sign-in could not be completed. `str()` is safe to show the person signing in;
    `detail` says why, for the audit log, and is never shown."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail or message


NOT_COMPLETED = "Single sign-on did not complete. Please start again."


@dataclass(frozen=True)
class SsoConfig:
    issuer: str
    client_id: str
    redirect_url: str
    client_secret: str = ""
    name: str = "single sign-on"
    scopes: str = "openid email profile"
    email_claim: str = "email"
    require_verified_email: bool = True

    def problems(self) -> list[str]:
        """What is wrong with this configuration, in words for whoever runs the server."""
        found = []
        for label, value in (
            ("WRI_SSO_ISSUER", self.issuer),
            ("WRI_SSO_REDIRECT_URL", self.redirect_url),
        ):
            if not _acceptable(value):
                found.append(f"{label} must be an https address (got {value!r})")
        if not self.client_id:
            found.append("WRI_SSO_CLIENT_ID is not set")
        if "openid" not in self.scopes.split():
            found.append("WRI_SSO_SCOPES must include openid")
        return found


@dataclass(frozen=True)
class Begun:
    """What `begin` hands back: where to send the browser, and what to remember."""

    authorization_url: str
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True)
class Identity:
    """Who the provider says signed in. `subject` is the provider's own permanent id for
    the person; an email address can be given to someone else, a subject cannot."""

    issuer: str
    subject: str
    email: str
    name: str = ""


def _acceptable(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in LOOPBACK


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


class OidcClient:
    """Talks to one identity provider. `http` is how it reaches the provider; a test passes
    its own, and nothing else in the product makes outbound requests."""

    def __init__(self, config: SsoConfig, http: httpx.Client | None = None, now=time.time):
        self.config = config
        self._http = http or httpx.Client(timeout=TIMEOUT, follow_redirects=False)
        self._now = now
        self._discovery: tuple[float, dict] | None = None
        self._keys: tuple[float, dict] | None = None

    # -- the provider's own description of itself ----------------------------------------
    def _get(self, url: str, what: str) -> dict:
        if not _acceptable(url):
            raise SsoError(NOT_COMPLETED, f"the provider's {what} is not an https address: {url}")
        try:
            response = self._http.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError(NOT_COMPLETED, f"could not read the provider's {what}: {exc}") from None
        if not isinstance(body, dict):
            raise SsoError(NOT_COMPLETED, f"the provider's {what} is not what was expected")
        return body

    def discovery(self) -> dict:
        if self._discovery and self._now() - self._discovery[0] < CACHE_SECONDS:
            return self._discovery[1]
        issuer = self.config.issuer.rstrip("/")
        document = self._get(f"{issuer}/.well-known/openid-configuration", "description")
        if str(document.get("issuer", "")).rstrip("/") != issuer:
            raise SsoError(
                NOT_COMPLETED,
                f"the provider describes itself as {document.get('issuer')!r}, "
                f"not the configured {self.config.issuer!r}",
            )
        for field in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not _acceptable(str(document.get(field, ""))):
                raise SsoError(NOT_COMPLETED, f"the provider's {field} is missing or not https")
        self._discovery = (self._now(), document)
        return document

    def _signing_keys(self, *, refresh: bool = False) -> dict:
        if not refresh and self._keys and self._now() - self._keys[0] < CACHE_SECONDS:
            return self._keys[1]
        body = self._get(self.discovery()["jwks_uri"], "signing keys")
        keys = {k.get("kid"): k for k in body.get("keys", []) if isinstance(k, dict)}
        self._keys = (self._now(), keys)
        return keys

    def _key_for(self, token: str):
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise SsoError(NOT_COMPLETED, f"the identity token could not be read: {exc}") from None
        algorithm, kid = header.get("alg"), header.get("kid")
        if algorithm not in ALLOWED_ALGORITHMS:
            raise SsoError(NOT_COMPLETED, f"the identity token is signed with {algorithm!r}")
        keys = self._signing_keys()
        if kid not in keys:
            # Providers change their keys. Look once more before giving up.
            keys = self._signing_keys(refresh=True)
        candidates = [keys[kid]] if kid in keys else ([] if kid else list(keys.values()))
        if len(candidates) != 1:
            raise SsoError(NOT_COMPLETED, f"the provider publishes no key {kid!r} for this token")
        try:
            return jwt.PyJWK(candidates[0], algorithm=algorithm).key, algorithm
        except (jwt.PyJWTError, ValueError) as exc:
            raise SsoError(
                NOT_COMPLETED, f"the provider's signing key is unusable: {exc}"
            ) from None

    # -- the sign-in itself --------------------------------------------------------------
    def begin(self) -> Begun:
        """Start a sign-in: the address to send the browser to, and what to remember."""
        state, nonce = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_url,
                "scope": self.config.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
            }
        )
        endpoint = self.discovery()["authorization_endpoint"]
        joiner = "&" if "?" in endpoint else "?"
        return Begun(f"{endpoint}{joiner}{query}", state, nonce, verifier)

    def _exchange(self, code: str, code_verifier: str) -> str:
        """Trade the one-time code for an identity token, server to server."""
        document = self.discovery()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_url,
            "client_id": self.config.client_id,
            "code_verifier": code_verifier,
        }
        auth = None
        if self.config.client_secret:
            methods = document.get("token_endpoint_auth_methods_supported") or [
                "client_secret_basic"
            ]
            if "client_secret_basic" in methods:
                auth = (self.config.client_id, self.config.client_secret)
            else:
                form["client_secret"] = self.config.client_secret
        try:
            response = self._http.post(
                document["token_endpoint"],
                data=form,
                auth=auth,
                headers={"Accept": "application/json"},
            )
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise SsoError(
                NOT_COMPLETED, f"the provider did not answer the code exchange: {exc}"
            ) from None
        if response.status_code != 200 or not isinstance(body, dict) or "id_token" not in body:
            reason = (
                body.get("error", response.status_code)
                if isinstance(body, dict)
                else response.status_code
            )
            raise SsoError(NOT_COMPLETED, f"the provider refused the code exchange: {reason}")
        return str(body["id_token"])

    def finish(self, *, code: str, code_verifier: str, nonce: str) -> Identity:
        """Complete a sign-in begun earlier and say who the provider vouches for."""
        token = self._exchange(code, code_verifier)
        key, algorithm = self._key_for(token)
        try:
            claims = jwt.decode(
                token,
                key,
                algorithms=[algorithm],
                audience=self.config.client_id,
                # As the provider itself spells it, which `discovery` has already held to
                # the configured address; some end theirs with a slash.
                issuer=self.discovery()["issuer"],
                leeway=CLOCK_LEEWAY,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise SsoError(NOT_COMPLETED, f"the identity token was not accepted: {exc}") from None
        audience = claims["aud"]
        if (
            isinstance(audience, list)
            and len(audience) > 1
            and claims.get("azp") != self.config.client_id
        ):
            raise SsoError(
                NOT_COMPLETED, "the identity token was issued for more than this product"
            )
        if not nonce or not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise SsoError(NOT_COMPLETED, "the identity token does not answer this sign-in (nonce)")

        email = str(claims.get(self.config.email_claim) or "").strip().lower()
        if not email or "@" not in email:
            raise SsoError(
                "Your identity provider did not say what your email address is. Ask whoever "
                "runs it to release the email address to this application.",
                f"no usable {self.config.email_claim!r} claim in the identity token",
            )
        if self.config.require_verified_email and claims.get("email_verified") is not True:
            raise SsoError(
                "Your identity provider has not confirmed your email address, so it cannot "
                "be used to sign in here.",
                f"email_verified is {claims.get('email_verified')!r} for {email}",
            )
        return Identity(
            issuer=str(self.discovery()["issuer"]).rstrip("/"),
            subject=str(claims["sub"]),
            email=email,
            name=str(claims.get("name") or ""),
        )
