"""Client deliverables: the report, the workbook beside it, and the firm's name and logo.

The claims worth holding a deliverable to are that it says what the screen says, that it
shows a client reader nothing the screen would not, that the PDF and the workbook agree, and
that nothing a person typed can be run as a formula or break the layout.
"""

from __future__ import annotations

import io
import re
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from PIL import Image
from pypdf import PdfReader

from test_accounts_engagements import (
    ANALYST,
    CLIENT,
    LEAD,
    OUTSIDER,
    OWNER,
    Installation,
    override,
)
from test_template_intake import ACTION, EMPLOYEE, write
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.reporting.branding import MAX_LOGO_BYTES, check_logo
from wri_engine.reporting.pdf import money

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def pdf_text(data: bytes) -> str:
    """The report's words in reading order, with line breaks folded away: where a sentence
    wraps in a table cell is layout, not content."""
    pages = (page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    return " ".join(" ".join(pages).split())


def png(width: int = 320, height: int = 120, color=(36, 59, 83)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return buffer.getvalue()


def sheet_rows(book, name: str) -> list[tuple]:
    return [row for row in book[name].iter_rows(values_only=True)]


# ---------------------------------------------------------------------------
# the synthetic walkthrough: no firm, no engagement
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def demo():
    STATE.load(SYNTHETIC_DIR / "sample")
    return TestClient(app)


def role(name: str) -> dict:
    return {"X-WRI-Role": name}


@pytest.fixture(scope="module")
def demo_summary(demo) -> dict:
    return demo.get("/costs/summary", headers=role("executive")).json()


@pytest.fixture(scope="module")
def demo_pdf(demo) -> bytes:
    response = demo.get("/exports/report.pdf", headers=role("executive"))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/pdf"
    assert (
        'filename="harlow_county_cost_of_discipline.pdf"'
        in (response.headers["content-disposition"])
    )
    return response.content


@pytest.fixture(scope="module")
def demo_book(demo):
    response = demo.get("/exports/report.xlsx", headers=role("executive"))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == XLSX
    return load_workbook(io.BytesIO(response.content))


def test_the_report_says_what_the_screen_says(demo_pdf, demo_summary):
    text = pdf_text(demo_pdf)
    assert demo_pdf.startswith(b"%PDF")
    assert "The Cost of Discipline" in text and "Harlow County" in text
    for key in ("annual_net", "net_per_fte_per_year", "total_net", "total_gross"):
        assert money(Decimal(demo_summary[key])) in text, key
    assert f"{demo_summary['actions_costed']:,} disciplinary actions were costed" in text
    # With no firm set, the report carries the product's name rather than a blank.
    assert "Workforce Risk Intelligence" in text
    assert "Synthetic data for a fictional county" in text
    for heading in (
        "Where the money goes",
        "How far the figure moves with the assumptions",
        "Where the cost concentrates",
        "What the records could and could not support",
        "Appendix A. The assumptions behind these figures",
        "Appendix B. Values specific to this client",
    ):
        assert heading in text, heading


def test_the_report_shows_the_range_as_well_as_the_point(demo, demo_pdf):
    text = pdf_text(demo_pdf)
    for mode in ("low", "base", "high"):
        net = demo.get("/costs/summary", params={"mode": mode}, headers=role("executive")).json()
        assert money(Decimal(net["total_net"])) in text, mode


def test_a_report_never_names_a_person_or_an_action(demo_pdf, demo_book):
    """The data-quality section is counts by rule. No employee id and no action id reaches a
    deliverable, whoever asks for it."""
    ids = re.compile(r"\b(E\d{5}|A\d{5,}|EMP[-_ ]?\d+)\b")
    assert not ids.search(pdf_text(demo_pdf))
    for sheet in demo_book.worksheets:
        for row in sheet.iter_rows(values_only=True):
            for value in row:
                assert not (isinstance(value, str) and ids.search(value)), (sheet.title, value)


def test_the_workbook_has_every_sheet_and_the_same_figures(demo_book, demo_summary, demo_pdf):
    assert demo_book.sheetnames == [
        "Summary",
        "Components",
        "Low to high",
        "Cost matrix",
        "Departments",
        "Data quality",
        "Assumptions used",
        "Client values",
    ]
    summary = {row[0]: row[1] for row in sheet_rows(demo_book, "Summary") if row[0]}
    net = Decimal(demo_summary["total_net"])
    assert Decimal(str(summary["Net cost"])) == net
    assert Decimal(str(summary["Gross cost"])) == Decimal(demo_summary["total_gross"])
    assert Decimal(str(summary["Net cost per year"])) == Decimal(demo_summary["annual_net"])
    assert summary["Actions costed"] == demo_summary["actions_costed"]
    # The PDF and the workbook are rendered from one content object, so they agree.
    assert money(net) in pdf_text(demo_pdf)


def test_the_component_sheet_adds_up_to_the_totals(demo_book, demo_summary):
    rows = sheet_rows(demo_book, "Components")[1:]
    components = [r for r in rows if r[0]]
    spent = sum(Decimal(str(r[2])) for r in components if r[4] != "yes")
    saved = sum(Decimal(str(r[2])) for r in components if r[4] == "yes")
    assert spent == Decimal(demo_summary["total_gross"])
    assert saved == Decimal(demo_summary["total_offset"])
    assert spent + saved == Decimal(demo_summary["total_net"])
    assert {r[0] for r in components} == set(demo_summary["component_totals"])
    # A saving has no share of gross: it is not part of gross.
    assert all(r[3] is None for r in components if r[4] == "yes")


def test_a_withheld_cell_carries_no_figure_and_the_totals_still_reconcile(demo, demo_book):
    """Suppression is applied before the content is built, exactly as on screen: a withheld
    cell has its labels and its reason, and nothing a person could subtract."""
    rows = sheet_rows(demo_book, "Cost matrix")
    cells = [r for r in rows[1:] if r[0] and r[1]]
    withheld = [r for r in cells if r[9]]
    shown = [r for r in cells if not r[9]]
    assert withheld and shown
    for row in withheld:
        assert all(value is None for value in row[2:9]), row
    matrix = demo.get("/costs/matrix", headers=role("executive")).json()
    assert len(withheld) == sum(1 for c in matrix["cells"] if c["suppressed"])
    assert len(withheld) == matrix["suppression"]["suppressed_cells"]
    total = next(r for r in rows if r[0] == "Total")
    hidden = next(r for r in rows if r[0] == "All withheld cells together")
    visible_net = sum(Decimal(str(r[6])) for r in shown)
    assert visible_net + Decimal(str(hidden[6])) == Decimal(str(total[6]))
    assert Decimal(str(total[6])) == Decimal(matrix["totals"]["net"])
    assert Decimal(str(hidden[6])) == Decimal(matrix["suppression"]["other_suppressed_net"])


def test_the_appendix_lists_the_assumptions_the_figures_used(demo, demo_book, demo_pdf):
    rows = sheet_rows(demo_book, "Assumptions used")[1:]
    used = {r[0] for r in rows}
    registry = demo.get("/assumptions", headers=role("executive")).json()["assumptions"]
    assert used < {a["id"] for a in registry}, "the appendix is the ones used, not all of them"
    assert "benefits_multiplier_civilian" in used
    # A public-sector run does not consume a private-sector coefficient.
    assert not any(a.startswith("private_") or a.startswith("c6_") for a in used)
    civilian = next(r for r in rows if r[0] == "benefits_multiplier_civilian")
    assert Decimal(str(civilian[1])) == Decimal("1.627")
    assert civilian[6] == "researched" and "Employer Costs for Employee Compensation" in civilian[9]
    assert f"These are the {len(used)} assumptions" in pdf_text(demo_pdf)


def test_the_report_follows_the_sensitivity_mode(demo):
    high = demo.get("/costs/summary", params={"mode": "high"}, headers=role("executive")).json()
    response = demo.get("/exports/report.xlsx", params={"mode": "high"}, headers=role("executive"))
    book = load_workbook(io.BytesIO(response.content))
    summary = {row[0]: row[1] for row in sheet_rows(book, "Summary") if row[0]}
    assert summary["Assumptions at"] == "high values"
    assert Decimal(str(summary["Net cost"])) == Decimal(high["total_net"])
    assert (
        demo.get(
            "/exports/report.pdf", params={"mode": "wild"}, headers=role("executive")
        ).status_code
        == 422
    )


# ---------------------------------------------------------------------------
# a licensed installation: the firm, its clients, their roles
# ---------------------------------------------------------------------------
@pytest.fixture
def firm_site(tmp_path):
    site = Installation(tmp_path)
    for person in (LEAD, ANALYST, CLIENT, OUTSIDER):
        site.add_user(person)
    site.engagement = site.new_engagement("FY26 discipline cost review", "Pine Valley County")
    site.add_member(site.engagement, LEAD, "admin")
    site.add_member(site.engagement, ANALYST, "hr_analyst")
    site.add_member(site.engagement, CLIENT, "executive")
    assert site.upload_sample(site.engagement, LEAD).status_code == 200
    return site


def report(site, who, kind: str = "pdf", **params):
    return site.http.get(
        f"/engagements/{site.engagement}/exports/report.{kind}",
        params=params,
        headers=site.auth(who),
    )


def test_a_report_carries_the_firms_name_and_logo(firm_site):
    site = firm_site
    assert site.http.get("/firm", headers=site.auth(CLIENT)).json() == {
        "name": "",
        "has_logo": False,
        "logo_mime": None,
    }
    named = site.http.put(
        "/firm", json={"name": "  Northgate Advisory LLP "}, headers=site.auth(OWNER)
    )
    assert named.json()["name"] == "Northgate Advisory LLP"
    logo = png()
    saved = site.http.put(
        "/firm/logo",
        files={"file": ("logo.png", logo, "image/png")},
        headers=site.auth(OWNER),
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "name": "Northgate Advisory LLP",
        "has_logo": True,
        "logo_mime": "image/png",
    }
    assert site.http.get("/firm/logo", headers=site.auth(LEAD)).content == logo

    data = report(site, CLIENT).content
    text = pdf_text(data)
    assert "Northgate Advisory LLP" in text
    assert "Pine Valley County" in text and "FY26 discipline cost review" in text
    assert "Public sector" in text
    # Their name stands where the product's would otherwise be.
    assert "Workforce Risk Intelligence" not in text
    assert len(PdfReader(io.BytesIO(data)).pages[0].images) == 1

    removed = site.http.delete("/firm/logo", headers=site.auth(OWNER)).json()
    assert removed["has_logo"] is False
    assert site.http.get("/firm/logo", headers=site.auth(LEAD)).status_code == 404
    assert len(PdfReader(io.BytesIO(report(site, CLIENT).content)).pages[0].images) == 0


def test_only_a_firm_administrator_sets_the_name_and_logo(firm_site):
    site = firm_site
    for who in (LEAD, ANALYST, CLIENT):
        assert site.http.put("/firm", json={"name": "x"}, headers=site.auth(who)).status_code == 403
        denied = site.http.put(
            "/firm/logo", files={"file": ("l.png", png(), "image/png")}, headers=site.auth(who)
        )
        assert denied.status_code == 403
        assert site.http.delete("/firm/logo", headers=site.auth(who)).status_code == 403
    assert site.http.get("/firm").status_code == 401
    assert site.store.firm().name == ""


def test_a_logo_that_is_not_an_ordinary_picture_is_refused(firm_site):
    site = firm_site

    def upload(name: str, data: bytes, mime: str):
        return site.http.put(
            "/firm/logo", files={"file": (name, data, mime)}, headers=site.auth(OWNER)
        )

    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    # The name and the claimed type are not trusted: the file itself is read.
    assert upload("logo.png", svg, "image/png").status_code == 400
    assert upload("logo.png", b"not a picture at all", "image/png").status_code == 400
    assert upload("logo.png", b"", "image/png").status_code == 400
    too_big = upload("logo.png", b"\x89PNG" + b"0" * MAX_LOGO_BYTES, "image/png")
    assert too_big.status_code == 400 and "limit" in too_big.json()["detail"]
    gif = io.BytesIO()
    Image.new("RGB", (10, 10)).save(gif, format="GIF")
    refused = upload("logo.gif", gif.getvalue(), "image/gif")
    assert refused.status_code == 400 and "PNG or a JPEG" in refused.json()["detail"]
    assert site.store.firm().logo is None


def test_check_logo_accepts_png_and_jpeg_and_reports_which():
    assert check_logo(png()) == "image/png"
    jpeg = io.BytesIO()
    Image.new("RGB", (64, 64)).save(jpeg, format="JPEG")
    assert check_logo(jpeg.getvalue()) == "image/jpeg"
    with pytest.raises(ValueError, match="pixels"):
        check_logo(png(4200, 10))


def test_every_member_may_download_and_nobody_else(firm_site):
    site = firm_site
    for who in (LEAD, ANALYST, CLIENT):
        for kind in ("pdf", "xlsx"):
            assert report(site, who, kind).status_code == 200, (who, kind)
    assert report(site, OUTSIDER).status_code == 404
    url = f"/engagements/{site.engagement}/exports/report.pdf"
    assert site.http.get(url).status_code == 401


def test_a_client_reader_gets_the_same_withheld_cells_as_on_screen(firm_site):
    site = firm_site
    book = load_workbook(io.BytesIO(report(site, CLIENT, "xlsx").content))
    cells = [r for r in sheet_rows(book, "Cost matrix")[1:] if r[0] and r[1]]
    on_screen = site.http.get(
        f"/engagements/{site.engagement}/costs/matrix", headers=site.auth(CLIENT)
    ).json()
    assert sum(1 for r in cells if r[9]) == sum(1 for c in on_screen["cells"] if c["suppressed"])
    assert all(all(v is None for v in r[2:9]) for r in cells if r[9])


def test_the_clients_own_values_are_listed_with_who_set_them_and_why(firm_site):
    site = firm_site
    before = report(site, LEAD, "xlsx").content
    assert "None." in sheet_rows(load_workbook(io.BytesIO(before)), "Client values")[1][0]
    reason = "Rate on the client's 2026 engagement letter"
    assert (
        override(
            site, site.engagement, "c4_outside_counsel_hourly_rate", "450", reason=reason
        ).status_code
        == 200
    )

    book = load_workbook(io.BytesIO(report(site, CLIENT, "xlsx").content))
    row = sheet_rows(book, "Client values")[1]
    assert row[0] == "c4_outside_counsel_hourly_rate"
    assert Decimal(str(row[2])) == Decimal("450") and Decimal(str(row[1])) != Decimal("450")
    assert (row[4], row[5]) == (reason, LEAD[0])
    used = next(
        r for r in sheet_rows(book, "Assumptions used") if r[0] == "c4_outside_counsel_hourly_rate"
    )
    assert Decimal(str(used[1])) == Decimal("450") and used[8] == "yes"

    text = pdf_text(report(site, CLIENT).content)
    assert reason in text and LEAD[0] in text
    assert "450 *" in text, "the client's own value is marked in Appendix A"


def test_text_a_person_typed_is_never_run_as_a_formula_or_as_markup(firm_site):
    """An override's reason is free text. In the workbook it must stay text whatever it
    starts with, and in the PDF it must be printed, not read as layout markup."""
    site = firm_site
    hostile = '=HYPERLINK("http://example.invalid","click") <b>bold & </i> broken'
    assert (
        override(
            site, site.engagement, "c4_outside_counsel_hourly_rate", "450", reason=hostile
        ).status_code
        == 200
    )
    site.http.put("/firm", json={"name": "@SUM(1+1) & <Sons>"}, headers=site.auth(OWNER))

    book = load_workbook(io.BytesIO(report(site, LEAD, "xlsx").content))
    cell = book["Client values"]["E2"]
    assert cell.value == hostile and cell.data_type == "s"
    for sheet in book.worksheets:
        for row in sheet.iter_rows():
            for c in row:
                assert c.data_type != "f", (sheet.title, c.coordinate, c.value)

    response = report(site, LEAD)
    assert response.status_code == 200
    text = pdf_text(response.content)
    assert "@SUM(1+1) & <Sons>" in text
    assert "<b>bold & </i> broken" in text


def test_a_clients_name_cannot_steer_the_file_name(tmp_path):
    site = Installation(tmp_path)
    site.engagement = site.new_engagement("E", '../../etc/passwd"; rm -rf')
    assert site.upload_sample(site.engagement, OWNER).status_code == 200
    disposition = report(site, OWNER).headers["content-disposition"]
    assert disposition == 'attachment; filename="etc_passwd_rm_rf_cost_of_discipline.pdf"'


# ---------------------------------------------------------------------------
# a private-sector engagement
# ---------------------------------------------------------------------------
def test_a_private_engagements_report_carries_its_own_components(tmp_path):
    """Separation and insurance costs exist only in the private model. They have to reach
    the report and the one-page summary, which once listed the public components by name."""
    site = Installation(tmp_path / "install")
    site.engagement = site.http.post(
        "/engagements",
        json={
            "name": "Site review",
            "client_name": "Ridgeline Builders",
            "sector": "private_sector",
            "occupation_group": "construction_maintenance",
        },
        headers=site.auth(OWNER),
    ).json()["id"]
    folder = tmp_path / "files"
    folder.mkdir()
    write(folder, "employees", [EMPLOYEE])
    write(folder, "actions", [{**ACTION, "action_type": "removal"}])
    write(
        folder,
        "separations",
        [
            {
                "employee_id": "E1",
                "separation_date": "2025-06-13",
                "separation_reason": "removal_for_cause",
                "linked_action_id": "A1",
                "separation_agreement": "Y",
                "severance_amount": "8000",
            }
        ],
    )
    files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(folder.iterdir())]
    uploaded = site.http.post(
        f"/engagements/{site.engagement}/datasets/upload",
        params={"source": "wri_template"},
        files=files,
        headers=site.auth(OWNER),
    )
    assert uploaded.status_code == 200, uploaded.text

    book = load_workbook(io.BytesIO(report(site, OWNER, "xlsx").content))
    components = {r[0]: r for r in sheet_rows(book, "Components")[1:] if r[0]}
    assert "C6" in components
    assert components["C6"][1] == "Separation agreements & unemployment"
    assert Decimal(str(components["C6"][2])) >= Decimal("8000")
    summary = {row[0]: row[1] for row in sheet_rows(book, "Summary") if row[0]}
    assert summary["Cost model"] == "Private sector"
    assert summary["Occupation group"] == "Construction, maintenance and natural resources"
    used = {r[0] for r in sheet_rows(book, "Assumptions used")[1:]}
    assert "private_benefits_multiplier_construction_maintenance" in used

    text = pdf_text(report(site, OWNER).content)
    assert "Private sector" in text and "Separation agreements & unemployment" in text
    markdown = site.http.get(
        f"/engagements/{site.engagement}/exports/summary.md", headers=site.auth(OWNER)
    ).text
    assert "C6 Separation agreements & unemployment" in markdown
