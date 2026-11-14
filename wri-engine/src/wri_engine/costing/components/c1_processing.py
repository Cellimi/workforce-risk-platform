"""C1 -- Processing labor.

Every discipline action consumes staff time before it produces any other cost. A supervisor
writes it up, HR or labor relations reviews it, and a deciding official signs it. If the
matter was investigated, an investigator's hours are added on top.

Each person's hours are costed at that person's own loaded rate when the record names them.
When it does not -- HR staff are never in the HR extract, and senior deciding officials
usually are not named on the action -- the hours are costed at a reference salary from
`assumptions.yaml`, and the line item says so.

Hours by action type live in `assumptions.yaml` as `c1_hours_<action>_<actor>`. None of them
are in this file.
"""

from __future__ import annotations

from decimal import Decimal

from wri_engine.costing.context import CostContext, money
from wri_engine.costing.rates import Rate
from wri_engine.schema import ActionType, CostComponent, CostLineItem, DisciplineAction

#: Action types that share one set of processing hours.
_HOURS_KEY = {
    ActionType.DOCUMENTED_COUNSELING: "counseling",
    ActionType.WRITTEN_REPRIMAND: "reprimand",
    ActionType.SUSPENSION_1_3: "suspension",
    ActionType.SUSPENSION_4_14: "suspension",
    ActionType.SUSPENSION_15_PLUS: "suspension",
    ActionType.DEMOTION: "demotion",
    ActionType.REMOVAL: "removal",
}

_ACTOR_LABEL = {
    "supervisor": "Supervisor",
    "hr": "HR / labor relations",
    "deciding": "Deciding official",
}


#: Component codes this module can emit. Read by `SectorPack.component_codes`.
PRODUCES = ("C1",)

#: Coefficients this module needs beyond the ones every sector shares. Declared here so the
#: engine's preflight finds a missing one by name before a run starts.
REQUIRES_ASSUMPTIONS = ("c1_outside_investigator_hourly_rate",)


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    if employee is None:
        return []

    key = _HOURS_KEY[action.action_type]
    actors: dict[str, Rate] = {
        "supervisor": ctx.rates.supervisor_rate(employee),
        "hr": ctx.rates.hr_loaded_hourly(),
        "deciding": ctx.rates.deciding_official_rate(
            str(action.deciding_official_level), action.deciding_official_id
        ),
    }

    items: list[CostLineItem] = []
    for actor, rate in actors.items():
        assumption_id = f"c1_hours_{key}_{actor}"
        hours = ctx.value(assumption_id)
        if hours <= 0:
            continue
        items.append(
            _line(
                action,
                employee.employee_id,
                subcomponent=f"{actor}_hours",
                hours=hours,
                rate=rate,
                label=f"{_ACTOR_LABEL[actor]} time on a {action.action_type.replace('_', ' ')}",
                assumption_ids=[assumption_id, *rate.assumption_ids],
            )
        )

    items.extend(_investigation(action, ctx))
    items.extend(_outside_investigator(action, ctx))
    return items


def _outside_investigator(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    """A firm brought in from outside to run the investigation.

    Public employers normally use an internal investigator, so their records leave this at
    zero and no line item appears. A private employer facing a harassment complaint
    routinely retains outside counsel or a specialist firm instead, precisely so the
    investigation is independent of the managers involved -- and that is billed by the hour
    on top of whatever internal time the matter still takes.
    """
    if action.outside_investigator_hours <= 0:
        return []
    employee = ctx.employee_for(action)
    rate_id = "c1_outside_investigator_hourly_rate"
    rate = ctx.value(rate_id)
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=employee.employee_id,
            component=CostComponent.C1_PROCESSING,
            subcomponent="outside_investigator",
            amount=money(action.outside_investigator_hours * rate),
            formula=(
                f"Outside investigator retained for this matter: "
                f"{action.outside_investigator_hours} hrs x ${rate:,.2f}/hr"
            ),
            inputs={
                "hours": str(action.outside_investigator_hours),
                "hourly_rate": str(rate),
            },
            assumption_ids=[rate_id],
        )
    ]


def _investigation(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    if not action.was_investigated or action.investigation_type is None:
        return []
    employee = ctx.employee_for(action)
    investigation_type = str(action.investigation_type)
    assumption_id = f"c1_investigation_hours_{investigation_type}"
    hours = ctx.value(assumption_id)
    if hours <= 0:
        return []
    rate = {
        "supervisory": lambda: ctx.rates.supervisor_rate(employee),
        "hr": ctx.rates.hr_loaded_hourly,
        "internal_affairs": lambda: ctx.rates.internal_affairs_rate(employee),
    }[investigation_type]()
    return [
        _line(
            action,
            employee.employee_id,
            subcomponent=f"investigation_{investigation_type}",
            hours=hours,
            rate=rate,
            label=f"{investigation_type.replace('_', ' ').title()} investigation",
            assumption_ids=[assumption_id, *rate.assumption_ids],
        )
    ]


def _line(
    action: DisciplineAction,
    employee_id: str,
    *,
    subcomponent: str,
    hours: Decimal,
    rate: Rate,
    label: str,
    assumption_ids: list[str],
) -> CostLineItem:
    return CostLineItem(
        action_id=action.action_id,
        employee_id=employee_id,
        component=CostComponent.C1_PROCESSING,
        subcomponent=subcomponent,
        amount=money(hours * rate.amount),
        formula=f"{label}: {hours} hrs x ${rate.amount:,.2f}/hr ({rate.basis})",
        inputs={
            "hours": str(hours),
            "hourly_rate": str(money(rate.amount)),
            "rate_basis": rate.basis,
        },
        assumption_ids=list(dict.fromkeys(assumption_ids)),
    )
