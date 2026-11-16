"""The interventions section of the client deliverables.

Phase 3 comes to this: management is handed what was found in a unit, what is proposed, what
that would cost and what it would save. What is held here: that the report says what the
Interventions page says, to the cent; that the client's copy names a cause by its label and
never carries the words typed for "Other"; that a finding about fewer people than the
minimum group is left out and counted; that a unit's cost which may not be released keeps
its finding and loses its money; that the working papers carry all of it; and that a saving
adds to no total.
"""

from __future__ import annotations

import io
import itertools
import re
from collections import defaultdict
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_reporting import pdf_text, sheet_rows
from wri_engine.aggregation.rollups import window_years
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.generator import generate
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.reporting import build_interventions
from wri_engine.reporting.pdf import money

HOT = ("corrections_officer", "Main Detention Center")
HOT_NAME = "Attendance / AWOL among Corrections Officer at Main Detention Center"
WORDS = "The first bus arrives after the day shift starts"
IDS = r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b"
ADMIN = {"X-WRI-Role": "admin", "X-WRI-Session": "interventions-lead"}
_sessions = itertools.count(1)


@pytest.fixture(scope="module")
def county(tmp_path_factory):
    folder = tmp_path_factory.mktemp("county")
    generate(seed=20260917).write(folder)
    STATE.load(folder)
    yield TestClient(app)
    STATE._appraisals = None
    STATE.load(SYNTHETIC_DIR / "sample")


def proposal(**changes) -> dict:
    return {
        "role_family": HOT[0],
        "work_location": HOT[1],
        "misconduct_category": "",
        "name": "Supervisor training",
        "one_time_cost": "15000",
        "annual_cost_per_person": "50",
        "people_covered": 300,
        "other_annual_cost": "0",
        "years": 3,
        "reduction_low": "0.10",
        "reduction_expected": "0.25",
        "reduction_high": "0.40",
        "basis": "Interviews on site in October, and the pilot at the annex.",
        "causes": ["training"],
        **changes,
    }


def small_unit() -> tuple[str, str]:
    """A job at a location where fewer than five people were disciplined."""
    people = defaultdict(set)
    for action in STATE.run(mode="base").action_costs:
        d = action.dimensions
        people[(d["role_family"], d["work_location"])].add(action.employee_id)
    return next(unit for unit, who in sorted(people.items()) if len(who) < 5)


@pytest.fixture
def kept(county) -> dict:
    """Three appraisals kept: one for the flagged unit by type, with "Other" and its words;
    one for the same unit with every type together; one for a unit of four people."""
    STATE._appraisals = None
    job, place = small_unit()
    bodies = {
        "by_type": proposal(
            misconduct_category="attendance_awol",
            name="Shift-swap scheme and transit passes",
            causes=["commuting", "other"],
            other_cause=WORDS,
        ),
        "all_types": proposal(),
        "small": proposal(
            role_family=job, work_location=place, name="Flexible start", causes=["scheduling"]
        ),
    }
    made = {}
    for key, body in bodies.items():
        response = county.post("/interventions", json=body, headers=ADMIN)
        assert response.status_code == 200, response.text
        made[key] = response.json()
    return made


def reader() -> dict:
    """A client reader nobody has shown anything to yet."""
    return {"X-WRI-Role": "executive", "X-WRI-Session": f"interventions-reader-{next(_sessions)}"}


def book_of(response):
    assert response.status_code == 200, response.text
    return load_workbook(io.BytesIO(response.content))


def all_text(book) -> str:
    return " ".join(
        str(value)
        for sheet in book.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if value is not None
    )


def client_copy(county) -> tuple[str, object]:
    who = reader()
    pdf = county.get("/exports/report.pdf", headers=who)
    assert pdf.status_code == 200, pdf.text
    return pdf_text(pdf.content), book_of(county.get("/exports/report.xlsx", headers=who))


