"""A stand-in identity provider, for testing single sign-on without a real one.

It speaks enough OpenID Connect to be signed in through: a discovery document, an
authorization endpoint that approves at once as whoever `signing_in_as` names, a token
endpoint that checks the client, the redirect address, the one-time code and the PKCE proof,
and a published signing key. It can also be told to misbehave, so the product's checks can
be held to refusing what a real attacker or a broken provider would send.

It is a FastAPI app. In tests it is reached in-process (`provider.http`); `python -m` style
it can be served on a port for a real browser (see `serve`).
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.testclient import TestClient


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


@dataclass
class Person:
    subject: str
    email: str
    name: str = ""
    email_verified: bool | None = True


@dataclass
class Provider:
    issuer: str = "https://idp.test"
    client_id: str = "wri-client"
    client_secret: str = "wri-client-secret"
    redirect_url: str = "https://wri.firm.example/"
    #: Who the provider signs in when the browser arrives. Tests set this.
    signing_in_as: Person | None = None
    #: Changes made to the identity token's claims just before it is signed.
    alter_claims: dict = field(default_factory=dict)
    #: "wrong_key" signs with a key the provider does not publish; "none" and "hs256" are
    #: the two classic forgeries; "" behaves.
    forge: str = ""
    #: What the discovery document says the issuer is, when it should lie.
    claimed_issuer: str | None = None
    token_auth_methods: list[str] = field(default_factory=lambda: ["client_secret_basic"])
    kid: str = "key-1"
    codes: dict = field(default_factory=dict)
    token_requests: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.app = self._app()
        self.http = TestClient(self.app, base_url=self.issuer, follow_redirects=False)

    # -- keys ----------------------------------------------------------------------------
    def jwk(self) -> dict:
        numbers = self.key.public_key().public_numbers()
        size = (numbers.n.bit_length() + 7) // 8
        return {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": self.kid,
            "n": _b64(numbers.n.to_bytes(size, "big")),
            "e": _b64(numbers.e.to_bytes(3, "big")),
        }

    def rotate_key(self) -> None:
        """Start signing with a new key, as providers do from time to time."""
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = f"key-{secrets.token_hex(3)}"

    def _public_pem(self) -> bytes:
        return self.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

    # -- the identity token --------------------------------------------------------------
    def id_token(self, person: Person, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": self.issuer,
            "aud": self.client_id,
            "sub": person.subject,
            "iat": now,
            "exp": now + 300,
            "nonce": nonce,
            "email": person.email,
            "name": person.name,
        }
        if person.email_verified is not None:
            claims["email_verified"] = person.email_verified
        claims.update(self.alter_claims)
        claims = {k: v for k, v in claims.items() if v is not None}
        headers = {"kid": self.kid}
        if self.forge == "none":
            head = _b64(json.dumps({"alg": "none", "typ": "JWT", "kid": self.kid}).encode())
            return f"{head}.{_b64(json.dumps(claims).encode())}."
        if self.forge == "hs256":
            # Signed with the PUBLIC key as if it were a shared secret: the forgery that
            # works against a verifier which trusts the token's own header.
            head = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": self.kid}).encode())
            body = _b64(json.dumps(claims).encode())
            import hmac

            mac = hmac.new(self._public_pem(), f"{head}.{body}".encode(), hashlib.sha256).digest()
            return f"{head}.{body}.{_b64(mac)}"
        key = self.other_key if self.forge == "wrong_key" else self.key
        return jwt.encode(claims, key, algorithm="RS256", headers=headers)

    # -- the endpoints -------------------------------------------------------------------
    def _app(self) -> FastAPI:
        app = FastAPI()

        @app.get("/.well-known/openid-configuration")
        def discovery():
            return {
                "issuer": self.claimed_issuer or self.issuer,
                "authorization_endpoint": f"{self.issuer}/authorize",
                "token_endpoint": f"{self.issuer}/token",
                "jwks_uri": f"{self.issuer}/jwks",
                "response_types_supported": ["code"],
                "id_token_signing_alg_values_supported": ["RS256"],
                "token_endpoint_auth_methods_supported": self.token_auth_methods,
            }

        @app.get("/jwks")
        def jwks():
            return {"keys": [self.jwk()]}

        @app.get("/authorize")
        def authorize(
            client_id: str,
            redirect_uri: str,
            response_type: str,
            scope: str,
            state: str,
            nonce: str,
            code_challenge: str,
            code_challenge_method: str,
        ):
            if client_id != self.client_id or redirect_uri != self.redirect_url:
                raise HTTPException(400, "unknown client or redirect address")
            if response_type != "code" or code_challenge_method != "S256":
                raise HTTPException(400, "unsupported request")
            if "openid" not in scope.split() or self.signing_in_as is None:
                raise HTTPException(400, "nobody is signing in")
            code = secrets.token_urlsafe(24)
            self.codes[code] = {
                "person": self.signing_in_as,
                "nonce": nonce,
                "challenge": code_challenge,
            }
            joiner = "&" if "?" in redirect_uri else "?"
            return RedirectResponse(
                f"{redirect_uri}{joiner}{urlencode({'code': code, 'state': state})}", 302
            )

        @app.post("/token")
        def token(
            request: Request,
            grant_type: str = Form(...),
            code: str = Form(...),
            redirect_uri: str = Form(...),
            client_id: str = Form(...),
            code_verifier: str = Form(...),
            client_secret: str | None = Form(None),
        ):
            basic = request.headers.get("authorization", "")
            via = "none"
            if basic.lower().startswith("basic "):
                user, _, secret = base64.b64decode(basic[6:]).decode().partition(":")
                via, client_id, client_secret = "client_secret_basic", user, secret
            elif client_secret is not None:
                via = "client_secret_post"
            self.token_requests.append({"via": via, "client_id": client_id})
            if client_id != self.client_id or client_secret != self.client_secret:
                return JSONResponse({"error": "invalid_client"}, 401)
            issued = self.codes.pop(code, None)  # a code works once
            if grant_type != "authorization_code" or issued is None:
                return JSONResponse({"error": "invalid_grant"}, 400)
            if redirect_uri != self.redirect_url:
                return JSONResponse({"error": "invalid_grant"}, 400)
            proof = _b64(hashlib.sha256(code_verifier.encode()).digest())
            if proof != issued["challenge"]:
                return JSONResponse({"error": "invalid_grant"}, 400)
            return {
                "access_token": secrets.token_urlsafe(16),
                "token_type": "Bearer",
                "expires_in": 300,
                "id_token": self.id_token(issued["person"], issued["nonce"]),
            }

        return app


def serve(provider: Provider, port: int):
    """Serve a provider on this machine, for a real browser. Returns (server, thread)."""
    import threading

    import uvicorn

    server = uvicorn.Server(
        uvicorn.Config(provider.app, host="127.0.0.1", port=port, log_level="error")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(200):
        if server.started:
            break
        time.sleep(0.02)
    return server, thread
