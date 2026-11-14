"""Filesystem anchors. Every other module asks here rather than guessing."""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parents[1]


def _env_path(var: str, default: Path) -> Path:
    raw = os.environ.get(var)
    return Path(raw).expanduser().resolve() if raw else default


CONFIG_DIR = _env_path("WRI_CONFIG_DIR", PROJECT_ROOT / "config")
DATA_DIR = _env_path("WRI_DATA_DIR", PROJECT_ROOT / "data")
SYNTHETIC_DIR = DATA_DIR / "synthetic"
ASSUMPTIONS_FILE = CONFIG_DIR / "assumptions.yaml"
ORG_CONFIG_FILE = CONFIG_DIR / "org_county.yaml"
GENERATOR_PROFILE_FILE = CONFIG_DIR / "generator_profile.yaml"
AUDIT_LOG_FILE = _env_path("WRI_AUDIT_LOG", DATA_DIR / "audit_log.jsonl")