# ---------------------------------------------------------------------------
# the client's copy
# ---------------------------------------------------------------------------
def test_the_report_gives_management_the_cost_benefit(county, kept):
    """It costs X a year; the intervention costs Y; it saves Z, three ways."""
    text, _book = client_copy(county)
    assert "What was found, and what could be done" in text
    assert f"Shift-swap scheme and transit passes: {HOT_NAME}" in text
    assert "Found on site: Commuting burden, Other." in text
    assert "The finding of Walkthrough user (admin), entered" in text
    # 15,000 + 50 x 300 x 3 = 60,000, against 79,940 a year.
    assert "$79,940 $60,000 -$36,018 -$45 $35,928" in text
    assert (
        "It pays for itself if it prevents 25.02% of these actions: $60,000.00 / "
        "($79,940.18 a year x 3 years)." in text
    )
    assert "Based on: Interviews on site in October, and the pilot at the annex." in text
    # Whose finding, and what a saving is, said to the reader.
    assert "This product did not find it and does not check it." in text
    assert "A saving is a projection: it is not part of any total in this report." in text
    assert "Only the cost of discipline is counted as a benefit." in text
    assert not re.search(IDS, text)


def test_the_report_says_what_the_interventions_page_says(county, kept):
    """The workbook's figures are the ones the lead sees on screen, to the cent."""
    _text, book = client_copy(county)
    on_screen = kept["by_type"]["appraisal"]
    header, *rows = sheet_rows(book, "Interventions")
    row = next(dict(zip(header, r, strict=True)) for r in rows if r[0] == HOT_NAME)
    assert str(row["The discipline costs, a year"]) == on_screen["unit"]["annual_net"] == "79940.18"
    assert str(row["The intervention costs"]) + ".00" == on_screen["intervention"]["total_cost"]
    for label, scenario in zip(("low", "expected", "high"), on_screen["scenarios"], strict=True):
        assert Decimal(str(row[f"Net saving: {label}"])) == Decimal(scenario["net_saving"])
        assert Decimal(str(row[f"Prevented: {label}"])) == Decimal(scenario["reduction"])
    assert row["Pays for itself when"] == on_screen["break_even_note"]
    assert row["Found on site"] == "Commuting burden, Other"
    assert row["Proposed"] == "Shift-swap scheme and transit passes"
    assert on_screen["unit"]["formula"] in row["How the costs were worked out"]


def test_the_clients_copy_never_carries_what_was_typed_for_other(county, kept):
    """Decided by the owner on 2026-11-16: the client's copy shows "Other" and no more."""
    assert kept["by_type"]["finding"]["other_cause"] == WORDS
    text, book = client_copy(county)
    cells = all_text(book)
    for copy in (text, cells):
        assert "Commuting burden, Other" in copy
        assert WORDS not in copy and "first bus" not in copy
    assert "Interventions working" not in book.sheetnames
    assert "Other: what was said" not in cells
    # Whoever asks: the lead's ordinary copy is the client's copy too.
    lead = pdf_text(county.get("/exports/report.pdf", headers=ADMIN).content)
    assert "Found on site: Commuting burden, Other." in lead and "first bus" not in lead


def test_a_finding_about_fewer_people_than_the_minimum_is_left_out_and_counted(county, kept):
    """A cause said of four people is said of them. Neither the unit, nor what was found
    there, nor what was proposed for it reaches the client's copy."""
    job, place = small_unit()
    text, book = client_copy(county)
    assert "1 more was looked into and is not shown here: it concerns fewer than 5 people." in text
    section = text[text.index("What was found, and what could be done") :]
    section = section[: section.index("Cost per employee per year")]
    assert "Flexible start" not in section and "Inflexible scheduling" not in section
    assert place not in section
    rows = sheet_rows(book, "Interventions")
    assert not any(place in str(row[0]) for row in rows[1:] if row[0])
    assert "1 more looked into and not shown here" in all_text(book)


