"""Rising units in the client deliverables.

What is held here: that the report and the workbook carry the units drift flags as rising,
under the same rules as the flagged patterns beside them; that a client's copy says when one
was held back, carries none of its figures, and never carries the working; that the working
papers carry everything; that a rising unit, when shown, is laid out with what it cost in
each half; and that nothing here changes another figure in the report.

The synthetic county plants a level and no rise, so these tests plant one: three in four of
one unit's attendance actions in the earlier half are taken out of every file the client
"sent", exactly as a thinner early record would look.
"""

from __future__ import annotations

import csv
import dataclasses
import io
import re
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_reporting import pdf_text, sheet_rows
from wri_engine.access.roles import Role
from wri_engine.api.context import Ctx
from wri_engine.api.main import _drift_report, _report_content, app
from wri_engine.api.state import STATE
from wri_engine.generator import generate
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.reporting import build_rising, render_pdf

HOT = ("corrections_officer", "Main Detention Center", "attendance_awol")
HOT_NAME = "Attendance / AWOL among Corrections Officer at Main Detention Center"
IDS = r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b"


def unit_of(action) -> tuple[str, str, str]:
    d = action.dimensions
    return (d["role_family"], d["work_location"], d["misconduct_category"])


@pytest.fixture(scope="module")
def county(tmp_path_factory):
    folder = tmp_path_factory.mktemp("county")
    generate(seed=20260917).write(folder)
    STATE.load(folder)
    run = STATE.run(mode="base")
    later_starts = _drift_report(STATE, run, "misconduct").later_start
    dropped, seen = set(), 0
    for action in run.action_costs:
        early = date.fromisoformat(str(action.dimensions["decision_date"])) < later_starts
        if unit_of(action) == HOT and early:
            seen += 1
            if seen % 4:
                dropped.add(action.action_id)
    assert len(dropped) == 28
    for path in folder.glob("*.csv"):
        rows = list(csv.reader(path.open(newline="")))
        if "ACTN_NBR" in rows[0]:
            at = rows[0].index("ACTN_NBR")
            with path.open("w", newline="") as handle:
                csv.writer(handle).writerows(
                    [rows[0], *(r for r in rows[1:] if r[at] not in dropped)]
                )
    STATE.load(folder)
    yield TestClient(app)
    STATE.load(SYNTHETIC_DIR / "sample")


def who(role: str, session: str) -> dict:
    return {"X-WRI-Role": role, "X-WRI-Session": session}


def book_of(response):
    assert response.status_code == 200, response.text
    return load_workbook(io.BytesIO(response.content))


def all_text(book) -> str:
    return " ".join(
        str(v)
        for sheet in book.worksheets
        for row in sheet.iter_rows(values_only=True)
        for v in row
        if v is not None
    )


def test_the_planted_rise_is_found(county):
    found = county.get("/detection/drift", headers=who("admin", "rise-lead")).json()
    assert [(f["row"], f["col"], f["topic"]) for f in found["flagged"]] == [HOT]
    hot = found["flagged"][0]
    assert (hot["earlier_actions"], hot["later_actions"]) == (9, 33)
    assert (hot["own_change"], hot["ratio"]) == (3.91, 4.46)


def test_the_clients_copy_shows_the_rise_and_withholds_only_the_comparison(county):
    """The rest of the organization's earlier half is fifty people a few people away from
    small cells of the report's own cost matrix, which is printed first, so its figures may
    not be released. The unit's own may. The client's copy shows the rising unit with what
    it cost in each half, marks the comparison figures withheld, and says why (owner, on
    2026-11-16: "make it the most transparent way")."""
    reader = who("executive", "rise-reader")
    text = pdf_text(county.get("/exports/report.pdf", headers=reader).content)
    assert "Rising over the period" in text
    assert "Earlier half: 2023-11-12 to 2025-04-14. Later half: 2025-04-15 to 2026-09-17." in text
    assert "By type of misconduct 21 comparisons were made. 1 rose." in text
    assert f"{HOT_NAME} 9 then 33 3.91 withheld $29,523 then $151,319 withheld" in text
    assert "It rose by at least 1.5 times as much as the same type did in the rest of the" in text
    assert "The figures for the rest of the organization are not shown here" in text
    assert "1 more rose" not in text
    book = book_of(county.get("/exports/report.xlsx", headers=reader))
    assert "Rising" in book.sheetnames and "Rising working" not in book.sheetnames
    header, *rows = sheet_rows(book, "Rising")
    (hot,) = [dict(zip(header, r, strict=True)) for r in rows if r[4] == "Rising"]
    assert (hot["Actions, earlier half"], hot["Actions, later half"]) == (9, 33)
    assert Decimal(str(hot["Cost, later half"])) == Decimal("151319.31")
    for column in (
        "Actions elsewhere, earlier half",
        "Actions elsewhere, later half",
        "Times the earlier rate elsewhere",
        "Times the rise elsewhere",
        "Adjusted chance value",
        "Estimated excess (not in any total)",
    ):
        assert hot[column] == "withheld", column
    cells = all_text(book)
    for copy in (text, cells):
        # Nothing about the rest of the organization: its counts, its change, the ratio.
        for figure in ("50 to 41", "0.88", "4.46", "117,409", "117409"):
            assert figure not in copy
        assert not re.search(IDS, copy)
    assert "Not flagged" not in " ".join(str(r[4]) for r in rows if r[4])


