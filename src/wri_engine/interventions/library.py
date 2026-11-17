"""Starting figures for common interventions, from `config/intervention_library.yaml`.

A consultant may start an appraisal from one of these instead of a blank form, then change
any figure to the client's own. The library is a convenience and never a finding: nothing
here is used unless a person chooses it, and choosing it says nothing about what is causing
a unit's pattern.

Every figure is an assumption with the cost registry's shape and rules: fetched by id, and a
missing id is an error. A figure is one of three things:

* **a placeholder** (`TBD-MIKE`): no published source;
* **researched** (`researched`): sourced, and not yet confirmed by the owner;
* **confirmed** (`confirmed`): the owner has said so, in his own words, to a session.

On 2026-11-17 the owner's research sourced six of the twelve, now `researched`; the other
six are costs with no published benchmark and stay placeholders. Until a figure is
confirmed, an appraisal that carries it says so wherever it is shown, the client report
included. Only the owner confirms a figure; a session never does it on its own reading.

**What the evidence measured.** For three of the four entries the studies behind the share
of actions prevented measured absences and lateness, not discipline. Each entry therefore
says which types of misconduct it is aimed at (`aims_at`), and an appraisal started from
it is aimed at those, so its saving counts only those actions. If a consultant keeps the
library's share but aims it more widely, the appraisal says that the evidence does not
cover the wider aim.

For the share of actions prevented, an assumption's low, value and high are the
appraisal's low, expected and high readings.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from wri_engine.costing.assumptions import AssumptionSet, load_assumptions
from wri_engine.paths import CONFIG_DIR

LIBRARY_FILE = CONFIG_DIR / "intervention_library.yaml"
FIELDS = ("one_time_cost_per_person", "annual_cost_per_person", "reduction")
#: The figures of an intervention a library entry supplies, and how to say each.
FIGURE_LABELS = {
    "one_time_cost_per_person": "the one-time cost per person",
    "annual_cost_per_person": "the yearly cost per person",
    "reduction": "the share of actions prevented",
}
RESEARCHED_NOTE = (
    'Researched figures: {figures} {verb} the product\'s starting {noun} for "{label}", '
    "sourced but not yet confirmed by the owner, and not this client's own."
)
PLACEHOLDER_NOTE = (
    'Placeholder figures: {figures} {verb} the product\'s starting {noun} for "{label}", '
    "with no published source and not reviewed, and not this client's own. The client's "
    "own figures, from payroll or the benefits administrator, should replace {them}."
)
WIDER_AIM_NOTE = (
    'The share of actions prevented is the library\'s figure for "{label}", which comes from '
    "studies of {evidence}. Here it is applied to discipline those studies did not measure."
)


def _say(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    label: str
    description: str
    #: Cause ids from `config/interventions.yaml` this kind of intervention is aimed at.
    answers: tuple[str, ...]
    #: Misconduct type ids its evidence covers. Empty: every type.
    aims_at: tuple[str, ...]
    #: What the studies behind its share prevented measured, in words.
    evidence: str
    one_time_cost_per_person: Decimal
    annual_cost_per_person: Decimal
    reduction_low: Decimal
    reduction_expected: Decimal
    reduction_high: Decimal
    #: The assumption id behind each figure.
    assumption_ids: dict[str, str]
    #: Assumption ids the owner has not confirmed: placeholders and researched alike.
    unreviewed: tuple[str, ...]
    sources: tuple[str, ...]
    #: Of those, the ones with no published source.
    placeholders: tuple[str, ...] = ()

    @property
    def placeholder_fields(self) -> tuple[str, ...]:
        return tuple(f for f in FIELDS if self.assumption_ids[f] in self.placeholders)

    @property
    def researched_fields(self) -> tuple[str, ...]:
        return tuple(
            f
            for f in FIELDS
            if self.assumption_ids[f] in self.unreviewed
            and self.assumption_ids[f] not in self.placeholders
        )

    @property
    def note(self) -> str:
        """Which of its figures are placeholders, and which are researched but not yet
        confirmed. Empty once the owner has confirmed all three."""
        said = []
        fields = self.placeholder_fields
        if fields:
            names = _say([FIGURE_LABELS[f] for f in fields])
            one = len(fields) == 1
            said.append(
                f"{names[0].upper() + names[1:]} "
                f"{'is a placeholder' if one else 'are placeholders'}: no published source. "
                f"Replace {'it' if one else 'them'} with the client's own."
            )
        fields = self.researched_fields
        if fields:
            names = _say([FIGURE_LABELS[f] for f in fields])
            one = len(fields) == 1
            said.append(
                f"{names[0].upper() + names[1:]} {'is' if one else 'are'} researched and "
                f"sourced, not yet confirmed by the owner."
            )
        return " ".join(said)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "answers": list(self.answers),
            "aims_at": list(self.aims_at),
            "evidence": self.evidence,
            "one_time_cost_per_person": str(self.one_time_cost_per_person),
            "annual_cost_per_person": str(self.annual_cost_per_person),
            "reduction_low": str(self.reduction_low),
            "reduction_expected": str(self.reduction_expected),
            "reduction_high": str(self.reduction_high),
            "assumption_ids": dict(self.assumption_ids),
            "unreviewed": list(self.unreviewed),
            "placeholder_fields": list(self.placeholder_fields),
            "researched_fields": list(self.researched_fields),
            "sources": list(self.sources),
            "note": self.note,
        }


@functools.lru_cache(maxsize=4)
def load_library(path: Path | None = None) -> dict[str, LibraryEntry]:
    """The library, by id, in the order the file lists it."""
    path = path or LIBRARY_FILE
    raw = yaml.safe_load(path.read_text())
    figures: AssumptionSet = load_assumptions(path)
    entries: dict[str, LibraryEntry] = {}
    for item in raw["library"]:
        ids = {name: str(item[name]) for name in FIELDS}
        figures.require_all(list(ids.values()))
        reduction = figures.get(ids["reduction"])
        held = [figures.get(i) for i in ids.values()]
        entries[str(item["id"])] = LibraryEntry(
            id=str(item["id"]),
            label=str(item["label"]),
            description=str(item["description"]),
            answers=tuple(str(c) for c in item.get("answers", [])),
            aims_at=tuple(str(t) for t in item.get("aims_at", [])),
            evidence=str(item["evidence"]),
            one_time_cost_per_person=figures.value(ids["one_time_cost_per_person"]),
            annual_cost_per_person=figures.value(ids["annual_cost_per_person"]),
            reduction_low=reduction.low,
            reduction_expected=reduction.value,
            reduction_high=reduction.high,
            assumption_ids=ids,
            unreviewed=tuple(a.id for a in held if a.status != "confirmed"),
            sources=tuple(dict.fromkeys(a.source for a in held)),
            placeholders=tuple(a.id for a in held if a.is_placeholder),
        )
    return entries


def _stands(entry: LibraryEntry, field: str, proposal) -> bool:
    """Whether a proposal's figure is still exactly the library entry's."""
    if field == "reduction":
        return (proposal.reduction_low, proposal.reduction_expected, proposal.reduction_high) == (
            entry.reduction_low,
            entry.reduction_expected,
            entry.reduction_high,
        )
    return getattr(proposal, field) == getattr(entry, field)


def fields_from_library(proposal, library_id: str, library: dict[str, LibraryEntry]) -> tuple:
    """The figures of a proposal that stand exactly as the named library entry gives them.

    Worked out on the server from the figures themselves, so a caller cannot claim a figure
    is its own by saying so, and one that has been changed to the client's is not marked.
    An id the library does not hold marks nothing.
    """
    entry = library.get(library_id)
    if entry is None:
        return ()
    return tuple(field for field in FIELDS if _stands(entry, field, proposal))


def placeholder_note(proposal, library: dict[str, LibraryEntry]) -> str:
    """What to say beside an appraisal that rests on a library figure it should not be
    taken on trust for. Empty when there is nothing to say.

    Two things are said, each when it applies:

    * a figure taken from the library that the owner has not confirmed is a placeholder or
      researched-but-unconfirmed, and says which; one that no longer matches the library
      (an earlier value since replaced) was never confirmed, and counts as a placeholder;
    * the library's share of actions prevented, kept but aimed at types of misconduct its
      evidence did not measure, is applied beyond its evidence.
    """
    entry = library.get(proposal.library_id)
    if entry is None or not proposal.library_fields:
        return ""
    said = []
    kept = [f for f in proposal.library_fields if f in FIGURE_LABELS]
    placeholders = [
        f
        for f in kept
        if entry.assumption_ids[f] in entry.placeholders or not _stands(entry, f, proposal)
    ]
    researched = [
        f for f in kept if f not in placeholders and entry.assumption_ids[f] in entry.unreviewed
    ]
    for fields, template in ((placeholders, PLACEHOLDER_NOTE), (researched, RESEARCHED_NOTE)):
        if not fields:
            continue
        names = _say([FIGURE_LABELS[f] for f in fields])
        one = len(fields) == 1
        said.append(
            template.format(
                figures=names[0].upper() + names[1:],
                verb="is" if one else "are",
                noun="figure" if one else "figures",
                label=entry.label,
                them="it" if one else "them",
            )
        )
    wider = not proposal.targets or not set(proposal.targets) <= set(entry.aims_at)
    if "reduction" in proposal.library_fields and entry.aims_at and wider:
        said.append(WIDER_AIM_NOTE.format(label=entry.label, evidence=entry.evidence))
    return " ".join(said)
