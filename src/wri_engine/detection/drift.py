"""Drift: units where a type of misconduct is rising, and rising more than everywhere else.

`hotspots.py` asks whether a unit's rate is higher than the same job's elsewhere, over the
whole period. This asks a different question of the same units: **is it getting worse?**

The period the records cover is cut in half by date. For each unit (a job at a work
location, by type of misconduct or with every type together) the later half is set against
the earlier half, as actions per employee-year.

What a rise is compared with, and why
-------------------------------------
A unit's own rise is not enough. If the client's records are thin in the early years, or a
new attendance policy raised attendance discipline everywhere, every unit rises and none of
them is a finding. So a unit's change is set against the change in **the rest of the
organization for the same type of misconduct** over the same two halves. Rates differ
between jobs, but this compares *changes*, and a change that happened to everyone is not a
pattern in a unit.

A unit is **flagged as rising** only when all of these hold:

1. **It rose more than the rest did, by more than chance.** Of all the actions of this type
   in the organization, if the unit had changed as everyone else did, its actions would be
   no more likely to fall in the later half than anyone else's, after allowing for any
   change in its staffing. The exact test of that is `stats.shifted_upper_tail`. The result
   is adjusted for the number of comparisons made (`detection_false_discovery_rate`).
2. **It rose by enough to matter, on both counts.** The unit's later rate is at least
   `detection_min_rate_ratio` times its own earlier rate, *and* its rise is at least that
   many times the rest of the organization's. The first stops "held steady while everyone
   else improved" being called a rise. The second stops a general rise being called the
   unit's.
3. **It is not a handful of people.** At least the engagement's minimum group size of
   different people were disciplined in the later half, and the test still holds when
   people are counted instead of actions. Otherwise it is reported as *concentrated in few
   people* and not flagged, exactly as a hotspot is.

The two thresholds are the hotspot detector's. A rise is not held to a different standard
from a level.

What a flag says, and what it does not
--------------------------------------
It says the rate in this unit went up between the two halves, by more than the same type
went up elsewhere, by more than chance and by enough to matter. It does **not** say why,
and it says nothing about any individual. Nothing here looks at who was disciplined beyond
counting different people.

Paired with what it costs
-------------------------
Each finding carries what the cost baseline shows the unit's actions cost in each half:
two actuals. A flagged finding also carries an **estimated excess**: the later half's
actions beyond what the earlier rate would have given if it had only changed as the rest of
the organization did, times the unit's average net cost per action in the later half. It is
an estimate, labelled as one, and is in no baseline total.

Who sees what
-------------
As for hotspots (see "Who sees what" in `hotspots.py`): every role that sees totals sees
the flagged units; the working stays with roles that see records. A totals reader's finding
is released only if every group it gives figures for passes the small-group rules: the
people disciplined in the unit in the earlier half, in the later half, across both, and the
comparison's in each half. Where the unit's three pass and the rest's two do not, the
reader gets the unit's own figures with the rest's left out (`REST_FIELDS`) and a sentence
saying so. A unit that went from two people to twenty is withheld outright, because the
finding would state a figure about the two.

Limits, stated plainly
----------------------
* An action is dated by its decision. Misconduct late in the period that is not yet decided
  is not counted, so the later half runs low, if anything. A rise is understated, not
  invented.
* Two halves are a blunt instrument. This says the later half is higher than the earlier;
  it does not say when the change happened or whether it is still going.
* The roster records where a person works now. Someone who moved is counted wholly at
  their current location, in both halves.
* A change that reached one whole job across the organization, and no other job, will show
  in each of that job's units.
* Among units that pass the first two tests, the people check is adjusted for how many of
  those there are. Other units carry no people value: it is shown as 1.
* Only rises are flagged. A unit that fell is listed in the working as not flagged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from wri_engine.aggregation.suppression import (
    DEFAULT_MIN_CELL_SIZE,
    DisclosureLedger,
    release_together,
)
from wri_engine.costing.assumptions import AssumptionSet
from wri_engine.costing.engine import CostRun
from wri_engine.detection.exposure import Exposure
from wri_engine.detection.hotspots import (
    BY_MISCONDUCT,
    CENT,
    COLS,
    COMPARISON_WITHHELD,
    CONCENTRATED,
    FDR,
    FLAGGED,
    MIN_RATIO,
    NOT_FLAGGED,
    OVERALL,
    ROWS,
    SETTING_IDS,
    TOPIC,
    NotAssessed,
    _money,
)
from wri_engine.detection.stats import benjamini_hochberg, shifted_upper_tail

NEW_UNIT = (
    "The roster shows nobody in this job at this location in one of the two halves, so "
    "there is no earlier rate to set a later one against."
)
NOTHING_ELSE = (
    "No one else in the organization was disciplined for this in the period, so there is "
    "no change elsewhere to compare with."
)


def halves(start: date, end: date) -> tuple[date, date]:
    """Where the earlier half ends and the later half begins, for a period counted
    inclusively. With an odd number of days the earlier half is the shorter by one."""
    last_of_earlier = start + timedelta(days=((end - start).days + 1) // 2 - 1)
    return last_of_earlier, last_of_earlier + timedelta(days=1)


@dataclass(frozen=True)
class Tally:
    """What the records show for one group of people in one half of the period."""

    actions: int = 0
    net: Decimal = Decimal(0)
    #: Who they are. Used only to decide what a totals reader may be shown.
    cohort: frozenset[str] = frozenset()

    @property
    def people(self) -> int:
        return len(self.cohort)

    def minus(self, other: Tally) -> Tally:
        """Everyone in this group except a part of it."""
        return Tally(self.actions - other.actions, self.net - other.net, self.cohort - other.cohort)


@dataclass(frozen=True)
class UnitHalves:
    """One unit's records in the earlier and the later half."""

    row_label: str
    col_label: str
    topic: str
    topic_label: str
    earlier: Tally
    later: Tally


