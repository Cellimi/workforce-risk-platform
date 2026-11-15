"""What a client deliverable says, before anything decides what it looks like.

A consultant hands a client two things: a report to read and a workbook to work in. They must
say the same thing. So neither is built from the engine directly. Both are rendered from one
`ReportContent`, assembled here, and a figure cannot appear in the PDF and differ in the
spreadsheet.

Everything in a `ReportContent` is something the caller was already allowed to see:

* the matrix arrives **already suppressed**, built by the API with the caller's own
  disclosure ledger, exactly as the on-screen matrix is (CLAUDE.md rule 10);
* the data-quality section carries counts by rule and nothing else;
* the copy for the client is aggregates only: no employee id and no action id reaches it.

There is one exception, and it is a different file. A caller entitled to record-level detail
may ask for the **working papers**: the same workbook with every action and every line item
added (`RecordSection`). It is assembled only when the API has already checked that
entitlement, and the identifiers in it arrive here already masked for the caller, so nothing
in this module decides who may see what. A `ReportContent` with no `records` is the client's
copy; one with `records` is never the client's copy, and its file name says so.

Wording for the client is settled here too. The registry records who estimated a value
("Owner professional estimate"); a report a consulting firm hands its client says
"Professional estimate". `client_wording` makes that change in one place, so the PDF and
the workbook cannot word a source differently, and the registry is left as the record it is.

`build_report` and `build_records` are pure functions: no I/O, no clock read. The date the
report was prepared is passed in.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from wri_engine.aggregation.rollups import Matrix, summarize
from wri_engine.costing.assumptions import AssumptionSet
from wri_engine.costing.engine import CostRun
from wri_engine.schema import (
    COMPONENT_LABELS,
    OFFSET_COMPONENTS,
    CanonicalDataset,
    CostComponent,
    CostLineItem,
)

CENT = Decimal("0.01")
PRODUCT_NAME = "Workforce Risk Intelligence"
REPORT_TITLE = "The Cost of Discipline"

#: The closing note. It is here, not in a renderer, so the PDF and the workbook cannot word
#: the limits of the figures differently.
METHOD_NOTE = (
    "Every figure in this report is the sum of line items, and each line item names its "
    "formula, the values it used and the assumptions behind it. A cost is counted only where "
    "the record shows the event happened: nothing is forecast and nothing is weighted by "
    "likelihood. Where more cost is still to come - a challenge not yet resolved, a post not "
    "yet refilled - the action is counted as still accruing rather than estimated."
)
ASSUMPTION_NOTE = (
    "The figures rest on the assumptions listed in Appendix A. An assumption marked confirmed "
    "is a professional estimate that has been signed off; one marked researched is backed by "
    "the source cited beside it. Sign-off is not verification, and a client's own figure, "
    "where one exists, is better than either. Appendix B lists the values this engagement "
    "uses in place of the defaults, with the reason given for each."
)


#: The registry's words for who stands behind a value, and the words a client reads.
#: Order matters: the longer phrase is replaced before the shorter one inside it.
CLIENT_WORDING = (
    ("Owner professional estimate, accepted by the owner on", "Professional estimate, accepted on"),
    ("Owner professional estimate", "Professional estimate"),
)
RECORDS_NOTE = (
    "WORKING PAPERS - RECORD LEVEL. This copy lists every action and every line item behind "
    "the figures. It is for the engagement team. It is not the client's copy: the client's "
    "workbook is the one without the Actions and Line items sheets."
)


def client_wording(source: str) -> str:
    """A source as a client should read it. Only the attribution changes; the citation,
    the caveats and everything else in the text are left exactly as the registry has them."""
    for registry_words, client_words in CLIENT_WORDING:
        source = source.replace(registry_words, client_words)
    return source


@dataclass(frozen=True)
class Branding:
    """Whose report this is: the consulting firm's name and logo, if it has set them."""

    firm_name: str = ""
    logo: bytes | None = None
    logo_mime: str | None = None

    @property
    def prepared_by(self) -> str:
        return self.firm_name or PRODUCT_NAME


@dataclass(frozen=True)
class ComponentRow:
    code: str
    label: str
    amount: Decimal
    #: Share of gross. None for an offset, which is a saving and not part of gross.
    share_of_gross: Decimal | None
    is_offset: bool


