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
    GRIEVANCE_STEP = "grievance_step"
    ARBITRATION = "arbitration"
    CIVIL_SERVICE_BOARD = "civil_service_board"
    COURT = "court"


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
    """The five cost components, plus the one negative-by-design offset line."""

    C1_PROCESSING = "C1"
    C2_ADMIN_LEAVE = "C2"
    C3_BACKFILL = "C3"
    C3_OFFSET = "C3-offset"
    C4_APPEALS = "C4"
    C5_TURNOVER = "C5"


COMPONENT_LABELS: dict[CostComponent, str] = {
    CostComponent.C1_PROCESSING: "Processing labor",
    CostComponent.C2_ADMIN_LEAVE: "Paid administrative leave",
    CostComponent.C3_BACKFILL: "Backfill overtime",
    CostComponent.C3_OFFSET: "Unpaid suspension salary savings",
    CostComponent.C4_APPEALS: "Appeals & grievances",
    CostComponent.C5_TURNOVER: "Removal turnover",
}
