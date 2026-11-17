"""Phase 3: the library of starting figures for common interventions.

On 2026-11-16 the owner asked the build to "go with your placeholder assumptions" and said
he will research the values himself. What is held here: that every figure in the library is
an assumption in a file, marked unreviewed, and nowhere in the code; that nothing is used
unless a person chooses it; that an appraisal says, wherever it is shown, which of its
figures are still the product's placeholders and not the client's own; and that nobody can
unmark a figure except by changing it.
"""

from __future__ import annotations

import dataclasses
import io
import json
import re
from decimal import Decimal

import pytest
import yaml
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from test_interventions import baseline, proposal
from test_reporting import pdf_text, sheet_rows
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.costing.assumptions import MissingAssumptionError
from wri_engine.interventions import AppraisalBook, appraise, load_causes
from wri_engine.interventions.findings import FILE_NAME
from wri_engine.interventions.library import (
    FIELDS,
    LIBRARY_FILE,
    fields_from_library,
    load_library,
    placeholder_note,
)
from wri_engine.paths import CONFIG_DIR, PROJECT_ROOT, SYNTHETIC_DIR

LIBRARY = load_library()

#: Library figures the owner has not researched. Every one is a placeholder proposed by the
#: build. This is the list to read and then delete from, as the cost registry's is: when he
#: has researched, cited and reviewed a figure, its status changes and it leaves this set.
LIBRARY_AWAITING_OWNER_RESEARCH = {
    f"intervention_{entry}_{figure}"
    for entry in ("transit_subsidy", "childcare_subsidy", "targeted_training", "flexible_hours")
    for figure in FIELDS
}


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_the_library_holds_the_four_the_overview_names_each_a_placeholder():
    assert LIBRARY_FILE == CONFIG_DIR / "intervention_library.yaml"
    assert list(LIBRARY) == [
        "transit_subsidy",
        "childcare_subsidy",
        "targeted_training",
        "flexible_hours",
    ]
    raw = yaml.safe_load(LIBRARY_FILE.read_text())
    figures = {a["id"]: a for a in raw["assumptions"]}
    assert set(figures) == LIBRARY_AWAITING_OWNER_RESEARCH and len(figures) == 12
    pending = {i for i, a in figures.items() if a["status"] == "TBD-MIKE"}
    assert pending == LIBRARY_AWAITING_OWNER_RESEARCH, (
        "a library figure changed standing: update LIBRARY_AWAITING_OWNER_RESEARCH"
    )
    for figure in figures.values():
        # Nothing here may pass for a researched figure.
        assert figure["confidence"] == "low"
        assert "Placeholder" in figure["source"] and "Not researched" in figure["source"]
    for entry in LIBRARY.values():
        assert set(entry.unreviewed) == set(entry.assumption_ids.values())
        assert entry.note.endswith("placeholders: not yet researched, sourced or reviewed.")
        assert 0 <= entry.reduction_low <= entry.reduction_expected <= entry.reduction_high <= 1
    # Each is aimed at a cause the investigation list holds.
    causes = {c.id for c in load_causes().causes}
    assert {c for e in LIBRARY.values() for c in e.answers} <= causes
    assert LIBRARY["transit_subsidy"].as_dict()["annual_cost_per_person"] == "1200"


def test_no_library_figure_is_written_into_the_code():
    """Rule 1, for the library: the figures are in the file and nowhere in the code."""
    raw = yaml.safe_load(LIBRARY_FILE.read_text())
    values = {str(a["value"]) for a in raw["assumptions"]} | {
        str(a[bound]) for a in raw["assumptions"] for bound in ("low", "high")
    }
    # Too ordinary to tell apart from a width, a limit, or rounding to one decimal place.
    values -= {"0", "50", "100", "200", "0.1"}
    assert {"1200", "3000", "400", "0.15", "0.2", "0.35"} <= values
    for path in (PROJECT_ROOT / "src" / "wri_engine" / "interventions").glob("*.py"):
        code = re.sub(r'""".*?"""', "", path.read_text(), flags=re.S)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        for value in values:
            assert not re.search(rf"(?<![\w.]){re.escape(value)}(?![\w.])", code), (
                path.name,
                value,
            )