@dataclass(frozen=True)
class ModeRow:
    """One sensitivity mode's totals: every assumption at its low, base or high value."""

    mode: str
    gross: Decimal
    offset: Decimal
    net: Decimal
    annual_net: Decimal | None


@dataclass(frozen=True)
class CellRow:
    """One cell of the cost matrix. A suppressed cell carries its labels and no money."""

    row_label: str
    col_label: str
    suppressed: bool
    suppression_reason: str = ""
    action_count: int | None = None
    employee_count: int | None = None
    gross: Decimal | None = None
    offset: Decimal | None = None
    net: Decimal | None = None
    cost_per_action: Decimal | None = None
    incomplete_count: int | None = None


@dataclass(frozen=True)
class TopCell:
    row_label: str
    col_label: str
    action_count: int
    net: Decimal
    cost_per_action: Decimal


@dataclass(frozen=True)
class DepartmentRow:
    department: str
    active_fte: int
    net: Decimal
    net_per_fte_per_year: Decimal | None


@dataclass(frozen=True)
class AssumptionRow:
    id: str
    value: Decimal
    default_value: Decimal
    unit: str
    low: Decimal
    high: Decimal
    status: str
    confidence: str
    source: str
    overridden: bool


@dataclass(frozen=True)
class OverrideRow:
    assumption_id: str
    value: Decimal
    default_value: Decimal
    unit: str
    reason: str
    set_by: str
    set_on: date


@dataclass(frozen=True)
class QualityRow:
    rule_id: str
    count: int


@dataclass(frozen=True)
class ActionRow:
    """One costed action. `employee` is the identifier the caller may see, already masked
    where their role requires it."""

    action_id: str
    employee: str
    decision_date: str
    department: str
    role: str
    misconduct: str
    action_type: str
    gross: Decimal
    offset: Decimal
    net: Decimal
    still_accruing: bool


@dataclass(frozen=True)
class LineItemRow:
    """One explainable amount: what it is, how it was worked out, and what it rests on."""

    action_id: str
    employee: str
    component: str
    component_label: str
    subcomponent: str
    amount: Decimal
    formula: str
    assumption_ids: str
    still_accruing: bool


@dataclass(frozen=True)
class RecordSection:
    """Every action and line item behind a report's figures: the working papers.

    Amounts are carried unrounded, exactly as the engine produced them, so the sheet sums to
    the report's totals and not to something a cent away.
    """

    #: "real" or "masked": which kind of employee identifier this copy carries.
    identifiers: str
    actions: list[ActionRow]
    line_items: list[LineItemRow]
    note: str = RECORDS_NOTE


@dataclass(frozen=True)
class ReportContent:
    # -- whose it is and what it covers
    branding: Branding
    client_name: str
    engagement_name: str
    cost_model: str
    occupation_group: str
    caption: str
    generated_on: date
    mode: str
    as_of: date | None
    window_years: Decimal
    # -- headline
    actions_costed: int
    actions_excluded: int
    actions_incomplete: int
    active_employees: int
    gross: Decimal
    offset: Decimal
    net: Decimal
    annual_net: Decimal | None
    net_per_employee_per_year: Decimal | None
    # -- breakdowns
    components: list[ComponentRow]
    modes: list[ModeRow]
    top_cells: list[TopCell]
    departments: list[DepartmentRow]
    # -- the matrix, as suppressed for this caller
    row_dimension: str
    col_dimension: str
    cells: list[CellRow]
    min_cell_size: int
    suppressed_cell_count: int
    suppressed_gross: Decimal
    suppressed_net: Decimal
    # -- data quality: counts only
    quality_blocking: int
    quality_warnings: int
    quality: list[QualityRow]
    # -- appendices
    assumptions_used: list[AssumptionRow]
    registry_size: int
    overrides: list[OverrideRow]
    standing: str
    method_note: str = METHOD_NOTE
    assumption_note: str = ASSUMPTION_NOTE
    title: str = REPORT_TITLE
    #: The working papers. None in the client's copy, which is aggregates only.
    records: RecordSection | None = None
    extra: dict = field(default_factory=dict)

    @property
    def visible_cells(self) -> list[CellRow]:
        """The cells that carry money, dearest first."""
        shown = [c for c in self.cells if not c.suppressed]
        return sorted(shown, key=lambda c: c.net or Decimal(0), reverse=True)

    @property
    def file_stem(self) -> str:
        """A safe file name: letters, digits and underscores from the client's name."""
        cleaned = "".join(ch if ch.isalnum() else "_" for ch in self.client_name.lower())
        cleaned = "_".join(part for part in cleaned.split("_") if part)
        stem = f"{cleaned or 'client'}_cost_of_discipline"
        # The two copies must never be mistaken for each other in a folder or an email.
        return f"{stem}_working_papers" if self.records else stem


