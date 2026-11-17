"""Hotspots: units whose discipline rate stands out from the same job elsewhere.

A **unit** is a job at a place: corrections officers at the main detention center, equipment
operators at the north yard. It is looked at in two ways.

* **By type of misconduct** (the main one): over the period, were there more discipline
  actions *of this type* per employee-year here than for the same job at every other
  location? "Attendance violations among corrections officers at the main detention center."
  This is where the cost baseline's question, what discipline costs by employee type and by
  type of misconduct, meets the question of where it clusters.
* **Overall**: the same, counting every type together. A unit can stand out overall
  without standing out on any one type, and the reverse.

The comparison is always within one job, because a deputy and a clerk are not disciplined
at the same rate anywhere and comparing them would say nothing. By type, it is also within
one type: attendance at one location against attendance at the others.

A unit is **flagged** only when all three of these hold:

1. **It is unlikely to be chance**, after allowing for how many units were compared
   (`detection_false_discovery_rate`; see `stats.py`).
2. **It is large enough to matter**: the unit's rate is at least
   `detection_min_rate_ratio` times the comparison's.
3. **It is not a handful of people.** The test is repeated counting people instead of
   actions: how many different people were disciplined here, against how many in the same
   job elsewhere. This is a check on units that already stand out, not a second search, so
   it is adjusted for the number of units being checked and not for every comparison made.
   If the unit stands out on actions but not on people, the extra actions belong to a few
   people disciplined many times. It is then reported as *concentrated in few people* and
   is not flagged. That is a different thing from a pattern in a unit, and this product
   does not point at individuals.

What a flag says, and what it does not
--------------------------------------
A flag says the unit's rate is higher than the same job's elsewhere, by more than chance and
by enough to matter. It does **not** say why. More actions can mean more misconduct, a
supervisor who writes people up more readily, harder working conditions, or better record
keeping. Telling those apart is not attempted here (it is Phase 3 and, before that, a
person's job). It says nothing about any individual, and nobody is scored.

Paired with what it costs
-------------------------
Every finding carries the **cost of the unit's actions from the cost baseline**: what the
records show these actions cost, the same figure the cost matrix gives for that job, place
and type. That is an actual.

A flagged finding also carries an **estimated excess cost**: the number of actions beyond
what the comparison rate would give, times the unit's own average net cost per action. It is
an estimate of what the difference costs, labelled as one. It is not part of the cost
baseline, which counts only what the records show happened and is not changed by anything
here.

Who sees what
-------------
A flag exists to be put in front of the people who run the organization, so every role that
sees totals sees the flagged units. What differs is the working behind them.

* **A role that sees every record** (the engagement lead, the analyst) gets everything: the
  flagged units, those concentrated in few people, every unit assessed and not flagged, and
  those that could not be assessed. They can count every action by job and place in the
  drill-down, so none of it tells them anything new.
* **A role that sees totals only** (the client reader) gets the flagged units and nothing
  else, through `HotspotReport.for_totals_reader`. A flagged unit is a figure about a
  group of people like any cell of the cost matrix, so it is released under the same two
  rules (`aggregation/suppression.py`): the group must be at least the engagement's minimum
  size, and with what this person has already been shown it must not let a smaller group
  be worked out by subtraction. That is asked of three groups at once, because a finding
  gives figures for all three: the people disciplined in the unit, those in the comparison,
  and the two together. If the unit passes and the comparison does not, the reader gets
  the unit's own figures with the comparison's left out (`COMPARISON_FIELDS`) and a
  sentence saying so; the finding is still about a unit that stands out, and the reader is
  told everything that can be told. If the unit itself fails, the finding is withheld and
  the reader is told that one was. The list of units that were *not* flagged stays with the
  record-level roles: most locations hold one large job and one or two small ones, and that
  list would hand over the small ones.

A unit where fewer people were disciplined than the engagement's minimum group size is
never flagged, whoever is looking: a handful of people is not a pattern in a unit. No person
is named in any finding.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path

from wri_engine.aggregation.suppression import (
    DEFAULT_MIN_CELL_SIZE,
    DisclosureLedger,
    release_together,
)
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
    #: The type of misconduct, when the unit is looked at by type. Empty for "overall".
    topic: str = ""
    topic_label: str = ""
    #: Who the people are. Used only to decide what a totals-only reader may be shown; it
    #: never leaves the server.
    cohort: frozenset[str] = frozenset()


OVERALL, BY_MISCONDUCT = "overall", "misconduct"
TOPIC = "misconduct_category"


def unit_counts(run: CostRun, by: str = OVERALL) -> dict[tuple[str, str, str], UnitCount]:
    """Actions, distinct people and net cost by (job, work location, type of misconduct),
    from a costing run. With `by="overall"` the type is empty and every type is counted
    together."""
    if by not in (OVERALL, BY_MISCONDUCT):
        raise ValueError(f"by must be {OVERALL!r} or {BY_MISCONDUCT!r}, got {by!r}")
    Key = tuple[str, str, str]
    actions: dict[Key, int] = {}
    people: dict[Key, set[str]] = {}
    net: dict[Key, Decimal] = {}
    labels: dict[Key, tuple[str, str, str]] = {}
    for action in run.action_costs:
        d = action.dimensions
        topic = str(d[TOPIC]) if by == BY_MISCONDUCT else ""
        key = (str(d[ROWS]), str(d[COLS]), topic)
        actions[key] = actions.get(key, 0) + 1
        people.setdefault(key, set()).add(action.employee_id)
        net[key] = net.get(key, Decimal(0)) + action.net
        labels.setdefault(
            key,
            (
                str(d.get(f"{ROWS}_label", key[0])),
                key[1],
                str(d.get(f"{TOPIC}_label", topic)) if topic else "",
            ),
        )
    return {
        key: UnitCount(
            actions[key],
            len(people[key]),
            net[key],
            labels[key][0],
            labels[key][1],
            key[2],
            labels[key][2],
            frozenset(people[key]),
        )
        for key in actions
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
    topic: str
    topic_label: str
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
    #: What the cost baseline shows these actions cost. An actual, not an estimate.
    net: Decimal
    cost_per_action: Decimal
    #: Only for a flagged unit. An estimate, and not part of the cost baseline.
    estimated_excess_cost: Decimal | None
    explanation: str
    formula: str
    setting_ids: tuple[str, ...] = SETTING_IDS
    #: The people behind the unit's figures and behind the comparison's. Never serialized.
    cohort: frozenset[str] = field(default=frozenset(), repr=False, compare=False)
    comparison_cohort: frozenset[str] = field(default=frozenset(), repr=False, compare=False)

    def as_dict(self) -> dict:
        return {
            "row": self.row,
            "col": self.col,
            "row_label": self.row_label,
            "col_label": self.col_label,
            "topic": self.topic,
            "topic_label": self.topic_label,
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
            "net": str(self.net),
            "cost_per_action": str(self.cost_per_action),
            "estimated_excess_cost": None
            if self.estimated_excess_cost is None
            else str(self.estimated_excess_cost),
            "explanation": self.explanation,
            "formula": self.formula,
            "setting_ids": list(self.setting_ids),
            "comparison_withheld": False,
        }


#: What a finding says about its comparison group. Left out together when that group may not
#: be released to the reader; everything else in the finding is about the unit itself.
COMPARISON_FIELDS = (
    "comparison_actions",
    "comparison_people",
    "comparison_employee_years",
    "expected_actions",
    "ratio",
    "p_value",
    "q_value",
    "q_value_people",
    "excess_actions",
    "estimated_excess_cost",
)
COMPARISON_WITHHELD = (
    "The figures for {where} are not shown here: together with figures already shown, they "
    "would let a group of fewer than {min_people} people be worked out."
)


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
    #: "misconduct" (a type of misconduct in a job at a place) or "overall".
    by: str
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

    def _heading(self) -> dict:
        return {
            "by": self.by,
            "unit": (
                "A type of misconduct, in an employee type, at a work location"
                if self.by == BY_MISCONDUCT
                else "An employee type at a work location"
            ),
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "comparisons": self.comparisons,
            "min_people": self.min_people,
            "settings": {
                FDR: self.false_discovery_rate,
                MIN_RATIO: self.min_rate_ratio,
                "unreviewed": list(self.unreviewed_settings),
            },
        }

    def as_dict(self) -> dict:
        """Everything, for a role that sees every record."""
        return {
            **self._heading(),
            "detail": "full",
            "withheld": 0,
            "flagged": [f.as_dict() for f in self.findings if f.status == FLAGGED],
            "concentrated": [f.as_dict() for f in self.findings if f.status == CONCENTRATED],
            "not_flagged": [f.as_dict() for f in self.findings if f.status == NOT_FLAGGED],
            "not_assessed": [n.as_dict() for n in self.not_assessed],
        }

    def for_totals_reader(self, ledger: DisclosureLedger) -> dict:
        """The flagged units, for a role that sees totals only. See "Who sees what" above.

        `ledger` is what this person has already been shown. Each finding released is added
        to it, so a later request for the cost matrix is checked against these too.
        """
        shown: list[dict] = []
        withheld = 0
        for finding in self.flagged:
            unit = [finding.cohort]
            comparison = [finding.comparison_cohort, finding.cohort | finding.comparison_cohort]
            if release_together(ledger, unit + comparison, self.min_people):
                shown.append(finding.as_dict())
            elif release_together(ledger, unit, self.min_people):
                # The unit may be shown; what it is compared with may not. Show what can be
                # shown and say what is left out, rather than nothing.
                shown.append(self._unit_only(finding))
            else:
                withheld += 1
        return {**self._heading(), "detail": "flagged_only", "withheld": withheld, "flagged": shown}

    def _unit_only(self, finding: UnitFinding) -> dict:
        """A flagged unit with its own figures and none of its comparison's."""
        what = f"{finding.topic_label} actions" if finding.topic_label else "actions"
        explanation = (
            f"{finding.actions} {what} among {finding.people} people over "
            f"{finding.employee_years:,.1f} employee-years. That is higher than the same "
            f"job's rate at its other locations: at least {self.min_rate_ratio:g} times it, by "
            f"more than chance would explain after allowing for the {self.comparisons} "
            f"comparisons made, and among enough different people that it is not the record "
            f"of a few. "
            + COMPARISON_WITHHELD.format(
                where="the same job at its other locations", min_people=self.min_people
            )
        )
        return {
            **finding.as_dict(),
            **dict.fromkeys(COMPARISON_FIELDS),
            "formula": "",
            "explanation": explanation,
            "comparison_withheld": True,
        }


