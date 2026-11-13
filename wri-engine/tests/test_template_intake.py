"""The standard intake template, the mapping layer and the intake endpoints.

The central claim is that a client's data, arriving in the template layout or in its own
layout with a mapping, costs to exactly the same total as the hand-built county adapter
produces for the same records. Harlow County's raw export stands in for "a client's own
layout": to the template adapter it is just a set of files with unfamiliar column names and
codes.
"""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import date
from decimal import Decimal

import pytest
import yaml
from fastapi.testclient import TestClient

from template_fixture import write_template
from test_accounts_engagements import ANALYST, CLIENT, LEAD, SAMPLE, Installation
from wri_engine.adapters.base import Severity
from wri_engine.adapters.county_hr_csv import DEFAULT_MAPPING
from wri_engine.adapters.template import (
    NON_REPRESENTED,
    SCHEDULES,
    TABLES,
    MappingError,
    TemplateAdapter,
    parse_mapping,
    suggest_mapping,
    template_guide,
    template_zip,
)
from wri_engine.api.main import create_app
from wri_engine.api.state import SOURCE_MARKER
from wri_engine.costing.engine import run_costing
from wri_engine.schema import CoverageMethod
from wri_engine.settings import Settings

AS_OF = date(2026, 9, 17)


def cost(adapter, data, assumptions):
    return run_costing(
        data,
        as_of=AS_OF,
        org=adapter.org,
        assumptions=assumptions,
        excluded_action_ids=adapter.report.blocked_action_ids,
    )


def harlow_mapping(org) -> dict:
    """Describe Harlow County's export to the template adapter, as a consultant would for
    any client: which column feeds each template field, and what the codes mean."""
    with open(DEFAULT_MAPPING) as fh:
        county = yaml.safe_load(fh)
    codes = county["codes"]
    schedule_name = {v: k for k, v in SCHEDULES.items()}
    by_class = {
        code: org.role_families[role_id]
        for code, role_id in county["job_class_to_role_family"].items()
        if role_id in org.role_families
    }

    def per_class(fn) -> dict:
        return {code: str(fn(role)) for code, role in by_class.items()}

    return {
        "date_format": county["source"]["date_format"],
        "tables": {
            "employees": {
                "file": county["files"]["employees"],
                "columns": {
                    "employee_id": "EMP_NBR",
                    "department": "DEPT_CD",
                    "job_family": "CLASS_CD",
                    "job_title": "POSN_TITLE",
                    "job_class_code": "CLASS_CD",
                    "pay_grade": "GRADE",
                    "pay_step": "STEP",
                    "annual_base_salary": "ANNL_SAL",
                    "entry_annual_salary": "CLASS_CD",
                    "flsa_status": "FLSA_CD",
                    "bargaining_unit": "BARG_CD",
                    "is_sworn": "SWORN_IND",
                    "minimum_staffing_role": "MIN_STAFF_IND",
                    "work_schedule": "CLASS_CD",
                    "standard_shift_hours": "SHIFT_HRS",
                    "turnover_profile": "CLASS_CD",
                    "is_supervisor": "CLASS_CD",
                    "work_location": "WORK_LOC",
                    "supervisor_id": "SUPV_EMP_NBR",
                    "hire_date": "HIRE_DT",
                    "separation_date": "TERM_DT",
                    "separation_reason": "TERM_RSN_CD",
                },
                "values": {
                    "department": {
                        k: org.departments[v]["name"] for k, v in codes["department"].items()
                    },
                    "job_family": per_class(lambda r: r.id),
                    "entry_annual_salary": per_class(lambda r: org.salary_for(r.pay_grade, 1)),
                    "work_schedule": per_class(lambda r: schedule_name[r.schedule_id]),
                    "turnover_profile": per_class(lambda r: r.turnover_profile),
                    "is_supervisor": per_class(lambda r: "Y" if r.is_supervisory else "N"),
                    "flsa_status": codes["flsa"],
                    "bargaining_unit": {
                        k: org.bargaining_units[v] for k, v in codes["bargaining_unit"].items()
                    },
                    "separation_reason": codes["separation_reason"],
                },
            },
            "actions": {
                "file": county["files"]["actions"],
                "columns": {
                    "action_id": "ACTN_NBR",
                    "employee_id": "EMP_NBR",
                    "incident_date": "INCDT_DT",
                    "proposal_date": "PROP_DT",
                    "decision_date": "DECN_DT",
                    "misconduct_category": "MISCND_CD",
                    "misconduct_subtype": "MISCND_SUB",
                    "action_type": "ACTN_CD",
                    "suspension_days": "SUSP_DAYS",
                    "suspension_start_date": "SUSP_BEG_DT",
                    "was_investigated": "INVEST_IND",
                    "investigation_type": "INVEST_TYP",
                    "deciding_official_level": "DECIDE_LVL",
                    "deciding_official_id": "DECIDE_EMP_NBR",
                    "position_abolished": "POSN_ABOLISH_IND",
                },
                "values": {
                    "misconduct_category": codes["misconduct"],
                    "action_type": codes["action_type"],
                    "investigation_type": codes["investigation"],
                    "deciding_official_level": codes["deciding_official_level"],
                },
            },
            "admin_leave": {
                "file": county["files"]["admin_leave"],
                "columns": {
                    "leave_id": "LEAVE_NBR",
                    "employee_id": "EMP_NBR",
                    "action_id": "ACTN_NBR",
                    "start_date": "BEG_DT",
                    "end_date": "END_DT",
                    "paid": "PAID_IND",
                },
            },
            "appeals": {
                "file": county["files"]["appeals"],
                "columns": {
                    "appeal_id": "APPL_NBR",
                    "action_id": "ACTN_NBR",
                    "forum": "FORUM_CD",
                    "filed_date": "FILED_DT",
                    "resolution_date": "RESOLV_DT",
                    "outcome": "OUTCOME_CD",
                    "back_pay_awarded": "BACKPAY_AMT",
                    "settlement_amount": "SETTLE_AMT",
                    "outside_counsel_hours": "OC_HOURS",
                },
                "values": {"forum": codes["appeal_forum"], "outcome": codes["appeal_outcome"]},
            },
            "separations": {
                "file": county["files"]["separations"],
                "columns": {
                    "employee_id": "EMP_NBR",
                    "separation_date": "TERM_DT",
                    "separation_reason": "TERM_RSN_CD",
                    "linked_action_id": "ACTN_NBR",
                    "position_refilled": "REFILL_IND",
                    "refill_date": "REFILL_DT",
                    "position_abolished": "ABOLISH_IND",
                },
                "values": {"separation_reason": codes["separation_reason"]},
            },
        },
    }


