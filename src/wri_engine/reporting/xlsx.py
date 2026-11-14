"""The workbook that goes with the report: a `ReportContent` as a spreadsheet.

The report is for reading; this is for working in. It carries the same figures -- both are
rendered from one `ReportContent` -- plus what a page cannot hold: every cell of the cost
matrix, and every assumption with its bounds.

Two things are done to every cell on purpose:

* **Money stays `Decimal`** all the way into the file, and is shown to the cent by the
  cell's number format, not by rounding the value.
* **Text is written as text.** A spreadsheet treats a cell beginning with `=`, `+`, `-` or
  `@` as a formula. A client's department name or the reason typed for an override must never
  be run as one, so every string is stored as a string whatever it starts with.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from wri_engine.reporting.content import PRODUCT_NAME, ReportContent

MONEY = "#,##0.00;[Red]-#,##0.00"
SHARE = "0.0%"
HEADER_FILL = PatternFill("solid", fgColor="243B53")
HEADER_FONT = Font(bold=True, color="FFFFFF")
BOLD = Font(bold=True)
MUTED = Font(color="52606D", italic=True)


def _put(sheet, row: int, column: int, value, *, number_format: str | None = None, font=None):
    cell = sheet.cell(row=row, column=column)
    if isinstance(value, str):
        cell.value = value
        # Whatever the text starts with, it is text. See the module docstring.
        cell.data_type = "s"
        cell.alignment = Alignment(vertical="top", wrap_text=len(value) > 60)
    else:
        cell.value = value
    if number_format:
        cell.number_format = number_format
    if font:
        cell.font = font
    return cell


def _header(sheet, row: int, titles: list[str]) -> None:
    for column, title in enumerate(titles, start=1):
        cell = _put(sheet, row, column, title)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
    sheet.freeze_panes = sheet.cell(row=row + 1, column=1)


def _widths(sheet, widths: list[int]) -> None:
    for column, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width


def _summary(book: Workbook, content: ReportContent) -> None:
    sheet = book.active
    sheet.title = "Summary"
    _put(sheet, 1, 1, f"{content.title}: {content.client_name}", font=Font(bold=True, size=14))
    _put(sheet, 2, 1, f"Prepared by {content.branding.prepared_by}", font=MUTED)
    _put(sheet, 3, 1, content.caption, font=MUTED)

    facts: list[tuple[str, object, str | None]] = [
        ("Prepared for", content.client_name, None),
        ("Engagement", content.engagement_name or "", None),
        ("Cost model", content.cost_model, None),
        ("Occupation group", content.occupation_group or "", None),
        ("Data as of", content.as_of.isoformat() if content.as_of else "", None),
        ("Years of decided actions covered", content.window_years, "0.00"),
        ("Assumptions at", f"{content.mode} values", None),
        ("Report prepared", content.generated_on.isoformat(), None),
        ("", "", None),
        ("Net cost per year", content.annual_net, MONEY),
        ("Net cost per employee per year", content.net_per_employee_per_year, MONEY),
        ("Gross cost", content.gross, MONEY),
        ("Savings shown separately", content.offset, MONEY),
        ("Net cost", content.net, MONEY),
        ("", "", None),
        ("Actions costed", content.actions_costed, "#,##0"),
        ("Actions left out for data quality", content.actions_excluded, "#,##0"),
        ("Actions still accruing cost", content.actions_incomplete, "#,##0"),
        ("Active employees", content.active_employees, "#,##0"),
    ]
    for offset, (label, value, number_format) in enumerate(facts):
        _put(sheet, 5 + offset, 1, label, font=BOLD if label else None)
        if value is not None:
            _put(sheet, 5 + offset, 2, value, number_format=number_format)
    end = 5 + len(facts) + 1
    _put(sheet, end, 1, content.method_note, font=MUTED)
    _put(sheet, end + 1, 1, content.assumption_note, font=MUTED)
    _put(sheet, end + 2, 1, content.standing, font=MUTED)
    _widths(sheet, [38, 40])


def _components(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Components")
    _header(sheet, 1, ["Code", "Component", "Amount", "Share of gross", "Saving?"])
    row = 2
    for component in content.components:
        _put(sheet, row, 1, component.code)
        _put(sheet, row, 2, component.label)
        _put(sheet, row, 3, component.amount, number_format=MONEY)
        if component.share_of_gross is not None:
            _put(sheet, row, 4, component.share_of_gross, number_format=SHARE)
        _put(sheet, row, 5, "yes" if component.is_offset else "")
        row += 1
    for label, amount in (
        ("Gross", content.gross),
        ("Savings shown separately", content.offset),
        ("Net", content.net),
    ):
        _put(sheet, row, 2, label, font=BOLD)
        _put(sheet, row, 3, amount, number_format=MONEY, font=BOLD)
        row += 1
    _widths(sheet, [12, 44, 18, 16, 10])


def _range(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Low to high")
    _header(sheet, 1, ["Assumptions at", "Gross", "Savings", "Net", "Net per year"])
    for row, mode in enumerate(content.modes, start=2):
        _put(sheet, row, 1, mode.mode)
        _put(sheet, row, 2, mode.gross, number_format=MONEY)
        _put(sheet, row, 3, mode.offset, number_format=MONEY)
        _put(sheet, row, 4, mode.net, number_format=MONEY)
        if mode.annual_net is not None:
            _put(sheet, row, 5, mode.annual_net, number_format=MONEY)
    _widths(sheet, [18, 18, 18, 18, 18])


def _matrix(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Cost matrix")
    _header(
        sheet,
        1,
        [
            content.row_dimension,
            content.col_dimension,
            "Actions",
            "Employees",
            "Gross",
            "Savings",
            "Net",
            "Per action",
            "Still accruing",
            "Withheld",
        ],
    )
    row = 2
    ordered = content.visible_cells + [c for c in content.cells if c.suppressed]
    for cell in ordered:
        _put(sheet, row, 1, cell.row_label)
        _put(sheet, row, 2, cell.col_label)
        if cell.suppressed:
            # A withheld cell carries its labels and its reason, and no figure at all.
            _put(sheet, row, 10, cell.suppression_reason)
        else:
            _put(sheet, row, 3, cell.action_count, number_format="#,##0")
            _put(sheet, row, 4, cell.employee_count, number_format="#,##0")
            _put(sheet, row, 5, cell.gross, number_format=MONEY)
            _put(sheet, row, 6, cell.offset, number_format=MONEY)
            _put(sheet, row, 7, cell.net, number_format=MONEY)
            _put(sheet, row, 8, cell.cost_per_action, number_format=MONEY)
            _put(sheet, row, 9, cell.incomplete_count, number_format="#,##0")
        row += 1
    _put(sheet, row, 1, "All withheld cells together", font=BOLD)
    _put(sheet, row, 5, content.suppressed_gross, number_format=MONEY, font=BOLD)
    _put(sheet, row, 7, content.suppressed_net, number_format=MONEY, font=BOLD)
    row += 1
    _put(sheet, row, 1, "Total", font=BOLD)
    _put(sheet, row, 3, content.actions_costed, number_format="#,##0", font=BOLD)
    _put(sheet, row, 5, content.gross, number_format=MONEY, font=BOLD)
    _put(sheet, row, 6, content.offset, number_format=MONEY, font=BOLD)
    _put(sheet, row, 7, content.net, number_format=MONEY, font=BOLD)
    row += 2
    _put(
        sheet,
        row,
        1,
        f"A cell is shown only where at least {content.min_cell_size} different employees "
        f"stand behind it. Withheld cells are in every total.",
        font=MUTED,
    )
    _widths(sheet, [34, 34, 10, 11, 16, 16, 16, 16, 14, 34])


def _departments(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Departments")
    _header(sheet, 1, ["Department", "Active employees", "Net cost", "Per employee per year"])
    for row, department in enumerate(content.departments, start=2):
        _put(sheet, row, 1, department.department)
        _put(sheet, row, 2, department.active_fte, number_format="#,##0")
        _put(sheet, row, 3, department.net, number_format=MONEY)
        if department.net_per_fte_per_year is not None:
            _put(sheet, row, 4, department.net_per_fte_per_year, number_format=MONEY)
    _widths(sheet, [44, 18, 18, 24])


def _quality(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Data quality")
    _header(sheet, 1, ["What was found", "How many"])
    row = 2
    for item in content.quality:
        _put(sheet, row, 1, item.rule_id.replace("_", " "))
        _put(sheet, row, 2, item.count, number_format="#,##0")
        row += 1
    row += 1
    for label, value in (
        ("Problems that stopped a record being used", content.quality_blocking),
        ("Problems noted without stopping anything", content.quality_warnings),
        ("Actions left out of every figure", content.actions_excluded),
    ):
        _put(sheet, row, 1, label, font=BOLD)
        _put(sheet, row, 2, value, number_format="#,##0")
        row += 1
    _widths(sheet, [56, 14])


def _assumptions(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Assumptions used")
    _header(
        sheet,
        1,
        [
            "Assumption",
            "Value used",
            "Default",
            "Low",
            "High",
            "Unit",
            "Status",
            "Confidence",
            "Client's own value?",
            "Where it comes from",
        ],
    )
    for row, a in enumerate(content.assumptions_used, start=2):
        _put(sheet, row, 1, a.id)
        _put(sheet, row, 2, a.value)
        _put(sheet, row, 3, a.default_value)
        _put(sheet, row, 4, a.low)
        _put(sheet, row, 5, a.high)
        _put(sheet, row, 6, a.unit)
        _put(sheet, row, 7, a.status)
        _put(sheet, row, 8, a.confidence)
        _put(sheet, row, 9, "yes" if a.overridden else "")
        _put(sheet, row, 10, a.source)
    _widths(sheet, [46, 12, 12, 10, 10, 16, 12, 12, 18, 100])


def _overrides(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Client values")
    _header(
        sheet,
        1,
        ["Assumption", "Default", "This client", "Unit", "Reason given", "Set by", "Set on"],
    )
    if not content.overrides:
        _put(sheet, 2, 1, "None. Every figure uses the default value of each assumption.")
    for row, o in enumerate(content.overrides, start=2):
        _put(sheet, row, 1, o.assumption_id)
        _put(sheet, row, 2, o.default_value)
        _put(sheet, row, 3, o.value)
        _put(sheet, row, 4, o.unit)
        _put(sheet, row, 5, o.reason)
        _put(sheet, row, 6, o.set_by)
        _put(sheet, row, 7, o.set_on.isoformat())
    _widths(sheet, [46, 12, 12, 16, 70, 30, 12])


def render_xlsx(content: ReportContent) -> bytes:
    """Write a report's content as a workbook and return the file's bytes."""
    book = Workbook()
    book.properties.title = f"{content.title} - {content.client_name}"
    book.properties.creator = content.branding.prepared_by
    book.properties.subject = f"{PRODUCT_NAME}: {content.cost_model}"
    _summary(book, content)
    _components(book, content)
    _range(book, content)
    _matrix(book, content)
    _departments(book, content)
    _quality(book, content)
    _assumptions(book, content)
    _overrides(book, content)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
