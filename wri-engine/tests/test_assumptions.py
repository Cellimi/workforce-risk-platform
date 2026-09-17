"""The assumptions registry: a missing coefficient must be a hard error, never a default."""

from __future__ import annotations

from decimal import Decimal

import pytest
import yaml

from wri_engine.costing.assumptions import (
    Assumption,
    MissingAssumptionError,
    load_assumptions,
)

BASE = {
    "id": "x", "value": 10, "low": 5, "high": 20, "unit": "hours",
    "applies_to": {"scope": "test"}, "source": "test fixture",
    "confidence": "medium", "owner": "Mike Celli", "status": "TBD-MIKE",
    "last_reviewed": "2026-09-17", "notes": "",
}


def write(tmp_path, entries):
    path = tmp_path / "assumptions.yaml"
    path.write_text(yaml.safe_dump({"assumptions": entries}))
    return path


def test_missing_id_raises_rather_than_defaulting(assumptions):
    with pytest.raises(MissingAssumptionError) as exc:
        assumptions.value("c5_academy_weeks_astronaut")
    assert "never falls back to a default" in str(exc.value)


def test_require_all_names_every_missing_id(assumptions):
    with pytest.raises(MissingAssumptionError) as exc:
        assumptions.require_all(["benefits_multiplier_civilian", "nope_one", "nope_two"])
    message = str(exc.value)
    assert "nope_one" in message and "nope_two" in message
    assert "benefits_multiplier_civilian" not in message


def test_bounds_must_be_ordered():
    with pytest.raises(ValueError, match="low <= value <= high"):
        Assumption(**{**BASE, "low": 15})


def test_source_is_required():
    with pytest.raises(ValueError, match="source is required"):
        Assumption(**{**BASE, "source": "   "})


def test_status_and_confidence_are_constrained():
    with pytest.raises(ValueError, match="status"):
        Assumption(**{**BASE, "status": "probably fine"})
    with pytest.raises(ValueError, match="confidence"):
        Assumption(**{**BASE, "confidence": "vibes"})


def test_duplicate_ids_are_rejected(tmp_path):
    path = write(tmp_path, [BASE, {**BASE, "value": 12}])
    with pytest.raises(ValueError, match="duplicate assumption id"):
        load_assumptions(path)


def test_modes_select_the_right_bound(tmp_path):
    loaded = load_assumptions(write(tmp_path, [BASE]))
    assert loaded.with_mode("low").value("x") == Decimal("5")
    assert loaded.with_mode("base").value("x") == Decimal("10")
    assert loaded.with_mode("high").value("x") == Decimal("20")


def test_an_invalid_mode_is_rejected(tmp_path):
    loaded = load_assumptions(write(tmp_path, [BASE]))
    with pytest.raises(ValueError, match="mode must be one of"):
        loaded.with_mode("optimistic")


def test_overrides_win_over_the_mode_and_do_not_mutate(tmp_path):
    loaded = load_assumptions(write(tmp_path, [BASE]))
    scenario = loaded.with_mode("high").with_overrides({"x": 7})
    assert scenario.value("x") == Decimal("7")
    assert loaded.value("x") == Decimal("10")          # original untouched
    assert scenario.as_records()[0]["overridden"] is True


def test_overrides_reject_unknown_ids(assumptions):
    with pytest.raises(MissingAssumptionError, match="unknown assumptions"):
        assumptions.with_overrides({"not_a_real_assumption": 1})


def test_yaml_floats_survive_as_exact_decimals(assumptions):
    """1.627 must stay 1.627, not 1.6269999999999999."""
    assert assumptions.value("benefits_multiplier_civilian") == Decimal("1.627")


def test_placeholders_are_listed(assumptions):
    ids = {a.id for a in assumptions.placeholders}
    assert "benefits_multiplier_sworn" in ids
    assert "benefits_multiplier_civilian" not in ids


def test_records_expose_the_effective_value(assumptions):
    record = next(
        r for r in assumptions.as_records() if r["id"] == "c3_ot_premium_multiplier"
    )
    assert record["effective_value"] == "1.5"
    assert record["status"] == "confirmed"
