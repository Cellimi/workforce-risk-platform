"""Canonical data models.

Every source system (a county HR CSV export today, NFC or Oracle HCM later) is translated
into these models by an adapter. The cost engine reads nothing else.

Money is `Decimal`. Dates are timezone-naive `datetime.date`.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from wri_engine.schema.enums import (
    ActionType,
    AppealForum,
    AppealOutcome,
    CostComponent,
    DecidingOfficialLevel,
    FLSAStatus,
    InvestigationType,
    SeparationReason,
)

_DEFAULT_ANNUAL_PAID_HOURS = 2080


class CanonicalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class Employee(CanonicalModel):
    employee_id: str
    department: str
    role_family: str
    job_title: str
    job_class_code: str
    pay_grade: str
    pay_step: int = Field(ge=1, le=20)
    annual_base_salary: Decimal = Field(gt=0)
    hourly_base_rate: Decimal = Field(gt=0)
    flsa_status: FLSAStatus
    bargaining_unit: str
    is_sworn: bool
    minimum_staffing_role: bool
    standard_shift_hours: Decimal = Field(gt=0, le=24)
    annual_paid_hours: int = Field(default=_DEFAULT_ANNUAL_PAID_HOURS, gt=0)
    schedule_id: str = "civilian_5x8"
    benefits_group: str = "civilian"
    turnover_profile: str = "civilian_standard"
    work_location: str
    supervisor_id: str | None = None
    hire_date: date
    separation_date: date | None = None
    separation_reason: SeparationReason | None = None

    @model_validator(mode="after")
    def _separation_consistency(self) -> Employee:
        if self.separation_date is not None and self.separation_date < self.hire_date:
            raise ValueError("separation_date precedes hire_date")
        return self

    def is_active_on(self, when: date) -> bool:
        if when < self.hire_date:
            return False
        return self.separation_date is None or when <= self.separation_date


class DisciplineAction(CanonicalModel):
    action_id: str
    employee_id: str
    incident_date: date
    proposal_date: date | None = None
    decision_date: date
    misconduct_category: str
    misconduct_subtype: str
    action_type: ActionType
    suspension_days: int = Field(default=0, ge=0)
    suspension_start_date: date | None = None
    was_investigated: bool = False
    investigation_type: InvestigationType | None = None
    deciding_official_level: DecidingOfficialLevel
    deciding_official_id: str | None = None
    position_abolished: bool = False

    @model_validator(mode="after")
    def _investigation_consistency(self) -> DisciplineAction:
        if self.was_investigated and self.investigation_type is None:
            raise ValueError("was_investigated is true but investigation_type is missing")
        return self


class AdminLeavePeriod(CanonicalModel):
    leave_id: str
    employee_id: str
    action_id: str | None = None
    start_date: date
    end_date: date
    paid: bool = True

    @model_validator(mode="after")
    def _dates_ordered(self) -> AdminLeavePeriod:
        if self.end_date < self.start_date:
            raise ValueError("end_date precedes start_date")
        return self

    @property
    def calendar_days(self) -> int:
        return (self.end_date - self.start_date).days + 1


class AppealOrGrievance(CanonicalModel):
    appeal_id: str
    action_id: str
    forum: AppealForum
    filed_date: date
    resolution_date: date | None = None
    outcome: AppealOutcome
    back_pay_awarded: Decimal = Decimal("0")
    settlement_amount: Decimal = Decimal("0")
    outside_counsel_hours: Decimal = Decimal("0")

    @model_validator(mode="after")
    def _pending_has_no_resolution(self) -> AppealOrGrievance:
        if self.outcome == AppealOutcome.PENDING and self.resolution_date is not None:
            raise ValueError("a pending appeal cannot carry a resolution_date")
        return self

    @property
    def is_open(self) -> bool:
        return self.outcome == AppealOutcome.PENDING or self.resolution_date is None


class Separation(CanonicalModel):
    """Modeled explicitly (though derivable from Employee) so C5 has one clear input."""

    employee_id: str
    separation_date: date
    separation_reason: SeparationReason
    linked_action_id: str | None = None
    position_refilled: bool = False
    refill_date: date | None = None
    position_abolished: bool = False

    @model_validator(mode="after")
    def _refill_consistency(self) -> Separation:
        if self.position_refilled and self.refill_date is None:
            raise ValueError("position_refilled is true but refill_date is missing")
        if self.position_abolished and self.position_refilled:
            raise ValueError("a position cannot be both abolished and refilled")
        return self


class CostLineItem(BaseModel):
    """One explainable dollar amount.

    `formula` is written for a person to read; `inputs` carries the actual values used;
    `assumption_ids` lists every assumption consumed. Together they make the number
    reproducible by hand.
    """

    model_config = ConfigDict(extra="forbid")

    action_id: str
    employee_id: str
    component: CostComponent
    subcomponent: str
    amount: Decimal
    formula: str
    inputs: dict = Field(default_factory=dict)
    assumption_ids: list[str] = Field(default_factory=list)
    cost_incomplete: bool = False

    @field_validator("amount")
    @classmethod
    def _quantize(cls, v: Decimal) -> Decimal:
        return Decimal(v)

    @model_validator(mode="after")
    def _sign_convention(self) -> CostLineItem:
        if self.component == CostComponent.C3_OFFSET:
            if self.amount > 0:
                raise ValueError("C3-offset line items must be zero or negative")
        elif self.amount < 0:
            raise ValueError(f"{self.component} line items must not be negative")
        return self


class CanonicalDataset(BaseModel):
    """Everything the engine needs, already translated out of the source format."""

    model_config = ConfigDict(extra="forbid")

    employees: list[Employee] = Field(default_factory=list)
    actions: list[DisciplineAction] = Field(default_factory=list)
    leave_periods: list[AdminLeavePeriod] = Field(default_factory=list)
    appeals: list[AppealOrGrievance] = Field(default_factory=list)
    separations: list[Separation] = Field(default_factory=list)

    def employees_by_id(self) -> dict[str, Employee]:
        return {e.employee_id: e for e in self.employees}

    def leave_by_action(self) -> dict[str, list[AdminLeavePeriod]]:
        out: dict[str, list[AdminLeavePeriod]] = {}
        for leave in self.leave_periods:
            if leave.action_id:
                out.setdefault(leave.action_id, []).append(leave)
        return out

    def appeals_by_action(self) -> dict[str, list[AppealOrGrievance]]:
        out: dict[str, list[AppealOrGrievance]] = {}
        for appeal in self.appeals:
            out.setdefault(appeal.action_id, []).append(appeal)
        return out

    def separations_by_action(self) -> dict[str, Separation]:
        return {s.linked_action_id: s for s in self.separations if s.linked_action_id}
