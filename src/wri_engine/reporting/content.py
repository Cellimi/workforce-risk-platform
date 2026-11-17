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

**Patterns** (`PatternSection`) are where the report calls out to the client's leadership
where a type of misconduct clusters and what it costs there. The same rule holds: every
flagged unit arrives here already decided. In the client's copy each one has passed the
small-group rules that govern a cell of the matrix, and one that failed is only counted. The
working behind the flags, every unit assessed and not flagged, is in the working papers and
never in the client's copy.

**Interventions** (`InterventionSection`) are what Phase 3 comes to: what someone found on
site in a unit, what is proposed for it, what that would cost and what it would save. Again
each entry arrives already decided for the copy being made:

* the client's copy names a cause by its label only. The words a person typed for "Other"
  never reach it (decided by the owner on 2026-11-16); they are in the working papers;
* an entry about fewer people than the minimum group is left out of the client's copy and
  counted, because a cause said of two or three people is said of them;
* where the unit's cost may not be released beside the other figures in the report, the
  entry keeps the finding and the proposal and says the money is withheld.

A saving is a projection. The section says so, says what it assumes, and adds to no total.

`build_report`, `build_records`, `build_patterns` and `build_interventions` are pure
functions: no I/O, no clock read. The date the report was prepared is passed in.
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


PATTERN_NOTE = (
    "A pattern is flagged where discipline in one job at one work location was more "
    "frequent than in the same job at the other locations: by more than chance would "
    "explain, by enough to matter, and among enough different people that it is not the "
    "record of a few individuals. A flag says where to look. It does not say why the rate "
    "is higher, and it says nothing about any one person."
)
PATTERN_COST_NOTE = (
    "The cost of the actions is what the records show those actions cost, and is already "
    "part of every total in this report. The estimated excess is the number of actions "
    "beyond what the rate elsewhere would give, at this unit's average cost per action. It "
    "is an estimate, and it is not added to any total."
)
RISING_NOTE = (
    "A unit is flagged as rising where its rate in the later half of the period was higher "
    "than in the earlier half, by more than the same type of misconduct rose in the rest of "
    "the organization: by more than chance would explain, by enough to matter, and among "
    "enough different people. A flag says where to look. It does not say why, and it says "
    "nothing about any one person."
)
RISING_COST_NOTE = (
    "The cost in each half is what the records show those actions cost, and is already part "
    "of every total in this report. The estimated excess is the later half's actions beyond "
    "what the earlier rate would have given had it changed only as the rest of the "
    "organization did, at this unit's average cost per action. It is an estimate, and it is "
    "not added to any total."
)
PATTERN_VIEW_LABELS = {
    "misconduct": "By type of misconduct",
    "overall": "Every type of misconduct together",
}


