"""The workbook that goes with the report: a `ReportContent` as a spreadsheet.

The report is for reading; this is for working in. It carries the same figures -- both are
rendered from one `ReportContent` -- plus what a page cannot hold: every cell of the cost
matrix, and every assumption with its bounds.

There are two copies. The client's copy is aggregates only. The **working papers** are the
same workbook with two more sheets, Actions and Line items, listing every record behind the
figures; they are written only when the content carries a `RecordSection`, which the API
builds only for a caller entitled to record-level detail. This module does not decide that.
It lays out what it is given, and marks the working papers as such on the first sheet.

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
WARNING = Font(bold=True, color="9B1C1C")
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
    if content.records:
        _put(sheet, end + 4, 1, content.records.note, font=WARNING)
        _put(
            sheet,
            end + 5,
            1,
            "Employee identifiers in this copy are "
            + (
                "the client's own."
                if content.records.identifiers == "real"
                else "masked: the same person is the same code throughout, and the code "
                "cannot be turned back into an id."
            ),
            font=WARNING,
        )
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


PATTERN_COLUMNS = [
    "View",
    "Type of misconduct",
    "Employee type",
    "Work location",
    "Result",
    "Actions",
    "People",
    "Employee-years",
    "Actions elsewhere",
    "People elsewhere",
    "Employee-years elsewhere",
    "Expected at the rate elsewhere",
    "Times the rate elsewhere",
    "Adjusted chance value",
    "Cost of these actions",
    "Estimated excess (not in any total)",
    "How it was worked out",
]
PATTERN_RESULTS = {
    "flagged": "Flagged",
    "concentrated": "Not flagged: concentrated in few people",
    "not_flagged": "Not flagged",
}


def _pattern_rows(sheet, start: int, label: str, rows) -> int:
    row = start
    for found in rows:
        _put(sheet, row, 1, label)
        _put(sheet, row, 2, found.topic_label or "All types")
        _put(sheet, row, 3, found.row_label)
        _put(sheet, row, 4, found.col_label)
        _put(sheet, row, 5, PATTERN_RESULTS[found.status])
        _put(sheet, row, 6, found.actions)
        _put(sheet, row, 7, found.people)
        _put(sheet, row, 8, found.employee_years)
        _put(sheet, row, 9, found.comparison_actions)
        _put(sheet, row, 10, found.comparison_people)
        _put(sheet, row, 11, found.comparison_employee_years)
        _put(sheet, row, 12, found.expected_actions)
        _put(sheet, row, 13, found.ratio if found.ratio is not None else "n/a")
        _put(sheet, row, 14, found.adjusted_chance, number_format="0.0000")
        _put(sheet, row, 15, found.net, number_format=MONEY)
        if found.estimated_excess_cost is not None:
            _put(sheet, row, 16, found.estimated_excess_cost, number_format=MONEY)
        worked = found.explanation
        if found.formula:
            worked += f" Estimated excess: {found.formula}."
        _put(sheet, row, 17, worked)
        row += 1
    return row


PATTERN_WIDTHS = [30, 26, 28, 30, 22, 9, 9, 15, 17, 16, 23, 28, 23, 21, 22, 32, 120]


def _patterns(book: Workbook, content: ReportContent) -> None:
    """The flagged units: the same ones the report lists, with the figures behind each."""
    section = content.patterns
    sheet = book.create_sheet("Patterns")
    _header(sheet, 1, PATTERN_COLUMNS)
    row = 2
    for view in section.views:
        row = _pattern_rows(sheet, row, view.label, view.flagged)
    if not section.flagged:
        _put(sheet, row, 1, "Nothing stood out.")
        row += 1
    row += 1
    for view in section.views:
        text = f"{view.label}: {view.comparisons:,} comparisons made, {len(view.flagged)} shown"
        if view.withheld:
            text += (
                f", {view.withheld} more stood out and not shown here, to protect a group "
                f"of fewer than {section.min_people} people"
            )
        _put(sheet, row, 1, text + ".", font=MUTED)
        row += 1
    if section.window_start and section.window_end:
        period = f"{section.window_start.isoformat()} to {section.window_end.isoformat()}"
        _put(sheet, row, 1, f"Period: {period}.", font=MUTED)
        row += 1
    _put(
        sheet,
        row,
        1,
        f"A flag needs an adjusted chance value of {section.false_discovery_rate:f} or less, "
        f"a rate at least {section.min_rate_ratio:f} times the rate elsewhere, and at least "
        f"{section.min_people} different people disciplined.",
        font=MUTED,
    )
    row += 1
    if section.unreviewed:
        _put(
            sheet,
            row,
            1,
            "The thresholds that decide a flag are the product's starting values and are "
            "still under review.",
            font=WARNING,
        )
        row += 1
    _put(sheet, row, 1, section.note, font=MUTED)
    _put(sheet, row + 1, 1, section.cost_note, font=MUTED)
    _widths(sheet, PATTERN_WIDTHS)


def _patterns_working(book: Workbook, content: ReportContent) -> None:
    """The working papers: every unit assessed, flagged or not, and those that could not
    be. Never in the client's copy: this list would give away its small groups."""
    section = content.patterns
    sheet = book.create_sheet("Patterns working")
    _header(sheet, 1, PATTERN_COLUMNS)
    row = 2
    for view in section.views:
        row = _pattern_rows(sheet, row, view.label, view.working)
    row += 1
    missing = [(view.label, *entry) for view in section.views for entry in view.not_assessed]
    if missing:
        _put(sheet, row, 1, "Not assessed", font=BOLD)
        row += 1
        for label, job, place, reason in missing:
            _put(sheet, row, 1, label)
            _put(sheet, row, 3, job)
            _put(sheet, row, 4, place)
            _put(sheet, row, 5, reason)
            row += 1
        row += 1
    _put(sheet, row, 1, content.records.note, font=WARNING)
    _widths(sheet, PATTERN_WIDTHS)


