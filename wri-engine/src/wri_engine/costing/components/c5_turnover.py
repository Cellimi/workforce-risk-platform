"""C5 -- Removal turnover: recruiting, hiring and training a replacement.

This is usually the largest component, and the one agencies never put on the discipline
ledger. Removing someone does not end the cost; it starts a second one.

Applies when a discipline-driven separation is linked to the action -- a removal for cause,
or a resignation in lieu of removal. Three rules bound it:

* **Position abolished -> no turnover cost.** Nobody is hired, so nothing is spent.
* **Not yet refilled -> expected costs, flagged `cost_incomplete`.** The vacancy is costed at
  the expected refill duration for that role, and the action is marked as still accruing.
* **Reinstated on appeal -> no separation record exists**, so this component never runs.

What it counts, itemized so each piece stands on its own:

*Vacancy period.* For a minimum-staffing post, the seat is covered by overtime until the
replacement reaches solo duty -- and the removed employee's own salary stops, which is
credited back as a **C5-offset** line item over exactly the same shifts. Charging the
overtime without crediting the salary would overstate the cost, and would be inconsistent
with how C3 already treats an unpaid suspension.

For every other role the seat simply sits empty, and the cost is the share of that position's
output nobody delivers. Those roles get **no salary offset**, because
`c5_vacancy_productivity_loss_factor` is defined as a NET figure: the value of the work
nobody did over and above the salary already saved. The assumption's notes ask the owner to
confirm that reading -- if the factor is meant as gross lost output, a civilian offset is
needed too.

A position that is **abolished** produces no C5 line items at all, so it has no offset
either: nobody is hired, no overtime is worked, and the salary saving belongs to the
abolished post rather than to the discipline action. A **resignation in lieu of removal**
follows the same path as a removal, offset included.

*Recruiting and selection.* Advertising, recruiter hours, testing, interview panel time, and
pre-employment screening -- background investigation, polygraph, psychological evaluation,
medical exam, drug screen. Screening costs differ by an order of magnitude between a sworn
background investigation and a civilian records check.

*Onboarding and training.* Orientation, academy tuition AND the recruit's full loaded salary
for the academy's length, field training (the trainee's salary plus the trainer's
differential), the productivity ramp for civilian roles, and equipment and uniform issue.

*Attrition adjustment.* Not every recruit finishes. Recruiting and training costs are divided
by `(1 - washout_rate)`, because filling one seat means hiring more than one person. The
multiplier appears in every line item it touches, so it is never hidden in a total.
"""

from __future__ import annotations

from decimal import Decimal

from wri_engine.costing.components.c3_backfill import vacancy_period_shifts
from wri_engine.costing.context import CostContext, money
from wri_engine.schema import CostComponent, CostLineItem, DisciplineAction, Employee

_WEEKS_PER_YEAR = Decimal("52")


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    separation = ctx.separation_for(action)
    if employee is None or separation is None:
        return []
    if not separation.separation_reason.is_discipline_driven:
        return []
    if separation.position_abolished or action.position_abolished:
        return []  # the seat is gone; there is no replacement to hire

    builder = _Builder(action, employee, separation, ctx)
    return [
        *builder.vacancy(),
        *builder.recruiting_and_selection(),
        *builder.onboarding_and_training(),
        *builder.productivity_ramp(),
    ]


