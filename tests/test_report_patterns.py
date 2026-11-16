"""The patterns section of the client deliverables.

Phase 2 ends with the pattern put in front of the client's leadership together with what it
costs. What is held here: that the report and the workbook say what the Patterns page says;
that the client's copy carries flagged units only, each under the small-group rules, and
says when one was withheld; that the working behind the flags is in the working papers and
nowhere else; and that including the section changes no other figure in the report.
"""

from __future__ import annotations

import io
import re

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_reporting import pdf_text, sheet_rows
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.generator import generate
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.reporting import build_patterns

HOT = "Attendance / AWOL among Corrections Officer at Main Detention Center"
IDS = r"\bE\d{5}\b|EMP-[0-9A-F]{8}|\bA\d{5}\b"


@pytest.fixture(scope="module")
def county(tmp_path_factory):
    """The whole synthetic county, where the generator's planted pattern can be found. The
    committed sample keeps mostly disciplined employees, so rates in it mean nothing."""
    folder = tmp_path_factory.mktemp("county")
    generate(seed=20260917).write(folder)
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
        str(value)
        for sheet in book.worksheets
        for row in sheet.iter_rows(values_only=True)
        for value in row
        if value is not None
    )


@pytest.fixture(scope="module")
def client_pdf(county) -> str:
    response = county.get("/exports/report.pdf", headers=who("executive", "report-reader"))
    assert response.status_code == 200, response.text
    return pdf_text(response.content)


@pytest.fixture(scope="module")
def client_book(county, client_pdf):
    return book_of(county.get("/exports/report.xlsx", headers=who("executive", "report-reader")))


def test_the_report_calls_out_the_pattern_with_what_it_costs(client_pdf):
    assert "Where discipline clusters" in client_pdf
    assert HOT in client_pdf
    assert "196 comparisons were made. 1 stood out." in client_pdf
    # The cost of those actions from the baseline, and the estimate beside it.
    assert "70 20.6 3.40 $232,626 $164,168" in client_pdf
    # What a flag is and is not, said to the reader of the report.
    assert "It does not say why the rate is higher" in client_pdf
    assert "it says nothing about any one person" in client_pdf
    assert "It is an estimate, and it is not added to any total." in client_pdf
    # How it was worked out, in sentences that can be checked against the table.
    assert "70 Attendance / AWOL actions among 64 people" in client_pdf
    assert "The period is 2023-10-20 to 2026-09-17." in client_pdf


def test_an_unreviewed_threshold_would_be_said_in_the_report(client_pdf, client_book):
    """Rule 7: a threshold the owner has not reviewed is a starting value, and a deliverable
    that used one says so. He reviewed both on 2026-11-16, so today's report does not."""
    said = "starting values and are still under review"
    assert said not in client_pdf
    assert said not in all_text(client_book)

    from wri_engine.reporting.pdf import _pattern_settings

    reviewed = build_patterns([view("flagged_only")])
    pending = build_patterns(
        [
            view(
                "flagged_only",
                settings={
                    "detection_false_discovery_rate": 0.05,
                    "detection_min_rate_ratio": 1.5,
                    "unreviewed": ["detection_min_rate_ratio"],
                },
            )
        ]
    )
    assert said not in _pattern_settings(reviewed)
    assert said in _pattern_settings(pending)


def test_the_report_says_what_the_patterns_page_says(county, client_book):
    """The same reader, the same figures, on screen and in the workbook."""
    on_screen = county.get("/detection/hotspots", headers=who("executive", "report-reader")).json()[
        "flagged"
    ]
    assert len(on_screen) == 1
    shown = on_screen[0]
    header, *rows = sheet_rows(client_book, "Patterns")
    found = [dict(zip(header, row, strict=True)) for row in rows if row[4] == "Flagged"]
    assert len(found) == 1
    row = found[0]
    assert row["View"] == "By type of misconduct"
    assert (row["Type of misconduct"], row["Employee type"], row["Work location"]) == (
        shown["topic_label"],
        shown["row_label"],
        shown["col_label"],
    )
    assert (row["Actions"], row["People"]) == (shown["actions"], shown["people"]) == (70, 64)
    assert str(row["Cost of these actions"]) == shown["net"] == "232625.91"
    assert str(row["Estimated excess (not in any total)"]) == shown["estimated_excess_cost"]
    assert row["How it was worked out"].startswith(shown["explanation"])


def test_the_clients_copy_holds_flagged_units_and_nothing_else(client_pdf, client_book):
    assert "Patterns" in client_book.sheetnames
    assert "Patterns working" not in client_book.sheetnames
    for text in (client_pdf, all_text(client_book)):
        assert "Not flagged" not in text and "Not assessed" not in text
        assert not re.search(IDS, text)
    results = {row[4] for row in sheet_rows(client_book, "Patterns")[1:] if row[4]}
    assert results == {"Flagged"}


