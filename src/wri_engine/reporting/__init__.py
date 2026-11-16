"""Client deliverables: the report a consultant hands over, and the workbook beside it.

`content` assembles what a deliverable says; `pdf` and `xlsx` each lay that out. Both are
rendered from the same `ReportContent`, so the two files cannot disagree.
"""

from wri_engine.reporting.content import (
    Branding,
    InterventionSection,
    OverrideRow,
    PatternSection,
    RecordSection,
    ReportContent,
    build_interventions,
    build_patterns,
    build_records,
    build_report,
    client_wording,
)
from wri_engine.reporting.pdf import render_pdf
from wri_engine.reporting.xlsx import render_xlsx

__all__ = [
    "Branding",
    "InterventionSection",
    "OverrideRow",
    "PatternSection",
    "RecordSection",
    "ReportContent",
    "build_interventions",
    "build_patterns",
    "build_records",
    "build_report",
    "client_wording",
    "render_pdf",
    "render_xlsx",
]
