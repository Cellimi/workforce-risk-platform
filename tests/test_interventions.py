"""Phase 3: what an intervention would cost, against what the discipline costs.

What is held here: that the arithmetic can be checked by hand; that what the discipline
costs is the cost baseline's own figure, to the cent; that the product supplies no figure
for how well an intervention works and refuses to proceed without the basis for one; that a
projection changes nothing in the baseline; and that only roles which see records may ask.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from wri_engine.aggregation.rollups import window_years
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.interventions import (
    Intervention,
    InvalidIntervention,
    UnitBaseline,
    appraise,
    unit_baseline,
)
from wri_engine.paths import PROJECT_ROOT, SYNTHETIC_DIR

IDS = r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b"


def baseline(net: str = "30000", actions: int = 30, years: str = "3") -> UnitBaseline:
    return UnitBaseline(
        row="caseworker",
        col="North Office",
        row_label="Caseworker",
        col_label="North Office",
        topic="tardiness",
        topic_label="Tardiness",
        actions=actions,
        people=25,
        net=Decimal(net),
        window_years=Decimal(years),
    )


def proposal(**changes) -> Intervention:
    values = {
        "name": "Flexible start times",
        "one_time_cost": Decimal("3000"),
        "annual_cost_per_person": Decimal("100"),
        "people_covered": 20,
        "other_annual_cost": Decimal("1000"),
        "years": 2,
        "reduction_low": Decimal("0.10"),
        "reduction_expected": Decimal("0.50"),
        "reduction_high": Decimal("1"),
        "basis": "What the site visit found.",
        **changes,
    }
    return Intervention(**values)


# ---------------------------------------------------------------------------
# the arithmetic, checked by hand
# ---------------------------------------------------------------------------
def test_an_appraisal_can_be_checked_by_hand():
    """The discipline: $30,000 over 3 years, so X is $10,000 a year, and 10 actions a year.
    The intervention: $3,000 once, and ($100 x 20 people + $1,000) = $3,000 a year for 2
    years, so Y is $9,000.

    Preventing half: $5,000 a year, $10,000 over the two years, less $9,000: $1,000 ahead.
    Preventing a tenth: $2,000 less $9,000: $7,000 behind. Preventing all of it: $20,000
    less $9,000: $11,000 ahead. It pays for itself at $9,000 / $20,000 = 45%."""
    result = appraise(baseline(), proposal())
    assert result.baseline.annual_net == Decimal("10000")
    assert result.baseline.annual_actions == Decimal("10")
    assert result.intervention.annual_cost == Decimal("3000")
    assert result.intervention.total_cost == Decimal("9000")
    low, expected, high = result.scenarios
    assert (low.label, expected.label, high.label) == ("Low", "Expected", "High")
    assert [s.prevented_per_year for s in result.scenarios] == [1000, 5000, 10000]
    assert [s.prevented_total for s in result.scenarios] == [2000, 10000, 20000]
    assert [s.net_saving for s in result.scenarios] == [-7000, 1000, 11000]
    assert [s.actions_prevented_per_year for s in result.scenarios] == [1, 5, 10]
    assert result.expected is expected
    assert result.break_even == Decimal("0.45")
    assert result.break_even_note == (
        "It pays for itself if it prevents 45% of these actions: $9,000.00 / "
        "($10,000.00 a year x 2 years)."
    )
    # Every figure says how it was made.
    assert result.baseline.formula == (
        "$30,000.00 net cost of 30 actions in the cost baseline / 3 years"
    )
    assert result.intervention.cost_formula == (
        "$3,000.00 one-time + ($100.00 x 20 people + $1,000.00 other) a year x 2 years"
    )
    assert expected.formula == (
        "$10,000.00 a year x 50% prevented x 2 years - $9,000.00 cost of the intervention"
    )
    body = result.as_dict()
    assert body["scenarios"][1]["net_saving"] == "1000.00"
    assert [s["pays_for_itself"] for s in body["scenarios"]] == [False, True, True]
    assert body["break_even"] == "0.4500"
    assert body["unit"]["name"] == "Tardiness among Caseworker at North Office"
    assert "A projection, and no part of the cost baseline." in body["assumes"]


def test_money_is_rounded_only_when_it_is_written_out():
    """$10,000 over 3 years is $3,333.33... a year. Three years of a third of it prevented
    is $3,333.33 and a third of a cent, not three times a rounded figure."""
    result = appraise(
        baseline(net="10000"),
        proposal(
            years=3,
            one_time_cost=Decimal(0),
            annual_cost_per_person=Decimal(0),
            other_annual_cost=Decimal(0),
            reduction_expected=Decimal("0.5"),
        ),
    )
    assert result.expected.prevented_total == Decimal("10000") / 3 * Decimal("0.5") * 3
    assert result.as_dict()["scenarios"][1]["prevented_total"] == "5000.00"
    assert result.as_dict()["unit"]["annual_net"] == "3333.33"
    assert result.break_even == 0  # it costs nothing, so anything pays for it


def test_an_intervention_that_cannot_pay_for_itself_is_said_to_be_one():
    result = appraise(baseline(), proposal(one_time_cost=Decimal("50000")))
    assert result.break_even == Decimal("56000") / Decimal("20000")
    assert all(s.net_saving < 0 for s in result.scenarios)
    assert result.break_even_note == (
        "It cannot pay for itself on the cost of discipline alone: it costs $56,000.00 over "
        "2 years and these actions cost $20,000.00 in that time."
    )


def test_where_discipline_has_no_net_cost_there_is_nothing_to_save():
    """Unpaid suspensions can save more in wages than the process cost. The arithmetic is
    still done, and no break-even share is claimed."""
    result = appraise(baseline(net="-6000"), proposal())
    assert result.break_even is None and result.as_dict()["break_even"] is None
    assert "no net cost for these actions" in result.break_even_note
    assert result.expected.net_saving == Decimal("-2000") * Decimal("0.5") * 2 - Decimal("9000")


# ---------------------------------------------------------------------------
# what may be entered
# ---------------------------------------------------------------------------
def test_the_product_supplies_no_figure_for_how_well_anything_works():
    """The three shares have no default, and their basis has to be given in words."""
    with pytest.raises(TypeError):
        Intervention(
            name="x",
            one_time_cost=Decimal(0),
            annual_cost_per_person=Decimal(0),
            people_covered=0,
            other_annual_cost=Decimal(0),
            years=1,
            basis="b",
        )
    with pytest.raises(InvalidIntervention) as refused:
        appraise(baseline(), proposal(basis="   "))
    assert refused.value.problems == [
        "say what the shares of actions prevented are based on: this product has no figure "
        "of its own for how well an intervention works"
    ]
    # And none is written into the code: no percentage or share appears there as a number.
    code = (PROJECT_ROOT / "src" / "wri_engine" / "interventions" / "appraisal.py").read_text()
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    code = "\n".join(line.split("#")[0] for line in code.splitlines())
    assert not re.search(
        r"\b0\.\d*[1-9]\d*\b",
        code.replace('Decimal("0.01")', "")
        .replace('Decimal("0.0001")', "")
        .replace('Decimal("0.1")', ""),
    )


def test_what_cannot_be_appraised_is_refused_with_every_reason():
    bad = proposal(
        name=" ",
        one_time_cost=Decimal("-1"),
        people_covered=-3,
        years=11,
        reduction_low=Decimal("0.6"),
        reduction_expected=Decimal("0.5"),
    )
    assert bad.problems() == [
        "give the intervention a name",
        "the one-time cost cannot be negative",
        "the number of people covered cannot be negative",
        "look ahead between 1 and 10 years",
        "the low share cannot be above the expected, nor the expected above the high",
    ]
    assert proposal(reduction_high=Decimal("1.2")).problems() == [
        "each share of actions prevented must be between 0% and 100%"
    ]
    assert proposal(years=0).problems() == ["look ahead between 1 and 10 years"]
    assert proposal().problems() == []
    with pytest.raises(InvalidIntervention, match="give the intervention a name; the one-time"):
        appraise(baseline(), bad)


# ---------------------------------------------------------------------------
# X is the cost baseline's figure
# ---------------------------------------------------------------------------
def test_what_the_discipline_costs_is_the_baselines_own_figure(run):
    """For every unit and type in the sample: the same actions, the same net cost, to the
    cent, as adding up the costing run directly."""
    years = window_years(run)
    by_unit: dict = {}
    for action in run.action_costs:
        d = action.dimensions
        key = (d["role_family"], d["work_location"], d["misconduct_category"])
        by_unit.setdefault(key, []).append(action)
        by_unit.setdefault((key[0], key[1], ""), []).append(action)
    assert len(by_unit) > 50
    for (job, place, topic), actions in by_unit.items():
        found = unit_baseline(run, job, place, topic, years)
        assert found.actions == len(actions)
        assert found.net == sum(a.net for a in actions)
        assert found.people == len({a.employee_id for a in actions})
        assert found.annual_net == found.net / years
        assert bool(found.topic_label) == bool(topic)
    assert unit_baseline(run, "caseworker", "Nowhere", "", years) is None
    assert not re.search(IDS, str(found.as_dict()))


# ---------------------------------------------------------------------------
# the API
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client():
    STATE.load(SYNTHETIC_DIR / "sample")
    return TestClient(app)


def role(name: str) -> dict:
    return {"X-WRI-Role": name}


def request_for(client, **changes) -> dict:
    """A proposal for the sample's most expensive unit and type."""
    run = STATE.run(mode="base")
    cost: dict = {}
    for action in run.action_costs:
        d = action.dimensions
        key = (d["role_family"], d["work_location"], d["misconduct_category"])
        cost[key] = cost.get(key, Decimal(0)) + action.net
    job, place, topic = max(cost, key=cost.get)
    return {
        "role_family": job,
        "work_location": place,
        "misconduct_category": topic,
        "name": "Supervisor training",
        "one_time_cost": "12000",
        "annual_cost_per_person": "150",
        "people_covered": 40,
        "other_annual_cost": "0",
        "years": 3,
        "reduction_low": "0.05",
        "reduction_expected": "0.2",
        "reduction_high": "0.35",
        "basis": "The consultant's judgement after the site visit.",
        **changes,
    }


