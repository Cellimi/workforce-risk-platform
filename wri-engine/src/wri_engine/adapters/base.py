"""The source-adapter contract.

The engine is source-system agnostic. It reads the canonical schema and nothing else. Each
source system -- a county HR flat-file export today, NFC or Oracle HCM later -- gets one
adapter that translates its format into `CanonicalDataset` and reports what it could not
translate.

Two methods, deliberately:

    load(path) -> CanonicalDataset        everything that parsed
    validation_report() -> list[Issue]    everything that did not, and why

A `blocking` issue removes the affected action from cost totals. The count of exclusions is
always shown -- records are never silently dropped.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from wri_engine.schema import CanonicalDataset


class Severity(StrEnum):
    BLOCKING = "blocking"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    """One thing wrong with one record.

    `blocks_action_ids` names the discipline actions that cannot be costed because of this
    issue. An employee-level problem (a missing salary) blocks every action for that person.
    """

    rule_id: str
    severity: Severity
    entity: str
    entity_id: str
    message: str
    field: str | None = None
    blocks_action_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "severity": str(self.severity),
            "entity": self.entity,
            "entity_id": self.entity_id,
            "message": self.message,
            "field": self.field,
            "blocked_actions": list(self.blocks_action_ids),
        }


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)

    def add(self, issue: Issue) -> None:
        self.issues.append(issue)

    @property
    def blocking(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.BLOCKING]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def blocked_action_ids(self) -> set[str]:
        out: set[str] = set()
        for issue in self.blocking:
            out.update(issue.blocks_action_ids)
        return out

    def counts_by_rule(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for issue in self.issues:
            counts[issue.rule_id] = counts.get(issue.rule_id, 0) + 1
        return dict(sorted(counts.items()))

    def as_dict(self) -> dict:
        return {
            "total_issues": len(self.issues),
            "blocking": len(self.blocking),
            "warnings": len(self.warnings),
            "excluded_actions": len(self.blocked_action_ids),
            "counts_by_rule": self.counts_by_rule(),
            "issues": [i.as_dict() for i in self.issues],
        }


class SourceAdapter(abc.ABC):
    """One per source system. Implementations must not leak source vocabulary upward."""

    source_id: str

    def __init__(self) -> None:
        self._report = ValidationReport()

    @abc.abstractmethod
    def load(self, path: Path) -> CanonicalDataset:
        """Read the source export at `path` and return canonical records.

        Records that cannot be translated are omitted from the dataset and recorded as
        issues. This method must not raise on bad data -- bad data is a reportable outcome,
        not a crash.
        """

    def validation_report(self) -> list[Issue]:
        return list(self._report.issues)

    @property
    def report(self) -> ValidationReport:
        return self._report