INTERVENTION_NOTE = (
    "Each cause below is the finding of the person named, made on site. This product did "
    "not find it and does not check it. What an intervention costs, and how well it is "
    "expected to work, are that person's figures, with their stated basis. A saving is a "
    "projection: it is not part of any total in this report."
)
MONEY_WITHHELD = (
    "What the discipline costs in this unit is not shown: with the other figures in this "
    "report it would let a group of fewer than {min_people} people be worked out. So no "
    "saving is shown either."
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
class PatternRow:
    """One unit: a job at a work location, for one type of misconduct or for all of them."""

    status: str
    topic_label: str
    row_label: str
    col_label: str
    actions: int
    people: int
    employee_years: Decimal
    #: The comparison's figures. None when they may not be released in this copy.
    comparison_actions: int | None
    comparison_people: int | None
    comparison_employee_years: Decimal | None
    expected_actions: Decimal | None
    #: The unit's rate over the rate elsewhere. None when elsewhere had no such action.
    ratio: Decimal | None
    adjusted_chance: float | None
    #: What the cost baseline shows these actions cost. An actual.
    net: Decimal
    #: Flagged units only. An estimate, and in no total.
    estimated_excess_cost: Decimal | None
    explanation: str
    formula: str
    #: True when this copy shows the unit's own figures and not its comparison's, because
    #: those would let a small group be worked out. The explanation says so.
    comparison_withheld: bool = False

    @property
    def name(self) -> str:
        where = f"{self.row_label} at {self.col_label}"
        return f"{self.topic_label} among {where}" if self.topic_label else where


@dataclass(frozen=True)
class PatternView:
    """One way of looking: by type of misconduct, or every type together."""

    by: str
    label: str
    comparisons: int
    flagged: list[PatternRow]
    #: Flagged, and left out of this copy to protect a small group.
    withheld: int
    #: The working papers only: every unit assessed, flagged or not.
    working: list[PatternRow] = field(default_factory=list)
    #: The working papers only: (job, location, why it could not be assessed).
    not_assessed: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class RisingRow:
    """One unit, its earlier half against its later half."""

    status: str
    topic_label: str
    row_label: str
    col_label: str
    earlier_actions: int
    earlier_people: int
    later_actions: int
    later_people: int
    #: The unit's later rate over its earlier rate. None when it had no earlier action.
    own_change: Decimal | None
    #: The rest of the organization's figures. None when they may not be released in this copy.
    elsewhere_earlier_actions: int | None
    elsewhere_later_actions: int | None
    elsewhere_change: Decimal | None
    #: How many times the rest's rise the unit's was. None when either is.
    ratio: Decimal | None
    adjusted_chance: float | None
    #: What the cost baseline shows the unit's actions cost in each half. Actuals.
    earlier_net: Decimal
    later_net: Decimal
    #: Flagged units only, and only when there is a change elsewhere to go by. An estimate.
    estimated_excess_cost: Decimal | None
    explanation: str
    formula: str
    #: True when this copy shows the unit's own figures and not the rest's.
    comparison_withheld: bool = False

    @property
    def name(self) -> str:
        where = f"{self.row_label} at {self.col_label}"
        return f"{self.topic_label} among {where}" if self.topic_label else where


@dataclass(frozen=True)
class RisingView:
    """One way of looking, for rises: by type of misconduct, or every type together."""

    by: str
    label: str
    comparisons: int
    flagged: list[RisingRow]
    #: Flagged, and left out of this copy to protect a small group.
    withheld: int
    #: The working papers only: every unit assessed, flagged or not.
    working: list[RisingRow] = field(default_factory=list)
    #: The working papers only: (job, location, why it could not be assessed).
    not_assessed: list[tuple[str, str, str]] = field(default_factory=list)


@dataclass(frozen=True)
class RisingSection:
    earlier_start: date | None
    earlier_end: date | None
    later_start: date | None
    later_end: date | None
    views: list[RisingView]
    note: str = RISING_NOTE
    cost_note: str = RISING_COST_NOTE

    @property
    def flagged(self) -> list[RisingRow]:
        return [row for view in self.views for row in view.flagged]


@dataclass(frozen=True)
class PatternSection:
    window_start: date | None
    window_end: date | None
    min_people: int
    false_discovery_rate: Decimal
    min_rate_ratio: Decimal
    #: Threshold ids the owner has not reviewed. The report says so when there are any.
    unreviewed: tuple[str, ...]
    views: list[PatternView]
    note: str = PATTERN_NOTE
    cost_note: str = PATTERN_COST_NOTE
    #: Units rising over the period. None when the report was asked for without it.
    rising: RisingSection | None = None

    @property
    def flagged(self) -> list[PatternRow]:
        return [row for view in self.views for row in view.flagged]

    @property
    def withheld(self) -> int:
        return sum(view.withheld for view in self.views)


@dataclass(frozen=True)
class SavingRow:
    """One reading of how well an intervention works."""

    label: str
    reduction: Decimal
    prevented_total: Decimal
    net_saving: Decimal


@dataclass(frozen=True)
class InterventionRow:
    """An intervention proposed for a unit, with what was found there."""

    unit_name: str
    #: The causes found, by label. "Other" is only ever the word "Other" here.
    causes: tuple[str, ...]
    #: What "Other" was said to be. Empty in the client's copy, always.
    other_cause: str
    entered_by: str
    entered_on: str
    name: str
    basis: str
    years: int
    total_cost: Decimal
    cost_formula: str
    #: What the discipline costs a year, from the baseline. None when it is withheld from
    #: this copy, or when the records no longer show any action for the unit.
    annual_net: Decimal | None
    baseline_formula: str
    savings: tuple[SavingRow, ...]
    break_even_note: str
    #: What must be said when a figure is the product's unreviewed placeholder and not the
    #: client's own. Empty when none is. Carried in every copy, the client's included.
    placeholder_note: str = ""
    #: The types of misconduct it is aimed at, by label. Empty: every type. When set, the
    #: yearly cost above and every saving are for those actions only.
    aimed_at: str = ""
    #: The working papers only.
    withdrawn_by: str = ""
    withdrawn_on: str = ""

    @property
    def found(self) -> str:
        return ", ".join(self.causes)


@dataclass(frozen=True)
class ComparedOption:
    """One of several interventions kept for a unit, on yearly terms."""

    name: str
    #: The causes it answers, by label.
    found: str
    years: int
    yearly_cost: Decimal
    yearly_net_low: Decimal
    yearly_net_expected: Decimal
    yearly_net_high: Decimal
    #: The share of actions it must prevent to pay for itself, or None.
    break_even: Decimal | None


@dataclass(frozen=True)
class SideBySide:
    """The interventions kept for one unit, next to each other."""

    unit_name: str
    annual_net: Decimal
    options: tuple[ComparedOption, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class InterventionSection:
    rows: list[InterventionRow]
    #: Kept, and left out of this copy to protect a small group.
    not_shown: int
    min_people: int
    assumes: tuple[str, ...]
    #: True for the working papers, which carry the words for "Other" and what was withdrawn.
    working: bool = False
    #: Units with two or more interventions shown with their money, set next to each other.
    side_by_side: tuple[SideBySide, ...] = ()
    note: str = INTERVENTION_NOTE

    @property
    def current(self) -> list[InterventionRow]:
        return [row for row in self.rows if not row.withdrawn_on]

    @property
    def money_withheld(self) -> str:
        return MONEY_WITHHELD.format(min_people=self.min_people)


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
    #: Where a type of misconduct clusters. None when the report was asked for without it.
    patterns: PatternSection | None = None
    #: What was found on site and what is proposed. None when nothing is kept, or when the
    #: report was asked for without it.
    interventions: InterventionSection | None = None
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


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _pattern_row(found: dict) -> PatternRow:
    ratio = found["ratio"]
    return PatternRow(
        status=found["status"],
        topic_label=found["topic_label"],
        row_label=found["row_label"],
        col_label=found["col_label"],
        actions=found["actions"],
        people=found["people"],
        employee_years=Decimal(str(found["employee_years"])),
        comparison_actions=found["comparison_actions"],
        comparison_people=found["comparison_people"],
        comparison_employee_years=_decimal(found["comparison_employee_years"]),
        expected_actions=_decimal(found["expected_actions"]),
        ratio=None if ratio is None else Decimal(str(ratio)),
        adjusted_chance=found["q_value"],
        net=_money(found["net"]),
        estimated_excess_cost=_optional_money(found["estimated_excess_cost"]),
        explanation=found["explanation"],
        formula=found["formula"],
        comparison_withheld=found.get("comparison_withheld", False),
    )


def _day(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _rising_row(found: dict) -> RisingRow:
    return RisingRow(
        status=found["status"],
        topic_label=found["topic_label"],
        row_label=found["row_label"],
        col_label=found["col_label"],
        earlier_actions=found["earlier_actions"],
        earlier_people=found["earlier_people"],
        later_actions=found["later_actions"],
        later_people=found["later_people"],
        own_change=_decimal(found["own_change"]),
        elsewhere_earlier_actions=found["elsewhere_earlier_actions"],
        elsewhere_later_actions=found["elsewhere_later_actions"],
        elsewhere_change=_decimal(found["elsewhere_change"]),
        ratio=_decimal(found["ratio"]),
        adjusted_chance=found["q_value"],
        earlier_net=_money(found["earlier_net"]),
        later_net=_money(found["later_net"]),
        estimated_excess_cost=_optional_money(found["estimated_excess_cost"]),
        explanation=found["explanation"],
        formula=found["formula"],
        comparison_withheld=found.get("comparison_withheld", False),
    )


def build_rising(views: list[dict]) -> RisingSection | None:
    """Turn drift's findings into a report section, **as already decided for this copy**:
    `DriftReport.for_totals_reader` for the client's copy, `DriftReport.as_dict` for the
    working papers. As with `build_patterns`, nothing here chooses what may be shown."""
    if not views:
        return None
    first = views[0]
    return RisingSection(
        earlier_start=_day(first["earlier_start"]),
        earlier_end=_day(first["earlier_end"]),
        later_start=_day(first["later_start"]),
        later_end=_day(first["later_end"]),
        views=[
            RisingView(
                by=view["by"],
                label=PATTERN_VIEW_LABELS[view["by"]],
                comparisons=view["comparisons"],
                flagged=[_rising_row(f) for f in view["flagged"]],
                withheld=view["withheld"],
                working=[
                    _rising_row(f)
                    for key in ("flagged", "concentrated", "not_flagged")
                    for f in view.get(key, [])
                ]
                if view["detail"] == "full"
                else [],
                not_assessed=[
                    (n["row_label"], n["col_label"], n["reason"])
                    for n in view.get("not_assessed", [])
                ],
            )
            for view in views
        ],
    )


def build_patterns(views: list[dict], rising: list[dict] | None = None) -> PatternSection | None:
    """Turn detection's findings into a report section.

    Each item of `views` is what detection gave for one way of looking, **as already
    decided for this copy**: `HotspotReport.for_totals_reader` for the client's copy,
    `HotspotReport.as_dict` for the working papers. Nothing here chooses what may be shown;
    it only carries what it was given, so a unit that was not flagged can reach the working
    papers and cannot reach the client's copy.
    """
    if not views:
        return None
    first = views[0]
    settings = first["settings"]
    # A threshold not yet reviewed is said once, whichever detector used it.
    unreviewed = list(settings["unreviewed"])
    for view in rising or []:
        unreviewed += [i for i in view["settings"]["unreviewed"] if i not in unreviewed]
    return PatternSection(
        window_start=date.fromisoformat(first["window_start"]) if first["window_start"] else None,
        window_end=date.fromisoformat(first["window_end"]) if first["window_end"] else None,
        min_people=first["min_people"],
        false_discovery_rate=Decimal(str(settings["detection_false_discovery_rate"])),
        min_rate_ratio=Decimal(str(settings["detection_min_rate_ratio"])),
        unreviewed=tuple(unreviewed),
        rising=build_rising(rising or []),
        views=[
            PatternView(
                by=view["by"],
                label=PATTERN_VIEW_LABELS[view["by"]],
                comparisons=view["comparisons"],
                flagged=[_pattern_row(f) for f in view["flagged"]],
                withheld=view["withheld"],
                working=[
                    _pattern_row(f)
                    for key in ("flagged", "concentrated", "not_flagged")
                    for f in view.get(key, [])
                ]
                if view["detail"] == "full"
                else [],
                not_assessed=[
                    (n["row_label"], n["col_label"], n["reason"])
                    for n in view.get("not_assessed", [])
                ],
            )
            for view in views
        ],
    )


def _intervention_row(entry: dict, working: bool) -> InterventionRow:
    finding = entry["finding"]
    appraisal = entry.get("appraisal")
    proposal = appraisal["intervention"] if appraisal else entry["intervention"]
    return InterventionRow(
        unit_name=entry["unit_name"],
        causes=tuple(cause["label"] for cause in finding["causes"]),
        # The one place this is decided: a copy that is not the working papers never
        # carries the words, whatever it was handed.
        other_cause=finding.get("other_cause", "") if working else "",
        entered_by=finding["entered_by"],
        entered_on=finding["entered_at"][:10],
        name=proposal["name"],
        basis=proposal["basis"],
        years=proposal["years"],
        total_cost=_money(proposal["total_cost"]),
        cost_formula=proposal["cost_formula"],
        annual_net=(
            _money((appraisal.get("aimed_at") or appraisal["unit"])["annual_net"])
            if appraisal
            else None
        ),
        baseline_formula=appraisal["unit"]["formula"] if appraisal else "",
        savings=tuple(
            SavingRow(
                label=s["label"],
                reduction=Decimal(s["reduction"]),
                prevented_total=_money(s["prevented_total"]),
                net_saving=_money(s["net_saving"]),
            )
            for s in (appraisal["scenarios"] if appraisal else [])
        ),
        break_even_note=appraisal["break_even_note"] if appraisal else "",
        placeholder_note=(appraisal or entry).get("placeholder_note", ""),
        aimed_at=entry.get("aimed_at_label", ""),
        withdrawn_by=entry.get("withdrawn_by", "") if working else "",
        withdrawn_on=entry.get("withdrawn_at", "")[:10] if working else "",
    )


def build_interventions(
    entries: list[dict],
    *,
    not_shown: int,
    min_people: int,
    assumes: tuple[str, ...],
    working: bool = False,
    side_by_side: list[dict] | None = None,
) -> InterventionSection | None:
    """Turn kept appraisals into a report section.

    Each entry is a kept appraisal as the API shows it, with `unit_name` added, **as
    already decided for this copy**: an entry whose `appraisal` is None carries no money,
    and an entry left out was never handed over. One thing is decided here and not left to
    the caller: the words typed for "Other" are carried only when `working` is true.
    """
    if not entries and not not_shown:
        return None
    rows = [_intervention_row(entry, working) for entry in entries]
    found = {entry.get("id"): row.found for entry, row in zip(entries, rows, strict=True)}
    return InterventionSection(
        rows=rows,
        not_shown=not_shown,
        min_people=min_people,
        assumes=assumes,
        working=working,
        side_by_side=tuple(
            SideBySide(
                unit_name=compared["unit"]["name"],
                annual_net=_money(compared["unit"]["annual_net"]),
                options=tuple(
                    ComparedOption(
                        name=option["name"],
                        found=found.get(option["key"], ""),
                        years=option["years"],
                        yearly_cost=_money(option["yearly_cost"]),
                        yearly_net_low=_money(option["yearly_net_saving_low"]),
                        yearly_net_expected=_money(option["yearly_net_saving_expected"]),
                        yearly_net_high=_money(option["yearly_net_saving_high"]),
                        break_even=None
                        if option["break_even"] is None
                        else Decimal(option["break_even"]),
                    )
                    for option in compared["rows"]
                ),
                notes=tuple(compared["notes"]),
            )
            for compared in (side_by_side or [])
        ),
    )


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
    patterns: PatternSection | None = None,
    interventions: InterventionSection | None = None,
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
        patterns=patterns,
        interventions=interventions,
    )
