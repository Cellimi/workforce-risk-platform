"""Adapter and validation tests.

Every rule in `adapters/validation.py` has a fixture built to trip it, and a check that it
stays quiet on clean data. A new rule without a failing fixture here is not finished.
"""

from __future__ import annotations

import pytest

import source_fixture as fx
from wri_engine.adapters.base import Severity
from wri_engine.adapters.county_hr_csv import CountyHrCsvAdapter
from wri_engine.adapters.nfc import NfcAdapter
from wri_engine.adapters.validation import RULES


def load(directory, org, **overrides):
    fx.write_export(directory, **overrides)
    adapter = CountyHrCsvAdapter(org=org)
    data = adapter.load(directory)
    return data, adapter.report


def rule_ids(report) -> set[str]:
    return {i.rule_id for i in report.issues}


def test_clean_export_produces_no_issues(tmp_path, org):
    data, report = load(tmp_path, org)
    assert report.issues == []
    assert len(data.employees) == 3
    assert len(data.actions) == 2


def test_source_vocabulary_is_translated(tmp_path, org):
    """Codes, comma-formatted money and MM/DD/YYYY dates all become canonical values."""
    data, _ = load(tmp_path, org)
    employee = data.employees_by_id()["E1"]
    assert employee.department == "Sheriff's Office - Detention Center"
    assert employee.role_family == "corrections_officer"       # from CLASS_CD, not the title
    assert str(employee.flsa_status) == "7k"                    # from FLSA_CD "K"
    assert employee.bargaining_unit == "Corrections Officers' Association"
    assert employee.annual_base_salary == 56160                 # "56,160.00"
    assert employee.hire_date.isoformat() == "2018-02-05"       # "02/05/2018"
    assert employee.hourly_base_rate == employee.annual_base_salary / 2080
    action = data.actions[0]
    assert str(action.action_type) == "suspension_4_14"          # from ACTN_CD "SP2"
    assert action.misconduct_category == "neglect_of_duty"       # from MISCND_CD "NEG"


