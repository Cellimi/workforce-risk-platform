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

import secrets
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from wri_engine.access.roles import CAPABILITIES, Role
from wri_engine.api.context import bearer_token, current_user, runtime_of
from wri_engine.api.licensing import license_state
from wri_engine.costing.assumptions import load_assumptions
from wri_engine.licensing.keys import License, LicenseError, check_for_engagement, verify
from wri_engine.licensing.usage import to_jsonl, verify_chain
from wri_engine.reporting.branding import MAX_LOGO_BYTES, check_logo
from wri_engine.schema import OCCUPATION_GROUP_LABELS
from wri_engine.sectors import DEFAULT_SECTOR, SectorPack, get_pack
from wri_engine.sso import SsoError
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
        "has_password": user.has_password,
        "sso_linked": user.sso_linked,
    }


# ---------------------------------------------------------------------------
# sign-in
# ---------------------------------------------------------------------------
class SignIn(BaseModel):
    email: str
    password: str


def _signed_in(token: str, user: UserRecord, session_hours: int) -> dict:
    return {
        "token": token,
        "token_type": "bearer",
        "expires_in_hours": session_hours,
        "user": _user_view(user),
    }


@router.get("/auth/sso", tags=["auth"])
def sso_status(request: Request) -> dict:
    """How people sign in to this installation. Asked before anyone is signed in."""
    runtime = runtime_of(request)
    return {
        "enabled": runtime.sso is not None,
        "name": runtime.sso.config.name if runtime.sso else "",
        "password_sign_in": runtime.settings.password_sign_in,
    }


def _sso(request: Request):
    client = runtime_of(request).sso
    if client is None:
        raise HTTPException(404, "single sign-on is not set up on this installation")
    return client


@router.post("/auth/sso/start", tags=["auth"])
def sso_start(request: Request) -> dict:
    """Begin a sign-in at the firm's identity provider.

    Returns where to send the person's browser, and a `binding`: a secret for that browser
    to keep and hand back when it returns. A sign-in can only be finished by the browser
    that started it, so a link to someone else's half-finished sign-in is useless.

    Where the browser is sent back to afterwards is fixed in the installation's settings.
    It is never taken from the caller.
    """
    client = _sso(request)
    try:
        begun = client.begin()
        binding = secrets.token_urlsafe(32)
        _store(request).begin_sso(
            state=begun.state,
            binding=binding,
            nonce=begun.nonce,
            code_verifier=begun.code_verifier,
        )
    except SsoError as exc:
        _audit("/auth/sso/start", outcome="failed", reason=exc.detail[:300])
        raise HTTPException(
            503, "The identity provider could not be reached. Try again shortly."
        ) from None
    except AuthError as exc:
        raise HTTPException(429, str(exc)) from None
    return {"authorization_url": begun.authorization_url, "binding": binding}


class SsoReturn(BaseModel):
    code: str = Field(..., min_length=1, max_length=4000)
    state: str = Field(..., min_length=1, max_length=500)
    binding: str = Field(..., min_length=1, max_length=500)


@router.post("/auth/sso/complete", tags=["auth"])
def sso_complete(body: SsoReturn, request: Request) -> dict:
    """Finish a sign-in: check what the identity provider sent back and open a session."""
    client = _sso(request)
    store = _store(request)
    settings = runtime_of(request).settings
    started = store.take_sso_request(state=body.state, binding=body.binding)
    if started is None:
        _audit("/auth/sso/complete", outcome="denied", reason="no such sign-in for this browser")
        raise HTTPException(
            401,
            "This sign-in was not started from this browser, or took too long. Please start again.",
        )
    nonce, code_verifier = started
    try:
        identity = client.finish(code=body.code, code_verifier=code_verifier, nonce=nonce)
    except SsoError as exc:
        _audit("/auth/sso/complete", outcome="denied", reason=exc.detail[:300])
        raise HTTPException(401, str(exc)) from None
    try:
        token, user = store.sign_in_sso(
            issuer=identity.issuer,
            subject=identity.subject,
            email=identity.email,
            session_hours=settings.session_hours,
        )
    except AuthError as exc:
        _audit("/auth/sso/complete", user=identity.email[:320], outcome="denied", reason=str(exc))
        raise HTTPException(403, str(exc)) from None
    _audit("/auth/sso/complete", user=user.email, via=client.config.name)
    return _signed_in(token, user, settings.session_hours)


