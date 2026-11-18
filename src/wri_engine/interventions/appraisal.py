"""The cost-benefit of one intervention for one unit.

The question a consultant takes to a client's leadership, once someone has been on site and
found out what is going on in a flagged unit:

    It costs you X a year to discipline people here for this.
    Doing something about it would cost Y.
    If it works as well as we expect, you come out Z ahead.

**X comes from the cost baseline.** It is what the records show this unit's actions of this
type cost over the period, divided by the length of the period. Nothing about it is
estimated here.

**Y is what the consultant says the intervention costs**: a one-time cost, a yearly cost for
each person covered, any other yearly cost, and how many years to look ahead.

**How well it works is the consultant's judgement, and it is entered three times**: a low,
an expected and a high share of the actions it would prevent, with the basis for those
figures in words. An appraisal has no default for them. The product has no evidence of its
own on how well anything works: the only figures it offers are the library's (`library.py`),
which a person must choose, and which are placeholders until the owner has researched them
and are marked as such wherever an appraisal that keeps one is shown.

**What it is aimed at.** An intervention may be aimed at some types of misconduct only: a
transit subsidy at absences and lateness, not at insubordination. Then X is the cost of the
unit's actions of those types alone, and only those can be prevented. Aimed at nothing in
particular, it is aimed at every type the unit was disciplined for.

**Z is then arithmetic**, done three times:

    prevented cost a year  = X x share of actions prevented
    prevented, in all      = prevented cost a year x years
    Y                      = one-time cost + yearly cost x years
    Z, net saving          = prevented, in all - Y

and one more figure that needs no judgement at all: the **break-even share**, Y divided by
(X x years). It is the share of these actions the intervention would have to prevent to pay
for itself. If that is more than anyone would claim for it, the three scenarios do not
matter.

What this rests on, stated wherever a figure appears
-----------------------------------------------------
* **It is a projection, and no part of the cost baseline.** The baseline counts what
  happened. This says what might. No figure here is added to or netted against a baseline
  figure.
* Without the intervention, the unit is assumed to go on costing what it cost a year over
  the period.
* A prevented action is assumed to cost what this unit's actions of this type cost on
  average. If the intervention prevents mostly the minor ones, the saving is less.
* Money in later years counts the same as money now: nothing is discounted.
* Only the cost of discipline is counted as a benefit. Whatever else the intervention
  improves is not.

Nothing here says why the unit's rate is what it is, and nothing is about any one person.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from wri_engine.costing.engine import CostRun

CENT = Decimal("0.01")
SHARE = Decimal("0.0001")
ZERO = Decimal(0)
ONE = Decimal(1)
ROWS, COLS, TOPIC = "role_family", "work_location", "misconduct_category"
#: How far ahead an appraisal may look. A limit on what can be typed, not a coefficient.
MAX_YEARS = 10

ASSUMES = (
    "A projection, and no part of the cost baseline.",
    "Without the intervention the unit is assumed to go on costing what it cost a year "
    "over the period.",
    "A prevented action is assumed to cost what this unit's actions of this type cost on average.",
    "Nothing is discounted: money in later years counts the same as money now.",
    "Only the cost of discipline is counted as a benefit.",
)


class InvalidIntervention(ValueError):
    """What was entered cannot be appraised. `problems` says why, one line each."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def _money(value: Decimal) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value.quantize(CENT)):,.2f}"


def _percent(share: Decimal) -> str:
    return f"{(share * 100).normalize():f}%"


