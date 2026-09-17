"""The cost engine: runs every component over every action and collects the line items.

The engine itself does no arithmetic. It decides *which* actions to cost, calls the five
components in order, and assembles the result into one auditable run. All the money is made
in `components/`.

Three guarantees the engine enforces:

1. **Nothing is costed twice.** Each component owns its slice; the engine never adds a
   number of its own.
2. **Blocked records are excluded, and counted.** An action the adapter flagged as blocking
   contributes nothing and appears in `excluded_action_ids`.
3. **Every assumption referenced exists.** The registry is checked against the full set of
   ids the engine could request before a single line item is produced.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from wri_engine.costing.assumptions import AssumptionSet, Mode, default_assumptions
from wri_engine.costing.components import (
    c1_processing,
    c2_admin_leave,
    c3_backfill,
    c4_appeals,
    c5_turnover,
)
from wri_engine.costing.context import CostContext
from wri_engine.orgconfig import OrgConfig, default_org_config
from wri_engine.schema import (
    ActionType,
    CanonicalDataset,
    CostComponent,
    CostLineItem,
    DisciplineAction,
)

COMPONENTS = (c1_processing, c2_admin_leave, c3_backfill, c4_appeals, c5_turnover)

_TURNOVER_PROFILES = (
    "sworn_deputy", "corrections", "dispatch", "fire_ems",
    "civilian_skilled", "civilian_standard",
)
_C5_KEYS = (
    "advertising_cost", "hr_recruiter_hours", "testing_cost", "panel_size", "panel_hours",
    "background_investigation_cost", "polygraph_cost", "psych_eval_cost", "medical_exam_cost",
    "drug_screen_cost", "orientation_hours", "academy_weeks", "academy_tuition",
    "field_training_weeks", "trainer_differential_pct", "ramp_weeks", "ramp_loss_factor",
    "equipment_uniform_cost", "washout_rate", "vacancy_productivity_loss_factor",
    "expected_vacancy_days",
)


def expected_assumption_ids(org: OrgConfig) -> list[str]:
    """Every assumption id the engine can ask for, given this organization.

    Used as a preflight check so a missing coefficient fails at load time with a list of
    names, rather than halfway through a run with a KeyError.
    """
    ids = [
        "benefits_multiplier_civilian",
        "benefits_multiplier_sworn",
        "c1_reference_annual_hours",
        "c1_hr_reference_annual_salary",
        "c3_ot_premium_multiplier",
        "c3_ot_burden_multiplier",
        "c3_unpaid_suspension_burden_multiplier",
        "c4_outside_counsel_hourly_rate",
        "c4_arbitration_flat_cost",
        "c4_back_pay_interest_annual_rate",
    ]
    for level in org.raw["taxonomies"]["deciding_official_levels"]:
        ids.append(f"c1_deciding_annual_salary_{level}")
    for action_key in ("counseling", "reprimand", "suspension", "demotion", "removal"):
        for actor in ("supervisor", "hr", "deciding"):
            ids.append(f"c1_hours_{action_key}_{actor}")
    for investigation in org.raw["taxonomies"]["investigation_types"]:
        ids.append(f"c1_investigation_hours_{investigation}")
    for forum in org.appeal_forums:
        ids.append(f"c4_hr_hours_{forum}")
        ids.append(f"c4_filing_window_days_{forum}")
    for profile in _TURNOVER_PROFILES:
        for key in _C5_KEYS:
            ids.append(f"c5_{key}_{profile}")
    return sorted(set(ids))


@dataclass(frozen=True)
class ActionCost:
    """One action's costs, with the dimensions every rollup slices by."""

    action_id: str
    employee_id: str
    line_items: list[CostLineItem]
    dimensions: dict[str, str | int | bool]

    @property
    def gross(self) -> Decimal:
        return sum(
            (i.amount for i in self.line_items if i.component != CostComponent.C3_OFFSET),
            Decimal("0"),
        )

    @property
    def offset(self) -> Decimal:
        return sum(
            (i.amount for i in self.line_items if i.component == CostComponent.C3_OFFSET),
            Decimal("0"),
        )

    @property
    def net(self) -> Decimal:
        return self.gross + self.offset

    @property
    def cost_incomplete(self) -> bool:
        return any(i.cost_incomplete for i in self.line_items)

    def by_component(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = {}
        for item in self.line_items:
            out[str(item.component)] = out.get(str(item.component), Decimal("0")) + item.amount
        return out


@dataclass
class CostRun:
    """Everything one costing pass produced, plus what it was run with."""

    action_costs: list[ActionCost] = field(default_factory=list)
    excluded_action_ids: set[str] = field(default_factory=set)
    mode: Mode = "base"
    overrides: dict = field(default_factory=dict)
    as_of: date | None = None

    @property
    def line_items(self) -> list[CostLineItem]:
        return [item for ac in self.action_costs for item in ac.line_items]

    @property
    def gross(self) -> Decimal:
        return sum((ac.gross for ac in self.action_costs), Decimal("0"))

    @property
    def offset(self) -> Decimal:
        return sum((ac.offset for ac in self.action_costs), Decimal("0"))

    @property
    def net(self) -> Decimal:
        return self.gross + self.offset

    @property
    def incomplete_count(self) -> int:
        return sum(1 for ac in self.action_costs if ac.cost_incomplete)

    def by_action(self) -> dict[str, ActionCost]:
        return {ac.action_id: ac for ac in self.action_costs}


def _dimensions(action: DisciplineAction, ctx: CostContext) -> dict:
    employee = ctx.employee_for(action)
    category = ctx.org.misconduct_categories.get(action.misconduct_category)
    role = ctx.org.role_families.get(employee.role_family) if employee else None
    return {
        "role_family": employee.role_family if employee else "unknown",
        "role_family_label": role.name if role else "Unknown",
        "department": employee.department if employee else "Unknown",
        "department_label": employee.department if employee else "Unknown",
        "misconduct_category": action.misconduct_category,
        "misconduct_category_label": category.label if category else action.misconduct_category,
        "action_type": str(action.action_type),
        "action_type_label": str(action.action_type).replace("_", " ").title(),
        "work_location": employee.work_location if employee else "Unknown",
        "bargaining_unit": employee.bargaining_unit if employee else "Unknown",
        "is_sworn": bool(employee.is_sworn) if employee else False,
        "year": action.decision_date.year,
        "employee_id": action.employee_id,
        "decision_date": action.decision_date.isoformat(),
    }


def run_costing(
    data: CanonicalDataset,
    *,
    as_of: date,
    org: OrgConfig | None = None,
    assumptions: AssumptionSet | None = None,
    mode: Mode = "base",
    overrides: dict | None = None,
    excluded_action_ids: Iterable[str] = (),
) -> CostRun:
    """Cost every action in `data` that is not excluded."""
    org = org or default_org_config()
    assumptions = assumptions or default_assumptions(mode)
    if assumptions.mode != mode:
        assumptions = assumptions.with_mode(mode)
    if overrides:
        assumptions = assumptions.with_overrides(overrides)
    assumptions.require_all(expected_assumption_ids(org))

    excluded = set(excluded_action_ids)
    ctx = CostContext.build(data, org, assumptions, as_of)

    run = CostRun(
        excluded_action_ids=excluded,
        mode=mode,
        overrides=dict(overrides or {}),
        as_of=as_of,
    )
    for action in data.actions:
        if action.action_id in excluded:
            continue
        if ctx.employee_for(action) is None:
            continue
        items: list[CostLineItem] = []
        for component in COMPONENTS:
            items.extend(component.compute(action, ctx))
        run.action_costs.append(
            ActionCost(
                action_id=action.action_id,
                employee_id=action.employee_id,
                line_items=items,
                dimensions=_dimensions(action, ctx),
            )
        )
    return run


def cost_one(
    action_id: str,
    data: CanonicalDataset,
    *,
    as_of: date,
    org: OrgConfig | None = None,
    assumptions: AssumptionSet | None = None,
    mode: Mode = "base",
    overrides: dict | None = None,
) -> ActionCost | None:
    """Cost a single action. Used by the drill-down endpoint and by the golden tests."""
    run = run_costing(
        data, as_of=as_of, org=org, assumptions=assumptions, mode=mode, overrides=overrides
    )
    return run.by_action().get(action_id)


__all__ = [
    "ActionCost",
    "ActionType",
    "CostRun",
    "cost_one",
    "expected_assumption_ids",
    "run_costing",
]
