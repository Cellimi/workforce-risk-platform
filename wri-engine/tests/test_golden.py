"""Golden cases: hand-calculated actions, asserted to the cent.

Each case in `tests/golden/` carries the arithmetic a person worked out, in a comment. If one
of these fails, read the case's HAND CALCULATION block before changing any code -- the block
is the specification, and the code is the thing under test.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from golden_case import load_cases
from wri_engine.schema import OFFSET_COMPONENTS, CostComponent

CASES = load_cases()
IDS = [c.path.stem for c in CASES]


def test_every_component_is_covered():
    """Every component the engine can emit must appear in at least one golden case.

    Derived from the enum, not a hardcoded list, so adding a component forces a golden case
    rather than silently shipping one nothing has ever hand-checked.
    """
    seen = {i["component"] for case in CASES for i in case.raw["expected"]["line_items"]}
    assert seen == {str(c) for c in CostComponent}


def test_required_scenarios_present():
    names = {c.path.stem for c in CASES}
    for required in (
        "01_unpaid_suspension_deputy",
        "02_corrections_removal_arbitration",
        "03_civilian_removal_refilled",
        "04_sworn_removal_academy_washout",
        "05_pending_appeal_incomplete",
        "06_position_abolished_no_turnover",
        "10_vacancy_offset_senior_deputy",
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


def test_vacancy_offset_mirrors_the_overtime_it_credits(org, assumptions):
    """The charge and the credit must cover the same shifts, or the net is meaningless."""
    for case in CASES:
        result = case.compute(org, assumptions)
        overtime = [i for i in result.line_items if i.subcomponent == "vacancy_coverage_overtime"]
        offsets = [i for i in result.line_items if i.subcomponent == "vacancy_salary_saved"]
        assert len(overtime) == len(offsets), (
            f"{case.name}: {len(overtime)} vacancy overtime line(s) but "
            f"{len(offsets)} offset line(s) -- they must come in pairs"
        )
        for charge, credit in zip(overtime, offsets, strict=True):
            assert charge.inputs["scheduled_shifts"] == credit.inputs["scheduled_shifts"]
            assert credit.component in OFFSET_COMPONENTS
            assert credit.amount < 0


def test_civilian_vacancies_get_no_salary_offset(org, assumptions):
    """`c5_vacancy_productivity_loss_factor` is defined as a NET figure, so crediting the
    salary again would double-count it. Documented in cost_methodology.md section 7."""
    for stem in ("03_civilian_removal_refilled", "08_removal_refill_pending"):
        case = next(c for c in CASES if c.path.stem == stem)
        result = case.compute(org, assumptions)
        subs = {i.subcomponent for i in result.line_items}
        assert "vacancy_productivity_loss" in subs
        assert "vacancy_salary_saved" not in subs
        assert result.offset == 0


def test_abolished_position_has_no_offset_either(org, assumptions):
    case = next(c for c in CASES if c.path.stem == "06_position_abolished_no_turnover")
    result = case.compute(org, assumptions)
    assert not [i for i in result.line_items if i.component in OFFSET_COMPONENTS]


def test_washout_multiplier_is_visible_in_the_drill_down(org, assumptions):
    case = next(c for c in CASES if c.path.stem == "04_sworn_removal_academy_washout")
    result = case.compute(org, assumptions)
    academy = next(i for i in result.line_items if i.subcomponent == "academy_salary")
    assert Decimal(academy.inputs["washout_multiplier"]) > 1
    assert "washout" in academy.formula
