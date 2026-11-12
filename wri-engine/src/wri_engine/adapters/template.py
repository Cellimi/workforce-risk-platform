"""Adapter: the WRI standard intake template -> canonical schema.

The county adapter reads one fictional county's export. This one is what a licensee uses for
a real client. It does two jobs.

**It defines a plain template.** Five CSV files with readable column names and readable
values (`flsa_status` is `exempt`, not `E`). Only `employees.csv` and `actions.csv` are
required. A client with no clean export fills the template in directly.

**It applies a mapping when the client's export looks different.** A mapping says, per
template field, which column of the client's file it comes from, and how the client's codes
translate into template values. So a client file with `EMP_NBR` and `FLSA_CD = E` loads
without anyone editing it. The mapping is data, saved with the engagement.

It also **builds the organization from the data**. The county adapter needs a hand-written
description of the county (departments, job families, pay plan). Here the departments, job
families, locations and bargaining units are taken from what the employee file says, and the
entry salary used to price a replacement hire comes from the file too. Work schedules and
the action, forum and outcome vocabularies are the engine's standard ones.

Nothing raises on bad data. A row that cannot be used is reported as an `Issue`.
"""

from __future__ import annotations

import copy
import csv
import functools
import io
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from wri_engine.adapters.base import Issue, Severity, SourceAdapter
from wri_engine.adapters.validation import run_rules
from wri_engine.orgconfig import OrgConfig, default_org_config
from wri_engine.schema import (
    ActionType,
    AdminLeavePeriod,
    AppealForum,
    AppealOrGrievance,
    AppealOutcome,
    CanonicalDataset,
    CoverageMethod,
    DecidingOfficialLevel,
    DisciplineAction,
    Employee,
    FLSAStatus,
    InvestigationType,
    Separation,
    SeparationReason,
)
from wri_engine.sectors import DEFAULT_SECTOR, SectorPack, get_pack

SOURCE_ID = "wri_template"
DEFAULT_DATE_FORMAT = "%Y-%m-%d"
NON_REPRESENTED = "Non-represented"

#: Template wording -> the engine's schedule ids.
SCHEDULES = {
    "weekday_8": "civilian_5x8",
    "shift_12": "shift_12",
    "shift_24_48": "fire_24_48",
}
TURNOVER_PROFILES = (
    "sworn_deputy",
    "corrections",
    "dispatch",
    "fire_ems",
    "civilian_skilled",
    "civilian_standard",
)
_TRUE = {"Y", "YES", "TRUE", "1", "T"}
_FALSE = {"N", "NO", "FALSE", "0", "F"}
#: Used when an action row does not say who decided it.
_DEFAULT_DECIDER = {
    ActionType.DOCUMENTED_COUNSELING: DecidingOfficialLevel.SUPERVISOR,
    ActionType.WRITTEN_REPRIMAND: DecidingOfficialLevel.SUPERVISOR,
    ActionType.SUSPENSION_1_3: DecidingOfficialLevel.DIVISION_HEAD,
    ActionType.SUSPENSION_4_14: DecidingOfficialLevel.DIVISION_HEAD,
    ActionType.SUSPENSION_15_PLUS: DecidingOfficialLevel.DEPARTMENT_HEAD,
    ActionType.DEMOTION: DecidingOfficialLevel.DEPARTMENT_HEAD,
    ActionType.REMOVAL: DecidingOfficialLevel.DEPARTMENT_HEAD,
}


# ---------------------------------------------------------------------------
# the template
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldSpec:
    name: str
    required: bool
    kind: str  # text | date | yes_no | number | money | choice
    description: str
    choices: tuple[str, ...] = ()
    synonyms: tuple[str, ...] = ()


@dataclass(frozen=True)
class TableSpec:
    name: str
    file: str
    required: bool
    description: str
    fields: tuple[FieldSpec, ...]

    def field(self, name: str) -> FieldSpec:
        return next(f for f in self.fields if f.name == name)

    @property
    def field_names(self) -> list[str]:
        return [f.name for f in self.fields]


def _f(name, required, kind, description, choices=(), synonyms=()):
    return FieldSpec(name, required, kind, description, tuple(choices), tuple(synonyms))


def _values(enum) -> tuple[str, ...]:
    return tuple(str(v) for v in enum)


def _misconduct_ids() -> tuple[str, ...]:
    return tuple(default_org_config().misconduct_categories)


