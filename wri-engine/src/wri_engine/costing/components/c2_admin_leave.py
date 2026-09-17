"""C2 -- Paid administrative leave.

When an employee is relieved of duty pending an outcome, the county keeps paying them for
shifts they do not work. This is the most literal cost in the model: it comes entirely from
the dates on the leave record and the employee's own loaded rate.

    cost = scheduled shifts in the leave period x shift hours x loaded hourly rate

"Scheduled shifts" comes from the schedule model in `org_county.yaml`, so a Monday-to-Friday
civilian is not charged for the weekend, and a 24/48 firefighter is charged for one shift in
three rather than every calendar day.

Only PAID leave is counted. Unpaid leave costs nothing.
"""

from __future__ import annotations

from wri_engine.costing.context import CostContext, money
from wri_engine.schema import CostComponent, CostLineItem, DisciplineAction


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    if employee is None:
        return []

    items: list[CostLineItem] = []
    rate = ctx.rates.loaded_hourly(employee)
    schedule = ctx.schedule_for(employee)
    for leave in ctx.paid_leave_for(action):
        shifts = ctx.shifts_between(employee, leave.start_date, leave.end_date)
        hours = shifts * employee.standard_shift_hours
        if hours <= 0:
            continue
        items.append(
            CostLineItem(
                action_id=action.action_id,
                employee_id=employee.employee_id,
                component=CostComponent.C2_ADMIN_LEAVE,
                subcomponent="paid_admin_leave",
                amount=money(hours * rate.amount),
                formula=(
                    f"Paid admin leave {leave.start_date} to {leave.end_date} "
                    f"({leave.calendar_days} calendar days, {schedule.id} schedule): "
                    f"{shifts} shifts x {employee.standard_shift_hours} hrs x "
                    f"${rate.amount:,.2f}/hr loaded ({rate.basis})"
                ),
                inputs={
                    "leave_id": leave.leave_id,
                    "start_date": leave.start_date.isoformat(),
                    "end_date": leave.end_date.isoformat(),
                    "calendar_days": leave.calendar_days,
                    "schedule": schedule.id,
                    "scheduled_shifts": str(shifts),
                    "shift_hours": str(employee.standard_shift_hours),
                    "paid_hours": str(hours),
                    "loaded_hourly_rate": str(money(rate.amount)),
                },
                assumption_ids=list(rate.assumption_ids),
            )
        )
    return items
