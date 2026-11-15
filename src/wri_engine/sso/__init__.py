"""Single sign-on with the firm's own identity provider, over OpenID Connect."""

from wri_engine.sso.oidc import Identity, OidcClient, SsoConfig, SsoError

__all__ = ["Identity", "OidcClient", "SsoConfig", "SsoError"]
