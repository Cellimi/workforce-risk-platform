"""Phase 2: where a type of misconduct is rising.

What is held here: that the test is exact and can be checked by hand; that a rise which
reached everyone is not called a unit's; that a unit which only held steady, or only grew
in staff, is not called rising; that a few people disciplined many times are not a pattern
in a unit; that the synthetic county, which plants a level and no rise, is left alone, and
that a rise planted in it is found; and that who sees what is as for hotspots.
"""

from __future__ import annotations

import dataclasses
import math
import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from wri_engine.adapters.county_hr_csv import CountyHrCsvAdapter
from wri_engine.aggregation.suppression import DisclosureLedger
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.costing.engine import run_costing
from wri_engine.detection import (
    BY_MISCONDUCT,
    OVERALL,
    Exposure,
    Tally,
    UnitHalves,
    drift_counts,
    employee_years,
    find_drift,
    halves,
    load_detection_settings,
)
from wri_engine.detection.drift import NEW_UNIT, NOTHING_ELSE
from wri_engine.detection.stats import shifted_upper_tail
from wri_engine.generator import generate
from wri_engine.paths import SYNTHETIC_DIR

SETTINGS = load_detection_settings()
HOT = ("corrections_officer", "Main Detention Center")
HOT_TYPE = "attendance_awol"
IDS = r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b"


# ---------------------------------------------------------------------------
# the arithmetic, checked by hand
# ---------------------------------------------------------------------------
def test_the_two_period_test_is_fishers_and_exact():
    """Twelve actions, six of them later, six of them the unit's. If the unit changed as
    everyone did, the chance that five or six of its six are later ones is
    (C(6,5)*C(6,1) + C(6,6)*C(6,0)) / C(12,6) = (36 + 1) / 924."""
    assert shifted_upper_tail(5, 6, 6, 12) == pytest.approx(37 / 924, abs=1e-12)
    assert shifted_upper_tail(6, 6, 6, 12) == pytest.approx(1 / 924, abs=1e-12)
    assert shifted_upper_tail(0, 6, 6, 12) == 1.0
    # Fewer than is possible is certain; more than is possible is a mistake.
    assert shifted_upper_tail(1, 10, 11, 12) == 1.0  # at least nine must be later
    with pytest.raises(ValueError):
        shifted_upper_tail(7, 6, 6, 12)
    with pytest.raises(ValueError):
        shifted_upper_tail(1, 13, 6, 12)
    with pytest.raises(ValueError):
        shifted_upper_tail(1, 6, 6, 12, odds=0)


def test_the_allowance_for_staffing_is_the_weighted_count():
    """With the unit's staffing grown 1.3 times more than the rest's, each way of having x
    of its actions later counts odds**x times. Against that definition, term by term."""
    group, later, total, odds, k = 50, 300, 700, 1.3, 30
    weights = {
        x: math.comb(group, x) * math.comb(total - group, later - x) * odds**x
        for x in range(0, group + 1)
    }
    by_definition = sum(w for x, w in weights.items() if x >= k) / sum(weights.values())
    assert shifted_upper_tail(k, group, later, total, odds) == pytest.approx(by_definition)
    # Growth makes later actions less surprising, never more.
    assert shifted_upper_tail(k, group, later, total, odds) > shifted_upper_tail(
        k, group, later, total
    )


def test_the_period_is_cut_in_half_by_date():
    # 366 days: 183 and 183.
    assert halves(date(2024, 1, 1), date(2024, 12, 31)) == (date(2024, 7, 1), date(2024, 7, 2))
    # 365 days: the earlier half is the shorter by one, 182 and 183.
    assert halves(date(2025, 1, 1), date(2025, 12, 31)) == (date(2025, 7, 1), date(2025, 7, 2))
    assert halves(date(2025, 3, 1), date(2025, 3, 2)) == (date(2025, 3, 1), date(2025, 3, 2))