@dataclass(frozen=True)
class UnitBaseline:
    """What the cost baseline shows for one unit: X, and what it is made of."""

    row: str
    col: str
    row_label: str
    col_label: str
    #: The type of misconduct, or empty for every type together.
    topic: str
    topic_label: str
    actions: int
    people: int
    net: Decimal
    window_years: Decimal
    #: Who the people are. Used only to decide whether the unit's cost may go into a
    #: client's copy of a report; it never leaves the server.
    cohort: frozenset[str] = field(default=frozenset(), repr=False, compare=False)
    #: The same, by type of misconduct: (actions, net cost, who). What an intervention
    #: aimed at some types only can prevent is worked out from these.
    by_type: dict[str, tuple[int, Decimal, frozenset[str]]] = field(
        default_factory=dict, repr=False, compare=False
    )
    type_labels: dict[str, str] = field(default_factory=dict, repr=False, compare=False)

    def aimed(self, targets: tuple[str, ...]) -> tuple[Decimal, Decimal, frozenset[str]]:
        """Actions a year, net cost a year, and who, for the types an intervention is
        aimed at. Aimed at nothing in particular: the whole unit."""
        if not targets:
            return self.annual_actions, self.annual_net, self.cohort
        hit = [self.by_type[t] for t in targets if t in self.by_type]
        actions = sum((a for a, _, _ in hit), 0)
        net = sum((n for _, n, _ in hit), ZERO)
        who = frozenset().union(*(c for _, _, c in hit)) if hit else frozenset()
        return Decimal(actions) / self.window_years, net / self.window_years, who

    def label_of(self, targets: tuple[str, ...]) -> str:
        """The types an intervention is aimed at, as a reader would say them."""
        names = [self.type_labels.get(t, t) for t in targets]
        return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]

    @property
    def annual_net(self) -> Decimal:
        return self.net / self.window_years

    @property
    def annual_actions(self) -> Decimal:
        return Decimal(self.actions) / self.window_years

    @property
    def name(self) -> str:
        where = f"{self.row_label} at {self.col_label}"
        return f"{self.topic_label} among {where}" if self.topic_label else where

    @property
    def formula(self) -> str:
        return (
            f"{_money(self.net)} net cost of {self.actions} actions in the cost baseline "
            f"/ {self.window_years} years"
        )

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "col": self.col,
            "row_label": self.row_label,
            "col_label": self.col_label,
            "topic": self.topic,
            "topic_label": self.topic_label,
            "name": self.name,
            "actions": self.actions,
            "people": self.people,
            "net": str(self.net.quantize(CENT)),
            "window_years": str(self.window_years),
            "annual_actions": str(self.annual_actions.quantize(Decimal("0.1"))),
            "annual_net": str(self.annual_net.quantize(CENT)),
            "formula": self.formula,
        }


def unit_baseline(
    run: CostRun, row: str, col: str, topic: str, window_years: Decimal
) -> UnitBaseline | None:
    """The unit's actions in a costing run, or None when it has none. `topic` empty means
    every type of misconduct together."""
    theirs = [
        action
        for action in run.action_costs
        if str(action.dimensions[ROWS]) == row
        and str(action.dimensions[COLS]) == col
        and (not topic or str(action.dimensions[TOPIC]) == topic)
    ]
    if not theirs:
        return None
    first = theirs[0].dimensions
    by_type: dict[str, tuple[int, Decimal, frozenset[str]]] = {}
    for action in theirs:
        kind = str(action.dimensions[TOPIC])
        actions, net, who = by_type.get(kind, (0, ZERO, frozenset()))
        by_type[kind] = (actions + 1, net + action.net, who | {action.employee_id})
    # Labels from the whole run, so that a type this unit was never disciplined for is
    # still named as a reader would name it.
    type_labels: dict[str, str] = {}
    for action in run.action_costs:
        kind = str(action.dimensions[TOPIC])
        type_labels.setdefault(kind, str(action.dimensions.get(f"{TOPIC}_label", kind)))
    return UnitBaseline(
        row=row,
        col=col,
        row_label=str(first.get(f"{ROWS}_label", row)),
        col_label=col,
        topic=topic,
        topic_label=str(first.get(f"{TOPIC}_label", topic)) if topic else "",
        actions=len(theirs),
        people=len({action.employee_id for action in theirs}),
        net=sum((action.net for action in theirs), ZERO),
        window_years=window_years,
        cohort=frozenset(action.employee_id for action in theirs),
        by_type=by_type,
        type_labels=type_labels,
    )


