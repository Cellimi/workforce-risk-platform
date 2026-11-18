"""Phase 3: the library of starting figures for common interventions, and what an
intervention is aimed at.

On 2026-11-16 the owner asked for placeholders; on 2026-11-17 his research sourced seven of
the twelve figures and he confirmed them. What is held here: that every figure is an
assumption in a file and nowhere in the code; that each has the standing he gave it; that an appraisal
says, wherever it is shown, which of its figures are placeholders and which are researched
but unconfirmed; that a library share of actions prevented is aimed at what its evidence
measured, and says so when aimed wider; that an aimed intervention counts only the actions
it is aimed at; and that nobody can unmark a figure except by changing it.
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

from test_interventions import proposal
from test_reporting import pdf_text, sheet_rows
from wri_engine.api.main import app
from wri_engine.api.state import STATE
from wri_engine.costing.assumptions import MissingAssumptionError
from wri_engine.interventions import AppraisalBook, UnitBaseline, appraise, load_causes
from wri_engine.interventions.findings import FILE_NAME
from wri_engine.interventions.library import (
    FIELDS,
    LIBRARY_FILE,
    fields_from_library,
    load_library,
    placeholder_note,
)
from wri_engine.orgconfig import default_org_config
from wri_engine.paths import CONFIG_DIR, PROJECT_ROOT, SYNTHETIC_DIR

LIBRARY = load_library()
ATTENDANCE = ("attendance_awol", "tardiness")


def ids(entry: str, *figures: str) -> set[str]:
    return {f"intervention_{entry}_{figure}" for figure in figures}


#: Costs with no published benchmark. They stay placeholders, as the owner's research said:
#: a client's own figures should come from payroll or the benefits administrator.
PLACEHOLDERS = (
    ids("transit_subsidy", "one_time_cost_per_person", "annual_cost_per_person")
    | ids("childcare_subsidy", "one_time_cost_per_person")
    | ids("flexible_hours", "one_time_cost_per_person", "annual_cost_per_person")
)
#: Sourced by the owner's research of 2026-11-17 and confirmed by him the same day, in his
#: own words: "it's all reviewed".
CONFIRMED = (
    ids("transit_subsidy", "reduction")
    | ids("childcare_subsidy", "annual_cost_per_person", "reduction")
    | ids("targeted_training", "one_time_cost_per_person", "annual_cost_per_person", "reduction")
    | ids("flexible_hours", "reduction")
)


# ---------------------------------------------------------------------------
# the file
# ---------------------------------------------------------------------------
def test_each_figure_has_the_standing_the_owners_research_gave_it():
    assert LIBRARY_FILE == CONFIG_DIR / "intervention_library.yaml"
    assert list(LIBRARY) == [
        "transit_subsidy",
        "childcare_subsidy",
        "targeted_training",
        "flexible_hours",
    ]
    raw = yaml.safe_load(LIBRARY_FILE.read_text())
    figures = {a["id"]: a for a in raw["assumptions"]}
    assert len(figures) == 12
    standing = {
        status: {i for i, a in figures.items() if a["status"] == status}
        for status in ("TBD-MIKE", "researched", "confirmed")
    }
    assert standing["TBD-MIKE"] == PLACEHOLDERS, "a placeholder changed standing"
    assert standing["confirmed"] == CONFIRMED, (
        "a figure changed standing: only the owner confirms one, in his own words"
    )
    assert standing["researched"] == set()
    for i in CONFIRMED:
        source = figures[i]["source"]
        assert "Placeholder" not in source and re.search(r"\b(19|20)\d\d\b", source), i
        assert "confirmed by him" in source, i
    for i in PLACEHOLDERS:
        assert "placeholder" in figures[i]["source"].lower(), i
        assert figures[i]["confidence"] == "low"


def test_the_figures_are_the_owners_research():
    """The values in the owner's research of 2026-11-17, figure by figure."""

    def figure(entry: str, name: str) -> tuple:
        e = LIBRARY[entry]
        if name == "reduction":
            return (e.reduction_low, e.reduction_expected, e.reduction_high)
        return getattr(e, name)

    assert figure("transit_subsidy", "annual_cost_per_person") == 1200
    assert figure("transit_subsidy", "reduction") == (
        Decimal("0.02"),
        Decimal("0.08"),
        Decimal("0.15"),
    )
    assert figure("childcare_subsidy", "annual_cost_per_person") == 3000
    assert figure("childcare_subsidy", "reduction") == (
        Decimal("0.05"),
        Decimal("0.15"),
        Decimal("0.30"),
    )
    assert figure("targeted_training", "one_time_cost_per_person") == 400
    assert figure("targeted_training", "annual_cost_per_person") == 165
    assert figure("targeted_training", "reduction") == (0, Decimal("0.15"), Decimal("0.25"))
    assert figure("flexible_hours", "reduction") == (0, Decimal("0.20"), Decimal("0.25"))
    raw = {a["id"]: a for a in yaml.safe_load(LIBRARY_FILE.read_text())["assumptions"]}
    assert raw["intervention_transit_subsidy_annual_cost_per_person"]["high"] == 4080
    assert (
        raw["intervention_childcare_subsidy_annual_cost_per_person"]["low"],
        raw["intervention_childcare_subsidy_annual_cost_per_person"]["high"],
    ) == (2400, 6000)