# ---------------------------------------------------------------------------
# the central claim: same records, same total
# ---------------------------------------------------------------------------
def test_template_layout_costs_to_the_same_cent_as_the_county_adapter(
    loaded, run, org, assumptions, tmp_path
):
    data, _ = loaded
    adapter = TemplateAdapter(agency_name="Harlow County")
    template_data = adapter.load(write_template(data, org, tmp_path))
    template_run = cost(adapter, template_data, assumptions)

    assert len(template_run.action_costs) == len(run.action_costs) > 100
    assert template_run.net == run.net
    assert template_run.gross == run.gross
    county, template = run.by_action(), template_run.by_action()
    assert set(county) == set(template)
    assert all(county[k].net == template[k].net for k in county)


def test_a_foreign_layout_with_a_mapping_costs_to_the_same_cent(run, org, assumptions, sample_dir):
    adapter = TemplateAdapter(mapping=harlow_mapping(org), agency_name="Harlow County")
    data = adapter.load(sample_dir)
    mapped_run = cost(adapter, data, assumptions)

    assert mapped_run.net == run.net
    assert mapped_run.offset == run.offset
    assert {a.action_id for a in mapped_run.action_costs} == {a.action_id for a in run.action_costs}
    county, mapped = run.by_action(), mapped_run.by_action()
    worst = max(abs(county[k].net - mapped[k].net) for k in county)
    assert worst == Decimal(0)


def test_the_organization_is_built_from_the_data(loaded, org, tmp_path):
    data, _ = loaded
    adapter = TemplateAdapter(agency_name="Riverbend County")
    adapter.load(write_template(data, org, tmp_path))
    built = adapter.org
    assert built.agency_name == "Riverbend County"
    assert {d["name"] for d in built.departments.values()} == {e.department for e in data.employees}
    assert set(built.role_families) >= {e.role_family for e in data.employees} or len(
        built.role_families
    ) == len({e.role_family for e in data.employees})
    assert built.bargaining_units["non_represented"] == NON_REPRESENTED
    assert set(built.schedules) == set(org.schedules)


# ---------------------------------------------------------------------------
# the template itself
# ---------------------------------------------------------------------------
def test_blank_template_zip_has_every_file_with_its_headers():
    archive = zipfile.ZipFile(io.BytesIO(template_zip()))
    names = set(archive.namelist())
    assert names == {"READ_ME_FIRST.txt", *(t.file for t in TABLES.values())}
    for table in TABLES.values():
        header = archive.read(table.file).decode().strip().split(",")
        assert header == table.field_names
    guide = archive.read("READ_ME_FIRST.txt").decode()
    assert guide == template_guide()
    assert "Social Security" in guide and "attendance_awol" in guide
    for table in TABLES.values():
        for spec in table.fields:
            assert spec.name in guide


def test_only_employees_and_actions_are_required():
    assert {t.name for t in TABLES.values() if t.required} == {"employees", "actions"}


# ---------------------------------------------------------------------------
# small files, written by hand
# ---------------------------------------------------------------------------
EMPLOYEE = {
    "employee_id": "E1",
    "department": "Distribution",
    "job_family": "Warehouse Associate",
    "annual_base_salary": "41600",
    "flsa_status": "non_exempt",
    "is_sworn": "N",
    "minimum_staffing_role": "N",
    "work_schedule": "weekday_8",
    "turnover_profile": "civilian_standard",
    "work_location": "Plant 1",
    "hire_date": "2020-03-02",
}
ACTION = {
    "action_id": "A1",
    "employee_id": "E1",
    "decision_date": "2025-06-10",
    "misconduct_category": "tardiness",
    "action_type": "written_reprimand",
}