class _Builder:
    def __init__(self, action, employee: Employee, separation, ctx: CostContext):
        self.action = action
        self.employee = employee
        self.separation = separation
        self.ctx = ctx
        self.profile = employee.turnover_profile
        self.refilled = separation.position_refilled and separation.refill_date is not None
        self.incomplete = not self.refilled

        if self.refilled:
            self.vacancy_days = (separation.refill_date - separation.separation_date).days
            self.vacancy_basis = (
                f"separated {separation.separation_date}, replacement on duty "
                f"{separation.refill_date}"
            )
            self.vacancy_assumptions: list[str] = []
        else:
            assumption_id = self._id("expected_vacancy_days")
            self.vacancy_days = int(ctx.value(assumption_id))
            self.vacancy_basis = (
                f"position not yet refilled as of {ctx.as_of}; expected refill duration used"
            )
            self.vacancy_assumptions = [assumption_id]

        self.washout_id = self._id("washout_rate")
        self.washout = ctx.value(self.washout_id)
        if self.washout >= 1:
            raise ValueError(
                f"{self.washout_id} must be below 1; a washout rate of 1 means no recruit "
                f"ever completes training"
            )
        self.washout_multiplier = Decimal("1") / (Decimal("1") - self.washout)
        self.washout_note = f"/ (1 - {self.washout} washout) = x{self.washout_multiplier:.4f}"
        schedule = ctx.schedule_for(employee)
        self.weekly_hours = Decimal(schedule.annual_paid_hours) / _WEEKS_PER_YEAR
        self.replacement_rate = ctx.rates.step_one_loaded_hourly(employee)

    def _id(self, key: str) -> str:
        return f"c5_{key}_{self.profile}"

    # -- vacancy -----------------------------------------------------------
    def vacancy(self) -> list[CostLineItem]:
        if self.vacancy_days <= 0:
            return []
        if self.employee.minimum_staffing_role:
            return self._vacancy_overtime()
        return self._vacancy_productivity_loss()

    def _vacancy_overtime(self) -> list[CostLineItem]:
        shifts = vacancy_period_shifts(
            self.ctx, self.employee, self.separation.separation_date, self.vacancy_days
        )
        hours = shifts * self.employee.standard_shift_hours
        if hours <= 0:
            return []
        base = self.ctx.rates.backfill_base_rate(self.employee)
        ot_rate, ot_ids, ot_note = self.ctx.rates.overtime_rate(base)
        burden_id = "c5_vacancy_salary_burden_multiplier"
        burden = self.ctx.value(burden_id)
        saved = hours * self.employee.hourly_base_rate * burden
        return [
            self._item(
                "vacancy_coverage_overtime",
                hours * ot_rate,
                formula=(
                    f"Minimum-staffing post covered by overtime for {self.vacancy_days} "
                    f"days ({self.vacancy_basis}): {shifts} shifts x "
                    f"{self.employee.standard_shift_hours} hrs x ${ot_rate:,.2f}/hr "
                    f"overtime ({ot_note}; {base.basis})"
                ),
                inputs={
                    "vacancy_days": self.vacancy_days,
                    "scheduled_shifts": str(shifts),
                    "overtime_hours": str(hours),
                    "overtime_rate": str(money(ot_rate)),
                    "basis": self.vacancy_basis,
                },
                assumption_ids=[*ot_ids, *self.vacancy_assumptions],
            ),
            self._item(
                "vacancy_salary_saved",
                -saved,
                component=CostComponent.C5_OFFSET,
                formula=(
                    f"Salary no longer paid to the removed employee during the "
                    f"{self.vacancy_days}-day vacancy: {shifts} shifts x "
                    f"{self.employee.standard_shift_hours} hrs x "
                    f"${self.employee.hourly_base_rate:,.2f} base x {burden} "
                    f"wage-scaling burden. Health and OPEB for the vacant seat are not "
                    f"counted as saved."
                ),
                inputs={
                    "vacancy_days": self.vacancy_days,
                    # Identical to the overtime line above by construction: both read the
                    # same `shifts`, so the credit covers exactly the period charged.
                    "scheduled_shifts": str(shifts),
                    "unpaid_hours": str(hours),
                    "hourly_base_rate": str(money(self.employee.hourly_base_rate)),
                    "burden_multiplier": str(burden),
                    "basis": self.vacancy_basis,
                },
                assumption_ids=[burden_id, *self.vacancy_assumptions],
            ),
        ]

    def _vacancy_productivity_loss(self) -> list[CostLineItem]:
        factor_id = self._id("vacancy_productivity_loss_factor")
        factor = self.ctx.value(factor_id)
        if factor <= 0:
            return []
        shifts = vacancy_period_shifts(
            self.ctx, self.employee, self.separation.separation_date, self.vacancy_days
        )
        rate = self.ctx.rates.loaded_hourly(self.employee)
        daily = rate.amount * self.employee.standard_shift_hours
        amount = shifts * daily * factor
        if amount <= 0:
            return []
        return [
            self._item(
                "vacancy_productivity_loss",
                amount,
                formula=(
                    f"Work not done while the seat sat empty for {self.vacancy_days} days "
                    f"({self.vacancy_basis}): {shifts} work days x ${daily:,.2f} loaded "
                    f"daily rate x {factor} productivity loss factor"
                ),
                inputs={
                    "vacancy_days": self.vacancy_days,
                    "vacancy_work_days": str(shifts),
                    "loaded_daily_rate": str(money(daily)),
                    "productivity_loss_factor": str(factor),
                    "basis": self.vacancy_basis,
                },
                assumption_ids=[factor_id, *rate.assumption_ids, *self.vacancy_assumptions],
            )
        ]

    # -- recruiting and selection ------------------------------------------
    def recruiting_and_selection(self) -> list[CostLineItem]:
        items: list[CostLineItem] = []
        items += self._flat("advertising_cost", "advertising", "Job advertising and recruiting")
        items += self._hours(
            "hr_recruiter_hours",
            "hr_recruiter_hours",
            "HR recruiter and analyst time per hire",
            self.ctx.rates.hr_loaded_hourly(),
        )
        items += self._flat(
            "testing_cost", "selection_testing", "Written and physical ability testing"
        )
        items += self._panel()
        for key, sub, label in (
            (
                "background_investigation_cost",
                "background_investigation",
                "Pre-employment background investigation",
            ),
            ("polygraph_cost", "polygraph", "Pre-employment polygraph"),
            (
                "psych_eval_cost",
                "psychological_evaluation",
                "Pre-employment psychological evaluation",
            ),
            ("medical_exam_cost", "medical_exam", "Pre-placement medical examination"),
            ("drug_screen_cost", "drug_screen", "Pre-employment drug screen"),
        ):
            items += self._flat(key, sub, label)
        return items

    def _panel(self) -> list[CostLineItem]:
        size_id, hours_id = self._id("panel_size"), self._id("panel_hours")
        size, hours = self.ctx.value(size_id), self.ctx.value(hours_id)
        if size <= 0 or hours <= 0:
            return []
        rate = self.ctx.rates.supervisor_rate(self.employee)
        raw = size * hours * rate.amount
        return [
            self._item(
                "interview_panel",
                raw * self.washout_multiplier,
                formula=(
                    f"Interview panel: {size} panel members x {hours} hrs x "
                    f"${rate.amount:,.2f}/hr ({rate.basis}) = ${raw:,.2f} "
                    f"{self.washout_note}"
                ),
                inputs={
                    "panel_size": str(size),
                    "hours_each": str(hours),
                    "hourly_rate": str(money(rate.amount)),
                    "before_washout": str(money(raw)),
                    "washout_rate": str(self.washout),
                    "washout_multiplier": str(self.washout_multiplier),
                },
                assumption_ids=[size_id, hours_id, self.washout_id, *rate.assumption_ids],
            )
        ]

    # -- onboarding and training -------------------------------------------
    def onboarding_and_training(self) -> list[CostLineItem]:
        items: list[CostLineItem] = []
        items += self._hours(
            "orientation_hours",
            "orientation",
            "New-hire orientation",
            self.replacement_rate,
        )
        items += self._flat("academy_tuition", "academy_tuition", "Academy tuition per recruit")
        items += self._weeks_of_salary(
            "academy_weeks",
            "academy_salary",
            "Recruit salary during the academy",
            self.replacement_rate,
        )
        items += self._weeks_of_salary(
            "field_training_weeks",
            "field_training_trainee_salary",
            "Trainee salary during field training",
            self.replacement_rate,
        )
        items += self._trainer_differential()
        items += self._flat(
            "equipment_uniform_cost", "equipment_and_uniform", "Uniform and equipment issue"
        )
        return items

    def _trainer_differential(self) -> list[CostLineItem]:
        weeks_id, pct_id = self._id("field_training_weeks"), self._id("trainer_differential_pct")
        weeks, pct = self.ctx.value(weeks_id), self.ctx.value(pct_id)
        if weeks <= 0 or pct <= 0:
            return []
        rate = self.ctx.rates.trainer_loaded_rate(self.employee)
        hours = weeks * self.weekly_hours
        raw = hours * rate.amount * pct
        return [
            self._item(
                "field_training_trainer_differential",
                raw * self.washout_multiplier,
                formula=(
                    f"Trainer differential during field training: {weeks} weeks x "
                    f"{self.weekly_hours:.1f} hrs/week x ${rate.amount:,.2f}/hr "
                    f"({rate.basis}) x {pct} differential = ${raw:,.2f} {self.washout_note}"
                ),
                inputs={
                    "weeks": str(weeks),
                    "weekly_hours": str(self.weekly_hours),
                    "trainer_hourly_rate": str(money(rate.amount)),
                    "differential_pct": str(pct),
                    "before_washout": str(money(raw)),
                    "washout_multiplier": str(self.washout_multiplier),
                },
                assumption_ids=[weeks_id, pct_id, self.washout_id, *rate.assumption_ids],
            )
        ]

    # -- ramp (not washout adjusted: only the person who stays ramps up) ----
    def productivity_ramp(self) -> list[CostLineItem]:
        weeks_id, factor_id = self._id("ramp_weeks"), self._id("ramp_loss_factor")
        weeks, factor = self.ctx.value(weeks_id), self.ctx.value(factor_id)
        if weeks <= 0 or factor <= 0:
            return []
        rate = self.replacement_rate
        weekly_cost = self.weekly_hours * rate.amount
        amount = weeks * weekly_cost * factor
        return [
            self._item(
                "productivity_ramp",
                amount,
                formula=(
                    f"Productivity ramp for the replacement: {weeks} weeks x "
                    f"${weekly_cost:,.2f} loaded weekly rate x {factor} ramp loss factor. "
                    f"Not washout-adjusted -- only the person who stays ramps up."
                ),
                inputs={
                    "ramp_weeks": str(weeks),
                    "loaded_weekly_rate": str(money(weekly_cost)),
                    "ramp_loss_factor": str(factor),
                },
                assumption_ids=[weeks_id, factor_id, *rate.assumption_ids],
            )
        ]

    # -- shared shapes -----------------------------------------------------
    def _flat(self, key: str, subcomponent: str, label: str) -> list[CostLineItem]:
        assumption_id = self._id(key)
        raw = self.ctx.value(assumption_id)
        if raw <= 0:
            return []
        return [
            self._item(
                subcomponent,
                raw * self.washout_multiplier,
                formula=f"{label}: ${raw:,.2f} {self.washout_note}",
                inputs={
                    "before_washout": str(raw),
                    "washout_rate": str(self.washout),
                    "washout_multiplier": str(self.washout_multiplier),
                },
                assumption_ids=[assumption_id, self.washout_id],
            )
        ]

    def _hours(self, key: str, subcomponent: str, label: str, rate) -> list[CostLineItem]:
        assumption_id = self._id(key)
        hours = self.ctx.value(assumption_id)
        if hours <= 0:
            return []
        raw = hours * rate.amount
        return [
            self._item(
                subcomponent,
                raw * self.washout_multiplier,
                formula=(
                    f"{label}: {hours} hrs x ${rate.amount:,.2f}/hr ({rate.basis}) "
                    f"= ${raw:,.2f} {self.washout_note}"
                ),
                inputs={
                    "hours": str(hours),
                    "hourly_rate": str(money(rate.amount)),
                    "before_washout": str(money(raw)),
                    "washout_multiplier": str(self.washout_multiplier),
                },
                assumption_ids=[assumption_id, self.washout_id, *rate.assumption_ids],
            )
        ]

    def _weeks_of_salary(self, key: str, subcomponent: str, label: str, rate) -> list[CostLineItem]:
        assumption_id = self._id(key)
        weeks = self.ctx.value(assumption_id)
        if weeks <= 0:
            return []
        hours = weeks * self.weekly_hours
        raw = hours * rate.amount
        return [
            self._item(
                subcomponent,
                raw * self.washout_multiplier,
                formula=(
                    f"{label}: {weeks} weeks x {self.weekly_hours:.1f} hrs/week x "
                    f"${rate.amount:,.2f}/hr ({rate.basis}) = ${raw:,.2f} "
                    f"{self.washout_note}"
                ),
                inputs={
                    "weeks": str(weeks),
                    "weekly_hours": str(self.weekly_hours),
                    "hourly_rate": str(money(rate.amount)),
                    "before_washout": str(money(raw)),
                    "washout_multiplier": str(self.washout_multiplier),
                },
                assumption_ids=[assumption_id, self.washout_id, *rate.assumption_ids],
            )
        ]

    def _item(
        self,
        subcomponent: str,
        amount: Decimal,
        *,
        formula: str,
        inputs: dict,
        assumption_ids: list[str],
        component: CostComponent = CostComponent.C5_TURNOVER,
    ) -> CostLineItem:
        return CostLineItem(
            action_id=self.action.action_id,
            employee_id=self.employee.employee_id,
            component=component,
            subcomponent=subcomponent,
            amount=money(amount),
            formula=formula,
            inputs={**inputs, "turnover_profile": self.profile},
            assumption_ids=list(dict.fromkeys(assumption_ids)),
            cost_incomplete=self.incomplete,
        )