def test_each_share_is_aimed_at_what_its_evidence_measured():
    """The studies behind three of the four measured absences and lateness, not discipline.
    Those are aimed at attendance and tardiness actions only. Training targets misconduct."""
    taxonomy = set(default_org_config().misconduct_categories)
    for entry_id in ("transit_subsidy", "childcare_subsidy", "flexible_hours"):
        assert LIBRARY[entry_id].aims_at == ATTENDANCE
        assert "absences" in LIBRARY[entry_id].evidence
    assert LIBRARY["targeted_training"].aims_at == ()
    assert {t for e in LIBRARY.values() for t in e.aims_at} <= taxonomy
    causes = {c.id for c in load_causes().causes}
    assert {c for e in LIBRARY.values() for c in e.answers} <= causes


def test_no_library_figure_is_written_into_the_code():
    raw = yaml.safe_load(LIBRARY_FILE.read_text())
    values = {str(a[k]) for a in raw["assumptions"] for k in ("value", "low", "high")}
    # Too ordinary to tell apart from a width, a limit, or rounding.
    values -= {"0", "50", "100", "200", "0.1"}
    assert {"1200", "3000", "165", "4080", "0.08", "0.15"} <= values
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


def test_the_library_says_which_figures_are_placeholders():
    assert LIBRARY["transit_subsidy"].placeholder_fields == (
        "one_time_cost_per_person",
        "annual_cost_per_person",
    )
    assert LIBRARY["transit_subsidy"].researched_fields == ()
    assert LIBRARY["transit_subsidy"].note == (
        "The one-time cost per person and the yearly cost per person are placeholders: no "
        "published source. Replace them with the client's own."
    )
    assert LIBRARY["childcare_subsidy"].placeholder_fields == ("one_time_cost_per_person",)
    # Every training figure is confirmed: nothing to say.
    assert LIBRARY["targeted_training"].unreviewed == ()
    assert LIBRARY["targeted_training"].note == ""


# ---------------------------------------------------------------------------
# which figures are still the library's, and what is said about them
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
        "targets": entry.aims_at,
        **changes,
    }
    return proposal(**values)


def marked(entry_id: str = "targeted_training", library=LIBRARY, **changes):
    started = from_library(entry_id, **changes)
    fields = fields_from_library(started, entry_id, library)
    return dataclasses.replace(started, library_id=entry_id, library_fields=fields)


