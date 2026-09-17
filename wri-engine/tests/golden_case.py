"""Loader for the hand-calculated golden cases in `tests/golden/`.

A golden case is a tiny canonical dataset -- one or two employees, one action, and whatever
leave, appeal and separation records the case is about -- plus the exact line items a person
worked out by hand. The test asserts every line item and every total, to the cent.

The YAML carries the hand calculation in its `hand_calculation` block. When a golden case
fails, read that block first: either the arithmetic or the code is wrong, and the block says
what the answer is supposed to be.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import yaml

from wri_engine.costing.assumptions import AssumptionSet
from wri_engine.costing.engine import ActionCost, cost_one
from wri_engine.orgconfig import OrgConfig
from wri_engine.schema import (
    AdminLeavePeriod,
    AppealOrGrievance,
    CanonicalDataset,
    DisciplineAction,
    Employee,
    Separation,
)

GOLDEN_DIR = Path(__file__).parent / "golden"

EMPLOYEE_DEFAULTS = {
    "job_title": "Employee",
    "pay_step": 1,
    "supervisor_id": None,
    "separation_date": None,
    "separation_reason": None,
}


@dataclass
class GoldenCase:
    path: Path
    raw: dict

    @property
    def name(self) -> str:
        return self.raw["name"]

    @property
    def as_of(self) -> date:
        return date.fromisoformat(str(self.raw["as_of"]))

    def dataset(self) -> CanonicalDataset:
        employees = [
            Employee(
                **{
                    **EMPLOYEE_DEFAULTS,
                    **_decimalize(
                        e, ("annual_base_salary", "hourly_base_rate", "standard_shift_hours")
                    ),
                }
            )
            for e in self.raw["employees"]
        ]
        actions = [DisciplineAction(**a) for a in self.raw.get("actions", [])]
        leave = [AdminLeavePeriod(**lv) for lv in self.raw.get("admin_leave", [])]
        appeals = [
            AppealOrGrievance(
                **_decimalize(
                    ap, ("back_pay_awarded", "settlement_amount", "outside_counsel_hours")
                )
            )
            for ap in self.raw.get("appeals", [])
        ]
        separations = [Separation(**s) for s in self.raw.get("separations", [])]
        return CanonicalDataset(
            employees=employees,
            actions=actions,
            leave_periods=leave,
            appeals=appeals,
            separations=separations,
        )

    def compute(self, org: OrgConfig, assumptions: AssumptionSet) -> ActionCost | None:
        return cost_one(
            self.raw["action_under_test"],
            self.dataset(),
            as_of=self.as_of,
            org=org,
            assumptions=assumptions,
        )

    @property
    def expected_items(self) -> list[tuple[str, str, Decimal]]:
        return sorted(
            (i["component"], i["subcomponent"], Decimal(str(i["amount"])))
            for i in self.raw["expected"]["line_items"]
        )

    def expected_total(self, key: str) -> Decimal:
        return Decimal(str(self.raw["expected"][key]))


def _decimalize(raw: dict, keys: tuple[str, ...]) -> dict:
    out = dict(raw)
    for key in keys:
        if key in out and out[key] is not None:
            out[key] = Decimal(str(out[key]))
    return out


def load_cases() -> list[GoldenCase]:
    return [
        GoldenCase(path=p, raw=yaml.safe_load(p.read_text()))
        for p in sorted(GOLDEN_DIR.glob("*.yaml"))
    ]