def test_a_missing_library_figure_is_an_error_not_a_default(tmp_path):
    text = LIBRARY_FILE.read_text()
    start = text.index("- id: intervention_flexible_hours_reduction")
    (tmp_path / "library.yaml").write_text(text[:start])
    with pytest.raises(MissingAssumptionError, match="intervention_flexible_hours_reduction"):
        load_library(tmp_path / "library.yaml")


# ---------------------------------------------------------------------------
# which figures are still the library's
# ---------------------------------------------------------------------------
def from_library(entry_id: str = "targeted_training", **changes):
    entry = LIBRARY[entry_id]
    values = {
        "name": entry.label,
        "one_time_cost": Decimal(0),
        "one_time_cost_per_person": entry.one_time_cost_per_person,
        "annual_cost_per_person": entry.annual_cost_per_person,
        "other_annual_cost": Decimal(0),
        "reduction_low": entry.reduction_low,
        "reduction_expected": entry.reduction_expected,
        "reduction_high": entry.reduction_high,
        **changes,
    }
    return proposal(**values)


def marked(entry_id: str = "targeted_training", **changes):
    started = from_library(entry_id, **changes)
    fields = fields_from_library(started, entry_id, LIBRARY)
    return dataclasses.replace(started, library_id=entry_id, library_fields=fields)


def test_a_figure_is_marked_while_it_stands_and_unmarked_by_changing_it():
    assert fields_from_library(from_library(), "targeted_training", LIBRARY) == FIELDS
    # The client's own yearly cost: that figure is theirs now, the other two are not.
    own_cost = from_library(annual_cost_per_person=Decimal("85"))
    assert fields_from_library(own_cost, "targeted_training", LIBRARY) == (
        "one_time_cost_per_person",
        "reduction",
    )
    # Changing one of the three shares makes the estimate the consultant's.
    own_view = from_library(reduction_expected=Decimal("0.25"))
    assert "reduction" not in fields_from_library(own_view, "targeted_training", LIBRARY)
    everything = from_library(
        one_time_cost_per_person=Decimal("1"),
        annual_cost_per_person=Decimal("2"),
        reduction_low=Decimal("0.01"),
    )
    assert fields_from_library(everything, "targeted_training", LIBRARY) == ()
    # Not started from the library, or from an entry it does not hold: nothing is marked.
    assert fields_from_library(from_library(), "", LIBRARY) == ()
    assert fields_from_library(from_library(), "magic_beans", LIBRARY) == ()
    # The same figures against a different entry are not that entry's.
    assert fields_from_library(from_library(), "transit_subsidy", LIBRARY) == ()


def test_what_is_said_beside_an_appraisal_that_keeps_a_placeholder():
    assert placeholder_note(marked(), LIBRARY) == (
        "Placeholder figures: The one-time cost per person, the yearly cost per person and "
        "the share of actions prevented are the product's starting figures for \"Targeted "
        "training\", not yet researched, sourced or reviewed, and not this client's own."
    )
    one = marked(annual_cost_per_person=Decimal("85"), one_time_cost_per_person=Decimal("350"))
    assert placeholder_note(one, LIBRARY) == (
        "Placeholder figures: The share of actions prevented is the product's starting "
        'figure for "Targeted training", not yet researched, sourced or reviewed, and not '
        "this client's own."
    )
    assert placeholder_note(proposal(), LIBRARY) == ""
    own = marked(
        one_time_cost_per_person=Decimal("1"),
        annual_cost_per_person=Decimal("2"),
        reduction_low=Decimal("0.01"),
    )
    assert own.library_fields == () and placeholder_note(own, LIBRARY) == ""


