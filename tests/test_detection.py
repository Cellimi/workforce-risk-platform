"""Phase 2: which units' discipline rate stands out.

What is held here: that the arithmetic is exact and can be checked by hand; that the one
pattern planted in the synthetic county is found and nothing else is; that a few people
disciplined many times are not called a pattern in a unit; that nothing here names a person
or changes a dollar of the cost baseline; and that a role which sees totals only is refused.
"""

from __future__ import annotations

import math
import re
from datetime import date
from decimal import Decimal

import pytest
import yaml
from fastapi.testclient import TestClient

from wri_engine.adapters.county_hr_csv import CountyHrCsvAdapter
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.costing.assumptions import MissingAssumptionError, load_assumptions
from wri_engine.costing.engine import run_costing
from wri_engine.detection import (
    Exposure,
    UnitCount,
    employee_years,
    find_hotspots,
    load_detection_settings,
    unit_counts,
)
from wri_engine.detection.hotspots import DETECTION_FILE, NO_ROSTER, ONE_LOCATION, SETTING_IDS
from wri_engine.detection.stats import benjamini_hochberg, binomial_upper_tail
from wri_engine.generator import generate
from wri_engine.paths import CONFIG_DIR, PROJECT_ROOT, SYNTHETIC_DIR

#: Detection settings the owner has not reviewed. Both are starting values proposed by the
#: build. This is the list to read and then delete from, as the cost registry's is.
DETECTION_AWAITING_OWNER_REVIEW = {"detection_false_discovery_rate", "detection_min_rate_ratio"}

HOT = ("corrections_officer", "Main Detention Center")
SETTINGS = load_detection_settings()


# ---------------------------------------------------------------------------
# the arithmetic, checked by hand
# ---------------------------------------------------------------------------
def test_the_binomial_tail_is_exact():
    # Three fair coins: at least two heads is 4 of the 8 outcomes; three heads is 1 of 8.
    assert binomial_upper_tail(2, 3, 0.5) == pytest.approx(0.5, abs=1e-12)
    assert binomial_upper_tail(3, 3, 0.5) == pytest.approx(0.125, abs=1e-12)
    # At least none is certain; at least one of n is 1 - (1-p)^n.
    assert binomial_upper_tail(0, 40, 0.3) == 1.0
    assert binomial_upper_tail(1, 10, 0.2) == pytest.approx(1 - 0.8**10, abs=1e-12)
    # Against the definition, summed term by term, for a case too big to do in the head.
    n, p, k = 205, 0.632, 160
    by_definition = sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))
    assert binomial_upper_tail(k, n, p) == pytest.approx(by_definition, rel=1e-9)
    # The edges do not fall over.
    assert binomial_upper_tail(5, 5, 1.0) == 1.0 and binomial_upper_tail(1, 5, 0.0) == 0.0
    with pytest.raises(ValueError):
        binomial_upper_tail(6, 5, 0.5)


def test_the_adjustment_for_many_comparisons_is_the_published_one():
    """Benjamini-Hochberg by hand. Four p-values; sorted they are 0.01, 0.03, 0.04, 0.20.
    Each becomes p * 4 / rank: 0.04, 0.06, 0.0533, 0.20. Then no value may exceed a later
    one, so the 0.06 comes down to 0.0533. Returned in the order they were given."""
    adjusted = benjamini_hochberg([0.01, 0.04, 0.03, 0.20])
    assert adjusted == pytest.approx([0.04, 0.16 / 3, 0.16 / 3, 0.20])
    assert benjamini_hochberg([]) == []
    assert benjamini_hochberg([0.5]) == [0.5]
    # One comparison among many identical nulls is not made significant by the others.
    assert max(benjamini_hochberg([1.0] * 20 + [0.04])) == 1.0
    assert benjamini_hochberg([1.0] * 20 + [0.04])[-1] == pytest.approx(0.84)


