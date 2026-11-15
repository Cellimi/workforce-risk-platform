"""License keys: what one says, how it is signed, and how the product checks it offline.

A license key is a short piece of text the licensor gives a consulting firm for **one
engagement**. It says who the firm is, which client the engagement is for, which cost model
it may run and until when. The licensor signs it with a private key that never leaves the
licensor; the installed product checks the signature with the matching public key, which is
all it holds. No network call is made: a firm's server can sit inside a client's network
with no route out and still verify a key.

    WRI1.<what the key says, base64url>.<signature, base64url>

What this is and is not
-----------------------
The signature makes a key impossible to **forge or alter**: nobody without the private key
can extend an expiry date or change the client's name. It does not make the product
impossible to run without one. The product is source code on the licensee's own machine, and
anyone who edits out the check has broken the license agreement, not the cryptography. A key
is how an honest licensee and the licensor keep the same count of engagements; the usage log
(`usage.py`) is the other half of that.

The signing scheme is Ed25519, from the `cryptography` library. Nothing here is home-made.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
from dataclasses import dataclass
from datetime import date

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

PREFIX = "WRI1"
PRODUCT = "wri"


class LicenseError(ValueError):
    """A key could not be accepted. The message is written for the person holding it."""


@dataclass(frozen=True)
class License:
    """What a key says. Every field is covered by the signature."""

    key_id: str
    licensee: str
    client_name: str
    sector: str
    issued_on: date
    expires_on: date
    #: The largest employee file the engagement may load. None means no limit was set.
    max_employees: int | None = None

    def is_expired(self, today: date) -> bool:
        """A key is good through its last day."""
        return today > self.expires_on

    def as_dict(self) -> dict:
        return {
            "key_id": self.key_id,
            "licensee": self.licensee,
            "client_name": self.client_name,
            "sector": self.sector,
            "issued_on": self.issued_on.isoformat(),
            "expires_on": self.expires_on.isoformat(),
            "max_employees": self.max_employees,
        }


# ---------------------------------------------------------------------------
# keys for signing and checking
# ---------------------------------------------------------------------------
def generate_keypair() -> tuple[str, str]:
    """A new signing key and the public key that checks it, both as PEM text.

    The first is the licensor's secret. It signs every key ever issued, so it is generated
    once, on the licensor's own machine, and never put in a repository, an installation or a
    message. The second is safe to publish and is what an installation is given.
    """
    private = Ed25519PrivateKey.generate()
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    public_pem = (
        private.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return private_pem, public_pem


def load_private_key(pem: str) -> Ed25519PrivateKey:
    try:
        key = serialization.load_pem_private_key(pem.encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise LicenseError(f"the signing key could not be read: {exc}") from None
    if not isinstance(key, Ed25519PrivateKey):
        raise LicenseError("the signing key is not an Ed25519 key")
    return key


def load_public_key(pem: str) -> Ed25519PublicKey:
    try:
        key = serialization.load_pem_public_key(pem.encode())
    except (ValueError, TypeError) as exc:
        raise LicenseError(f"the verification key could not be read: {exc}") from None
    if not isinstance(key, Ed25519PublicKey):
        raise LicenseError("the verification key is not an Ed25519 key")
    return key


# ---------------------------------------------------------------------------
# issuing and checking
# ---------------------------------------------------------------------------
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue(
    private_key: Ed25519PrivateKey,
    *,
    licensee: str,
    client_name: str,
    sector: str,
    issued_on: date,
    expires_on: date,
    max_employees: int | None = None,
    key_id: str | None = None,
) -> str:
    """Sign a key for one engagement and return its text."""
    if not licensee.strip() or not client_name.strip():
        raise LicenseError("a key needs the licensee's name and the client's name")
    if expires_on < issued_on:
        raise LicenseError("a key cannot expire before it is issued")
    if max_employees is not None and max_employees < 1:
        raise LicenseError("max_employees must be 1 or more, or left out for no limit")
    license_ = License(
        key_id=key_id or secrets.token_hex(8),
        licensee=licensee.strip(),
        client_name=client_name.strip(),
        sector=sector,
        issued_on=issued_on,
        expires_on=expires_on,
        max_employees=max_employees,
    )
    payload = {"v": 1, "product": PRODUCT, **license_.as_dict()}
    body = _b64(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    # The signature covers the encoded text exactly as it travels, so checking it never
    # depends on two programs agreeing how to write the same JSON.
    signed = f"{PREFIX}.{body}"
    return f"{signed}.{_b64(private_key.sign(signed.encode()))}"


def verify(text: str, public_key: Ed25519PublicKey) -> License:
    """Check a key's signature and return what it says, or raise `LicenseError`.

    Only the signature and the shape are checked here. Whether the key is for *this*
    engagement and still in date is `check_for_engagement`'s job, because those answers
    depend on the engagement and on today's date.
    """
    parts = "".join(text.split()).split(".")
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenseError(
            "that is not a license key. A key starts with 'WRI1.' and has three parts; "
            "check that all of it was copied."
        )
    _, body, signature = parts
    try:
        public_key.verify(_unb64(signature), f"{PREFIX}.{body}".encode())
    except (InvalidSignature, binascii.Error, ValueError):
        raise LicenseError(
            "the license key's signature does not check out. It was not issued for this "
            "product, or it has been changed since it was issued."
        ) from None
    try:
        payload = json.loads(_unb64(body))
        if payload.get("product") != PRODUCT or payload.get("v") != 1:
            raise LicenseError("the license key is for a different product or version")
        maximum = payload.get("max_employees")
        return License(
            key_id=str(payload["key_id"]),
            licensee=str(payload["licensee"]),
            client_name=str(payload["client_name"]),
            sector=str(payload["sector"]),
            issued_on=date.fromisoformat(payload["issued_on"]),
            expires_on=date.fromisoformat(payload["expires_on"]),
            max_employees=int(maximum) if maximum is not None else None,
        )
    except (KeyError, TypeError, ValueError, binascii.Error) as exc:
        if isinstance(exc, LicenseError):
            raise
        raise LicenseError("the license key is signed but its contents cannot be read") from None


def normalize_name(name: str) -> str:
    """A client's name as compared between a key and an engagement: case, spacing and
    punctuation do not matter, so 'Ridgeline Builders, Inc.' matches 'ridgeline builders inc'."""
    return " ".join(re.sub(r"[^\w\s]", " ", name.casefold()).split())


def check_for_engagement(license_: License, *, client_name: str, sector: str, today: date) -> None:
    """Refuse a key that is not for this engagement, or is out of date, saying which."""
    if normalize_name(license_.client_name) != normalize_name(client_name):
        raise LicenseError(
            f"this key was issued for the client {license_.client_name!r}, and this "
            f"engagement is for {client_name!r}. A key covers one engagement."
        )
    if license_.sector != sector:
        raise LicenseError(
            f"this key covers the {license_.sector.replace('_', ' ')} cost model, and this "
            f"engagement runs the {sector.replace('_', ' ')} one"
        )
    if license_.is_expired(today):
        raise LicenseError(f"this key expired on {license_.expires_on.isoformat()}")