def _money(value: Decimal) -> str:
    return f"${value:,.2f}"


def _explain(f: dict, comparisons: int, fdr: float, min_ratio: float, min_people: int) -> str:
    """One finding in sentences a person can check against the numbers beside it."""
    what = f"{f['topic_label']} actions" if f["topic_label"] else "actions"
    here = (
        f"{f['actions']} {what} among {f['people']} people over "
        f"{f['employee_years']:,.1f} employee-years."
    )
    there = (
        f"The same job at other locations had {f['comparison_actions']} {what} among "
        f"{f['comparison_people']} people over {f['comparison_employee_years']:,.1f} "
        f"employee-years, which at that rate would be {f['expected_actions']:,.1f} {what} "
        f"here."
    )
    if f["ratio"] is None:
        size = "The comparison had no actions at all, so no ratio can be given."
    else:
        size = f"That is {f['ratio']:.2f} times the rate elsewhere."
    chance = (
        f"Allowing for the {comparisons} comparisons made, the adjusted chance value is "
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
    counts: dict[tuple[str, str, str], UnitCount],
    exposure: dict[tuple[str, str], Exposure],
    settings: AssumptionSet,
    *,
    by: str = OVERALL,
    min_people: int = DEFAULT_MIN_CELL_SIZE,
    window_start: date | None = None,
    window_end: date | None = None,
) -> HotspotReport:
    """Compare every unit with the same job (and, by type, the same type of misconduct) at
    its other locations.

    `counts` is what the records show for each unit (`unit_counts`); `exposure` is
    employee-years for each job and place from the roster. `min_people` is the fewest
    disciplined people a unit must have before it can be flagged: the engagement's minimum
    group size.

    What is compared: for each job, and for each type of misconduct that job was disciplined
    for at least once anywhere, every location where the roster shows people in that job.
    A type no one in the job was ever disciplined for cannot stand out anywhere and is not
    counted among the comparisons. A location where the job had no actions of that type is
    counted, because "none here, some elsewhere" is a result too.
    """
    fdr = float(settings.value(FDR))
    min_ratio = float(settings.value(MIN_RATIO))

    places_of_job: dict[str, set[str]] = {}
    for job, place in exposure:
        places_of_job.setdefault(job, set()).add(place)
    groups: dict[tuple[str, str], dict[str, UnitCount]] = {}
    for (job, place, topic), seen in counts.items():
        groups.setdefault((job, topic), {})[place] = seen
        places_of_job.setdefault(job, set()).add(place)

    not_assessed: dict[tuple[str, str], NotAssessed] = {}
    candidates: list[dict] = []
    for (job, topic), group in sorted(groups.items()):
        places = sorted(places_of_job[job])
        job_actions = sum(c.actions for c in group.values())
        # A person works at one location, so the people disciplined in a job are the people
        # disciplined at each of its locations, added up.
        job_people = sum(c.people for c in group.values())
        job_years = sum(exposure[(job, p)].employee_years for p in places if (job, p) in exposure)
        job_cohort = frozenset().union(*(c.cohort for c in group.values()))
        for place in places:
            seen = group.get(place)
            unit = exposure.get((job, place))
            if unit is None or unit.employee_years <= 0:
                if seen is not None:
                    not_assessed.setdefault(
                        (job, place),
                        NotAssessed(job, place, seen.row_label, seen.col_label, NO_ROSTER),
                    )
                continue
            comparison_years = job_years - unit.employee_years
            if comparison_years <= 0:
                if seen is not None:
                    not_assessed.setdefault(
                        (job, place),
                        NotAssessed(job, place, seen.row_label, seen.col_label, ONE_LOCATION),
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
                    "topic": topic,
                    "seen": seen,
                    "actions": actions,
                    "people": people,
                    "employee_years": unit.employee_years,
                    "comparison_actions": comparison_actions,
                    "comparison_people": comparison_people,
                    "comparison_cohort": job_cohort - (seen.cohort if seen else frozenset()),
                    "comparison_employee_years": comparison_years,
                    "expected_actions": comparison_actions / comparison_years * unit.employee_years,
                    "p_value": binomial_upper_tail(actions, actions + comparison_actions, share),
                    # The same question about people: different people disciplined here,
                    # against different people disciplined in the same job elsewhere.
                    "p_people": binomial_upper_tail(people, people + comparison_people, share),
                }
            )

    q_values = benjamini_hochberg([c["p_value"] for c in candidates])

    def stands_out(candidate: dict, q: float) -> bool:
        expected = candidate["expected_actions"]
        ratio = candidate["actions"] / expected if expected > 0 else None
        return (
            candidate["seen"] is not None
            and q <= fdr
            and (ratio is None or ratio >= min_ratio)
            and candidate["people"] >= min_people
        )

    # The people check confirms units that already stand out on actions; it is not a second
    # search. So it is adjusted for the number of units being confirmed, and not for every
    # comparison made: adjusting it for all of them would fail a unit with 64 different
    # people behind 73 actions, and call that "concentrated in few people".
    selected = [
        i for i, (c, q) in enumerate(zip(candidates, q_values, strict=True)) if stands_out(c, q)
    ]
    q_people = benjamini_hochberg([c["p_people"] for c in candidates])
    for i, adjusted in zip(
        selected, benjamini_hochberg([candidates[i]["p_people"] for i in selected]), strict=True
    ):
        q_people[i] = adjusted

    findings: list[UnitFinding] = []
    for index, (candidate, q, q_p) in enumerate(zip(candidates, q_values, q_people, strict=True)):
        seen = candidate["seen"]
        if seen is None:
            continue  # no actions at all: counted among the comparisons, nothing to show
        expected = candidate["expected_actions"]
        ratio = candidate["actions"] / expected if expected > 0 else None
        if index in selected and q_p <= fdr:
            status = FLAGGED
        elif index in selected:
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
            "topic_label": seen.topic_label,
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
                topic=candidate["topic"],
                p_value=candidate["p_value"],
                excess_actions=excess,
                net=seen.net,
                cost_per_action=cost_per_action,
                cohort=seen.cohort,
                comparison_cohort=candidate["comparison_cohort"],
                estimated_excess_cost=excess_cost,
                explanation=_explain(fields, len(candidates), fdr, min_ratio, min_people),
                formula=formula,
                **fields,
            )
        )

    order = {FLAGGED: 0, CONCENTRATED: 1, NOT_FLAGGED: 2}
    findings.sort(
        key=lambda f: (order[f.status], f.q_value, f.row_label, f.col_label, f.topic_label)
    )
    return HotspotReport(
        findings=findings,
        not_assessed=sorted(not_assessed.values(), key=lambda n: (n.row_label, n.col_label)),
        comparisons=len(candidates),
        by=by,
        min_people=min_people,
        window_start=window_start,
        window_end=window_end,
        false_discovery_rate=fdr,
        min_rate_ratio=min_ratio,
        unreviewed_settings=tuple(i for i in SETTING_IDS if settings.get(i).is_placeholder),
    )