def test_a_unit_cost_that_may_not_be_released_keeps_its_finding_and_loses_its_money(county, kept):
    """Every type together, the unit is all 126 disciplined officers there, a few people
    away from groups the report's own matrix has just shown. The finding and the proposal
    are for management to read; the unit's cost, and so any saving, is withheld."""
    text, book = client_copy(county)
    assert "Supervisor training: Corrections Officer at Main Detention Center" in text
    assert "Found on site: Missing training or education." in text
    assert "The intervention would cost $60,000 over 3 years." in text
    assert "What the discipline costs in this unit is not shown" in text
    assert "So no saving is shown either." in text
    on_screen = kept["all_types"]["appraisal"]
    yearly = Decimal(on_screen["unit"]["annual_net"])
    cells = all_text(book)
    for figure in (money(yearly), on_screen["unit"]["annual_net"], on_screen["unit"]["net"]):
        assert figure not in text and figure not in cells
    for scenario in on_screen["scenarios"]:
        assert money(Decimal(scenario["net_saving"])) not in text
        assert scenario["net_saving"] not in cells
    header, *rows = sheet_rows(book, "Interventions")
    row = next(
        dict(zip(header, r, strict=True))
        for r in rows
        if r[0] == "Corrections Officer at Main Detention Center"
    )
    assert row["The discipline costs, a year"] is None and row["Net saving: expected"] is None
    assert row["The intervention costs"] == 60000
    assert "is not shown" in row["Pays for itself when"]


def test_a_saving_adds_to_no_total(county, kept):
    """With the section and without it, every other sheet is the same."""
    who = reader()
    with_it = book_of(county.get("/exports/report.xlsx", headers=who))
    without = book_of(
        county.get("/exports/report.xlsx", params={"interventions": "false"}, headers=who)
    )
    assert "Interventions" in with_it.sheetnames and "Interventions" not in without.sheetnames
    assert [n for n in with_it.sheetnames if n != "Interventions"] == without.sheetnames
    for name in without.sheetnames:
        assert sheet_rows(with_it, name) == sheet_rows(without, name), name
    pdf = county.get("/exports/report.pdf", params={"interventions": "false"}, headers=who)
    assert "What was found, and what could be done" not in pdf_text(pdf.content)


def test_with_nothing_kept_the_report_has_no_such_section(county):
    STATE._appraisals = None
    text, book = client_copy(county)
    assert "What was found, and what could be done" not in text
    assert "Interventions" not in book.sheetnames
    assert "Where discipline clusters" in text, "the patterns section is still there"


# ---------------------------------------------------------------------------
# the working papers
# ---------------------------------------------------------------------------
def working_papers(county, role: str = "hr_analyst"):
    who = {"X-WRI-Role": role, "X-WRI-Session": f"interventions-papers-{next(_sessions)}"}
    return county.get("/exports/report.xlsx", params={"records": "true"}, headers=who)


def test_the_working_papers_carry_all_of_it(county, kept):
    job, place = small_unit()
    papers = book_of(working_papers(county))
    assert papers.sheetnames[-1] == "Interventions working"
    header, *rows = sheet_rows(papers, "Interventions working")
    working = [dict(zip(header, r, strict=True)) for r in rows if r[0] and r[5]]
    assert len(working) == 3 and {w["Standing"] for w in working} == {"Kept"}
    assert [w["Other: what was said"] for w in working] == [WORDS, None, None]
    assert any(place in w["Where"] for w in working)
    # Nothing is withheld from a role that sees every record: all three, with their money.
    header, *rows = sheet_rows(papers, "Interventions")
    shown = [dict(zip(header, r, strict=True)) for r in rows if r[0] and r[4]]
    assert len(shown) == 3
    for row, key in zip(shown, ("by_type", "all_types", "small"), strict=True):
        assert Decimal(str(row["The discipline costs, a year"])) == Decimal(
            kept[key]["appraisal"]["unit"]["annual_net"]
        )
    assert "not shown" not in " ".join(str(v) for r in rows for v in r if v)
    assert not re.search(
        IDS, " ".join(str(v) for r in sheet_rows(papers, "Interventions working") for v in r if v)
    )
    # A reader of totals cannot have them.
    assert working_papers(county, "executive").status_code == 403


