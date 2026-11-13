"""Structural enumerations for the canonical schema.

These are the values the *engine branches on*: action type shapes the cost calculation,
FLSA status shapes overtime, forum shapes appeal handling. They are declared in code so
the type checker can see them, and `tests/test_config_consistency.py` asserts they stay in
step with `config/org_county.yaml`.

Misconduct categories and subtypes are deliberately NOT enums. They are a taxonomy that
customers will extend, so they live only in config and are validated at load time.
"""

from __future__ import annotations

from enum import StrEnum


class FLSAStatus(StrEnum):
    EXEMPT = "exempt"
    NON_EXEMPT = "non_exempt"
    SEVEN_K = "7k"


class ActionType(StrEnum):
    DOCUMENTED_COUNSELING = "documented_counseling"
    WRITTEN_REPRIMAND = "written_reprimand"
    SUSPENSION_1_3 = "suspension_1_3"
    SUSPENSION_4_14 = "suspension_4_14"
    SUSPENSION_15_PLUS = "suspension_15_plus"
    DEMOTION = "demotion"
    REMOVAL = "removal"

    @property
    def is_suspension(self) -> bool:
        return self in _SUSPENSION_TYPES

    @property
    def expected_suspension_days(self) -> tuple[int, int]:
        """Inclusive (min, max) suspension days this action type may carry."""
        return _SUSPENSION_DAY_RANGE[self]


_SUSPENSION_TYPES = frozenset(
    {ActionType.SUSPENSION_1_3, ActionType.SUSPENSION_4_14, ActionType.SUSPENSION_15_PLUS}
)

_SUSPENSION_DAY_RANGE: dict[ActionType, tuple[int, int]] = {
    ActionType.DOCUMENTED_COUNSELING: (0, 0),
    ActionType.WRITTEN_REPRIMAND: (0, 0),
    ActionType.SUSPENSION_1_3: (1, 3),
    ActionType.SUSPENSION_4_14: (4, 14),
    ActionType.SUSPENSION_15_PLUS: (15, 45),
    ActionType.DEMOTION: (0, 0),
    ActionType.REMOVAL: (0, 0),
}


class CoverageMethod(StrEnum):
    """How an employer covers a post while the employee is away or the seat is empty.

    `OVERTIME` is what a minimum-staffing public post does: a peer works the shift. A
    private employer more often brings in an agency temp, which is billed at a marked-up
    rate rather than at a peer's overtime rate. `NONE` means the work simply waits.
    """

    OVERTIME = "overtime"
    TEMP_AGENCY = "temp_agency"
    NONE = "none"


class OccupationGroup(StrEnum):
    """The five broad occupational groups the BLS publishes compensation costs for.

    A private employer's benefits cost per wage dollar differs a good deal by the kind of
    work: a service worker's wage carries far less in benefits than a production worker's.
    These are the groups in the Employer Costs for Employee Compensation series, so a
    client's job families can be mapped to a multiplier with a published source behind it.
    """

    MANAGEMENT_PROFESSIONAL = "management_professional"
    SALES_OFFICE = "sales_office"
    SERVICE = "service"
    CONSTRUCTION_MAINTENANCE = "construction_maintenance"
    PRODUCTION_TRANSPORTATION = "production_transportation"


class InvestigationType(StrEnum):
    SUPERVISORY = "supervisory"
    HR = "hr"
    INTERNAL_AFFAIRS = "internal_affairs"


class DecidingOfficialLevel(StrEnum):
    SUPERVISOR = "supervisor"
    DIVISION_HEAD = "division_head"
    DEPARTMENT_HEAD = "department_head"
    COUNTY_ADMINISTRATOR = "county_administrator"


class AppealForum(StrEnum):
    """Where a disciplined employee can challenge the action.

    The first four are the public-sector routes. The last three are the ones a private
    employer without a union contract faces instead: a charge filed with an enforcement
    agency, a lawyer's demand letter before any filing, and mediation. `COURT` covers
    litigation defence in both sectors.

    One vocabulary, not two. A public employer can face an agency charge as readily as a
    private one; what differs is which routes the sector's data actually contains.
    """

    GRIEVANCE_STEP = "grievance_step"
    ARBITRATION = "arbitration"
    CIVIL_SERVICE_BOARD = "civil_service_board"
    COURT = "court"
    AGENCY_CHARGE = "agency_charge"
    DEMAND_LETTER = "demand_letter"
    MEDIATION = "mediation"


class AppealOutcome(StrEnum):
    SUSTAINED = "sustained"
    MITIGATED = "mitigated"
    OVERTURNED = "overturned"
    SETTLED = "settled"
    PENDING = "pending"


class SeparationReason(StrEnum):
    VOLUNTARY_RESIGNATION = "voluntary_resignation"
    RESIGNATION_IN_LIEU_OF_REMOVAL = "resignation_in_lieu_of_removal"
    REMOVAL_FOR_CAUSE = "removal_for_cause"
    RETIREMENT = "retirement"
    LAYOFF = "layoff"
    END_OF_PROBATION = "end_of_probation"

    @property
    def is_discipline_driven(self) -> bool:
        return self in {
            SeparationReason.REMOVAL_FOR_CAUSE,
            SeparationReason.RESIGNATION_IN_LIEU_OF_REMOVAL,
        }


class CostComponent(StrEnum):
    """The cost components, plus the negative-by-design offset lines.

    C1-C5 are what any employer spends on a discipline action. C6 and C7 only ever carry a
    figure when the record shows the event happened, which in practice means a private
    employer: a separation agreement, an unemployment claim, or a liability policy. Nothing
    here is sector-specific by construction -- the data decides.
    """

    C1_PROCESSING = "C1"
    C2_ADMIN_LEAVE = "C2"
    C3_BACKFILL = "C3"
    C3_OFFSET = "C3-offset"
    C4_APPEALS = "C4"
    C5_TURNOVER = "C5"
    C5_OFFSET = "C5-offset"
    C6_SEPARATION = "C6"
    C7_INSURANCE = "C7"
    C7_OFFSET = "C7-offset"


COMPONENT_LABELS: dict[CostComponent, str] = {
    CostComponent.C1_PROCESSING: "Processing labor",
    CostComponent.C2_ADMIN_LEAVE: "Paid administrative leave",
    CostComponent.C3_BACKFILL: "Backfill overtime",
    CostComponent.C3_OFFSET: "Unpaid suspension salary savings",
    CostComponent.C4_APPEALS: "Appeals & grievances",
    CostComponent.C5_TURNOVER: "Removal turnover",
    CostComponent.C5_OFFSET: "Vacancy salary savings",
    CostComponent.C6_SEPARATION: "Separation agreements & unemployment",
    CostComponent.C7_INSURANCE: "Employment practices liability",
    CostComponent.C7_OFFSET: "Insurer-paid amounts",
}

#: The components that record money the county did NOT spend. They are the only ones
#: allowed to be negative, they are excluded from gross, and they are what `offset` sums.
#:
#: Defined once, here. Anything that needs to know "is this an offset?" asks this set --
#: never `== C3_OFFSET`, which was the bug that made adding C5-offset a six-file change.
OFFSET_COMPONENTS: frozenset[CostComponent] = frozenset(
    {CostComponent.C3_OFFSET, CostComponent.C5_OFFSET, CostComponent.C7_OFFSET}
)
