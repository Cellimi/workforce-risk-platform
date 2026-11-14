"""Sign-in, users and engagements. Mounted only in a licensed (`local` mode) installation.

Three kinds of people use an installation:

* a **firm administrator** creates user accounts and engagements, and can open every
  engagement as its admin;
* an **engagement admin** (the lead consultant) manages that engagement's data, assumption
  overrides and members;
* everyone else has exactly the role their membership gives them -- `hr_analyst` for a
  consultant who needs record-level detail, `executive` for a client reader who sees
  aggregates only.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field

from wri_engine.access.roles import CAPABILITIES, Role
from wri_engine.api.context import bearer_token, current_user, runtime_of
from wri_engine.costing.assumptions import load_assumptions
from wri_engine.schema import OCCUPATION_GROUP_LABELS
from wri_engine.sectors import DEFAULT_SECTOR, SectorPack, get_pack
from wri_engine.store import MIN_PASSWORD_LENGTH, AuthError, Store, UserRecord
from wri_engine.store.db import SECTORS

router = APIRouter()
User = Annotated[UserRecord, Depends(current_user)]


def _store(request: Request) -> Store:
    return runtime_of(request).store


def _audit(
    endpoint: str, *, user: str = "", engagement: str = "", outcome: str = "allowed", **detail
):
    from wri_engine.api import main

    main.AUDIT.record(
        role="", user=user, engagement=engagement, endpoint=endpoint, outcome=outcome, detail=detail
    )


def _firm_admin(user: User) -> UserRecord:
    if not user.is_firm_admin:
        _audit("firm-admin action", user=user.email, outcome="denied")
        raise HTTPException(403, "only a firm administrator may do this")
    return user


FirmAdmin = Annotated[UserRecord, Depends(_firm_admin)]


def _user_view(user: UserRecord) -> dict:
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "is_firm_admin": user.is_firm_admin,
        "is_active": user.is_active,
    }


# ---------------------------------------------------------------------------
# sign-in
# ---------------------------------------------------------------------------
class SignIn(BaseModel):
    email: str
    password: str


@router.post("/auth/login", tags=["auth"])
def login(body: SignIn, request: Request) -> dict:
    settings = runtime_of(request).settings
    try:
        token, user = _store(request).sign_in(
            body.email, body.password, session_hours=settings.session_hours
        )
    except AuthError as exc:
        _audit("/auth/login", user=body.email.strip().lower()[:320], outcome="denied")
        raise HTTPException(401, str(exc)) from None
    _audit("/auth/login", user=user.email)
    return {
        "token": token,
        "token_type": "bearer",
        "expires_in_hours": settings.session_hours,
        "user": _user_view(user),
    }


@router.post("/auth/logout", tags=["auth"])
def logout(
    request: Request, user: User, authorization: Annotated[str | None, Header()] = None
) -> dict:
    _store(request).sign_out(bearer_token(authorization))
    _audit("/auth/logout", user=user.email)
    return {"signed_out": True}


@router.get("/auth/me", tags=["auth"])
def me(request: Request, user: User) -> dict:
    return {
        "user": _user_view(user),
        "engagements": [
            _engagement_view(e, role) for e, role in _store(request).engagements_for(user)
        ],
    }


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class NewUser(BaseModel):
    email: str
    name: str = ""
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH)
    is_firm_admin: bool = False


@router.post("/users", tags=["users"], status_code=201)
def create_user(body: NewUser, request: Request, admin: FirmAdmin) -> dict:
    try:
        user = _store(request).create_user(
            body.email, body.password, name=body.name, is_firm_admin=body.is_firm_admin
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    _audit("/users", user=admin.email, created=user.email, is_firm_admin=user.is_firm_admin)
    return _user_view(user)


@router.get("/users", tags=["users"])
def list_users(request: Request, admin: FirmAdmin) -> dict:
    return {"users": [_user_view(u) for u in _store(request).list_users()]}


class ActiveFlag(BaseModel):
    is_active: bool


@router.put("/users/{user_id}/active", tags=["users"])
def set_active(user_id: int, body: ActiveFlag, request: Request, admin: FirmAdmin) -> dict:
    """Deactivate (or restore) an account. Deactivating signs the user out everywhere."""
    if user_id == admin.id and not body.is_active:
        raise HTTPException(400, "you cannot deactivate your own account")
    try:
        _store(request).set_user_active(user_id, body.is_active)
    except KeyError:
        raise HTTPException(404, "user not found") from None
    _audit("/users/{user_id}/active", user=admin.email, target=user_id, is_active=body.is_active)
    return {"id": user_id, "is_active": body.is_active}


# ---------------------------------------------------------------------------
# engagements
# ---------------------------------------------------------------------------
def _engagement_view(engagement, role: Role) -> dict:
    return {
        "id": engagement.id,
        "name": engagement.name,
        "client_name": engagement.client_name,
        "sector": engagement.sector,
        "occupation_group": engagement.occupation_group,
        "occupation_group_label": OCCUPATION_GROUP_LABELS.get(engagement.occupation_group),
        "status": engagement.status,
        "created_at": engagement.created_at.isoformat(),
        "your_role": str(role),
        "capabilities": sorted(str(c) for c in CAPABILITIES[role]),
    }


class NewEngagement(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    client_name: str = Field(..., min_length=1, max_length=200)
    sector: str = Field(DEFAULT_SECTOR, description=f"One of: {', '.join(SECTORS)}")
    occupation_group: str | None = Field(
        None,
        description="The kind of work the client's people mostly do. Offered only where the "
        "sector loads benefits by occupation; see GET /sectors for the choices.",
    )


def _occupation_groups(pack: SectorPack) -> list[dict]:
    """The groups a person may select in this sector, each with the multiplier it brings.

    The figure shown is the registry's own, read at the time of asking, so the screen can
    say what a selection will do before it is made.
    """
    if not pack.benefits_group_by_occupation:
        return []
    assumptions = load_assumptions()
    return [
        {
            "id": str(group),
            "label": OCCUPATION_GROUP_LABELS[group],
            "assumption_id": pack.benefits_assumption_by_group[benefits_group],
            "multiplier": str(assumptions.value(pack.benefits_assumption_by_group[benefits_group])),
        }
        for group, benefits_group in pack.benefits_group_by_occupation.items()
    ]


def _unselected(pack: SectorPack) -> dict | None:
    """What applies when no group is selected: the sector's all-occupations figure."""
    if not pack.benefits_group_by_occupation:
        return None
    assumption_id = pack.benefits_assumption_by_group[pack.default_benefits_group]
    return {
        "assumption_id": assumption_id,
        "multiplier": str(load_assumptions().value(assumption_id)),
    }


