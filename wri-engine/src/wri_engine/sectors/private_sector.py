"""The private-sector pack: a company rather than a government employer.

Same engine, same principle -- a cost is counted only when the record shows the event
happened. What differs is which events there are to count.

What this pack adds
-------------------
**C6, separation and unemployment.** A private removal often ends in a signed agreement with
a payment attached, and the separated employee usually files an unemployment claim that is
charged back to the employer's account. Neither has a public-sector equivalent in this model.

**C7, employment practices liability.** Where a policy stands behind a charge or a suit, the
insurer pays much of it. The pack books the full exposure as gross and credits the
insurer-paid part back, so the employer's own burden is the net and the transfer is visible.

What it reuses
--------------
Processing labour, paid leave, coverage, challenges and turnover are the same arithmetic,
reading the same coefficient ids. Two places where the sector genuinely differs are handled
by the **data**, not by a branch on the sector:

* **Coverage.** `Employee.coverage_method` says whether a post is covered by a peer on
  overtime or by an agency temp billed at a marked-up rate.
* **The unpaid-suspension saving.** For a salaried exempt employee the FLSA salary-basis
  rule allows an unpaid disciplinary suspension only under a written conduct policy
  (29 CFR 541.602(b)(5)), so `DisciplineAction.written_conduct_policy` decides whether there
  is a wage saving to credit at all.

Turnover reuses the `civilian_*` profiles. The name is a public-sector inheritance, but the
shape is right for a private employer: recruiting, screening, onboarding, a productivity
ramp and a washout allowance, with no academy and no field training.
"""

from __future__ import annotations

from wri_engine.costing.components import (
    c1_processing,
    c2_admin_leave,
    c3_backfill,
    c4_appeals,
    c5_turnover,
    c6_separation,
    c7_insurance,
)
from wri_engine.schema import AppealForum
from wri_engine.sectors.base import SectorPack

PACK = SectorPack(
    id="private_sector",
    label="Private sector",
    description=(
        "A company. Posts that must be staffed are covered by overtime or an agency temp; "
        "challenges run through a union grievance where a contract exists and otherwise "
        "through an agency charge, a demand letter, mediation or litigation; a removal "
        "often carries a separation agreement, an unemployment claim and an insurer."
    ),
    components=(
        c1_processing,
        c2_admin_leave,
        c3_backfill,
        c4_appeals,
        c5_turnover,
        c6_separation,
        c7_insurance,
    ),
    benefits_assumption_by_group={
        "private_all": "private_benefits_multiplier_all",
    },
    default_benefits_group="private_all",
    turnover_profiles=("civilian_skilled", "civilian_standard"),
    standard_turnover_profile="civilian_standard",
    challenge_forums=(
        AppealForum.GRIEVANCE_STEP,
        AppealForum.ARBITRATION,
        AppealForum.AGENCY_CHARGE,
        AppealForum.DEMAND_LETTER,
        AppealForum.MEDIATION,
        AppealForum.COURT,
    ),
)