def test_the_note_goes_when_the_owner_has_reviewed_and_not_before(tmp_path):
    """Review every figure of one entry, unchanged: an appraisal resting on them needs no
    warning. Review one with a new value: an appraisal still carrying the old one does."""
    reviewed = tmp_path / "library.yaml"
    text = LIBRARY_FILE.read_text()
    head, tail = text.split(
        "# ---------------------------------------------------------------- targeted training"
    )
    block, rest = tail.split(
        "# ---------------------------------------------------------------- flexible", 1
    )
    block = block.replace("status: TBD-MIKE", "status: confirmed")
    reviewed.write_text(
        head
        + "# ---------------------------------------------------------------- targeted training"
        + block
        + "# ---------------------------------------------------------------- flexible"
        + rest
    )
    library = load_library(reviewed)
    assert library["targeted_training"].unreviewed == () and library["targeted_training"].note == ""
    assert library["transit_subsidy"].unreviewed != ()
    assert placeholder_note(marked(), library) == ""
    assert "the yearly cost per person" in placeholder_note(marked("transit_subsidy"), library)

    changed = tmp_path / "changed.yaml"
    changed.write_text(
        reviewed.read_text().replace("  value: 400\n  low: 200", "  value: 450\n  low: 200")
    )
    assert load_library(changed)["targeted_training"].one_time_cost_per_person == Decimal("450")
    still_old = placeholder_note(marked(), load_library(changed))
    assert still_old.startswith("Placeholder figures: The one-time cost per person is ")


def test_a_one_time_cost_per_person_is_part_of_what_the_intervention_costs():
    """$400 x 20 people once, and ($100 x 20 people) a year for 2 years: $8,000 + $4,000."""
    result = appraise(baseline(), from_library(people_covered=20))
    assert result.intervention.one_time_total == Decimal("8000")
    assert result.intervention.total_cost == Decimal("12000")
    assert result.intervention.cost_formula == (
        "$0.00 one-time + $400.00 x 20 people one-time + ($100.00 x 20 people + $0.00 other) "
        "a year x 2 years"
    )
    assert from_library(one_time_cost_per_person=Decimal("-1")).problems() == [
        "the one-time cost per person cannot be negative"
    ]
    # An appraisal entered before this figure existed costs what it always did.
    assert proposal().one_time_cost_per_person == 0 and proposal().total_cost == Decimal("9000")


def test_an_appraisal_kept_before_the_library_existed_still_reads(tmp_path):
    book = AppraisalBook(tmp_path)
    old_line = {
        "event": "kept",
        "id": "old",
        "row": "caseworker",
        "col": "North Office",
        "topic": "",
        "causes": ["training"],
        "other_cause": "",
        "entered_by": "Dana Lead",
        "entered_at": "2026-11-16T15:00:00+00:00",
        "intervention": {
            "name": "Training",
            "one_time_cost": "3000",
            "annual_cost_per_person": "100",
            "people_covered": 20,
            "other_annual_cost": "1000",
            "years": 2,
            "reduction_low": "0.1",
            "reduction_expected": "0.5",
            "reduction_high": "1",
            "basis": "The site visit.",
        },
    }
    (tmp_path / FILE_NAME).write_text(json.dumps(old_line) + "\n")
    (kept,) = book.current()
    assert kept.intervention.total_cost == Decimal("9000")
    assert kept.intervention.library_id == "" and kept.intervention.library_fields == ()
    assert placeholder_note(kept.intervention, LIBRARY) == ""


# ---------------------------------------------------------------------------
# the API and the deliverables
# ---------------------------------------------------------------------------
@pytest.fixture
def client():
    STATE.load(SYNTHETIC_DIR / "sample")
    STATE._appraisals = None
    yield TestClient(app)
    STATE._appraisals = None


def role(name: str) -> dict:
    return {"X-WRI-Role": name}


def body_from_library(entry_id: str = "targeted_training", **changes) -> dict:
    entry = LIBRARY[entry_id].as_dict()
    first = STATE.run(mode="base").action_costs[0].dimensions
    return {
        "role_family": first["role_family"],
        "work_location": first["work_location"],
        "misconduct_category": "",
        "name": entry["label"],
        "library_id": entry_id,
        "one_time_cost_per_person": entry["one_time_cost_per_person"],
        "annual_cost_per_person": entry["annual_cost_per_person"],
        "people_covered": 25,
        "years": 3,
        "reduction_low": entry["reduction_low"],
        "reduction_expected": entry["reduction_expected"],
        "reduction_high": entry["reduction_high"],
        "basis": "The consultant's judgement after the site visit.",
        **changes,
    }