def _interventions(book: Workbook, content: ReportContent) -> None:
    """What was found on site and what is proposed: the figures behind the report's
    section. The causes are named by label; the words typed for "Other" are not here."""
    section = content.interventions
    sheet = book.create_sheet("Interventions")
    _header(
        sheet,
        1,
        [
            "Where",
            "Found on site",
            "Whose finding",
            "Entered",
            "Proposed",
            "Based on",
            "Years",
            "The intervention costs",
            "The discipline costs, a year",
            "Prevented: low",
            "Prevented: expected",
            "Prevented: high",
            "Net saving: low",
            "Net saving: expected",
            "Net saving: high",
            "Pays for itself when",
            "How the costs were worked out",
        ],
    )
    row = 2
    for found in section.current:
        _put(sheet, row, 1, found.unit_name)
        _put(sheet, row, 2, found.found)
        _put(sheet, row, 3, found.entered_by)
        _put(sheet, row, 4, found.entered_on)
        _put(sheet, row, 5, found.name)
        _put(sheet, row, 6, found.basis)
        _put(sheet, row, 7, found.years)
        _put(sheet, row, 8, found.total_cost, number_format=MONEY)
        worked = found.cost_formula
        if found.annual_net is None:
            reason = (
                "The records now show no discipline action for this unit."
                if section.working
                else section.money_withheld
            )
            _put(sheet, row, 16, reason)
        else:
            _put(sheet, row, 9, found.annual_net, number_format=MONEY)
            for offset, saving in enumerate(found.savings):
                _put(sheet, row, 10 + offset, saving.reduction, number_format=SHARE)
                _put(sheet, row, 13 + offset, saving.net_saving, number_format=MONEY)
            _put(sheet, row, 16, found.break_even_note)
            worked = f"{found.baseline_formula}. {found.cost_formula}"
        _put(sheet, row, 17, worked)
        row += 1
    if not section.current:
        _put(sheet, row, 1, "Nothing shown.")
        row += 1
    row += 1
    if section.not_shown:
        _put(
            sheet,
            row,
            1,
            f"{section.not_shown} more looked into and not shown here: fewer than "
            f"{section.min_people} people concerned.",
            font=MUTED,
        )
        row += 1
    _put(sheet, row, 1, section.note, font=MUTED)
    _put(sheet, row + 1, 1, "A saving assumes: " + " ".join(section.assumes), font=MUTED)
    _widths(sheet, [52, 38, 34, 12, 34, 50, 8, 22, 26, 16, 20, 16, 18, 22, 18, 70, 110])


