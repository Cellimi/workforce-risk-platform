"""Validation rules run against a loaded canonical dataset.

A rule is a small function: given the dataset and the org config, yield `Issue`s. Adding a
rule means adding a function and a fixture that fails it -- see `tests/test_adapter.py`.

Blocking vs warning
-------------------
`blocking` means the record's cost cannot be trusted, so its action is excluded from every
total and counted as an exclusion. `warning` means the record looks odd but still costs
correctly.
"""

from __future__ import annotations

from collections.abc import Iterator

from wri_engine.adapters.base import Issue, Severity
from wri_engine.orgconfig import OrgConfig
from wri_engine.schema import AppealOutcome, CanonicalDataset

Rule = "Callable[[CanonicalDataset, OrgConfig], Iterator[Issue]]"


def orphan_actions(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """An action whose employee is not in the HR master cannot be costed at all."""
    known = set(data.employees_by_id())
    for action in data.actions:
        if action.employee_id not in known:
            yield Issue(
                rule_id="orphan_action",
                severity=Severity.BLOCKING,
                entity="action",
                entity_id=action.action_id,
                field="employee_id",
                message=(
                    f"action {action.action_id} references employee "
                    f"{action.employee_id!r}, which is not in the employee master"
                ),
                blocks_action_ids=(action.action_id,),
            )


def suspension_days_fit_action_type(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """Suspension days must fall inside the range the action type allows."""
    for action in data.actions:
        low, high = action.action_type.expected_suspension_days
        if not low <= action.suspension_days <= high:
            yield Issue(
                rule_id="suspension_days_mismatch",
                severity=Severity.BLOCKING,
                entity="action",
                entity_id=action.action_id,
                field="suspension_days",
                message=(
                    f"action type {action.action_type} allows {low}-{high} suspension days "
                    f"but the record shows {action.suspension_days}"
                ),
                blocks_action_ids=(action.action_id,),
            )


def decision_after_incident(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """A decision cannot predate the incident it decides."""
    for action in data.actions:
        if action.decision_date < action.incident_date:
            yield Issue(
                rule_id="decision_before_incident",
                severity=Severity.BLOCKING,
                entity="action",
                entity_id=action.action_id,
                field="decision_date",
                message=(
                    f"decision date {action.decision_date} precedes incident date "
                    f"{action.incident_date}"
                ),
                blocks_action_ids=(action.action_id,),
            )
        elif action.proposal_date and action.proposal_date > action.decision_date:
            yield Issue(
                rule_id="proposal_after_decision",
                severity=Severity.WARNING,
                entity="action",
                entity_id=action.action_id,
                field="proposal_date",
                message=(
                    f"proposal date {action.proposal_date} is later than the decision date "
                    f"{action.decision_date}"
                ),
            )


def leave_within_employment(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """Administrative leave cannot run past the employee's separation date."""
    employees = data.employees_by_id()
    for leave in data.leave_periods:
        employee = employees.get(leave.employee_id)
        if employee is None or employee.separation_date is None:
            continue
        if leave.end_date > employee.separation_date:
            yield Issue(
                rule_id="leave_after_separation",
                severity=Severity.BLOCKING,
                entity="admin_leave",
                entity_id=leave.leave_id,
                field="end_date",
                message=(
                    f"leave {leave.leave_id} ends {leave.end_date}, after employee "
                    f"{leave.employee_id} separated on {employee.separation_date}"
                ),
                blocks_action_ids=(leave.action_id,) if leave.action_id else (),
            )


def sworn_only_categories(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """A sworn-only misconduct category cannot be recorded against a non-sworn employee."""
    employees = data.employees_by_id()
    for action in data.actions:
        category = org.misconduct_categories.get(action.misconduct_category)
        employee = employees.get(action.employee_id)
        if category is None or employee is None:
            continue
        if category.sworn_only and not employee.is_sworn:
            yield Issue(
                rule_id="sworn_only_category_misapplied",
                severity=Severity.BLOCKING,
                entity="action",
                entity_id=action.action_id,
                field="misconduct_category",
                message=(
                    f"category {category.label!r} is sworn-only but employee "
                    f"{employee.employee_id} in role {employee.role_family} is not sworn"
                ),
                blocks_action_ids=(action.action_id,),
            )


def known_misconduct_category(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """Categories and subtypes must exist in the configured taxonomy."""
    for action in data.actions:
        category = org.misconduct_categories.get(action.misconduct_category)
        if category is None:
            yield Issue(
                rule_id="unknown_misconduct_category",
                severity=Severity.BLOCKING,
                entity="action",
                entity_id=action.action_id,
                field="misconduct_category",
                message=(
                    f"misconduct category {action.misconduct_category!r} is not in the "
                    f"configured taxonomy"
                ),
                blocks_action_ids=(action.action_id,),
            )
        elif action.misconduct_subtype and action.misconduct_subtype not in category.subtypes:
            yield Issue(
                rule_id="unknown_misconduct_subtype",
                severity=Severity.WARNING,
                entity="action",
                entity_id=action.action_id,
                field="misconduct_subtype",
                message=(
                    f"subtype {action.misconduct_subtype!r} is not listed under "
                    f"{category.label!r}"
                ),
            )


def orphan_appeals(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """An appeal must attach to an action that exists."""
    action_ids = {a.action_id for a in data.actions}
    for appeal in data.appeals:
        if appeal.action_id not in action_ids:
            yield Issue(
                rule_id="orphan_appeal",
                severity=Severity.BLOCKING,
                entity="appeal",
                entity_id=appeal.appeal_id,
                field="action_id",
                message=(
                    f"appeal {appeal.appeal_id} references action {appeal.action_id!r}, "
                    f"which does not exist"
                ),
            )


def orphan_separations(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """A separation must attach to an employee that exists, and its linked action too."""
    employees = set(data.employees_by_id())
    action_ids = {a.action_id for a in data.actions}
    for sep in data.separations:
        if sep.employee_id not in employees:
            yield Issue(
                rule_id="orphan_separation",
                severity=Severity.BLOCKING,
                entity="separation",
                entity_id=sep.employee_id,
                field="employee_id",
                message=f"separation references employee {sep.employee_id!r}, which is unknown",
                blocks_action_ids=(sep.linked_action_id,) if sep.linked_action_id else (),
            )
        elif sep.linked_action_id and sep.linked_action_id not in action_ids:
            yield Issue(
                rule_id="separation_unknown_action",
                severity=Severity.BLOCKING,
                entity="separation",
                entity_id=sep.employee_id,
                field="linked_action_id",
                message=(
                    f"separation for {sep.employee_id} links to action "
                    f"{sep.linked_action_id!r}, which does not exist"
                ),
            )


def suspension_start_after_decision(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """A suspension normally begins on or after the decision that imposed it."""
    for action in data.actions:
        if action.suspension_start_date and action.suspension_start_date < action.decision_date:
            yield Issue(
                rule_id="suspension_starts_before_decision",
                severity=Severity.WARNING,
                entity="action",
                entity_id=action.action_id,
                field="suspension_start_date",
                message=(
                    f"suspension starts {action.suspension_start_date}, before the decision "
                    f"date {action.decision_date}"
                ),
            )


def suspension_days_present(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """A suspension action with zero days recorded has nothing to cost."""
    for action in data.actions:
        if action.action_type.is_suspension and action.suspension_days == 0:
            yield Issue(
                rule_id="suspension_without_days",
                severity=Severity.BLOCKING,
                entity="action",
                entity_id=action.action_id,
                field="suspension_days",
                message=f"action {action.action_id} is a suspension but records zero days",
                blocks_action_ids=(action.action_id,),
            )


def resolved_appeal_has_date(data: CanonicalDataset, org: OrgConfig) -> Iterator[Issue]:
    """A closed appeal should carry a resolution date."""
    for appeal in data.appeals:
        if appeal.outcome != AppealOutcome.PENDING and appeal.resolution_date is None:
            yield Issue(
                rule_id="resolved_appeal_missing_date",
                severity=Severity.WARNING,
                entity="appeal",
                entity_id=appeal.appeal_id,
                field="resolution_date",
                message=(
                    f"appeal {appeal.appeal_id} has outcome {appeal.outcome} but no "
                    f"resolution date; interest on back pay cannot be computed"
                ),
            )


#: Every rule, in the order the report presents them.
RULES = [
    orphan_actions,
    known_misconduct_category,
    suspension_days_fit_action_type,
    suspension_days_present,
    decision_after_incident,
    suspension_start_after_decision,
    leave_within_employment,
    sworn_only_categories,
    orphan_appeals,
    orphan_separations,
    resolved_appeal_has_date,
]


def run_rules(data: CanonicalDataset, org: OrgConfig) -> list[Issue]:
    issues: list[Issue] = []
    for rule in RULES:
        issues.extend(rule(data, org))
    return issues
