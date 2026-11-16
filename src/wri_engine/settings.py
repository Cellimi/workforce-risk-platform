"""Runtime settings, read once from the environment when the API starts.

Two authentication modes exist, and the secure one is the default:

* ``local``  -- every request needs a signed-in user, and what that user may see comes from
  their membership of an engagement. This is the mode a licensee runs.
* ``demo``   -- the caller picks a role in a header and there is one built-in synthetic
  dataset. It exists only so the Harlow County walkthrough keeps working, and it has to be
  switched on explicitly with ``WRI_AUTH_MODE=demo``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wri_engine.paths import CONFIG_DIR, DATA_DIR
from wri_engine.sso.oidc import SsoConfig

AUTH_MODES = ("local", "demo")


@dataclass(frozen=True)
class Settings:
    auth_mode: str = "local"
    data_dir: Path = DATA_DIR
    database_url: str = ""
    session_hours: int = 12
    max_upload_mb: int = 50
    #: The licensor's verification key, as PEM text. With one installed, every engagement
    #: needs a license key; with none, nothing is enforced. Only the public half is ever
    #: here: it checks signatures and cannot make them.
    license_public_key: str = ""
    #: Single sign-on with the firm's identity provider. Off unless an issuer is given.
    sso_issuer: str = ""
    sso_client_id: str = ""
    sso_client_secret: str = ""
    sso_redirect_url: str = ""
    sso_name: str = "single sign-on"
    sso_scopes: str = "openid email profile"
    sso_email_claim: str = "email"
    sso_require_verified_email: bool = True
    #: Whether an email and password may still be used. Switched off, only the identity
    #: provider signs people in, so someone removed there is removed here.
    password_sign_in: bool = True

    @property
    def sso(self) -> SsoConfig | None:
        """The identity provider's settings, or None when single sign-on is off."""
        if not self.sso_issuer.strip():
            return None
        return SsoConfig(
            issuer=self.sso_issuer.strip(),
            client_id=self.sso_client_id.strip(),
            client_secret=self.sso_client_secret,
            redirect_url=self.sso_redirect_url.strip(),
            name=self.sso_name.strip() or "single sign-on",
            scopes=self.sso_scopes.strip() or "openid email profile",
            email_claim=self.sso_email_claim.strip() or "email",
            require_verified_email=self.sso_require_verified_email,
        )

    @property
    def licensing_enforced(self) -> bool:
        """The synthetic walkthrough is never licensed: it has no engagements and no client."""
        return bool(self.license_public_key.strip()) and self.auth_mode == "local"

    def __post_init__(self) -> None:
        if self.auth_mode not in AUTH_MODES:
            raise ValueError(f"WRI_AUTH_MODE must be one of {AUTH_MODES}, got {self.auth_mode!r}")
        if self.sso is not None and self.sso.problems():
            # Loudly, at start-up: half-configured sign-on must not come up looking fine.
            raise ValueError("single sign-on is misconfigured: " + "; ".join(self.sso.problems()))
        if not self.password_sign_in and self.sso is None:
            raise ValueError(
                "WRI_PASSWORD_SIGN_IN is off but single sign-on is not configured, so nobody "
                "could sign in. Set WRI_SSO_ISSUER and the settings that go with it first."
            )
        if not self.database_url:
            object.__setattr__(self, "database_url", f"sqlite:///{Path(self.data_dir) / 'wri.db'}")

    @property
    def engagements_dir(self) -> Path:
        return Path(self.data_dir) / "engagements"

    @property
    def audit_log_path(self) -> Path:
        """Where the access audit log is written: `WRI_AUDIT_LOG` if set, otherwise beside
        the database in the data directory."""
        named = os.environ.get("WRI_AUDIT_LOG", "").strip()
        return Path(named).expanduser() if named else Path(self.data_dir) / "audit_log.jsonl"

    @staticmethod
    def _license_public_key() -> str:
        """`WRI_LICENSE_PUBLIC_KEY_FILE` if set, otherwise `config/license_public_key.pem`
        if an installation has been given one."""
        named = os.environ.get("WRI_LICENSE_PUBLIC_KEY_FILE", "").strip()
        path = Path(named).expanduser() if named else CONFIG_DIR / "license_public_key.pem"
        if named and not path.exists():
            raise ValueError(f"WRI_LICENSE_PUBLIC_KEY_FILE points at {path}, which is missing")
        return path.read_text() if path.exists() else ""

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            license_public_key=cls._license_public_key(),
            auth_mode=os.environ.get("WRI_AUTH_MODE", "local").strip().lower(),
            data_dir=DATA_DIR,
            database_url=os.environ.get("WRI_DATABASE_URL", ""),
            session_hours=int(os.environ.get("WRI_SESSION_HOURS", "12")),
            max_upload_mb=int(os.environ.get("WRI_MAX_UPLOAD_MB", "50")),
            sso_issuer=os.environ.get("WRI_SSO_ISSUER", ""),
            sso_client_id=os.environ.get("WRI_SSO_CLIENT_ID", ""),
            sso_client_secret=os.environ.get("WRI_SSO_CLIENT_SECRET", ""),
            sso_redirect_url=os.environ.get("WRI_SSO_REDIRECT_URL", ""),
            sso_name=os.environ.get("WRI_SSO_NAME", "") or "single sign-on",
            sso_scopes=os.environ.get("WRI_SSO_SCOPES", "") or "openid email profile",
            sso_email_claim=os.environ.get("WRI_SSO_EMAIL_CLAIM", "") or "email",
            sso_require_verified_email=_switch("WRI_SSO_REQUIRE_VERIFIED_EMAIL", True),
            password_sign_in=_switch("WRI_PASSWORD_SIGN_IN", True),
        )


def _switch(name: str, default: bool) -> bool:
    """An on/off setting. Anything that is not clearly one or the other is refused, so a
    typing mistake never quietly leaves a safeguard off."""
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("on", "true", "yes", "1"):
        return True
    if raw in ("off", "false", "no", "0"):
        return False
    raise ValueError(f"{name} must be on or off, got {raw!r}")