# ---------------------------------------------------------------------------
# findings, worked by hand
# ---------------------------------------------------------------------------
def unit(job: str, place: str, earlier: tuple, later: tuple, topic: str = "") -> dict:
    """One unit: (actions, people) in each half. Its people are its own, and different
    people in each half unless a test says otherwise."""

    def tally(count: tuple, half: str) -> Tally:
        actions, people = count
        cohort = frozenset(f"{job}-{place}-{topic}-{half}{n}" for n in range(people))
        return Tally(actions, Decimal(actions) * 1000, cohort)

    return {
        (job, place, topic): UnitHalves(
            f"Job {job}", place, topic, topic.title(), tally(earlier, "e"), tally(later, "l")
        )
    }


def staff(**units: float) -> dict:
    """Employee-years by unit, written job_place=years."""
    return {tuple(key.split("_")): Exposure(float(v), int(v)) for key, v in units.items()}


def one(report, job: str, place: str):
    return next(f for f in report.findings if (f.row, f.col) == (job, place))


def test_a_rising_unit_can_be_checked_by_hand():
    """Unit A went from 2 actions to 12; unit B, the rest of the organization, from 10 to
    4. Staffing did not change. Of 28 actions 16 are later, and 14 are A's. If A had
    changed as B did, the chance that 12 or more of A's 14 are later ones is
    (C(14,12)*C(14,4) + C(14,13)*C(14,3) + C(14,14)*C(14,2)) / C(28,16)
    = (91,091 + 5,096 + 91) / 30,421,755."""
    counts = {**unit("j", "A", (2, 2), (12, 12)), **unit("k", "B", (10, 10), (4, 4))}
    same = staff(j_A=100, k_B=100)
    report = find_drift(counts, same, same, SETTINGS, by=OVERALL)
    assert report.comparisons == 2
    a = one(report, "j", "A")
    assert a.p_value == pytest.approx(96278 / 30421755)
    assert a.q_value == pytest.approx(2 * 96278 / 30421755)  # the smaller of two
    assert a.own_change == pytest.approx(6.0)
    assert a.elsewhere_change == pytest.approx(0.4)
    assert a.ratio == pytest.approx(15.0)
    assert a.status == "flagged"
    # People are the same counts here, and the check confirms the one unit that stood out.
    assert a.q_value_people == pytest.approx(96278 / 30421755)
    # Paired with the baseline: what the actions cost in each half.
    assert (a.earlier_net, a.later_net) == (Decimal("2000"), Decimal("12000"))
    # Expected later: the earlier rate, changed as the rest changed. 2 x 0.4 = 0.8.
    assert a.expected_later_actions == pytest.approx(0.8)
    assert a.estimated_excess_cost == Decimal("11200.00")
    assert a.formula.startswith("11.2 later actions more than expected x $1,000.00 average")
    assert "Earlier half: 2 actions among 2 people over 100.0 employee-years." in a.explanation
    assert "Later half: 12 among 12 people over 100.0." in a.explanation
    assert "That is 6.00 times the unit's earlier rate." in a.explanation
    assert "went from 10 to 4, 0.40 times the earlier rate there" in a.explanation
    assert "Flagged as rising" in a.explanation
    b = one(report, "k", "B")
    assert b.status == "not_flagged" and b.estimated_excess_cost is None and b.formula == ""


def test_a_rise_that_reached_everyone_is_not_a_units():
    """Three units each go from 10 actions to 30: thin early records, or a new policy. Each
    tripled, and none of them is a finding."""
    counts = {
        **unit("j", "A", (10, 10), (30, 30)),
        **unit("k", "B", (10, 10), (30, 30)),
        **unit("m", "C", (10, 10), (30, 30)),
    }
    same = staff(j_A=100, k_B=100, m_C=100)
    report = find_drift(counts, same, same, SETTINGS, by=OVERALL)
    assert report.flagged == []
    a = one(report, "j", "A")
    assert a.own_change == pytest.approx(3.0) and a.ratio == pytest.approx(1.0)
    assert "Not flagged: a difference this size could be chance." in a.explanation


def test_holding_steady_while_the_rest_improved_is_not_a_rise():
    counts = {**unit("j", "A", (20, 20), (20, 20)), **unit("k", "B", (80, 80), (20, 20))}
    same = staff(j_A=100, k_B=400)
    report = find_drift(counts, same, same, SETTINGS, by=OVERALL)
    a = one(report, "j", "A")
    assert a.q_value < 0.05 and a.ratio == pytest.approx(4.0)
    assert a.own_change == pytest.approx(1.0)
    assert a.status == "not_flagged"
    assert "the unit's own rate rose by less than the 1.5 times" in a.explanation