def test_employee_years_count_the_part_of_the_period_each_person_was_there(loaded):
    data, _ = loaded
    someone = data.employees[0]
    start, end = date(2024, 1, 1), date(2024, 12, 31)

    def person(**changes):
        return someone.model_copy(update={"role_family": "j", "work_location": "A", **changes})

    whole = person(hire_date=date(2020, 5, 1), separation_date=None)
    joined = person(hire_date=date(2024, 7, 2), separation_date=None)  # 183 days
    left = person(hire_date=date(2019, 1, 1), separation_date=date(2024, 3, 31))  # 91 days
    before = person(hire_date=date(2015, 1, 1), separation_date=date(2023, 6, 30))
    after = person(hire_date=date(2025, 2, 1), separation_date=None)
    elsewhere = person(work_location="B", hire_date=date(2020, 1, 1), separation_date=None)
    data = data.model_copy(update={"employees": [whole, joined, left, before, after, elsewhere]})

    found = employee_years(data, start, end)
    assert found[("j", "A")].people == 3
    assert found[("j", "A")].employee_years == pytest.approx((366 + 183 + 91) / 365.25)
    assert found[("j", "B")] == Exposure(pytest.approx(366 / 365.25), 1)


# ---------------------------------------------------------------------------
# one finding, worked by hand
# ---------------------------------------------------------------------------
def counts(**units) -> dict:
    return {
        ("j", place): UnitCount(actions, people, Decimal(actions) * 1000, "Job", place)
        for place, (actions, people) in units.items()
    }


def years(**units) -> dict:
    return {("j", place): Exposure(float(value), int(value)) for place, value in units.items()}


def test_a_finding_can_be_checked_by_hand():
    """One job at two places with the same staffing. Place A had 12 actions among 10 people,
    place B had 2 among 2. If the two had the same rate, each of the 14 actions was equally
    likely to be at either place, so the chance of 12 or more at A is the chance of 12, 13
    or 14 heads in 14 fair coin tosses: (91 + 14 + 1) / 16384."""
    report = find_hotspots(counts(A=(12, 10), B=(2, 2)), years(A=100, B=100), SETTINGS)
    assert report.comparisons == 2
    a = next(f for f in report.findings if f.col == "A")
    assert a.p_value == pytest.approx(106 / 16384)
    assert (a.comparison_actions, a.comparison_people) == (2, 2)
    assert a.expected_actions == pytest.approx(2.0)  # B's rate, at A's staffing
    assert a.ratio == pytest.approx(6.0)
    # Two comparisons: the adjusted value is p * 2 / 1.
    assert a.q_value == pytest.approx(2 * 106 / 16384)
    # People: 10 here against 2 elsewhere, so 10, 11 or 12 heads in 12 tosses.
    assert a.q_value_people == pytest.approx(2 * (66 + 12 + 1) / 4096)
    assert a.status == "flagged"
    assert a.excess_actions == pytest.approx(10.0)
    assert a.cost_per_action == Decimal("1000.00")
    assert a.estimated_excess_cost == Decimal("10000.00")
    assert a.formula.startswith("10.0 actions more than expected x $1,000.00 average net cost")
    assert "12 actions among 10 people over 100.0 employee-years." in a.explanation
    assert "That is 6.00 times the rate elsewhere." in a.explanation
    b = next(f for f in report.findings if f.col == "B")
    assert b.status == "not_flagged" and b.estimated_excess_cost is None and b.formula == ""


def test_a_few_people_disciplined_many_times_are_not_a_pattern_in_the_unit():
    """Thirty actions at A, but among six people; B and C had 12 actions among 12. On
    actions A stands out. On people it does not, and it is not flagged."""
    report = find_hotspots(
        counts(A=(30, 6), B=(6, 6), C=(6, 6)), years(A=100, B=100, C=100), SETTINGS
    )
    a = next(f for f in report.findings if f.col == "A")
    assert a.q_value < 0.001 and a.ratio == pytest.approx(5.0)
    assert a.q_value_people > 0.05
    assert a.status == "concentrated"
    assert a.estimated_excess_cost is None
    assert "concentrated in few people" in a.explanation
    assert report.flagged == []