def write(directory, table, rows, file=None):
    fields = list(dict.fromkeys(k for row in rows for k in row))
    with open(directory / (file or TABLES[table].file), "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load(tmp_path, employees, actions, mapping=None, sector=None):
    write(tmp_path, "employees", employees)
    write(tmp_path, "actions", actions)
    adapter = TemplateAdapter(mapping=mapping, sector=sector)
    return adapter, adapter.load(tmp_path)


def test_a_minimal_file_loads_with_sensible_defaults(tmp_path, assumptions):
    adapter, data = load(tmp_path, [EMPLOYEE], [ACTION])
    counts = adapter.report.counts_by_rule()
    assert adapter.report.blocking == []
    # The three optional files were not supplied, and no one said who decided the action.
    assert counts == {"defaulted_deciding_official_level": 1, "missing_source_file": 3}
    employee, action = data.employees[0], data.actions[0]
    assert employee.bargaining_unit == NON_REPRESENTED
    assert employee.benefits_group == "civilian"
    assert employee.hourly_base_rate == Decimal("20")
    assert employee.standard_shift_hours == Decimal("8")
    assert action.incident_date == action.decision_date
    assert str(action.deciding_official_level) == "supervisor"
    assert action.misconduct_subtype == "Unspecified"
    result = cost(adapter, data, assumptions)
    assert len(result.action_costs) == 1 and result.net > 0


def test_entry_salary_defaults_to_the_lowest_current_salary_in_the_job_family(tmp_path):
    rows = [
        {**EMPLOYEE, "employee_id": "E1", "annual_base_salary": "52000"},
        {**EMPLOYEE, "employee_id": "E2", "annual_base_salary": "41600"},
        {
            **EMPLOYEE,
            "employee_id": "E3",
            "annual_base_salary": "30000",
            "separation_date": "2024-01-05",
        },
    ]
    adapter, _ = load(tmp_path, rows, [ACTION])
    assert adapter.org.salary_for("warehouse_associate", 1) == Decimal("41600")

    for row in rows:
        row["entry_annual_salary"] = "39000"
    adapter, _ = load(tmp_path, rows, [ACTION])
    assert adapter.org.salary_for("warehouse_associate", 1) == Decimal("39000")


@pytest.mark.parametrize(
    ("days", "expected"),
    [
        (1, "suspension_1_3"),
        (3, "suspension_1_3"),
        (4, "suspension_4_14"),
        (14, "suspension_4_14"),
        (15, "suspension_15_plus"),
    ],
)
def test_a_plain_suspension_is_banded_by_its_days(tmp_path, days, expected):
    action = {**ACTION, "action_type": "suspension", "suspension_days": str(days)}
    _, data = load(tmp_path, [EMPLOYEE], [action])
    assert str(data.actions[0].action_type) == expected


def test_a_suspension_without_days_is_reported_not_guessed(tmp_path):
    adapter, data = load(tmp_path, [EMPLOYEE], [{**ACTION, "action_type": "suspension"}])
    assert data.actions == []
    issue = next(i for i in adapter.report.blocking if i.rule_id == "unusable_action_row")
    assert issue.field == "suspension_days"
    assert issue.blocks_action_ids == ("A1",)


def test_an_unfamiliar_misconduct_category_is_kept_and_flagged(tmp_path, assumptions):
    actions = [
        {**ACTION, "misconduct_category": "Tardiness"},
        {**ACTION, "action_id": "A2", "misconduct_category": "Safety lockout violation"},
    ]
    adapter, data = load(tmp_path, [EMPLOYEE], actions)
    assert [a.misconduct_category for a in data.actions] == [
        "tardiness",
        "safety_lockout_violation",
    ]
    flagged = [i for i in adapter.report.warnings if i.rule_id == "custom_misconduct_category"]
    assert [i.entity_id for i in flagged] == ["safety_lockout_violation"]
    assert adapter.org.misconduct_categories["safety_lockout_violation"].label == (
        "Safety lockout violation"
    )
    assert len(cost(adapter, data, assumptions).action_costs) == 2


def test_bad_rows_are_reported_with_the_field_and_block_their_actions(tmp_path):
    employees = [
        EMPLOYEE,
        {**EMPLOYEE, "employee_id": "E2", "annual_base_salary": ""},
        {**EMPLOYEE, "employee_id": "E3", "flsa_status": "hourly"},
        {**EMPLOYEE, "employee_id": "E1"},
        {**EMPLOYEE, "employee_id": "E5", "hire_date": "03/02/2020x"},
    ]
    actions = [
        ACTION,
        {**ACTION, "action_id": "A2", "employee_id": "E2"},
        {**ACTION, "action_id": "A3", "employee_id": "E3"},
        {**ACTION, "action_id": "A4", "decision_date": ""},
        {**ACTION, "action_id": "A5", "employee_id": "nobody"},
    ]
    adapter, data = load(tmp_path, employees, actions)
    assert [e.employee_id for e in data.employees] == ["E1"]
    by_entity = {(i.entity, i.entity_id): i for i in adapter.report.blocking}
    assert by_entity[("employee", "E2")].rule_id == "missing_salary"
    assert by_entity[("employee", "E2")].blocks_action_ids == ("A2",)
    assert by_entity[("employee", "E3")].field == "flsa_status"
    assert "Add it to the mapping" in by_entity[("employee", "E3")].message
    assert by_entity[("employee", "E5")].field == "hire_date"
    assert by_entity[("action", "A4")].field == "decision_date"
    assert by_entity[("action", "A5")].rule_id == "orphan_action"
    assert adapter.report.blocked_action_ids == {"A2", "A3", "A4", "A5"}


def test_an_exact_repeat_row_is_ignored_with_a_warning(tmp_path):
    adapter, data = load(tmp_path, [EMPLOYEE, dict(EMPLOYEE)], [ACTION])
    assert [e.employee_id for e in data.employees] == ["E1"]
    assert adapter.report.blocked_action_ids == set()
    repeat = next(i for i in adapter.report.warnings if i.rule_id == "duplicate_employee_rows")
    assert "E1" in repeat.message


def test_rows_that_disagree_drop_the_employee_and_block_their_actions(tmp_path):
    other_salary = {**EMPLOYEE, "annual_base_salary": "99000"}
    adapter, data = load(tmp_path, [EMPLOYEE, other_salary, dict(EMPLOYEE)], [ACTION])
    assert data.employees == []
    issue = next(i for i in adapter.report.blocking if i.entity_id == "E1")
    assert issue.rule_id == "conflicting_employee_rows"
    assert issue.blocks_action_ids == ("A1",)
    assert not any(i.rule_id == "orphan_action" for i in adapter.report.issues)


def test_a_missing_required_column_is_named(tmp_path):
    employee = {k: v for k, v in EMPLOYEE.items() if k != "turnover_profile"}
    adapter, data = load(tmp_path, [employee], [ACTION])
    missing = [i for i in adapter.report.blocking if i.rule_id == "missing_required_column"]
    assert [(i.entity_id, i.field) for i in missing] == [("employees.csv", "turnover_profile")]
    assert data.employees == []


def test_a_mapping_renames_columns_translates_codes_and_fills_constants(tmp_path):
    write(
        tmp_path,
        "employees",
        [
            {
                "Worker ID": "E1",
                "Dept": "Distribution",
                "Job Group": "WA",
                "Salary": "$41,600.00",
                "OT": "H",
                "Site": "Plant 1",
                "Start": "03/02/2020",
            }
        ],
        file="workers.csv",
    )
    write(
        tmp_path,
        "actions",
        [
            {
                "Case": "A1",
                "Worker ID": "E1",
                "Date": "06/10/2025",
                "Offense": "LATE",
                "Penalty": "WW",
            }
        ],
        file="cases.csv",
    )
    mapping = {
        "date_format": "%m/%d/%Y",
        "tables": {
            "employees": {
                "file": "workers.csv",
                "columns": {
                    "employee_id": "Worker ID",
                    "department": "Dept",
                    "job_family": "Job Group",
                    "annual_base_salary": "Salary",
                    "flsa_status": "OT",
                    "work_location": "Site",
                    "hire_date": "Start",
                },
                "values": {
                    "job_family": {"WA": "Warehouse Associate"},
                    "flsa_status": {"H": "non_exempt", "S": "exempt"},
                },
                "constants": {
                    "is_sworn": "N",
                    "minimum_staffing_role": "N",
                    "work_schedule": "weekday_8",
                    "turnover_profile": "civilian_standard",
                },
            },
            "actions": {
                "file": "cases.csv",
                "columns": {
                    "action_id": "Case",
                    "employee_id": "Worker ID",
                    "decision_date": "Date",
                    "misconduct_category": "Offense",
                    "action_type": "Penalty",
                },
                "values": {
                    "misconduct_category": {"LATE": "tardiness"},
                    "action_type": {"WW": "written_reprimand"},
                },
            },
        },
    }
    adapter = TemplateAdapter(mapping=mapping)
    data = adapter.load(tmp_path)
    assert adapter.report.blocking == []
    employee, action = data.employees[0], data.actions[0]
    assert (employee.role_family, employee.annual_base_salary) == (
        "warehouse_associate",
        Decimal("41600.00"),
    )
    assert employee.hire_date == date(2020, 3, 2)
    assert str(employee.flsa_status) == "non_exempt"
    assert (action.misconduct_category, str(action.action_type)) == (
        "tardiness",
        "written_reprimand",
    )
    assert action.decision_date == date(2025, 6, 10)


@pytest.mark.parametrize(
    "bad",
    [
        {"tables": {"people": {}}},
        {"tables": {"employees": {"columns": {"employe_id": "X"}}}},
        {"tables": {"employees": {"values": {"shoe_size": {"1": "2"}}}}},
        {"tables": {"employees": {"file": "../../etc/passwd.csv"}}},
        {"tables": {"employees": {"file": "workers.xlsx"}}},
        {"date_format": "MM/DD/YYYY"},
        {"tables": ["employees"]},
    ],
)
def test_a_malformed_mapping_is_refused(bad):
    with pytest.raises(MappingError):
        parse_mapping(bad)


def test_a_mapping_is_suggested_from_the_column_names(sample_dir):
    suggestion = suggest_mapping(sample_dir)
    tables = suggestion["suggested_mapping"]["tables"]
    assert tables["employees"]["file"] == "hr_empl_master.csv"
    assert tables["actions"]["file"] == "hr_disc_actn.csv"
    assert tables["employees"]["columns"]["employee_id"] == "EMP_NBR"
    assert tables["employees"]["columns"]["annual_base_salary"] == "ANNL_SAL"
    assert tables["employees"]["columns"]["hire_date"] == "HIRE_DT"
    assert tables["actions"]["columns"]["decision_date"] == "DECN_DT"
    assert tables["actions"]["columns"]["misconduct_category"] == "MISCND_CD"
    # It is a draft: what it could not place is listed for a person to finish.
    assert "job_family" in suggestion["tables"]["employees"]["missing_required_fields"]
    assert "CLASS_CD" in suggestion["tables"]["employees"]["unused_columns"] or (
        tables["employees"]["columns"].get("job_class_code") == "CLASS_CD"
    )
    parse_mapping(suggestion["suggested_mapping"])


def test_investigated_without_a_type_is_costed_as_the_lightest_type(tmp_path, assumptions):
    investigated = {**ACTION, "was_investigated": "Y"}
    adapter, data = load(tmp_path, [EMPLOYEE], [investigated])
    assert adapter.report.blocking == []
    assert data.actions[0].investigation_type == "supervisory"
    warning = next(
        i for i in adapter.report.warnings if i.rule_id == "defaulted_investigation_type"
    )
    assert "A1" in warning.message and "floor" in warning.message
    assert len(cost(adapter, data, assumptions).action_costs) == 1


REMOVAL = {**ACTION, "action_id": "R1", "action_type": "removal"}
LEFT = {**EMPLOYEE, "separation_date": "2025-06-12", "separation_reason": "removal_for_cause"}


def turnover(adapter, data, assumptions) -> Decimal:
    run = cost(adapter, data, assumptions)
    return sum((i.amount for i in run.line_items if i.component == "C5"), Decimal(0))


def test_a_removal_with_no_separation_record_is_reported(tmp_path, assumptions):
    adapter, data = load(tmp_path, [EMPLOYEE], [REMOVAL])
    warning = next(i for i in adapter.report.warnings if i.rule_id == "removal_without_separation")
    assert "R1" in warning.message and "turnover" in warning.message
    assert turnover(adapter, data, assumptions) == 0


def test_a_separation_in_the_employee_file_is_used_and_linked(tmp_path, assumptions):
    adapter, data = load(tmp_path, [LEFT], [REMOVAL])
    assert [(s.employee_id, s.linked_action_id) for s in data.separations] == [("E1", "R1")]
    rules = adapter.report.counts_by_rule()
    assert rules["separation_from_employee_file"] == 1
    assert rules["separation_linked_to_removal"] == 1
    assert "removal_without_separation" not in rules
    assert turnover(adapter, data, assumptions) > 0


def test_the_separations_file_wins_over_the_employee_file(tmp_path):
    write(
        tmp_path,
        "separations",
        [
            {
                "employee_id": "E1",
                "separation_date": "2025-06-20",
                "separation_reason": "removal_for_cause",
                "linked_action_id": "R1",
            }
        ],
    )
    adapter, data = load(tmp_path, [LEFT], [REMOVAL])
    assert [s.separation_date.isoformat() for s in data.separations] == ["2025-06-20"]
    assert "separation_from_employee_file" not in adapter.report.counts_by_rule()


def test_a_separation_is_not_guessed_onto_one_of_two_removals(tmp_path, assumptions):
    second = {**REMOVAL, "action_id": "R2", "decision_date": "2025-01-10"}
    adapter, data = load(tmp_path, [LEFT], [REMOVAL, second])
    assert data.separations[0].linked_action_id is None
    rules = adapter.report.counts_by_rule()
    assert rules["separation_link_ambiguous"] == 1
    assert rules["removal_without_separation"] == 1
    assert turnover(adapter, data, assumptions) == 0


def test_a_resignation_is_not_linked_to_a_removal(tmp_path):
    resigned = {**LEFT, "separation_reason": "voluntary_resignation"}
    adapter, data = load(tmp_path, [resigned], [REMOVAL])
    assert data.separations[0].linked_action_id is None


# ---------------------------------------------------------------------------
# the suggested mapping, on an export nobody planned for
# ---------------------------------------------------------------------------
ROSTER = [
    {
        "Worker #": f"W{n}",
        "Org Unit": "Roads",
        "Position Category": family,
        "Base Pay": "52,000",
        "Pay Type": flsa,
        "Sched": sched,
        "Yard": "North Yard",
        "Start Dt": "02/27/2011",
        "Supv": "N",
    }
    for n, (family, flsa, sched) in enumerate(
        [("Mechanic", "N", "D8"), ("Mechanic", "N", "D8"), ("Office Admin", "E", "D8")]
    )
]
LOG = [
    {
        "Case No": "DC-1",
        "Worker #": "W0",
        "Date of Decision": "07/27/2024",
        "Offense": "ATT",
        "Penalty": "WR",
        "Investigated?": "Y",
    },
    {
        "Case No": "DC-2",
        "Worker #": "W1",
        "Date of Decision": "08/25/2024",
        "Offense": "tardiness",
        "Penalty": "written_reprimand",
        "Investigated?": "N",
    },
]


def write_raw(directory, roster=ROSTER, log=LOG):
    write(directory, "employees", roster, file="Staff_Roster_FY26.csv")
    write(directory, "actions", log, file="Discipline_Log.csv")
    return directory


def test_abbreviated_column_names_are_matched(tmp_path):
    suggestion = suggest_mapping(write_raw(tmp_path))
    employees = suggestion["suggested_mapping"]["tables"]["employees"]
    actions = suggestion["suggested_mapping"]["tables"]["actions"]
    assert employees["file"] == "Staff_Roster_FY26.csv"
    assert {
        "employee_id": "Worker #",
        "department": "Org Unit",
        "job_family": "Position Category",
        "annual_base_salary": "Base Pay",
        "flsa_status": "Pay Type",
        "work_schedule": "Sched",
        "work_location": "Yard",
        "hire_date": "Start Dt",
        "is_supervisor": "Supv",
    }.items() <= employees["columns"].items()
    assert {
        "action_id": "Case No",
        "employee_id": "Worker #",
        "decision_date": "Date of Decision",
        "was_investigated": "Investigated?",
    }.items() <= actions["columns"].items()
    for table in ("employees", "actions"):
        assert suggestion["tables"][table]["missing_required_fields"] == []


def test_codes_that_need_translating_are_listed_with_counts(tmp_path):
    report = suggest_mapping(write_raw(tmp_path))["tables"]
    assert report["employees"]["codes_to_translate"] == {
        "flsa_status": {"N": 2, "E": 1},
        "work_schedule": {"D8": 3},
    }
    # Values the template already accepts are not listed.
    assert report["actions"]["codes_to_translate"] == {
        "misconduct_category": {"ATT": 1},
        "action_type": {"WR": 1},
    }
    assert (
        "custom categories" in report["actions"]["codes_note"]
        or "own categories" in (report["actions"]["codes_note"])
    )


def test_job_level_fields_are_set_up_per_job_family(tmp_path):
    suggestion = suggest_mapping(write_raw(tmp_path))
    employees = suggestion["suggested_mapping"]["tables"]["employees"]
    decide = suggestion["tables"]["employees"]["decide_per_job_family"]
    assert set(decide) == {"is_sworn", "minimum_staffing_role", "turnover_profile"}
    assert decide["turnover_profile"]["job_families"] == ["Mechanic", "Office Admin"]
    assert employees["columns"]["turnover_profile"] == "Position Category"
    assert employees["values"]["turnover_profile"] == {"Mechanic": "", "Office Admin": ""}


def test_an_unfinished_suggestion_says_what_to_fill_in(tmp_path):
    suggestion = suggest_mapping(write_raw(tmp_path))
    mapping = suggestion["suggested_mapping"]
    untouched = TemplateAdapter(mapping=mapping)
    assert untouched.load(tmp_path).employees == []
    assert all(
        "Add it to the mapping's values" in i.message
        for i in untouched.report.blocking
        if i.entity == "employee"
    )
    # With the codes translated, what is left is the per-job-family decision.
    mapping["tables"]["employees"]["values"].update(
        flsa_status={"N": "non_exempt", "E": "exempt"}, work_schedule={"D8": "weekday_8"}
    )
    adapter = TemplateAdapter(mapping=mapping)
    assert adapter.load(tmp_path).employees == []
    blank = {i.field for i in adapter.report.blocking if "is blank. Fill it in" in i.message}
    assert blank == {"is_sworn"}


def test_a_finished_suggestion_loads_and_costs(tmp_path, assumptions):
    suggestion = suggest_mapping(write_raw(tmp_path))
    mapping = suggestion["suggested_mapping"]
    employees, actions = mapping["tables"]["employees"], mapping["tables"]["actions"]
    employees["values"].update(
        flsa_status={"N": "non_exempt", "E": "exempt"},
        work_schedule={"D8": "weekday_8"},
        is_sworn={"Mechanic": "N", "Office Admin": "N"},
        minimum_staffing_role={"Mechanic": "N", "Office Admin": "N"},
        turnover_profile={"Mechanic": "civilian_skilled", "Office Admin": "civilian_standard"},
    )
    actions["values"].update(
        misconduct_category={"ATT": "attendance_awol"}, action_type={"WR": "written_reprimand"}
    )
    adapter = TemplateAdapter(mapping=mapping)
    data = adapter.load(tmp_path)
    assert adapter.report.blocking == []
    assert [a.misconduct_category for a in data.actions] == ["attendance_awol", "tardiness"]
    assert [str(e.turnover_profile) for e in data.employees] == [
        "civilian_skilled",
        "civilian_skilled",
        "civilian_standard",
    ]
    assert data.employees[0].hire_date.isoformat() == "2011-02-27"
    assert len(cost(adapter, data, assumptions).action_costs) == 2


@pytest.mark.parametrize(
    "dates, expected, ambiguous",
    [
        (["2024-07-27", "2011-02-01"], "%Y-%m-%d", False),
        (["07/27/2024", "02/01/2011"], "%m/%d/%Y", False),
        (["27/07/2024", "01/02/2011"], "%d/%m/%Y", False),
        (["03/04/2024", "02/01/2011"], "%m/%d/%Y", True),
    ],
)
def test_the_date_format_is_detected(tmp_path, dates, expected, ambiguous):
    roster = [{**ROSTER[0], "Worker #": f"W{i}", "Start Dt": d} for i, d in enumerate(dates)]
    log = [{**LOG[1], "Worker #": "W0", "Date of Decision": dates[0]}]
    found = suggest_mapping(write_raw(tmp_path, roster, log))
    assert found["suggested_mapping"]["date_format"] == expected
    assert found["date_format"]["ambiguous"] is ambiguous


# ---------------------------------------------------------------------------
# over the API
# ---------------------------------------------------------------------------
@pytest.fixture
def site(tmp_path):
    if not (SAMPLE / "hr_empl_master.csv").exists():
        pytest.skip("sample export missing; run `make generate`")
    site = Installation(tmp_path)
    for person in (LEAD, ANALYST, CLIENT):
        site.add_user(person)
    site.engagement = site.new_engagement("FY26 review", "Riverbend County")
    site.add_member(site.engagement, LEAD, "admin")
    site.add_member(site.engagement, ANALYST, "hr_analyst")
    site.add_member(site.engagement, CLIENT, "executive")
    return site


def raw_files():
    return [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(SAMPLE.glob("*.csv"))]


def test_template_download(site):
    e = site.engagement
    spec = site.http.get(f"/engagements/{e}/intake/template", headers=site.auth(CLIENT)).json()
    assert [t["name"] for t in spec["tables"]][:2] == ["employees", "actions"]
    assert any(c["id"] == "tardiness" for c in spec["standard_misconduct_categories"])
    archive = site.http.get(f"/engagements/{e}/intake/template.zip", headers=site.auth(CLIENT))
    assert archive.headers["content-type"] == "application/zip"
    assert "employees.csv" in zipfile.ZipFile(io.BytesIO(archive.content)).namelist()


def test_only_the_engagement_admin_runs_intake(site):
    e = site.engagement
    for who in (ANALYST, CLIENT):
        headers = site.auth(who)
        assert site.http.get(f"/engagements/{e}/intake", headers=headers).status_code == 403
        assert (
            site.http.post(
                f"/engagements/{e}/intake/files", files=raw_files(), headers=headers
            ).status_code
            == 403
        )
        assert (
            site.http.put(
                f"/engagements/{e}/intake/mapping", json={"mapping": {}}, headers=headers
            ).status_code
            == 403
        )
        assert site.http.post(f"/engagements/{e}/intake/commit", headers=headers).status_code == 403


def test_intake_from_a_clients_own_layout_end_to_end(site, org, run, tmp_path):
    """Upload the raw export, review the suggestion, fix the mapping, preview, commit."""
    e, headers = site.engagement, site.auth(LEAD)
    base = f"/engagements/{e}"

    # 1. Upload as received. Nothing is costed yet.
    uploaded = site.http.post(f"{base}/intake/files", files=raw_files(), headers=headers).json()
    assert uploaded["files"]["hr_empl_master.csv"]["rows"] > 100
    assert uploaded["suggestion"]["tables"]["employees"]["file"] == "hr_empl_master.csv"
    assert site.http.get(f"{base}/costs/summary", headers=headers).status_code == 409

    # 2. The untouched suggestion is not enough, and the preview says why.
    draft = site.http.put(
        f"{base}/intake/mapping",
        json={"mapping": uploaded["suggestion"]["suggested_mapping"]},
        headers=headers,
    ).json()
    assert draft["ready_to_commit"] is False
    assert draft["validation"]["counts_by_rule"]["missing_required_column"] >= 1
    assert site.http.post(f"{base}/intake/commit", headers=headers).status_code == 422
    assert site.http.get(f"{base}/costs/summary", headers=headers).status_code == 409

    # 3. A complete mapping previews cleanly and reports what would be excluded.
    preview = site.http.put(
        f"{base}/intake/mapping", json={"mapping": harlow_mapping(org)}, headers=headers
    ).json()
    assert preview["ready_to_commit"] is True
    assert preview["counts"]["actions"] == 223
    assert preview["actions_that_would_be_excluded"] == 3
    assert site.http.get(f"{base}/costs/summary", headers=headers).status_code == 409

    # 4. Commit. The total equals the county adapter's for the same records.
    committed = site.http.post(f"{base}/intake/commit", headers=headers)
    assert committed.status_code == 200, committed.text
    assert committed.json()["source_id"] == "wri_template"
    assert site.net(e, LEAD) == run.net
    meta = site.http.get(f"{base}/meta", headers=headers).json()
    assert meta["agency"] == "Riverbend County" and meta["synthetic"] is False

    # 5. A restart reloads the same files through the same mapping.
    marker = site.runtime.workspaces.source_dir(e) / SOURCE_MARKER
    assert marker.exists()
    restarted = TestClient(create_app(Settings(auth_mode="local", data_dir=tmp_path)))
    again = restarted.get(f"{base}/costs/summary", headers=headers)
    assert Decimal(again.json()["total_net"]) == run.net


def test_a_mapping_that_is_not_valid_is_refused_and_not_saved(site):
    e, headers = site.engagement, site.auth(LEAD)
    site.http.post(f"/engagements/{e}/intake/files", files=raw_files(), headers=headers)
    response = site.http.put(
        f"/engagements/{e}/intake/mapping",
        json={"mapping": {"tables": {"employees": {"columns": {"employe_id": "EMP_NBR"}}}}},
        headers=headers,
    )
    assert response.status_code == 400
    assert "employe_id" in response.json()["detail"]
    status = site.http.get(f"/engagements/{e}/intake", headers=headers).json()
    assert status["draft_mapping"] is None
    assert site.http.post(f"/engagements/{e}/intake/commit", headers=headers).status_code == 409


def test_intake_never_disturbs_a_committed_dataset(site, org):
    e, headers = site.engagement, site.auth(LEAD)
    assert site.upload_sample(e, LEAD).status_code == 200
    before = site.net(e, LEAD)
    site.http.post(f"/engagements/{e}/intake/files", files=raw_files(), headers=headers)
    site.http.put(
        f"/engagements/{e}/intake/mapping",
        json={"mapping": {"tables": {"employees": {"file": "hr_empl_master.csv"}}}},
        headers=headers,
    )
    assert site.http.post(f"/engagements/{e}/intake/commit", headers=headers).status_code == 422
    assert site.net(e, LEAD) == before


def test_template_files_can_be_uploaded_in_one_step(site, loaded, org, run, tmp_path):
    data, _ = loaded
    folder = write_template(data, org, tmp_path / "template")
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(folder.iterdir())]
    response = site.http.post(
        f"/engagements/{site.engagement}/datasets/upload",
        params={"source": "wri_template"},
        files=files,
        headers=site.auth(LEAD),
    )
    assert response.status_code == 200, response.text
    assert site.net(site.engagement, LEAD) == run.net


