"""Adapter: Harlow County HR flat-file export -> canonical schema.

The whole translation contract lives in `config/mapping_county_hr_csv.yaml`: file names,
column names, code values, date format and the job-class-to-role-family lookup. This module
holds only the mechanics of applying it.

Things the source export does NOT carry, which the adapter derives from `org_county.yaml`:

* `role_family` -- looked up from the job class code, the only reliable link in the export
* `schedule_id`, `annual_paid_hours` -- from the role family's schedule
* `hourly_base_rate` -- annual base salary / annual paid hours
* `benefits_group`, `turnover_profile` -- from the role family

Nothing raises on bad data. A row that cannot be translated is skipped and recorded as an
`Issue`, because a blocked record is a reportable outcome, not a crash.
"""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import yaml

from wri_engine.adapters.base import Issue, Severity, SourceAdapter
from wri_engine.adapters.validation import run_rules
from wri_engine.orgconfig import OrgConfig, default_org_config
from wri_engine.paths import CONFIG_DIR
from wri_engine.schema import (
    AdminLeavePeriod,
    AppealOrGrievance,
    CanonicalDataset,
    DisciplineAction,
    Employee,
    Separation,
)

DEFAULT_MAPPING = CONFIG_DIR / "mapping_county_hr_csv.yaml"