def test_a_withdrawn_appraisal_leaves_the_clients_copy_and_stays_in_the_working_papers(
    county, kept
):
    gone = kept["by_type"]["id"]
    assert county.delete(f"/interventions/{gone}", headers=ADMIN).status_code == 200
    text, book = client_copy(county)
    assert "Shift-swap scheme and transit passes" not in text
    assert "Shift-swap scheme and transit passes" not in all_text(book)
    assert "Supervisor training" in text
    header, *rows = sheet_rows(book_of(working_papers(county)), "Interventions working")
    working = {r[5]: dict(zip(header, r, strict=True)) for r in rows if r[0] and r[5]}
    withdrawn = working["Shift-swap scheme and transit passes"]
    assert withdrawn["Standing"] == "Withdrawn"
    assert withdrawn["Withdrawn by"] == "Walkthrough user (admin)"
    assert re.fullmatch(r"\d{4}-\d\d-\d\d", withdrawn["Withdrawn on"])
    assert withdrawn["Other: what was said"] == WORDS, "the record of it stays"
    assert working["Supervisor training"]["Standing"] == "Kept"


def test_words_a_person_typed_stay_words(county):
    """A name or a basis that looks like a formula or like markup is neither run nor read
    as layout."""
    STATE._appraisals = None
    body = proposal(
        misconduct_category="attendance_awol",
        name='=HYPERLINK("http://example.invalid","Training")',
        basis="<b>Interviews</b> & a <font color='red'>pilot</font>",
    )
    assert county.post("/interventions", json=body, headers=ADMIN).status_code == 200
    who = reader()
    text = pdf_text(county.get("/exports/report.pdf", headers=who).content)
    assert "Based on: <b>Interviews</b> & a <font color='red'>pilot</font>" in text
    book = book_of(county.get("/exports/report.xlsx", headers=who))
    for row in book["Interventions"].iter_rows():
        for cell in row:
            if isinstance(cell.value, str):
                assert cell.data_type == "s", cell.coordinate
    assert book["Interventions"]["E2"].value.startswith("=HYPERLINK")


# ---------------------------------------------------------------------------
# the content model, on its own
# ---------------------------------------------------------------------------
def entry(**changes) -> dict:
    return {
        "unit_name": "Tardiness among Caseworker at North Office",
        "finding": {
            "causes": [
                {"id": "scheduling", "label": "Inflexible scheduling"},
                {"id": "other", "label": "Other"},
            ],
            "other_cause": "Something a person typed",
            "entered_by": "Dana Lead (lead@firm.example)",
            "entered_at": "2026-11-16T15:00:00+00:00",
        },
        "appraisal": None,
        "intervention": {
            "name": "Flexible start times",
            "basis": "The site visit.",
            "years": 2,
            "total_cost": "9000.00",
            "cost_formula": "$3,000.00 one-time + ...",
        },
        "withdrawn_by": "Sam Analyst",
        "withdrawn_at": "2026-11-17T09:00:00+00:00",
        **changes,
    }


def test_the_words_for_other_cannot_reach_a_copy_that_is_not_the_working_papers():
    """Handed the words anyway, a section built for the client does not carry them."""
    section = build_interventions([entry()], not_shown=0, min_people=5, assumes=("a",))
    row = section.rows[0]
    assert row.other_cause == "" and row.found == "Inflexible scheduling, Other"
    assert (row.withdrawn_by, row.withdrawn_on) == ("", ""), "nor who withdrew what"
    assert row.entered_on == "2026-11-16" and row.annual_net is None and row.savings == ()
    assert "fewer than 5 people" in section.money_withheld

    papers = build_interventions([entry()], not_shown=0, min_people=5, assumes=("a",), working=True)
    assert papers.rows[0].other_cause == "Something a person typed"
    assert papers.rows[0].withdrawn_on == "2026-11-17" and papers.current == []

    assert build_interventions([], not_shown=0, min_people=5, assumes=()) is None
    only_counted = build_interventions([], not_shown=2, min_people=5, assumes=())
    assert only_counted.rows == [] and only_counted.not_shown == 2


def test_the_figure_in_the_report_is_the_baselines(county, kept):
    """X in the report is the net cost of that unit's actions in the costing run, over the
    length of the period: nothing in between."""
    run = STATE.run(mode="base")
    theirs = [
        a
        for a in run.action_costs
        if (a.dimensions["role_family"], a.dimensions["work_location"]) == HOT
        and a.dimensions["misconduct_category"] == "attendance_awol"
    ]
    yearly = sum(a.net for a in theirs) / window_years(run)
    text, _book = client_copy(county)
    assert f"{money(yearly)} $60,000" in text
