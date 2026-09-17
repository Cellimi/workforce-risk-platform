"""Generator determinism and the distribution targets it is tuned to."""

from __future__ import annotations

from collections import Counter

import pytest

from wri_engine.generator import generate

SEED = 20260917


@pytest.fixture(scope="module")
def export():
    return generate(seed=SEED)


def test_the_same_seed_produces_identical_output(export):
    again = generate(seed=SEED)
    assert again.actions == export.actions
    assert again.employees == export.employees
    assert again.appeals == export.appeals
    assert again.separations == export.separations


def test_a_different_seed_produces_different_output(export):
    other = generate(seed=SEED + 1)
    assert other.actions != export.actions


def test_manifest_records_what_produced_the_data(export):
    manifest = export.manifest
    assert manifest["seed"] == SEED
    assert manifest["synthetic"] is True
    assert "not derived from any real agency" in manifest["disclaimer"]
    assert set(manifest["config_digests"]) == {
        "org_county.yaml",
        "generator_profile.yaml",
        "mapping_county_hr_csv.yaml",
    }


def test_action_volume_is_in_the_intended_range(export):
    assert 400 <= len(export.actions) <= 600


def test_removals_are_five_to_eight_percent_of_actions(export):
    share = Counter(r["ACTN_CD"] for r in export.actions)["RMV"] / len(export.actions)
    assert 0.05 <= share <= 0.08, f"removal share {share:.1%} is outside the 5-8% target"


def test_suspension_appeal_rate_is_fifteen_to_twenty_five_percent(export):
    appealed = {r["ACTN_NBR"] for r in export.appeals}
    suspensions = [r for r in export.actions if r["ACTN_CD"].startswith("SP")]
    rate = sum(1 for r in suspensions if r["ACTN_NBR"] in appealed) / len(suspensions)
    assert 0.15 <= rate <= 0.25, f"suspension appeal rate {rate:.1%} is outside target"


def test_use_of_force_only_appears_for_sworn_employees(export):
    sworn = {r["EMP_NBR"] for r in export.employees if r["SWORN_IND"] == "Y"}
    misapplied = [
        r
        for r in export.actions
        if r["MISCND_CD"] == "UOF"
        and r["EMP_NBR"] in {e["EMP_NBR"] for e in export.employees}
        and r["EMP_NBR"] not in sworn
    ]
    # The only civilian use-of-force rows are the ones deliberately injected as bad data.
    assert (
        len(misapplied)
        == export.manifest["injected_data_quality_issues"]["sworn_only_category_misapplied"]
    )


def test_attendance_is_the_most_common_category(export):
    counts = Counter(r["MISCND_CD"] for r in export.actions)
    assert counts.most_common(1)[0][0] == "ATT"
    assert counts["TRD"] == counts.most_common(2)[1][1]


def test_the_embedded_pattern_shows_up_as_the_largest_volume_cell(export):
    """Documented in generator/README.md. Phase 1 surfaces it as an expensive cell, never
    as an alert."""
    by_id = {r["EMP_NBR"]: r for r in export.employees}
    cells = Counter()
    for row in export.actions:
        employee = by_id.get(row["EMP_NBR"])
        if employee:
            cells[(employee["CLASS_CD"], employee["WORK_LOC"], row["MISCND_CD"])] += 1
    top = cells.most_common(1)[0][0]
    assert top == ("CO-2100", "Main Detention Center", "ATT")


def test_deliberate_data_quality_defects_are_present(export):
    injected = export.manifest["injected_data_quality_issues"]
    assert sum(injected.values()) == 13
    assert set(injected) == {
        "orphan_action",
        "suspension_days_mismatch",
        "decision_before_incident",
        "missing_salary",
        "leave_after_separation",
        "sworn_only_category_misapplied",
    }


def test_no_action_is_decided_after_the_employee_left(export):
    """The generator prunes these; if it stops doing so the adapter would flag them."""
    from datetime import datetime

    def parse(text):
        return datetime.strptime(text, "%m/%d/%Y").date() if text else None

    by_id = {r["EMP_NBR"]: r for r in export.employees}
    for row in export.actions:
        employee = by_id.get(row["EMP_NBR"])
        if employee and employee["TERM_DT"]:
            assert parse(row["DECN_DT"]) <= parse(employee["TERM_DT"]), row["ACTN_NBR"]


def test_pending_appeals_exist_to_exercise_the_incomplete_flag(export):
    pending = sum(1 for r in export.appeals if r["OUTCOME_CD"] == "PND")
    assert pending >= 3