def test_a_unit_that_only_grew_in_staff_is_not_rising():
    """A's staff doubled and so did its actions: the same rate. Without the allowance for
    staffing, twice the actions would look like a rise."""
    counts = {**unit("j", "A", (20, 20), (40, 40)), **unit("k", "B", (60, 60), (60, 60))}
    earlier, later = staff(j_A=100, k_B=300), staff(j_A=200, k_B=300)
    report = find_drift(counts, earlier, later, SETTINGS, by=OVERALL)
    a = one(report, "j", "A")
    assert a.own_change == pytest.approx(1.0) and a.ratio == pytest.approx(1.0)
    assert a.p_value > 0.4 and a.status == "not_flagged"
    unallowed = find_drift(counts, earlier, earlier, SETTINGS, by=OVERALL)
    assert one(unallowed, "j", "A").own_change == pytest.approx(2.0)
    assert one(unallowed, "j", "A").p_value < a.p_value


def test_a_few_people_disciplined_many_times_are_not_a_rising_unit():
    """A went from 2 actions to 30, but the 30 are five people's. On actions it stands out.
    Counting people, five against two is nothing, and it is not flagged."""
    counts = {**unit("j", "A", (2, 2), (30, 5)), **unit("k", "B", (40, 40), (40, 40))}
    same = staff(j_A=100, k_B=400)
    report = find_drift(counts, same, same, SETTINGS, by=OVERALL)
    a = one(report, "j", "A")
    assert a.q_value < 0.001 and a.q_value_people > 0.05
    assert a.status == "concentrated" and a.estimated_excess_cost is None
    assert "concentrated in few people" in a.explanation
    assert report.flagged == []
    # One fewer and it is under the minimum group, whatever the test says.
    counts = {**unit("j", "A", (2, 2), (30, 4)), **unit("k", "B", (40, 40), (40, 40))}
    a = one(find_drift(counts, same, same, SETTINGS, by=OVERALL), "j", "A")
    assert a.status == "not_flagged"
    assert "fewer than 5 people were disciplined here in the later half" in a.explanation


def test_a_unit_too_small_to_flag_is_listed_and_not_compared():
    """Four actions in all can never be five people in the later half. Counting it as a
    comparison would only make the real ones harder to pass."""
    big = {**unit("j", "A", (2, 2), (12, 12)), **unit("k", "B", (10, 10), (4, 4))}
    same = staff(j_A=100, k_B=100, m_C=100)
    without = find_drift(big, same, same, SETTINGS, by=OVERALL)
    with_small = find_drift(
        {**big, **unit("m", "C", (1, 1), (3, 3))}, same, same, SETTINGS, by=OVERALL
    )
    assert with_small.comparisons == without.comparisons == 2
    small = one(with_small, "m", "C")
    assert small.status == "not_flagged" and small.q_value == 1.0
    assert "adjusted chance value" not in small.explanation
    assert "fewer than 5 people" in small.explanation
    # The engagement's own minimum is what applies.
    looser = find_drift(
        {**big, **unit("m", "C", (1, 1), (3, 3))}, same, same, SETTINGS, by=OVERALL, min_people=3
    )
    assert looser.comparisons == 3


def test_none_before_and_some_now_is_assessed_without_a_rate_of_change():
    counts = {**unit("j", "A", (0, 0), (12, 12)), **unit("k", "B", (30, 30), (30, 30))}
    same = staff(j_A=100, k_B=300)
    a = one(find_drift(counts, same, same, SETTINGS, by=OVERALL), "j", "A")
    assert a.own_change is None and a.ratio is None and a.status == "flagged"
    assert "There was none in the earlier half" in a.explanation
    assert a.expected_later_actions == 0.0 and a.estimated_excess_cost == Decimal("12000.00")
    # And when the rest had none earlier either, no estimate is offered.
    counts = {**unit("j", "A", (0, 0), (12, 12)), **unit("k", "B", (0, 0), (2, 2))}
    a = one(find_drift(counts, same, same, SETTINGS, by=OVERALL), "j", "A")
    assert a.elsewhere_change is None and a.expected_later_actions is None
    assert a.estimated_excess_cost is None and a.formula == ""
    assert a.as_dict()["expected_later_actions"] is None