def test_a_figure_is_marked_while_it_stands_and_unmarked_by_changing_it():
    assert fields_from_library(from_library(), "targeted_training", LIBRARY) == FIELDS
    own_cost = from_library(annual_cost_per_person=Decimal("85"))
    assert fields_from_library(own_cost, "targeted_training", LIBRARY) == (
        "one_time_cost_per_person",
        "reduction",
    )
    own_view = from_library(reduction_expected=Decimal("0.2"))
    assert "reduction" not in fields_from_library(own_view, "targeted_training", LIBRARY)
    assert fields_from_library(from_library(), "", LIBRARY) == ()
    assert fields_from_library(from_library(), "magic_beans", LIBRARY) == ()
    assert fields_from_library(from_library(), "transit_subsidy", LIBRARY) == ()


def test_what_is_said_beside_an_appraisal_that_keeps_library_figures(tmp_path):
    assert placeholder_note(marked("transit_subsidy"), LIBRARY) == (
        "Placeholder figures: The one-time cost per person and the yearly cost per person "
        'are the product\'s starting figures for "Transit subsidy", with no published source '
        "and not reviewed, and not this client's own. The client's own figures, from payroll "
        "or the benefits administrator, should replace them."
    )
    # The client's own costs: only confirmed figures are left, and nothing is said.
    own = marked(
        "transit_subsidy",
        one_time_cost_per_person=Decimal("10"),
        annual_cost_per_person=Decimal("900"),
    )
    assert own.library_fields == ("reduction",) and placeholder_note(own, LIBRARY) == ""
    assert placeholder_note(marked(), LIBRARY) == "", "every training figure is confirmed"
    assert placeholder_note(proposal(), LIBRARY) == ""

    # A figure researched but not confirmed is said to be so.
    text = LIBRARY_FILE.read_text()
    start = text.index("- id: intervention_targeted_training_reduction")
    end = text.index("# ---", start)
    researched = tmp_path / "researched.yaml"
    researched.write_text(
        text[:start]
        + text[start:end].replace("status: confirmed", "status: researched")
        + text[end:]
    )
    library = load_library(researched)
    assert library["targeted_training"].researched_fields == ("reduction",)
    assert placeholder_note(marked(library=library), library) == (
        "Researched figures: The share of actions prevented is the product's starting figure "
        'for "Targeted training", sourced but not yet confirmed by the owner, and not this '
        "client's own."
    )


def test_a_share_aimed_wider_than_its_evidence_says_so():
    """The transit share comes from studies of absences and lateness. Aimed at those it
    says nothing more; aimed at every type, or at insubordination, it says the evidence does
    not reach that far."""
    own_costs = {
        "one_time_cost_per_person": Decimal("10"),
        "annual_cost_per_person": Decimal("900"),
    }
    narrow = marked("transit_subsidy", **own_costs)
    assert narrow.targets == ATTENDANCE
    assert "studies of" not in placeholder_note(narrow, LIBRARY)
    assert (
        placeholder_note(
            marked("transit_subsidy", targets=("attendance_awol",), **own_costs), LIBRARY
        )
        == ""
    )
    wider = (
        'The share of actions prevented is the library\'s figure for "Transit subsidy", which '
        "comes from studies of absences and lateness. Here it is applied to discipline those "
        "studies did not measure."
    )
    for aim in ((), ("insubordination",), (*ATTENDANCE, "insubordination")):
        assert placeholder_note(
            marked("transit_subsidy", targets=aim, **own_costs), LIBRARY
        ).endswith(wider)
    # Training's evidence is about misconduct itself, so it is never aimed too wide.
    assert "studies of" not in placeholder_note(marked(targets=()), LIBRARY)
    # And with the consultant's own share, the evidence is theirs to answer for.
    own_share = marked(
        "transit_subsidy", targets=(), reduction_expected=Decimal("0.1"), **own_costs
    )
    assert placeholder_note(own_share, LIBRARY) == ""


