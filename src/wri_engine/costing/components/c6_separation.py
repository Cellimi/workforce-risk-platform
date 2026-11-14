"""C6 -- Separation agreements and unemployment.

Two costs that follow a private-sector removal out of the door and are almost never on the
discipline ledger.

**The separation agreement.** Many private removals end with a signed agreement: the employer
pays a sum, the employee releases claims. That payment is a cost of the discipline action as
surely as the hours spent writing it up, and somebody had to draft and negotiate it.

**The unemployment claim.** A separated employee usually files. Benefits paid on that claim
are charged to the employer's unemployment account, and in an experience-rated state a
dollar charged today raises the employer's tax tomorrow.

What this component will and will not do
----------------------------------------
It counts the severance the record says was paid, and the benefits the record says were
charged. It does **not** estimate either one. An agreement recorded with no amount is not
guessed at from weeks-of-service: it is reported as a cost that cannot be counted, so the
total is visibly a floor rather than quietly wrong (rule 13).

Public-sector data carries none of these fields, so this component produces nothing for it.
"""

from __future__ import annotations

from decimal import Decimal

from wri_engine.costing.context import CostContext, money
from wri_engine.schema import CostComponent, CostLineItem, DisciplineAction

#: Component codes this module can emit. Read by `SectorPack.component_codes`.
PRODUCES = ("C6",)

REQUIRES_ASSUMPTIONS = (
    "c6_separation_agreement_hr_hours",
    "c6_separation_agreement_counsel_hours",
    "c6_unemployment_experience_rating_multiplier",
)


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    separation = ctx.separation_for(action)
    if employee is None or separation is None:
        return []
    if not separation.separation_reason.is_discipline_driven:
        return []

    items: list[CostLineItem] = []
    items.extend(_severance(action, ctx, separation))
    items.extend(_agreement_labour(action, ctx, separation))
    items.extend(_unemployment(action, ctx, separation))
    return items


def _severance(action, ctx: CostContext, separation) -> list[CostLineItem]:
    """What the employer actually paid under the agreement. Never estimated."""
    if separation.severance_amount <= 0:
        return []
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=separation.employee_id,
            component=CostComponent.C6_SEPARATION,
            subcomponent="severance_paid",
            amount=money(separation.severance_amount),
            formula=(
                f"Severance paid under a separation agreement signed on leaving: "
                f"${separation.severance_amount:,.2f} from the record"
            ),
            inputs={
                "separation_date": separation.separation_date.isoformat(),
                "severance_amount": str(separation.severance_amount),
            },
            assumption_ids=[],
        )
    ]


def _agreement_labour(action, ctx: CostContext, separation) -> list[CostLineItem]:
    """Drafting and negotiating the agreement costs HR time and, usually, counsel time."""
    if not separation.separation_agreement:
        return []
    items: list[CostLineItem] = []

    hours_id = "c6_separation_agreement_hr_hours"
    hours = ctx.value(hours_id)
    rate = ctx.rates.hr_loaded_hourly()
    if hours > 0:
        items.append(
            CostLineItem(
                action_id=action.action_id,
                employee_id=separation.employee_id,
                component=CostComponent.C6_SEPARATION,
                subcomponent="agreement_hr_hours",
                amount=money(hours * rate.amount),
                formula=(
                    f"HR time drafting and negotiating the separation agreement: "
                    f"{hours} hrs x ${rate.amount:,.2f}/hr ({rate.basis})"
                ),
                inputs={"hours": str(hours), "hourly_rate": str(money(rate.amount))},
                assumption_ids=[hours_id, *rate.assumption_ids],
            )
        )

    counsel_hours_id = "c6_separation_agreement_counsel_hours"
    counsel_rate_id = "c4_outside_counsel_hourly_rate"
    counsel_hours = ctx.value(counsel_hours_id)
    counsel_rate = ctx.value(counsel_rate_id)
    if counsel_hours > 0:
        items.append(
            CostLineItem(
                action_id=action.action_id,
                employee_id=separation.employee_id,
                component=CostComponent.C6_SEPARATION,
                subcomponent="agreement_counsel",
                amount=money(counsel_hours * counsel_rate),
                formula=(
                    f"Outside counsel reviewing the separation agreement and its release: "
                    f"{counsel_hours} hrs x ${counsel_rate:,.2f}/hr"
                ),
                inputs={
                    "hours": str(counsel_hours),
                    "hourly_rate": str(counsel_rate),
                },
                assumption_ids=[counsel_hours_id, counsel_rate_id],
            )
        )
    return items


def _unemployment(action, ctx: CostContext, separation) -> list[CostLineItem]:
    """Benefits charged to the employer's account, with the experience-rating effect shown
    separately so it can be set to nil without touching the charged amount."""
    charged = separation.unemployment_benefits_charged
    if charged <= 0:
        return []
    multiplier_id = "c6_unemployment_experience_rating_multiplier"
    multiplier = ctx.value(multiplier_id)
    amount = charged * multiplier
    tail = (
        ""
        if multiplier == Decimal("1")
        else (
            f" x {multiplier} experience-rating multiplier, because a dollar charged to the "
            f"account raises the employer's later tax by more than a dollar"
        )
    )
    return [
        CostLineItem(
            action_id=action.action_id,
            employee_id=separation.employee_id,
            component=CostComponent.C6_SEPARATION,
            subcomponent="unemployment_benefits_charged",
            amount=money(amount),
            formula=(
                f"Unemployment benefits charged to the employer's account for this "
                f"separation: ${charged:,.2f} from the record{tail}"
            ),
            inputs={
                "benefits_charged": str(charged),
                "experience_rating_multiplier": str(multiplier),
                "claim_filed": separation.unemployment_claim_filed,
            },
            assumption_ids=[multiplier_id],
        )
    ]
