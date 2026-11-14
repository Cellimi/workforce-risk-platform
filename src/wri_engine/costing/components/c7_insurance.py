"""C7 -- Employment practices liability, and what the insurer paid.

A private employer facing a discrimination charge or a wrongful-termination suit usually has
an employment practices liability policy behind it. That changes who pays, not whether the
money is spent, and a cost model that quietly reports only the employer's share understates
what the discipline actually cost.

**C7-offset** credits back whatever the insurer paid, so gross shows the **full exposure**
and net shows the **employer's burden**. That is the same shape as C3 and C5, where the
engine charges gross and credits what the employer did not pay.

The retention trap
------------------
The employer's own share and the insurer's share are two slices of one payout, and it is
easy to count the employer's slice twice. If C4 has already booked a $45,000 settlement and
the insurer paid $30,000 of it, the employer's $15,000 retention is *already* in the
number -- it is the part the offset does not credit back. Booking the retention again as its
own line would overstate the matter by exactly the retention.

So the retention is booked **only when nothing else captured the payout**: when the record
carries a retention but no settlement and no back pay, which is what a client's accounts
payable export looks like when all it knows is "we paid our deductible". Where both are
recorded, a validation warning says so rather than the engine guessing.

Every figure here is read from the record; none is estimated. No premium is counted either:
a premium is paid whether or not anyone is disciplined, so it is not a cost of this action
(rule 9).
"""

from __future__ import annotations

from wri_engine.costing.context import CostContext, money
from wri_engine.schema import CostComponent, CostLineItem, DisciplineAction

#: Component codes this module can emit. Read by `SectorPack.component_codes`.
PRODUCES = ("C7", "C7-offset")

#: None. Every figure this component books is an actual read from the record.
REQUIRES_ASSUMPTIONS = ()


def compute(action: DisciplineAction, ctx: CostContext) -> list[CostLineItem]:
    employee = ctx.employee_for(action)
    if employee is None:
        return []

    items: list[CostLineItem] = []
    for appeal in ctx.appeals_for(action):
        forum = str(appeal.forum).replace("_", " ")
        payout_booked = appeal.settlement_amount > 0 or appeal.back_pay_awarded > 0

        if appeal.insurance_retention_paid > 0 and not payout_booked:
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C7_INSURANCE,
                    subcomponent="policy_retention",
                    amount=money(appeal.insurance_retention_paid),
                    formula=(
                        f"Retention the employer bore under its employment practices "
                        f"liability policy on a {forum}: "
                        f"${appeal.insurance_retention_paid:,.2f} from the record. Counted "
                        f"because no settlement or back pay was recorded for this matter, "
                        f"so this is the only figure capturing what the employer paid."
                    ),
                    inputs={
                        "appeal_id": appeal.appeal_id,
                        "forum": str(appeal.forum),
                        "retention_paid": str(appeal.insurance_retention_paid),
                        "payout_recorded_elsewhere": False,
                    },
                    assumption_ids=[],
                    cost_incomplete=appeal.is_open,
                )
            )

        if appeal.insurer_paid_amount > 0:
            items.append(
                CostLineItem(
                    action_id=action.action_id,
                    employee_id=employee.employee_id,
                    component=CostComponent.C7_OFFSET,
                    subcomponent="insurer_paid_credited",
                    amount=money(-appeal.insurer_paid_amount),
                    formula=(
                        f"Credit for the ${appeal.insurer_paid_amount:,.2f} an insurer paid "
                        f"on this {forum}. Gross above shows the full exposure; this line "
                        f"takes out the part the employer did not pay, so net is the "
                        f"employer's own burden. The premium that bought the cover is not "
                        f"counted: it is paid whether or not anyone is disciplined."
                    ),
                    inputs={
                        "appeal_id": appeal.appeal_id,
                        "forum": str(appeal.forum),
                        "insurer_paid": str(appeal.insurer_paid_amount),
                    },
                    assumption_ids=[],
                    cost_incomplete=appeal.is_open,
                )
            )
    return items