def test_a_withheld_pattern_is_counted_and_its_figures_are_not_given(client_pdf, client_book):
    """Every type together, the same unit stands out: 160 actions among 126 officers. Beside
    the report's own matrix of job against type of misconduct, that group sits a few people
    away from groups already shown, so it is left out of the client's copy, and the copy
    says one was."""
    assert "Every type of misconduct together 29 comparisons were made." in client_pdf
    assert "1 more stood out and is not shown" in client_pdf
    assert "would let a group of fewer than 5 people be worked out" in client_pdf
    book = all_text(client_book)
    assert "1 more stood out and not shown here" in book
    for text in (client_pdf, book):
        for figure in ("1,072,052", "1072051.96", "554,786", "554786.50"):
            assert figure not in text


def test_the_working_papers_carry_the_working(county):
    papers = book_of(
        county.get(
            "/exports/report.xlsx",
            params={"records": "true"},
            headers=who("hr_analyst", "report-analyst"),
        )
    )
    assert papers.sheetnames[-1] == "Patterns working"
    full = {
        by: county.get(
            "/detection/hotspots", params={"by": by}, headers=who("hr_analyst", "report-analyst")
        ).json()
        for by in ("misconduct", "overall")
    }
    assessed = sum(len(view[key]) for view in full.values() for key in ("flagged", "not_flagged"))
    header, *rows = sheet_rows(papers, "Patterns working")
    results = [row[4] for row in rows if row[0] and row[5] is not None]
    assert len(results) == assessed == 160
    assert results.count("Flagged") == 2 and results.count("Not flagged") == 158
    assert any(row[0] == "Not assessed" for row in rows)
    # Nothing is withheld from a role that sees every record: both views' flags are listed.
    flagged = [row for row in sheet_rows(papers, "Patterns")[1:] if row[4] == "Flagged"]
    assert {row[0] for row in flagged} == {
        "By type of misconduct",
        "Every type of misconduct together",
    }
    assert "not shown" not in all_text(papers)
    # A reader of totals cannot have them.
    refused = county.get(
        "/exports/report.xlsx",
        params={"records": "true"},
        headers=who("executive", "report-reader"),
    )
    assert refused.status_code == 403


def test_including_patterns_changes_no_other_figure(county, client_book):
    """Detection reads the baseline. The estimate is in no total."""
    without = book_of(
        county.get(
            "/exports/report.xlsx",
            params={"patterns": "false"},
            headers=who("executive", "report-reader"),
        )
    )
    assert "Patterns" not in without.sheetnames
    assert [n for n in client_book.sheetnames if n != "Patterns"] == without.sheetnames
    for name in without.sheetnames:
        assert sheet_rows(client_book, name) == sheet_rows(without, name), name
    pdf = county.get(
        "/exports/report.pdf",
        params={"patterns": "false"},
        headers=who("executive", "report-reader"),
    )
    assert "Where discipline clusters" not in pdf_text(pdf.content)


def test_asking_twice_gives_the_same_report(county, client_pdf):
    again = county.get("/exports/report.pdf", headers=who("executive", "report-reader"))
    assert pdf_text(again.content) == client_pdf


# ---------------------------------------------------------------------------
# the content model, on its own
# ---------------------------------------------------------------------------
def finding(status: str = "flagged", **changes) -> dict:
    return {
        "status": status,
        "topic_label": "Tardiness",
        "row_label": "Caseworker",
        "col_label": "North Office",
        "actions": 12,
        "people": 10,
        "employee_years": 100.0,
        "comparison_actions": 6,
        "comparison_people": 6,
        "comparison_employee_years": 300.0,
        "expected_actions": 2.0,
        "ratio": 6.0,
        "q_value": 0.001,
        "net": "12000.00",
        "estimated_excess_cost": "10000.00" if status == "flagged" else None,
        "explanation": "12 Tardiness actions among 10 people.",
        "formula": "10.0 actions more than expected x $1,000.00" if status == "flagged" else "",
        **changes,
    }


def view(detail: str, **parts) -> dict:
    return {
        "by": "misconduct",
        "detail": detail,
        "window_start": "2024-01-01",
        "window_end": "2025-12-31",
        "comparisons": 8,
        "min_people": 5,
        "withheld": 0,
        "settings": {
            "detection_false_discovery_rate": 0.05,
            "detection_min_rate_ratio": 1.5,
            "unreviewed": [],
        },
        "flagged": [finding()],
        **parts,
    }


def test_a_unit_that_was_not_flagged_cannot_reach_the_clients_copy():
    """Even handed the working, a section built for a reader of totals does not carry it."""
    leaky = view("flagged_only", not_flagged=[finding("not_flagged")])
    section = build_patterns([leaky])
    assert [row.status for row in section.flagged] == ["flagged"]
    assert section.views[0].working == []
    assert section.flagged[0].name == "Tardiness among Caseworker at North Office"
    assert section.unreviewed == ()

    full = build_patterns([view("full", not_flagged=[finding("not_flagged")])])
    assert [row.status for row in full.views[0].working] == ["flagged", "not_flagged"]
    assert build_patterns([]) is None


def test_a_unit_with_every_type_together_is_named_without_a_type():
    section = build_patterns([view("flagged_only", flagged=[finding(topic_label="")])])
    assert section.flagged[0].name == "Caseworker at North Office"
