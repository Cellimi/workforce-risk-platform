"""Property tests: invariants that must hold for every action, in every mode.

These are the rules the model cannot break without being wrong, whatever the data looks like.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from wri_engine.aggregation.rollups import build_matrix
from wri_engine.costing.engine import run_costing
from wri_engine.schema import CostComponent

MODES = ("low", "base", "high")


def test_only_the_offset_may_be_negative(run):
    for item in run.line_items:
        if item.component == CostComponent.C3_OFFSET:
            assert item.amount <= 0, f"{item.subcomponent} offset must not be positive"
        else:
            assert item.amount >= 0, f"{item.subcomponent} must not be negative"


def test_net_equals_gross_plus_offset(run):
    for action in run.action_costs:
        assert action.net == action.gross + action.offset
    assert run.net == run.gross + run.offset


def test_action_totals_sum_to_their_line_items(run):
    for action in run.action_costs:
        assert action.gross + action.offset == sum(
            (i.amount for i in action.line_items), Decimal("0")
        )


def test_rollup_totals_equal_the_sum_of_line_items(run):
    matrix = build_matrix(run)
    assert matrix.total_net == run.net
    assert matrix.total_gross == run.gross
    assert matrix.visible_net + matrix.suppressed_net == matrix.total_net


def test_matrix_reconciles_on_every_dimension_pair(run):
    for rows in ("role_family", "department", "year"):
        for cols in ("misconduct_category", "action_type", "bargaining_unit"):
            matrix = build_matrix(run, rows=rows, cols=cols)
            assert matrix.reconciles(), f"{rows} x {cols} does not reconcile"
            assert matrix.total_net == run.net


def test_low_base_high_are_ordered(loaded, org, assumptions):
    """Sensitivity has to behave like sensitivity: low <= base <= high, everywhere."""
    from datetime import date

    data, report = loaded
    totals = {}
    for mode in MODES:
        result = run_costing(
            data, as_of=date(2026, 9, 17), org=org, assumptions=assumptions, mode=mode,
            excluded_action_ids=report.blocked_action_ids,
        )
        totals[mode] = result.gross
    assert totals["low"] <= totals["base"] <= totals["high"]


def test_every_assumption_bound_is_ordered(assumptions):
    for assumption in assumptions.assumptions.values():
        assert assumption.low <= assumption.value <= assumption.high, assumption.id


def test_excluded_actions_contribute_nothing(run, loaded):
    _, report = loaded
    costed = {ac.action_id for ac in run.action_costs}
    assert not (costed & report.blocked_action_ids)


def test_incomplete_flag_propagates_from_line_items(run):
    for action in run.action_costs:
        expected = any(i.cost_incomplete for i in action.line_items)
        assert action.cost_incomplete is expected


@settings(max_examples=25, deadline=None)
@given(
    overrides=st.dictionaries(
        st.sampled_from(
            [
                "c5_washout_rate_sworn_deputy",
                "c5_academy_tuition_sworn_deputy",
                "c1_hours_removal_supervisor",
                "c4_outside_counsel_hourly_rate",
            ]
        ),
        st.decimals(min_value=0, max_value=200, allow_nan=False, allow_infinity=False,
                    places=2),
        min_size=1,
        max_size=4,
    )
)
def test_scenario_overrides_never_break_the_invariants(overrides, loaded, org, assumptions):
    from datetime import date

    data, report = loaded
    try:
        result = run_costing(
            data, as_of=date(2026, 9, 17), org=org, assumptions=assumptions,
            overrides={k: v for k, v in overrides.items()},
            excluded_action_ids=report.blocked_action_ids,
        )
    except ValueError as exc:
        # The only value the engine refuses outright is a washout rate of 1 or more, which
        # would mean no recruit ever reaches solo duty.
        assert overrides.get("c5_washout_rate_sworn_deputy", Decimal("0")) >= 1, exc
        return
    assert result.net == result.gross + result.offset
    assert all(
        i.amount >= 0
        for i in result.line_items
        if i.component != CostComponent.C3_OFFSET
    )