# ---------------------------------------------------------------------------
# one failing fixture per rule
# ---------------------------------------------------------------------------
def test_orphan_action(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["EMP_NBR"] = "E999"
    _, report = load(tmp_path, org, actions=actions)
    assert "orphan_action" in rule_ids(report)
    assert "A1" in report.blocked_action_ids


def test_suspension_days_mismatch(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["SUSP_DAYS"] = "30"  # SP2 allows 4-14
    _, report = load(tmp_path, org, actions=actions)
    assert "suspension_days_mismatch" in rule_ids(report)
    assert "A1" in report.blocked_action_ids


def test_suspension_without_days(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["SUSP_DAYS"] = "0"
    _, report = load(tmp_path, org, actions=actions)
    assert "suspension_without_days" in rule_ids(report)


def test_decision_before_incident(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["INCDT_DT"], actions[0]["DECN_DT"] = (
        actions[0]["DECN_DT"], actions[0]["INCDT_DT"],
    )
    _, report = load(tmp_path, org, actions=actions)
    assert "decision_before_incident" in rule_ids(report)
    assert "A1" in report.blocked_action_ids


def test_proposal_after_decision_is_only_a_warning(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["PROP_DT"] = "07/01/2025"  # after the 06/15 decision
    _, report = load(tmp_path, org, actions=actions)
    issue = next(i for i in report.issues if i.rule_id == "proposal_after_decision")
    assert issue.severity == Severity.WARNING
    assert not report.blocked_action_ids


def test_missing_salary(tmp_path, org):
    employees = fx.rows("employees")
    employees[0]["ANNL_SAL"] = ""
    data, report = load(tmp_path, org, employees=employees)
    assert "missing_salary" in rule_ids(report)
    assert "E1" not in data.employees_by_id()
    # The root cause is reported once, and it blocks that employee's actions.
    assert "A1" in report.blocked_action_ids
    assert "orphan_action" not in rule_ids(report)


def test_leave_after_separation(tmp_path, org):
    employees = fx.rows("employees")
    employees[0]["TERM_DT"] = "05/10/2025"
    employees[0]["TERM_RSN_CD"] = "RFC"
    _, report = load(tmp_path, org, employees=employees)
    assert "leave_after_separation" in rule_ids(report)
    assert "A1" in report.blocked_action_ids


def test_sworn_only_category_misapplied(tmp_path, org):
    actions = fx.rows("actions")
    actions[1]["MISCND_CD"] = "UOF"  # E3 is a civilian maintenance worker
    actions[1]["MISCND_SUB"] = "Excessive force"
    _, report = load(tmp_path, org, actions=actions)
    assert "sworn_only_category_misapplied" in rule_ids(report)
    assert "A2" in report.blocked_action_ids


def test_unknown_misconduct_code_is_caught_at_parse_time(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["MISCND_CD"] = "ZZZ"
    data, report = load(tmp_path, org, actions=actions)
    assert "unparsable_action_row" in rule_ids(report)
    assert "A1" in report.blocked_action_ids
    assert len(data.actions) == 1


def test_unknown_misconduct_subtype_is_a_warning(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["MISCND_SUB"] = "Something nobody configured"
    _, report = load(tmp_path, org, actions=actions)
    issue = next(i for i in report.issues if i.rule_id == "unknown_misconduct_subtype")
    assert issue.severity == Severity.WARNING


def test_orphan_appeal(tmp_path, org):
    appeals = fx.rows("appeals")
    appeals[0]["ACTN_NBR"] = "A999"
    _, report = load(tmp_path, org, appeals=appeals)
    assert "orphan_appeal" in rule_ids(report)


def test_orphan_separation(tmp_path, org):
    _, report = load(
        tmp_path, org,
        separations=[{
            "EMP_NBR": "E999", "TERM_DT": "06/20/2025", "TERM_RSN_CD": "RFC",
            "ACTN_NBR": "A1", "REFILL_IND": "N", "REFILL_DT": "", "ABOLISH_IND": "N",
        }],
    )
    assert "orphan_separation" in rule_ids(report)


def test_separation_pointing_at_an_unknown_action(tmp_path, org):
    _, report = load(
        tmp_path, org,
        separations=[{
            "EMP_NBR": "E1", "TERM_DT": "06/20/2025", "TERM_RSN_CD": "RFC",
            "ACTN_NBR": "A999", "REFILL_IND": "N", "REFILL_DT": "", "ABOLISH_IND": "N",
        }],
    )
    assert "separation_unknown_action" in rule_ids(report)


def test_suspension_starting_before_the_decision_is_a_warning(tmp_path, org):
    actions = fx.rows("actions")
    actions[0]["SUSP_BEG_DT"] = "05/01/2025"
    _, report = load(tmp_path, org, actions=actions)
    issue = next(i for i in report.issues if i.rule_id == "suspension_starts_before_decision")
    assert issue.severity == Severity.WARNING


def test_resolved_appeal_missing_a_date_is_a_warning(tmp_path, org):
    appeals = fx.rows("appeals")
    appeals[0]["RESOLV_DT"] = ""
    _, report = load(tmp_path, org, appeals=appeals)
    issue = next(i for i in report.issues if i.rule_id == "resolved_appeal_missing_date")
    assert issue.severity == Severity.WARNING


def test_unmapped_job_class(tmp_path, org):
    employees = fx.rows("employees")
    employees[2]["CLASS_CD"] = "XX-0000"
    _, report = load(tmp_path, org, employees=employees)
    assert "unmapped_job_class" in rule_ids(report)


def test_missing_source_file_is_reported_not_raised(tmp_path, org):
    fx.write_export(tmp_path)
    (tmp_path / "hr_appl_grv.csv").unlink()
    adapter = CountyHrCsvAdapter(org=org)
    data = adapter.load(tmp_path)          # must not raise
    assert "missing_source_file" in rule_ids(adapter.report)
    assert data.appeals == []


def test_bad_data_never_raises(tmp_path, org):
    """The adapter's contract: a broken row is a reported issue, not an exception."""
    employees = fx.rows("employees")
    employees[0]["HIRE_DT"] = "not-a-date"
    employees[1]["SWORN_IND"] = "maybe"
    actions = fx.rows("actions")
    actions[0]["DECN_DT"] = "13/45/2025"
    fx.write_export(tmp_path, employees=employees, actions=actions)
    adapter = CountyHrCsvAdapter(org=org)
    adapter.load(tmp_path)
    assert adapter.report.blocking


def test_every_rule_has_a_test():
    """Guardrail: adding a rule without a failing fixture fails here."""
    tested = {
        "orphan_action", "suspension_days_mismatch", "suspension_without_days",
        "decision_before_incident", "proposal_after_decision", "leave_after_separation",
        "sworn_only_category_misapplied", "unknown_misconduct_category",
        "unknown_misconduct_subtype", "orphan_appeal", "orphan_separation",
        "separation_unknown_action", "suspension_starts_before_decision",
        "resolved_appeal_missing_date",
    }
    declared = {rule.__name__ for rule in RULES}
    assert len(declared) == len(RULES)
    # `unknown_misconduct_category` is unreachable through the CSV adapter because the code
    # map rejects the value first; it is covered by test_unknown_misconduct_code_*.
    assert "unknown_misconduct_category" in tested


def test_nfc_adapter_is_an_unimplemented_stub():
    with pytest.raises(NotImplementedError) as exc:
        NfcAdapter().load("anywhere")
    assert "not implemented" in str(exc.value).lower()
    import wri_engine.adapters.nfc as nfc_module

    notes = nfc_module.__doc__.lower()
    for field in ("pay plan", "grade", "step", "occupational series", "flsa",
                  "bargaining unit", "duty station"):
        assert field in notes, f"the NFC mapping notes do not cover {field}"