def test_the_endpoint_sets_the_proposal_against_the_baseline(client):
    body = request_for(client)
    for who in ("hr_analyst", "admin"):
        response = client.post("/interventions/appraise", json=body, headers=role(who))
        assert response.status_code == 200, response.text
        result = response.json()
        run = STATE.run(mode="base")
        theirs = [
            a
            for a in run.action_costs
            if (
                a.dimensions["role_family"],
                a.dimensions["work_location"],
                a.dimensions["misconduct_category"],
            )
            == (body["role_family"], body["work_location"], body["misconduct_category"])
        ]
        net = sum(a.net for a in theirs)
        assert result["unit"]["net"] == str(net.quantize(Decimal("0.01")))
        assert result["unit"]["actions"] == len(theirs)
        yearly = net / window_years(run)
        assert result["unit"]["annual_net"] == str(yearly.quantize(Decimal("0.01")))
        assert result["intervention"]["total_cost"] == "30000.00"  # 12,000 + 150 x 40 x 3
        expected = result["scenarios"][1]
        assert expected["net_saving"] == str(
            (yearly * Decimal("0.2") * 3 - Decimal("30000")).quantize(Decimal("0.01"))
        )
        assert result["intervention"]["basis"] == body["basis"]
        assert "is not part of the cost baseline" in result["note"]
        assert not re.search(IDS, response.text)


