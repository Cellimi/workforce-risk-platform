"""Hotspots: units whose discipline rate stands out from the same job elsewhere.

A **unit** is a job at a place: corrections officers at the main detention center, equipment
operators at the north yard. For each unit this asks one question: over the period, were
there more discipline actions per employee-year here than for the same job at every other
location? The comparison is always within one job, because a deputy and a clerk are not
disciplined at the same rate anywhere and comparing them would say nothing.

A unit is **flagged** only when all three of these hold:

1. **It is unlikely to be chance**, after allowing for how many units were compared
   (`detection_false_discovery_rate`; see `stats.py`).
2. **It is large enough to matter**: the unit's rate is at least
   `detection_min_rate_ratio` times the comparison's.
3. **It is not a handful of people.** The test is repeated counting people instead of
   actions: how many different people were disciplined here, against how many in the same
   job elsewhere. If the unit stands out on actions but not on people, the extra actions
   belong to a few people disciplined many times. It is then reported as *concentrated in
   few people* and is not flagged. That is a different thing from a pattern in a unit, and
   this product does not point at individuals.

What a flag says, and what it does not
--------------------------------------
A flag says the unit's rate is higher than the same job's elsewhere, by more than chance and
by enough to matter. It does **not** say why. More actions can mean more misconduct, a
supervisor who writes people up more readily, harder working conditions, or better record
keeping. Telling those apart is not attempted here (it is Phase 3 and, before that, a
person's job). It says nothing about any individual, and nobody is scored.

The **estimated excess cost** is the number of actions beyond what the comparison rate would
give, times the unit's own average net cost per action. It is an estimate of what the
difference costs, clearly labelled as one. It is not part of the cost baseline, which counts
only what the records show happened and is not changed by anything here.

Who may see it, and why not everyone
------------------------------------
Only a role that already sees every record: the engagement lead and the analyst. They can
count every action by job and place in the drill-down, so a finding about a unit tells them
nothing they could not work out. No person is named in a finding either way.

A client reader, who sees totals only, is not shown this. The reason is arithmetic and not
caution for its own sake. Most locations hold one large job and one or two small ones (the
officers and their two sergeants). A location's total is already visible to a client reader.
Show them the large unit as well and the small one falls out by subtraction: the cost and
count of discipline for two or three people. The cost matrix withholds those cells for
exactly that reason, and on the synthetic county that is 28 of its 32 job-and-place cells.
What a client reader could safely be shown about units is a separate question for the owner.

A unit where fewer people were disciplined than the engagement's minimum group size is
never flagged, whoever is looking: a handful of people is not a pattern in a unit.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

from wri_engine.aggregation.suppression import DEFAULT_MIN_CELL_SIZE
from wri_engine.costing.assumptions import AssumptionSet, load_assumptions
from wri_engine.costing.engine import CostRun
from wri_engine.detection.exposure import Exposure
from wri_engine.detection.stats import benjamini_hochberg, binomial_upper_tail
from wri_engine.paths import CONFIG_DIR

DETECTION_FILE = CONFIG_DIR / "detection.yaml"
FDR = "detection_false_discovery_rate"
MIN_RATIO = "detection_min_rate_ratio"
SETTING_IDS = (FDR, MIN_RATIO)
ROWS, COLS = "role_family", "work_location"
CENT = Decimal("0.01")

FLAGGED = "flagged"
CONCENTRATED = "concentrated"
NOT_FLAGGED = "not_flagged"

ONE_LOCATION = "This job exists at one location only, so there is nowhere to compare it with."
NO_ROSTER = (
    "The roster shows nobody in this job at this location during the period, so there is "
    "no rate to work out."
)


@dataclass(frozen=True)
class UnitCount:
    """What the records show for one unit: how many actions, among how many people, and
    what they cost."""

    actions: int
    people: int
    net: Decimal
    row_label: str
    col_label: str


def unit_counts(run: CostRun) -> dict[tuple[str, str], UnitCount]:
    """Actions, distinct people and net cost by (job, work location), from a costing run."""
    actions: dict[tuple[str, str], int] = {}
    people: dict[tuple[str, str], set[str]] = {}
    net: dict[tuple[str, str], Decimal] = {}
    labels: dict[tuple[str, str], tuple[str, str]] = {}
    for action in run.action_costs:
        d = action.dimensions
        key = (str(d[ROWS]), str(d[COLS]))
        actions[key] = actions.get(key, 0) + 1
        people.setdefault(key, set()).add(action.employee_id)
        net[key] = net.get(key, Decimal(0)) + action.net
        labels.setdefault(key, (str(d.get(f"{ROWS}_label", key[0])), key[1]))
    return {
        key: UnitCount(actions[key], len(people[key]), net[key], *labels[key]) for key in actions
    }


@functools.lru_cache(maxsize=4)
def load_detection_settings(path: Path | None = None) -> AssumptionSet:
    """The detection thresholds, from `config/detection.yaml`. A missing id is an error."""
    settings = load_assumptions(path or DETECTION_FILE)
    settings.require_all(list(SETTING_IDS))
    return settings


@dataclass(frozen=True)
class UnitFinding:
    """One unit that could be assessed, and what was found."""

    row: str
    col: str
    row_label: str
    col_label: str
    status: str
    actions: int
    people: int
    employee_years: float
    comparison_actions: int
    comparison_people: int
    comparison_employee_years: float
    expected_actions: float
    #: The unit's rate over the comparison's. None when the comparison had no actions.
    ratio: float | None
    p_value: float
    q_value: float
    q_value_people: float
    excess_actions: float
    cost_per_action: Decimal
    #: Only for a flagged unit. An estimate, and not part of the cost baseline.
    estimated_excess_cost: Decimal | None
    explanation: str
    formula: str
    setting_ids: tuple[str, ...] = SETTING_IDS

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "col": self.col,
            "row_label": self.row_label,
            "col_label": self.col_label,
            "status": self.status,
            "actions": self.actions,
            "people": self.people,
            "employee_years": round(self.employee_years, 1),
            "comparison_actions": self.comparison_actions,
            "comparison_people": self.comparison_people,
            "comparison_employee_years": round(self.comparison_employee_years, 1),
            "expected_actions": round(self.expected_actions, 1),
            "ratio": None if self.ratio is None else round(self.ratio, 2),
            "p_value": self.p_value,
            "q_value": self.q_value,
            "q_value_people": self.q_value_people,
            "excess_actions": round(self.excess_actions, 1),
            "cost_per_action": str(self.cost_per_action),
            "estimated_excess_cost": None
            if self.estimated_excess_cost is None
            else str(self.estimated_excess_cost),
            "explanation": self.explanation,
            "formula": self.formula,
            "setting_ids": list(self.setting_ids),
        }


@dataclass(frozen=True)
class NotAssessed:
    """A unit that was not assessed, and why. No figure is carried."""

    row: str
    col: str
    row_label: str
    col_label: str
    reason: str

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "col": self.col,
            "row_label": self.row_label,
            "col_label": self.col_label,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class HotspotReport:
    findings: list[UnitFinding]
    not_assessed: list[NotAssessed]
    #: How many units were compared, including those with no actions at all. This is the
    #: number the chance adjustment allows for.
    comparisons: int
    #: The fewest disciplined people a unit needs before it can be flagged.
    min_people: int
    window_start: date | None
    window_end: date | None
    false_discovery_rate: float
    min_rate_ratio: float
    #: Settings still awaiting the owner's review, by id.
    unreviewed_settings: tuple[str, ...]

    @property
    def flagged(self) -> list[UnitFinding]:
        return [f for f in self.findings if f.status == FLAGGED]

    def as_dict(self) -> dict:
        return {
            "unit": "Employee type at a work location",
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "comparisons": self.comparisons,
            "min_people": self.min_people,
            "settings": {
                FDR: self.false_discovery_rate,
                MIN_RATIO: self.min_rate_ratio,
                "unreviewed": list(self.unreviewed_settings),
            },
            "flagged": [f.as_dict() for f in self.findings if f.status == FLAGGED],
            "concentrated": [f.as_dict() for f in self.findings if f.status == CONCENTRATED],
            "not_flagged": [f.as_dict() for f in self.findings if f.status == NOT_FLAGGED],
            "not_assessed": [n.as_dict() for n in self.not_assessed],
        }


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _explain(f: dict, comparisons: int, fdr: float, min_ratio: float, min_people: int) -> str:
    """One finding in sentences a person can check against the numbers beside it."""
    here = (
        f"{f['actions']} actions among {f['people']} people over "
        f"{f['employee_years']:,.1f} employee-years."
    )
    there = (
        f"The same job at other locations had {f['comparison_actions']} actions among "
        f"{f['comparison_people']} people over {f['comparison_employee_years']:,.1f} "
        f"employee-years, which at that rate would be {f['expected_actions']:,.1f} actions "
        f"here."
    )
    if f["ratio"] is None:
        size = "The comparison had no actions at all, so no ratio can be given."
    else:
        size = f"That is {f['ratio']:.2f} times the rate elsewhere."
    chance = (
        f"Allowing for the {comparisons} units compared, the adjusted chance value is "
        f"{f['q_value']:.4f} (a flag needs {fdr:g} or less)."
    )
    if f["status"] == FLAGGED:
        verdict = (
            f"Flagged: unlikely to be chance, at least {min_ratio:g} times the comparison, "
            f"and it still holds when people are counted instead of actions (adjusted value "
            f"{f['q_value_people']:.4f})."
        )
    elif f["status"] == CONCENTRATED:
        verdict = (
            "Not flagged: counting people instead of actions, the unit no longer stands "
            f"out (adjusted value {f['q_value_people']:.4f}). The extra actions are "
            "concentrated in few people, which is not a pattern in the unit."
        )
    elif f["people"] < min_people:
        verdict = (
            f"Not flagged: fewer than {min_people} people were disciplined here, which is "
            f"too few to call anything a pattern in the unit."
        )
    elif f["q_value"] > fdr:
        verdict = "Not flagged: a difference this size could be chance."
    else:
        verdict = (
            f"Not flagged: unlikely to be chance, but smaller than the {min_ratio:g} times "
            f"that counts as large enough to matter."
        )
    return " ".join((here, there, size, chance, verdict))


def find_hotspots(
    counts: dict[tuple[str, str], UnitCount],
    exposure: dict[tuple[str, str], Exposure],
    settings: AssumptionSet,
    *,
    min_people: int = DEFAULT_MIN_CELL_SIZE,
    window_start: date | None = None,
    window_end: date | None = None,
) -> HotspotReport:
    """Compare every unit with the same job at its other locations.

    `counts` is what the records show for each unit (`unit_counts`); `exposure` is
    employee-years for each unit from the roster. `min_people` is the fewest disciplined
    people a unit must have before it can be flagged: the engagement's minimum group size.
    """
    fdr = float(settings.value(FDR))
    min_ratio = float(settings.value(MIN_RATIO))

    by_job: dict[str, list[str]] = {}
    for job, place in sorted(set(exposure) | set(counts)):
        by_job.setdefault(job, []).append(place)

    not_assessed: list[NotAssessed] = []
    candidates: list[dict] = []
    for job, places in by_job.items():
        job_actions = sum(counts[(job, p)].actions for p in places if (job, p) in counts)
        # A person works at one location, so the people disciplined in a job are the people
        # disciplined at each of its locations, added up.
        job_people = sum(counts[(job, p)].people for p in places if (job, p) in counts)
        job_years = sum(exposure[(job, p)].employee_years for p in places if (job, p) in exposure)
        for place in places:
            seen = counts.get((job, place))
            unit = exposure.get((job, place))
            if unit is None or unit.employee_years <= 0:
                if seen is not None:
                    not_assessed.append(
                        NotAssessed(job, place, seen.row_label, seen.col_label, NO_ROSTER)
                    )
                continue
            comparison_years = job_years - unit.employee_years
            if comparison_years <= 0:
                if seen is not None:
                    not_assessed.append(
                        NotAssessed(job, place, seen.row_label, seen.col_label, ONE_LOCATION)
                    )
                continue
            actions = seen.actions if seen else 0
            people = seen.people if seen else 0
            comparison_actions = job_actions - actions
            comparison_people = job_people - people
            share = unit.employee_years / (unit.employee_years + comparison_years)
            candidates.append(
                {
                    "job": job,
                    "place": place,
                    "seen": seen,
                    "actions": actions,
                    "people": people,
                    "employee_years": unit.employee_years,
                    "comparison_actions": comparison_actions,
                    "comparison_people": comparison_people,
                    "comparison_employee_years": comparison_years,
                    "expected_actions": comparison_actions / comparison_years * unit.employee_years,
                    "p_value": binomial_upper_tail(actions, actions + comparison_actions, share),
                    # The same question about people: different people disciplined here,
                    # against different people disciplined in the same job elsewhere.
                    "p_people": binomial_upper_tail(people, people + comparison_people, share),
                }
            )

    q_values = benjamini_hochberg([c["p_value"] for c in candidates])
    q_people = benjamini_hochberg([c["p_people"] for c in candidates])

    findings: list[UnitFinding] = []
    for candidate, q, q_p in zip(candidates, q_values, q_people, strict=True):
        seen = candidate["seen"]
        if seen is None:
            continue  # no actions at all: counted among the comparisons, nothing to show
        expected = candidate["expected_actions"]
        ratio = candidate["actions"] / expected if expected > 0 else None
        stands_out = q <= fdr and (ratio is None or ratio >= min_ratio)
        if stands_out and candidate["people"] >= min_people and q_p <= fdr:
            status = FLAGGED
        elif stands_out and candidate["people"] >= min_people:
            status = CONCENTRATED
        else:
            status = NOT_FLAGGED
        excess = max(candidate["actions"] - expected, 0.0)
        cost_per_action = (seen.net / seen.actions).quantize(CENT)
        excess_cost, formula = None, ""
        if status == FLAGGED:
            excess_rounded = Decimal(str(round(excess, 1)))
            excess_cost = (excess_rounded * cost_per_action).quantize(CENT)
            formula = (
                f"{excess_rounded} actions more than expected x {_money(cost_per_action)} "
                f"average net cost per action in this unit (an estimate, not part of the "
                f"cost baseline)"
            )
        fields = {
            "status": status,
            "actions": candidate["actions"],
            "people": candidate["people"],
            "employee_years": candidate["employee_years"],
            "comparison_actions": candidate["comparison_actions"],
            "comparison_people": candidate["comparison_people"],
            "comparison_employee_years": candidate["comparison_employee_years"],
            "expected_actions": expected,
            "ratio": ratio,
            "q_value": q,
            "q_value_people": q_p,
        }
        findings.append(
            UnitFinding(
                row=candidate["job"],
                col=candidate["place"],
                row_label=seen.row_label,
                col_label=seen.col_label,
                p_value=candidate["p_value"],
                excess_actions=excess,
                cost_per_action=cost_per_action,
                estimated_excess_cost=excess_cost,
                explanation=_explain(fields, len(candidates), fdr, min_ratio, min_people),
                formula=formula,
                **fields,
            )
        )

    order = {FLAGGED: 0, CONCENTRATED: 1, NOT_FLAGGED: 2}
    findings.sort(key=lambda f: (order[f.status], f.q_value, f.row_label, f.col_label))
    not_assessed.sort(key=lambda n: (n.row_label, n.col_label))
    return HotspotReport(
        findings=findings,
        not_assessed=not_assessed,
        comparisons=len(candidates),
        min_people=min_people,
        window_start=window_start,
        window_end=window_end,
        false_discovery_rate=fdr,
        min_rate_ratio=min_ratio,
        unreviewed_settings=tuple(i for i in SETTING_IDS if settings.get(i).is_placeholder),
    )
