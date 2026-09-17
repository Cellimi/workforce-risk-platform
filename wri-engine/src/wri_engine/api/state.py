"""Server-side state: the loaded dataset, and a cache of costing runs.

A costing run is deterministic in (dataset, mode, overrides), so runs are memoized on that
key. Scenario runs with overrides get their own cache entry and never disturb the base run.

Disclosure ledgers are held per caller session, because the differencing guard only works if
it remembers what that caller has already been shown.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wri_engine.adapters.base import SourceAdapter, ValidationReport
from wri_engine.adapters.county_hr_csv import CountyHrCsvAdapter
from wri_engine.aggregation.suppression import DEFAULT_MIN_CELL_SIZE, DisclosureLedger
from wri_engine.costing.assumptions import AssumptionSet, Mode, load_assumptions
from wri_engine.costing.engine import CostRun, run_costing
from wri_engine.orgconfig import OrgConfig, load_org_config
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.schema import CanonicalDataset

ADAPTERS: dict[str, type[SourceAdapter]] = {"county_hr_csv": CountyHrCsvAdapter}


@dataclass
class EngineState:
    org: OrgConfig = field(default_factory=load_org_config)
    assumptions: AssumptionSet = field(default_factory=load_assumptions)
    min_cell_size: int = DEFAULT_MIN_CELL_SIZE
    dataset: CanonicalDataset | None = None
    report: ValidationReport | None = None
    source_id: str | None = None
    source_path: Path | None = None
    as_of: date | None = None
    manifest: dict = field(default_factory=dict)
    _runs: dict[tuple, CostRun] = field(default_factory=dict)
    _ledgers: dict[str, DisclosureLedger] = field(default_factory=dict)

    # -- loading -----------------------------------------------------------
    def load(
        self, path: Path | str | None = None, source: str = "county_hr_csv"
    ) -> ValidationReport:
        path = Path(path or SYNTHETIC_DIR)
        adapter = ADAPTERS[source](org=self.org)
        self.dataset = adapter.load(path)
        self.report = adapter.report
        self.source_id = source
        self.source_path = path
        self.manifest = self._read_manifest(path)
        self.as_of = self._resolve_as_of()
        self._runs.clear()
        self._ledgers.clear()
        return self.report

    def ensure_loaded(self) -> None:
        if self.dataset is None:
            self.load()

    @staticmethod
    def _read_manifest(path: Path) -> dict:
        manifest = path / "manifest.json"
        if manifest.exists():
            return json.loads(manifest.read_text())
        return {}

    def _resolve_as_of(self) -> date:
        raw = self.manifest.get("as_of_date")
        if raw:
            return date.fromisoformat(raw)
        dates = [a.decision_date for a in self.dataset.actions] if self.dataset else []
        return max(dates) if dates else date.today()

    # -- runs --------------------------------------------------------------
    def run(self, mode: Mode = "base", overrides: dict | None = None) -> CostRun:
        self.ensure_loaded()
        key = (mode, tuple(sorted((overrides or {}).items())))
        if key not in self._runs:
            self._runs[key] = run_costing(
                self.dataset,
                as_of=self.as_of,
                org=self.org,
                assumptions=self.assumptions,
                mode=mode,
                overrides=overrides,
                excluded_action_ids=self.report.blocked_action_ids if self.report else (),
            )
        return self._runs[key]

    def ledger(self, session_key: str) -> DisclosureLedger:
        if session_key not in self._ledgers:
            self._ledgers[session_key] = DisclosureLedger(min_cell_size=self.min_cell_size)
        return self._ledgers[session_key]

    def reset_ledger(self, session_key: str) -> None:
        self._ledgers.pop(session_key, None)

    @property
    def agency_name(self) -> str:
        return self.org.agency_name


STATE = EngineState()