def test_fewer_people_than_the_minimum_group_is_never_a_flag():
    report = find_hotspots(counts(A=(40, 4), B=(1, 1)), years(A=50, B=400), SETTINGS)
    a = next(f for f in report.findings if f.col == "A")
    assert a.q_value < 1e-6 and a.status == "not_flagged"
    assert "fewer than 5 people were disciplined here" in a.explanation
    # The engagement's own minimum group size is what applies.
    looser = find_hotspots(counts(A=(40, 4), B=(1, 1)), years(A=50, B=400), SETTINGS, min_people=3)
    assert next(f for f in looser.findings if f.col == "A").status == "flagged"


def test_a_unit_must_differ_by_enough_to_matter():
    """Huge units: 1,150 actions against 1,000 at the same staffing is not chance, and it
    is a ratio of 1.15, which is not a flag."""
    report = find_hotspots(counts(A=(1150, 900), B=(1000, 800)), years(A=5000, B=5000), SETTINGS)
    a = next(f for f in report.findings if f.col == "A")
    assert a.q_value < 0.01 and a.ratio == pytest.approx(1.15)
    assert a.status == "not_flagged"
    assert "smaller than the 1.5 times" in a.explanation


def test_a_comparison_built_on_almost_nothing_flags_nothing():
    """Three actions where the comparison had none is an infinite ratio and proves nothing:
    with equal staffing it is three coin tosses."""
    report = find_hotspots(counts(A=(3, 3)), years(A=60, B=60), SETTINGS)
    a = report.findings[0]
    assert a.ratio is None and a.p_value == pytest.approx(0.125)
    assert a.status == "not_flagged"
    assert "The comparison had no actions at all" in a.explanation


def test_what_cannot_be_compared_is_said_and_not_guessed():
    report = find_hotspots(
        {
            ("solo", "HQ"): UnitCount(9, 9, Decimal(9000), "Solo job", "HQ"),
            ("ghost", "X"): UnitCount(2, 2, Decimal(2000), "Ghost job", "X"),
        },
        {("solo", "HQ"): Exposure(40.0, 40), ("ghost", "Y"): Exposure(10.0, 10)},
        SETTINGS,
    )
    reasons = {(n.row, n.col): n.reason for n in report.not_assessed}
    assert reasons == {("solo", "HQ"): ONE_LOCATION, ("ghost", "X"): NO_ROSTER}
    assert report.findings == [] and report.comparisons == 0


def test_units_with_no_actions_still_count_as_comparisons():
    few = find_hotspots(counts(A=(12, 10), B=(2, 2)), years(A=100, B=100), SETTINGS)
    many = find_hotspots(
        counts(A=(12, 10), B=(2, 2)),
        {**years(A=100, B=100), **{("k", p): Exposure(50.0, 50) for p in "PQRSTUVW"}},
        SETTINGS,
    )
    assert many.comparisons == few.comparisons + 8
    a_few = next(f for f in few.findings if f.col == "A")
    a_many = next(f for f in many.findings if f.col == "A")
    assert a_many.p_value == a_few.p_value and a_many.q_value > a_few.q_value


# ---------------------------------------------------------------------------
# the synthetic county: the planted pattern, and nothing else
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def county(tmp_path_factory, org, assumptions):
    """The whole synthetic county, generated here. The committed sample is a cut for
    browsing that keeps mostly disciplined employees, so rates in it mean nothing."""
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


def assess(data, run, settings=SETTINGS):
    decided = sorted(
        date.fromisoformat(str(a.dimensions["decision_date"])) for a in run.action_costs
    )
    return find_hotspots(
        unit_counts(run),
        employee_years(data, decided[0], decided[-1]),
        settings,
        window_start=decided[0],
        window_end=decided[-1],
    )