def drift_counts(
    run: CostRun, later_starts: date, by: str = BY_MISCONDUCT
) -> dict[tuple[str, str, str], UnitHalves]:
    """Actions, people and net cost by (job, work location, type of misconduct), split at
    `later_starts`: an action decided on or after that date is in the later half. With
    `by="overall"` the type is empty and every type is counted together."""
    if by not in (OVERALL, BY_MISCONDUCT):
        raise ValueError(f"by must be {OVERALL!r} or {BY_MISCONDUCT!r}, got {by!r}")
    Key = tuple[str, str, str]
    actions: dict[tuple[Key, bool], int] = {}
    net: dict[tuple[Key, bool], Decimal] = {}
    people: dict[tuple[Key, bool], set[str]] = {}
    labels: dict[Key, tuple[str, str, str]] = {}
    for action in run.action_costs:
        d = action.dimensions
        topic = str(d[TOPIC]) if by == BY_MISCONDUCT else ""
        key = (str(d[ROWS]), str(d[COLS]), topic)
        half = (key, date.fromisoformat(str(d["decision_date"])) >= later_starts)
        actions[half] = actions.get(half, 0) + 1
        net[half] = net.get(half, Decimal(0)) + action.net
        people.setdefault(half, set()).add(action.employee_id)
        labels.setdefault(
            key,
            (
                str(d.get(f"{ROWS}_label", key[0])),
                key[1],
                str(d.get(f"{TOPIC}_label", topic)) if topic else "",
            ),
        )

    def tally(key: Key, later: bool) -> Tally:
        half = (key, later)
        if half not in actions:
            return Tally()
        return Tally(actions[half], net[half], frozenset(people[half]))

    return {
        key: UnitHalves(
            *labels[key][:2], key[2], labels[key][2], tally(key, False), tally(key, True)
        )
        for key in labels
    }


