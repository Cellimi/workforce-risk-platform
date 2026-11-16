"""Phase 2: detection. Which units' discipline stands out, and what that costs.

Detection reads the cost baseline and never changes it. Nothing here scores a person.
"""

from wri_engine.detection.exposure import Exposure, employee_years
from wri_engine.detection.hotspots import (
    BY_MISCONDUCT,
    OVERALL,
    HotspotReport,
    NotAssessed,
    UnitCount,
    UnitFinding,
    find_hotspots,
    load_detection_settings,
    unit_counts,
)

__all__ = [
    "BY_MISCONDUCT",
    "OVERALL",
    "Exposure",
    "HotspotReport",
    "NotAssessed",
    "UnitCount",
    "UnitFinding",
    "employee_years",
    "find_hotspots",
    "load_detection_settings",
    "unit_counts",
]