def test_the_note_goes_once_the_owner_has_confirmed_and_not_before(tmp_path):
    """Every training figure is confirmed and unchanged: an appraisal resting on them needs
    no sentence. Change one in the file: an appraisal still carrying the old value is told
    it carries a figure that was never confirmed."""
    assert placeholder_note(marked(), LIBRARY) == ""
    assert placeholder_note(marked("transit_subsidy"), LIBRARY) != ""
    changed = tmp_path / "changed.yaml"
    changed.write_text(LIBRARY_FILE.read_text().replace("  value: 165\n", "  value: 170\n"))
    new = load_library(changed)
    assert new["targeted_training"].annual_cost_per_person == 170
    assert placeholder_note(marked(), new).startswith(
        "Placeholder figures: The yearly cost per person is"
    )


# ---------------------------------------------------------------------------
# aimed at some types only: the arithmetic, by hand
# ---------------------------------------------------------------------------
def unit(**changes) -> UnitBaseline:
    """$30,000 over 3 years, $10,000 a year: attendance is 10 actions costing $6,000 among
    eight people, insubordination 20 costing $24,000 among fifteen."""
    attendance = frozenset(f"a{n}" for n in range(8))
    insubordination = frozenset(f"i{n}" for n in range(15))
    values = {
        "row": "caseworker",
        "col": "North Office",
        "row_label": "Caseworker",
        "col_label": "North Office",
        "topic": "",
        "topic_label": "",
        "actions": 30,
        "people": 23,
        "net": Decimal("30000"),
        "window_years": Decimal("3"),
        "cohort": attendance | insubordination,
        "by_type": {
            "attendance_awol": (10, Decimal("6000"), attendance),
            "insubordination": (20, Decimal("24000"), insubordination),
        },
        "type_labels": {
            "attendance_awol": "Attendance / AWOL",
            "insubordination": "Insubordination",
        },
        **changes,
    }
    return UnitBaseline(**values)


def test_an_aimed_intervention_counts_only_the_actions_it_is_aimed_at():
    """Aimed at attendance: X is $6,000 / 3 = $2,000 a year, not $10,000. A $1,000
    intervention over 2 years pays for itself at $1,000 / ($2,000 x 2) = 25%. Preventing
    half saves $1,000 a year: $2,000 - $1,000 = $1,000 ahead over two years."""
    cheap = {
        "one_time_cost": Decimal("1000"),
        "annual_cost_per_person": Decimal(0),
        "other_annual_cost": Decimal(0),
    }
    aimed = appraise(unit(), proposal(targets=("attendance_awol",), **cheap))
    assert aimed.aimed_annual_net == Decimal("2000")
    assert aimed.break_even == Decimal("0.25")
    assert aimed.expected.prevented_per_year == Decimal("1000")
    assert aimed.expected.net_saving == Decimal("1000")
    assert aimed.expected.actions_prevented_per_year == Decimal(10) / 3 * Decimal("0.5")
    assert aimed.expected.formula == (
        "$2,000.00 a year of Attendance / AWOL actions x 50% prevented x 2 years - $1,000.00 "
        "cost of the intervention"
    )
    assert aimed.break_even_note == (
        "It pays for itself if it prevents 25% of the Attendance / AWOL actions: $1,000.00 / "
        "($2,000.00 a year x 2 years)."
    )
    assert aimed.as_dict()["aimed_at"] == {
        "types": ["attendance_awol"],
        "label": "Attendance / AWOL",
        "annual_actions": "3.3",
        "annual_net": "2000.00",
    }
    assert unit().aimed(("attendance_awol",))[2] == frozenset(f"a{n}" for n in range(8))
    # Aimed at nothing in particular: the whole unit, exactly as before.
    whole = appraise(unit(), proposal(**cheap))
    assert whole.aimed_annual_net == Decimal("10000") and whole.as_dict()["aimed_at"] is None
    assert whole.break_even == Decimal("0.05")
    assert unit().label_of(("attendance_awol", "insubordination")) == (
        "Attendance / AWOL and Insubordination"
    )


