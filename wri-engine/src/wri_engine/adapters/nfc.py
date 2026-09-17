"""Adapter stub: USDA National Finance Center (NFC) payroll/personnel extract.

NOT IMPLEMENTED. This file exists so the field mapping is designed and reviewable before
anyone writes the loader, and so the shape of the second source system is visible while the
first one is still being built.

Field mapping notes
-------------------
NFC personnel data arrives as fixed-layout or delimited extracts keyed on the employee's
pseudo-SSN/NFC employee identifier. The canonical fields map roughly as follows.

    canonical                 NFC concept                         notes
    ------------------------- ----------------------------------- ---------------------------
    employee_id               NFC employee ID (never the SSN)     must be pseudonymized on load
    annual_base_salary        Basic pay rate (annual)             excludes locality unless the
                                                                  extract carries adjusted basic
    pay_plan (-> pay_grade)   Pay plan code (GS, GL, FW, WG, ...) GL is the law-enforcement
                                                                  schedule; WG/WL/WS are wage
                                                                  grade and use a different
                                                                  step structure
    pay_grade                 Grade                               two characters, zero padded
    pay_step                  Step                                wage-grade steps are not
                                                                  comparable to GS steps
    job_class_code            Occupational series (4 digits)      e.g. 1811 criminal
                                                                  investigator, 0083 police,
                                                                  0007 correctional officer
    flsa_status               FLSA category code (E / N)          does NOT distinguish 7(k);
                                                                  7(k) must be derived from the
                                                                  series plus the work schedule
    bargaining_unit           BUS code (bargaining unit status)   4 digits; 7777/8888 mean
                                                                  ineligible / not represented
    work_location             Duty station code                   state+county+city FIPS-style
                                                                  code, needs a lookup table
    is_sworn                  derived                             from occupational series, not
                                                                  a field
    standard_shift_hours      Work schedule code + tour of duty   F/P/I plus tour hours
    supervisor_id             Supervisory status code + org unit  NFC does not carry a direct
                                                                  supervisor pointer; the
                                                                  reporting line has to be
                                                                  reconstructed from the
                                                                  organizational structure code

Discipline records are NOT in the NFC payroll extract. Adverse and disciplinary actions live
in the agency's ER/LR case system (and, for actions that reach it, in MSPB case data). A
production NFC integration therefore needs TWO sources: NFC for the employee master, and the
case system for `DisciplineAction`, `AppealOrGrievance` and `AdminLeavePeriod`.

Open questions before implementing
----------------------------------
1. Which NFC extract (PACS, EPIC/Web, or a Reporting Center download) is available, and at
   what refresh cadence?
2. Is the employee identifier already pseudonymized at export, or must the adapter do it?
3. How is 7(k) status determined for the agency's law-enforcement and firefighter series?
4. Does the ER/LR case system expose a stable case identifier that can serve as `action_id`?
"""

from __future__ import annotations

from pathlib import Path

from wri_engine.adapters.base import SourceAdapter
from wri_engine.schema import CanonicalDataset


class NfcAdapter(SourceAdapter):
    """Not implemented. See the module docstring for the field mapping (pay plan, grade,
    step, occupational series, FLSA category, bargaining unit status, duty station) and the
    four questions that have to be answered before this can be built."""

    source_id = "nfc"

    def load(self, path: Path) -> CanonicalDataset:
        raise NotImplementedError(
            "The NFC adapter is not implemented. See the module docstring for the field "
            "mapping and the four open questions that must be answered first."
        )