def test_the_planted_pattern_is_found_and_nothing_else_is(county):
    """The generator gives corrections officers at the main detention center 1.8 times the
    action rate (`embedded_pattern` in config/generator_profile.yaml). Detection has to find
    that unit, and must not find any of the thirty-one that were left alone."""
    _folder, data, run = county
    profile = yaml.safe_load((CONFIG_DIR / "generator_profile.yaml").read_text())
    planted = profile["embedded_pattern"]
    assert (planted["role_family"], planted["work_location"]) == HOT

    report = assess(data, run)
    assert [(f.row, f.col) for f in report.flagged] == [HOT]
    assert [f for f in report.findings if f.status == "concentrated"] == []
    assert report.comparisons == 32

    hot = report.flagged[0]
    assert (hot.actions, hot.people) == (160, 126)
    assert (hot.comparison_actions, hot.comparison_people) == (45, 41)
    # Found at about twice the rate, where 1.8 was planted: the planted multiplier also
    # shifts what people are disciplined for, and this is one draw of a random county.
    assert 1.8 <= hot.ratio <= 2.3
    assert hot.q_value < 0.001 and hot.q_value_people < 0.05
    assert hot.estimated_excess_cost == Decimal("554786.50")
    assert hot.estimated_excess_cost == (
        Decimal(str(round(hot.excess_actions, 1))) * hot.cost_per_action
    ).quantize(Decimal("0.01"))
    assert report.unreviewed_settings == tuple(sorted(DETECTION_AWAITING_OWNER_REVIEW))


def test_the_same_county_gives_the_same_findings(county):
    _folder, data, run = county
    assert assess(data, run).as_dict() == assess(data, run).as_dict()


def test_the_thresholds_decide_the_flag(county):
    """Ask for more than the planted unit shows and it is no longer flagged."""
    _folder, data, run = county
    stricter = SETTINGS.with_overrides({"detection_min_rate_ratio": Decimal("2.5")})
    assert assess(data, run, stricter).flagged == []
    cautious = SETTINGS.with_overrides({"detection_false_discovery_rate": Decimal("0.01")})
    assert assess(data, run, cautious).flagged == [], "the people test is 0.0156"
    looser = SETTINGS.with_overrides({"detection_min_rate_ratio": Decimal("1.25")})
    assert [(f.row, f.col) for f in assess(data, run, looser).flagged] == [HOT], (
        "nothing else in the county comes near, even at a lower bar"
    )


def test_detection_changes_nothing_in_the_cost_baseline(county):
    _folder, data, run = county
    before = (run.gross, run.offset, len(run.line_items), [i.amount for i in run.line_items])
    roster = data.model_dump_json()
    assess(data, run)
    assert (
        run.gross,
        run.offset,
        len(run.line_items),
        [i.amount for i in run.line_items],
    ) == before
    assert data.model_dump_json() == roster