def test_aimed_at_types_the_unit_was_never_disciplined_for_there_is_nothing_to_prevent():
    result = appraise(unit(), proposal(targets=("tardiness",)))
    assert result.break_even is None and result.aimed_annual_net == 0
    assert result.break_even_note == (
        "None of this unit's discipline is of the types this intervention is aimed at "
        "(tardiness), so there is nothing here for it to prevent."
    )
    assert all(s.prevented_per_year == 0 for s in result.scenarios)


def test_a_one_time_cost_per_person_is_part_of_what_the_intervention_costs():
    """$400 x 20 people once, and $165 x 20 people a year for 2 years: $8,000 + $6,600."""
    result = appraise(unit(), from_library(people_covered=20))
    assert result.intervention.one_time_total == Decimal("8000")
    assert result.intervention.total_cost == Decimal("14600")
    assert result.intervention.cost_formula == (
        "$0.00 one-time + $400.00 x 20 people one-time + ($165.00 x 20 people + $0.00 other) "
        "a year x 2 years"
    )
    assert from_library(one_time_cost_per_person=Decimal("-1")).problems() == [
        "the one-time cost per person cannot be negative"
    ]
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
    assert kept.intervention.library_id == "" and kept.intervention.targets == ()
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


def role(name: str, session: str = "") -> dict:
    return {"X-WRI-Role": name, **({"X-WRI-Session": session} if session else {})}


def biggest_unit() -> tuple[str, str]:
    people: dict = {}
    for action in STATE.run(mode="base").action_costs:
        d = action.dimensions
        people.setdefault((d["role_family"], d["work_location"]), set()).add(action.employee_id)
    return max(people, key=lambda u: len(people[u]))


def body_from_library(entry_id: str = "targeted_training", **changes) -> dict:
    entry = LIBRARY[entry_id].as_dict()
    job, place = biggest_unit()
    return {
        "role_family": job,
        "work_location": place,
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
        "targets": entry["aims_at"],
        "basis": "The consultant's judgement after the site visit.",
        **changes,
    }


def test_the_library_is_served_with_what_each_entry_is_aimed_at(client):
    served = client.get("/interventions/library", headers=role("hr_analyst")).json()
    assert [e["id"] for e in served["entries"]] == list(LIBRARY)
    assert (served["unreviewed"], served["placeholders"]) == (5, 5)
    transit = served["entries"][0]
    assert transit["aims_at_labels"] == ["Attendance / AWOL", "Tardiness"]
    assert transit["placeholder_fields"] == ["one_time_cost_per_person", "annual_cost_per_person"]
    assert transit["researched_fields"] == []
    assert any("IRS" in source for source in transit["sources"])
    types = {t["id"] for t in served["misconduct_types"]}
    assert set(ATTENDANCE) <= types and "insubordination" in types
    assert client.get("/interventions/library", headers=role("executive")).status_code == 403


def test_an_appraisal_says_what_it_rests_on_and_what_it_is_aimed_at(client):
    admin = role("admin")
    training = client.post(
        "/interventions/appraise", json=body_from_library(), headers=admin
    ).json()
    assert training["intervention"]["library_fields"] == list(FIELDS)
    assert training["placeholder_note"] == "", "every training figure is confirmed"
    # 400 x 25 once, and 165 x 25 a year for three years.
    assert training["intervention"]["total_cost"] == "22375.00"
    assert training["aimed_at"] is None

    transit = client.post(
        "/interventions/appraise", json=body_from_library("transit_subsidy"), headers=admin
    ).json()
    assert transit["aimed_at"]["label"] == "Attendance / AWOL and Tardiness"
    assert Decimal(transit["aimed_at"]["annual_net"]) < Decimal(transit["unit"]["annual_net"])
    assert transit["placeholder_note"].startswith(
        "Placeholder figures: The one-time cost per person and the yearly cost per person"
    )
    assert "studies of" not in transit["placeholder_note"]
    wide = client.post(
        "/interventions/appraise",
        json=body_from_library("transit_subsidy", targets=[]),
        headers=admin,
    ).json()
    assert wide["aimed_at"] is None
    assert wide["placeholder_note"].endswith(
        "Here it is applied to discipline those studies did not measure."
    )

    claimed = {**body_from_library("transit_subsidy"), "library_fields": []}
    result = client.post("/interventions/appraise", json=claimed, headers=admin).json()
    assert result["intervention"]["library_fields"] == list(FIELDS), "worked out on the server"
    unknown = client.post(
        "/interventions/appraise", json=body_from_library(targets=["gossip"]), headers=admin
    )
    assert unknown.status_code == 422
    assert unknown.json()["detail"]["problems"] == [
        "gossip is not a type of misconduct in this engagement"
    ]