def test_the_library_is_served_to_roles_that_see_records(client):
    served = client.get("/interventions/library", headers=role("hr_analyst")).json()
    assert [e["id"] for e in served["entries"]] == list(LIBRARY)
    assert served["unreviewed"] == 12
    training = served["entries"][2]
    assert training["label"] == "Targeted training" and len(training["unreviewed"]) == 3
    assert training["note"].endswith("placeholders: not yet researched, sourced or reviewed.")
    assert client.get("/interventions/library", headers=role("executive")).status_code == 403


def test_an_appraisal_says_which_of_its_figures_are_placeholders(client):
    admin = role("admin")
    started = client.post("/interventions/appraise", json=body_from_library(), headers=admin)
    assert started.status_code == 200, started.text
    result = started.json()
    assert result["intervention"]["library_id"] == "targeted_training"
    assert result["intervention"]["library_fields"] == list(FIELDS)
    assert result["placeholder_note"].startswith(
        "Placeholder figures: The one-time cost per person,"
    )
    # 400 x 25 once, and 100 x 25 a year for three years.
    assert result["intervention"]["total_cost"] == "17500.00"

    # The client's own costs: only the estimate of how well it works is still a placeholder.
    own_costs = body_from_library(one_time_cost_per_person="350", annual_cost_per_person="85")
    result = client.post("/interventions/appraise", json=own_costs, headers=admin).json()
    assert result["intervention"]["library_fields"] == ["reduction"]
    assert result["placeholder_note"].startswith(
        "Placeholder figures: The share of actions prevented is"
    )

    # Saying nothing about the library does not unmark figures; it just is not a library
    # appraisal, and a caller cannot send which figures to mark.
    silent = {k: v for k, v in body_from_library().items() if k != "library_id"}
    result = client.post("/interventions/appraise", json=silent, headers=admin).json()
    assert result["intervention"]["library_fields"] == [] and result["placeholder_note"] == ""
    claimed = {**body_from_library(), "library_fields": []}
    result = client.post("/interventions/appraise", json=claimed, headers=admin).json()
    assert result["intervention"]["library_fields"] == list(FIELDS), "worked out on the server"
    unknown = body_from_library(library_id="magic_beans")
    result = client.post("/interventions/appraise", json=unknown, headers=admin).json()
    assert result["intervention"]["library_id"] == "" and result["placeholder_note"] == ""


def test_the_clients_report_says_so_beside_a_kept_appraisal(client):
    """A report that goes to a client may not pass a placeholder off as a figure."""
    admin = {"X-WRI-Role": "admin", "X-WRI-Session": "library-lead"}
    run = STATE.run(mode="base")
    people: dict = {}
    for action in run.action_costs:
        d = action.dimensions
        people.setdefault((d["role_family"], d["work_location"]), set()).add(action.employee_id)
    job, place = max(people, key=lambda unit: len(people[unit]))
    body = body_from_library(
        role_family=job, work_location=place, causes=["training"], annual_cost_per_person="85"
    )
    made = client.post("/interventions", json=body, headers=admin)
    assert made.status_code == 200, made.text
    note = made.json()["appraisal"]["placeholder_note"]
    assert note.startswith("Placeholder figures: The one-time cost per person and the share of")
    listed = client.get("/interventions", headers=admin).json()["kept"][0]
    assert listed["appraisal"]["placeholder_note"] == note

    reader = {"X-WRI-Role": "executive", "X-WRI-Session": "library-reader"}
    text = pdf_text(client.get("/exports/report.pdf", headers=reader).content)
    assert "Targeted training:" in text
    assert note in text
    book = load_workbook(io.BytesIO(client.get("/exports/report.xlsx", headers=reader).content))
    header, *rows = sheet_rows(book, "Interventions")
    row = next(dict(zip(header, r, strict=True)) for r in rows if r[4] == "Targeted training")
    assert row["Placeholder figures"] == note

    # An appraisal in the consultant's own figures carries no such line.
    STATE._appraisals = None
    own = {k: v for k, v in body.items() if k != "library_id"}
    assert client.post("/interventions", json=own, headers=admin).status_code == 200
    text = pdf_text(client.get("/exports/report.pdf", headers=reader).content)
    assert "Targeted training:" in text and "Placeholder figures" not in text
