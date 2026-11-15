"""Where an engagement stands with its license key, worked out for each request.

`keys.py` knows how to read a key. This module answers the product's question: given this
installation and this engagement, today, what may be done?

    not_required   the installation has no verification key, so nothing is enforced
    valid          the engagement has a key that is for it and in date
    expired        the key is genuine and for this engagement, but past its last day
    missing        enforcement is on and the engagement has no key
    invalid        there is a key, but it is not for this engagement or cannot be verified

What each state allows:

* **valid** and **not_required** -- everything.
* **expired** -- the work already done stays readable: results, drill-downs and the client's
  report can still be opened and downloaded. Nothing new may be done: no data loaded, no
  value changed, no scenario run. A firm that delivered a report can still open it next
  year; it cannot carry on working under a lapsed key.
* **missing** and **invalid** -- the engagement's data and results are closed. Its members
  and its key can still be managed, so a firm administrator can put it right.

A license refusal is HTTP 402, not 403. 403 means "your role may not see this", and the
screen says so; a missing key is nobody's role, and must not be reported as one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from fastapi import HTTPException

from wri_engine.licensing.keys import License, LicenseError, check_for_engagement, verify

LICENSE_REFUSED = 402
READ_ONLY_METHODS = ("GET", "HEAD", "OPTIONS")


@dataclass(frozen=True)
class LicenseState:
    status: str
    license: License | None = None
    problem: str = ""

    @property
    def max_employees(self) -> int | None:
        return self.license.max_employees if self.license else None

    @property
    def key_id(self) -> str | None:
        return self.license.key_id if self.license else None

    def as_dict(self) -> dict:
        """What the screen is told. The key itself is never sent back."""
        out = {"status": self.status, "problem": self.problem}
        if self.license:
            out.update(
                key_id=self.license.key_id,
                licensee=self.license.licensee,
                expires_on=self.license.expires_on.isoformat(),
                max_employees=self.license.max_employees,
            )
        return out


NOT_REQUIRED = LicenseState("not_required")


def license_state(runtime, engagement, today: date | None = None) -> LicenseState:
    """The engagement's standing today. Never raises: a bad key is a state, not an error."""
    if not runtime.settings.licensing_enforced or runtime.license_public_key is None:
        return NOT_REQUIRED
    if engagement is None or not engagement.license_key:
        return LicenseState(
            "missing",
            problem="This engagement has no license key. A firm administrator can add one.",
        )
    try:
        license_ = verify(engagement.license_key, runtime.license_public_key)
    except LicenseError as exc:
        return LicenseState("invalid", problem=str(exc))
    today = today or date.today()
    if license_.is_expired(today):
        # Checked before the match below so that a lapsed key reads as lapsed.
        try:
            check_for_engagement(
                license_,
                client_name=engagement.client_name,
                sector=engagement.sector,
                today=license_.expires_on,
            )
        except LicenseError as exc:
            return LicenseState("invalid", license_, str(exc))
        return LicenseState(
            "expired",
            license_,
            f"This engagement's license key expired on {license_.expires_on.isoformat()}. "
            f"What was already produced can still be opened; nothing new can be done until "
            f"a firm administrator adds a current key.",
        )
    try:
        check_for_engagement(
            license_, client_name=engagement.client_name, sector=engagement.sector, today=today
        )
    except LicenseError as exc:
        return LicenseState("invalid", license_, str(exc))
    return LicenseState("valid", license_)


def require_license(state: LicenseState, method: str) -> None:
    """Refuse the request if the engagement's key does not allow it."""
    if state.status in ("missing", "invalid"):
        raise HTTPException(LICENSE_REFUSED, state.problem)
    if state.status == "expired" and method.upper() not in READ_ONLY_METHODS:
        raise HTTPException(LICENSE_REFUSED, state.problem)


def require_within_size(state: LicenseState, employees: int) -> None:
    """Refuse a dataset larger than the key covers, before anything is replaced."""
    limit = state.max_employees
    if limit is not None and employees > limit:
        raise HTTPException(
            LICENSE_REFUSED,
            f"This engagement's license key covers up to {limit:,} employees and the file "
            f"has {employees:,}. Nothing was changed. A firm administrator can add a key "
            f"that covers it.",
        )
