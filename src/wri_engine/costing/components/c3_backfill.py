"""C3 -- Backfill overtime, and the C3-offset for unpaid suspensions.

A minimum-staffing post has to be filled. When a deputy, corrections officer,
telecommunicator or firefighter is suspended or sent home on administrative leave, someone
else works that shift on overtime.

    backfill = lost shifts x shift hours x peer base rate x FLSA premium x overtime burden

Two things about that formula matter.

**The peer's rate, not the disciplined employee's.** Overtime is worked by whoever covers
the post, so the rate is the role family's average base rate at that location.

**The overtime burden is not the full benefits multiplier.** Health insurance and retiree
health do not grow because someone works an extra shift. Only the burdens that scale with
wages do -- FICA, and pension where overtime is pensionable. That is a separate, smaller
multiplier in `assumptions.yaml`.

**Paid administrative leave costs the county twice**: the leave itself (C2) and the overtime
covering the empty post (C3). That double charge is deliberate and is the single most
counter-intuitive number in the model.

The **C3-offset** is the mirror image: an unpaid suspension saves the wage the employee does
not earn. It is recorded as a negative line item so the net effect is visible and the
headline can never quietly hide it. The saving is the wage plus only the burdens that scale
with the wage -- the county keeps paying health and OPEB throughout an unpaid suspension.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from wri_engine.costing.context import CostContext, money
from wri_engine.schema import CostComponent, CostLineItem, DisciplineAction, FLSAStatus
from wri_engine.schema.enums import CoverageMethod

#: Component codes this module can emit. Read by `SectorPack.component_codes`.
PRODUCES = ("C3", "C3-offset")

#: Required in both sectors: a public employer can hire an agency temp too, and a model that
#: only priced one way of covering a post would quietly miss the other.
REQUIRES_ASSUMPTIONS = ("c3_temp_agency_markup",)


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    if employee is None:
        return []

    items: list[CostLineItem] = []
    if employee.minimum_staffing_role and employee.coverage_method != CoverageMethod.NONE:
        items.extend(_suspension_backfill(action, ctx))
        items.extend(_admin_leave_backfill(action, ctx))
    items.extend(_unpaid_suspension_offset(action, ctx))
    return items


def _coverage_rate(employee, ctx: CostContext):
    """What an hour of cover costs, and how to say so.

    Two ways to fill a post. A public minimum-staffing post is covered by a peer on
    overtime, priced off the peer's own base rate at time and a half. A private employer
    more often calls an agency, which bills a single marked-up rate: no FLSA premium,
    because the temp is not working overtime, but a markup that carries the agency's
    payroll taxes, insurance, recruiting and margin.

    Returns (rate, assumption ids, a phrase for the formula, a noun for the line).
    """
    base = ctx.rates.backfill_base_rate(employee)
    if employee.coverage_method == CoverageMethod.TEMP_AGENCY:
        markup_id = "c3_temp_agency_markup"
        markup = ctx.value(markup_id)
        rate = base.amount * markup
        note = f"${base.amount:,.2f} peer base x {markup} agency markup"
        return rate, (markup_id,), f"{note}; {base.basis}", "agency cover"
    rate, ids, note = ctx.rates.overtime_rate(base)
    return rate, ids, f"{note}; {base.basis}", "overtime"


def _suspension_backfill(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    if not action.action_type.is_suspension or action.suspension_days <= 0:
        return []
    employee = ctx.employee_for(action)
    hours = Decimal(action.suspension_days) * employee.standard_shift_hours
    base = ctx.rates.backfill_base_rate(employee)
    rate, rate_ids, rate_note, noun = _coverage_rate(employee, ctx)
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=employee.employee_id,
            component=CostComponent.C3_BACKFILL,
            subcomponent="backfill_suspension",
            amount=money(hours * rate),
            formula=(
                f"Cover for a {action.suspension_days}-shift suspension in a post that "
                f"must be staffed: {action.suspension_days} shifts x "
                f"{employee.standard_shift_hours} hrs x ${rate:,.2f}/hr {noun} "
                f"({rate_note})"
            ),
            inputs={
                "lost_shifts": action.suspension_days,
                "shift_hours": str(employee.standard_shift_hours),
                "coverage_method": str(employee.coverage_method),
                "overtime_hours": str(hours),
                "backfill_base_rate": str(money(base.amount)),
                "overtime_rate": str(money(rate)),
                "rate_basis": base.basis,
            },
            assumption_ids=list(rate_ids),
        )
    ]


def _admin_leave_backfill(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    base = ctx.rates.backfill_base_rate(employee)
    ot_rate, ot_ids, ot_note_raw, noun = _coverage_rate(employee, ctx)
    ot_note = ot_note_raw
    items: list[CostLineItem] = []
    for leave in ctx.paid_leave_for(action):
        shifts = ctx.shifts_between(employee, leave.start_date, leave.end_date)
        hours = shifts * employee.standard_shift_hours
        if hours <= 0:
            continue
        items.append(
            CostLineItem(
                action_id=action.action_id,
                employee_id=employee.employee_id,
                component=CostComponent.C3_BACKFILL,
                subcomponent="backfill_admin_leave",
                amount=money(hours * ot_rate),
                formula=(
                    f"Cover for the post during paid leave "
                    f"{leave.start_date} to {leave.end_date}: {shifts} shifts x "
                    f"{employee.standard_shift_hours} hrs x ${ot_rate:,.2f}/hr {noun} "
                    f"({ot_note}). The employer pays twice here -- the leave itself "
                    f"is C2."
                ),
                inputs={
                    "leave_id": leave.leave_id,
                    "lost_shifts": str(shifts),
                    "shift_hours": str(employee.standard_shift_hours),
                    "overtime_hours": str(hours),
                    "coverage_method": str(employee.coverage_method),
                    "backfill_base_rate": str(money(base.amount)),
                    "overtime_rate": str(money(ot_rate)),
                    "rate_basis": base.basis,
                },
                assumption_ids=list(ot_ids),
            )
        )
    return items


def _unpaid_suspension_offset(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    if not action.action_type.is_suspension or action.suspension_days <= 0:
        return []
    employee = ctx.employee_for(action)

    blocked = _salary_basis_block(action, employee)
    if blocked is not None:
        return [blocked]

    hours = Decimal(action.suspension_days) * employee.standard_shift_hours
    burden_id = "c3_unpaid_suspension_burden_multiplier"
    burden = ctx.value(burden_id)
    saved = hours * employee.hourly_base_rate * burden
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=employee.employee_id,
            component=CostComponent.C3_OFFSET,
            subcomponent="unpaid_suspension_salary_saved",
            amount=money(-saved),
            formula=(
                f"Salary not paid during a {action.suspension_days}-shift unpaid "
                f"suspension: {action.suspension_days} shifts x "
                f"{employee.standard_shift_hours} hrs x "
                f"${employee.hourly_base_rate:,.2f} base x {burden} wage-scaling burden. "
                f"Recorded as a negative line item; health and OPEB continue and are not "
                f"saved."
            ),
            inputs={
                "suspension_days": action.suspension_days,
                "shift_hours": str(employee.standard_shift_hours),
                "unpaid_hours": str(hours),
                "hourly_base_rate": str(money(employee.hourly_base_rate)),
                "burden_multiplier": str(burden),
            },
            assumption_ids=[burden_id],
        )
    ]


def _salary_basis_block(action: DisciplineAction, employee) -> CostLineItem | None:
    """The saving an exempt employee's unpaid suspension does NOT produce.

    Under the FLSA salary-basis rule, an employer may dock an exempt employee's pay for a
    disciplinary suspension only in full-day increments, in good faith, for infractions of
    workplace conduct rules, and only where a written policy applicable to all employees
    exists (29 CFR 541.602(b)(5)). Without that policy the suspension cannot lawfully be
    unpaid: the employee is suspended and still paid, so there is no wage saving to credit.

    Getting this wrong is expensive in both directions. Claim the saving and the total is
    understated and the advice is unlawful; stay silent and the drill-down looks like the
    engine simply forgot. So a zero-valued offset line is emitted instead, which says in
    plain words why the saving is not there.

    Returns that line for an exempt employee with no written policy, otherwise None.
    """
    if employee.flsa_status != FLSAStatus.EXEMPT or action.written_conduct_policy:
        return None
    return CostLineItem(
        action_id=action.action_id,
        employee_id=employee.employee_id,
        component=CostComponent.C3_OFFSET,
        subcomponent="unpaid_suspension_not_available",
        amount=Decimal("0.00"),
        formula=(
            f"No wage saving: {employee.job_title} is salaried exempt and the record shows "
            f"no written workplace conduct policy applying to all employees. The FLSA "
            f"salary-basis rule (29 CFR 541.602(b)(5)) permits an unpaid disciplinary "
            f"suspension of an exempt employee only under such a policy, so the "
            f"{action.suspension_days}-day suspension is served on full pay and there is "
            f"nothing to credit back."
        ),
        inputs={
            "flsa_status": str(employee.flsa_status),
            "written_conduct_policy": action.written_conduct_policy,
            "suspension_days": action.suspension_days,
            "authority": "29 CFR 541.602(b)(5)",
        },
        assumption_ids=[],
    )


def vacancy_period_shifts(ctx: CostContext, employee, start, days: int) -> Decimal:
    """Scheduled shifts across a vacancy of `days` calendar days beginning the day after
    `start`. Shared with C5, which costs vacancy coverage the same way."""
    if days <= 0:
        return Decimal("0")
    first = start + timedelta(days=1)
    return ctx.shifts_between(employee, first, first + timedelta(days=days - 1))