def test_what_cannot_be_compared_is_said_and_not_guessed():
    counts = {**unit("j", "A", (0, 0), (9, 9)), **unit("k", "B", (9, 9), (9, 9))}
    # Job j did not exist at A in the earlier half.
    report = find_drift(counts, staff(k_B=100), staff(j_A=50, k_B=100), SETTINGS, by=OVERALL)
    reasons = {(n.row, n.col): n.reason for n in report.not_assessed}
    assert reasons[("j", "A")] == NEW_UNIT
    # A type of misconduct only one unit was ever disciplined for.
    alone = unit("j", "A", (4, 4), (9, 9), topic="safety")
    same = staff(j_A=100, k_B=100)
    report = find_drift(alone, same, same, SETTINGS)
    assert [n.reason for n in report.not_assessed] == [NOTHING_ELSE]
    assert report.findings == [] and report.comparisons == 0
    # No records at all.
    empty = find_drift({}, {}, {}, SETTINGS)
    assert empty.as_dict()["earlier_start"] is None and empty.findings == []
    with pytest.raises(ValueError, match="by must be"):
        drift_counts(None, date(2025, 1, 1), "person")


def test_by_type_a_rise_is_set_against_the_same_type_elsewhere():
    """Attendance at A rose; safety at A did not. Each is compared with its own type in
    the rest of the organization, never with the other."""
    counts = {
        **unit("j", "A", (2, 2), (12, 12), topic="attendance"),
        **unit("k", "B", (10, 10), (4, 4), topic="attendance"),
        **unit("j", "A", (8, 8), (8, 8), topic="safety"),
        **unit("k", "B", (8, 8), (8, 8), topic="safety"),
    }
    same = staff(j_A=100, k_B=100)
    report = find_drift(counts, same, same, SETTINGS, by=BY_MISCONDUCT)
    assert report.by == "misconduct" and report.comparisons == 4
    assert [(f.col, f.topic) for f in report.flagged] == [("A", "attendance")]
    hot = report.flagged[0]
    assert (hot.elsewhere_earlier_actions, hot.elsewhere_later_actions) == (10, 4)
    assert "Earlier half: 2 Attendance actions among 2 people" in hot.explanation
    assert hot.p_value == pytest.approx(96278 / 30421755)


# ---------------------------------------------------------------------------
# what a totals-only reader may be shown
# ---------------------------------------------------------------------------
def test_a_totals_reader_gets_rising_units_under_the_small_group_rules():
    counts = {**unit("j", "A", (6, 6), (40, 40)), **unit("k", "B", (30, 30), (30, 30))}
    same = staff(j_A=100, k_B=300)
    report = find_drift(counts, same, same, SETTINGS, by=OVERALL)
    assert len(report.flagged) == 1
    ledger = DisclosureLedger(min_cell_size=5)
    shown = report.for_totals_reader(ledger)
    assert shown["detail"] == "flagged_only" and shown["withheld"] == 0
    assert shown["flagged"] == [report.flagged[0].as_dict()]
    assert not {"not_flagged", "concentrated", "not_assessed"} & set(shown)
    assert "cohort" not in str(shown), "who the people are never leaves the server"
    # The unit in each half, the unit across both, and the rest in each half.
    assert sorted(len(c) for c in ledger.released) == [6, 30, 30, 40, 46]
    full = report.as_dict()
    assert full["detail"] == "full" and len(full["not_flagged"]) == 1


def test_a_rise_from_two_people_is_withheld_from_a_totals_reader():
    """The finding would have to state a figure about the two."""
    counts = {**unit("j", "A", (2, 2), (12, 12)), **unit("k", "B", (10, 10), (4, 4))}
    same = staff(j_A=100, k_B=100)
    report = find_drift(counts, same, same, SETTINGS, by=OVERALL)
    assert len(report.flagged) == 1
    ledger = DisclosureLedger(min_cell_size=5)
    shown = report.for_totals_reader(ledger)
    assert shown["flagged"] == [] and shown["withheld"] == 1
    assert ledger.released == [], "nothing about a withheld finding is recorded"