@router.get("/sectors", tags=["engagements"])
def list_sectors(request: Request, user: User) -> dict:
    """The cost models this installation can run.

    The UI offers these and nothing else. It reads them from here rather than carrying its
    own list, so a sector the engine cannot cost is never offered, and a sector it can is
    never hidden.
    """
    return {
        "sectors": [
            {
                "id": pack.id,
                "label": pack.label,
                "description": pack.description,
                "components": list(pack.component_codes()),
                "occupation_groups": _occupation_groups(pack),
                "no_occupation_group": _unselected(pack),
            }
            # The default sector leads, because the screen's chooser opens on the first.
            for pack in (
                get_pack(sector)
                for sector in sorted(SECTORS, key=lambda s: (s != DEFAULT_SECTOR, s))
            )
        ],
        "default": DEFAULT_SECTOR,
    }


@router.post("/engagements", tags=["engagements"], status_code=201)
def create_engagement(body: NewEngagement, request: Request, admin: FirmAdmin) -> dict:
    try:
        engagement = _store(request).create_engagement(
            name=body.name,
            client_name=body.client_name,
            sector=body.sector,
            created_by=admin.id,
            occupation_group=body.occupation_group,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    _audit("/engagements", user=admin.email, engagement=engagement.id, name=engagement.name)
    return _engagement_view(engagement, Role.ADMIN)


@router.get("/engagements", tags=["engagements"])
def list_engagements(request: Request, user: User) -> dict:
    return {
        "engagements": [
            _engagement_view(e, role) for e, role in _store(request).engagements_for(user)
        ]
    }


class Member(BaseModel):
    email: str
    role: Role


def _engagement_admin(
    request: Request,
    engagement_id: str,
    user: UserRecord,
    what: str = "manage its members",
    endpoint: str = "members",
) -> None:
    role = _store(request).role_in(user, engagement_id)
    if role is None:
        raise HTTPException(404, "engagement not found")
    if role != Role.ADMIN:
        _audit(endpoint, user=user.email, engagement=engagement_id, outcome="denied")
        raise HTTPException(403, f"only an engagement admin may {what}")


class OccupationGroupChoice(BaseModel):
    occupation_group: str | None = Field(
        None, description="One of the sector's occupation groups, or null to clear it."
    )


@router.put("/engagements/{engagement_id}/occupation-group", tags=["engagements"])
def set_occupation_group(
    engagement_id: str, body: OccupationGroupChoice, request: Request, user: User
) -> dict:
    """Select, change or clear the engagement's occupation group.

    The selection decides which benefits multiplier loads the wage of every employee whose
    own row does not name a group. Changing it re-reads the engagement's files under the new
    selection, so every figure follows it.
    """
    _engagement_admin(
        request, engagement_id, user, "change its occupation group", "occupation-group"
    )
    store = _store(request)
    before = store.get_engagement(engagement_id)
    try:
        engagement = store.set_occupation_group(engagement_id, body.occupation_group)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    runtime_of(request).workspaces.refresh_occupation_group(engagement_id)
    _audit(
        "/engagements/{engagement_id}/occupation-group",
        user=user.email,
        engagement=engagement_id,
        before=before.occupation_group if before else None,
        after=engagement.occupation_group,
    )
    return _engagement_view(engagement, Role.ADMIN)


@router.get("/engagements/{engagement_id}/members", tags=["engagements"])
def list_members(engagement_id: str, request: Request, user: User) -> dict:
    _engagement_admin(request, engagement_id, user)
    return {
        "members": [
            {**_user_view(u), "role": str(role)}
            for u, role in _store(request).members(engagement_id)
        ]
    }


@router.put("/engagements/{engagement_id}/members", tags=["engagements"])
def set_member(engagement_id: str, body: Member, request: Request, user: User) -> dict:
    """Add an existing user to the engagement, or change their role."""
    _engagement_admin(request, engagement_id, user)
    target = _store(request).get_user_by_email(body.email)
    if target is None:
        raise HTTPException(404, "no user has that email; a firm administrator must create it")
    _store(request).set_member(engagement_id, target.id, body.role)
    _audit(
        "/engagements/{engagement_id}/members",
        user=user.email,
        engagement=engagement_id,
        member=target.email,
        role=str(body.role),
    )
    return {"email": target.email, "role": str(body.role)}


@router.delete("/engagements/{engagement_id}/members/{user_id}", tags=["engagements"])
def remove_member(engagement_id: str, user_id: int, request: Request, user: User) -> dict:
    _engagement_admin(request, engagement_id, user)
    try:
        _store(request).set_member(engagement_id, user_id, None)
    except KeyError:
        raise HTTPException(404, "user not found") from None
    _audit(
        "/engagements/{engagement_id}/members/{user_id}",
        user=user.email,
        engagement=engagement_id,
        removed=user_id,
    )
    return {"removed": user_id}
