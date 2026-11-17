"""What an investigation found, and the appraisals kept with an engagement.

An appraisal (`appraisal.py`) is arithmetic and can be redone at any time. What cannot be
redone is the visit: someone went to the unit, talked to the people there, and came back
with a view of what is causing the pattern. This module keeps that, with the intervention
proposed for it.

What a finding is
-----------------
One or more **causes chosen from a short fixed list** (`config/interventions.yaml`), by a
person, after an investigation. The product never chooses one and never infers one from the
data. The list is short on purpose: it says what kind of condition was found without
bringing anyone's interview notes into the product. One entry, "Other", takes a line of
words. A finding records who entered it and when, and the screen says so.

What is kept, and where
-----------------------
One file per engagement, in the engagement's own folder:

    <data>/engagements/<engagement id>/interventions/appraisals.jsonl

The words typed for "Other" describe a client's workplace and are that client's
confidential material, so they are kept beside the client's files and not in the database
(CLAUDE.md rule 12), and a backup of the engagement carries them. No employee id and no
action id is written: a kept appraisal names a unit, never a person.

The file is only ever added to. Keeping an appraisal adds a line; withdrawing one adds a
line saying who withdrew it and when. Nothing is rewritten and nothing is removed (rule
15), so the file is also the history. A line half-written when the machine stopped is
skipped on reading.

Only the inputs are kept: the unit, the causes, and the consultant's figures for the
intervention. The saving is worked out afresh each time it is shown, from the cost baseline
as it then stands, so a corrected client file or a changed assumption is never contradicted
by a stale figure.
"""

from __future__ import annotations

import functools
import json
import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

import yaml

from wri_engine.interventions.appraisal import Intervention
from wri_engine.paths import CONFIG_DIR

CAUSES_FILE = CONFIG_DIR / "interventions.yaml"
FILE_NAME = "appraisals.jsonl"
KEPT, WITHDRAWN = "kept", "withdrawn"


@dataclass(frozen=True)
class Cause:
    id: str
    label: str
    description: str
    takes_text: bool = False


@dataclass(frozen=True)
class CauseList:
    causes: tuple[Cause, ...]
    other_text_max_length: int

    def get(self, cause_id: str) -> Cause | None:
        return next((c for c in self.causes if c.id == cause_id), None)

    def as_dict(self) -> dict:
        return {
            "causes": [
                {
                    "id": c.id,
                    "label": c.label,
                    "description": c.description,
                    "takes_text": c.takes_text,
                }
                for c in self.causes
            ],
            "other_text_max_length": self.other_text_max_length,
        }


@functools.lru_cache(maxsize=4)
def load_causes(path: Path | None = None) -> CauseList:
    """The causes an investigation may record. A file without them is an error, not an
    empty list."""
    raw = yaml.safe_load((path or CAUSES_FILE).read_text())
    causes = tuple(
        Cause(
            id=str(entry["id"]),
            label=str(entry["label"]),
            description=str(entry["description"]),
            takes_text=bool(entry.get("takes_text", False)),
        )
        for entry in raw["causes"]
    )
    if not causes:
        raise ValueError("config/interventions.yaml lists no causes")
    return CauseList(causes, int(raw["other_text_max_length"]))


def clean_text(text: str) -> str:
    """A line of words: surrounding space removed, and any run of space or line breaks
    inside it folded to one space."""
    return " ".join(text.split())


def finding_problems(cause_ids: list[str], other_text: str, allowed: CauseList) -> list[str]:
    """Why a finding cannot be kept, one line each. Empty when it can."""
    found = []
    if not cause_ids:
        found.append(
            "say what the investigation found: choose at least one cause. An appraisal is "
            "kept as the record of a finding, and this product does not suggest one"
        )
    unknown = sorted(set(cause_ids) - {c.id for c in allowed.causes})
    if unknown:
        found.append(f"{', '.join(unknown)} is not a cause on the list")
    if len(set(cause_ids)) != len(cause_ids):
        found.append("a cause can be chosen once")
    takes_text = {c.id for c in allowed.causes if c.takes_text}
    wants_text = bool(takes_text & set(cause_ids))
    text = clean_text(other_text)
    if wants_text and not text:
        found.append('say what "Other" is, in a line')
    if text and not wants_text:
        found.append('words are kept only with "Other": choose it, or clear the words')
    if len(text) > allowed.other_text_max_length:
        found.append(
            f'"Other" is a line, not a set of notes: at most '
            f"{allowed.other_text_max_length} characters, and this is {len(text)}"
        )
    return found


