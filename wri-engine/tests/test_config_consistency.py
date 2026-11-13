"""The config files and the code must not drift apart.

Enums live in code because the engine branches on them; taxonomies live in config because
customers extend them. These tests hold the seam together.
"""

from __future__ import annotations

from decimal import Decimal

from wri_engine.costing.engine import expected_assumption_ids
from wri_engine.schema import ActionType, AppealForum, AppealOutcome, SeparationReason
from wri_engine.sectors import get_pack, sector_ids


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


def test_every_sector_offers_every_profile_its_intake_can_fall_back_to(assumptions):
    """A sector's `standard_turnover_profile` must be one it actually runs, and its
    coefficients must exist -- otherwise the intake fallback would ask for an id that is
    not there."""
    for sector in sector_ids():
        pack = get_pack(sector)
        assert pack.standard_turnover_profile in pack.turnover_profiles
        assumptions.get(f"c5_washout_rate_{pack.standard_turnover_profile}")


def test_every_role_family_has_a_configured_turnover_profile(org, assumptions):
    for role in org.role_families.values():
        assumptions.get(f"c5_washout_rate_{role.turnover_profile}")
        assumptions.get(f"c5_academy_weeks_{role.turnover_profile}")


def test_the_vacancy_salary_burden_assumption_exists(assumptions):
    """C5 references it for every minimum-staffing vacancy, so a missing id would take the
    whole engine down rather than degrade quietly."""
    assumption = assumptions.get("c5_vacancy_salary_burden_multiplier")
    assert assumption.unit == "ratio"
    assert assumption.value >= 1, "a burden multiplier below 1 would credit less than the wage"
    assert assumption.status == "confirmed"
    assert "OPEN ITEM 3" in assumption.notes


#: Values the owner has not reviewed yet. This is the review checklist: a new value goes in
#: here when it is added and comes out when the owner approves it, so the two tests below
#: hold the registry and this list to each other.
#:
#: Empty, because the owner approved the public-sector registry on 2026-11-12 and the twelve
#: private-sector values on 2026-11-13. Add to it, do not delete it -- an empty checklist is
#: what makes the next unreviewed value fail the build.
AWAITING_OWNER_REVIEW: set[str] = set()


def test_nothing_the_owner_approved_has_gone_back_to_pending(assumptions):
    """The owner signed off the public-sector registry on 2026-11-12 and the private-sector
    values on 2026-11-13.

    New work may add values he has not seen -- those belong in AWAITING_OWNER_REVIEW above.
    What must not happen is an already-approved value quietly reverting to a placeholder, or
    a new one being added without landing on the review list.
    """
    pending = {a.id for a in assumptions.placeholders}
    unexpected = sorted(pending - AWAITING_OWNER_REVIEW)
    assert not unexpected, (
        f"{len(unexpected)} assumptions are pending that are not on the review checklist: "
        f"{unexpected}. Either the owner's sign-off was undone, or a new value was added "
        f"without being listed for him to review."
    )


def test_the_review_checklist_is_not_stale(assumptions):
    """Once the owner approves one, it has to come off the list."""
    pending = {a.id for a in assumptions.placeholders}
    approved = sorted(AWAITING_OWNER_REVIEW - pending)
    assert not approved, (
        f"{approved} are no longer pending, so remove them from AWAITING_OWNER_REVIEW."
    )


def test_every_pending_value_says_why_it_is_not_researched(assumptions):
    """A pending value must explain itself, so the owner can review it without digging.

    The owner asked for every new value to carry a citation. Where no authoritative figure
    exists, the honest answer is to say so rather than dress a vendor page up as research --
    so each pending entry has to state that it is not researched and what would settle it.
    """
    for assumption in assumptions.placeholders:
        assert assumption.source.strip(), assumption.id
        assert _says_it_is_not_researched(assumption), (
            f"{assumption.id} is pending but does not say why it is not researched"
        )


def _says_it_is_not_researched(assumption) -> bool:
    notes = assumption.notes.lower()
    return (
        "not a researched figure" in notes
        or "not marked researched" in notes
        or "Owner professional estimate" in assumption.source
    )


#: The twelve coefficients the private-sector cost model added.
PRIVATE_SECTOR_VALUES = {
    "private_benefits_multiplier_all",
    "c4_filing_window_days_agency_charge",
    "c1_outside_investigator_hourly_rate",
    "c3_temp_agency_markup",
    "c4_filing_window_days_demand_letter",
    "c4_filing_window_days_mediation",
    "c4_hr_hours_agency_charge",
    "c4_hr_hours_demand_letter",
    "c4_hr_hours_mediation",
    "c6_separation_agreement_counsel_hours",
    "c6_separation_agreement_hr_hours",
    "c6_unemployment_experience_rating_multiplier",
}