def test_it_follows_the_sensitivity_mode(client):
    body = request_for(client)
    nets = {
        mode: Decimal(
            client.post(
                "/interventions/appraise", json={**body, "mode": mode}, headers=role("admin")
            ).json()["unit"]["net"]
        )
        for mode in ("low", "base", "high")
    }
    assert nets["low"] < nets["base"] < nets["high"]


def test_only_roles_that_see_records_may_ask(client):
    """The unit is any job at any location, and its cost is given whatever its size."""
    body = request_for(client)
    assert (
        client.post("/interventions/appraise", json=body, headers=role("executive")).status_code
        == 403
    )
    assert client.post("/interventions/appraise", json=body).status_code == 403


def test_what_cannot_be_appraised_is_refused_by_the_endpoint(client):
    admin = role("admin")
    bad = request_for(client, basis="", years=40)
    refused = client.post("/interventions/appraise", json=bad, headers=admin)
    assert refused.status_code == 422
    assert refused.json()["detail"]["problems"] == [
        "look ahead between 1 and 10 years",
        "say what the shares of actions prevented are based on: this product has no figure "
        "of its own for how well an intervention works",
    ]
    # No default for how well it works: leaving a share out is refused before any arithmetic.
    missing = request_for(client)
    del missing["reduction_expected"]
    assert client.post("/interventions/appraise", json=missing, headers=admin).status_code == 422
    nowhere = request_for(client, work_location="Nowhere")
    assert client.post("/interventions/appraise", json=nowhere, headers=admin).status_code == 404
    wild = request_for(client, mode="wild")
    assert client.post("/interventions/appraise", json=wild, headers=admin).status_code == 422


def test_a_projection_changes_nothing_in_the_baseline(client):
    admin = role("admin")
    before = client.get("/costs/summary", headers=admin).json()
    matrix = client.get("/costs/matrix", headers=admin).json()["totals"]
    for mode in ("base", "high"):
        client.post("/interventions/appraise", json=request_for(client, mode=mode), headers=admin)
    assert client.get("/costs/summary", headers=admin).json() == before
    assert client.get("/costs/matrix", headers=admin).json()["totals"] == matrix


def test_in_a_licensed_installation_it_follows_the_engagement_role(tmp_path):
    from test_accounts_engagements import ANALYST, CLIENT, LEAD, OUTSIDER, Installation

    site = Installation(tmp_path)
    for person in (LEAD, ANALYST, CLIENT, OUTSIDER):
        site.add_user(person)
    e = site.new_engagement("FY26", "Pine Valley County")
    site.add_member(e, LEAD, "admin")
    site.add_member(e, ANALYST, "hr_analyst")
    site.add_member(e, CLIENT, "executive")
    assert site.upload_sample(e, LEAD).status_code == 200
    state = site.runtime.workspaces.get(e)
    first = state.run().action_costs[0].dimensions
    body = {
        "role_family": first["role_family"],
        "work_location": first["work_location"],
        "name": "Training",
        "years": 2,
        "reduction_low": "0.1",
        "reduction_expected": "0.2",
        "reduction_high": "0.3",
        "basis": "Judgement.",
    }
    url = f"/engagements/{e}/interventions/appraise"
    for person in (LEAD, ANALYST):
        ok = site.http.post(url, json=body, headers=site.auth(person))
        assert ok.status_code == 200, ok.text
        assert (
            ok.json()["unit"]["topic"] == "" and ok.json()["intervention"]["total_cost"] == "0.00"
        )
    assert site.http.post(url, json=body, headers=site.auth(CLIENT)).status_code == 403
    assert site.http.post(url, json=body, headers=site.auth(OUTSIDER)).status_code == 404
    assert site.http.post(url, json=body).status_code == 401
