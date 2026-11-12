"""Server-side state: one `EngineState` per engagement, and the registry that hands them out.

An `EngineState` holds a loaded dataset, the engagement's assumption overrides and a cache
of costing runs. Nothing in it is the system of record: the dataset is re-read from the
engagement's folder and the overrides from the database, so a restart loses no work.

A costing run is deterministic in (dataset, mode, overrides), so runs are memoized on that
key. Scenario runs with overrides get their own cache entry and never disturb the base run.

Disclosure ledgers are held per caller session, because the differencing guard only works if
it remembers what that caller has already been shown.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from wri_engine.adapters.base import SourceAdapter, ValidationReport
from wri_engine.adapters.county_hr_csv import CountyHrCsvAdapter
from wri_engine.adapters.template import SOURCE_ID as TEMPLATE_SOURCE
from wri_engine.adapters.template import TemplateAdapter
from wri_engine.aggregation.suppression import DEFAULT_MIN_CELL_SIZE, DisclosureLedger
from wri_engine.costing.assumptions import AssumptionSet, Mode, load_assumptions
from wri_engine.costing.engine import CostRun, run_costing
from wri_engine.orgconfig import OrgConfig, default_org_config, load_org_config
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.schema import CanonicalDataset
from wri_engine.store import Store


class NoDataset(RuntimeError):
    """The engagement has no source files yet. The API turns this into a 409."""


ADAPTERS: dict[str, type[SourceAdapter]] = {
    "county_hr_csv": CountyHrCsvAdapter,
    TEMPLATE_SOURCE: TemplateAdapter,
}
#: Written beside an engagement's source files: which adapter reads them, and with what
#: mapping. The leading underscore keeps it out of reach of uploads, whose names must start
#: with a letter or digit.
SOURCE_MARKER = "_wri_source.json"


def make_adapter(source: str, *, mapping: dict | None = None, client_name: str = "Client"):
    """Build the adapter for a source. The county adapter always describes Harlow County;
    the template adapter builds the organization from the client's own data."""
    if source == TEMPLATE_SOURCE:
        return TemplateAdapter(mapping=mapping, agency_name=client_name)
    if source == "county_hr_csv":
        return CountyHrCsvAdapter(org=default_org_config())
    raise KeyError(source)


def read_marker(directory: Path) -> dict:
    marker = Path(directory) / SOURCE_MARKER
    if marker.exists():
        return json.loads(marker.read_text())
    return {}


def write_marker(directory: Path, source: str, mapping: dict | None) -> None:
    (Path(directory) / SOURCE_MARKER).write_text(
        json.dumps({"source": source, "mapping": mapping}, indent=2, sort_keys=True)
    )


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
    #: Where this engagement's source files live. None means the built-in synthetic demo.
    default_source: Path | None = None
    #: The client's name, used to label an organization built from template data.
    client_name: str = "Client"
    #: Engagement-level assumption values (the client's own figures). They apply to every
    #: run, including the baseline a scenario is compared against.
    engagement_overrides: dict = field(default_factory=dict)
    _runs: dict[tuple, CostRun] = field(default_factory=dict)
    _ledgers: dict[str, DisclosureLedger] = field(default_factory=dict)

    # -- loading -----------------------------------------------------------
    def load(
        self,
        path: Path | str | None = None,
        source: str | None = None,
        mapping: dict | None = None,
    ) -> ValidationReport:
        """Read source files into this workspace. With no `source`, the choice recorded
        beside the files is used, so a restart reloads them the way they were loaded."""
        path = Path(path or self.default_source or SYNTHETIC_DIR)
        if source is None:
            marker = read_marker(path)
            source = marker.get("source", "county_hr_csv")
            mapping = marker.get("mapping")
        adapter = make_adapter(source, mapping=mapping, client_name=self.client_name)
        self.dataset = adapter.load(path)
        self.org = adapter.org
        self.report = adapter.report
        self.source_id = source
        self.source_path = path
        self.manifest = self._read_manifest(path)
        self.as_of = self._resolve_as_of()
        self._runs.clear()
        self._ledgers.clear()
        return self.report

    def ensure_loaded(self) -> None:
        if self.dataset is not None:
            return
        if self.default_source is not None and not any(self.default_source.glob("*.csv")):
            raise NoDataset("no dataset has been uploaded to this engagement yet")
        self.load()

    def set_engagement_overrides(self, overrides: dict) -> None:
        self.engagement_overrides = dict(overrides)
        self._runs.clear()
        self._ledgers.clear()

    @property
    def effective_assumptions(self) -> AssumptionSet:
        """The registry with this engagement's overrides applied."""
        if not self.engagement_overrides:
            return self.assumptions
        return self.assumptions.with_overrides(self.engagement_overrides)

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
        overrides = {**self.engagement_overrides, **(overrides or {})}
        key = (mode, tuple(sorted((k, str(v)) for k, v in overrides.items())))
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


#: The single built-in workspace used in demo mode.
STATE = EngineState()


class Workspaces:
    """Hands out one `EngineState` per engagement and keeps their files apart.

    Each engagement's source files live in `<data>/engagements/<id>/source/`. Engagement ids
    are generated by the store (hex only), and the path is checked anyway, so a caller can
    never steer a read or a write into another engagement's folder.
    """

    def __init__(self, root: Path, store: Store):
        self.root = Path(root)
        self.store = store
        self._states: dict[str, EngineState] = {}
        self._lock = threading.Lock()

    def source_dir(self, engagement_id: str) -> Path:
        if not engagement_id.isalnum():
            raise ValueError("invalid engagement id")
        path = (self.root / engagement_id / "source").resolve()
        if self.root.resolve() not in path.parents:
            raise ValueError("invalid engagement id")
        return path

    def intake_dir(self, engagement_id: str) -> Path:
        """Files a consultant has uploaded but not yet committed as the dataset."""
        return self.source_dir(engagement_id).parent / "intake"

    def draft_mapping_file(self, engagement_id: str) -> Path:
        return self.source_dir(engagement_id).parent / "mapping_draft.json"

    def get(self, engagement_id: str) -> EngineState:
        with self._lock:
            state = self._states.get(engagement_id)
            if state is None:
                source = self.source_dir(engagement_id)
                source.mkdir(parents=True, exist_ok=True)
                engagement = self.store.get_engagement(engagement_id)
                state = EngineState(
                    default_source=source,
                    client_name=engagement.client_name if engagement else "Client",
                )
                state.engagement_overrides = {
                    o.assumption_id: o.value for o in self.store.overrides(engagement_id)
                }
                self._states[engagement_id] = state
            return state

    def refresh_overrides(self, engagement_id: str) -> None:
        self.get(engagement_id).set_engagement_overrides(
            {o.assumption_id: o.value for o in self.store.overrides(engagement_id)}
        )

    def forget(self) -> None:
        """Drop every cached state. Used by tests to prove nothing is lost on restart."""
        with self._lock:
            self._states.clear()