TEMPLATE: tuple[TableSpec, ...] = (
    TableSpec(
        "employees",
        "employees.csv",
        True,
        "One row per employee who was on the payroll at any time in the review period, "
        "including people who have since left.",
        (
            _f(
                "employee_id",
                True,
                "text",
                "The employer's unique id for the person. Never a Social Security number.",
                synonyms=(
                    "emp nbr",
                    "employee number",
                    "emp id",
                    "employee no",
                    "ee id",
                    "worker id",
                    "person id",
                    "emp no",
                ),
            ),
            _f(
                "department",
                True,
                "text",
                "Department or business unit name.",
                synonyms=("dept", "dept name", "business unit", "division", "org unit"),
            ),
            _f(
                "job_family",
                True,
                "text",
                "The employee type used to group results, such as "
                "'Deputy' or 'Warehouse Associate'. Broader than a job title.",
                synonyms=(
                    "role family",
                    "employee type",
                    "job group",
                    "classification",
                    "job category",
                    "position category",
                    "position type",
                ),
            ),
            _f(
                "job_title",
                False,
                "text",
                "Position title.",
                synonyms=("position title", "title", "posn title", "job name", "position"),
            ),
            _f(
                "job_class_code",
                False,
                "text",
                "The employer's job class or job code.",
                synonyms=("class cd", "job code", "class code", "job class"),
            ),
            _f(
                "pay_grade",
                False,
                "text",
                "Pay grade or band label.",
                synonyms=("grade", "band", "pay band"),
            ),
            _f("pay_step", False, "number", "Step within the grade. Blank means 1."),
            _f(
                "annual_base_salary",
                True,
                "money",
                "Annual base pay, before overtime and benefits.",
                synonyms=(
                    "annl sal",
                    "annual salary",
                    "base salary",
                    "salary",
                    "annual pay",
                    "base pay",
                ),
            ),
            _f(
                "entry_annual_salary",
                False,
                "money",
                "Starting annual pay for a new hire in "
                "this job family. Used to price a replacement. Blank means the lowest current "
                "salary in the job family.",
                synonyms=("starting salary", "entry salary", "step 1 salary", "hiring rate"),
            ),
            _f(
                "flsa_status",
                True,
                "choice",
                "Overtime status. '7k' is the public-safety partial exemption.",
                _values(FLSAStatus),
                ("flsa", "flsa cd", "overtime status", "exempt status", "pay type", "exempt"),
            ),
            _f(
                "bargaining_unit",
                False,
                "text",
                "Union or bargaining unit name. Blank means not represented.",
                synonyms=("union", "barg cd", "bargaining unit name", "union name"),
            ),
            _f(
                "is_sworn",
                True,
                "yes_no",
                "Y for sworn public-safety staff, otherwise N.",
                synonyms=("sworn", "sworn ind", "sworn officer"),
            ),
            _f(
                "minimum_staffing_role",
                True,
                "yes_no",
                "Y if the post must be covered when "
                "the person is absent (the absence is backfilled with overtime).",
                synonyms=(
                    "min staff ind",
                    "minimum staffing",
                    "backfill required",
                    "coverage required",
                ),
            ),
            _f(
                "work_schedule",
                True,
                "choice",
                "weekday_8 is Monday to Friday, 8-hour days. "
                "shift_12 is rotating 12-hour shifts. shift_24_48 is 24 hours on, 48 off.",
                tuple(SCHEDULES),
                ("schedule", "shift pattern", "work pattern"),
            ),
            _f(
                "standard_shift_hours",
                False,
                "number",
                "Hours in one normal shift. Blank means the schedule's standard.",
                synonyms=("shift hrs", "shift hours", "shift length"),
            ),
            _f(
                "turnover_profile",
                True,
                "choice",
                "Which replacement-cost profile applies if this person is removed.",
                TURNOVER_PROFILES,
                ("replacement profile",),
            ),
            _f(
                "coverage_method",
                False,
                "choice",
                "How the employer covers this person's post when they are away: overtime for "
                "a peer, a temp agency, or none because the post can stand empty. Blank "
                "means overtime.",
                _values(CoverageMethod),
                ("coverage", "backfill method", "cover method", "absence coverage"),
            ),
            _f(
                "is_supervisor",
                False,
                "yes_no",
                "Y if the person supervises others. Blank means N.",
                synonyms=(
                    "supervisor ind",
                    "supervisory",
                    "people manager",
                    "supv",
                    "supervisor flag",
                ),
            ),
            _f(
                "work_location",
                True,
                "text",
                "Site, station or facility.",
                synonyms=(
                    "work loc",
                    "location",
                    "site",
                    "work site",
                    "facility",
                    "duty station",
                    "station",
                    "yard",
                    "post",
                ),
            ),
            _f(
                "supervisor_id",
                False,
                "text",
                "The employee_id of the person's supervisor.",
                synonyms=(
                    "supv emp nbr",
                    "supervisor employee id",
                    "manager id",
                    "reports to",
                    "supervisor emp id",
                ),
            ),
            _f(
                "hire_date",
                True,
                "date",
                "Date hired.",
                synonyms=(
                    "hire dt",
                    "start date",
                    "date of hire",
                    "original hire date",
                    "service date",
                ),
            ),
            _f(
                "separation_date",
                False,
                "date",
                "Date the person left, if they have.",
                synonyms=("term dt", "termination date", "end date", "separation dt"),
            ),
            _f(
                "separation_reason",
                False,
                "choice",
                "Why the person left.",
                _values(SeparationReason),
                ("term rsn cd", "termination reason", "separation reason code"),
            ),
        ),
    ),
    TableSpec(
        "actions",
        "actions.csv",
        True,
        "One row per disciplinary action decided in the review period.",
        (
            _f(
                "action_id",
                True,
                "text",
                "Unique id for the action or case.",
                synonyms=(
                    "actn nbr",
                    "case number",
                    "case id",
                    "action number",
                    "discipline id",
                    "record id",
                ),
            ),
            _f(
                "employee_id",
                True,
                "text",
                "Who was disciplined. Must match employees.csv.",
                synonyms=(
                    "emp nbr",
                    "employee number",
                    "emp id",
                    "employee no",
                    "ee id",
                    "worker id",
                ),
            ),
            _f(
                "incident_date",
                False,
                "date",
                "When the conduct happened. Blank means the decision date.",
                synonyms=("incdt dt", "incident dt", "date of incident", "offense date"),
            ),
            _f(
                "proposal_date",
                False,
                "date",
                "When the action was proposed, if recorded.",
                synonyms=("prop dt", "proposed date", "notice date"),
            ),
            _f(
                "decision_date",
                True,
                "date",
                "When the action was decided.",
                synonyms=(
                    "decn dt",
                    "decision dt",
                    "action date",
                    "effective date",
                    "date of action",
                ),
            ),
            _f(
                "misconduct_category",
                True,
                "text",
                "Use a standard category where one fits. "
                "Anything else is kept as a custom category.",
                (),
                (
                    "miscnd cd",
                    "misconduct",
                    "offense",
                    "offense category",
                    "violation type",
                    "charge",
                    "infraction",
                ),
            ),
            _f(
                "misconduct_subtype",
                False,
                "text",
                "More specific description, if recorded.",
                synonyms=("miscnd sub", "subtype", "offense detail", "specification"),
            ),
            _f(
                "action_type",
                True,
                "choice",
                "The penalty. 'suspension' is accepted when suspension_days is filled in.",
                (*_values(ActionType), "suspension"),
                (
                    "actn cd",
                    "action",
                    "penalty",
                    "discipline type",
                    "action taken",
                    "corrective action",
                ),
            ),
            _f(
                "suspension_days",
                False,
                "number",
                "Working days suspended. Required for a suspension.",
                synonyms=("susp days", "days suspended", "suspension length", "days off"),
            ),
            _f(
                "suspension_start_date",
                False,
                "date",
                "First day of the suspension.",
                synonyms=("susp beg dt", "suspension start", "suspension begin date"),
            ),
            _f(
                "was_investigated",
                False,
                "yes_no",
                "Y if a formal investigation preceded the action. Blank means N.",
                synonyms=("invest ind", "investigated", "investigation"),
            ),
            _f(
                "investigation_type",
                False,
                "choice",
                "Who investigated. Required when was_investigated is Y.",
                _values(InvestigationType),
                ("invest typ", "investigator type", "investigation type code"),
            ),
            _f(
                "deciding_official_level",
                False,
                "choice",
                "The level of the person who "
                "decided. Blank is filled in from the action type and reported.",
                _values(DecidingOfficialLevel),
                ("decide lvl", "decision level", "deciding official", "approver level"),
            ),
            _f(
                "deciding_official_id",
                False,
                "text",
                "The employee_id of the decider, if known.",
                synonyms=("decide emp nbr", "deciding official id", "approver id"),
            ),
            _f(
                "position_abolished",
                False,
                "yes_no",
                "Y if the position was eliminated after a removal. Blank means N.",
                synonyms=("posn abolish ind", "position eliminated"),
            ),
            _f(
                "outside_investigator_hours",
                False,
                "number",
                "Hours billed by an outside investigator (a law firm or an HR consultant). "
                "Blank means the investigation was done in house.",
                synonyms=(
                    "ext invest hrs",
                    "outside investigator hours",
                    "external investigator hours",
                    "third party investigator hours",
                ),
            ),
            _f(
                "written_conduct_policy",
                False,
                "yes_no",
                "Y if an unpaid suspension was imposed under a written policy that applies to "
                "all employees. Blank means Y. It matters only for a salaried exempt "
                "employee, where federal wage-and-hour rules allow an unpaid disciplinary "
                "suspension only in full-day increments under such a policy.",
                synonyms=(
                    "written policy ind",
                    "written policy",
                    "conduct policy",
                    "policy in writing",
                ),
            ),
        ),
    ),
    TableSpec(
        "admin_leave",
        "admin_leave.csv",
        False,
        "One row per period an employee was kept off work, paid, while an action was pending.",
        (
            _f(
                "leave_id",
                False,
                "text",
                "Unique id. Blank is numbered automatically.",
                synonyms=("leave nbr", "leave number"),
            ),
            _f(
                "employee_id",
                True,
                "text",
                "Who was on leave.",
                synonyms=("emp nbr", "employee number", "emp id"),
            ),
            _f(
                "action_id",
                False,
                "text",
                "The action this leave belongs to.",
                synonyms=("actn nbr", "case number", "case id"),
            ),
            _f(
                "start_date",
                True,
                "date",
                "First day of leave.",
                synonyms=("beg dt", "begin date", "leave start"),
            ),
            _f(
                "end_date",
                True,
                "date",
                "Last day of leave.",
                synonyms=("end dt", "leave end", "return date"),
            ),
            _f(
                "paid",
                False,
                "yes_no",
                "Y if the leave was paid. Blank means Y.",
                synonyms=("paid ind", "paid leave"),
            ),
        ),
    ),
    TableSpec(
        "appeals",
        "appeals.csv",
        False,
        "One row per grievance, appeal, arbitration or court challenge to an action.",
        (
            _f(
                "appeal_id",
                False,
                "text",
                "Unique id. Blank is numbered automatically.",
                synonyms=("appl nbr", "grievance number", "appeal number"),
            ),
            _f(
                "action_id",
                True,
                "text",
                "The action being challenged.",
                synonyms=("actn nbr", "case number", "case id"),
            ),
            _f(
                "forum",
                True,
                "choice",
                "Where the challenge was heard.",
                _values(AppealForum),
                ("forum cd", "venue", "appeal type"),
            ),
            _f("filed_date", True, "date", "Date filed.", synonyms=("filed dt", "date filed")),
            _f(
                "resolution_date",
                False,
                "date",
                "Date resolved. Blank while pending.",
                synonyms=("resolv dt", "resolved date", "closed date", "decision date"),
            ),
            _f(
                "outcome",
                True,
                "choice",
                "Result. Use 'pending' if not yet resolved.",
                _values(AppealOutcome),
                ("outcome cd", "result", "disposition"),
            ),
            _f(
                "back_pay_awarded",
                False,
                "money",
                "Back pay the employer had to pay.",
                synonyms=("backpay amt", "back pay", "backpay"),
            ),
            _f(
                "settlement_amount",
                False,
                "money",
                "Settlement paid.",
                synonyms=("settle amt", "settlement"),
            ),
            _f(
                "outside_counsel_hours",
                False,
                "number",
                "Outside counsel hours billed.",
                synonyms=("oc hours", "counsel hours", "attorney hours"),
            ),
            _f(
                "insurance_retention_paid",
                False,
                "money",
                "The retention or deductible the employer paid under an employment practices "
                "liability policy. Fill this in only where no settlement or back pay is "
                "recorded against the same challenge, so the same dollars are not counted "
                "twice.",
                synonyms=(
                    "epli retention",
                    "retention paid",
                    "deductible paid",
                    "self insured retention",
                ),
            ),
            _f(
                "insurer_paid_amount",
                False,
                "money",
                "What the insurer paid of the amount above. It is reported as a credit "
                "against the cost, never as a cost.",
                synonyms=(
                    "insurer paid",
                    "carrier paid",
                    "insurance recovery",
                    "insurance reimbursement",
                ),
            ),
        ),
    ),
    TableSpec(
        "separations",
        "separations.csv",
        False,
        "One row per employee who left because of a disciplinary action.",
        (
            _f(
                "employee_id",
                True,
                "text",
                "Who left.",
                synonyms=("emp nbr", "employee number", "emp id"),
            ),
            _f(
                "separation_date",
                True,
                "date",
                "Last day of employment.",
                synonyms=("term dt", "termination date"),
            ),
            _f(
                "separation_reason",
                True,
                "choice",
                "Why the person left.",
                _values(SeparationReason),
                ("term rsn cd", "termination reason"),
            ),
            _f(
                "linked_action_id",
                False,
                "text",
                "The disciplinary action behind the separation.",
                synonyms=("actn nbr", "case number", "action id"),
            ),
            _f(
                "position_refilled",
                False,
                "yes_no",
                "Y once a replacement has started. Blank means N.",
                synonyms=("refill ind", "backfilled", "refilled"),
            ),
            _f(
                "refill_date",
                False,
                "date",
                "Date the replacement started.",
                synonyms=("refill dt", "backfill date", "replacement start date"),
            ),
            _f(
                "position_abolished",
                False,
                "yes_no",
                "Y if the position was eliminated. Blank means N.",
                synonyms=("abolish ind", "position eliminated"),
            ),
            _f(
                "separation_agreement",
                False,
                "yes_no",
                "Y if the person signed a separation or release agreement. Blank means N.",
                synonyms=(
                    "sep agmt ind",
                    "separation agreement",
                    "release agreement",
                    "severance agreement signed",
                ),
            ),
            _f(
                "severance_amount",
                False,
                "money",
                "Severance actually paid.",
                synonyms=("sev amt", "severance", "severance pay"),
            ),
            _f(
                "unemployment_claim_filed",
                False,
                "yes_no",
                "Y if the person filed an unemployment claim. Blank means N.",
                synonyms=("ui claim ind", "unemployment claim", "ui filed"),
            ),
            _f(
                "unemployment_benefits_charged",
                False,
                "money",
                "Benefits charged to the employer's unemployment account for this person. "
                "Blank where the third-party administrator has not reported them yet.",
                synonyms=(
                    "ui benefits charged",
                    "unemployment charged",
                    "ui charges",
                    "benefit charges",
                ),
            ),
        ),
    ),
)
TABLES: dict[str, TableSpec] = {t.name: t for t in TEMPLATE}