def _interventions_working(book: Workbook, content: ReportContent) -> None:
    """The working papers: every appraisal ever kept, with what "Other" was said to be and
    what was withdrawn. Never in the client's copy."""
    section = content.interventions
    sheet = book.create_sheet("Interventions working")
    _header(
        sheet,
        1,
        [
            "Where",
            "Found on site",
            "Other: what was said",
            "Whose finding",
            "Entered",
            "Proposed",
            "Standing",
            "Withdrawn by",
            "Withdrawn on",
        ],
    )
    row = 2
    for found in section.rows:
        _put(sheet, row, 1, found.unit_name)
        _put(sheet, row, 2, found.found)
        _put(sheet, row, 3, found.other_cause)
        _put(sheet, row, 4, found.entered_by)
        _put(sheet, row, 5, found.entered_on)
        _put(sheet, row, 6, found.name)
        _put(sheet, row, 7, "Withdrawn" if found.withdrawn_on else "Kept")
        _put(sheet, row, 8, found.withdrawn_by)
        _put(sheet, row, 9, found.withdrawn_on)
        row += 1
    _put(sheet, row + 1, 1, content.records.note, font=WARNING)
    _widths(sheet, [52, 38, 70, 34, 12, 34, 12, 34, 14])


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


def _actions(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Actions")
    _header(
        sheet,
        1,
        [
            "Action",
            "Employee",
            "Decided",
            "Department",
            "Role",
            "Misconduct",
            "Action taken",
            "Gross",
            "Savings",
            "Net",
            "Still accruing",
        ],
    )
    row = 2
    for a in content.records.actions:
        _put(sheet, row, 1, a.action_id)
        _put(sheet, row, 2, a.employee)
        _put(sheet, row, 3, a.decision_date)
        _put(sheet, row, 4, a.department)
        _put(sheet, row, 5, a.role)
        _put(sheet, row, 6, a.misconduct)
        _put(sheet, row, 7, a.action_type)
        _put(sheet, row, 8, a.gross, number_format=MONEY)
        _put(sheet, row, 9, a.offset, number_format=MONEY)
        _put(sheet, row, 10, a.net, number_format=MONEY)
        _put(sheet, row, 11, "yes" if a.still_accruing else "")
        row += 1
    # The report's own totals, not a sum worked out here: the rows above add up to them.
    _put(sheet, row, 1, "Total", font=BOLD)
    _put(sheet, row, 8, content.gross, number_format=MONEY, font=BOLD)
    _put(sheet, row, 9, content.offset, number_format=MONEY, font=BOLD)
    _put(sheet, row, 10, content.net, number_format=MONEY, font=BOLD)
    _put(sheet, row + 2, 1, content.records.note, font=WARNING)
    _widths(sheet, [14, 16, 12, 34, 28, 30, 22, 16, 16, 16, 14])


def _line_items(book: Workbook, content: ReportContent) -> None:
    sheet = book.create_sheet("Line items")
    _header(
        sheet,
        1,
        [
            "Action",
            "Employee",
            "Code",
            "Component",
            "What",
            "Amount",
            "How it was worked out",
            "Assumptions used",
            "Still accruing",
        ],
    )
    row = 2
    for item in content.records.line_items:
        _put(sheet, row, 1, item.action_id)
        _put(sheet, row, 2, item.employee)
        _put(sheet, row, 3, item.component)
        _put(sheet, row, 4, item.component_label)
        _put(sheet, row, 5, item.subcomponent.replace("_", " "))
        _put(sheet, row, 6, item.amount, number_format=MONEY)
        _put(sheet, row, 7, item.formula)
        _put(sheet, row, 8, item.assumption_ids)
        _put(sheet, row, 9, "yes" if item.still_accruing else "")
        row += 1
    _put(sheet, row, 1, "Net", font=BOLD)
    _put(sheet, row, 6, content.net, number_format=MONEY, font=BOLD)
    _put(sheet, row + 2, 1, content.records.note, font=WARNING)
    _widths(sheet, [14, 16, 11, 34, 28, 16, 110, 60, 14])


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
    if content.patterns:
        _patterns(book, content)
    if content.interventions:
        _interventions(book, content)
    _departments(book, content)
    _quality(book, content)
    _assumptions(book, content)
    _overrides(book, content)
    if content.records:
        _actions(book, content)
        _line_items(book, content)
        if content.patterns:
            _patterns_working(book, content)
        if content.interventions:
            _interventions_working(book, content)
        book.properties.title = f"{book.properties.title} - working papers"
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()
