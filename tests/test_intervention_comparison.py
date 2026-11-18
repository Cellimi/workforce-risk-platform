"""Phase 3: several interventions for one unit, side by side.

What is held here: that the yearly figures can be checked by hand and are only an
appraisal's own figures divided by its years; that the order rests on the one figure that
needs no judgement; that nothing is named the best and no savings are added together; that
only appraisals for the same unit and type are set against each other; and that a client's
copy compares only what it shows with its money.
"""

from __future__ import annotations

import io
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_interventions import baseline, proposal
from test_reporting import pdf_text, sheet_rows
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.generator import generate
from wri_engine.interventions import appraise, compare
from wri_engine.interventions.comparison import NOTES
from wri_engine.paths import SYNTHETIC_DIR

HOT = ("corrections_officer", "Main Detention Center")
HOT_NAME = "Attendance / AWOL among Corrections Officer at Main Detention Center"
ADMIN = {"X-WRI-Role": "admin", "X-WRI-Session": "compare-lead"}


# ---------------------------------------------------------------------------
# the arithmetic, checked by hand
# ---------------------------------------------------------------------------
def test_a_comparison_can_be_checked_by_hand():
    """The discipline costs $10,000 a year.

    Flexible start times: $9,000 over 2 years, so $4,500 a year. Preventing 10%, 50%, 100%
    prevents $1,000, $5,000, $10,000 a year: net -$3,500, $500, $5,500. It pays for itself
    at $9,000 / $20,000 = 45%.

    A rota change: $5,000 over 5 years, so $1,000 a year. Preventing 10%, 20%, 30%: net $0,
    $1,000, $2,000. It pays for itself at $5,000 / $50,000 = 10%.

    The rota change is listed first: it has less to prove."""
    flexible = appraise(baseline(), proposal())
    rota = appraise(
        baseline(),
        proposal(
            name="A rota change",
            one_time_cost=Decimal("5000"),
            annual_cost_per_person=Decimal(0),
            other_annual_cost=Decimal(0),
            years=5,
            reduction_low=Decimal("0.1"),
            reduction_expected=Decimal("0.2"),
            reduction_high=Decimal("0.3"),
        ),
    )
    (table,) = compare([("f", flexible), ("r", rota)])
    assert table.baseline.annual_net == Decimal("10000")
    first, second = table.rows
    assert (first.key, first.name) == ("r", "A rota change")
    assert first.yearly_cost == Decimal("1000") and first.break_even == Decimal("0.1")
    assert first.yearly_net_savings == (0, 1000, 2000)
    assert (second.key, second.years) == ("f", 2)
    assert second.yearly_cost == Decimal("4500") and second.break_even == Decimal("0.45")
    assert second.yearly_net_savings == (-3500, 500, 5500)
    # Nothing new is worked out: each yearly figure is the appraisal's own, over its years.
    for row, appraisal in ((first, rota), (second, flexible)):
        assert row.total_cost == appraisal.intervention.total_cost
        for yearly, scenario in zip(row.yearly_net_savings, appraisal.scenarios, strict=True):
            assert yearly * row.years == scenario.net_saving
    body = table.as_dict()
    assert body["rows"][0] == {
        "key": "r",
        "name": "A rota change",
        "years": 5,
        "total_cost": "5000.00",
        "yearly_cost": "1000.00",
        "yearly_net_saving_low": "0.00",
        "yearly_net_saving_expected": "1000.00",
        "yearly_net_saving_high": "2000.00",
        "reductions": ["0.1", "0.2", "0.3"],
        "break_even": "0.1000",
        "can_pay_for_itself": True,
    }
    assert body["unit"]["name"] == "Tardiness among Caseworker at North Office"


def test_the_order_does_not_rest_on_anyones_estimate():
    """Claim that the dearer proposal prevents everything and the cheaper one almost
    nothing: the dearer one now has much the larger expected saving, and is still listed
    second, because what each has to prove has not changed."""
    cheap = proposal(
        name="Cheap",
        one_time_cost=Decimal("1000"),
        annual_cost_per_person=Decimal(0),
        other_annual_cost=Decimal(0),
        reduction_low=Decimal("0.01"),
        reduction_expected=Decimal("0.02"),
        reduction_high=Decimal("0.03"),
    )
    dear = proposal(
        name="Dear",
        reduction_low=Decimal("0.9"),
        reduction_expected=Decimal("1"),
        reduction_high=Decimal("1"),
    )
    (table,) = compare([("d", appraise(baseline(), dear)), ("c", appraise(baseline(), cheap))])
    assert [row.name for row in table.rows] == ["Cheap", "Dear"]
    assert table.rows[1].yearly_net_savings[1] > table.rows[0].yearly_net_savings[1]