def template_spec() -> dict:
    """The template as data, for the API and for anyone building a file by hand."""
    org = default_org_config()
    return {
        "source": SOURCE_ID,
        "date_format": "YYYY-MM-DD unless the mapping says otherwise",
        "yes_no": "Y or N (yes/no, true/false and 1/0 are also read)",
        "standard_misconduct_categories": [
            {"id": c.id, "label": c.label} for c in org.misconduct_categories.values()
        ],
        "tables": [
            {
                "name": t.name,
                "file": t.file,
                "required": t.required,
                "description": t.description,
                "fields": [
                    {
                        "name": f.name,
                        "required": f.required,
                        "kind": f.kind,
                        "description": f.description,
                        "choices": list(f.choices),
                    }
                    for f in t.fields
                ],
            }
            for t in TEMPLATE
        ],
    }


def template_guide() -> str:
    lines = [
        "WRI DATA INTAKE TEMPLATE",
        "",
        "Fill in one CSV file per table below. Only employees.csv and actions.csv are",
        "required. Leave a file out if the employer does not keep that record; the costs",
        "that depend on it will be zero, and the load report will say so.",
        "",
        "Removals: the cost of replacing a removed employee is usually the largest cost of",
        "a removal, and it is counted only when the separation is recorded -- either in",
        "separations.csv with linked_action_id, or as separation_date and",
        "separation_reason in employees.csv.",
        "",
        "Dates: YYYY-MM-DD. Yes/no: Y or N. Money: plain numbers, no currency symbol needed.",
        "Never include Social Security numbers, dates of birth or home addresses.",
        "",
        "Standard misconduct categories (use the id; anything else is kept as custom):",
    ]
    org = default_org_config()
    lines += [f"  {c.id:<22} {c.label}" for c in org.misconduct_categories.values()]
    for table in TEMPLATE:
        lines += ["", "", f"{table.file}  ({'required' if table.required else 'optional'})"]
        lines += [table.description, ""]
        for spec in table.fields:
            need = "REQUIRED" if spec.required else "optional"
            lines.append(f"  {spec.name}  [{need}, {spec.kind.replace('_', '/')}]")
            lines.append(f"      {spec.description}")
            if spec.choices:
                lines.append(f"      Allowed: {', '.join(spec.choices)}")
    return "\n".join(lines) + "\n"


