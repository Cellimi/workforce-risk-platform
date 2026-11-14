"""The public-sector pack: a county, a city, a state agency.

This is the model Phase 1 was built for and nothing here changes it. The pack exists so the
engine can be told which sector it is costing; the components, the coefficient ids and the
figures they produce are exactly what they were before sectors existed.
"""

from __future__ import annotations

from wri_engine.costing.components import (
    c1_processing,
    c2_admin_leave,
    c3_backfill,
    c4_appeals,
    c5_turnover,
)
from wri_engine.schema import AppealForum
from wri_engine.sectors.base import SectorPack

PACK = SectorPack(
    id="public_sector",
    label="Public sector",
    description=(
        "A government employer. Posts with a minimum staffing requirement are covered by a "
        "peer on overtime; challenges run through grievance steps, arbitration, a civil "
        "service board or the courts; replacing a sworn employee means an academy."
    ),
    components=(c1_processing, c2_admin_leave, c3_backfill, c4_appeals, c5_turnover),
    benefits_assumption_by_group={
        "sworn_public_safety": "benefits_multiplier_sworn",
        "civilian": "benefits_multiplier_civilian",
    },
    default_benefits_group="civilian",
    turnover_profiles=(
        "sworn_deputy",
        "corrections",
        "dispatch",
        "fire_ems",
        "civilian_skilled",
        "civilian_standard",
    ),
    standard_turnover_profile="civilian_standard",
    challenge_forums=(
        AppealForum.GRIEVANCE_STEP,
        AppealForum.ARBITRATION,
        AppealForum.CIVIL_SERVICE_BOARD,
        AppealForum.COURT,
    ),
)
