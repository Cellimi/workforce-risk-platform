"""Loader and accessor for `config/assumptions.yaml`.

Rules this module enforces, because the cost numbers are only as trustworthy as the
coefficients behind them:

* Every id is unique, and `low <= value <= high`.
* Asking for an id that is not in the file is a hard error, never a silent default.
* A run happens in exactly one sensitivity mode -- low, base or high.
* A scenario may override values for one run without touching the file on disk.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

from wri_engine.paths import ASSUMPTIONS_FILE

Mode = Literal["low", "base", "high"]
VALID_MODES: tuple[Mode, ...] = ("low", "base", "high")
VALID_STATUSES = ("confirmed", "researched", "TBD-MIKE")
VALID_CONFIDENCE = ("high", "medium", "low")


class MissingAssumptionError(KeyError):
    """Raised when the engine asks for a coefficient that is not in the registry."""


class Assumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    value: Decimal
    low: Decimal
    high: Decimal
    unit: str
    applies_to: dict[str, Any]
    source: str
    confidence: str
    owner: str
    status: str
    last_reviewed: str
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> Assumption:
        if not self.low <= self.value <= self.high:
            raise ValueError(
                f"assumption {self.id}: expected low <= value <= high, "
                f"got {self.low} / {self.value} / {self.high}"
            )
        if self.confidence not in VALID_CONFIDENCE:
            raise ValueError(f"assumption {self.id}: confidence {self.confidence!r} is not valid")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"assumption {self.id}: status {self.status!r} is not valid")
        if not self.source.strip():
            raise ValueError(f"assumption {self.id}: source is required")
        return self

    def at(self, mode: Mode) -> Decimal:
        return {"low": self.low, "base": self.value, "high": self.high}[mode]

    @property
    def is_placeholder(self) -> bool:
        return self.status == "TBD-MIKE"


@dataclass(frozen=True)
class AssumptionSet:
    """An immutable view of the registry at one sensitivity mode, with optional overrides."""

    assumptions: dict[str, Assumption]
    mode: Mode = "base"
    overrides: dict[str, Decimal] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.mode not in VALID_MODES:
            raise ValueError(f"mode must be one of {VALID_MODES}, got {self.mode!r}")
        if self.overrides is None:
            object.__setattr__(self, "overrides", {})

    def __contains__(self, assumption_id: str) -> bool:
        return assumption_id in self.assumptions

    def get(self, assumption_id: str) -> Assumption:
        try:
            return self.assumptions[assumption_id]
        except KeyError:
            raise MissingAssumptionError(
                f"assumption {assumption_id!r} is not defined in the registry. "
                f"Add it to config/assumptions.yaml -- the engine never falls back to a default."
            ) from None

    def value(self, assumption_id: str) -> Decimal:
        """The coefficient to use for this run. Overrides win over the mode."""
        assumption = self.get(assumption_id)
        if assumption_id in self.overrides:
            return Decimal(str(self.overrides[assumption_id]))
        return assumption.at(self.mode)

    def require_all(self, assumption_ids: list[str]) -> None:
        missing = sorted({a for a in assumption_ids if a not in self.assumptions})
        if missing:
            raise MissingAssumptionError(
                "the engine references assumptions that are not in the registry: "
                + ", ".join(missing)
            )

    def with_mode(self, mode: Mode) -> AssumptionSet:
        return AssumptionSet(self.assumptions, mode, dict(self.overrides))

    def with_overrides(self, overrides: dict[str, Any]) -> AssumptionSet:
        """Return a new set with scenario overrides applied. Unknown ids are rejected."""
        unknown = sorted(set(overrides) - set(self.assumptions))
        if unknown:
            raise MissingAssumptionError(
                "scenario overrides reference unknown assumptions: " + ", ".join(unknown)
            )
        merged = dict(self.overrides)
        merged.update({k: Decimal(str(v)) for k, v in overrides.items()})
        return AssumptionSet(self.assumptions, self.mode, merged)

    @property
    def placeholders(self) -> list[Assumption]:
        return [a for a in self.assumptions.values() if a.is_placeholder]

    def as_records(self) -> list[dict]:
        """Serializable view for the API and the Assumptions page."""
        return [
            {
                **a.model_dump(mode="json"),
                "effective_value": str(self.value(a.id)),
                "overridden": a.id in self.overrides,
            }
            for a in self.assumptions.values()
        ]


def _decimalize(raw: dict) -> dict:
    """YAML gives floats; convert through str so 1.627 stays 1.627 and not 1.62700000000001."""
    out = dict(raw)
    for key in ("value", "low", "high"):
        if key in out and out[key] is not None:
            out[key] = Decimal(str(out[key]))
    return out


def load_assumptions(path: Path | None = None, mode: Mode = "base") -> AssumptionSet:
    path = path or ASSUMPTIONS_FILE
    with open(path) as fh:
        raw = yaml.safe_load(fh)
    entries = raw["assumptions"]
    by_id: dict[str, Assumption] = {}
    for entry in entries:
        assumption = Assumption(**_decimalize(entry))
        if assumption.id in by_id:
            raise ValueError(f"duplicate assumption id {assumption.id!r} in {path}")
        by_id[assumption.id] = assumption
    return AssumptionSet(by_id, mode)


@functools.lru_cache(maxsize=8)
def default_assumptions(mode: Mode = "base") -> AssumptionSet:
    return load_assumptions(mode=mode)