def test_the_clients_report_says_what_the_appraisal_rests_on_and_is_aimed_at(client):
    admin = role("admin", "library-lead")
    body = body_from_library("transit_subsidy", causes=["commuting"], annual_cost_per_person="900")
    made = client.post("/interventions", json=body, headers=admin)
    assert made.status_code == 200, made.text
    note = made.json()["appraisal"]["placeholder_note"]
    assert note.startswith("Placeholder figures: The one-time cost per person is")
    assert "Researched figures" not in note
    assert made.json()["aimed_at_label"] == "Attendance / AWOL and Tardiness"

    reader = role("executive", "library-reader")
    text = pdf_text(client.get("/exports/report.pdf", headers=reader).content)
    assert "Transit subsidy:" in text and note in text
    assert (
        "Aimed at Attendance / AWOL and Tardiness. The figures below count those actions only."
        in text
    )
    book = load_workbook(io.BytesIO(client.get("/exports/report.xlsx", headers=reader).content))
    header, *rows = sheet_rows(book, "Interventions")
    row = next(dict(zip(header, r, strict=True)) for r in rows if r[4] == "Transit subsidy")
    assert row["Placeholder figures"] == note
    assert row["Aimed at (the costs and savings count these only)"] == (
        "Attendance / AWOL and Tardiness"
    )


def test_an_aimed_group_too_small_to_show_withholds_the_money_not_the_finding(client):
    """The unit is large enough to show; the part of it an intervention is aimed at may not
    be. Then the client's copy keeps the finding and the proposal and withholds the money,
    because the aimed cost would be a figure about that small group."""
    run = STATE.run(mode="base")
    by_unit: dict = {}
    for action in run.action_costs:
        d = action.dimensions
        key = (d["role_family"], d["work_location"])
        by_unit.setdefault(key, {}).setdefault(d["misconduct_category"], set()).add(
            action.employee_id
        )
    (job, place), kind = next(
        (u, t)
        for u, types in sorted(by_unit.items())
        if len(set().union(*types.values())) >= 8
        for t, who in sorted(types.items())
        if len(who) < 5
    )
    admin = role("admin", "aim-lead")
    body = body_from_library(
        role_family=job, work_location=place, targets=[kind], causes=["training"], library_id=""
    )
    assert client.post("/interventions", json=body, headers=admin).status_code == 200
    text = pdf_text(
        client.get("/exports/report.pdf", headers=role("executive", "aim-reader")).content
    )
    assert "Targeted training:" in text
    assert "What the discipline costs in this unit is not shown" in text
    papers = load_workbook(
        io.BytesIO(
            client.get(
                "/exports/report.xlsx",
                params={"records": "true"},
                headers=role("admin", "aim-papers"),
            ).content
        )
    )
    header, *rows = sheet_rows(papers, "Interventions")
    shown = next(dict(zip(header, r, strict=True)) for r in rows if r[4] == "Targeted training")
    assert shown["The discipline costs, a year"] is not None, "the working papers carry it"