def template_zip() -> bytes:
    """Blank CSVs with the right headers, plus the field guide."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("READ_ME_FIRST.txt", template_guide())
        for table in TEMPLATE:
            archive.writestr(table.file, ",".join(table.field_names) + "\r\n")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# mapping
# ---------------------------------------------------------------------------
class MappingError(ValueError):
    """The mapping itself is malformed (not the data it is applied to)."""


@dataclass
class TableMapping:
    file: str
    columns: dict[str, str] = field(default_factory=dict)  # template field -> source column
    values: dict[str, dict[str, str]] = field(default_factory=dict)  # field -> code -> value
    constants: dict[str, str] = field(default_factory=dict)  # field -> fixed value


@dataclass
class Mapping:
    date_format: str = DEFAULT_DATE_FORMAT
    tables: dict[str, TableMapping] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "date_format": self.date_format,
            "tables": {
                name: {
                    "file": t.file,
                    "columns": dict(t.columns),
                    "values": {k: dict(v) for k, v in t.values.items()},
                    "constants": dict(t.constants),
                }
                for name, t in self.tables.items()
            },
        }


def parse_mapping(raw: dict | None) -> Mapping:
    """Validate a mapping supplied as data. Unknown tables or fields are errors, because a
    misspelt field would otherwise be ignored silently."""
    raw = raw or {}
    if not isinstance(raw, dict):
        raise MappingError("a mapping must be an object")
    date_format = raw.get("date_format") or DEFAULT_DATE_FORMAT
    try:
        datetime(2024, 1, 31).strftime(date_format)
    except (ValueError, TypeError):
        raise MappingError(f"date_format {date_format!r} is not a valid format") from None
    if "%" not in str(date_format):
        raise MappingError("date_format must use strptime codes such as %m/%d/%Y")
    mapping = Mapping(date_format=str(date_format))
    tables = raw.get("tables") or {}
    if not isinstance(tables, dict):
        raise MappingError("`tables` must be an object keyed by table name")
    for name, body in tables.items():
        if name not in TABLES:
            raise MappingError(f"unknown table {name!r}. Tables: {', '.join(TABLES)}")
        body = body or {}
        spec = TABLES[name]
        file = str(body.get("file") or spec.file)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.csv", file):
            raise MappingError(f"{name}: file must be a plain .csv file name, got {file!r}")
        table = TableMapping(file=file)
        for section in ("columns", "values", "constants"):
            part = body.get(section) or {}
            if not isinstance(part, dict):
                raise MappingError(f"{name}.{section} must be an object")
            unknown = sorted(set(part) - set(spec.field_names))
            if unknown:
                raise MappingError(
                    f"{name}.{section}: {', '.join(unknown)} "
                    f"{'is' if len(unknown) == 1 else 'are'} not in the template"
                )
        table.columns = {k: str(v) for k, v in (body.get("columns") or {}).items() if v}
        table.constants = {k: str(v) for k, v in (body.get("constants") or {}).items()}
        for field_name, codes in (body.get("values") or {}).items():
            if not isinstance(codes, dict):
                raise MappingError(f"{name}.values.{field_name} must be an object")
            table.values[field_name] = {str(k).strip(): str(v) for k, v in codes.items()}
        mapping.tables[name] = table
    return mapping


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


#: Abbreviations seen in HR exports, expanded before column names are compared.
_ABBREVIATIONS = {
    "dt": "date",
    "no": "id",
    "nbr": "id",
    "num": "id",
    "number": "id",
    "emp": "employee",
    "ee": "employee",
    "empl": "employee",
    "worker": "employee",
    "staff": "employee",
    "supv": "supervisor",
    "supvr": "supervisor",
    "mgr": "manager",
    "dept": "department",
    "loc": "location",
    "invest": "investigated",
    "investigation": "investigated",
    "susp": "suspension",
    "hrs": "hours",
    "sched": "schedule",
    "cat": "category",
    "term": "separation",
    "termination": "separation",
    "sepn": "separation",
    "rsn": "reason",
}
#: Words that carry no meaning in a column name.
_FILLER = {"of", "the", "a", "an", "is", "was", "ind", "flag", "cd", "code"}


def _tokens(text: str) -> frozenset[str]:
    """A column name as a set of meaningful words: `Worker #` and `employee_id` both become
    {employee, id}; `Start Dt` and `start date` both become {start, date}."""
    words = re.sub(r"[^a-z0-9]+", " ", text.lower().replace("#", " number ")).split()
    out = set()
    for word in words:
        word = _ABBREVIATIONS.get(word, word)
        if word in _FILLER or word.isdigit() or re.fullmatch(r"fy\d+", word):
            continue
        out.add(word)
    return frozenset(out)


def _match_score(column: str, spec: FieldSpec) -> int:
    """How well a source column name fits a template field: 3 for an exact name or
    synonym, 2 when the words are the same once abbreviations are expanded, 0 otherwise."""
    norm, tokens = _norm(column), _tokens(column)
    best = 0
    for candidate in (spec.name, *spec.synonyms):
        if norm == _norm(candidate):
            return 3
        if tokens and tokens == _tokens(candidate):
            best = 2
    return best


def read_headers(directory: Path) -> dict[str, dict]:
    """What is in a folder of CSVs: per file, its columns and row count."""
    out: dict[str, dict] = {}
    for path in sorted(Path(directory).glob("*.csv")):
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.reader(fh)
            headers = next(reader, [])
            rows = sum(1 for _ in reader)
        out[path.name] = {"columns": [h.strip() for h in headers], "rows": rows}
    return out


#: Tried in this order when guessing a file's date format. US month-first comes before
#: day-first, and the guess is flagged when both read every value.
_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%m/%d/%y",
    "%m-%d-%Y",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d %b %Y",
    "%b %d, %Y",
)
#: Employee fields that an HR export rarely holds. They describe the kind of job, so the
#: suggestion sets them up to be decided once per job family.
PER_JOB_FAMILY = ("is_sworn", "minimum_staffing_role", "work_schedule", "turnover_profile")


def _read_columns(path: Path) -> dict[str, list[str]]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        columns: dict[str, list[str]] = {(h or "").strip(): [] for h in reader.fieldnames or []}
        for row in reader:
            for key, value in row.items():
                if key is not None and (key := key.strip()) in columns:
                    columns[key].append((value or "").strip())
    return columns


@functools.cache
def _standard_categories() -> frozenset[str]:
    org = default_org_config()
    return frozenset(
        {c.id for c in org.misconduct_categories.values()}
        | {_norm(c.label) for c in org.misconduct_categories.values()}
    )


def _valid(spec: FieldSpec, value: str) -> bool:
    if spec.kind == "yes_no":
        return value.upper() in _TRUE | _FALSE
    if spec.name == "misconduct_category":
        standard = _standard_categories()
        return value in standard or _norm(value) in standard
    return _norm(value).replace(" ", "_") in spec.choices


def _guess_date_format(samples: list[str]) -> tuple[str | None, bool]:
    samples = [v for v in samples if v][:500]
    if not samples:
        return None, False
    fits = []
    for fmt in _DATE_FORMATS:
        try:
            for value in samples:
                datetime.strptime(value, fmt)
        except ValueError:
            continue
        fits.append(fmt)
    if not fits:
        return None, False
    ambiguous = "%m/%d/%Y" in fits and "%d/%m/%Y" in fits
    return fits[0], ambiguous


def codes_to_translate(directory: Path, mapping: dict | Mapping) -> dict[str, dict]:
    """For a mapping as it stands now: every value in a mapped yes/no or choice column (and
    every non-standard misconduct code) that the mapping does not yet translate, with its
    count. Used by the preview so the list follows the mapping as a person edits it."""
    parsed = mapping if isinstance(mapping, Mapping) else parse_mapping(mapping)
    out: dict[str, dict] = {}
    for table in TEMPLATE:
        tm = parsed.tables.get(table.name) or TableMapping(file=table.file)
        path = Path(directory) / tm.file
        if not path.exists():
            continue
        columns = _read_columns(path)
        found: dict[str, dict[str, int]] = {}
        for spec in table.fields:
            if spec.kind not in ("yes_no", "choice") and spec.name != "misconduct_category":
                continue
            if spec.name in tm.constants:
                continue
            column = tm.columns.get(spec.name, spec.name)
            if column not in columns:
                continue
            codes = tm.values.get(spec.name, {})
            odd = Counter(
                v
                for v in columns[column]
                if v and not _valid(spec, codes.get(v, v)) and not codes.get(v)
            )
            if odd:
                found[spec.name] = {
                    "column": column,
                    "allowed": list(spec.choices) or ["Y", "N"],
                    "codes": dict(odd.most_common(100)),
                }
        if found:
            out[table.name] = found
    return out


def suggest_mapping(directory: Path) -> dict:
    """A first-draft mapping from the uploaded files, for a person to check.

    * Each file is matched to the template table whose fields it covers best, and each
      template field to a column by name, synonym or abbreviation.
    * Every code in a mapped yes/no or choice column that the template does not already
      accept is listed with its count, so it can be translated before the preview.
    * Employee fields that describe a kind of job (sworn, minimum staffing, schedule,
      turnover profile) and have no column are set up to be decided once per job family.
    * The date format is guessed from the mapped date columns.

    Nothing here is applied without review.
    """
    found = read_headers(directory)
    data = {name: _read_columns(Path(directory) / name) for name in found}
    draft: dict[str, dict] = {}
    report: dict[str, dict] = {}
    used_files: set[str] = set()
    date_samples: list[str] = []
    for table in TEMPLATE:
        best: tuple[int, str, dict[str, str]] | None = None
        for file, info in found.items():
            if file in used_files:
                continue
            columns: dict[str, str] = {}
            ordered = sorted(table.fields, key=lambda f: not f.required)
            for spec in ordered:
                scored = [
                    (_match_score(c, spec), c) for c in info["columns"] if c not in columns.values()
                ]
                scored = [x for x in scored if x[0]]
                if scored:
                    columns[spec.name] = max(scored)[1]
            score = sum(2 if table.field(f).required else 1 for f in columns)
            if file.lower() == table.file:
                score += 100
            if columns and (best is None or score > best[0]):
                best = (score, file, columns)
        required = [f.name for f in table.fields if f.required]
        if best is None or sum(1 for f in required if f in best[2]) < min(2, len(required)):
            report[table.name] = {
                "file": None,
                "required_table": table.required,
                "missing_required_fields": required,
            }
            continue
        _, file, columns = best
        used_files.add(file)
        values: dict[str, dict[str, str]] = {}
        to_translate: dict[str, dict[str, int]] = {}
        for field_name, column in columns.items():
            spec = table.field(field_name)
            if spec.kind == "date":
                date_samples.extend(data[file].get(column, []))
            if spec.kind in ("yes_no", "choice") or field_name == "misconduct_category":
                odd = Counter(v for v in data[file].get(column, []) if v and not _valid(spec, v))
                if odd:
                    to_translate[field_name] = dict(odd.most_common(50))
        decide: dict[str, dict] = {}
        if table.name == "employees" and "job_family" in columns:
            families = sorted(set(v for v in data[file][columns["job_family"]] if v))
            for field_name in PER_JOB_FAMILY:
                if field_name in columns:
                    continue
                spec = table.field(field_name)
                columns[field_name] = columns["job_family"]
                values[field_name] = {family: "" for family in families}
                decide[field_name] = {
                    "from_column": columns["job_family"],
                    "allowed": list(spec.choices) or ["Y", "N"],
                    "job_families": families,
                }
        draft[table.name] = {"file": file, "columns": columns, "values": values, "constants": {}}
        report[table.name] = {
            "file": file,
            "required_table": table.required,
            "matched_fields": sorted(f for f in columns if f not in decide),
            "missing_required_fields": [f for f in required if f not in columns],
            "unused_columns": [c for c in found[file]["columns"] if c not in columns.values()],
            "codes_to_translate": to_translate,
            "codes_note": (
                "Misconduct codes left untranslated are kept as the client's own categories "
                "and flagged; translate them to a standard category to compare across clients."
                if "misconduct_category" in to_translate
                else None
            ),
            "decide_per_job_family": decide,
        }
    date_format, ambiguous = _guess_date_format(date_samples)
    return {
        "files": found,
        "suggested_mapping": {"date_format": date_format or DEFAULT_DATE_FORMAT, "tables": draft},
        "date_format": {
            "detected": date_format,
            "ambiguous": ambiguous,
            "note": (
                "Every date here could be month-first or day-first; month-first was assumed."
                if ambiguous
                else None
            ),
        },
        "tables": report,
        "unmatched_files": sorted(set(found) - used_files),
    }


# ---------------------------------------------------------------------------
# the adapter
# ---------------------------------------------------------------------------
def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "unspecified"


def _mode(values: list):
    return Counter(values).most_common(1)[0][0]


class _RowError(ValueError):
    def __init__(self, message: str, field_name: str | None = None):
        super().__init__(message)
        self.field_name = field_name


class TemplateAdapter(SourceAdapter):
    source_id = SOURCE_ID

    def __init__(
        self,
        mapping: dict | Mapping | None = None,
        agency_name: str = "Client",
        sector: str | None = None,
    ):
        super().__init__()
        self.mapping = mapping if isinstance(mapping, Mapping) else parse_mapping(mapping)
        self.agency_name = agency_name
        #: The engagement's sector. It decides which benefits group a wage is loaded under
        #: and which turnover profiles the engine will run, so it comes from the engagement
        #: record and never from the client's file.
        self.sector = sector or DEFAULT_SECTOR
        self._pack: SectorPack = get_pack(self.sector)
        self._base = default_org_config()
        #: Built from the data in `load`. Until then, the engine's standard organization.
        self.org: OrgConfig = self._base
        self._categories = {
            **{c.id: c.id for c in self._base.misconduct_categories.values()},
            **{_norm(c.label): c.id for c in self._base.misconduct_categories.values()},
        }

    # -- reading -----------------------------------------------------------
    def _table(self, name: str) -> TableMapping:
        return self.mapping.tables.get(name) or TableMapping(file=TABLES[name].file)

    def _rows(self, directory: Path, name: str) -> list[dict[str, str]]:
        """Read one table and return its rows keyed by template field name, with the
        mapping's column renames, constants and code translations already applied."""
        spec, table = TABLES[name], self._table(name)
        path = directory / table.file
        if not path.exists():
            self._report.add(
                Issue(
                    rule_id="missing_source_file",
                    severity=Severity.BLOCKING if spec.required else Severity.WARNING,
                    entity="file",
                    entity_id=table.file,
                    message=(
                        f"{table.file} was not supplied"
                        + (
                            ""
                            if spec.required
                            else "; separations can come only from the employee file's "
                            "separation date and reason"
                            if name == "separations"
                            else f"; no {name.replace('_', ' ')} costs can be counted"
                        )
                    ),
                )
            )
            return []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            headers = [h.strip() for h in (reader.fieldnames or [])]
            raw_rows = [
                {(k or "").strip(): (v or "").strip() for k, v in r.items()} for r in reader
            ]
        source_of = {f: table.columns.get(f, f) for f in spec.field_names}
        for spec_field in spec.fields:
            column = source_of[spec_field.name]
            present = column in headers or spec_field.name in table.constants
            if spec_field.required and not present:
                self._report.add(
                    Issue(
                        rule_id="missing_required_column",
                        severity=Severity.BLOCKING,
                        entity="file",
                        entity_id=table.file,
                        field=spec_field.name,
                        message=(
                            f"{table.file} has no column for required field "
                            f"{spec_field.name!r} (looked for {column!r})"
                        ),
                    )
                )
        out = []
        for raw in raw_rows:
            row: dict[str, str] = {}
            for name_ in spec.field_names:
                value = table.constants.get(name_, raw.get(source_of[name_], ""))
                codes = table.values.get(name_)
                if codes and value in codes:
                    value = codes[value]
                row[name_] = value
            out.append(row)
        return out

    # -- scalar parsing ----------------------------------------------------
    def _date(self, raw: str, name: str, required: bool = False) -> date | None:
        if not raw:
            if required:
                raise _RowError(f"{name} is blank", name)
            return None
        for fmt in dict.fromkeys((self.mapping.date_format, DEFAULT_DATE_FORMAT)):
            try:
                return datetime.strptime(raw, fmt).date()
            except ValueError:
                continue
        raise _RowError(f"{name} {raw!r} is not a date in the expected format", name)

    @staticmethod
    def _bool(raw: str, name: str, default: bool | None = None) -> bool:
        text = raw.upper()
        if text in _TRUE:
            return True
        if text in _FALSE:
            return False
        if not text and default is not None:
            return default
        if not text:
            raise _RowError(f"{name} is blank. Fill it in, or set it in the mapping.", name)
        raise _RowError(f"{name} {raw!r} is not Y or N. Add it to the mapping's values.", name)

    @staticmethod
    def _number(raw: str, name: str, default: Decimal | None = None) -> Decimal:
        text = raw.replace(",", "").replace("$", "").strip()
        if not text:
            if default is None:
                raise _RowError(f"{name} is blank", name)
            return default
        try:
            value = Decimal(text)
        except InvalidOperation:
            raise _RowError(f"{name} {raw!r} is not a number", name) from None
        if not value.is_finite() or value < 0:
            raise _RowError(f"{name} {raw!r} is not a usable amount", name)
        return value

    @staticmethod
    def _choice(raw: str, name: str, table: str, default: str | None = None) -> str:
        spec = TABLES[table].field(name)
        text = _norm(raw).replace(" ", "_")
        if not text and default is not None:
            return default
        if text in spec.choices:
            return text
        if not text:
            raise _RowError(f"{name} is blank. Fill it in, or set it in the mapping.", name)
        raise _RowError(
            f"{name} {raw!r} is not one of: {', '.join(spec.choices)}. Add it to the "
            f"mapping's values for {name}.",
            name,
        )

    # -- sector ------------------------------------------------------------
    def _benefits_group(self, sworn: bool) -> str:
        """Which benefits multiplier loads this person's wage.

        A county loads a sworn deputy's wage differently from a clerk's, so the public pack
        has a group for each. A company has one private-industry multiplier, so a private
        engagement uses it for everyone -- including anyone the client's file happens to
        mark sworn, because what the group selects is the employer's benefits cost, not the
        job's character.
        """
        groups = self._pack.benefits_assumption_by_group
        if sworn and "sworn_public_safety" in groups:
            return "sworn_public_safety"
        return self._pack.default_benefits_group

    def _turnover_profile(self, raw: str, employee_id: str) -> str:
        """The replacement-cost profile, limited to the ones this sector runs.

        A private client's file can arrive carrying a public-sector profile -- a mapped code,
        or a template filled in from an earlier engagement. Pricing a warehouse hire at
        deputy recruitment cost would overstate the figure, so the profile falls back to the
        sector's cheapest and the substitution is reported rather than made silently
        (CLAUDE.md rule 13).
        """
        profile = self._choice(raw, "turnover_profile", "employees")
        if profile in self._pack.turnover_profiles:
            return profile
        fallback = self._pack.standard_turnover_profile
        self._issue(
            "turnover_profile_not_in_sector",
            "employee",
            employee_id,
            f"turnover_profile {profile!r} is not one the {self._pack.label.lower()} cost "
            f"model runs. {fallback!r} was used instead, which is the cheapest to replace, "
            f"so no cost is filled in upward. Map the client's code to one of: "
            f"{', '.join(self._pack.turnover_profiles)}.",
            "turnover_profile",
            severity=Severity.WARNING,
        )
        return fallback

    # -- load --------------------------------------------------------------
    def load(self, path: Path) -> CanonicalDataset:
        path = Path(path)
        employee_rows = self._rows(path, "employees")
        action_rows = self._rows(path, "actions")
        self.org = self._build_org(employee_rows, action_rows)

        employees, employee_problems = self._load_employees(employee_rows)
        actions = self._load_actions(action_rows)
        leave = self._load_leave(self._rows(path, "admin_leave"))
        appeals = self._load_appeals(self._rows(path, "appeals"))
        separations = self._load_separations(self._rows(path, "separations"))

        separations = self._complete_separations(employees, actions, separations)

        for employee_id, (rule_id, message, field_name) in employee_problems.items():
            blocked = tuple(a.action_id for a in actions if a.employee_id == employee_id)
            self._issue(rule_id, "employee", employee_id, message, field_name, blocked)

        data = CanonicalDataset(
            employees=employees,
            actions=actions,
            leave_periods=leave,
            appeals=appeals,
            separations=separations,
        )
        for issue in run_rules(data, self.org):
            if issue.rule_id == "orphan_action":
                action = next(a for a in actions if a.action_id == issue.entity_id)
                if action.employee_id in employee_problems:
                    continue
            self._report.add(issue)
        return data

    def _complete_separations(
        self,
        employees: list[Employee],
        actions: list[DisciplineAction],
        separations: list[Separation],
    ) -> list[Separation]:
        """Turnover is costed only for a separation linked to the removal that caused it.

        * An employee row with a separation date and reason counts as a separation record
          when the separations file has none for that person.
        * A removal-for-cause separation with no linked action is linked to the employee's
          removal when there is exactly one; otherwise it is left alone and reported.
        * Any removal still without a linked separation is reported, because its replacement
          cost -- usually the largest single cost -- is not being counted.
        """
        removals: dict[str, list[DisciplineAction]] = {}
        for action in actions:
            if action.action_type == ActionType.REMOVAL:
                removals.setdefault(action.employee_id, []).append(action)

        out = list(separations)
        derived: list[str] = []
        recorded = {s.employee_id for s in separations}
        for employee in employees:
            if (
                employee.employee_id in recorded
                or employee.separation_date is None
                or employee.separation_reason is None
            ):
                continue
            out.append(
                Separation(
                    employee_id=employee.employee_id,
                    separation_date=employee.separation_date,
                    separation_reason=employee.separation_reason,
                )
            )
            derived.append(employee.employee_id)

        linked: list[str] = []
        ambiguous: list[str] = []
        for index, separation in enumerate(out):
            if (
                separation.linked_action_id
                or separation.separation_reason != SeparationReason.REMOVAL_FOR_CAUSE
            ):
                continue
            candidates = removals.get(separation.employee_id, [])
            if len(candidates) == 1:
                out[index] = separation.model_copy(
                    update={"linked_action_id": candidates[0].action_id}
                )
                linked.append(candidates[0].action_id)
            elif len(candidates) > 1:
                ambiguous.append(separation.employee_id)

        if derived:
            self._issue(
                "separation_from_employee_file",
                "file",
                self._table("employees").file,
                f"{len(derived)} separation(s) were taken from the employee file's separation "
                f"date and reason, because the separations file had no row for them.",
                "separation_date",
                severity=Severity.WARNING,
            )
        if linked:
            self._issue(
                "separation_linked_to_removal",
                "file",
                self._table("separations").file,
                f"{len(linked)} removal-for-cause separation(s) did not name the action that "
                f"caused them and were linked to the employee's only removal: "
                f"{', '.join(linked[:10])}" + (" ..." if len(linked) > 10 else ""),
                "linked_action_id",
                severity=Severity.WARNING,
            )
        if ambiguous:
            self._issue(
                "separation_link_ambiguous",
                "file",
                self._table("separations").file,
                f"{len(ambiguous)} employee(s) have more than one removal and a separation that "
                f"does not say which one caused it, so no replacement cost is counted. Fill in "
                f"linked_action_id: {', '.join(sorted(ambiguous)[:10])}",
                "linked_action_id",
                severity=Severity.WARNING,
            )
        covered = {s.linked_action_id for s in out if s.linked_action_id}
        unlinked = [
            a.action_id for group in removals.values() for a in group if a.action_id not in covered
        ]
        if unlinked:
            self._issue(
                "removal_without_separation",
                "file",
                self._table("actions").file,
                f"{len(unlinked)} removal(s) have no separation record linked to them, so no "
                f"replacement (turnover) cost is counted for them. This is usually the largest "
                f"cost of a removal. Supply separations.csv, or separation dates and reasons in "
                f"the employee file: {', '.join(sorted(unlinked)[:10])}"
                + (" ..." if len(unlinked) > 10 else ""),
                "linked_action_id",
                severity=Severity.WARNING,
            )
        return out

    def _issue(
        self,
        rule_id,
        entity,
        entity_id,
        message,
        field_name=None,
        blocks=(),
        severity=Severity.BLOCKING,
    ) -> None:
        self._report.add(
            Issue(
                rule_id=rule_id,
                severity=severity,
                entity=entity,
                entity_id=entity_id,
                message=message,
                field=field_name,
                blocks_action_ids=tuple(blocks),
            )
        )

    # -- the organization, from the data -------------------------------------
    def _category(self, raw: str) -> str:
        return self._categories.get(raw) or self._categories.get(_norm(raw)) or _slug(raw)

    def _build_org(self, employee_rows: list[dict], action_rows: list[dict]) -> OrgConfig:
        base = self._base.raw
        families: dict[str, list[dict]] = {}
        for row in employee_rows:
            if row["job_family"]:
                families.setdefault(_slug(row["job_family"]), []).append(row)

        def usable_salary(row: dict, name: str) -> Decimal | None:
            try:
                value = self._number(row[name], name)
            except _RowError:
                return None
            return value if value > 0 else None

        departments: dict[str, dict] = {}
        grades: dict[str, dict] = {}
        units = {"non_represented": NON_REPRESENTED}
        for row in employee_rows:
            if row["bargaining_unit"]:
                units.setdefault(_slug(row["bargaining_unit"]), row["bargaining_unit"])
        for family_id, rows in families.items():
            department = _mode([r["department"] or "Unspecified" for r in rows])
            entry = [s for r in rows if (s := usable_salary(r, "entry_annual_salary"))]
            current = [
                s
                for r in rows
                if not r["separation_date"] and (s := usable_salary(r, "annual_base_salary"))
            ] or [s for r in rows if (s := usable_salary(r, "annual_base_salary"))]
            if entry:
                grades[family_id] = {"step1_base": str(_mode(entry))}
            elif current:
                grades[family_id] = {"step1_base": str(min(current))}
            sworn = _mode([r["is_sworn"].upper() in _TRUE for r in rows])
            dept = departments.setdefault(
                _slug(department),
                {
                    "id": _slug(department),
                    "name": department,
                    "bargaining_unit": "non_represented",
                    "locations": [],
                    "role_families": [],
                },
            )
            for location in dict.fromkeys(r["work_location"] for r in rows if r["work_location"]):
                if location not in dept["locations"]:
                    dept["locations"].append(location)
            dept["role_families"].append(
                {
                    "id": family_id,
                    "name": _mode([r["job_family"] for r in rows]),
                    "job_titles": list(
                        dict.fromkeys(r["job_title"] for r in rows if r["job_title"])
                    ),
                    "job_class_code": _mode([r["job_class_code"] for r in rows]),
                    "pay_grade": family_id,
                    "headcount": len(rows),
                    "is_sworn": sworn,
                    "minimum_staffing_role": _mode(
                        [r["minimum_staffing_role"].upper() in _TRUE for r in rows]
                    ),
                    "flsa_status": _mode([r["flsa_status"] for r in rows]),
                    "schedule": SCHEDULES.get(
                        _mode([_norm(r["work_schedule"]).replace(" ", "_") for r in rows]),
                        "civilian_5x8",
                    ),
                    "benefits_group": self._benefits_group(sworn),
                    "turnover_profile": _mode([r["turnover_profile"] for r in rows]),
                    "is_supervisory": _mode([r["is_supervisor"].upper() in _TRUE for r in rows]),
                }
            )

        taxonomy = copy.deepcopy(base["taxonomies"])
        by_id = {c["id"]: c for c in taxonomy["misconduct_categories"]}
        custom: dict[str, str] = {}
        for row in action_rows:
            raw = row["misconduct_category"]
            if not raw:
                continue
            category_id = self._category(raw)
            if category_id not in by_id:
                by_id[category_id] = {
                    "id": category_id,
                    "label": raw,
                    "sworn_only": False,
                    "subtypes": [],
                }
                taxonomy["misconduct_categories"].append(by_id[category_id])
                custom[category_id] = raw
            subtype = row["misconduct_subtype"] or "Unspecified"
            if subtype not in by_id[category_id]["subtypes"]:
                by_id[category_id]["subtypes"].append(subtype)
        for category_id, label in custom.items():
            self._issue(
                "custom_misconduct_category",
                "taxonomy",
                category_id,
                f"{label!r} is not a standard misconduct category; it is kept as a custom one. "
                f"Map it to a standard category if one fits, so results compare across clients.",
                "misconduct_category",
                severity=Severity.WARNING,
            )

        return OrgConfig(
            {
                "agency": {"id": _slug(self.agency_name), "name": self.agency_name},
                "schedules": copy.deepcopy(base["schedules"]),
                "holidays": list(base.get("holidays", [])),
                "pay_plan": {"steps": 20, "step_increment_pct": 0, "grades": grades},
                "bargaining_units": [{"id": k, "name": v} for k, v in units.items()],
                "departments": list(departments.values()),
                "taxonomies": taxonomy,
            }
        )

    # -- rows --------------------------------------------------------------
    def _load_employees(self, rows: list[dict]) -> tuple[list[Employee], dict]:
        out: list[Employee] = []
        problems: dict[str, tuple[str, str, str | None]] = {}
        #: The first row seen for each employee id, to tell a harmless repeat from a conflict.
        first_row: dict[str, dict] = {}
        repeats: Counter = Counter()
        for index, row in enumerate(rows, start=2):
            employee_id = row["employee_id"] or f"(row {index})"
            if row["employee_id"] and employee_id in first_row:
                if row == first_row[employee_id]:
                    # An exact repeat (a common export artefact) changes nothing; drop it.
                    repeats[employee_id] += 1
                    continue
                # Two rows that disagree: there is no way to know which salary or job is
                # right, so the employee is not used at all.
                out = [e for e in out if e.employee_id != employee_id]
                problems[employee_id] = (
                    "conflicting_employee_rows",
                    f"employee_id {employee_id} appears more than once with different values",
                    "employee_id",
                )
                continue
            first_row[employee_id] = row
            try:
                if not row["employee_id"]:
                    raise _RowError("employee_id is blank", "employee_id")
                for name in ("department", "job_family", "work_location"):
                    if not row[name]:
                        raise _RowError(f"{name} is blank", name)
                family_id = _slug(row["job_family"])
                role = self.org.role_families[family_id]
                if family_id not in self.org.raw["pay_plan"]["grades"]:
                    raise _RowError(
                        f"job family {row['job_family']!r} has no usable salary to price a "
                        f"replacement",
                        "annual_base_salary",
                    )
                salary = self._number(row["annual_base_salary"], "annual_base_salary")
                if salary <= 0:
                    raise _RowError(
                        "annual_base_salary must be more than zero", "annual_base_salary"
                    )
                schedule_id = SCHEDULES[
                    self._choice(row["work_schedule"], "work_schedule", "employees")
                ]
                schedule = self.org.schedules[schedule_id]
                sworn = self._bool(row["is_sworn"], "is_sworn")
                separation_reason = (
                    self._choice(row["separation_reason"], "separation_reason", "employees")
                    if row["separation_reason"]
                    else None
                )
                employee = Employee(
                    employee_id=employee_id,
                    department=row["department"],
                    role_family=family_id,
                    job_title=row["job_title"] or role.name,
                    job_class_code=row["job_class_code"] or family_id,
                    pay_grade=row["pay_grade"] or family_id,
                    pay_step=int(self._number(row["pay_step"], "pay_step", Decimal(1))),
                    annual_base_salary=salary,
                    hourly_base_rate=salary / Decimal(schedule.annual_paid_hours),
                    flsa_status=self._choice(row["flsa_status"], "flsa_status", "employees"),
                    bargaining_unit=row["bargaining_unit"] or NON_REPRESENTED,
                    is_sworn=sworn,
                    minimum_staffing_role=self._bool(
                        row["minimum_staffing_role"], "minimum_staffing_role"
                    ),
                    standard_shift_hours=self._number(
                        row["standard_shift_hours"], "standard_shift_hours", schedule.shift_hours
                    ),
                    annual_paid_hours=schedule.annual_paid_hours,
                    schedule_id=schedule_id,
                    benefits_group=self._benefits_group(sworn),
                    turnover_profile=self._turnover_profile(row["turnover_profile"], employee_id),
                    coverage_method=self._choice(
                        row["coverage_method"],
                        "coverage_method",
                        "employees",
                        default=CoverageMethod.OVERTIME,
                    ),
                    work_location=row["work_location"],
                    supervisor_id=row["supervisor_id"] or None,
                    hire_date=self._date(row["hire_date"], "hire_date", required=True),
                    separation_date=self._date(row["separation_date"], "separation_date"),
                    separation_reason=separation_reason,
                )
            except _RowError as exc:
                rule = (
                    "missing_salary"
                    if exc.field_name == "annual_base_salary"
                    else "unusable_employee_row"
                )
                problems[employee_id] = (rule, str(exc), exc.field_name)
                continue
            except Exception as exc:  # noqa: BLE001 - any bad row becomes a reported issue
                problems[employee_id] = ("unusable_employee_row", str(exc), None)
                continue
            out.append(employee)
        if repeats:
            self._issue(
                "duplicate_employee_rows",
                "file",
                self._table("employees").file,
                f"{sum(repeats.values())} exact repeat row(s) for {len(repeats)} employee(s) "
                f"were ignored: {', '.join(sorted(repeats)[:10])}",
                "employee_id",
                severity=Severity.WARNING,
            )
        return out, problems

    def _load_actions(self, rows: list[dict]) -> list[DisciplineAction]:
        out: list[DisciplineAction] = []
        defaulted = 0
        defaulted_investigation: list[str] = []
        seen: set[str] = set()
        for index, row in enumerate(rows, start=2):
            action_id = row["action_id"] or f"(row {index})"
            try:
                if not row["action_id"]:
                    raise _RowError("action_id is blank", "action_id")
                if action_id in seen:
                    raise _RowError(f"action_id {action_id} appears more than once", "action_id")
                if not row["misconduct_category"]:
                    raise _RowError("misconduct_category is blank", "misconduct_category")
                days = int(self._number(row["suspension_days"], "suspension_days", Decimal(0)))
                kind = self._choice(row["action_type"], "action_type", "actions")
                if kind == "suspension":
                    if days < 1:
                        raise _RowError(
                            "a suspension needs suspension_days of 1 or more", "suspension_days"
                        )
                    kind = (
                        "suspension_1_3"
                        if days <= 3
                        else "suspension_4_14"
                        if days <= 14
                        else "suspension_15_plus"
                    )
                action_type = ActionType(kind)
                decision = self._date(row["decision_date"], "decision_date", required=True)
                investigated = self._bool(row["was_investigated"], "was_investigated", False)
                investigation = None
                if investigated and row["investigation_type"]:
                    investigation = self._choice(
                        row["investigation_type"], "investigation_type", "actions"
                    )
                elif investigated:
                    # The lightest type, so the cost is a floor rather than a guess upward.
                    investigation = InvestigationType.SUPERVISORY
                    defaulted_investigation.append(action_id)
                if row["deciding_official_level"]:
                    level = self._choice(
                        row["deciding_official_level"], "deciding_official_level", "actions"
                    )
                else:
                    level = _DEFAULT_DECIDER[action_type]
                    defaulted += 1
                action = DisciplineAction(
                    action_id=action_id,
                    employee_id=row["employee_id"],
                    incident_date=self._date(row["incident_date"], "incident_date") or decision,
                    proposal_date=self._date(row["proposal_date"], "proposal_date"),
                    decision_date=decision,
                    misconduct_category=self._category(row["misconduct_category"]),
                    misconduct_subtype=row["misconduct_subtype"] or "Unspecified",
                    action_type=action_type,
                    suspension_days=days,
                    suspension_start_date=self._date(
                        row["suspension_start_date"], "suspension_start_date"
                    ),
                    was_investigated=investigated,
                    investigation_type=investigation,
                    deciding_official_level=level,
                    deciding_official_id=row["deciding_official_id"] or None,
                    position_abolished=self._bool(
                        row["position_abolished"], "position_abolished", False
                    ),
                    outside_investigator_hours=self._number(
                        row["outside_investigator_hours"],
                        "outside_investigator_hours",
                        Decimal(0),
                    ),
                    # Blank means the deduction was made under a written policy, which is
                    # the ordinary case and the one that keeps the unpaid-suspension credit.
                    # Assuming otherwise would withhold a credit the employer did take.
                    written_conduct_policy=self._bool(
                        row["written_conduct_policy"], "written_conduct_policy", True
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                self._issue(
                    "unusable_action_row",
                    "action",
                    action_id,
                    str(exc),
                    getattr(exc, "field_name", None),
                    (action_id,) if row["action_id"] else (),
                )
                continue
            seen.add(action_id)
            out.append(action)
        if defaulted:
            self._issue(
                "defaulted_deciding_official_level",
                "file",
                self._table("actions").file,
                f"{defaulted} action(s) did not say who decided them. The level was filled in "
                f"from the action type (supervisor for counseling and reprimands, division "
                f"head for shorter suspensions, department head above that).",
                "deciding_official_level",
                severity=Severity.WARNING,
            )
        if defaulted_investigation:
            self._issue(
                "defaulted_investigation_type",
                "file",
                self._table("actions").file,
                f"{len(defaulted_investigation)} investigated action(s) did not say what kind of "
                f"investigation. Each was costed as a supervisory inquiry, the lightest type, so "
                f"the investigation cost is a floor. Map investigation_type to raise it: "
                f"{', '.join(defaulted_investigation[:10])}"
                + (" ..." if len(defaulted_investigation) > 10 else ""),
                "investigation_type",
                severity=Severity.WARNING,
            )
        return out

    def _load_leave(self, rows: list[dict]) -> list[AdminLeavePeriod]:
        out: list[AdminLeavePeriod] = []
        for index, row in enumerate(rows, start=2):
            leave_id = row["leave_id"] or f"LV-{index - 1:05d}"
            try:
                out.append(
                    AdminLeavePeriod(
                        leave_id=leave_id,
                        employee_id=row["employee_id"],
                        action_id=row["action_id"] or None,
                        start_date=self._date(row["start_date"], "start_date", required=True),
                        end_date=self._date(row["end_date"], "end_date", required=True),
                        paid=self._bool(row["paid"], "paid", True),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._issue(
                    "unusable_leave_row",
                    "admin_leave",
                    leave_id,
                    str(exc),
                    getattr(exc, "field_name", None),
                    (row["action_id"],) if row["action_id"] else (),
                )
        return out

    def _load_appeals(self, rows: list[dict]) -> list[AppealOrGrievance]:
        out: list[AppealOrGrievance] = []
        zero = Decimal(0)
        for index, row in enumerate(rows, start=2):
            appeal_id = row["appeal_id"] or f"AP-{index - 1:05d}"
            try:
                out.append(
                    AppealOrGrievance(
                        appeal_id=appeal_id,
                        action_id=row["action_id"],
                        forum=self._choice(row["forum"], "forum", "appeals"),
                        filed_date=self._date(row["filed_date"], "filed_date", required=True),
                        resolution_date=self._date(row["resolution_date"], "resolution_date"),
                        outcome=self._choice(row["outcome"], "outcome", "appeals"),
                        back_pay_awarded=self._number(
                            row["back_pay_awarded"], "back_pay_awarded", zero
                        ),
                        settlement_amount=self._number(
                            row["settlement_amount"], "settlement_amount", zero
                        ),
                        outside_counsel_hours=self._number(
                            row["outside_counsel_hours"], "outside_counsel_hours", zero
                        ),
                        insurance_retention_paid=self._number(
                            row["insurance_retention_paid"], "insurance_retention_paid", zero
                        ),
                        insurer_paid_amount=self._number(
                            row["insurer_paid_amount"], "insurer_paid_amount", zero
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._issue(
                    "unusable_appeal_row",
                    "appeal",
                    appeal_id,
                    str(exc),
                    getattr(exc, "field_name", None),
                )
        return out

    def _load_separations(self, rows: list[dict]) -> list[Separation]:
        out: list[Separation] = []
        for index, row in enumerate(rows, start=2):
            employee_id = row["employee_id"] or f"(row {index})"
            try:
                out.append(
                    Separation(
                        employee_id=row["employee_id"],
                        separation_date=self._date(
                            row["separation_date"], "separation_date", required=True
                        ),
                        separation_reason=self._choice(
                            row["separation_reason"], "separation_reason", "separations"
                        ),
                        linked_action_id=row["linked_action_id"] or None,
                        position_refilled=self._bool(
                            row["position_refilled"], "position_refilled", False
                        ),
                        refill_date=self._date(row["refill_date"], "refill_date"),
                        position_abolished=self._bool(
                            row["position_abolished"], "position_abolished", False
                        ),
                        separation_agreement=self._bool(
                            row["separation_agreement"], "separation_agreement", False
                        ),
                        severance_amount=self._number(
                            row["severance_amount"], "severance_amount", Decimal(0)
                        ),
                        unemployment_claim_filed=self._bool(
                            row["unemployment_claim_filed"], "unemployment_claim_filed", False
                        ),
                        unemployment_benefits_charged=self._number(
                            row["unemployment_benefits_charged"],
                            "unemployment_benefits_charged",
                            Decimal(0),
                        ),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                self._issue(
                    "unusable_separation_row",
                    "separation",
                    employee_id,
                    str(exc),
                    getattr(exc, "field_name", None),
                    (row["linked_action_id"],) if row["linked_action_id"] else (),
                )
        return out
