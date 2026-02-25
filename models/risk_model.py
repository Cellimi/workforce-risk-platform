"""
Forecasting (Prophet-based) and risk classification models.
"""

import pandas as pd
import numpy as np
import warnings
warnings.filterwarnings("ignore")

from sklearn.ensemble import GradientBoostingClassifier
from sklearn.preprocessing import LabelEncoder
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


TARGET_METRICS = ["grievances", "workers_comp_claims", "attrition"]
FORECAST_HORIZONS = [30, 60, 90]  # days


# ─────────────────────────────────────────────────────────────────────────────
# Time-series forecasting
# ─────────────────────────────────────────────────────────────────────────────

def _try_prophet(series_df: pd.DataFrame, horizon_days: int) -> pd.DataFrame | None:
    """Try to fit Prophet and return forecast. Returns None on failure."""
    try:
        from prophet import Prophet
        m = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False)
        m.fit(series_df)
        future = m.make_future_dataframe(periods=horizon_days // 7, freq="W")
        forecast = m.predict(future)
        return forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(horizon_days // 7)
    except Exception:
        return None


def _linear_forecast(series: pd.Series, horizon_days: int) -> pd.DataFrame:
    """Simple linear extrapolation fallback."""
    n = len(series)
    x = np.arange(n)
    if n < 2:
        slope, intercept = 0, float(series.iloc[-1]) if n else 0
    else:
        slope, intercept = np.polyfit(x, series.values, 1)

    last_date = series.index[-1] if hasattr(series.index, 'freq') else pd.Timestamp.now()
    future_x = np.arange(n, n + horizon_days // 7)
    future_dates = pd.date_range(last_date, periods=horizon_days // 7 + 1, freq="W")[1:]
    yhat = intercept + slope * future_x

    return pd.DataFrame({
        "ds": future_dates[:len(yhat)],
        "yhat": np.clip(yhat, 0, None),
        "yhat_lower": np.clip(yhat * 0.85, 0, None),
        "yhat_upper": yhat * 1.15,
    })


def forecast_metric(df_long: pd.DataFrame, unit: str, metric: str) -> dict:
    """
    Forecast a single metric for a single unit.
    Returns dict with keys: 30d, 60d, 90d → forecast DataFrames,
    and 'history' → historical series.
    """
    subset = (
        df_long[(df_long["unit"] == unit) & (df_long["metric"] == metric)]
        .copy()
        .sort_values("date")
        .dropna(subset=["value"])
    )

    if len(subset) < 8:
        return {}

    series_df = subset[["date", "value"]].rename(columns={"date": "ds", "value": "y"})
    history = subset.set_index("date")["value"]

    results = {"history": history}

    # Try Prophet, fall back to linear
    prophet_ok = False
    try:
        from prophet import Prophet
        m = Prophet(yearly_seasonality=True, weekly_seasonality=False, daily_seasonality=False,
                    interval_width=0.80)
        m.fit(series_df)
        for h in FORECAST_HORIZONS:
            future = m.make_future_dataframe(periods=h // 7, freq="W")
            forecast = m.predict(future)
            results[f"{h}d"] = forecast[["ds", "yhat", "yhat_lower", "yhat_upper"]].tail(h // 7)
        prophet_ok = True
    except Exception:
        pass

    if not prophet_ok:
        series = history
        for h in FORECAST_HORIZONS:
            results[f"{h}d"] = _linear_forecast(series, h)

    return results


def forecast_all_units(df_long: pd.DataFrame) -> dict:
    """
    Returns nested dict: {unit: {metric: forecast_result}}
    """
    results = {}
    units = df_long["unit"].unique()
    for unit in units:
        results[unit] = {}
        for metric in TARGET_METRICS:
            results[unit][metric] = forecast_metric(df_long, unit, metric)
    return results


# ─────────────────────────────────────────────────────────────────────────────
# Risk classification
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    "grievances_roll30", "grievances_pct_chg", "grievances_per100",
    "workers_comp_claims_roll30", "workers_comp_claims_pct_chg", "workers_comp_claims_per100",
    "attrition_roll30", "attrition_pct_chg",
    "leave_usage_roll30", "leave_usage_pct_chg",
    "workload_volume_roll30", "workload_volume_pct_chg",
    "workload_volume_lag30",
]


def _assign_label(row) -> str:
    """Rule-based label for training data generation."""
    score = 0
    if row.get("grievances_per100", 0) > 5: score += 2
    elif row.get("grievances_per100", 0) > 2: score += 1
    if row.get("workers_comp_claims_per100", 0) > 3: score += 2
    elif row.get("workers_comp_claims_per100", 0) > 1: score += 1
    if row.get("attrition_pct_chg", 0) > 25: score += 2
    elif row.get("attrition_pct_chg", 0) > 10: score += 1
    if row.get("workload_volume_pct_chg", 0) > 30: score += 1
    if score >= 4: return "High"
    elif score >= 2: return "Medium"
    return "Low"


def train_risk_model(features: pd.DataFrame):
    """Train a gradient boosting classifier for risk level."""
    df = features.copy()

    # Generate labels via rules
    df["risk_label"] = df.apply(_assign_label, axis=1)

    available_cols = [c for c in FEATURE_COLS if c in df.columns]
    X = df[available_cols].fillna(0).replace([np.inf, -np.inf], 0)
    y = df["risk_label"]

    if len(X) < 10 or y.nunique() < 2:
        return None, available_cols, None

    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    model = Pipeline([
        ("scaler", StandardScaler()),
        ("clf", GradientBoostingClassifier(n_estimators=100, max_depth=3, random_state=42)),
    ])
    model.fit(X, y_enc)
    return model, available_cols, le


def predict_risk(model, feature_cols, le, snapshot: pd.DataFrame) -> pd.DataFrame:
    """Predict risk level for each unit using latest snapshot."""
    if model is None:
        # Rule-based fallback
        snapshot = snapshot.copy()
        snapshot["risk_level"] = snapshot.apply(_assign_label, axis=1)
        snapshot["risk_score"] = snapshot["risk_level"].map({"Low": 1, "Medium": 2, "High": 3})
        return snapshot[["unit", "risk_level", "risk_score"]]

    available_cols = [c for c in feature_cols if c in snapshot.columns]
    X = snapshot[available_cols].fillna(0).replace([np.inf, -np.inf], 0)
    proba = model.predict_proba(X)
    pred = model.predict(X)

    result = snapshot[["unit"]].copy()
    result["risk_level"] = le.inverse_transform(pred)
    # Risk score = weighted probability
    class_order = {c: i for i, c in enumerate(le.classes_)}
    score_map = {"Low": 1, "Medium": 2, "High": 3}
    result["risk_score"] = result["risk_level"].map(score_map)
    return result.sort_values("risk_score", ascending=False).reset_index(drop=True)


def get_feature_importance(model, feature_cols) -> pd.DataFrame:
    """Extract feature importances from trained model."""
    if model is None:
        return pd.DataFrame({"feature": feature_cols, "importance": [1 / len(feature_cols)] * len(feature_cols)})
    clf = model.named_steps["clf"]
    imp = clf.feature_importances_
    available_cols = feature_cols[:len(imp)]
    df = pd.DataFrame({"feature": available_cols, "importance": imp})
    return df.sort_values("importance", ascending=False).reset_index(drop=True)
