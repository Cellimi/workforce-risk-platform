"""
Data ingestion, schema normalization, and feature engineering.
"""

import pandas as pd
import numpy as np
from io import StringIO


CANONICAL_SCHEMA = ["date", "unit", "metric", "value"]

REQUIRED_METRICS = [
    "headcount",
    "leave_usage",
    "grievances",
    "workers_comp_claims",
    "attrition",
    "workload_volume",
]


def load_csv(file_obj) -> pd.DataFrame:
    """Load CSV from file path or file-like object, normalize to canonical schema."""
    if isinstance(file_obj, str):
        df = pd.read_csv(file_obj)
    else:
        df = pd.read_csv(file_obj)

    # Normalize column names
    df.columns = [c.strip().lower().replace(" ", "_") for c in df.columns]

    # Validate schema
    for col in CANONICAL_SCHEMA:
        if col not in df.columns:
            raise ValueError(f"Missing required column: '{col}'. Expected columns: {CANONICAL_SCHEMA}")

    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["date", "value"])
    return df[CANONICAL_SCHEMA]


def pivot_wide(df: pd.DataFrame) -> pd.DataFrame:
    """Pivot long format to wide (one row per unit/date, columns per metric)."""
    wide = df.pivot_table(index=["date", "unit"], columns="metric", values="value", aggfunc="sum").reset_index()
    wide.columns.name = None
    # Fill missing metrics with 0
    for m in REQUIRED_METRICS:
        if m not in wide.columns:
            wide[m] = 0.0
    return wide.sort_values(["unit", "date"]).reset_index(drop=True)


def engineer_features(wide: pd.DataFrame) -> pd.DataFrame:
    """Add rolling averages, percent changes, ratios, and lag variables."""
    out = []
    for unit, grp in wide.groupby("unit"):
        grp = grp.copy().sort_values("date").reset_index(drop=True)

        for metric in REQUIRED_METRICS:
            if metric not in grp.columns:
                grp[metric] = 0.0
            col = grp[metric]

            # Rolling averages
            grp[f"{metric}_roll7"]  = col.rolling(7,  min_periods=1).mean()
            grp[f"{metric}_roll30"] = col.rolling(30, min_periods=1).mean()
            grp[f"{metric}_roll60"] = col.rolling(60, min_periods=1).mean()

            # % change vs prior 4 weeks
            grp[f"{metric}_pct_chg"] = col.pct_change(periods=4).fillna(0) * 100

            # Lag 30 days
            grp[f"{metric}_lag30"] = col.shift(4).bfill()  # 4 weeks ≈ 30 days

        # Ratios per 100 employees
        hc = grp["headcount"].replace(0, np.nan)
        for metric in ["grievances", "workers_comp_claims", "attrition", "leave_usage"]:
            if metric in grp.columns:
                grp[f"{metric}_per100"] = (grp[metric] / hc * 100).fillna(0)

        out.append(grp)

    result = pd.concat(out, ignore_index=True)
    return result


def get_latest_snapshot(features: pd.DataFrame) -> pd.DataFrame:
    """Return the most recent row per unit."""
    return features.sort_values("date").groupby("unit").last().reset_index()