def test_nothing_is_named_the_best_and_no_savings_are_added():
    said = " ".join(NOTES).lower()
    assert "cannot be added" in said and "alternatives" in said
    assert "no judgement" in said and "projection" in said
    for word in ("best", "recommend", "winner", "should choose", "combined saving"):
        assert word not in said
    (table,) = compare(
        [("a", appraise(baseline(), proposal())), ("b", appraise(baseline(), proposal(name="B")))]
    )
    body = table.as_dict()
    assert set(body) == {"unit", "rows", "notes"}, "no total, no combined figure, no pick"
    assert not any("rank" in key or "best" in key for row in body["rows"] for key in row)


def test_only_the_same_unit_and_type_are_set_against_each_other():
    here = appraise(baseline(), proposal())
    import dataclasses

    elsewhere = appraise(dataclasses.replace(baseline(), col="South Office"), proposal())
    other_type = appraise(dataclasses.replace(baseline(), topic="", topic_label=""), proposal())
    assert compare([("a", here)]) == []
    assert compare([("a", here), ("b", elsewhere), ("c", other_type)]) == []
    tables = compare([("a", here), ("b", elsewhere), ("c", here), ("d", elsewhere), ("e", here)])
    assert [len(t.rows) for t in tables] == [3, 2]
    assert [t.baseline.col for t in tables] == ["North Office", "South Office"]
    assert compare([]) == []


def test_ties_and_units_with_nothing_to_save_have_a_settled_order():
    same = [
        (key, appraise(baseline(), proposal(name=name))) for key, name in (("2", "B"), ("1", "A"))
    ]
    assert [row.name for row in compare(same)[0].rows] == ["A", "B"]
    # No net cost of discipline: no break-even share for anything, and they go by name.
    free = [
        (k, appraise(baseline(net="-6000"), proposal(name=n))) for k, n in (("x", "Z"), ("y", "Y"))
    ]
    (table,) = compare(free)
    assert [row.name for row in table.rows] == ["Y", "Z"]
    assert all(row.break_even is None for row in table.rows)
    assert table.as_dict()["rows"][0]["can_pay_for_itself"] is False
    assert compare(same) == compare(same)


# ---------------------------------------------------------------------------
# the API and the deliverables, on the synthetic county
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def county(tmp_path_factory):
    folder = tmp_path_factory.mktemp("county")
    generate(seed=20260917).write(folder)
    STATE.load(folder)
    yield TestClient(app)
    STATE._appraisals = None
    STATE.load(SYNTHETIC_DIR / "sample")


def body(**changes) -> dict:
    return {
        "role_family": HOT[0],
        "work_location": HOT[1],
        "misconduct_category": "attendance_awol",
        "name": "Transit passes",
        "one_time_cost": "0",
        "annual_cost_per_person": "240",
        "people_covered": 300,
        "other_annual_cost": "0",
        "years": 3,
        "reduction_low": "0.10",
        "reduction_expected": "0.25",
        "reduction_high": "0.40",
        "basis": "Interviews on site.",
        "causes": ["commuting"],
        **changes,
    }


SWAP = {
    "name": "Shift-swap scheme",
    "one_time_cost": "15000",
    "annual_cost_per_person": "0",
    "people_covered": 0,
    "years": 5,
    "reduction_low": "0.05",
    "reduction_expected": "0.15",
    "reduction_high": "0.30",
    "causes": ["scheduling", "other"],
    "other_cause": "The first bus arrives after the day shift starts",
}


@pytest.fixture
def kept(county) -> dict:
    """Two proposals for attendance in the flagged unit, and two for the same unit with
    every type of misconduct together."""
    STATE._appraisals = None
    bodies = {
        "passes": body(),
        "swap": body(**SWAP),
        "all_passes": body(misconduct_category=""),
        "all_swap": body(misconduct_category="", **SWAP),
    }
    made = {}
    for key, one in bodies.items():
        response = county.post("/interventions", json=one, headers=ADMIN)
        assert response.status_code == 200, response.text
        made[key] = response.json()
    return made


