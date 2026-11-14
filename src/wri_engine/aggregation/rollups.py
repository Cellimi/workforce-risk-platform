"""Rollups: turning line items into the cost matrix and the headline figures.

The default view is **employee type (role family) x misconduct category**, with department
as a toggle. Every cell carries the numbers a reader needs to judge it: how many actions,
how many distinct people, gross cost, the unpaid-suspension offset, net cost, cost per
action, cost per 100 FTE, the C1-C5 component mix, and how many of its records are still
accruing cost.

Suppression is applied here, before anything leaves the engine. A suppressed cell returns no
money at all -- its value is pooled into an "Other (suppressed)" bucket so the row, column
and grand totals still reconcile exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from wri_engine.aggregation.suppression import (
    DEFAULT_MIN_CELL_SIZE,
    DisclosureLedger,
    SuppressionReason,
    apply_complementary_suppression,
)
from wri_engine.costing.engine import ActionCost, CostRun
from wri_engine.schema import CanonicalDataset, CostComponent

#: Dimensions a caller may put on the rows, the columns, or a filter.
DIMENSIONS = {
    "role_family": "Employee type",
    "department": "Department",
    "misconduct_category": "Misconduct type",
    "action_type": "Action type",
    "work_location": "Work location",
    "bargaining_unit": "Bargaining unit",
    "year": "Year",
    "is_sworn": "Sworn / civilian",
}

COMPONENTS = [
    CostComponent.C1_PROCESSING,
    CostComponent.C2_ADMIN_LEAVE,
    CostComponent.C3_BACKFILL,
    CostComponent.C3_OFFSET,
    CostComponent.C4_APPEALS,
    CostComponent.C5_TURNOVER,
    CostComponent.C5_OFFSET,
]
ZERO = Decimal("0")


class UnknownDimensionError(KeyError):
    pass


def _check(dimension: str) -> str:
    if dimension not in DIMENSIONS:
        raise UnknownDimensionError(
            f"{dimension!r} is not a breakdown dimension. Available: "
            + ", ".join(sorted(DIMENSIONS))
        )
    return dimension


def _value(action: ActionCost, dimension: str) -> str:
    return str(action.dimensions[dimension])


def _label(action: ActionCost, dimension: str) -> str:
    return str(action.dimensions.get(f"{dimension}_label", action.dimensions[dimension]))


def matches(action: ActionCost, filters: dict[str, list[str]] | None) -> bool:
    for dimension, allowed in (filters or {}).items():
        _check(dimension)
        if not allowed:
            continue
        if _value(action, dimension) not in {str(a) for a in allowed}:
            return False
    return True


@dataclass
class Cell:
    row: str
    col: str
    row_label: str
    col_label: str
    action_count: int = 0
    employee_count: int = 0
    gross: Decimal = ZERO
    offset: Decimal = ZERO
    incomplete_count: int = 0
    component_mix: dict[str, Decimal] = field(default_factory=dict)
    suppressed: bool = False
    suppression_reason: str | None = None
    cost_per_100_fte: Decimal | None = None

    @property
    def net(self) -> Decimal:
        return self.gross + self.offset

    @property
    def cost_per_action(self) -> Decimal:
        return (self.net / self.action_count) if self.action_count else ZERO

    def as_dict(self) -> dict:
        if self.suppressed:
            return {
                "row": self.row,
                "col": self.col,
                "row_label": self.row_label,
                "col_label": self.col_label,
                "suppressed": True,
                "suppression_reason": self.suppression_reason,
            }
        return {
            "row": self.row,
            "col": self.col,
            "row_label": self.row_label,
            "col_label": self.col_label,
            "suppressed": False,
            "action_count": self.action_count,
            "employee_count": self.employee_count,
            "gross": str(self.gross),
            "offset": str(self.offset),
            "net": str(self.net),
            "cost_per_action": str(self.cost_per_action.quantize(Decimal("0.01"))),
            "cost_per_100_fte": (
                str(self.cost_per_100_fte.quantize(Decimal("0.01")))
                if self.cost_per_100_fte is not None
                else None
            ),
            "incomplete_count": self.incomplete_count,
            "component_mix": {k: str(v) for k, v in self.component_mix.items()},
        }


@dataclass
class Matrix:
    rows: list[str]
    cols: list[str]
    row_labels: dict[str, str]
    col_labels: dict[str, str]
    cells: dict[tuple[str, str], Cell]
    row_dimension: str
    col_dimension: str
    min_cell_size: int
    mode: str = "base"
    withheld_totals: set = field(default_factory=set)
    suppressed_net: Decimal = ZERO
    suppressed_gross: Decimal = ZERO
    suppressed_cells: int = 0
    total_gross: Decimal = ZERO
    total_offset: Decimal = ZERO
    total_actions: int = 0
    total_incomplete: int = 0

    @property
    def total_net(self) -> Decimal:
        return self.total_gross + self.total_offset

    @property
    def visible_net(self) -> Decimal:
        return sum((c.net for c in self.cells.values() if not c.suppressed), ZERO)

    def line_totals(self, axis: str) -> list[dict]:
        """Row or column totals, with any line whose total would give away a suppressed
        cell withheld. See `suppression.apply_complementary_suppression`."""
        keys = self.rows if axis == "row" else self.cols
        labels = self.row_labels if axis == "row" else self.col_labels
        out = []
        for key in keys:
            line = [c for c in self.cells.values() if (c.row if axis == "row" else c.col) == key]
            if (axis, key) in self.withheld_totals:
                out.append({"key": key, "label": labels[key], "suppressed": True})
                continue
            out.append(
                {
                    "key": key,
                    "label": labels[key],
                    "suppressed": False,
                    "actions": sum(c.action_count for c in line),
                    "gross": str(sum((c.gross for c in line), ZERO)),
                    "offset": str(sum((c.offset for c in line), ZERO)),
                    "net": str(sum((c.net for c in line), ZERO)),
                    "incomplete_count": sum(c.incomplete_count for c in line),
                }
            )
        return out

    def reconciles(self) -> bool:
        """Visible cells plus the suppressed bucket must equal the true total, exactly."""
        return self.visible_net + self.suppressed_net == self.total_net

    def as_dict(self) -> dict:
        return {
            "row_dimension": self.row_dimension,
            "row_dimension_label": DIMENSIONS[self.row_dimension],
            "col_dimension": self.col_dimension,
            "col_dimension_label": DIMENSIONS[self.col_dimension],
            "rows": [{"key": r, "label": self.row_labels[r]} for r in self.rows],
            "cols": [{"key": c, "label": self.col_labels[c]} for c in self.cols],
            "cells": [cell.as_dict() for cell in self.cells.values()],
            "row_totals": self.line_totals("row"),
            "col_totals": self.line_totals("col"),
            "totals": {
                "gross": str(self.total_gross),
                "offset": str(self.total_offset),
                "net": str(self.total_net),
                "actions": self.total_actions,
                "incomplete_actions": self.total_incomplete,
            },
            "suppression": {
                "min_cell_size": self.min_cell_size,
                "suppressed_cells": self.suppressed_cells,
                "other_suppressed_gross": str(self.suppressed_gross),
                "other_suppressed_net": str(self.suppressed_net),
                "reconciles": self.reconciles(),
            },
            "mode": self.mode,
        }


def fte_denominators(data: CanonicalDataset, dimension: str) -> dict[str, int]:
    """Active headcount by dimension, for cost-per-100-FTE. Employees only -- an action
    dimension like misconduct type has no FTE denominator."""
    _check(dimension)
    counts: dict[str, int] = {}
    for employee in data.employees:
        if employee.separation_date is not None:
            continue
        key = {
            "role_family": employee.role_family,
            "department": employee.department,
            "work_location": employee.work_location,
            "bargaining_unit": employee.bargaining_unit,
            "is_sworn": str(employee.is_sworn),
        }.get(dimension)
        if key is None:
            return {}
        counts[key] = counts.get(key, 0) + 1
    return counts


def build_matrix(
    run: CostRun,
    *,
    rows: str = "role_family",
    cols: str = "misconduct_category",
    filters: dict[str, list[str]] | None = None,
    min_cell_size: int = DEFAULT_MIN_CELL_SIZE,
    ledger: DisclosureLedger | None = None,
    fte_by_row: dict[str, int] | None = None,
) -> Matrix:
    row_dim, col_dim = _check(rows), _check(cols)
    selected = [ac for ac in run.action_costs if matches(ac, filters)]

    cells: dict[tuple[str, str], Cell] = {}
    cohorts: dict[tuple[str, str], set[str]] = {}
    row_labels: dict[str, str] = {}
    col_labels: dict[str, str] = {}

    for action in selected:
        row_key, col_key = _value(action, row_dim), _value(action, col_dim)
        row_labels.setdefault(row_key, _label(action, row_dim))
        col_labels.setdefault(col_key, _label(action, col_dim))
        key = (row_key, col_key)
        cell = cells.get(key)
        if cell is None:
            cell = cells[key] = Cell(
                row=row_key,
                col=col_key,
                row_label=row_labels[row_key],
                col_label=col_labels[col_key],
            )
            cohorts[key] = set()
        cell.action_count += 1
        cell.gross += action.gross
        cell.offset += action.offset
        cell.incomplete_count += 1 if action.cost_incomplete else 0
        cohorts[key].add(action.employee_id)
        for component, amount in action.by_component().items():
            cell.component_mix[component] = cell.component_mix.get(component, ZERO) + amount

    for key, cell in cells.items():
        cell.employee_count = len(cohorts[key])

    matrix = Matrix(
        rows=sorted(row_labels, key=lambda k: row_labels[k]),
        cols=sorted(col_labels, key=lambda k: col_labels[k]),
        row_labels=row_labels,
        col_labels=col_labels,
        cells=cells,
        row_dimension=row_dim,
        col_dimension=col_dim,
        min_cell_size=min_cell_size,
        mode=run.mode,
        total_gross=sum((ac.gross for ac in selected), ZERO),
        total_offset=sum((ac.offset for ac in selected), ZERO),
        total_actions=len(selected),
        total_incomplete=sum(1 for ac in selected if ac.cost_incomplete),
    )

    _suppress(matrix, cohorts, min_cell_size, ledger)
    _attach_fte(matrix, fte_by_row)
    return matrix


def _suppress(
    matrix: Matrix,
    cohorts: dict[tuple[str, str], set[str]],
    min_cell_size: int,
    ledger: DisclosureLedger | None,
) -> None:
    frozen = {k: frozenset(v) for k, v in cohorts.items()}
    decisions: dict[tuple[str, str], str] = {
        key: SuppressionReason.THRESHOLD
        for key, cohort in frozen.items()
        if len(cohort) < min_cell_size
    }
    withheld = apply_complementary_suppression(
        frozen, decisions, matrix.rows, matrix.cols, min_cell_size
    )

    if ledger is not None:
        for key in sorted(frozen, key=lambda k: (-len(frozen[k]), k)):
            if key in decisions:
                continue
            if ledger.would_expose(frozen[key]):
                decisions[key] = SuppressionReason.DIFFERENCING
            else:
                ledger.record(frozen[key])
        withheld |= apply_complementary_suppression(
            frozen, decisions, matrix.rows, matrix.cols, min_cell_size
        )

    # One suppressed cell in the whole matrix is no protection either: the "Other
    # (suppressed)" bucket would simply be that cell. Give up a second one.
    if len(decisions) == 1:
        visible = [k for k in frozen if k not in decisions]
        if visible:
            victim = min(visible, key=lambda k: (len(frozen[k]), k))
            decisions[victim] = SuppressionReason.COMPLEMENTARY
            withheld |= apply_complementary_suppression(
                frozen, decisions, matrix.rows, matrix.cols, min_cell_size
            )

    matrix.withheld_totals = withheld
    for key, reason in decisions.items():
        cell = matrix.cells[key]
        cell.suppressed = True
        cell.suppression_reason = reason
        matrix.suppressed_gross += cell.gross
        matrix.suppressed_net += cell.net
        matrix.suppressed_cells += 1


def _attach_fte(matrix: Matrix, fte_by_row: dict[str, int] | None) -> None:
    if not fte_by_row:
        return
    for cell in matrix.cells.values():
        fte = fte_by_row.get(cell.row)
        if fte:
            cell.cost_per_100_fte = cell.net / Decimal(fte) * Decimal(100)


# ---------------------------------------------------------------------------
# Headline figures
# ---------------------------------------------------------------------------
def summarize(
    run: CostRun,
    data: CanonicalDataset,
    *,
    min_cell_size: int = DEFAULT_MIN_CELL_SIZE,
    years: Decimal | None = None,
    top_n: int = 5,
) -> dict:
    """The numbers the executive summary page leads with.

    `years` is the length of the history window; the annual figure is the net total divided
    by it. Cost per FTE uses active headcount, not the number of people disciplined.
    """
    active_fte = sum(1 for e in data.employees if e.separation_date is None)
    window = years or _window_years(run)
    net = run.net
    turnover = sum(
        (item.amount for item in run.line_items if item.component == CostComponent.C5_TURNOVER),
        ZERO,
    )

    matrix = build_matrix(
        run, rows="role_family", cols="misconduct_category", min_cell_size=min_cell_size
    )
    top_cells = sorted(
        (c for c in matrix.cells.values() if not c.suppressed),
        key=lambda c: c.net,
        reverse=True,
    )[:top_n]

    dept_matrix = build_matrix(
        run, rows="department", cols="misconduct_category", min_cell_size=min_cell_size
    )
    dept_fte = fte_denominators(data, "department")
    dept_net: dict[str, Decimal] = {}
    for cell in dept_matrix.cells.values():
        dept_net[cell.row] = dept_net.get(cell.row, ZERO) + cell.net

    return {
        "agency_note": (
            "Synthetic data for a fictional county. Cost figures reflect documented "
            "assumptions, not measured agency costs."
        ),
        "mode": run.mode,
        "window_years": str(window),
        "as_of": run.as_of.isoformat() if run.as_of else None,
        "actions_costed": len(run.action_costs),
        "actions_excluded": len(run.excluded_action_ids),
        "active_fte": active_fte,
        "total_gross": str(run.gross),
        "total_offset": str(run.offset),
        "total_net": str(net),
        "annual_net": str((net / window).quantize(Decimal("0.01"))) if window else None,
        "turnover_share": (
            str((turnover / run.gross).quantize(Decimal("0.0001"))) if run.gross else "0"
        ),
        "net_per_fte_per_year": (
            str((net / window / Decimal(active_fte)).quantize(Decimal("0.01")))
            if window and active_fte
            else None
        ),
        "incomplete_actions": run.incomplete_count,
        "component_totals": {
            str(component): str(
                sum((i.amount for i in run.line_items if i.component == component), ZERO)
            )
            for component in COMPONENTS
        },
        "top_cells": [
            {
                "row_label": c.row_label,
                "col_label": c.col_label,
                "net": str(c.net),
                "action_count": c.action_count,
                "cost_per_action": str(c.cost_per_action.quantize(Decimal("0.01"))),
            }
            for c in top_cells
        ],
        "department_cost_per_fte_per_year": sorted(
            (
                {
                    "department": dept,
                    "active_fte": dept_fte.get(dept, 0),
                    "net": str(amount),
                    "net_per_fte_per_year": str(
                        (amount / window / Decimal(dept_fte[dept])).quantize(Decimal("0.01"))
                    ),
                }
                for dept, amount in dept_net.items()
                if dept_fte.get(dept)
            ),
            key=lambda d: Decimal(d["net_per_fte_per_year"]),
            reverse=True,
        ),
    }


def _window_years(run: CostRun) -> Decimal:
    """Length of the history window in years, from the actual span of decision dates.

    Counting distinct calendar years would say "4" for a three-year window that starts in
    September, which would understate the annual figure by a quarter.
    """
    dates = sorted(str(ac.dimensions["decision_date"]) for ac in run.action_costs)
    if not dates:
        return Decimal("1")
    first, last = date.fromisoformat(dates[0]), date.fromisoformat(dates[-1])
    span_days = Decimal((last - first).days)
    return max(span_days / Decimal("365.25"), Decimal("0.25")).quantize(Decimal("0.01"))