@dataclass(frozen=True)
class DriftFinding:
    """One unit that could be assessed, and what was found."""

    row: str
    col: str
    row_label: str
    col_label: str
    topic: str
    topic_label: str
    status: str
    earlier_actions: int
    earlier_people: int
    earlier_employee_years: float
    later_actions: int
    later_people: int
    later_employee_years: float
    #: The unit's later rate over its earlier rate. None when it had no earlier action.
    own_change: float | None
    elsewhere_earlier_actions: int
    elsewhere_later_actions: int
    #: The same, for the rest of the organization. None when it had no earlier action.
    elsewhere_change: float | None
    #: How many times the rest's change the unit's was. None when either is.
    ratio: float | None
    #: Later actions if the unit had only changed as the rest did. None when not known.
    expected_later_actions: float | None
    p_value: float
    q_value: float
    q_value_people: float
    #: What the cost baseline shows the unit's actions cost in each half. Actuals.
    earlier_net: Decimal
    later_net: Decimal
    #: Only for a flagged unit. An estimate, and not part of the cost baseline.
    estimated_excess_cost: Decimal | None
    explanation: str
    formula: str
    setting_ids: tuple[str, ...] = SETTING_IDS
    #: The groups this finding gives figures for. Never serialized.
    cohorts: tuple[frozenset[str], ...] = field(default=(), repr=False, compare=False)

    def as_dict(self) -> dict:
        def rounded(value: float | None, places: int) -> float | None:
            return None if value is None else round(value, places)

        return {
            "row": self.row,
            "col": self.col,
            "row_label": self.row_label,
            "col_label": self.col_label,
            "topic": self.topic,
            "topic_label": self.topic_label,
            "status": self.status,
            "earlier_actions": self.earlier_actions,
            "earlier_people": self.earlier_people,
            "earlier_employee_years": round(self.earlier_employee_years, 1),
            "later_actions": self.later_actions,
            "later_people": self.later_people,
            "later_employee_years": round(self.later_employee_years, 1),
            "own_change": rounded(self.own_change, 2),
            "elsewhere_earlier_actions": self.elsewhere_earlier_actions,
            "elsewhere_later_actions": self.elsewhere_later_actions,
            "elsewhere_change": rounded(self.elsewhere_change, 2),
            "ratio": rounded(self.ratio, 2),
            "expected_later_actions": rounded(self.expected_later_actions, 1),
            "p_value": self.p_value,
            "q_value": self.q_value,
            "q_value_people": self.q_value_people,
            "earlier_net": str(self.earlier_net),
            "later_net": str(self.later_net),
            "estimated_excess_cost": None
            if self.estimated_excess_cost is None
            else str(self.estimated_excess_cost),
            "explanation": self.explanation,
            "formula": self.formula,
            "setting_ids": list(self.setting_ids),
            "comparison_withheld": False,
        }


#: What a finding says about the rest of the organization. Left out together when those
#: groups may not be released to the reader.
REST_FIELDS = (
    "elsewhere_earlier_actions",
    "elsewhere_later_actions",
    "elsewhere_change",
    "ratio",
    "expected_later_actions",
    "p_value",
    "q_value",
    "q_value_people",
    "estimated_excess_cost",
)


