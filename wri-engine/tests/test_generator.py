"""Generator determinism and the distribution targets it is tuned to."""

from __future__ import annotations

from collections import Counter
from datetime import date

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


def test_suspension_appeal_rate_on_the_demo_seed(export):
    """Smoke check on one seed. `test_appeal_rates_pooled_across_seeds` is the authoritative
    one -- a single seed's rate carries real sampling error and a tight band here would flake
    on any harmless change to the generator."""
    appealed = {r["ACTN_NBR"] for r in export.appeals}
    suspensions = [r for r in export.actions if r["ACTN_CD"].startswith("SP")]
    rate = sum(1 for r in suspensions if r["ACTN_NBR"] in appealed) / len(suspensions)
    assert 0.10 <= rate <= 0.30, f"suspension appeal rate {rate:.1%} is far outside target"


# ---------------------------------------------------------------------------
# Appeal rates, measured the only way that is meaningful
# ---------------------------------------------------------------------------
#
# Two things make a naive appeal-rate assertion misleading, and this test handles both.
#
# 1. ACTIONS WHOSE FILING WINDOW IS STILL OPEN have not had their chance to be appealed.
#    Counting them drags the realized rate below the configured probability for no good
#    reason, so they are excluded: only actions decided at least
#    `timing.decision_to_appeal_filed.max` days before the extract date are in scope.
#
# 2. ONE SEED IS NOT A MEASUREMENT. There are only ~30 removals per seed, so the standard
#    deviation on the removal rate is about 9 percentage points. A single seed sitting two
#    or three sigma off the parameter is ordinary sampling noise, not a bug -- the demo seed
#    20260917 is exactly such a draw, at 36% against a 60% parameter. Pooling five fixed
#    seeds gets n above 150 and makes the assertion mean something.
#
# If this test fails, the generator's draw is genuinely wrong. If a single seed looks wrong
# but this passes, the seed is unlucky and the parameter is fine.

APPEAL_RATE_SEEDS = (20260917, 1, 2, 3, 4)


def _closed_window_appeal_rates(seed: int) -> tuple[tuple[int, int], tuple[int, int]]:
    """(removals appealed, removals), (suspensions appealed, suspensions) for one seed,
    counting only actions whose filing window has closed by the extract date."""
    from datetime import datetime, timedelta

    import yaml

    from wri_engine.paths import GENERATOR_PROFILE_FILE

    profile = yaml.safe_load(GENERATOR_PROFILE_FILE.read_text())
    as_of = date.fromisoformat(str(profile["horizon"]["as_of_date"]))
    window = int(profile["timing"]["decision_to_appeal_filed"]["max"])
    cutoff = as_of - timedelta(days=window)

    export = generate(seed=seed)
    appealed = {r["ACTN_NBR"] for r in export.appeals}

    def closed(rows):
        return [r for r in rows if datetime.strptime(r["DECN_DT"], "%m/%d/%Y").date() <= cutoff]

    removals = closed([r for r in export.actions if r["ACTN_CD"] == "RMV"])
    suspensions = closed([r for r in export.actions if r["ACTN_CD"].startswith("SP")])
    return (
        (sum(1 for r in removals if r["ACTN_NBR"] in appealed), len(removals)),
        (sum(1 for r in suspensions if r["ACTN_NBR"] in appealed), len(suspensions)),
    )


def test_appeal_rates_pooled_across_seeds():
    removals_appealed = removals = suspensions_appealed = suspensions = 0
    for seed in APPEAL_RATE_SEEDS:
        (ra, r), (sa, s) = _closed_window_appeal_rates(seed)
        removals_appealed += ra
        removals += r
        suspensions_appealed += sa
        suspensions += s

    assert removals >= 150, f"only {removals} removals pooled; the band would be meaningless"
    removal_rate = removals_appealed / removals
    suspension_rate = suspensions_appealed / suspensions

    # profile sets removals at 0.60 and suspensions at roughly 0.18 once blended.
    assert 0.48 <= removal_rate <= 0.72, (
        f"pooled removal appeal rate {removal_rate:.1%} over {removals} removals is outside "
        f"the 48-72% band around the configured 60%. This is wide enough that sampling noise "
        f"will not trip it, so the draw itself is wrong."
    )
    assert 0.12 <= suspension_rate <= 0.24, (
        f"pooled suspension appeal rate {suspension_rate:.1%} over {suspensions} "
        f"suspensions is outside the 12-24% band around the configured ~18%."
    )


def test_open_filing_windows_are_not_counted_against_the_rate():
    """The exclusion in the test above must actually exclude something, or it is decoration."""
    from datetime import datetime, timedelta

    import yaml

    from wri_engine.paths import GENERATOR_PROFILE_FILE

    profile = yaml.safe_load(GENERATOR_PROFILE_FILE.read_text())
    as_of = date.fromisoformat(str(profile["horizon"]["as_of_date"]))
    window = int(profile["timing"]["decision_to_appeal_filed"]["max"])
    cutoff = as_of - timedelta(days=window)

    export = generate(seed=20260917)
    still_open = [
        r for r in export.actions if datetime.strptime(r["DECN_DT"], "%m/%d/%Y").date() > cutoff
    ]
    assert still_open, (
        "no action in the dataset is inside an open filing window, so the engine's "
        "open-window flag logic has nothing to exercise"
    )


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