@dataclass(frozen=True)
class Intervention:
    """What the consultant proposes, in their own figures."""

    name: str
    one_time_cost: Decimal
    annual_cost_per_person: Decimal
    people_covered: int
    other_annual_cost: Decimal
    years: int
    #: Shares of the unit's actions it would prevent: 0.25 is a quarter of them.
    reduction_low: Decimal
    reduction_expected: Decimal
    reduction_high: Decimal
    #: Where those three figures come from, in the consultant's words.
    basis: str
    #: A one-time cost for each person covered, beside the lump sum above.
    one_time_cost_per_person: Decimal = ZERO
    #: The library entry the form was started from, if any, and which figures still stand
    #: as that entry gave them. Set by the server, never taken from a caller: see
    #: `library.py`. They change no arithmetic; they decide what is said beside the result.
    library_id: str = ""
    library_fields: tuple[str, ...] = ()
    #: The types of misconduct it is aimed at, by id. Empty: every type the unit has.
    targets: tuple[str, ...] = ()

    def problems(self) -> list[str]:
        found = []
        if not self.name.strip():
            found.append("give the intervention a name")
        for label, amount in (
            ("the one-time cost", self.one_time_cost),
            ("the one-time cost per person", self.one_time_cost_per_person),
            ("the yearly cost per person", self.annual_cost_per_person),
            ("the other yearly cost", self.other_annual_cost),
        ):
            if amount < 0:
                found.append(f"{label} cannot be negative")
        if self.people_covered < 0:
            found.append("the number of people covered cannot be negative")
        if not 1 <= self.years <= MAX_YEARS:
            found.append(f"look ahead between 1 and {MAX_YEARS} years")
        shares = (self.reduction_low, self.reduction_expected, self.reduction_high)
        if any(share < 0 or share > 1 for share in shares):
            found.append("each share of actions prevented must be between 0% and 100%")
        elif not self.reduction_low <= self.reduction_expected <= self.reduction_high:
            found.append(
                "the low share cannot be above the expected, nor the expected above the high"
            )
        if not self.basis.strip():
            found.append(
                "say what the shares of actions prevented are based on: this product has no "
                "evidence of its own for how well an intervention works"
            )
        return found

    @property
    def annual_cost(self) -> Decimal:
        return self.annual_cost_per_person * self.people_covered + self.other_annual_cost

    @property
    def one_time_total(self) -> Decimal:
        return self.one_time_cost + self.one_time_cost_per_person * self.people_covered

    @property
    def total_cost(self) -> Decimal:
        return self.one_time_total + self.annual_cost * self.years

    @property
    def cost_formula(self) -> str:
        once = f"{_money(self.one_time_cost)} one-time"
        if self.one_time_cost_per_person:
            once += (
                f" + {_money(self.one_time_cost_per_person)} x {self.people_covered} people "
                f"one-time"
            )
        return (
            f"{once} + ({_money(self.annual_cost_per_person)} "
            f"x {self.people_covered} people + {_money(self.other_annual_cost)} other) a "
            f"year x {self.years} years"
        )

    def as_dict(self) -> dict:
        return {
            "name": self.name.strip(),
            "one_time_cost": str(self.one_time_cost.quantize(CENT)),
            "one_time_cost_per_person": str(self.one_time_cost_per_person.quantize(CENT)),
            "annual_cost_per_person": str(self.annual_cost_per_person.quantize(CENT)),
            "people_covered": self.people_covered,
            "other_annual_cost": str(self.other_annual_cost.quantize(CENT)),
            "years": self.years,
            "annual_cost": str(self.annual_cost.quantize(CENT)),
            "total_cost": str(self.total_cost.quantize(CENT)),
            "cost_formula": self.cost_formula,
            "basis": self.basis.strip(),
            "library_id": self.library_id,
            "library_fields": list(self.library_fields),
            "targets": list(self.targets),
        }


@dataclass(frozen=True)
class Scenario:
    """One reading of how well the intervention works."""

    label: str
    reduction: Decimal
    actions_prevented_per_year: Decimal
    prevented_per_year: Decimal
    prevented_total: Decimal
    net_saving: Decimal
    formula: str

    def as_dict(self) -> dict:
        return {
            "label": self.label,
            "reduction": str(self.reduction),
            "actions_prevented_per_year": str(
                self.actions_prevented_per_year.quantize(Decimal("0.1"))
            ),
            "prevented_per_year": str(self.prevented_per_year.quantize(CENT)),
            "prevented_total": str(self.prevented_total.quantize(CENT)),
            "net_saving": str(self.net_saving.quantize(CENT)),
            "pays_for_itself": self.net_saving >= 0,
            "formula": self.formula,
        }