def _money(value) -> Decimal:
    return Decimal(str(value)).quantize(CENT)


def _optional_money(value) -> Decimal | None:
    return None if value in (None, "") else _money(value)


def _components(run: CostRun) -> list[ComponentRow]:
    """Every component with money against it, in the engine's own order, labelled from the
    one place the labels live -- so a private engagement's C6 and C7 are never left out."""
    totals: dict[str, Decimal] = {}
    for item in run.line_items:
        totals[str(item.component)] = totals.get(str(item.component), Decimal(0)) + item.amount
    gross = run.gross
    rows = []
    for component in CostComponent:
        if str(component) not in totals:
            continue
        amount = totals[str(component)].quantize(CENT)
        is_offset = component in OFFSET_COMPONENTS
        share = None if is_offset or not gross else (amount / gross).quantize(Decimal("0.0001"))
        rows.append(
            ComponentRow(
                code=str(component),
                label=COMPONENT_LABELS[component],
                amount=amount,
                share_of_gross=share,
                is_offset=is_offset,
            )
        )
    return rows


def _cells(matrix: Matrix) -> list[CellRow]:
    rows = []
    for cell in matrix.cells.values():
        if cell.suppressed:
            rows.append(
                CellRow(
                    row_label=cell.row_label,
                    col_label=cell.col_label,
                    suppressed=True,
                    suppression_reason=cell.suppression_reason or "suppressed",
                )
            )
            continue
        rows.append(
            CellRow(
                row_label=cell.row_label,
                col_label=cell.col_label,
                suppressed=False,
                action_count=cell.action_count,
                employee_count=cell.employee_count,
                gross=_money(cell.gross),
                offset=_money(cell.offset),
                net=_money(cell.net),
                cost_per_action=_money(cell.cost_per_action),
                incomplete_count=cell.incomplete_count,
            )
        )
    return rows


def _assumptions_used(
    run: CostRun, effective: AssumptionSet, defaults: AssumptionSet
) -> list[AssumptionRow]:
    """The assumptions this run actually consumed -- read off the line items, so the
    appendix is the ones behind these figures and not the whole registry."""
    used = sorted({a for item in run.line_items for a in item.assumption_ids})
    rows = []
    for assumption_id in used:
        record = effective.get(assumption_id)
        value = effective.value(assumption_id)
        rows.append(
            AssumptionRow(
                id=assumption_id,
                value=value,
                # The registry's own figure in the same sensitivity mode, so the two columns
                # differ only where the engagement has set a value of its own.
                default_value=defaults.value(assumption_id),
                unit=record.unit,
                low=record.low,
                high=record.high,
                status=record.status,
                confidence=record.confidence,
                source=client_wording(record.source),
                overridden=assumption_id in effective.overrides,
            )
        )
    return rows


def build_records(
    run: CostRun,
    *,
    identifiers: str,
    identify: Callable[[str], str],
    explain: Callable[[CostLineItem], str],
) -> RecordSection:
    """List every action and line item of a run, dearest action first.

    Who may see what is not decided here. `identify` turns an employee id into the one the
    caller may see, and `explain` returns a line item's formula with any colleague it names
    masked the same way; both come from the access layer by way of the API.
    """
    actions = sorted(run.action_costs, key=lambda a: (-a.net, a.action_id))
    action_rows, item_rows = [], []
    for action in actions:
        shown_as = identify(action.employee_id)
        d = action.dimensions
        action_rows.append(
            ActionRow(
                action_id=action.action_id,
                employee=shown_as,
                decision_date=str(d.get("decision_date", "")),
                department=str(d.get("department_label", d.get("department", ""))),
                role=str(d.get("role_family_label", d.get("role_family", ""))),
                misconduct=str(
                    d.get("misconduct_category_label", d.get("misconduct_category", ""))
                ),
                action_type=str(d.get("action_type_label", d.get("action_type", ""))),
                gross=action.gross,
                offset=action.offset,
                net=action.net,
                still_accruing=action.cost_incomplete,
            )
        )
        for item in action.line_items:
            item_rows.append(
                LineItemRow(
                    action_id=item.action_id,
                    employee=shown_as,
                    component=str(item.component),
                    component_label=COMPONENT_LABELS[item.component],
                    subcomponent=item.subcomponent,
                    amount=item.amount,
                    formula=explain(item),
                    assumption_ids=", ".join(item.assumption_ids),
                    still_accruing=item.cost_incomplete,
                )
            )
    return RecordSection(identifiers=identifiers, actions=action_rows, line_items=item_rows)