class CountyHrCsvAdapter(SourceAdapter):
    source_id = "county_hr_csv"

    def __init__(self, org: OrgConfig | None = None, mapping_path: Path | None = None):
        super().__init__()
        self.org = org or default_org_config()
        with open(mapping_path or DEFAULT_MAPPING) as fh:
            self.mapping = yaml.safe_load(fh)
        source = self.mapping["source"]
        self.date_format: str = source["date_format"]
        self._true = {v.upper() for v in source["boolean_true"]}
        self._false = {v.upper() for v in source["boolean_false"]}
        self.codes = self.mapping["codes"]
        self.class_to_role = self.mapping["job_class_to_role_family"]

    # -- scalar parsing ----------------------------------------------------
    def _date(self, raw: str | None) -> date_or_none:  # noqa: F821 - see alias below
        text = (raw or "").strip()
        if not text:
            return None
        return datetime.strptime(text, self.date_format).date()

    def _bool(self, raw: str | None) -> bool:
        text = (raw or "").strip().upper()
        if text in self._true:
            return True
        if text in self._false:
            return False
        raise ValueError(f"{raw!r} is not a recognized Y/N value")

    def _money(self, raw: str | None) -> Decimal:
        text = (raw or "").strip().replace(",", "").replace("$", "")
        if not text:
            raise ValueError("empty money value")
        return Decimal(text)

    def _code(self, group: str, raw: str | None) -> str:
        text = (raw or "").strip()
        try:
            return self.codes[group][text]
        except KeyError:
            raise ValueError(f"{text!r} is not a known {group} code") from None

    # -- file reading ------------------------------------------------------
    def _rows(self, directory: Path, key: str) -> list[dict]:
        path = directory / self.mapping["files"][key]
        if not path.exists():
            self._report.add(
                Issue(
                    rule_id="missing_source_file",
                    severity=Severity.BLOCKING,
                    entity="file",
                    entity_id=path.name,
                    message=f"expected source file {path.name} was not found in {directory}",
                )
            )
            return []
        with open(path, newline="") as fh:
            return list(csv.DictReader(fh))

    def _row_issue(self, rule_id: str, entity: str, entity_id: str, message: str,
                   field: str | None = None, blocks: tuple[str, ...] = ()) -> None:
        self._report.add(
            Issue(
                rule_id=rule_id,
                severity=Severity.BLOCKING,
                entity=entity,
                entity_id=entity_id,
                message=message,
                field=field,
                blocks_action_ids=blocks,
            )
        )

    # -- load --------------------------------------------------------------
    def load(self, path: Path) -> CanonicalDataset:
        path = Path(path)
        employees, employee_problems = self._load_employees(self._rows(path, "employees"))
        actions = self._load_actions(self._rows(path, "actions"))
        leave = self._load_leave(self._rows(path, "admin_leave"))
        appeals = self._load_appeals(self._rows(path, "appeals"))
        separations = self._load_separations(self._rows(path, "separations"))

        # An unusable employee row blocks every action belonging to that person. That link
        # can only be made once the actions have been read.
        for employee_id, (rule_id, message, field) in employee_problems.items():
            blocked = tuple(a.action_id for a in actions if a.employee_id == employee_id)
            self._row_issue(rule_id, "employee", employee_id, message, field, blocked)

        data = CanonicalDataset(
            employees=employees,
            actions=actions,
            leave_periods=leave,
            appeals=appeals,
            separations=separations,
        )
        # An employee we already reported as unusable would otherwise make every one of
        # their actions look like an orphan as well. Report the root cause once.
        for issue in run_rules(data, self.org):
            if issue.rule_id == "orphan_action":
                action = next(a for a in actions if a.action_id == issue.entity_id)
                if action.employee_id in employee_problems:
                    continue
            self._report.add(issue)
        return data

    def _load_employees(self, rows: list[dict]) -> tuple[list[Employee], dict]:
        out: list[Employee] = []
        problems: dict[str, tuple[str, str, str | None]] = {}
        for row in rows:
            employee_id = (row.get("EMP_NBR") or "").strip()
            class_code = (row.get("CLASS_CD") or "").strip()
            role_id = self.class_to_role.get(class_code)
            if role_id is None or role_id not in self.org.role_families:
                problems[employee_id] = (
                    "unmapped_job_class",
                    f"job class {class_code!r} does not map to a configured role family",
                    "job_class_code",
                )
                continue
            role = self.org.role_families[role_id]
            schedule = self.org.schedules[role.schedule_id]
            try:
                salary = self._money(row.get("ANNL_SAL"))
            except (InvalidOperation, ValueError):
                problems[employee_id] = (
                    "missing_salary",
                    f"employee {employee_id} has no usable annual base salary, so no "
                    f"hourly rate can be computed",
                    "annual_base_salary",
                )
                continue
            try:
                out.append(
                    Employee(
                        employee_id=employee_id,
                        department=self.org.departments[
                            self._code("department", row.get("DEPT_CD"))
                        ]["name"],
                        role_family=role_id,
                        job_title=(row.get("POSN_TITLE") or "").strip(),
                        job_class_code=class_code,
                        pay_grade=(row.get("GRADE") or "").strip(),
                        pay_step=int(row.get("STEP") or 1),
                        annual_base_salary=salary,
                        hourly_base_rate=salary / Decimal(schedule.annual_paid_hours),
                        flsa_status=self._code("flsa", row.get("FLSA_CD")),
                        bargaining_unit=self.org.bargaining_units[
                            self._code("bargaining_unit", row.get("BARG_CD"))
                        ],
                        is_sworn=self._bool(row.get("SWORN_IND")),
                        minimum_staffing_role=self._bool(row.get("MIN_STAFF_IND")),
                        standard_shift_hours=Decimal(
                            str(row.get("SHIFT_HRS") or schedule.shift_hours)
                        ),
                        annual_paid_hours=schedule.annual_paid_hours,
                        schedule_id=role.schedule_id,
                        benefits_group=role.benefits_group,
                        turnover_profile=role.turnover_profile,
                        work_location=(row.get("WORK_LOC") or "").strip(),
                        supervisor_id=(row.get("SUPV_EMP_NBR") or "").strip() or None,
                        hire_date=self._date(row.get("HIRE_DT")),
                        separation_date=self._date(row.get("TERM_DT")),
                        separation_reason=(
                            self._code("separation_reason", row.get("TERM_RSN_CD"))
                            if (row.get("TERM_RSN_CD") or "").strip()
                            else None
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001 - any bad row becomes a reported issue
                problems[employee_id] = ("unparsable_employee_row", str(exc), None)
        return out, problems

    def _load_actions(self, rows: list[dict]) -> list[DisciplineAction]:
        out: list[DisciplineAction] = []
        for row in rows:
            action_id = (row.get("ACTN_NBR") or "").strip()
            try:
                investigated = self._bool(row.get("INVEST_IND"))
                out.append(
                    DisciplineAction(
                        action_id=action_id,
                        employee_id=(row.get("EMP_NBR") or "").strip(),
                        incident_date=self._date(row.get("INCDT_DT")),
                        proposal_date=self._date(row.get("PROP_DT")),
                        decision_date=self._date(row.get("DECN_DT")),
                        misconduct_category=self._code("misconduct", row.get("MISCND_CD")),
                        misconduct_subtype=(row.get("MISCND_SUB") or "").strip(),
                        action_type=self._code("action_type", row.get("ACTN_CD")),
                        suspension_days=int(row.get("SUSP_DAYS") or 0),
                        suspension_start_date=self._date(row.get("SUSP_BEG_DT")),
                        was_investigated=investigated,
                        investigation_type=(
                            self._code("investigation", row.get("INVEST_TYP"))
                            if investigated
                            else None
                        ),
                        deciding_official_level=self._code(
                            "deciding_official_level", row.get("DECIDE_LVL")
                        ),
                        deciding_official_id=(row.get("DECIDE_EMP_NBR") or "").strip() or None,
                        position_abolished=self._bool(row.get("POSN_ABOLISH_IND")),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._row_issue(
                    "unparsable_action_row", "action", action_id, str(exc),
                    blocks=(action_id,) if action_id else (),
                )
        return out

    def _load_leave(self, rows: list[dict]) -> list[AdminLeavePeriod]:
        out: list[AdminLeavePeriod] = []
        for row in rows:
            leave_id = (row.get("LEAVE_NBR") or "").strip()
            try:
                out.append(
                    AdminLeavePeriod(
                        leave_id=leave_id,
                        employee_id=(row.get("EMP_NBR") or "").strip(),
                        action_id=(row.get("ACTN_NBR") or "").strip() or None,
                        start_date=self._date(row.get("BEG_DT")),
                        end_date=self._date(row.get("END_DT")),
                        paid=self._bool(row.get("PAID_IND")),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                action_id = (row.get("ACTN_NBR") or "").strip()
                self._row_issue(
                    "unparsable_leave_row", "admin_leave", leave_id, str(exc),
                    blocks=(action_id,) if action_id else (),
                )
        return out

    def _load_appeals(self, rows: list[dict]) -> list[AppealOrGrievance]:
        out: list[AppealOrGrievance] = []
        for row in rows:
            appeal_id = (row.get("APPL_NBR") or "").strip()
            try:
                out.append(
                    AppealOrGrievance(
                        appeal_id=appeal_id,
                        action_id=(row.get("ACTN_NBR") or "").strip(),
                        forum=self._code("appeal_forum", row.get("FORUM_CD")),
                        filed_date=self._date(row.get("FILED_DT")),
                        resolution_date=self._date(row.get("RESOLV_DT")),
                        outcome=self._code("appeal_outcome", row.get("OUTCOME_CD")),
                        back_pay_awarded=self._money(row.get("BACKPAY_AMT") or "0"),
                        settlement_amount=self._money(row.get("SETTLE_AMT") or "0"),
                        outside_counsel_hours=Decimal(str(row.get("OC_HOURS") or "0")),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._row_issue("unparsable_appeal_row", "appeal", appeal_id, str(exc))
        return out

    def _load_separations(self, rows: list[dict]) -> list[Separation]:
        out: list[Separation] = []
        for row in rows:
            employee_id = (row.get("EMP_NBR") or "").strip()
            try:
                out.append(
                    Separation(
                        employee_id=employee_id,
                        separation_date=self._date(row.get("TERM_DT")),
                        separation_reason=self._code("separation_reason", row.get("TERM_RSN_CD")),
                        linked_action_id=(row.get("ACTN_NBR") or "").strip() or None,
                        position_refilled=self._bool(row.get("REFILL_IND")),
                        refill_date=self._date(row.get("REFILL_DT")),
                        position_abolished=self._bool(row.get("ABOLISH_IND")),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                action_id = (row.get("ACTN_NBR") or "").strip()
                self._row_issue(
                    "unparsable_separation_row", "separation", employee_id, str(exc),
                    blocks=(action_id,) if action_id else (),
                )
        return out


# `_date` returns an optional date; named here to keep the annotation readable above.
from datetime import date as date_or_none  # noqa: E402
