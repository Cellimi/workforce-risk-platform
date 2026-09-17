"""Loader for `config/org_county.yaml`.

Describes the organization: departments, role families, pay plan, work schedules and the
misconduct/action taxonomies. Contains no cost coefficients -- those are in
`config/assumptions.yaml`.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from wri_engine.paths import ORG_CONFIG_FILE

_WEEKDAY_NAMES = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


@dataclass(frozen=True)
class Schedule:
    """How a role's calendar days become paid shifts.

    Two kinds:
      * `weekday_calendar` -- counts Mon-Fri days, skipping observed county holidays.
      * `fractional` -- multiplies calendar days by a fixed duty fraction (a 12-hour
        rotation works roughly every other day; a 24/48 fire shift works one day in three).
    """

    id: str
    kind: str
    shift_hours: Decimal
    workdays: tuple[str, ...]
    shift_fraction: Decimal
    observes_holidays: bool
    annual_paid_hours: int
    description: str

    def shifts_between(
        self, start: date, end: date, holidays: frozenset[tuple[int, int]]
    ) -> Decimal:
        """Scheduled shifts in the inclusive range [start, end]."""
        if end < start:
            return Decimal("0")
        if self.kind == "fractional":
            calendar_days = Decimal((end - start).days + 1)
            return calendar_days * self.shift_fraction
        count = 0
        day = start
        while day <= end:
            if _WEEKDAY_NAMES[day.weekday()] in self.workdays and not (
                self.observes_holidays and (day.month, day.day) in holidays
            ):
                count += 1
            day += timedelta(days=1)
        return Decimal(count)

    def hours_between(
        self, start: date, end: date, holidays: frozenset[tuple[int, int]]
    ) -> Decimal:
        return self.shifts_between(start, end, holidays) * self.shift_hours


@dataclass(frozen=True)
class RoleFamily:
    id: str
    name: str
    department_id: str
    department_name: str
    bargaining_unit: str
    job_titles: tuple[str, ...]
    job_class_code: str
    pay_grade: str
    headcount: int
    is_sworn: bool
    minimum_staffing_role: bool
    flsa_status: str
    schedule_id: str
    benefits_group: str
    turnover_profile: str
    is_supervisory: bool
    locations: tuple[str, ...]


@dataclass(frozen=True)
class MisconductCategory:
    id: str
    label: str
    sworn_only: bool
    subtypes: tuple[str, ...]


class OrgConfig:
    """Read-only view over org_county.yaml."""

    def __init__(self, raw: dict):
        self._raw = raw
        self.agency_name: str = raw["agency"]["name"]
        self.agency_id: str = raw["agency"]["id"]
        self.holidays: frozenset[tuple[int, int]] = frozenset(
            (int(h.split("-")[0]), int(h.split("-")[1])) for h in raw.get("holidays", [])
        )
        self.schedules: dict[str, Schedule] = {
            sid: Schedule(
                id=sid,
                kind=s["kind"],
                shift_hours=Decimal(str(s["shift_hours"])),
                workdays=tuple(s.get("workdays", ())),
                shift_fraction=Decimal(str(s.get("shift_fraction", 1))),
                observes_holidays=bool(s.get("observes_holidays", False)),
                annual_paid_hours=int(s["annual_paid_hours"]),
                description=s.get("description", ""),
            )
            for sid, s in raw["schedules"].items()
        }
        self.bargaining_units: dict[str, str] = {
            b["id"]: b["name"] for b in raw.get("bargaining_units", [])
        }
        self.role_families: dict[str, RoleFamily] = {}
        self.departments: dict[str, dict] = {}
        for dept in raw["departments"]:
            self.departments[dept["id"]] = dept
            for rf in dept["role_families"]:
                self.role_families[rf["id"]] = RoleFamily(
                    id=rf["id"],
                    name=rf["name"],
                    department_id=dept["id"],
                    department_name=dept["name"],
                    bargaining_unit=self.bargaining_units.get(
                        dept["bargaining_unit"], dept["bargaining_unit"]
                    ),
                    job_titles=tuple(rf["job_titles"]),
                    job_class_code=rf["job_class_code"],
                    pay_grade=rf["pay_grade"],
                    headcount=int(rf["headcount"]),
                    is_sworn=bool(rf["is_sworn"]),
                    minimum_staffing_role=bool(rf["minimum_staffing_role"]),
                    flsa_status=str(rf["flsa_status"]),
                    schedule_id=rf["schedule"],
                    benefits_group=rf["benefits_group"],
                    turnover_profile=rf["turnover_profile"],
                    is_supervisory=bool(rf.get("is_supervisory", False)),
                    locations=tuple(dept["locations"]),
                )
        tax = raw["taxonomies"]
        self.misconduct_categories: dict[str, MisconductCategory] = {
            c["id"]: MisconductCategory(
                id=c["id"],
                label=c["label"],
                sworn_only=bool(c["sworn_only"]),
                subtypes=tuple(c["subtypes"]),
            )
            for c in tax["misconduct_categories"]
        }
        self.action_types: dict[str, dict] = {a["id"]: a for a in tax["action_types"]}
        self.separation_reasons: tuple[str, ...] = tuple(tax["separation_reasons"])
        self.appeal_forums: tuple[str, ...] = tuple(tax["appeal_forums"])
        self.appeal_outcomes: tuple[str, ...] = tuple(tax["appeal_outcomes"])

    # -- pay plan ----------------------------------------------------------
    def salary_for(self, pay_grade: str, pay_step: int) -> Decimal:
        """Step/grade lookup: step 1 base compounded by the step increment."""
        plan = self._raw["pay_plan"]
        grade = plan["grades"].get(pay_grade)
        if grade is None:
            raise KeyError(f"unknown pay grade {pay_grade!r}")
        steps = int(plan["steps"])
        if not 1 <= pay_step <= steps:
            raise ValueError(f"pay step {pay_step} outside 1..{steps} for grade {pay_grade}")
        increment = Decimal("1") + Decimal(str(plan["step_increment_pct"])) / Decimal("100")
        base = Decimal(str(grade["step1_base"]))
        return (base * increment ** (pay_step - 1)).quantize(Decimal("1"))

    @property
    def pay_steps(self) -> int:
        return int(self._raw["pay_plan"]["steps"])

    # -- convenience -------------------------------------------------------
    def schedule_for_role(self, role_family_id: str) -> Schedule:
        return self.schedules[self.role_families[role_family_id].schedule_id]

    def locations(self) -> list[str]:
        seen: list[str] = []
        for dept in self._raw["departments"]:
            for loc in dept["locations"]:
                if loc not in seen:
                    seen.append(loc)
        return seen

    def categories_for(self, *, is_sworn: bool) -> list[MisconductCategory]:
        return [c for c in self.misconduct_categories.values() if is_sworn or not c.sworn_only]

    @property
    def raw(self) -> dict:
        return self._raw


def load_org_config(path: Path | None = None) -> OrgConfig:
    path = path or ORG_CONFIG_FILE
    with open(path) as fh:
        return OrgConfig(yaml.safe_load(fh))


@functools.lru_cache(maxsize=4)
def default_org_config() -> OrgConfig:
    return load_org_config()