def test_the_kept_list_sets_proposals_for_one_unit_side_by_side(county, kept):
    """Transit passes: $240 x 300 = $72,000 a year. The shift-swap scheme: $15,000 over 5
    years, $3,000 a year. The discipline costs $79,930.36 a year. A quarter of that, less
    $72,000, is -$52,017.41; 15% of it, less $3,000, is $8,989.55."""
    listed = county.get("/interventions", headers=ADMIN).json()
    by_type, all_types = listed["side_by_side"]
    assert by_type["unit"]["name"] == HOT_NAME and by_type["unit"]["annual_net"] == "79930.36"
    swap, passes = by_type["rows"]
    assert (swap["name"], swap["key"]) == ("Shift-swap scheme", kept["swap"]["id"])
    assert (swap["yearly_cost"], swap["break_even"]) == ("3000.00", "0.0375")
    assert swap["yearly_net_saving_expected"] == "8989.55"
    assert (passes["name"], passes["key"]) == ("Transit passes", kept["passes"]["id"])
    assert (passes["yearly_cost"], passes["break_even"]) == ("72000.00", "0.9008")
    assert passes["yearly_net_saving_expected"] == "-52017.41"
    assert all_types["unit"]["topic"] == "" and len(all_types["rows"]) == 2
    # The figures are the kept appraisals' own, over their years.
    for row, key in ((swap, "swap"), (passes, "passes")):
        expected = kept[key]["appraisal"]["scenarios"][1]["net_saving"]
        assert Decimal(row["yearly_net_saving_expected"]) == (
            Decimal(expected) / row["years"]
        ).quantize(Decimal("0.01"))

    # Withdraw one and there is nothing left to set the other against.
    assert county.delete(f"/interventions/{kept['passes']['id']}", headers=ADMIN).status_code == 200
    after = county.get("/interventions", headers=ADMIN).json()["side_by_side"]
    assert [t["unit"]["topic"] for t in after] == [""]


def test_the_clients_copy_compares_only_what_it_shows_with_its_money(county, kept):
    """By type, both proposals are shown in full, and so set side by side. With every type
    together the unit's cost is withheld from the client's copy, and a comparison would
    give it back, so there is none."""
    who = {"X-WRI-Role": "executive", "X-WRI-Session": "compare-reader"}
    text = pdf_text(county.get("/exports/report.pdf", headers=who).content)
    assert f"Side by side: {HOT_NAME}" in text
    assert "The discipline costs $79,930 a year." in text
    assert "Shift-swap scheme Inflexible scheduling, Other $3,000 3.75% prevented" in text
    assert "$997 $8,990 $20,979" in text
    assert "Transit passes Commuting burden $72,000 90.08% prevented" in text
    assert "Their savings cannot be added" in text
    assert "Side by side: Corrections Officer at Main Detention Center" not in text
    assert text.count("Side by side:") == 1
    assert "first bus" not in text, 'what was typed for "Other" is still not in this copy'

    book = load_workbook(io.BytesIO(county.get("/exports/report.xlsx", headers=who).content))
    rows = sheet_rows(book, "Interventions")
    headings = [row[0] for row in rows if row[0] and str(row[0]).startswith("Side by side")]
    assert headings == [f"Side by side: {HOT_NAME}"]
    swap = next(row for row in rows if row[0] == "Shift-swap scheme" and row[2] == 5)
    assert swap[1] == "Inflexible scheduling, Other"
    assert (swap[3], swap[4], swap[6]) == (3000, 0.0375, 8989.55)
    assert "first bus" not in " ".join(str(v) for row in rows for v in row if v)


def test_the_working_papers_compare_everything_kept(county, kept):
    who = {"X-WRI-Role": "hr_analyst", "X-WRI-Session": "compare-papers"}
    papers = load_workbook(
        io.BytesIO(
            county.get("/exports/report.xlsx", params={"records": "true"}, headers=who).content
        )
    )
    rows = sheet_rows(papers, "Interventions")
    headings = [row[0] for row in rows if row[0] and str(row[0]).startswith("Side by side")]
    assert headings == [
        f"Side by side: {HOT_NAME}",
        "Side by side: Corrections Officer at Main Detention Center",
    ]


def test_a_proposal_that_cannot_pay_for_itself_is_said_so_not_given_a_percentage(county):
    STATE._appraisals = None
    for one in (
        body(),
        body(
            name="On-site childcare",
            one_time_cost="250000",
            annual_cost_per_person="0",
            people_covered=0,
            other_annual_cost="120000",
            years=5,
            causes=["childcare"],
        ),
    ):
        assert county.post("/interventions", json=one, headers=ADMIN).status_code == 200
    who = {"X-WRI-Role": "executive", "X-WRI-Session": "compare-cannot"}
    text = pdf_text(county.get("/exports/report.pdf", headers=who).content)
    assert "On-site childcare Childcare or family-care gaps $170,000 cannot" in text
    book = load_workbook(io.BytesIO(county.get("/exports/report.xlsx", headers=who).content))
    childcare = next(
        row
        for row in sheet_rows(book, "Interventions")
        if row[0] == "On-site childcare" and row[2] == 5
    )
    assert childcare[4] == "cannot"
