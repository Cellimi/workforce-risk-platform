"""Write a canonical dataset out in the WRI intake template layout.

Used to prove the template adapter costs the same data to the same cent as the county
adapter: load Harlow County through the county adapter, write it out as template files, load
those through the template adapter, and compare.
"""

from __future__ import annotations

import csv
from pathlib import Path

from wri_engine.adapters.template import NON_REPRESENTED, SCHEDULES, TABLES
from wri_engine.orgconfig import OrgConfig
from wri_engine.schema import CanonicalDataset

_SCHEDULE_NAME = {v: k for k, v in SCHEDULES.items()}


def _yn(value: bool) -> str:
    return "Y" if value else "N"


def _d(value) -> str:
    return value.isoformat() if value else ""


def write_template(data: CanonicalDataset, org: OrgConfig, directory: Path) -> Path:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    non_represented = org.bargaining_units.get("non_represented", NON_REPRESENTED)

    def emit(table: str, rows: list[dict]) -> None:
        spec = TABLES[table]
        with open(directory / spec.file, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=spec.field_names)
            writer.writeheader()
            writer.writerows(rows)

    employees = []
    for e in data.employees:
        role = org.role_families[e.role_family]
        employees.append(
            {
                "employee_id": e.employee_id,
                "department": e.department,
                "job_family": role.name if _unique_name(org, role) else e.role_family,
                "job_title": e.job_title,
                "job_class_code": e.job_class_code,
                "pay_grade": e.pay_grade,
                "pay_step": e.pay_step,
                "annual_base_salary": e.annual_base_salary,
                "entry_annual_salary": org.salary_for(role.pay_grade, 1),
                "flsa_status": str(e.flsa_status),
                "bargaining_unit": ""
                if e.bargaining_unit == non_represented
                else e.bargaining_unit,
                "is_sworn": _yn(e.is_sworn),
                "minimum_staffing_role": _yn(e.minimum_staffing_role),
                "work_schedule": _SCHEDULE_NAME[e.schedule_id],
                "standard_shift_hours": e.standard_shift_hours,
                "turnover_profile": e.turnover_profile,
                "is_supervisor": _yn(role.is_supervisory),
                "work_location": e.work_location,
                "supervisor_id": e.supervisor_id or "",
                "hire_date": _d(e.hire_date),
                "separation_date": _d(e.separation_date),
                "separation_reason": str(e.separation_reason) if e.separation_reason else "",
            }
        )
    emit("employees", employees)
    emit(
        "actions",
        [
            {
                "action_id": a.action_id,
                "employee_id": a.employee_id,
                "incident_date": _d(a.incident_date),
                "proposal_date": _d(a.proposal_date),
                "decision_date": _d(a.decision_date),
                "misconduct_category": a.misconduct_category,
                "misconduct_subtype": a.misconduct_subtype,
                "action_type": str(a.action_type),
                "suspension_days": a.suspension_days,
                "suspension_start_date": _d(a.suspension_start_date),
                "was_investigated": _yn(a.was_investigated),
                "investigation_type": str(a.investigation_type) if a.investigation_type else "",
                "deciding_official_level": str(a.deciding_official_level),
                "deciding_official_id": a.deciding_official_id or "",
                "position_abolished": _yn(a.position_abolished),
            }
            for a in data.actions
        ],
    )
    emit(
        "admin_leave",
        [
            {
                "leave_id": x.leave_id,
                "employee_id": x.employee_id,
                "action_id": x.action_id or "",
                "start_date": _d(x.start_date),
                "end_date": _d(x.end_date),
                "paid": _yn(x.paid),
            }
            for x in data.leave_periods
        ],
    )
    emit(
        "appeals",
        [
            {
                "appeal_id": x.appeal_id,
                "action_id": x.action_id,
                "forum": str(x.forum),
                "filed_date": _d(x.filed_date),
                "resolution_date": _d(x.resolution_date),
                "outcome": str(x.outcome),
                "back_pay_awarded": x.back_pay_awarded,
                "settlement_amount": x.settlement_amount,
                "outside_counsel_hours": x.outside_counsel_hours,
            }
            for x in data.appeals
        ],
    )
    emit(
        "separations",
        [
            {
                "employee_id": x.employee_id,
                "separation_date": _d(x.separation_date),
                "separation_reason": str(x.separation_reason),
                "linked_action_id": x.linked_action_id or "",
                "position_refilled": _yn(x.position_refilled),
                "refill_date": _d(x.refill_date),
                "position_abolished": _yn(x.position_abolished),
            }
            for x in data.separations
        ],
    )
    return directory


def _unique_name(org: OrgConfig, role) -> bool:
    return sum(1 for r in org.role_families.values() if r.name == role.name) == 1
