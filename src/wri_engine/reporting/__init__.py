"""Client deliverables: the report a consultant hands over, and the workbook beside it.

`content` assembles what a deliverable says; `pdf` and `xlsx` each lay that out. Both are
rendered from the same `ReportContent`, so the two files cannot disagree.
"""

from wri_engine.reporting.content import (
    Branding,
    OverrideRow,
    ReportContent,
    build_report,
)
from wri_engine.reporting.pdf import render_pdf
from wri_engine.reporting.xlsx import render_xlsx

__all__ = [
    "Branding",
    "OverrideRow",
    "ReportContent",
    "build_report",
    "render_pdf",
    "render_xlsx",
]
