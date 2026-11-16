"""Suppression, complementary suppression, and the differencing guard.

Use limitation is a promise the engine makes, not a UI convention, so these tests hit the
aggregation layer directly rather than going through the API.
"""

from __future__ import annotations

from decimal import Decimal

from wri_engine.aggregation.rollups import build_matrix
from wri_engine.aggregation.suppression import (
    DisclosureLedger,
    SuppressionReason,
    apply_complementary_suppression,
)


def test_small_cells_are_suppressed(run):
    matrix = build_matrix(run, min_cell_size=5)
    for cell in matrix.cells.values():
        if not cell.suppressed:
            assert cell.employee_count >= 5


def test_suppressed_cells_carry_no_money(run):
    matrix = build_matrix(run, min_cell_size=5)
    for cell in matrix.cells.values():
        if cell.suppressed:
            payload = cell.as_dict()
            assert "net" not in payload
            assert "gross" not in payload
            assert "employee_count" not in payload


def test_totals_still_reconcile_after_suppression(run):
    for size in (2, 5, 10, 25):
        matrix = build_matrix(run, min_cell_size=size)
        assert matrix.reconciles(), f"min_cell_size={size} broke reconciliation"
        assert matrix.visible_net + matrix.suppressed_net == matrix.total_net


def test_raising_the_threshold_never_reveals_more(run):
    visible_counts = []
    for size in (2, 5, 10, 25):
        matrix = build_matrix(run, min_cell_size=size)
        visible_counts.append(sum(1 for c in matrix.cells.values() if not c.suppressed))
    assert visible_counts == sorted(visible_counts, reverse=True)


def test_no_line_total_gives_away_a_suppressed_cell(run):
    """A line holding exactly one suppressed cell must not also publish its total."""
    matrix = build_matrix(run, min_cell_size=5)
    for axis, keys, pick in (
        ("row", matrix.rows, lambda c: c.row),
        ("col", matrix.cols, lambda c: c.col),
    ):
        totals = {t["key"]: t for t in matrix.line_totals(axis)}
        for key in keys:
            line = [c for c in matrix.cells.values() if pick(c) == key]
            hidden = sum(1 for c in line if c.suppressed)
            if hidden == 1:
                assert totals[key]["suppressed"], (
                    f"{axis} {key} has one suppressed cell but still publishes its total, "
                    f"so the hidden value can be recovered by subtraction"
                )


def test_a_lone_suppressed_cell_never_stands_alone_in_the_bucket(run):
    """One suppressed cell in the whole matrix would BE the 'Other (suppressed)' bucket."""
    for size in (2, 5, 10, 25):
        matrix = build_matrix(run, min_cell_size=size)
        assert matrix.suppressed_cells != 1


def test_complementary_suppression_picks_the_next_smallest():
    grid = {
        ("r1", "c1"): frozenset({"a", "b"}),  # below threshold
        ("r1", "c2"): frozenset({"c", "d", "e", "f", "g"}),  # smallest visible
        ("r1", "c3"): frozenset({f"x{i}" for i in range(20)}),
    }
    decisions = {("r1", "c1"): SuppressionReason.THRESHOLD}
    withheld = apply_complementary_suppression(grid, decisions, ["r1"], ["c1", "c2", "c3"], 5)
    assert decisions[("r1", "c2")] == SuppressionReason.COMPLEMENTARY
    assert ("r1", "c3") not in decisions
    # The row is protected by the pair. Each column here holds a single cell, so the two
    # suppressed columns must withhold their totals; the visible one need not.
    assert withheld == {("col", "c1"), ("col", "c2")}


def test_a_lone_cell_in_a_row_withholds_the_row_total():
    """Nothing to pair it with, so the row total itself must be withheld."""
    grid = {("r1", "c1"): frozenset({"a", "b"})}
    decisions = {("r1", "c1"): SuppressionReason.THRESHOLD}
    withheld = apply_complementary_suppression(grid, decisions, ["r1"], ["c1"], 5)
    assert ("row", "r1") in withheld
    assert ("col", "c1") in withheld


