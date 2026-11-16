"""How much opportunity each unit had: employee-years, by job and place.

Counting discipline actions says little on its own. Eighty actions among 900 people over
three years is a low rate; eighty among 90 is not. The denominator used here is
**employee-years**: each person counts for the part of the period they were employed. Someone
hired half-way through counts for half the time; someone who left counts until they left.

Limits, stated plainly. The roster records where a person works and what job they hold now,
not where they were on the day of each action. Someone who moved between locations during
the period is counted entirely at the one the roster shows. And an employee-year is a year
on the payroll, not a year of full-time work.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from wri_engine.schema import CanonicalDataset

DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class Exposure:
    """Employee-years and headcount for one unit over the period."""

    employee_years: float
    people: int


def employee_years(
    data: CanonicalDataset, start: date, end: date
) -> dict[tuple[str, str], Exposure]:
    """Employee-years by (job, work location) between two dates, both counted."""
    years: dict[tuple[str, str], float] = {}
    people: dict[tuple[str, str], int] = {}
    for employee in data.employees:
        first = max(employee.hire_date, start)
        last = min(employee.separation_date or end, end)
        days = (last - first).days + 1
        if days <= 0:
            continue  # not employed at any point in the period
        key = (employee.role_family, employee.work_location)
        years[key] = years.get(key, 0.0) + days / DAYS_PER_YEAR
        people[key] = people.get(key, 0) + 1
    return {key: Exposure(years[key], people[key]) for key in years}