def test_a_reader_who_has_seen_nothing_else_is_shown_it(county):
    """The same rule, against a fresh record: with no matrix seen first, it is released."""
    fresh = county.get("/detection/drift", headers=who("executive", "rise-fresh")).json()
    assert fresh["detail"] == "flagged_only" and fresh["withheld"] == 0
    assert [f["topic"] for f in fresh["flagged"]] == ["attendance_awol"]


def test_the_working_papers_carry_everything(county):
    papers = book_of(
        county.get(
            "/exports/report.xlsx",
            params={"records": "true"},
            headers=who("hr_analyst", "rise-papers"),
        )
    )
    assert "Rising" in papers.sheetnames and "Rising working" in papers.sheetnames
    header, *rows = sheet_rows(papers, "Rising")
    shown = [dict(zip(header, r, strict=True)) for r in rows if r[4] == "Rising"]
    assert len(shown) == 1
    hot = shown[0]
    assert (hot["Type of misconduct"], hot["Employee type"], hot["Work location"]) == (
        "Attendance / AWOL",
        "Corrections Officer",
        "Main Detention Center",
    )
    assert (hot["Actions, earlier half"], hot["Actions, later half"]) == (9, 33)
    # Paired with the baseline: the later half's cost is the sum of those very actions.
    run = STATE.run(mode="base")
    later_starts = _drift_report(STATE, run, "misconduct").later_start
    later = sum(
        a.net
        for a in run.action_costs
        if unit_of(a) == HOT
        and date.fromisoformat(str(a.dimensions["decision_date"])) >= later_starts
    )
    assert Decimal(str(hot["Cost, later half"])) == later == Decimal("151319.31")
    assert Decimal(str(hot["Estimated excess (not in any total)"])) == Decimal("117387.01")
    header, *rows = sheet_rows(papers, "Rising working")
    results = [r[4] for r in rows if r[0] and r[5] is not None]
    assert results.count("Rising") == 1 and results.count("Not flagged") == len(results) - 1
    assert any(r[0] == "Not assessed" for r in rows)


def test_a_rising_unit_when_shown_is_laid_out_with_its_cost_in_each_half(county):
    """The layout, through the same page code, with the rising unit in the section."""
    content = _report_content(
        Ctx(role=Role.ADMIN, state=STATE, session_key="layout"), "base", records=True
    )
    text = pdf_text(render_pdf(dataclasses.replace(content, records=None)))
    assert "By type of misconduct 21 comparisons were made. 1 rose." in text
    assert f"{HOT_NAME} 9 then 33 3.91 4.46 $29,523 then $151,319 $117,387" in text
    assert "So this unit's rate rose 4.46 times as much as the rest's." in text
    assert "Estimated excess: 25.6 later actions more than expected x $4,585.43" in text
    assert "The cost in each half is what the records show those actions cost" in text


def test_leaving_patterns_out_leaves_rising_out_and_changes_nothing_else(county):
    reader = who("executive", "rise-without")
    with_it = book_of(county.get("/exports/report.xlsx", headers=reader))
    without = book_of(
        county.get("/exports/report.xlsx", params={"patterns": "false"}, headers=reader)
    )
    assert "Rising" not in without.sheetnames
    for name in without.sheetnames:
        assert sheet_rows(with_it, name) == sheet_rows(without, name), name


def test_a_unit_that_was_not_flagged_cannot_reach_the_clients_copy():
    """Handed the working anyway, a section built for a reader of totals does not carry it."""
    row = {
        "status": "not_flagged",
        "topic_label": "",
        "row_label": "Caseworker",
        "col_label": "North Office",
        "earlier_actions": 4,
        "earlier_people": 4,
        "later_actions": 6,
        "later_people": 6,
        "own_change": 1.5,
        "elsewhere_earlier_actions": 10,
        "elsewhere_later_actions": 10,
        "elsewhere_change": 1.0,
        "ratio": 1.5,
        "q_value": 0.9,
        "earlier_net": "4000.00",
        "later_net": "6000.00",
        "estimated_excess_cost": None,
        "explanation": "Not flagged.",
        "formula": "",
    }
    view = {
        "by": "overall",
        "detail": "flagged_only",
        "earlier_start": "2024-01-01",
        "earlier_end": "2024-12-31",
        "later_start": "2025-01-01",
        "later_end": "2025-12-31",
        "comparisons": 2,
        "withheld": 0,
        "flagged": [],
        "not_flagged": [row],
    }
    section = build_rising([view])
    assert section.flagged == [] and section.views[0].working == []
    full = build_rising([{**view, "detail": "full"}])
    assert [r.status for r in full.views[0].working] == ["not_flagged"]
    assert full.views[0].working[0].own_change == Decimal("1.5")
    assert build_rising([]) is None