@dataclass(frozen=True)
class Appraisal:
    baseline: UnitBaseline
    intervention: Intervention
    scenarios: tuple[Scenario, ...]
    #: The share of actions it must prevent to pay for itself. None when the unit's
    #: discipline has no net cost to save.
    break_even: Decimal | None
    assumes: tuple[str, ...] = ASSUMES

    @property
    def expected(self) -> Scenario:
        return self.scenarios[1]

    @property
    def targets(self) -> tuple[str, ...]:
        return self.intervention.targets

    @property
    def aimed_annual_net(self) -> Decimal:
        return self.baseline.aimed(self.targets)[1]

    @property
    def aimed_label(self) -> str:
        return self.baseline.label_of(self.targets) if self.targets else ""

    @property
    def break_even_note(self) -> str:
        yearly = self.aimed_annual_net
        these = f"the {self.aimed_label} actions" if self.targets else "these actions"
        if self.break_even is None:
            if self.targets and not self.baseline.aimed(self.targets)[0]:
                return (
                    f"None of this unit's discipline is of the types this intervention is "
                    f"aimed at ({self.aimed_label}), so there is nothing here for it to "
                    f"prevent."
                )
            return (
                "The cost baseline shows no net cost for these actions, so there is no cost "
                "of discipline for an intervention to save here."
            )
        cost = _money(self.intervention.total_cost)
        years = self.intervention.years
        if self.break_even > ONE:
            return (
                f"It cannot pay for itself on the cost of discipline alone: it costs {cost} "
                f"over {years} years and {these} cost {_money(yearly * years)} in that time."
            )
        return (
            f"It pays for itself if it prevents {_percent(self.break_even.quantize(SHARE))} "
            f"of {these}: {cost} / ({_money(yearly)} a year x {years} years)."
        )

    def aimed_dict(self) -> dict | None:
        """What it is aimed at, and what that costs. None when aimed at the whole unit."""
        if not self.targets:
            return None
        actions, net, _ = self.baseline.aimed(self.targets)
        return {
            "types": list(self.targets),
            "label": self.aimed_label,
            "annual_actions": str(actions.quantize(Decimal("0.1"))),
            "annual_net": str(net.quantize(CENT)),
        }

    def as_dict(self) -> dict:
        return {
            "aimed_at": self.aimed_dict(),
            "unit": self.baseline.as_dict(),
            "intervention": self.intervention.as_dict(),
            "scenarios": [scenario.as_dict() for scenario in self.scenarios],
            "break_even": None if self.break_even is None else str(self.break_even.quantize(SHARE)),
            "break_even_note": self.break_even_note,
            "assumes": list(self.assumes),
        }


def appraise(baseline: UnitBaseline, intervention: Intervention) -> Appraisal:
    """Set what an intervention costs against what the unit's discipline costs.

    Raises `InvalidIntervention` when what was entered cannot be appraised.
    """
    problems = intervention.problems()
    if problems:
        raise InvalidIntervention(problems)
    years = intervention.years
    total_cost = intervention.total_cost
    yearly_actions, yearly, _ = baseline.aimed(intervention.targets)
    what = f" of {baseline.label_of(intervention.targets)} actions" if intervention.targets else ""
    scenarios = []
    for label, share in (
        ("Low", intervention.reduction_low),
        ("Expected", intervention.reduction_expected),
        ("High", intervention.reduction_high),
    ):
        prevented = yearly * share
        scenarios.append(
            Scenario(
                label=label,
                reduction=share,
                actions_prevented_per_year=yearly_actions * share,
                prevented_per_year=prevented,
                prevented_total=prevented * years,
                net_saving=prevented * years - total_cost,
                formula=(
                    f"{_money(yearly)} a year{what} x {_percent(share)} prevented x {years} years "
                    f"- {_money(total_cost)} cost of the intervention"
                ),
            )
        )
    break_even = total_cost / (yearly * years) if yearly > 0 else None
    return Appraisal(baseline, intervention, tuple(scenarios), break_even)
