"""Several interventions for one unit, side by side.

An investigation rarely ends with one idea. When more than one appraisal has been kept for
the same unit and the same type of misconduct, this lays them next to each other so that
whoever has to choose can see them on the same terms.

**The same terms means per year.** One proposal may look three years ahead and another
five, and their totals cannot be compared. So each is put as a yearly cost (its whole cost
divided by its years) and a yearly net saving (the cost of discipline it would prevent in a
year, less that yearly cost).

**They are listed by the share they must prevent to pay for themselves, lowest first.**
That figure needs no judgement about how well anything works: it is what the intervention
costs against what the discipline costs. The expected saving is shown beside it, but it
rests on each proposal's own estimate, and two estimates made by different people on
different evidence are not ranked against each other here. Nothing is named the best.

**The savings cannot be added up.** These are alternatives. Two interventions aimed at the
same actions do not prevent the sum of what each would prevent alone, and no combined
figure is offered.

Nothing here changes an appraisal: every figure is one from `appraisal.py`, or that figure
divided by the proposal's years.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from wri_engine.interventions.appraisal import CENT, SHARE, Appraisal, UnitBaseline

NOTES = (
    "Listed by the share of actions each must prevent to pay for itself, lowest first: the "
    "one figure here that needs no judgement about how well anything works.",
    "Yearly figures are each proposal's own totals divided by its own years, so that "
    "proposals looking different distances ahead can be read together.",
    "These are alternatives. Their savings cannot be added: two interventions aimed at the "
    "same actions do not prevent the sum of what each would prevent alone.",
    "Each expected saving rests on that proposal's own estimate of how well it works. A "
    "saving is a projection and no part of the cost baseline.",
)


@dataclass(frozen=True)
class ComparedRow:
    """One intervention, on yearly terms."""

    #: Whatever the caller uses to tell appraisals apart: the id it was kept under.
    key: str
    name: str
    years: int
    total_cost: Decimal
    yearly_cost: Decimal
    #: Net saving a year at the low, expected and high reading, in that order.
    yearly_net_savings: tuple[Decimal, Decimal, Decimal]
    reductions: tuple[Decimal, Decimal, Decimal]
    #: The share of actions it must prevent to pay for itself. None when the unit's
    #: discipline has no net cost to save.
    break_even: Decimal | None

    def as_dict(self) -> dict:
        low, expected, high = (str(v.quantize(CENT)) for v in self.yearly_net_savings)
        return {
            "key": self.key,
            "name": self.name,
            "years": self.years,
            "total_cost": str(self.total_cost.quantize(CENT)),
            "yearly_cost": str(self.yearly_cost.quantize(CENT)),
            "yearly_net_saving_low": low,
            "yearly_net_saving_expected": expected,
            "yearly_net_saving_high": high,
            "reductions": [str(r) for r in self.reductions],
            "break_even": None if self.break_even is None else str(self.break_even.quantize(SHARE)),
            "can_pay_for_itself": self.break_even is not None and self.break_even <= 1,
        }


@dataclass(frozen=True)
class UnitComparison:
    """The interventions kept for one unit, on the same terms."""

    baseline: UnitBaseline
    rows: tuple[ComparedRow, ...]
    notes: tuple[str, ...] = NOTES

    def as_dict(self) -> dict:
        return {
            "unit": self.baseline.as_dict(),
            "rows": [row.as_dict() for row in self.rows],
            "notes": list(self.notes),
        }


def _row(key: str, appraisal: Appraisal) -> ComparedRow:
    proposal = appraisal.intervention
    years = Decimal(proposal.years)
    return ComparedRow(
        key=key,
        name=proposal.name,
        years=proposal.years,
        total_cost=proposal.total_cost,
        yearly_cost=proposal.total_cost / years,
        yearly_net_savings=tuple(s.net_saving / years for s in appraisal.scenarios),
        reductions=tuple(s.reduction for s in appraisal.scenarios),
        break_even=appraisal.break_even,
    )


def compare(appraisals: list[tuple[str, Appraisal]]) -> list[UnitComparison]:
    """Group appraisals by unit and type of misconduct, and lay out every group of two or
    more. `appraisals` pairs each with the key the caller knows it by.

    Within a group the order is by break-even share, lowest first, then by name; a
    proposal with no break-even share (the unit's discipline has no net cost) comes last.
    Groups are in the order their first appraisal was given.
    """
    groups: dict[tuple[str, str, str], list[tuple[str, Appraisal]]] = {}
    for key, appraisal in appraisals:
        unit = appraisal.baseline
        groups.setdefault((unit.row, unit.col, unit.topic), []).append((key, appraisal))
    compared = []
    for members in groups.values():
        if len(members) < 2:
            continue
        rows = sorted(
            (_row(key, appraisal) for key, appraisal in members),
            key=lambda r: (r.break_even is None, r.break_even or Decimal(0), r.name, r.key),
        )
        compared.append(UnitComparison(members[0][1].baseline, tuple(rows)))
    return compared