def build_report(
    *,
    runs: dict[str, CostRun],
    mode: str,
    dataset: CanonicalDataset,
    matrix: Matrix,
    row_dimension: str,
    col_dimension: str,
    min_cell_size: int,
    quality_counts: dict[str, int],
    quality_blocking: int,
    quality_warnings: int,
    effective_assumptions: AssumptionSet,
    default_assumptions: AssumptionSet,
    overrides: list[OverrideRow],
    branding: Branding,
    client_name: str,
    engagement_name: str,
    cost_model: str,
    occupation_group: str,
    caption: str,
    standing: str,
    generated_on: date,
    records: RecordSection | None = None,
) -> ReportContent:
    """Assemble a deliverable's content from runs the caller is already entitled to.

    `runs` holds one costing run per sensitivity mode; `mode` picks the one the report is
    about, and the others give the low-to-high range. `matrix` must already be suppressed.
    """
    run = runs[mode]
    summary = summarize(run, dataset, min_cell_size=min_cell_size)
    window = Decimal(summary["window_years"])

    modes = []
    for name in ("low", "base", "high"):
        if name not in runs:
            continue
        other = runs[name]
        net = other.gross + other.offset
        modes.append(
            ModeRow(
                mode=name,
                gross=_money(other.gross),
                offset=_money(other.offset),
                net=_money(net),
                annual_net=_money(net / window) if window else None,
            )
        )

    return ReportContent(
        branding=branding,
        client_name=client_name,
        engagement_name=engagement_name,
        cost_model=cost_model,
        occupation_group=occupation_group,
        caption=caption,
        generated_on=generated_on,
        mode=mode,
        as_of=run.as_of,
        window_years=window,
        actions_costed=summary["actions_costed"],
        actions_excluded=summary["actions_excluded"],
        actions_incomplete=summary["incomplete_actions"],
        active_employees=summary["active_fte"],
        gross=_money(summary["total_gross"]),
        offset=_money(summary["total_offset"]),
        net=_money(summary["total_net"]),
        annual_net=_optional_money(summary["annual_net"]),
        net_per_employee_per_year=_optional_money(summary["net_per_fte_per_year"]),
        components=_components(run),
        modes=modes,
        top_cells=[
            TopCell(
                row_label=c["row_label"],
                col_label=c["col_label"],
                action_count=c["action_count"],
                net=_money(c["net"]),
                cost_per_action=_money(c["cost_per_action"]),
            )
            for c in summary["top_cells"]
        ],
        departments=[
            DepartmentRow(
                department=d["department"],
                active_fte=d["active_fte"],
                net=_money(d["net"]),
                net_per_fte_per_year=_optional_money(d["net_per_fte_per_year"]),
            )
            for d in summary["department_cost_per_fte_per_year"]
        ],
        row_dimension=row_dimension,
        col_dimension=col_dimension,
        cells=_cells(matrix),
        min_cell_size=matrix.min_cell_size,
        suppressed_cell_count=sum(1 for c in matrix.cells.values() if c.suppressed),
        suppressed_gross=_money(matrix.suppressed_gross),
        suppressed_net=_money(matrix.suppressed_net),
        quality_blocking=quality_blocking,
        quality_warnings=quality_warnings,
        quality=[QualityRow(rule, count) for rule, count in sorted(quality_counts.items())],
        assumptions_used=_assumptions_used(run, effective_assumptions, default_assumptions),
        registry_size=len(default_assumptions.assumptions),
        overrides=overrides,
        standing=standing,
        records=records,
    )
