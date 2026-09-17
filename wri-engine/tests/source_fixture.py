"""A tiny, valid county HR export, plus mutations that break exactly one validation rule.

Every rule in `adapters/validation.py` has a mutation here, so `test_adapter.py` can assert
that each rule fires on a fixture built to trip it and stays quiet otherwise.
"""

from __future__ import annotations

import csv
from pathlib import Path

from wri_engine.generator.generate import (
    ACTION_COLUMNS,
    APPEAL_COLUMNS,
    EMPLOYEE_COLUMNS,
    LEAVE_COLUMNS,
    SEPARATION_COLUMNS,
)

EMPLOYEES = [
    {
        "EMP_NBR": "E1", "DEPT_CD": "SHDET", "CLASS_CD": "CO-2100",
        "POSN_TITLE": "Corrections Officer II", "GRADE": "G17", "STEP": "4",
        "ANNL_SAL": "56,160.00", "FLSA_CD": "K", "BARG_CD": "COR", "SWORN_IND": "Y",
        "MIN_STAFF_IND": "Y", "SHIFT_HRS": "12", "WORK_LOC": "Main Detention Center",
        "SUPV_EMP_NBR": "E2", "HIRE_DT": "02/05/2018", "TERM_DT": "", "TERM_RSN_CD": "",
    },
    {
        "EMP_NBR": "E2", "DEPT_CD": "SHDET", "CLASS_CD": "CO-2300",
        "POSN_TITLE": "Corrections Sergeant", "GRADE": "G22", "STEP": "5",
        "ANNL_SAL": "74,880.00", "FLSA_CD": "K", "BARG_CD": "COR", "SWORN_IND": "Y",
        "MIN_STAFF_IND": "Y", "SHIFT_HRS": "12", "WORK_LOC": "Main Detention Center",
        "SUPV_EMP_NBR": "", "HIRE_DT": "09/12/2011", "TERM_DT": "", "TERM_RSN_CD": "",
    },
    {
        "EMP_NBR": "E3", "DEPT_CD": "PUBWK", "CLASS_CD": "PW-5100",
        "POSN_TITLE": "Maintenance Worker I", "GRADE": "G12", "STEP": "2",
        "ANNL_SAL": "43,000.00", "FLSA_CD": "N", "BARG_CD": "GEN", "SWORN_IND": "N",
        "MIN_STAFF_IND": "N", "SHIFT_HRS": "8", "WORK_LOC": "North Yard",
        "SUPV_EMP_NBR": "", "HIRE_DT": "03/01/2020", "TERM_DT": "", "TERM_RSN_CD": "",
    },
]

ACTIONS = [
    {
        "ACTN_NBR": "A1", "EMP_NBR": "E1", "INCDT_DT": "03/01/2025",
        "PROP_DT": "04/01/2025", "DECN_DT": "06/15/2025", "MISCND_CD": "NEG",
        "MISCND_SUB": "Failure to respond", "ACTN_CD": "SP2", "SUSP_DAYS": "6",
        "SUSP_BEG_DT": "06/25/2025", "INVEST_IND": "Y", "INVEST_TYP": "HRI",
        "DECIDE_LVL": "2", "DECIDE_EMP_NBR": "", "POSN_ABOLISH_IND": "N",
    },
    {
        "ACTN_NBR": "A2", "EMP_NBR": "E3", "INCDT_DT": "01/10/2025",
        "PROP_DT": "01/20/2025", "DECN_DT": "02/05/2025", "MISCND_CD": "TRD",
        "MISCND_SUB": "Late to shift", "ACTN_CD": "WRP", "SUSP_DAYS": "0",
        "SUSP_BEG_DT": "", "INVEST_IND": "N", "INVEST_TYP": "", "DECIDE_LVL": "1",
        "DECIDE_EMP_NBR": "", "POSN_ABOLISH_IND": "N",
    },
]

LEAVE = [
    {
        "LEAVE_NBR": "L1", "EMP_NBR": "E1", "ACTN_NBR": "A1", "BEG_DT": "05/01/2025",
        "END_DT": "06/15/2025", "PAID_IND": "Y",
    }
]

APPEALS = [
    {
        "APPL_NBR": "P1", "ACTN_NBR": "A1", "FORUM_CD": "GRV", "FILED_DT": "06/25/2025",
        "RESOLV_DT": "09/01/2025", "OUTCOME_CD": "SUS", "BACKPAY_AMT": "0.00",
        "SETTLE_AMT": "0.00", "OC_HOURS": "0.0",
    }
]

SEPARATIONS: list[dict] = []

TABLES = {
    "hr_empl_master.csv": (EMPLOYEES, EMPLOYEE_COLUMNS),
    "hr_disc_actn.csv": (ACTIONS, ACTION_COLUMNS),
    "hr_admn_leave.csv": (LEAVE, LEAVE_COLUMNS),
    "hr_appl_grv.csv": (APPEALS, APPEAL_COLUMNS),
    "hr_sepn.csv": (SEPARATIONS, SEPARATION_COLUMNS),
}


def write_export(directory: Path, **overrides: list[dict]) -> Path:
    """Write a valid export, replacing whole tables by keyword (e.g. `actions=[...]`)."""
    key_to_file = {
        "employees": "hr_empl_master.csv",
        "actions": "hr_disc_actn.csv",
        "admin_leave": "hr_admn_leave.csv",
        "appeals": "hr_appl_grv.csv",
        "separations": "hr_sepn.csv",
    }
    directory.mkdir(parents=True, exist_ok=True)
    tables = {name: [dict(r) for r in rows] for name, (rows, _) in TABLES.items()}
    for key, rows in overrides.items():
        tables[key_to_file[key]] = rows
    for filename, (_, columns) in TABLES.items():
        with open(directory / filename, "w", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns)
            writer.writeheader()
            writer.writerows(tables[filename])
    return directory


def rows(name: str) -> list[dict]:
    """A fresh, mutable copy of one base table."""
    lookup = {
        "employees": EMPLOYEES, "actions": ACTIONS, "admin_leave": LEAVE,
        "appeals": APPEALS, "separations": SEPARATIONS,
    }
    return [dict(r) for r in lookup[name]]
