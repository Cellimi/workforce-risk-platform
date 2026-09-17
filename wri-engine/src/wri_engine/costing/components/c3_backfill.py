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
from wri_engine.schema import CostComponent, CostLineItem, DisciplineAction


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    if employee is None:
        return []

    items: list[CostLineItem] = []
    if employee.minimum_staffing_role:
        items.extend(_suspension_backfill(action, ctx))
        items.extend(_admin_leave_backfill(action, ctx))
    items.extend(_unpaid_suspension_offset(action, ctx))
    return items


def _suspension_backfill(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    if not action.action_type.is_suspension or action.suspension_days <= 0:
        return []
    employee = ctx.employee_for(action)
    hours = Decimal(action.suspension_days) * employee.standard_shift_hours
    base = ctx.rates.backfill_base_rate(employee)
    ot_rate, ot_ids, ot_note = ctx.rates.overtime_rate(base)
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=employee.employee_id,
            component=CostComponent.C3_BACKFILL,
            subcomponent="backfill_suspension",
            amount=money(hours * ot_rate),
            formula=(
                f"Backfill for a {action.suspension_days}-shift suspension in a "
                f"minimum-staffing post: {action.suspension_days} shifts x "
                f"{employee.standard_shift_hours} hrs x ${ot_rate:,.2f}/hr overtime "
                f"({ot_note}; {base.basis})"
            ),
            inputs={
                "lost_shifts": action.suspension_days,
                "shift_hours": str(employee.standard_shift_hours),
                "overtime_hours": str(hours),
                "backfill_base_rate": str(money(base.amount)),
                "overtime_rate": str(money(ot_rate)),
                "rate_basis": base.basis,
            },
            assumption_ids=list(ot_ids),
        )
    ]


def _admin_leave_backfill(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    base = ctx.rates.backfill_base_rate(employee)
    ot_rate, ot_ids, ot_note = ctx.rates.overtime_rate(base)
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
                    f"Overtime covering the post during paid admin leave "
                    f"{leave.start_date} to {leave.end_date}: {shifts} shifts x "
                    f"{employee.standard_shift_hours} hrs x ${ot_rate:,.2f}/hr overtime "
                    f"({ot_note}; {base.basis}). The county pays twice here -- the leave "
                    f"itself is C2."
                ),
                inputs={
                    "leave_id": leave.leave_id,
                    "lost_shifts": str(shifts),
                    "shift_hours": str(employee.standard_shift_hours),
                    "overtime_hours": str(hours),
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


def vacancy_period_shifts(ctx: CostContext, employee, start, days: int) -> Decimal:
    """Scheduled shifts across a vacancy of `days` calendar days beginning the day after
    `start`. Shared with C5, which costs vacancy coverage the same way."""
    if days <= 0:
        return Decimal("0")
    first = start + timedelta(days=1)
    return ctx.shifts_between(employee, first, first + timedelta(days=days - 1))