# ---------------------------------------------------------------------------
# the private-sector intake
# ---------------------------------------------------------------------------
PRIVATE_EMPLOYEE = {
    **EMPLOYEE,
    "coverage_method": "temp_agency",
    "flsa_status": "exempt",
}
PRIVATE_ACTION = {
    **ACTION,
    "action_type": "suspension",
    "suspension_days": "2",
    "suspension_start_date": "2025-06-16",
    "outside_investigator_hours": "9.5",
    "written_conduct_policy": "N",
}


def test_a_private_engagement_loads_every_wage_under_the_private_multiplier(tmp_path):
    """One private-industry multiplier covers everyone. A client file that marks someone
    sworn -- a mapped code, or a column left over from another engagement -- must not pull a
    state-government multiplier into a company's figures."""
    sworn = {**EMPLOYEE, "employee_id": "E2", "is_sworn": "Y"}
    adapter, data = load(tmp_path, [EMPLOYEE, sworn], [ACTION], sector="private_sector")
    assert adapter.sector == "private_sector"
    assert {e.benefits_group for e in data.employees} == {"private_all"}


def test_a_public_engagement_still_splits_sworn_from_civilian(tmp_path):
    """The seam must not have moved the public sector's own behaviour."""
    sworn = {**EMPLOYEE, "employee_id": "E2", "is_sworn": "Y"}
    _, data = load(tmp_path, [EMPLOYEE, sworn], [ACTION])
    groups = {e.employee_id: e.benefits_group for e in data.employees}
    assert groups == {"E1": "civilian", "E2": "sworn_public_safety"}