# ---------------------------------------------------------------------------
# differencing
# ---------------------------------------------------------------------------
def test_ledger_blocks_a_small_residual():
    ledger = DisclosureLedger(min_cell_size=5)
    big = frozenset(f"E{i}" for i in range(40))
    ledger.record(big)
    # 37 of the same 40 people: the difference is 3, which isolates 3 employees.
    assert ledger.would_expose(frozenset(f"E{i}" for i in range(37)))
    # A cohort that differs by 10 is safe.
    assert not ledger.would_expose(frozenset(f"E{i}" for i in range(30)))


def test_ledger_allows_a_disjoint_cohort():
    ledger = DisclosureLedger(min_cell_size=5)
    ledger.record(frozenset(f"E{i}" for i in range(20)))
    assert not ledger.would_expose(frozenset(f"F{i}" for i in range(20)))


def test_differencing_two_overlapping_queries_reveals_nothing(run):
    """The attack: ask for all years, then ask for all-but-one, and subtract.

    With one shared ledger the second query must not hand back any cell whose difference
    from an already-released cell would isolate fewer than `min_cell_size` people.
    """
    years = sorted({str(ac.dimensions["year"]) for ac in run.action_costs})
    assert len(years) > 1, "need at least two years of data for this test"
    ledger = DisclosureLedger(min_cell_size=5)

    wide = build_matrix(run, filters={"year": years}, min_cell_size=5, ledger=ledger)
    narrow = build_matrix(run, filters={"year": years[:-1]}, min_cell_size=5, ledger=ledger)

    released_wide = {(c.row, c.col): c.net for c in wide.cells.values() if not c.suppressed}
    released_narrow = {(c.row, c.col): c.net for c in narrow.cells.values() if not c.suppressed}

    # For every cell released in both, the implied residual must cover a group that is
    # large enough to release on its own.
    residual = build_matrix(run, filters={"year": [years[-1]]}, min_cell_size=5)
    for key in set(released_wide) & set(released_narrow):
        implied = released_wide[key] - released_narrow[key]
        if implied == Decimal("0"):
            continue
        cell = residual.cells.get(key)
        assert cell is not None and cell.employee_count >= 5, (
            f"differencing on {key} would isolate a group of "
            f"{cell.employee_count if cell else 0} employees"
        )


def test_a_fresh_ledger_is_independent(run):
    """Documented behaviour: the guard is per session. A new ledger starts clean."""
    first = DisclosureLedger(min_cell_size=5)
    matrix_a = build_matrix(run, min_cell_size=5, ledger=first)
    matrix_b = build_matrix(run, min_cell_size=5, ledger=DisclosureLedger(min_cell_size=5))
    visible_a = {(c.row, c.col) for c in matrix_a.cells.values() if not c.suppressed}
    visible_b = {(c.row, c.col) for c in matrix_b.cells.values() if not c.suppressed}
    assert visible_a == visible_b


# ---------------------------------------------------------------------------
# totals are figures about people too
# ---------------------------------------------------------------------------
FILTER_DIMS = ("role_family", "work_location", "misconduct_category", "year")


def _one_action_selection(run) -> tuple[dict, object]:
    """Filters that select exactly one action, and that action."""
    seen: dict = {}
    for action in run.action_costs:
        seen.setdefault(tuple(str(action.dimensions[d]) for d in FILTER_DIMS), []).append(action)
    key, (only,) = next((k, v) for k, v in sorted(seen.items()) if len(v) == 1)
    return {d: [v] for d, v in zip(FILTER_DIMS, key, strict=True)}, only


def test_filtering_down_to_one_person_releases_nothing(run):
    """Every cell of a one-person selection is withheld. Its grand total, and the bucket of
    what was withheld, would be that person's cost if they were not withheld as well."""
    filters, only = _one_action_selection(run)
    matrix = build_matrix(run, rows="role_family", cols="year", filters=filters)
    assert matrix.selection_withheld
    body = matrix.as_dict()
    assert body["totals"] == {
        "withheld": True,
        "gross": None,
        "offset": None,
        "net": None,
        "actions": None,
        "incomplete_actions": None,
    }
    assert body["suppression"]["other_suppressed_net"] is None
    assert body["suppression"]["other_suppressed_gross"] is None
    assert all(c["suppressed"] for c in body["cells"])
    assert all(t["suppressed"] for t in body["row_totals"] + body["col_totals"])
    assert str(only.net) not in str(body) and str(only.gross) not in str(body)