# ---------------------------------------------------------------------------
# the synthetic county: a level is planted there, and no rise
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def county(tmp_path_factory, org, assumptions):
    folder = tmp_path_factory.mktemp("county")
    generate(seed=20260917).write(folder)
    adapter = CountyHrCsvAdapter(org=org)
    data = adapter.load(folder)
    run = run_costing(
        data,
        as_of=date(2026, 9, 17),
        org=org,
        assumptions=assumptions,
        excluded_action_ids=adapter.report.blocked_action_ids,
    )
    return folder, data, run


def assess(data, run, by=BY_MISCONDUCT):
    decided = sorted(
        date.fromisoformat(str(a.dimensions["decision_date"])) for a in run.action_costs
    )
    start, end = decided[0], decided[-1]
    earlier_ends, later_starts = halves(start, end)
    return find_drift(
        drift_counts(run, later_starts, by),
        employee_years(data, start, earlier_ends),
        employee_years(data, later_starts, end),
        SETTINGS,
        by=by,
        earlier=(start, earlier_ends),
        later=(later_starts, end),
    )


def test_nothing_in_the_county_is_rising(county):
    """The generator makes one unit's rate higher throughout. It does not make any rate
    change over time, so nothing may be flagged as rising: not even the unit the hotspot
    detector finds."""
    _folder, data, run = county
    by_type = assess(data, run)
    assert by_type.flagged == [] and by_type.comparisons == 21
    assert (by_type.earlier_start, by_type.later_end) == (date(2023, 10, 20), date(2026, 9, 17))
    hot = next(f for f in by_type.findings if (f.row, f.col, f.topic) == (*HOT, HOT_TYPE))
    assert (hot.earlier_actions, hot.later_actions) == (37, 33)
    assert hot.own_change == pytest.approx(0.95, abs=0.01)
    assert hot.ratio == pytest.approx(0.99, abs=0.01)
    overall = assess(data, run, OVERALL)
    assert overall.flagged == [] and overall.comparisons == 17
    for report in (by_type, overall):
        assert [f for f in report.findings if f.status == "concentrated"] == []
    # Every action is in one half or the other of some unit.
    assert overall.not_assessed == []
    assert sum(f.earlier_actions + f.later_actions for f in overall.findings) == len(
        run.action_costs
    )


def thinned(run, later_starts: date, keep_one_in: int):
    """The same run with most of one unit's earlier attendance actions taken out, so that
    its later half stands well above its earlier one."""
    kept, seen = [], 0
    for action in run.action_costs:
        d = action.dimensions
        early_hot = (d["role_family"], d["work_location"], d["misconduct_category"]) == (
            *HOT,
            HOT_TYPE,
        ) and date.fromisoformat(str(d["decision_date"])) < later_starts
        if early_hot:
            seen += 1
            if seen % keep_one_in:
                continue
        kept.append(action)
    return dataclasses.replace(run, action_costs=kept)


def test_a_rise_planted_in_the_county_is_found_and_nothing_else_is(county):
    _folder, data, run = county
    later_starts = assess(data, run).later_start
    rising = thinned(run, later_starts, keep_one_in=4)
    report = assess(data, rising)
    assert [(f.row, f.col, f.topic) for f in report.flagged] == [(*HOT, HOT_TYPE)]
    hot = report.flagged[0]
    # Taking actions out moved the start of the period, so the halves are its own.
    assert report.earlier_start == date(2023, 11, 12)
    assert (hot.earlier_actions, hot.later_actions) == (9, 33)
    assert (hot.elsewhere_earlier_actions, hot.elsewhere_later_actions) == (50, 41)
    assert hot.own_change == pytest.approx(3.91, abs=0.01)
    assert hot.ratio == pytest.approx(4.46, abs=0.01)
    assert hot.q_value == pytest.approx(0.0049, abs=0.0002)
    # Paired with the baseline: the later half's cost is the sum of those very actions.
    theirs = [
        a
        for a in run.action_costs
        if (
            a.dimensions["role_family"],
            a.dimensions["work_location"],
            a.dimensions["misconduct_category"],
        )
        == (*HOT, HOT_TYPE)
        and date.fromisoformat(str(a.dimensions["decision_date"])) >= report.later_start
    ]
    assert len(theirs) == 33 and hot.later_net == sum(a.net for a in theirs)
    assert hot.estimated_excess_cost == Decimal("117409.28")
    # Every type together, the same thinning is a smaller share of the unit, and is not
    # enough: 9 fewer earlier actions out of some eighty.
    assert assess(data, rising, OVERALL).flagged == []
    assert not re.search(IDS, str(report.as_dict()))


