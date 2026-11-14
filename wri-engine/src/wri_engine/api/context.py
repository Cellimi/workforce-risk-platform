"""Who is calling, in which engagement, and what they may see.

Every data endpoint receives a `Ctx` built here before it does anything else.

In a licensed installation (`local` mode) the caller presents the bearer token they were
given at sign-in. The token identifies a user; the user's role comes from their membership
of the engagement named in the URL. A user who is not a member is told the engagement does
not exist, so engagement ids cannot be probed. The `X-WRI-Role` header is ignored.

In `demo` mode there are no users: the header chooses the role and there is one synthetic
workspace. That mode is opt-in and is never what a licensee runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, Request

from wri_engine.access.roles import Role, parse_role
from wri_engine.aggregation.suppression import DisclosureLedger
from wri_engine.api.state import STATE, EngineState, Workspaces
from wri_engine.settings import Settings
from wri_engine.store import EngagementRecord, Store, UserRecord


@dataclass
class Runtime:
    """Everything the running API shares between requests."""

    settings: Settings
    store: Store | None = None
    workspaces: Workspaces | None = None


@dataclass
class Ctx:
    role: Role
    state: EngineState
    session_key: str
    user: UserRecord | None = None
    engagement: EngagementRecord | None = None
    store: Store | None = None
    workspaces: Workspaces | None = None
    max_upload_bytes: int = 50 * 1024 * 1024

    @property
    def user_label(self) -> str:
        return self.user.email if self.user else ""

    @property
    def engagement_id(self) -> str:
        return self.engagement.id if self.engagement else ""

    def ledger(self) -> DisclosureLedger:
        return self.state.ledger(self.session_key)


def runtime_of(request: Request) -> Runtime:
    return request.app.state.runtime


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    return token.strip() if scheme.lower() == "bearer" and token.strip() else None


def current_user(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> UserRecord:
    """The signed-in user, or 401. Only meaningful in `local` mode."""
    runtime = runtime_of(request)
    token = bearer_token(authorization)
    user = runtime.store.user_for_token(token) if token and runtime.store else None
    if user is None:
        raise HTTPException(401, "sign in required", headers={"WWW-Authenticate": "Bearer"})
    return user


def get_ctx(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_wri_role: Annotated[str | None, Header(alias="X-WRI-Role")] = None,
    x_wri_session: Annotated[str | None, Header(alias="X-WRI-Session")] = None,
) -> Ctx:
    runtime = runtime_of(request)
    if runtime.settings.auth_mode == "demo":
        role = parse_role(x_wri_role)
        key = f"{role}:{x_wri_session}" if x_wri_session else str(role)
        return Ctx(role=role, state=STATE, session_key=key)

    user = current_user(request, authorization)
    engagement_id = request.path_params.get("engagement_id", "")
    role = runtime.store.role_in(user, engagement_id)
    if role is None:
        raise HTTPException(404, "engagement not found")
    return Ctx(
        role=role,
        state=runtime.workspaces.get(engagement_id),
        # The disclosure ledger follows the person, not anything the client sends.
        session_key=f"user:{user.id}",
        user=user,
        engagement=runtime.store.get_engagement(engagement_id),
        store=runtime.store,
        workspaces=runtime.workspaces,
        max_upload_bytes=runtime.settings.max_upload_mb * 1024 * 1024,
    )