def test_a_selection_is_released_from_the_minimum_group_size_up(run):
    """Whatever is filtered, a grand total is given only for at least five people."""
    by_place: dict = {}
    for action in run.action_costs:
        by_place.setdefault(str(action.dimensions["work_location"]), set()).add(action.employee_id)
    sizes = set()
    for place, people in by_place.items():
        matrix = build_matrix(run, filters={"work_location": [place]}, min_cell_size=5)
        assert matrix.selection_withheld == (len(people) < 5), place
        assert (matrix.as_dict()["totals"]["net"] is None) == (len(people) < 5)
        sizes.add(len(people) < 5)
    assert sizes == {True, False}, "the sample has locations on both sides of the line"
    # The unfiltered matrix is untouched.
    whole = build_matrix(run)
    assert not whole.selection_withheld
    assert whole.as_dict()["totals"]["net"] == str(run.net)


def test_no_line_total_is_about_fewer_people_than_the_minimum(run):
    """Two withheld cells in a row can be one person in two years. The row's total is then
    that person's cost, so a total is held to the same minimum as a cell."""
    small_lines = 0
    for rows, cols in (
        ("role_family", "year"),
        ("role_family", "misconduct_category"),
        ("work_location", "misconduct_category"),
        ("department", "action_type"),
    ):
        matrix = build_matrix(run, rows=rows, cols=cols, min_cell_size=5)
        for axis, dimension in (("row", rows), ("col", cols)):
            people: dict = {}
            for action in run.action_costs:
                people.setdefault(str(action.dimensions[dimension]), set()).add(action.employee_id)
            for total in matrix.line_totals(axis):
                if len(people[total["key"]]) < 5:
                    small_lines += 1
                    assert total["suppressed"] and "net" not in total, (rows, cols, total)
    assert small_lines > 0, "the sample has rows about fewer than five people"


def test_all_but_a_few_people_is_withheld_like_the_few(run):
    """The unfiltered total is on the summary page for every role. A filtered total that
    leaves out three people gives those three away by subtraction, so it is withheld."""
    everyone = {a.employee_id for a in run.action_costs}
    by_value: dict = {}
    for action in run.action_costs:
        by_value.setdefault(str(action.dimensions["role_family"]), set()).add(action.employee_id)
    # A job held by one to four of the disciplined people: leave it out of the filter.
    left_out = next(job for job, people in sorted(by_value.items()) if 0 < len(people) < 5)
    filters = {"role_family": sorted(set(by_value) - {left_out})}
    assert 0 < len(everyone) - len(set().union(*(by_value[j] for j in filters["role_family"]))) < 5

    matrix = build_matrix(run, filters=filters, ledger=DisclosureLedger(min_cell_size=5))
    assert matrix.selection_withheld
    assert {c.suppression_reason for c in matrix.cells.values()} <= {
        SuppressionReason.THRESHOLD,
        SuppressionReason.COMPLEMENTARY,
        SuppressionReason.DIFFERENCING,
    }
    assert matrix.as_dict()["totals"]["net"] is None
    # A filter that leaves out a large group is an ordinary question.
    big = max(by_value, key=lambda job: len(by_value[job]))
    ordinary = build_matrix(
        run,
        filters={"role_family": sorted(set(by_value) - {big})},
        ledger=DisclosureLedger(min_cell_size=5),
    )
    assert not ordinary.selection_withheld


def test_a_group_already_released_may_be_shown_again():
    ledger = DisclosureLedger(min_cell_size=5)
    group = frozenset("abcdefgh")
    ledger.record(group)
    ledger.record(group - {"a"})  # recorded by another route, one person apart
    assert ledger.would_expose(group) is False
    assert ledger.would_expose(group - {"b"}) is True
