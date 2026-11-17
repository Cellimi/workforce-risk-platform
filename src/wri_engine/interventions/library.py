"""Starting figures for common interventions, from `config/intervention_library.yaml`.

A consultant may start an appraisal from one of these instead of a blank form, then change
any figure to the client's own. The library is a convenience and never a finding: nothing
here is used unless a person chooses it, and choosing it says nothing about what is causing
a unit's pattern.

Every figure is an assumption with the cost registry's shape and rules: fetched by id, a
missing id is an error, and an unreviewed one is TBD-MIKE. **On 2026-11-16 every figure is
a placeholder**: the owner asked the build to use placeholders and will research the
values himself. While any figure an appraisal started from is unreviewed, the appraisal
says so wherever it is shown, the client report included.

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
UNREVIEWED_NOTE = (
    'Its starting figures come from the product\'s library entry "{label}", which are '
    "placeholders: not yet researched, sourced or reviewed."
)


@dataclass(frozen=True)
class LibraryEntry:
    id: str
    label: str
    description: str
    #: Cause ids from `config/interventions.yaml` this kind of intervention is aimed at.
    answers: tuple[str, ...]
    one_time_cost_per_person: Decimal
    annual_cost_per_person: Decimal
    reduction_low: Decimal
    reduction_expected: Decimal
    reduction_high: Decimal
    #: The assumption id behind each figure.
    assumption_ids: dict[str, str]
    #: Assumption ids not yet reviewed by the owner.
    unreviewed: tuple[str, ...]
    sources: tuple[str, ...]

    @property
    def note(self) -> str:
        """What to say beside an appraisal that started here. Empty once all is reviewed."""
        return UNREVIEWED_NOTE.format(label=self.label) if self.unreviewed else ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "answers": list(self.answers),
            "one_time_cost_per_person": str(self.one_time_cost_per_person),
            "annual_cost_per_person": str(self.annual_cost_per_person),
            "reduction_low": str(self.reduction_low),
            "reduction_expected": str(self.reduction_expected),
            "reduction_high": str(self.reduction_high),
            "assumption_ids": dict(self.assumption_ids),
            "unreviewed": list(self.unreviewed),
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
            one_time_cost_per_person=figures.value(ids["one_time_cost_per_person"]),
            annual_cost_per_person=figures.value(ids["annual_cost_per_person"]),
            reduction_low=reduction.low,
            reduction_expected=reduction.value,
            reduction_high=reduction.high,
            assumption_ids=ids,
            unreviewed=tuple(a.id for a in held if a.is_placeholder),
            sources=tuple(dict.fromkeys(a.source for a in held)),
        )
    return entries


#: The figures of an intervention a library entry supplies, and how to say each.
FIGURE_LABELS = {
    "one_time_cost_per_person": "the one-time cost per person",
    "annual_cost_per_person": "the yearly cost per person",
    "reduction": "the share of actions prevented",
}
PLACEHOLDER_NOTE = (
    'Placeholder figures: {figures} {verb} the product\'s starting {noun} for "{label}", '
    "not yet researched, sourced or reviewed, and not this client's own."
)


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
    """What to say beside an appraisal that rests on a library figure the owner has not
    reviewed. Empty when it rests on none.

    A figure taken from the library counts as unreviewed while its assumption is TBD-MIKE,
    and also when it no longer matches the library: then it is an earlier placeholder that
    was since replaced.
    """
    entry = library.get(proposal.library_id)
    if entry is None or not proposal.library_fields:
        return ""
    unreviewed = [
        field
        for field in proposal.library_fields
        if field in FIGURE_LABELS
        and (entry.assumption_ids[field] in entry.unreviewed or not _stands(entry, field, proposal))
    ]
    if not unreviewed:
        return ""
    names = [FIGURE_LABELS[field] for field in unreviewed]
    figures = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
    one = len(names) == 1
    return PLACEHOLDER_NOTE.format(
        figures=figures[0].upper() + figures[1:],
        verb="is" if one else "are",
        noun="figure" if one else "figures",
        label=entry.label,
    )
