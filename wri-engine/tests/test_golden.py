"""Golden cases: hand-calculated actions, asserted to the cent.

Each case in `tests/golden/` carries the arithmetic a person worked out, in a comment. If one
of these fails, read the case's HAND CALCULATION block before changing any code -- the block
is the specification, and the code is the thing under test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from golden_case import load_cases

CASES = load_cases()
IDS = [c.path.stem for c in CASES]


def test_every_component_is_covered():
    """The set as a whole must exercise all five components and the offset."""
    seen = {i["component"] for case in CASES for i in case.raw["expected"]["line_items"]}
    assert seen == {"C1", "C2", "C3", "C3-offset", "C4", "C5"}


def test_required_scenarios_present():
    names = {c.path.stem for c in CASES}
    for required in (
        "01_unpaid_suspension_deputy",
        "02_corrections_removal_arbitration",
        "03_civilian_removal_refilled",
        "04_sworn_removal_academy_washout",
        "05_pending_appeal_incomplete",
        "06_position_abolished_no_turnover",
    ):
        assert required in names, f"golden case {required} is missing"


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_line_items_match_hand_calculation(case, org, assumptions):
    result = case.compute(org, assumptions)
    assert result is not None, f"{case.name}: the action under test produced no cost record"
    actual = sorted((str(i.component), i.subcomponent, i.amount) for i in result.line_items)
    assert actual == case.expected_items


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_totals_match(case, org, assumptions):
    result = case.compute(org, assumptions)
    assert result.gross == case.expected_total("gross")
    assert result.offset == case.expected_total("offset")
    assert result.net == case.expected_total("net")
    assert result.net == result.gross + result.offset
    assert result.cost_incomplete is bool(case.raw["expected"]["cost_incomplete"])


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_every_line_item_is_explainable(case, org, assumptions):
    """The whole point of the model: no amount without a formula behind it."""
    result = case.compute(org, assumptions)
    for item in result.line_items:
        assert item.formula.strip(), f"{item.subcomponent} has no formula"
        assert item.inputs, f"{item.subcomponent} records no inputs"
        for assumption_id in item.assumption_ids:
            assert assumption_id in assumptions, (
                f"{item.subcomponent} cites assumption {assumption_id!r}, which is not in "
                f"the registry"
            )


def test_abolished_position_has_no_turnover_cost(org, assumptions):
    case = next(c for c in CASES if c.path.stem == "06_position_abolished_no_turnover")
    result = case.compute(org, assumptions)
    assert not [i for i in result.line_items if str(i.component) == "C5"]


def test_pending_appeal_awards_nothing(org, assumptions):
    case = next(c for c in CASES if c.path.stem == "05_pending_appeal_incomplete")
    result = case.compute(org, assumptions)
    appeal_subs = {i.subcomponent for i in result.line_items if str(i.component) == "C4"}
    assert "back_pay" not in appeal_subs
    assert "settlement" not in appeal_subs
    assert result.cost_incomplete


def test_washout_multiplier_is_visible_in_the_drill_down(org, assumptions):
    case = next(c for c in CASES if c.path.stem == "04_sworn_removal_academy_washout")
    result = case.compute(org, assumptions)
    academy = next(i for i in result.line_items if i.subcomponent == "academy_salary")
    assert Decimal(academy.inputs["washout_multiplier"]) > 1
    assert "washout" in academy.formula
