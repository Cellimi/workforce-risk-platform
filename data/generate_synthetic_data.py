"""
Generate synthetic workforce data for MVP demo.
Produces a CSV with realistic patterns including seasonal trends and anomalies.
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta

np.random.seed(42)

UNITS = [
    "Unit_Alpha", "Unit_Beta", "Unit_Gamma", "Unit_Delta",
    "Unit_Echo", "Unit_Foxtrot", "Unit_Golf", "Unit_Hotel"
]

METRICS = ["headcount", "leave_usage", "grievances", "workers_comp_claims", "attrition", "workload_volume"]

START_DATE = datetime(2022, 1, 1)
END_DATE = datetime(2024, 6, 30)

# Unit-level base parameters (some units are "high risk")
UNIT_PARAMS = {
    "Unit_Alpha":   {"headcount": 120, "risk_factor": 1.0},
    "Unit_Beta":    {"headcount": 85,  "risk_factor": 1.5},   # moderate risk
    "Unit_Gamma":   {"headcount": 200, "risk_factor": 2.5},   # high risk
    "Unit_Delta":   {"headcount": 60,  "risk_factor": 0.7},
    "Unit_Echo":    {"headcount": 150, "risk_factor": 3.0},   # highest risk
    "Unit_Foxtrot": {"headcount": 95,  "risk_factor": 0.8},
    "Unit_Golf":    {"headcount": 110, "risk_factor": 1.2},
    "Unit_Hotel":   {"headcount": 75,  "risk_factor": 1.8},
}


def make_timeseries(dates, base, noise_std, trend=0, seasonal_amp=0, risk_factor=1.0):
    n = len(dates)
    t = np.arange(n)
    # Seasonal component (annual cycle)
    seasonal = seasonal_amp * np.sin(2 * np.pi * t / 365)
    # Trend
    trend_component = trend * t / 365
    # Noise
    noise = np.random.normal(0, noise_std, n)
    values = base * risk_factor + trend_component + seasonal + noise
    return np.clip(values, 0, None)


def generate_data():
    records = []
    dates = pd.date_range(START_DATE, END_DATE, freq="W-MON")  # weekly

    for unit, params in UNIT_PARAMS.items():
        hc = params["headcount"]
        rf = params["risk_factor"]

        headcount_series = make_timeseries(dates, hc, hc * 0.02, trend=0, risk_factor=1.0)
        workload_series = make_timeseries(dates, 500 * rf, 50, trend=10 * rf, seasonal_amp=80, risk_factor=1.0)
        leave_series = make_timeseries(dates, 0.08 * hc * rf, 1.5, trend=0.005 * rf, seasonal_amp=2, risk_factor=1.0)
        grievance_series = make_timeseries(dates, 0.5 * rf, 0.8, trend=0.01 * rf, risk_factor=1.0)
        wc_series = make_timeseries(dates, 0.4 * rf, 0.5, trend=0.008 * rf, risk_factor=1.0)
        attrition_series = make_timeseries(dates, 1.5 * rf, 0.6, trend=0.015 * rf, risk_factor=1.0)

        for i, date in enumerate(dates):
            records.append({"date": date.strftime("%Y-%m-%d"), "unit": unit, "metric": "headcount",        "value": round(headcount_series[i], 1)})
            records.append({"date": date.strftime("%Y-%m-%d"), "unit": unit, "metric": "workload_volume",  "value": round(workload_series[i], 1)})
            records.append({"date": date.strftime("%Y-%m-%d"), "unit": unit, "metric": "leave_usage",      "value": round(leave_series[i], 1)})
            records.append({"date": date.strftime("%Y-%m-%d"), "unit": unit, "metric": "grievances",       "value": round(grievance_series[i], 1)})
            records.append({"date": date.strftime("%Y-%m-%d"), "unit": unit, "metric": "workers_comp_claims", "value": round(wc_series[i], 1)})
            records.append({"date": date.strftime("%Y-%m-%d"), "unit": unit, "metric": "attrition",        "value": round(attrition_series[i], 1)})

    df = pd.DataFrame(records)
    return df


if __name__ == "__main__":
    df = generate_data()
    out = "workforce_data.csv"
    df.to_csv(out, index=False)
    print(f"Generated {len(df)} rows → {out}")
    print(df.head(10))
