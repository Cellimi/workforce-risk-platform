"""Roles and what each one may see.

Use limitation is architectural here, not cosmetic. The API asks this module before it
builds a response, and record-level data never reaches a caller who is not entitled to it.
The UI's role dropdown only *chooses* a role -- it cannot grant one.

| Role         | Sees                                                                  |
|--------------|-----------------------------------------------------------------------|
| `executive`  | Aggregates only, with suppression applied. No record drill-down.       |
| `hr_analyst` | Aggregates, plus record-level drill-down with employee IDs pseudonymized. |
| `admin`      | Everything, including real identifiers and assumption edits.           |

In a licensed installation the role comes from the signed-in user's membership of an
engagement (see `wri_engine.api.context`). Only in demo mode does it arrive in an
`X-WRI-Role` header with no authentication behind it.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from enum import StrEnum


class Role(StrEnum):
    EXECUTIVE = "executive"
    HR_ANALYST = "hr_analyst"
    ADMIN = "admin"


class Capability(StrEnum):
    VIEW_AGGREGATES = "view_aggregates"
    VIEW_RECORDS = "view_records"
    VIEW_REAL_IDENTIFIERS = "view_real_identifiers"
    EDIT_ASSUMPTIONS = "edit_assumptions"
    RUN_SCENARIOS = "run_scenarios"
    MANAGE_DATA = "manage_data"


CAPABILITIES: dict[Role, frozenset[Capability]] = {
    Role.EXECUTIVE: frozenset({Capability.VIEW_AGGREGATES}),
    Role.HR_ANALYST: frozenset(
        {Capability.VIEW_AGGREGATES, Capability.VIEW_RECORDS, Capability.RUN_SCENARIOS}
    ),
    Role.ADMIN: frozenset(Capability),
}

#: Changing this changes every pseudonym. A licensed installation generates its own on first
#: start (see `wri_engine.api.app.create_app`); the default is for the synthetic demo only.
_PSEUDONYM_SALT = os.environ.get("WRI_PSEUDONYM_SALT", "harlow-county-demo")


def set_pseudonym_salt(salt: str) -> None:
    global _PSEUDONYM_SALT
    _PSEUDONYM_SALT = salt


class AccessDenied(PermissionError):
    """Raised when a role asks for something it may not have. The API turns this into 403."""

    def __init__(self, role: Role, capability: Capability):
        self.role = role
        self.capability = capability
        super().__init__(f"role {role} is not permitted to {str(capability).replace('_', ' ')}")


def parse_role(raw: str | None, default: Role = Role.EXECUTIVE) -> Role:
    """Least privilege on anything unrecognized: an unknown role gets the weakest one."""
    if not raw:
        return default
    try:
        return Role(raw.strip().lower())
    except ValueError:
        return default


def allows(role: Role, capability: Capability) -> bool:
    return capability in CAPABILITIES[role]


def require(role: Role, capability: Capability) -> None:
    if not allows(role, capability):
        raise AccessDenied(role, capability)


def pseudonymize(employee_id: str) -> str:
    """Stable, non-reversible alias. The same employee is the same pseudonym across every
    view, so an analyst can still follow a person through a dataset without learning who
    they are."""
    digest = hashlib.sha256(f"{_PSEUDONYM_SALT}:{employee_id}".encode()).hexdigest()
    return f"EMP-{digest[:8].upper()}"


def project_identifier(role: Role, employee_id: str) -> str:
    """Return the identifier this role is entitled to see."""
    if allows(role, Capability.VIEW_REAL_IDENTIFIERS):
        return employee_id
    return pseudonymize(employee_id)


def project_named_colleagues(
    role: Role,
    text: str,
    named: list[tuple[str, str]],
    phrase: Callable[[str, str], str],
) -> str:
    """Mask the colleagues a piece of explanation names, for a role that may not see real
    identifiers.

    A formula can name someone other than the person disciplined: "supervisor E00483
    (Sergeant): $39.40 base x ...". Masking the subject's id and leaving the supervisor's in
    the sentence beside it would hand an analyst a real identifier.

    `named` lists who the record says could be named, as `(label, employee_id)`; `phrase`
    is the costing layer's own wording for naming one (`"supervisor E00483 ("`). Exactly
    that phrase is rewritten and nothing else in the text is touched, so an id that happens
    to be a number is never confused with an hour count or a rate.
    """
    if allows(role, Capability.VIEW_REAL_IDENTIFIERS):
        return text
    for label, employee_id in named:
        real = phrase(label, employee_id)
        if real in text:
            text = text.replace(real, phrase(label, pseudonymize(employee_id)))
    return text
