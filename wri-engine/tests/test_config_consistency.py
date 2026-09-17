"""The config files and the code must not drift apart.

Enums live in code because the engine branches on them; taxonomies live in config because
customers extend them. These tests hold the seam together.
"""

from __future__ import annotations

from decimal import Decimal

from wri_engine.costing.engine import expected_assumption_ids
from wri_engine.schema import ActionType, AppealForum, AppealOutcome, SeparationReason


def test_action_types_match_the_taxonomy(org):
    assert {str(a) for a in ActionType} == set(org.action_types)


def test_suspension_day_ranges_match_the_taxonomy(org):
    for action_type in ActionType:
        low, high = action_type.expected_suspension_days
        assert [low, high] == org.action_types[str(action_type)]["suspension_days_range"]


def test_forums_outcomes_and_separation_reasons_match(org):
    assert {str(f) for f in AppealForum} == set(org.appeal_forums)
    assert {str(o) for o in AppealOutcome} == set(org.appeal_outcomes)
    assert {str(r) for r in SeparationReason} == set(org.separation_reasons)


def test_every_assumption_the_engine_needs_exists(assumptions, org):
    assumptions.require_all(expected_assumption_ids(org))


def test_every_role_family_has_a_configured_turnover_profile(org, assumptions):
    for role in org.role_families.values():
        assumptions.get(f"c5_washout_rate_{role.turnover_profile}")
        assumptions.get(f"c5_academy_weeks_{role.turnover_profile}")


def test_every_role_family_has_a_benefits_multiplier(org, assumptions):
    for role in org.role_families.values():
        assert role.benefits_group in {"civilian", "sworn_public_safety"}
        assumptions.get(
            f"benefits_multiplier_{'sworn' if role.benefits_group == 'sworn_public_safety' else 'civilian'}"
        )


def test_every_role_family_has_a_schedule(org):
    for role in org.role_families.values():
        assert role.schedule_id in org.schedules


def test_every_role_family_has_a_pay_grade_in_the_plan(org):
    for role in org.role_families.values():
        assert org.salary_for(role.pay_grade, 1) > 0


def test_washout_rates_are_below_one(assumptions):
    for assumption in assumptions.assumptions.values():
        if assumption.id.startswith("c5_washout_rate_"):
            assert assumption.high < 1, (
                f"{assumption.id}: a washout rate of 1 means nobody ever completes training"
            )


def test_every_assumption_has_a_source_and_an_owner(assumptions):
    for assumption in assumptions.assumptions.values():
        assert assumption.source.strip()
        assert assumption.owner.strip()
        assert assumption.status in ("confirmed", "researched", "TBD-MIKE")


def test_researched_assumptions_cite_something(assumptions):
    """A sourced figure must carry its citation, not just a claim of one."""
    for assumption in assumptions.assumptions.values():
        if assumption.status == "researched":
            assert len(assumption.source) > 60, (
                f"{assumption.id} is marked researched but its source is too thin to check"
            )


def test_benefits_multipliers_are_plausible(assumptions):
    for name in ("benefits_multiplier_civilian", "benefits_multiplier_sworn"):
        value = assumptions.get(name).value
        assert Decimal("1.2") < value < Decimal("2.5"), f"{name} = {value} is not credible"


def test_overtime_premium_is_the_statutory_minimum(assumptions):
    assert assumptions.get("c3_ot_premium_multiplier").value >= Decimal("1.5")


def test_headcount_is_around_the_configured_target(org):
    total = sum(role.headcount for role in org.role_families.values())
    target = org.raw["agency"]["headcount_target"]
    assert abs(total - target) / target < 0.05


def test_no_real_agency_names_appear_in_config(org):
    """Guardrail for the 'fictional agency only' rule."""
    text = str(org.raw).lower()
    for banned in ("fraternal order of police", "teamsters", "afscme", "seiu", "county of "):
        assert banned not in text, f"config mentions {banned!r}"
    assert org.raw["agency"]["fictional"] is True
