"""C4 -- Appeals and grievances.

Counted only when an appeal or grievance record exists. Nothing here is
probability-weighted: an appeal that was never filed costs nothing, however likely it looked.

Per appeal record, up to six line items:

* internal HR / labor relations hours, by forum, at the HR reference loaded rate
* outside counsel hours x the counsel rate
* the county's flat share of arbitration costs (arbitrator fees, transcript, hearing room)
* back pay awarded, taken straight from the record
* simple interest on that back pay, from the filing date to resolution
* any settlement amount, taken straight from the record

Incomplete cost
---------------
Two situations mean the number on screen is a floor, not a total, and both set
`cost_incomplete`:

1. The appeal is still pending, or has no resolution date. More cost is coming.
2. Nobody has appealed yet, but the filing window is still open, so an appeal may still
   arrive. This case emits a $0 line item rather than nothing, so the flag is visible in the
   drill-down and countable in every rollup.

The window used is the longest one available to that employee: grievance or arbitration for
represented employees, the civil service board for non-represented ones. Court is
deliberately excluded -- its window runs months and including it would flag half a year of
actions as incomplete without telling anyone anything.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from wri_engine.costing.context import CostContext, money
from wri_engine.schema import (
    ActionType,
    CostComponent,
    CostLineItem,
    DisciplineAction,
)

_APPEALABLE = frozenset(
    {
        ActionType.SUSPENSION_1_3,
        ActionType.SUSPENSION_4_14,
        ActionType.SUSPENSION_15_PLUS,
        ActionType.DEMOTION,
        ActionType.REMOVAL,
    }
)
_REPRESENTED_FORUMS = ("grievance_step", "arbitration")
_NON_REPRESENTED_FORUMS = ("civil_service_board",)
_DAYS_PER_YEAR = Decimal("365")


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    if employee is None:
        return []

    appeals = ctx.appeals_for(action)
    if not appeals:
        return _open_window_flag(action, ctx)

    items: list[CostLineItem] = []
    for appeal in appeals:
        incomplete = appeal.is_open
        forum = str(appeal.forum)
        hours_id = f"c4_hr_hours_{forum}"
        hours = ctx.value(hours_id)
        rate = ctx.rates.hr_loaded_hourly()
        forum_label = forum.replace("_", " ")

        if hours > 0:
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C4_APPEALS,
                    subcomponent="internal_hr_hours",
                    amount=money(hours * rate.amount),
                    formula=(
                        f"Internal HR / labor relations time on a {forum_label}: "
                        f"{hours} hrs x ${rate.amount:,.2f}/hr ({rate.basis})"
                    ),
                    inputs={
                        "appeal_id": appeal.appeal_id,
                        "forum": forum,
                        "hours": str(hours),
                        "hourly_rate": str(money(rate.amount)),
                    },
                    assumption_ids=[hours_id, *rate.assumption_ids],
                    cost_incomplete=incomplete,
                )
            )

        if appeal.outside_counsel_hours > 0:
            counsel_id = "c4_outside_counsel_hourly_rate"
            counsel_rate = ctx.value(counsel_id)
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C4_APPEALS,
                    subcomponent="outside_counsel",
                    amount=money(appeal.outside_counsel_hours * counsel_rate),
                    formula=(
                        f"Outside counsel on a {forum_label}: "
                        f"{appeal.outside_counsel_hours} hrs x ${counsel_rate:,.2f}/hr"
                    ),
                    inputs={
                        "appeal_id": appeal.appeal_id,
                        "outside_counsel_hours": str(appeal.outside_counsel_hours),
                        "hourly_rate": str(counsel_rate),
                    },
                    assumption_ids=[counsel_id],
                    cost_incomplete=incomplete,
                )
            )

        if forum == "arbitration":
            flat_id = "c4_arbitration_flat_cost"
            flat = ctx.value(flat_id)
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C4_APPEALS,
                    subcomponent="arbitration_fees",
                    amount=money(flat),
                    formula=(
                        f"County share of arbitration costs (arbitrator fees and study "
                        f"time, transcript, hearing room): ${flat:,.2f} flat per case"
                    ),
                    inputs={"appeal_id": appeal.appeal_id},
                    assumption_ids=[flat_id],
                    cost_incomplete=incomplete,
                )
            )

        if appeal.back_pay_awarded > 0:
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C4_APPEALS,
                    subcomponent="back_pay",
                    amount=money(appeal.back_pay_awarded),
                    formula=(
                        f"Back pay awarded on a {forum_label}, outcome {appeal.outcome}: "
                        f"${appeal.back_pay_awarded:,.2f} from the record"
                    ),
                    inputs={
                        "appeal_id": appeal.appeal_id,
                        "outcome": str(appeal.outcome),
                        "back_pay_awarded": str(appeal.back_pay_awarded),
                    },
                    assumption_ids=[],
                    cost_incomplete=incomplete,
                )
            )
            items.extend(_back_pay_interest(action, ctx, appeal, incomplete))

        if appeal.settlement_amount > 0:
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C4_APPEALS,
                    subcomponent="settlement",
                    amount=money(appeal.settlement_amount),
                    formula=(
                        f"Settlement on a {forum_label}: "
                        f"${appeal.settlement_amount:,.2f} from the record"
                    ),
                    inputs={
                        "appeal_id": appeal.appeal_id,
                        "settlement_amount": str(appeal.settlement_amount),
                    },
                    assumption_ids=[],
                    cost_incomplete=incomplete,
                )
            )
    return items


def _back_pay_interest(
    action: DisciplineAction, ctx: CostContext, appeal, incomplete: bool
) -> list[CostLineItem]:
    if appeal.resolution_date is None:
        return []
    days = Decimal((appeal.resolution_date - appeal.filed_date).days)
    if days <= 0:
        return []
    rate_id = "c4_back_pay_interest_annual_rate"
    annual_rate = ctx.value(rate_id)
    interest = appeal.back_pay_awarded * annual_rate * days / _DAYS_PER_YEAR
    if interest <= 0:
        return []
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=action.employee_id,
            component=CostComponent.C4_APPEALS,
            subcomponent="back_pay_interest",
            amount=money(interest),
            formula=(
                f"Simple interest on back pay: ${appeal.back_pay_awarded:,.2f} x "
                f"{annual_rate} per year x {days} days / 365"
            ),
            inputs={
                "appeal_id": appeal.appeal_id,
                "back_pay_awarded": str(appeal.back_pay_awarded),
                "annual_rate": str(annual_rate),
                "days": str(days),
            },
            assumption_ids=[rate_id],
            cost_incomplete=incomplete,
        )
    ]


def _open_window_flag(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    """No appeal filed yet, but it is not too late for one."""
    if action.action_type not in _APPEALABLE:
        return []
    employee = ctx.employee_for(action)
    non_represented = ctx.org.bargaining_units.get("non_represented", "Non-represented")
    forums = (
        _NON_REPRESENTED_FORUMS
        if employee.bargaining_unit == non_represented
        else _REPRESENTED_FORUMS
    )
    window_ids = [f"c4_filing_window_days_{forum}" for forum in forums]
    windows = {fid: ctx.value(fid) for fid in window_ids}
    longest_id = max(windows, key=lambda k: windows[k])
    deadline = action.decision_date + timedelta(days=int(windows[longest_id]))
    if deadline < ctx.as_of:
        return []
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=employee.employee_id,
            component=CostComponent.C4_APPEALS,
            subcomponent="open_filing_window",
            amount=Decimal("0.00"),
            formula=(
                f"No appeal on file, but the filing window is open until {deadline} "
                f"({int(windows[longest_id])} days from the decision on "
                f"{action.decision_date}). Appeal cost so far is $0; this action's total is "
                f"a floor, not a final figure."
            ),
            inputs={
                "decision_date": action.decision_date.isoformat(),
                "window_days": str(int(windows[longest_id])),
                "filing_deadline": deadline.isoformat(),
                "as_of": ctx.as_of.isoformat(),
                "forums_considered": list(forums),
            },
            assumption_ids=window_ids,
            cost_incomplete=True,
        )
    ]
