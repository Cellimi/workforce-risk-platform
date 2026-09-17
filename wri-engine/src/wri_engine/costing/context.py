"""Everything a cost component needs, assembled once per run.

Components are pure functions of `(action, context)`. They do no I/O, read no globals and
never look at the clock -- `context.as_of` is the extract date, passed in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from wri_engine.costing.assumptions import AssumptionSet
from wri_engine.costing.rates import RateBook
from wri_engine.orgconfig import OrgConfig, Schedule
from wri_engine.schema import (
    AdminLeavePeriod,
    AppealOrGrievance,
    CanonicalDataset,
    DisciplineAction,
    Employee,
    Separation,
)

CENTS = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Quantize to cents, so a total is always the exact sum of the numbers shown."""
    return Decimal(value).quantize(CENTS, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class CostContext:
    org: OrgConfig
    assumptions: AssumptionSet
    rates: RateBook
    as_of: date
    employees: dict[str, Employee]
    leave_by_action: dict[str, list[AdminLeavePeriod]]
    appeals_by_action: dict[str, list[AppealOrGrievance]]
    separations_by_action: dict[str, Separation]

    @classmethod
    def build(
        cls,
        data: CanonicalDataset,
        org: OrgConfig,
        assumptions: AssumptionSet,
        as_of: date,
    ) -> CostContext:
        return cls(
            org=org,
            assumptions=assumptions,
            rates=RateBook(data, org, assumptions),
            as_of=as_of,
            employees=data.employees_by_id(),
            leave_by_action=data.leave_by_action(),
            appeals_by_action=data.appeals_by_action(),
            separations_by_action=data.separations_by_action(),
        )

    # -- lookups -----------------------------------------------------------
    def employee_for(self, action: DisciplineAction) -> Employee | None:
        return self.employees.get(action.employee_id)

    def schedule_for(self, employee: Employee) -> Schedule:
        return self.org.schedules[employee.schedule_id]

    def shifts_between(self, employee: Employee, start: date, end: date) -> Decimal:
        return self.schedule_for(employee).shifts_between(start, end, self.org.holidays)

    def paid_leave_for(self, action: DisciplineAction) -> list[AdminLeavePeriod]:
        return [leave for leave in self.leave_by_action.get(action.action_id, []) if leave.paid]

    def appeals_for(self, action: DisciplineAction) -> list[AppealOrGrievance]:
        return self.appeals_by_action.get(action.action_id, [])

    def separation_for(self, action: DisciplineAction) -> Separation | None:
        return self.separations_by_action.get(action.action_id)

    def value(self, assumption_id: str) -> Decimal:
        return self.assumptions.value(assumption_id)
