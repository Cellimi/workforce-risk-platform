"""Rates: turning a person into a dollars-per-hour figure, traceably.

One rule governs everything here: whenever a rate comes from an assumption, the assumption
id travels with it, so the line item that uses the rate can name it. `Rate` carries the
amount, the assumption ids behind it, and a phrase describing where it came from.

    loaded_hourly_rate = hourly_base_rate x benefits_multiplier[benefits_group]
    hourly_base_rate   = annual_base_salary / annual_paid_hours

`annual_paid_hours` is per schedule, not a flat 2,080: a firefighter on a 24/48 rotation is
paid for 2,912 hours a year, so the same salary buys a very different hourly rate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from wri_engine.costing.assumptions import AssumptionSet
from wri_engine.orgconfig import OrgConfig
from wri_engine.schema import CanonicalDataset, Employee

_BENEFITS_ASSUMPTION = {
    "sworn_public_safety": "benefits_multiplier_sworn",
    "civilian": "benefits_multiplier_civilian",
}


@dataclass(frozen=True)
class Rate:
    """A dollars-per-hour figure that knows where it came from."""

    amount: Decimal
    basis: str
    assumption_ids: tuple[str, ...] = field(default_factory=tuple)

    def __mul__(self, other: Decimal) -> Decimal:
        return self.amount * other


class RateBook:
    """Every rate the engine needs, derived once per dataset."""

    def __init__(self, data: CanonicalDataset, org: OrgConfig, assumptions: AssumptionSet):
        self.data = data
        self.org = org
        self.a = assumptions
        self._employees = data.employees_by_id()
        self._role_location_base: dict[tuple[str, str], Decimal] = {}
        self._role_base: dict[str, Decimal] = {}
        self._role_loaded: dict[str, Decimal] = {}
        self._dept_supervisor_loaded: dict[str, Decimal] = {}
        self._build_averages()

    # -- averages ----------------------------------------------------------
    def _build_averages(self) -> None:
        by_role_location: dict[tuple[str, str], list[Decimal]] = {}
        by_role: dict[str, list[Decimal]] = {}
        by_role_loaded: dict[str, list[Decimal]] = {}
        by_dept_supervisor: dict[str, list[Decimal]] = {}
        for employee in self.data.employees:
            if employee.separation_date is not None:
                continue  # averages describe who is on the payroll now
            by_role_location.setdefault((employee.role_family, employee.work_location), []).append(
                employee.hourly_base_rate
            )
            by_role.setdefault(employee.role_family, []).append(employee.hourly_base_rate)
            loaded = self.loaded_hourly(employee).amount
            by_role_loaded.setdefault(employee.role_family, []).append(loaded)
            role = self.org.role_families.get(employee.role_family)
            if role is not None and role.is_supervisory:
                by_dept_supervisor.setdefault(role.department_id, []).append(loaded)

        def mean(values: list[Decimal]) -> Decimal:
            return sum(values) / Decimal(len(values))

        self._role_location_base = {k: mean(v) for k, v in by_role_location.items()}
        self._role_base = {k: mean(v) for k, v in by_role.items()}
        self._role_loaded = {k: mean(v) for k, v in by_role_loaded.items()}
        self._dept_supervisor_loaded = {k: mean(v) for k, v in by_dept_supervisor.items()}

    # -- per employee ------------------------------------------------------
    def benefits_multiplier(self, employee: Employee) -> tuple[Decimal, str]:
        assumption_id = _BENEFITS_ASSUMPTION.get(
            employee.benefits_group, "benefits_multiplier_civilian"
        )
        return self.a.value(assumption_id), assumption_id

    def loaded_hourly(self, employee: Employee) -> Rate:
        multiplier, assumption_id = self.benefits_multiplier(employee)
        return Rate(
            amount=employee.hourly_base_rate * multiplier,
            basis=(
                f"${employee.hourly_base_rate:,.2f} base x {multiplier} benefits multiplier "
                f"({employee.benefits_group})"
            ),
            assumption_ids=(assumption_id,),
        )

    def step_one_loaded_hourly(self, employee: Employee) -> Rate:
        """What the *replacement* costs: a new hire enters the role family at step 1."""
        role = self.org.role_families[employee.role_family]
        salary = self.org.salary_for(role.pay_grade, 1)
        schedule = self.org.schedules[role.schedule_id]
        base = salary / Decimal(schedule.annual_paid_hours)
        multiplier, assumption_id = self.benefits_multiplier(employee)
        return Rate(
            amount=base * multiplier,
            basis=(
                f"replacement at {role.pay_grade} step 1 = ${salary:,.0f} / "
                f"{schedule.annual_paid_hours} paid hrs = ${base:,.2f} base "
                f"x {multiplier} benefits multiplier"
            ),
            assumption_ids=(assumption_id,),
        )

    # -- reference rates for people who are not in the extract -------------
    def _reference_loaded(self, salary_assumption_id: str, description: str) -> Rate:
        salary = self.a.value(salary_assumption_id)
        hours = self.a.value("c1_reference_annual_hours")
        multiplier = self.a.value("benefits_multiplier_civilian")
        return Rate(
            amount=(salary / hours) * multiplier,
            basis=(
                f"{description} reference ${salary:,.0f}/yr / {hours} hrs "
                f"x {multiplier} benefits multiplier"
            ),
            assumption_ids=(
                salary_assumption_id,
                "c1_reference_annual_hours",
                "benefits_multiplier_civilian",
            ),
        )

    def hr_loaded_hourly(self) -> Rate:
        return self._reference_loaded("c1_hr_reference_annual_salary", "HR / labor relations")

    def deciding_official_rate(self, level: str, official_id: str | None) -> Rate:
        """The named official's own rate when the record identifies them, otherwise the
        reference salary for that level."""
        if official_id and official_id in self._employees:
            official = self._employees[official_id]
            rate = self.loaded_hourly(official)
            return Rate(
                amount=rate.amount,
                basis=f"deciding official {official_id} ({official.job_title}): {rate.basis}",
                assumption_ids=rate.assumption_ids,
            )
        return self._reference_loaded(
            f"c1_deciding_annual_salary_{level}", f"deciding official ({level})"
        )

    def supervisor_rate(self, employee: Employee) -> Rate:
        """The employee's own supervisor when known; otherwise the average loaded rate of
        supervisors in their department; otherwise the supervisor reference salary."""
        supervisor_id = employee.supervisor_id
        if supervisor_id and supervisor_id in self._employees:
            supervisor = self._employees[supervisor_id]
            rate = self.loaded_hourly(supervisor)
            return Rate(
                amount=rate.amount,
                basis=f"supervisor {supervisor_id} ({supervisor.job_title}): {rate.basis}",
                assumption_ids=rate.assumption_ids,
            )
        role = self.org.role_families.get(employee.role_family)
        department_id = role.department_id if role else None
        average = self._dept_supervisor_loaded.get(department_id) if department_id else None
        if average is not None:
            return Rate(
                amount=average,
                basis=(
                    f"department average supervisory loaded rate "
                    f"({role.department_name}); no supervisor named on the record"
                ),
                assumption_ids=(),
            )
        return self._reference_loaded(
            "c1_deciding_annual_salary_supervisor", "first-line supervisor"
        )

    def internal_affairs_rate(self, employee: Employee) -> Rate:
        """Internal affairs investigators hold supervisory rank, so their hours are costed
        at the division-head reference rather than a line-employee rate."""
        return self._reference_loaded(
            "c1_deciding_annual_salary_division_head", "internal affairs investigator"
        )

    # -- backfill and trainer rates ----------------------------------------
    def backfill_base_rate(self, employee: Employee) -> Rate:
        """Overtime is worked by a peer, so the rate is the role family's average base rate
        at that location -- not the disciplined employee's own rate."""
        key = (employee.role_family, employee.work_location)
        if key in self._role_location_base:
            return Rate(
                amount=self._role_location_base[key],
                basis=(f"average base rate for {employee.role_family} at {employee.work_location}"),
            )
        if employee.role_family in self._role_base:
            return Rate(
                amount=self._role_base[employee.role_family],
                basis=f"average base rate for {employee.role_family} (all locations)",
            )
        return Rate(
            amount=employee.hourly_base_rate,
            basis="employee's own base rate; no peers on the payroll to average",
        )

    def trainer_loaded_rate(self, employee: Employee) -> Rate:
        if employee.role_family in self._role_loaded:
            return Rate(
                amount=self._role_loaded[employee.role_family],
                basis=f"average loaded rate for an experienced {employee.role_family}",
                assumption_ids=(self.benefits_multiplier(employee)[1],),
            )
        rate = self.loaded_hourly(employee)
        return Rate(amount=rate.amount, basis=rate.basis, assumption_ids=rate.assumption_ids)

    # -- overtime ----------------------------------------------------------
    def overtime_rate(self, base: Rate) -> tuple[Decimal, tuple[str, ...], str]:
        """Overtime is paid on the BASE rate plus only the burdens that scale with overtime
        -- not the full benefits multiplier. Health and OPEB do not grow because someone
        works an extra shift."""
        premium = self.a.value("c3_ot_premium_multiplier")
        burden = self.a.value("c3_ot_burden_multiplier")
        amount = base.amount * premium * burden
        note = f"${base.amount:,.2f} base x {premium} FLSA premium x {burden} overtime burden"
        return amount, ("c3_ot_premium_multiplier", "c3_ot_burden_multiplier"), note