@router.post("/auth/login", tags=["auth"])
def login(body: SignIn, request: Request) -> dict:
    settings = runtime_of(request).settings
    if not settings.password_sign_in:
        _audit("/auth/login", user=body.email.strip().lower()[:320], outcome="denied")
        raise HTTPException(
            403, f"Passwords are switched off on this installation. Use {settings.sso.name}."
        )
    try:
        token, user = _store(request).sign_in(
            body.email, body.password, session_hours=settings.session_hours
        )
    except AuthError as exc:
        _audit("/auth/login", user=body.email.strip().lower()[:320], outcome="denied")
        raise HTTPException(401, str(exc)) from None
    _audit("/auth/login", user=user.email)
    return _signed_in(token, user, settings.session_hours)


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
            _engagement_view(e, role, request) for e, role in _store(request).engagements_for(user)
        ],
    }


# ---------------------------------------------------------------------------
# the firm: whose name and logo go on a client's report
# ---------------------------------------------------------------------------
def _firm_view(firm) -> dict:
    return {"name": firm.name, "has_logo": firm.logo is not None, "logo_mime": firm.logo_mime}


class FirmName(BaseModel):
    name: str = Field("", max_length=200, description="As it should read on a report cover")


@router.get("/firm", tags=["firm"])
def get_firm(request: Request, user: User) -> dict:
    return _firm_view(_store(request).firm())


@router.put("/firm", tags=["firm"])
def set_firm_name(body: FirmName, request: Request, admin: FirmAdmin) -> dict:
    firm = _store(request).set_firm_name(body.name)
    _audit("/firm", user=admin.email, name=firm.name)
    return _firm_view(firm)


@router.get("/firm/logo", tags=["firm"])
def get_firm_logo(request: Request, user: User) -> Response:
    firm = _store(request).firm()
    if firm.logo is None:
        raise HTTPException(404, "no logo has been set")
    return Response(firm.logo, media_type=firm.logo_mime)


@router.put("/firm/logo", tags=["firm"])
async def set_firm_logo(
    request: Request,
    admin: FirmAdmin,
    file: UploadFile = File(..., description="A PNG or JPEG, 512 KB at most"),
) -> dict:
    """Set the logo printed on report covers. The file is read and checked as a picture
    before it is kept; its name and the type the browser claimed are not trusted."""
    data = await file.read(MAX_LOGO_BYTES + 1)
    try:
        mime = check_logo(data)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    firm = _store(request).set_firm_logo(data, mime)
    _audit("/firm/logo", user=admin.email, bytes=len(data), mime=mime)
    return _firm_view(firm)


@router.delete("/firm/logo", tags=["firm"])
def clear_firm_logo(request: Request, admin: FirmAdmin) -> dict:
    firm = _store(request).set_firm_logo(None, None)
    _audit("/firm/logo", user=admin.email, removed=True)
    return _firm_view(firm)


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class NewUser(BaseModel):
    email: str
    name: str = ""
    password: str | None = Field(
        None,
        min_length=MIN_PASSWORD_LENGTH,
        description="Leave out, where single sign-on is set up, for an account that signs "
        "in only through the identity provider.",
    )
    is_firm_admin: bool = False


@router.post("/users", tags=["users"], status_code=201)
def create_user(body: NewUser, request: Request, admin: FirmAdmin) -> dict:
    runtime = runtime_of(request)
    if body.password is None and runtime.sso is None:
        raise HTTPException(400, "a password is required: single sign-on is not set up")
    if body.password is not None and not runtime.settings.password_sign_in:
        raise HTTPException(
            400, "passwords are switched off on this installation; leave the password out"
        )
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