def test_a_turnover_profile_the_sector_does_not_run_is_reported_and_replaced(tmp_path):
    """Pricing a warehouse hire at deputy recruitment cost would overstate the figure. The
    profile falls back to the cheapest the sector runs, and the substitution is reported."""
    row = {**EMPLOYEE, "turnover_profile": "sworn_deputy"}
    adapter, data = load(tmp_path, [row], [ACTION], sector="private_sector")
    assert data.employees[0].turnover_profile == "civilian_standard"
    issue = next(i for i in adapter.report.issues if i.rule_id == "turnover_profile_not_in_sector")
    assert issue.severity is not Severity.BLOCKING
    assert issue.entity_id == "E1"
    assert "sworn_deputy" in issue.message and "civilian_standard" in issue.message
    assert adapter.report.blocking == []


def test_the_same_profile_is_untouched_in_a_public_engagement(tmp_path):
    row = {**EMPLOYEE, "turnover_profile": "sworn_deputy"}
    adapter, data = load(tmp_path, [row], [ACTION])
    assert data.employees[0].turnover_profile == "sworn_deputy"
    assert "turnover_profile_not_in_sector" not in adapter.report.counts_by_rule()


def test_the_private_columns_load_from_the_template(tmp_path):
    """Everything the private components need comes off the template: how a post is covered,
    an outside investigator's hours, whether a written policy backed an unpaid suspension,
    what an insurer paid, and what a separation cost."""
    write(
        tmp_path,
        "appeals",
        [
            {
                "appeal_id": "AP1",
                "action_id": "A1",
                "forum": "agency_charge",
                "filed_date": "2025-07-01",
                "resolution_date": "2026-01-15",
                "outcome": "settled",
                "settlement_amount": "45000",
                "outside_counsel_hours": "60",
                "insurance_retention_paid": "25000",
                "insurer_paid_amount": "30000",
            }
        ],
    )
    write(
        tmp_path,
        "separations",
        [
            {
                "employee_id": "E1",
                "separation_date": "2026-02-02",
                "separation_reason": "removal_for_cause",
                "linked_action_id": "A1",
                "separation_agreement": "Y",
                "severance_amount": "8000",
                "unemployment_claim_filed": "Y",
                "unemployment_benefits_charged": "6240",
            }
        ],
    )
    adapter, data = load(tmp_path, [PRIVATE_EMPLOYEE], [PRIVATE_ACTION], sector="private_sector")
    assert adapter.report.blocking == []
    employee, action = data.employees[0], data.actions[0]
    assert employee.coverage_method == CoverageMethod.TEMP_AGENCY
    assert action.outside_investigator_hours == Decimal("9.5")
    assert action.written_conduct_policy is False
    appeal = data.appeals[0]
    assert (appeal.insurance_retention_paid, appeal.insurer_paid_amount) == (
        Decimal(25000),
        Decimal(30000),
    )
    separation = next(s for s in data.separations if s.linked_action_id == "A1")
    assert separation.separation_agreement is True
    assert separation.severance_amount == Decimal(8000)
    assert separation.unemployment_claim_filed is True
    assert separation.unemployment_benefits_charged == Decimal(6240)
    # A retention recorded beside a settlement is ambiguous, and is reported rather than
    # resolved by guessing which way the policy runs.
    assert "retention_recorded_with_payout" in adapter.report.counts_by_rule()