@dataclass(frozen=True)
class DriftReport:
    findings: list[DriftFinding]
    not_assessed: list[NotAssessed]
    #: How many units were compared. This is the number the chance adjustment allows for.
    comparisons: int
    by: str
    min_people: int
    earlier_start: date | None
    earlier_end: date | None
    later_start: date | None
    later_end: date | None
    false_discovery_rate: float
    min_rate_ratio: float
    unreviewed_settings: tuple[str, ...]

    @property
    def flagged(self) -> list[DriftFinding]:
        return [f for f in self.findings if f.status == FLAGGED]

    def _heading(self) -> dict:
        def iso(day: date | None) -> str | None:
            return day.isoformat() if day else None

        return {
            "by": self.by,
            "earlier_start": iso(self.earlier_start),
            "earlier_end": iso(self.earlier_end),
            "later_start": iso(self.later_start),
            "later_end": iso(self.later_end),
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
        """The flagged units, for a role that sees totals only. See "Who sees what"."""
        shown: list[dict] = []
        withheld = 0
        for finding in self.flagged:
            unit, rest = list(finding.cohorts[:3]), list(finding.cohorts[3:])
            if release_together(ledger, unit + rest, self.min_people):
                shown.append(finding.as_dict())
            elif release_together(ledger, unit, self.min_people):
                # The unit may be shown; the rest of the organization's halves may not.
                shown.append(self._unit_only(finding))
            else:
                withheld += 1
        return {**self._heading(), "detail": "flagged_only", "withheld": withheld, "flagged": shown}

    def _unit_only(self, finding: DriftFinding) -> dict:
        """A rising unit with its own figures and none of the rest of the organization's."""
        what = f"{finding.topic_label} actions" if finding.topic_label else "actions"
        own = (
            "There was none in the earlier half."
            if finding.own_change is None
            else f"That is {finding.own_change:.2f} times the unit's earlier rate."
        )
        explanation = (
            f"Earlier half: {finding.earlier_actions} {what} among {finding.earlier_people} "
            f"people over {finding.earlier_employee_years:,.1f} employee-years. Later half: "
            f"{finding.later_actions} among {finding.later_people} people over "
            f"{finding.later_employee_years:,.1f}. {own} It rose by at least "
            f"{self.min_rate_ratio:g} times as much as the same type did in the rest of the "
            f"organization, by more than chance would explain after allowing for the "
            f"{self.comparisons} comparisons made, and among enough different people that it "
            f"is not the record of a few. "
            + COMPARISON_WITHHELD.format(
                where="the rest of the organization", min_people=self.min_people
            )
        )
        return {
            **finding.as_dict(),
            **dict.fromkeys(REST_FIELDS),
            "formula": "",
            "explanation": explanation,
            "comparison_withheld": True,
        }


def _rate_change(earlier: int, later: int, years_earlier: float, years_later: float):
    """The later rate over the earlier rate, or None when there was no earlier action."""
    if earlier == 0:
        return None
    return (later / years_later) / (earlier / years_earlier)


def _explain(f: dict, comparisons: int, fdr: float, min_ratio: float, min_people: int) -> str:
    """One finding in sentences a person can check against the numbers beside it."""
    what = f"{f['topic_label']} actions" if f["topic_label"] else "actions"
    here = (
        f"Earlier half: {f['earlier_actions']} {what} among {f['earlier_people']} people over "
        f"{f['earlier_employee_years']:,.1f} employee-years. Later half: {f['later_actions']} "
        f"among {f['later_people']} people over {f['later_employee_years']:,.1f}."
    )
    if f["own_change"] is None:
        own = "There was none in the earlier half, so no rate of change can be given here."
    else:
        own = f"That is {f['own_change']:.2f} times the unit's earlier rate."
    there = (
        f"In the rest of the organization the same went from "
        f"{f['elsewhere_earlier_actions']} to {f['elsewhere_later_actions']}"
    )
    if f["elsewhere_change"] is None:
        there += ", with none in the earlier half."
    else:
        there += f", {f['elsewhere_change']:.2f} times the earlier rate there."
    size = (
        ""
        if f["ratio"] is None
        else f" So this unit's rate rose {f['ratio']:.2f} times as much as the rest's."
    )
    chance = (
        f"Allowing for the {comparisons} comparisons made, the adjusted chance value is "
        f"{f['q_value']:.4f} (a flag needs {fdr:g} or less)."
    )
    own_ok = f["own_change"] is None or f["own_change"] >= min_ratio
    relative_ok = f["ratio"] is None or f["ratio"] >= min_ratio
    if f["status"] == FLAGGED:
        verdict = (
            f"Flagged as rising: unlikely to be chance, at least {min_ratio:g} times its own "
            f"earlier rate and {min_ratio:g} times the rise elsewhere, and it still holds "
            f"when people are counted instead of actions (adjusted value "
            f"{f['q_value_people']:.4f})."
        )
    elif f["status"] == CONCENTRATED:
        verdict = (
            "Not flagged: counting people instead of actions, the unit no longer stands "
            f"out (adjusted value {f['q_value_people']:.4f}). The extra actions are "
            "concentrated in few people, which is not a pattern in the unit."
        )
    elif f["later_people"] < min_people:
        verdict = (
            f"Not flagged: fewer than {min_people} people were disciplined here in the later "
            f"half, which is too few to call anything a pattern in the unit."
        )
        if f["earlier_actions"] + f["later_actions"] < min_people:
            # Never compared, so there is no chance value to quote.
            return " ".join((here, own, there + size, verdict))
    elif f["q_value"] > fdr:
        verdict = "Not flagged: a difference this size could be chance."
    elif not own_ok:
        verdict = (
            f"Not flagged: the unit's own rate rose by less than the {min_ratio:g} times "
            f"that counts as large enough to matter."
        )
    elif not relative_ok:
        verdict = (
            f"Not flagged: the rise is less than {min_ratio:g} times the rise in the rest of "
            f"the organization, so it is not this unit's alone."
        )
    else:
        verdict = "Not flagged."
    return " ".join((here, own, there + size, chance, verdict))


def find_drift(
    counts: dict[tuple[str, str, str], UnitHalves],
    exposure_earlier: dict[tuple[str, str], Exposure],
    exposure_later: dict[tuple[str, str], Exposure],
    settings: AssumptionSet,
    *,
    by: str = BY_MISCONDUCT,
    min_people: int = DEFAULT_MIN_CELL_SIZE,
    earlier: tuple[date, date] | None = None,
    later: tuple[date, date] | None = None,
) -> DriftReport:
    """Set each unit's later half against its earlier half, and that change against the
    change in the rest of the organization for the same type of misconduct.

    `counts` is what the records show for each unit in each half (`drift_counts`); the two
    exposures are employee-years by job and place in each half.

    What is compared: every unit with at least `min_people` actions in the period. A
    smaller one can never be flagged, because a flag needs that many different people in
    the later half alone, so testing it would only make every real comparison harder to
    pass. Leaving it out depends on the unit's total alone, which the test takes as given,
    so it cannot favour a flag. Such a unit is still listed, as not flagged.
    """
    fdr = float(settings.value(FDR))
    min_ratio = float(settings.value(MIN_RATIO))
    all_earlier = sum(e.employee_years for e in exposure_earlier.values())
    all_later = sum(e.employee_years for e in exposure_later.values())

    whole: dict[str, tuple[Tally, Tally]] = {}
    for (_job, _place, topic), unit in counts.items():
        before, after = whole.get(topic, (Tally(), Tally()))
        whole[topic] = (
            Tally(
                before.actions + unit.earlier.actions,
                before.net + unit.earlier.net,
                before.cohort | unit.earlier.cohort,
            ),
            Tally(
                after.actions + unit.later.actions,
                after.net + unit.later.net,
                after.cohort | unit.later.cohort,
            ),
        )

    not_assessed: list[NotAssessed] = []
    candidates: list[dict] = []
    for (job, place, topic), unit in sorted(counts.items()):
        years_earlier = exposure_earlier.get((job, place), Exposure(0.0, 0)).employee_years
        years_later = exposure_later.get((job, place), Exposure(0.0, 0)).employee_years
        if years_earlier <= 0 or years_later <= 0:
            not_assessed.append(NotAssessed(job, place, unit.row_label, unit.col_label, NEW_UNIT))
            continue
        rest_earlier = whole[topic][0].minus(unit.earlier)
        rest_later = whole[topic][1].minus(unit.later)
        rest_years_earlier = all_earlier - years_earlier
        rest_years_later = all_later - years_later
        if (
            rest_earlier.actions + rest_later.actions == 0
            or rest_years_earlier <= 0
            or rest_years_later <= 0
        ):
            not_assessed.append(
                NotAssessed(job, place, unit.row_label, unit.col_label, NOTHING_ELSE)
            )
            continue
        # How much more the unit's staffing grew than the rest's. An action is that much
        # more likely to be a later one for that reason alone.
        odds = (years_later / years_earlier) / (rest_years_later / rest_years_earlier)
        group = unit.earlier.actions + unit.later.actions
        late = unit.later.actions + rest_later.actions
        everything = group + rest_earlier.actions + rest_later.actions
        people_group = unit.earlier.people + unit.later.people
        people_late = unit.later.people + rest_later.people
        people_everything = people_group + rest_earlier.people + rest_later.people
        candidates.append(
            {
                "key": (job, place, topic),
                "compared": group >= min_people,
                "unit": unit,
                "rest": (rest_earlier, rest_later),
                "years": (years_earlier, years_later),
                "own_change": _rate_change(
                    unit.earlier.actions, unit.later.actions, years_earlier, years_later
                ),
                "elsewhere_change": _rate_change(
                    rest_earlier.actions, rest_later.actions, rest_years_earlier, rest_years_later
                ),
                "p_value": shifted_upper_tail(unit.later.actions, group, late, everything, odds),
                # The same question about people: different people disciplined in each half.
                "p_people": shifted_upper_tail(
                    unit.later.people, people_group, people_late, people_everything, odds
                ),
            }
        )

    compared = [i for i, c in enumerate(candidates) if c["compared"]]
    q_values = [1.0] * len(candidates)
    for i, adjusted in zip(
        compared, benjamini_hochberg([candidates[i]["p_value"] for i in compared]), strict=True
    ):
        q_values[i] = adjusted

    def relative(candidate: dict) -> float | None:
        own, elsewhere = candidate["own_change"], candidate["elsewhere_change"]
        return None if own is None or not elsewhere else own / elsewhere

    def stands_out(candidate: dict, q: float) -> bool:
        own, ratio = candidate["own_change"], relative(candidate)
        return (
            candidate["compared"]
            and q <= fdr
            and candidate["unit"].later.actions > 0
            and (own is None or own >= min_ratio)
            and (ratio is None or ratio >= min_ratio)
            and candidate["unit"].later.people >= min_people
        )

    # As for hotspots: the people check confirms units that already stand out, so it is
    # adjusted for how many of those there are and not for every comparison made.
    selected = [
        i for i, (c, q) in enumerate(zip(candidates, q_values, strict=True)) if stands_out(c, q)
    ]
    q_people = [1.0] * len(candidates)
    for i, adjusted in zip(
        selected, benjamini_hochberg([candidates[i]["p_people"] for i in selected]), strict=True
    ):
        q_people[i] = adjusted

    findings: list[DriftFinding] = []
    for index, (candidate, q, q_p) in enumerate(zip(candidates, q_values, q_people, strict=True)):
        unit: UnitHalves = candidate["unit"]
        rest_earlier, rest_later = candidate["rest"]
        years_earlier, years_later = candidate["years"]
        if index in selected and q_p <= fdr:
            status = FLAGGED
        elif index in selected:
            status = CONCENTRATED
        else:
            status = NOT_FLAGGED
        elsewhere_change = candidate["elsewhere_change"]
        expected = None
        if elsewhere_change is not None:
            expected = unit.earlier.actions / years_earlier * elsewhere_change * years_later
        excess_cost, formula = None, ""
        if status == FLAGGED and expected is not None:
            excess = Decimal(str(round(max(unit.later.actions - expected, 0.0), 1)))
            per_action = (unit.later.net / unit.later.actions).quantize(CENT)
            excess_cost = (excess * per_action).quantize(CENT)
            formula = (
                f"{excess} later actions more than expected x {_money(per_action)} average "
                f"net cost per action in this unit in the later half (an estimate, not part "
                f"of the cost baseline)"
            )
        fields = {
            "status": status,
            "topic_label": unit.topic_label,
            "earlier_actions": unit.earlier.actions,
            "earlier_people": unit.earlier.people,
            "earlier_employee_years": years_earlier,
            "later_actions": unit.later.actions,
            "later_people": unit.later.people,
            "later_employee_years": years_later,
            "own_change": candidate["own_change"],
            "elsewhere_earlier_actions": rest_earlier.actions,
            "elsewhere_later_actions": rest_later.actions,
            "elsewhere_change": elsewhere_change,
            "ratio": relative(candidate),
            "q_value": q,
            "q_value_people": q_p,
        }
        job, place, topic = candidate["key"]
        findings.append(
            DriftFinding(
                row=job,
                col=place,
                row_label=unit.row_label,
                col_label=unit.col_label,
                topic=topic,
                expected_later_actions=expected,
                p_value=candidate["p_value"],
                earlier_net=unit.earlier.net,
                later_net=unit.later.net,
                estimated_excess_cost=excess_cost,
                explanation=_explain(fields, len(compared), fdr, min_ratio, min_people),
                formula=formula,
                cohorts=(
                    unit.earlier.cohort,
                    unit.later.cohort,
                    unit.earlier.cohort | unit.later.cohort,
                    rest_earlier.cohort,
                    rest_later.cohort,
                ),
                **fields,
            )
        )

    order = {FLAGGED: 0, CONCENTRATED: 1, NOT_FLAGGED: 2}
    findings.sort(
        key=lambda f: (order[f.status], f.q_value, f.row_label, f.col_label, f.topic_label)
    )
    return DriftReport(
        findings=findings,
        not_assessed=sorted(
            {(n.row, n.col): n for n in not_assessed}.values(),
            key=lambda n: (n.row_label, n.col_label),
        ),
        comparisons=len(compared),
        by=by,
        min_people=min_people,
        earlier_start=earlier[0] if earlier else None,
        earlier_end=earlier[1] if earlier else None,
        later_start=later[0] if later else None,
        later_end=later[1] if later else None,
        false_discovery_rate=fdr,
        min_rate_ratio=min_ratio,
        unreviewed_settings=tuple(i for i in SETTING_IDS if settings.get(i).is_placeholder),
    )
