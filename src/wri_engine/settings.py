"""Runtime settings, read once from the environment when the API starts.

Two authentication modes exist, and the secure one is the default:

* ``local``  -- every request needs a signed-in user, and what that user may see comes from
  their membership of an engagement. This is the mode a licensee runs.
* ``demo``   -- the caller picks a role in a header and there is one built-in synthetic
  dataset. It exists only so the Harlow County walkthrough keeps working, and it has to be
  switched on explicitly with ``WRI_AUTH_MODE=demo``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from wri_engine.paths import DATA_DIR

AUTH_MODES = ("local", "demo")


@dataclass(frozen=True)
class Settings:
    auth_mode: str = "local"
    data_dir: Path = DATA_DIR
    database_url: str = ""
    session_hours: int = 12
    max_upload_mb: int = 50

    def __post_init__(self) -> None:
        if self.auth_mode not in AUTH_MODES:
            raise ValueError(f"WRI_AUTH_MODE must be one of {AUTH_MODES}, got {self.auth_mode!r}")
        if not self.database_url:
            object.__setattr__(self, "database_url", f"sqlite:///{Path(self.data_dir) / 'wri.db'}")

    @property
    def engagements_dir(self) -> Path:
        return Path(self.data_dir) / "engagements"

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            auth_mode=os.environ.get("WRI_AUTH_MODE", "local").strip().lower(),
            data_dir=DATA_DIR,
            database_url=os.environ.get("WRI_DATABASE_URL", ""),
            session_hours=int(os.environ.get("WRI_SESSION_HOURS", "12")),
            max_upload_mb=int(os.environ.get("WRI_MAX_UPLOAD_MB", "50")),
        )