def test_blank_private_columns_leave_the_public_behaviour_exactly_as_it_was(tmp_path):
    """Every new column is optional, and a file without them costs as it did before they
    existed: a post covered by overtime, no outside investigator, and an unpaid suspension
    treated as made under a written policy -- the case that keeps the employee's credit."""
    _, data = load(tmp_path, [EMPLOYEE], [ACTION])
    employee, action = data.employees[0], data.actions[0]
    assert employee.coverage_method == CoverageMethod.OVERTIME
    assert action.outside_investigator_hours == Decimal(0)
    assert action.written_conduct_policy is True


def test_an_unreadable_coverage_method_names_the_choices(tmp_path):
    """A code the template does not know is a mapping gap, reported with what to map it to,
    never guessed at."""
    row = {**EMPLOYEE, "coverage_method": "agency_temp"}
    adapter, data = load(tmp_path, [row], [ACTION], sector="private_sector")
    assert data.employees == []
    issue = next(i for i in adapter.report.issues if i.field == "coverage_method")
    assert "temp_agency" in issue.message


# ---------------------------------------------------------------------------
# occupational groups: which benefits multiplier loads a private wage
# ---------------------------------------------------------------------------
def test_an_occupation_group_selects_that_groups_own_multiplier(tmp_path, assumptions):
    """A service worker's wage carries far less in benefits than a production worker's, and
    the BLS publishes a figure for each. The template column routes each person to theirs."""
    rows = [
        {**EMPLOYEE, "employee_id": "E1", "occupation_group": "service"},
        {
            **EMPLOYEE,
            "employee_id": "E2",
            "job_family": "Plant Operative",
            "occupation_group": "Production Transportation",
        },
    ]
    adapter, data = load(tmp_path, rows, [ACTION], sector="private_sector")
    groups = {e.employee_id: e.benefits_group for e in data.employees}
    assert groups == {"E1": "private_service", "E2": "private_production_transportation"}
    assert "defaulted_occupation_group" not in adapter.report.counts_by_rule()
    # The job family built from the file carries the same group as the people in it.
    assert adapter.org.role_families["plant_operative"].benefits_group == (
        "private_production_transportation"
    )

    run = run_costing(
        data,
        as_of=AS_OF,
        org=adapter.org,
        assumptions=assumptions,
        sector="private_sector",
    )
    assert run.sector == "private_sector"
    service = assumptions.value("private_benefits_multiplier_service")
    assert service < assumptions.value("private_benefits_multiplier_all")