def test_approving_the_private_values_did_not_turn_them_into_measurements(assumptions):
    """The owner approved all twelve on 2026-11-13. That settles who stands behind them and
    nothing else.

    So the ten that are not researched must still say so and still name what would settle
    them, and the two that are researched must keep that status -- `researched` names a cited
    source and is the stronger claim, which a sign-off must not flatten away.
    """
    for assumption_id in sorted(PRIVATE_SECTOR_VALUES):
        assumption = assumptions.get(assumption_id)
        assert assumption.status in ("confirmed", "researched"), assumption_id
        assert str(assumption.last_reviewed) >= "2026-11-12", assumption_id
        if assumption.status == "researched":
            continue
        assert _says_it_is_not_researched(assumption), (
            f"{assumption_id} is confirmed rather than researched, so it has to say that it "
            f"is not a researched figure -- approval is not a citation"
        )

    researched = {a for a in PRIVATE_SECTOR_VALUES if assumptions.get(a).status == "researched"}
    assert researched == {
        "private_benefits_multiplier_all",
        "c4_filing_window_days_agency_charge",
    }, "the two values with a statutory or government source must stay `researched`"


def test_sign_off_did_not_inflate_confidence(assumptions):
    """Approving a number is not the same as verifying it.

    Most values are still the owner's professional estimates. `status` records who signed
    off; `confidence` records how much weight the figure bears, and sign-off must not quietly
    raise it. If this ever fails, someone has conflated the two and the registry has stopped
    telling the truth about where its numbers come from.
    """
    low = [a for a in assumptions.assumptions.values() if a.confidence == "low"]
    assert len(low) >= 80, (
        f"only {len(low)} low-confidence assumptions remain; confidence should not have "
        f"moved when the owner signed off on the values"
    )
    owner_estimates = [
        a for a in assumptions.assumptions.values() if "Owner professional estimate" in a.source
    ]
    assert len(owner_estimates) >= 100
    for assumption in owner_estimates:
        assert assumption.status != "researched", (
            f"{assumption.id} is an owner estimate but claims 'researched', which asserts a "
            f"cited source it does not have"
        )


def test_researched_assumptions_were_not_downgraded(assumptions):
    """`researched` is a stronger claim than `confirmed` -- it names a source. A blanket
    sign-off must not flatten it away."""
    researched = {a.id for a in assumptions.assumptions.values() if a.status == "researched"}
    assert "benefits_multiplier_civilian" in researched
    assert len(researched) >= 13


def test_every_assumption_names_the_owner_who_signed_it_off(assumptions):
    for assumption in assumptions.assumptions.values():
        assert assumption.owner == "Michael V. Celli", assumption.id


def test_the_two_wage_burden_assumptions_are_separate_ids(assumptions):
    """They start equal on purpose, but must stay independently settable: a vacancy and a
    suspension are different events and the employer may stop different things."""
    suspension = assumptions.get("c3_unpaid_suspension_burden_multiplier")
    vacancy = assumptions.get("c5_vacancy_salary_burden_multiplier")
    assert suspension.id != vacancy.id
    assert suspension.value == vacancy.value


def test_the_civilian_loss_factor_records_its_net_interpretation(assumptions):
    """Civilian vacancies get no salary offset, which is only correct if the factor is a NET
    figure. That reading has to be written down where the number lives."""
    for profile in ("civilian_skilled", "civilian_standard"):
        notes = assumptions.get(f"c5_vacancy_productivity_loss_factor_{profile}").notes
        assert "NET figure" in notes
        assert "CONFIRM THE INTERPRETATION" in notes


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
    """Guardrail for the 'fictional agency only' rule (CLAUDE.md rule 4).

    A banned list is a floor, not a proof: it catches the names most likely to be pasted in
    by accident, and the `fictional` flag has to stay true whatever else changes.
    """
    text = str(org.raw).lower()
    for banned in ("fraternal order of police", "teamsters", "afscme", "seiu", "county of "):
        assert banned not in text, f"config mentions {banned!r}"
    assert org.raw["agency"]["fictional"] is True


def test_the_agency_name_is_settled(org):
    """Harlow County was confirmed by the owner on 2026-11-12. If the name is ever reopened,
    the docs that quote it have to be reopened with it."""
    agency = org.raw["agency"]
    assert agency["name"] == "Harlow County"
    assert agency["name_status"] == "confirmed"
    assert agency["fictional"] is True
