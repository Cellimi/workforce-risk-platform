from __future__ import annotations

import os

# The pre-existing API tests exercise the synthetic walkthrough, which is demo mode. The
# licensed (signed-in) mode builds its own app in tests/test_accounts_engagements.py.
os.environ.setdefault("WRI_AUTH_MODE", "demo")

from datetime import date
from pathlib import Path

import pytest

from wri_engine.adapters.county_hr_csv import CountyHrCsvAdapter
from wri_engine.costing.assumptions import load_assumptions
from wri_engine.costing.engine import run_costing
from wri_engine.orgconfig import load_org_config
from wri_engine.paths import SYNTHETIC_DIR

SAMPLE_DIR = SYNTHETIC_DIR / "sample"


@pytest.fixture(scope="session")
def org():
    return load_org_config()


@pytest.fixture(scope="session")
def assumptions():
    return load_assumptions()


@pytest.fixture(scope="session")
def sample_dir() -> Path:
    if not (SAMPLE_DIR / "hr_empl_master.csv").exists():
        pytest.skip("sample export missing; run `make generate`")
    return SAMPLE_DIR


@pytest.fixture(scope="session")
def loaded(org, sample_dir):
    adapter = CountyHrCsvAdapter(org=org)
    data = adapter.load(sample_dir)
    return data, adapter.report


@pytest.fixture(scope="session")
def run(loaded, org, assumptions):
    data, report = loaded
    return run_costing(
        data,
        as_of=date(2026, 9, 17),
        org=org,
        assumptions=assumptions,
        excluded_action_ids=report.blocked_action_ids,
    )


@pytest.fixture(autouse=True)
def _close_databases(monkeypatch):
    """Every installation a test builds opens a database. Close them all afterwards, as the
    API does on shutdown, so no connection is left for the garbage collector."""
    from wri_engine.store import db

    opened: list = []
    original = db.Store.__init__

    def tracking_init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(db.Store, "__init__", tracking_init)
    yield
    for store in opened:
        store.close()