@router.delete("/users/{user_id}/sso-link", tags=["users"])
def reset_sso_link(user_id: int, request: Request, admin: FirmAdmin) -> dict:
    """Forget which identity an account is tied to, and sign it out everywhere. For when an
    email address has passed to a different person at the firm's identity provider."""
    try:
        user = _store(request).reset_sso_link(user_id)
    except KeyError:
        raise HTTPException(404, "user not found") from None
    _audit("/users/{user_id}/sso-link", user=admin.email, target=user.email)
    return _user_view(user)


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
def _engagement_view(engagement, role: Role, request: Request) -> dict:
    return {
        "license": license_state(runtime_of(request), engagement).as_dict(),
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
    license_key: str | None = Field(
        None,
        description="The key issued for this engagement. Required where the installation "
        "has a verification key; see GET /license.",
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


def _checked_key(request: Request, key: str | None, client_name: str, sector: str):
    """The key as it will be stored and what it says, or a 400 the person can act on.

    Returns `(None, None)` where the installation does not enforce licensing and no key was
    given. A key cannot be accepted by an installation with no verification key, because
    nothing there can tell a real key from typed text.
    """
    runtime = runtime_of(request)
    text_ = "".join((key or "").split()) or None
    if not runtime.settings.licensing_enforced:
        if text_:
            raise HTTPException(
                400,
                "this installation has no verification key, so a license key cannot be "
                "checked or kept. Leave the key out.",
            )
        return None, None
    if not text_:
        raise HTTPException(400, "a license key is required. One is issued for each engagement.")
    try:
        license_: License = verify(text_, runtime.license_public_key)
        check_for_engagement(license_, client_name=client_name, sector=sector, today=date.today())
    except LicenseError as exc:
        raise HTTPException(400, str(exc)) from None
    return text_, license_


@router.get("/license", tags=["license"])
def licensing(request: Request, user: User) -> dict:
    """Whether this installation requires a license key for each engagement."""
    return {"enforced": runtime_of(request).settings.licensing_enforced}


@router.post("/engagements", tags=["engagements"], status_code=201)
def create_engagement(body: NewEngagement, request: Request, admin: FirmAdmin) -> dict:
    key, license_ = _checked_key(request, body.license_key, body.client_name, body.sector)
    store = _store(request)
    try:
        engagement = store.create_engagement(
            name=body.name,
            client_name=body.client_name,
            sector=body.sector,
            created_by=admin.id,
            occupation_group=body.occupation_group,
            license_key=key,
            license_key_id=license_.key_id if license_ else None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    store.record_usage(
        "engagement_opened",
        engagement_id=engagement.id,
        key_id=engagement.license_key_id,
        detail={"sector": engagement.sector},
    )
    _audit("/engagements", user=admin.email, engagement=engagement.id, name=engagement.name)
    return _engagement_view(engagement, Role.ADMIN, request)


class LicenseKey(BaseModel):
    license_key: str = Field(..., min_length=1, max_length=4000)


@router.put("/engagements/{engagement_id}/license", tags=["license"])
def set_license(engagement_id: str, body: LicenseKey, request: Request, admin: FirmAdmin) -> dict:
    """Give an engagement its key, or replace a lapsed one with a current one."""
    store = _store(request)
    engagement = store.get_engagement(engagement_id)
    if engagement is None:
        raise HTTPException(404, "engagement not found")
    key, license_ = _checked_key(
        request, body.license_key, engagement.client_name, engagement.sector
    )
    try:
        engagement = store.set_license(engagement_id, key, license_.key_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    store.record_usage(
        "key_attached",
        engagement_id=engagement_id,
        key_id=license_.key_id,
        detail={"expires_on": license_.expires_on.isoformat()},
    )
    _audit(
        "/engagements/{engagement_id}/license",
        user=admin.email,
        engagement=engagement_id,
        key_id=license_.key_id,
    )
    return _engagement_view(engagement, Role.ADMIN, request)


@router.get("/audit/check", tags=["audit"])
def audit_check(admin: FirmAdmin) -> dict:
    """Whether the access audit log still holds together, and its head: the last entry's
    number and hash, to note down somewhere the server's administrators cannot change."""
    from wri_engine.api import main

    found = main.AUDIT.check()
    _audit("/audit/check", user=admin.email, intact=found.intact)
    body = found.as_dict()
    body["problems"] = body["problems"][:50]
    return body


@router.get("/license/usage", tags=["license"])
def usage(request: Request, admin: FirmAdmin) -> dict:
    """The usage log, with whether it still holds together."""
    entries = _store(request).usage_log()
    problems = verify_chain(entries)
    return {"entries": entries, "intact": not problems, "problems": problems}


@router.get("/license/usage.jsonl", tags=["license"])
def usage_export(request: Request, admin: FirmAdmin) -> Response:
    """The usage log as a file to send to the licensor, who checks it with
    `wri-license verify-log`."""
    _audit("/license/usage.jsonl", user=admin.email)
    return Response(
        to_jsonl(_store(request).usage_log()),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": 'attachment; filename="wri_usage_log.jsonl"'},
    )


@router.get("/engagements", tags=["engagements"])
def list_engagements(request: Request, user: User) -> dict:
    return {
        "engagements": [
            _engagement_view(e, role, request) for e, role in _store(request).engagements_for(user)
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
    return _engagement_view(engagement, Role.ADMIN, request)


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