def test_the_same_county_gives_the_same_findings_and_changes_nothing(county):
    _folder, data, run = county
    before = (run.gross, run.offset, len(run.line_items), [i.amount for i in run.line_items])
    roster = data.model_dump_json()
    assert assess(data, run).as_dict() == assess(data, run).as_dict()
    assert assess(data, run, OVERALL).as_dict() == assess(data, run, OVERALL).as_dict()
    assert (
        run.gross,
        run.offset,
        len(run.line_items),
        [i.amount for i in run.line_items],
    ) == before
    assert data.model_dump_json() == roster


# ---------------------------------------------------------------------------
# the API: who may see it
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(county):
    folder, _data, _run = county
    STATE.load(folder)
    yield TestClient(app)
    STATE.load(SYNTHETIC_DIR / "sample")


def role(name: str, session: str = "") -> dict:
    return {"X-WRI-Role": name, **({"X-WRI-Session": session} if session else {})}


def test_the_endpoint_gives_each_role_its_view(client):
    for who in ("hr_analyst", "admin"):
        response = client.get("/detection/drift", headers=role(who))
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["by"] == "misconduct" and body["detail"] == "full"
        assert body["flagged"] == [] and body["comparisons"] == 21
        assert (body["earlier_start"], body["later_end"]) == ("2023-10-20", "2026-09-17")
        assert body["earlier_end"] < body["later_start"]
        assert len(body["not_flagged"]) > 21, "units too small to compare are still listed"
        assert "does not say why" in body["note"]
        assert not re.search(IDS, response.text)
        overall = client.get("/detection/drift", params={"by": "overall"}, headers=role(who))
        assert overall.json()["comparisons"] == 17

    reader = client.get("/detection/drift", headers=role("executive", "drift-reader"))
    assert reader.status_code == 200
    assert reader.json()["detail"] == "flagged_only"
    assert not {"not_flagged", "concentrated", "not_assessed"} & set(reader.json())
    for bad in ({"by": "person"}, {"mode": "wild"}):
        assert client.get("/detection/drift", params=bad, headers=role("admin")).status_code == 422


def test_asking_changes_no_figure(client):
    before = client.get("/costs/summary", headers=role("admin")).json()
    client.get("/detection/drift", headers=role("admin"))
    client.get("/detection/drift", params={"mode": "high", "by": "overall"}, headers=role("admin"))
    assert client.get("/costs/summary", headers=role("admin")).json() == before


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
    url = f"/engagements/{e}/detection/drift"
    assert site.http.get(url, headers=site.auth(LEAD)).json()["detail"] == "full"
    assert site.http.get(url, headers=site.auth(ANALYST)).json()["detail"] == "full"
    reader = site.http.get(url, headers=site.auth(CLIENT))
    assert reader.status_code == 200 and reader.json()["detail"] == "flagged_only"
    assert site.http.get(url, headers=site.auth(OUTSIDER)).status_code == 404
    assert site.http.get(url).status_code == 401


def test_a_rise_only_a_little_more_than_everyones_is_not_the_units():
    """Huge units. A tripled while the rest rose 2.4 times: not chance, and 1.25 times the
    rise elsewhere, which is not a flag."""
    counts = {
        **unit("j", "A", (1000, 900), (3000, 2500)),
        **unit("k", "B", (1000, 900), (2400, 2000)),
    }
    same = staff(j_A=5000, k_B=5000)
    a = one(find_drift(counts, same, same, SETTINGS, by=OVERALL), "j", "A")
    assert a.q_value < 0.001 and a.own_change == pytest.approx(3.0)
    assert a.ratio == pytest.approx(1.25) and a.status == "not_flagged"
    assert "less than 1.5 times the rise in the rest of the organization" in a.explanation