def test_a_missing_occupation_group_uses_the_average_and_says_so(tmp_path):
    """Blank is allowed, because not every client's file says what kind of work a job is.
    The all-occupations figure is the published answer to "occupation unknown" -- but it
    overstates service staff and understates production staff, so the report says how many
    people it was used for rather than leaving the reader to assume none."""
    rows = [
        {**EMPLOYEE, "employee_id": "E1"},
        {**EMPLOYEE, "employee_id": "E2", "occupation_group": "sales_office"},
    ]
    adapter, data = load(tmp_path, rows, [ACTION], sector="private_sector")
    groups = {e.employee_id: e.benefits_group for e in data.employees}
    assert groups == {"E1": "private_all", "E2": "private_sales_office"}
    issue = next(i for i in adapter.report.issues if i.rule_id == "defaulted_occupation_group")
    assert issue.severity is Severity.WARNING
    assert issue.message.startswith("1 employee(s)")
    assert "private_benefits_multiplier_all" in issue.message
    assert adapter.report.blocking == []


def test_a_public_engagement_ignores_the_occupation_group(tmp_path):
    """A county's split is sworn against civilian. The column is not an error there, and it
    must neither change a figure nor raise a warning about a multiplier the county never
    uses."""
    row = {**EMPLOYEE, "occupation_group": "service"}
    adapter, data = load(tmp_path, [row], [ACTION])
    assert data.employees[0].benefits_group == "civilian"
    assert "defaulted_occupation_group" not in adapter.report.counts_by_rule()
    blank_adapter, _ = load(tmp_path, [EMPLOYEE], [ACTION])
    assert "defaulted_occupation_group" not in blank_adapter.report.counts_by_rule()


def test_an_unreadable_occupation_group_names_the_choices(tmp_path):
    row = {**EMPLOYEE, "occupation_group": "hospitality"}
    adapter, data = load(tmp_path, [row], [ACTION], sector="private_sector")
    assert data.employees == []
    issue = next(i for i in adapter.report.issues if i.field == "occupation_group")
    assert "service" in issue.message and "sales_office" in issue.message