def test_no_finding_names_a_person(county):
    _folder, data, run = county
    text = str(assess(data, run).as_dict())
    assert not re.search(r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b", text)


# ---------------------------------------------------------------------------
# the settings
# ---------------------------------------------------------------------------
def test_the_thresholds_live_in_their_own_file_with_their_standing():
    settings = load_detection_settings()
    assert set(settings.assumptions) == set(SETTING_IDS)
    pending = {i for i, a in settings.assumptions.items() if a.status == "TBD-MIKE"}
    assert pending == DETECTION_AWAITING_OWNER_REVIEW, (
        "a detection setting changed standing: update DETECTION_AWAITING_OWNER_REVIEW"
    )
    for setting in settings.assumptions.values():
        assert setting.source.strip() and setting.notes.strip()
        assert setting.applies_to == {"detector": "hotspots"}
    # They are not cost coefficients and are not in the cost registry.
    cost_registry = load_assumptions()
    assert not set(SETTING_IDS) & set(cost_registry.assumptions)
    assert len(cost_registry.assumptions) == 186
    assert DETECTION_FILE == CONFIG_DIR / "detection.yaml"


def test_a_missing_threshold_is_an_error_not_a_default(tmp_path):
    text = (CONFIG_DIR / "detection.yaml").read_text()
    cut = text[: text.index("- id: detection_min_rate_ratio")]
    (tmp_path / "detection.yaml").write_text(cut)
    with pytest.raises(MissingAssumptionError, match="detection_min_rate_ratio"):
        load_detection_settings(tmp_path / "detection.yaml")


def test_no_threshold_is_written_into_the_code():
    """Rule 1, for detection: the numbers that decide a flag are in the settings file and
    nowhere in the code that uses them."""
    values = {str(a.value) for a in load_detection_settings().assumptions.values()}
    assert values == {"0.05", "1.5"}
    for path in (PROJECT_ROOT / "src" / "wri_engine" / "detection").glob("*.py"):
        code = re.sub(r'""".*?"""', "", path.read_text(), flags=re.S)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        for value in values:
            assert not re.search(rf"(?<![\w.]){re.escape(value)}(?![\w.])", code), (
                path.name,
                value,
            )


# ---------------------------------------------------------------------------
# the API: who may see it
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def client(county):
    folder, _data, _run = county
    STATE.load(folder)
    yield TestClient(app)
    STATE.load(SYNTHETIC_DIR / "sample")


def role(name: str) -> dict:
    return {"X-WRI-Role": name}


def test_roles_that_see_records_see_the_units(client):
    for who in ("hr_analyst", "admin"):
        response = client.get("/detection/hotspots", headers=role(who))
        assert response.status_code == 200, response.text
        body = response.json()
        assert [(f["row"], f["col"]) for f in body["flagged"]] == [HOT]
        assert body["comparisons"] == 32 and body["min_people"] == 5
        assert body["settings"]["unreviewed"] == sorted(DETECTION_AWAITING_OWNER_REVIEW)
        assert "does not say why" in body["note"]
        hot = body["flagged"][0]
        assert hot["estimated_excess_cost"] == "554786.50"
        assert hot["setting_ids"] == list(SETTING_IDS)
        assert not re.search(r"\bE\d{5}\b|EMP-[0-9A-F]{8}", response.text)


def test_a_role_that_sees_totals_only_is_refused(client):
    """Shown a large unit beside a location total they can already see, a client reader
    could subtract their way to a group of two or three people. The cost matrix withholds
    those cells for the same reason."""
    refused = client.get("/detection/hotspots", headers=role("executive"))
    assert refused.status_code == 403
    assert "not permitted to view records" in refused.json()["detail"]
    assert client.get("/detection/hotspots").status_code == 403

    matrix = client.get(
        "/costs/matrix",
        params={"rows": "role_family", "cols": "work_location"},
        headers=role("executive"),
    ).json()
    hot_cell = next(c for c in matrix["cells"] if (c["row"], c["col"]) == HOT)
    assert hot_cell["suppressed"] is True, (
        "the matrix withholds the very unit, to protect its neighbours"
    )


def test_asking_changes_no_figure(client):
    before = client.get("/costs/summary", headers=role("admin")).json()
    client.get("/detection/hotspots", headers=role("admin"))
    client.get("/detection/hotspots", params={"mode": "high"}, headers=role("admin"))
    assert client.get("/costs/summary", headers=role("admin")).json() == before
    assert (
        client.get(
            "/detection/hotspots", params={"mode": "wild"}, headers=role("admin")
        ).status_code
        == 422
    )


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
    url = f"/engagements/{e}/detection/hotspots"
    assert site.http.get(url, headers=site.auth(LEAD)).status_code == 200
    assert site.http.get(url, headers=site.auth(ANALYST)).status_code == 200
    assert site.http.get(url, headers=site.auth(CLIENT)).status_code == 403
    assert site.http.get(url, headers=site.auth(OUTSIDER)).status_code == 404
    assert site.http.get(url).status_code == 401