@dataclass(frozen=True)
class KeptAppraisal:
    """An intervention proposed for a unit, with what was found there. Inputs only."""

    id: str
    row: str
    col: str
    #: The type of misconduct, or empty for every type together.
    topic: str
    intervention: Intervention
    causes: tuple[str, ...]
    other_cause: str
    #: Whose finding it is, as the screen will say it, and when it was entered.
    entered_by: str
    entered_at: str
    withdrawn_by: str = ""
    withdrawn_at: str = ""

    @property
    def is_withdrawn(self) -> bool:
        return bool(self.withdrawn_at)

    def as_line(self) -> dict:
        i = self.intervention
        return {
            "event": KEPT,
            "id": self.id,
            "row": self.row,
            "col": self.col,
            "topic": self.topic,
            "causes": list(self.causes),
            "other_cause": self.other_cause,
            "entered_by": self.entered_by,
            "entered_at": self.entered_at,
            "intervention": {
                "name": i.name,
                "one_time_cost": str(i.one_time_cost),
                "annual_cost_per_person": str(i.annual_cost_per_person),
                "people_covered": i.people_covered,
                "other_annual_cost": str(i.other_annual_cost),
                "years": i.years,
                "reduction_low": str(i.reduction_low),
                "reduction_expected": str(i.reduction_expected),
                "reduction_high": str(i.reduction_high),
                "basis": i.basis,
                "one_time_cost_per_person": str(i.one_time_cost_per_person),
                "library_id": i.library_id,
                "library_fields": list(i.library_fields),
            },
        }

    @classmethod
    def from_line(cls, line: dict) -> KeptAppraisal:
        i = line["intervention"]
        return cls(
            id=line["id"],
            row=line["row"],
            col=line["col"],
            topic=line["topic"],
            causes=tuple(line["causes"]),
            other_cause=line["other_cause"],
            entered_by=line["entered_by"],
            entered_at=line["entered_at"],
            intervention=Intervention(
                name=i["name"],
                one_time_cost=Decimal(i["one_time_cost"]),
                annual_cost_per_person=Decimal(i["annual_cost_per_person"]),
                people_covered=int(i["people_covered"]),
                other_annual_cost=Decimal(i["other_annual_cost"]),
                years=int(i["years"]),
                reduction_low=Decimal(i["reduction_low"]),
                reduction_expected=Decimal(i["reduction_expected"]),
                reduction_high=Decimal(i["reduction_high"]),
                basis=i["basis"],
                # Lines written before the library existed carry none of these.
                one_time_cost_per_person=Decimal(i.get("one_time_cost_per_person", "0")),
                library_id=i.get("library_id", ""),
                library_fields=tuple(i.get("library_fields", ())),
            ),
        )


def _replay(lines: list[dict]) -> list[KeptAppraisal]:
    """The appraisals a list of events leaves, in the order they were kept."""
    kept: dict[str, KeptAppraisal] = {}
    for line in lines:
        if line.get("event") == KEPT:
            kept[line["id"]] = KeptAppraisal.from_line(line)
        elif line.get("event") == WITHDRAWN and line.get("id") in kept:
            earlier = kept[line["id"]]
            kept[line["id"]] = KeptAppraisal(
                **{
                    **earlier.__dict__,
                    "withdrawn_by": line["withdrawn_by"],
                    "withdrawn_at": line["withdrawn_at"],
                }
            )
    return list(kept.values())


class AppraisalBook:
    """An engagement's kept appraisals. On disk when given a folder; in memory otherwise,
    as the synthetic walkthrough is."""

    def __init__(self, folder: Path | None = None) -> None:
        self.path = folder / FILE_NAME if folder else None
        self._memory: list[dict] = []
        self._lock = threading.Lock()

    def _lines(self) -> list[dict]:
        if self.path is None:
            return list(self._memory)
        if not self.path.exists():
            return []
        lines = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            try:
                line = json.loads(raw)
            except json.JSONDecodeError:
                continue  # half-written when the machine stopped; it was never confirmed
            if isinstance(line, dict):
                lines.append(line)
        return lines

    def _add(self, line: dict) -> None:
        if self.path is None:
            self._memory.append(line)
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def everything(self) -> list[KeptAppraisal]:
        """Every appraisal ever kept, withdrawn ones included, oldest first."""
        with self._lock:
            return _replay(self._lines())

    def current(self) -> list[KeptAppraisal]:
        return [a for a in self.everything() if not a.is_withdrawn]

    def keep(self, appraisal: KeptAppraisal) -> None:
        """Add one. It is on disk before this returns."""
        with self._lock:
            if any(line.get("id") == appraisal.id for line in self._lines()):
                raise ValueError(f"an appraisal with id {appraisal.id} is already kept")
            self._add(appraisal.as_line())

    def withdraw(self, appraisal_id: str, by: str, at: str) -> bool:
        """Mark one as withdrawn. Nothing is removed. False when there is none to withdraw."""
        with self._lock:
            live = {a.id for a in _replay(self._lines()) if not a.is_withdrawn}
            if appraisal_id not in live:
                return False
            self._add(
                {"event": WITHDRAWN, "id": appraisal_id, "withdrawn_by": by, "withdrawn_at": at}
            )
            return True
