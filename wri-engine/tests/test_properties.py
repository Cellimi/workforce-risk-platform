"""Property tests: invariants that must hold for every action, in every mode.

These are the rules the model cannot break without being wrong, whatever the data looks like.
"""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from wri_engine.aggregation.rollups import build_matrix
from wri_engine.costing.engine import run_costing
from wri_engine.schema import OFFSET_COMPONENTS

MODES = ("low", "base", "high")


def test_only_offset_components_may_be_negative(run):
    """Every member of OFFSET_COMPONENTS is <= 0; every other component is >= 0."""
    for item in run.line_items:
        if item.component in OFFSET_COMPONENTS:
            assert item.amount <= 0, f"{item.subcomponent} offset must not be positive"
        else:
            assert item.amount >= 0, f"{item.subcomponent} must not be negative"


def test_offset_components_are_the_only_ones_excluded_from_gross(run):
    """Guards the definition itself: gross + offset must partition the line items."""
    from decimal import Decimal as D

    everything = sum((i.amount for i in run.line_items), D("0"))
    assert run.gross + run.offset == everything


def test_both_offsets_actually_occur_in_the_dataset(run):
    """If one stopped being emitted, the tests above would pass vacuously."""
    seen = {i.component for i in run.line_items if i.component in OFFSET_COMPONENTS}
    assert seen == set(OFFSET_COMPONENTS), f"only saw {seen}"


def test_vacancy_charge_and_credit_cover_the_same_shifts(run):
    """Per action, every vacancy overtime line must be mirrored by an offset over an
    identical shift count. A mismatch means the county is credited for a different period
    than it was charged for."""
    for action in run.action_costs:
        charges = [i for i in action.line_items if i.subcomponent == "vacancy_coverage_overtime"]
        credits = [i for i in action.line_items if i.subcomponent == "vacancy_salary_saved"]
        assert len(charges) == len(credits), action.action_id
        for charge, credit in zip(charges, credits, strict=True):
            assert charge.inputs["scheduled_shifts"] == credit.inputs["scheduled_shifts"], (
                f"{action.action_id}: charged {charge.inputs['scheduled_shifts']} shifts "
                f"but credited {credit.inputs['scheduled_shifts']}"
            )
            assert charge.inputs["vacancy_days"] == credit.inputs["vacancy_days"]


def test_offset_cannot_exceed_the_overtime_at_base_assumptions(run):
    """A structural bound, not a coincidence.

    offset / overtime = own_base / (avg_base x 1.5), because both sides carry the same
    1.0765 burden at base values. The FLSA premium of 1.5 therefore caps the ratio, and
    within one pay grade the widest step spread is 1.025^9 = 1.2489, so the ceiling is
    1.2489 / 1.5 = 0.833. Golden case 10 sits exactly at it.
    """
    for action in run.action_costs:
        charges = {
            i.action_id: i
            for i in action.line_items
            if i.subcomponent == "vacancy_coverage_overtime"
        }
        for item in action.line_items:
            if item.subcomponent != "vacancy_salary_saved":
                continue
            charge = charges[item.action_id]
            assert abs(item.amount) < charge.amount, (
                f"{action.action_id}: the vacancy credit exceeded the charge at base "
                f"assumptions, which the 1.5x FLSA premium should make impossible"
            )


def test_offset_can_exceed_the_overtime_once_the_burdens_diverge(loaded, org, assumptions):
    """The other half of the bound, and the reason the vacancy burden has its own id.

    Push the vacancy burden to its high bound (pension stops with the pay) while the
    overtime burden stays at base, and the credit can overtake the charge. The engine must
    handle that without complaint -- a negative net vacancy is a real outcome, not an error.
    """
    from datetime import date

    data, report = loaded
    result = run_costing(
        data,
        as_of=date(2026, 9, 17),
        org=org,
        assumptions=assumptions,
        overrides={"c5_vacancy_salary_burden_multiplier": 2.5},
        excluded_action_ids=report.blocked_action_ids,
    )
    pairs = [
        (
            next(i for i in ac.line_items if i.subcomponent == "vacancy_coverage_overtime"),
            next(i for i in ac.line_items if i.subcomponent == "vacancy_salary_saved"),
        )
        for ac in result.action_costs
        if any(i.subcomponent == "vacancy_salary_saved" for i in ac.line_items)
    ]
    assert pairs, "no minimum-staffing vacancies in the sample to test"
    assert any(abs(credit.amount) > charge.amount for charge, credit in pairs)
    assert result.net == result.gross + result.offset


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
            data,
            as_of=date(2026, 9, 17),
            org=org,
            assumptions=assumptions,
            mode=mode,
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
        st.decimals(min_value=0, max_value=200, allow_nan=False, allow_infinity=False, places=2),
        min_size=1,
        max_size=4,
    )
)
def test_scenario_overrides_never_break_the_invariants(overrides, loaded, org, assumptions):
    from datetime import date

    data, report = loaded
    try:
        result = run_costing(
            data,
            as_of=date(2026, 9, 17),
            org=org,
            assumptions=assumptions,
            overrides={k: v for k, v in overrides.items()},
            excluded_action_ids=report.blocked_action_ids,
        )
    except ValueError as exc:
        # The only value the engine refuses outright is a washout rate of 1 or more, which
        # would mean no recruit ever reaches solo duty.
        assert overrides.get("c5_washout_rate_sworn_deputy", Decimal("0")) >= 1, exc
        return
    assert result.net == result.gross + result.offset
    assert all(i.amount >= 0 for i in result.line_items if i.component not in OFFSET_COMPONENTS)
