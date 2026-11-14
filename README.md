# Workforce Risk Intelligence

> **Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli.
> All rights reserved — see [LICENSE](LICENSE). Reading this repository grants no right to
> use, copy, modify or distribute the code.
>
> **All data here is synthetic and depicts a fictional county.** Nothing in this repository
> is derived from any real agency's records.

This repository holds **two separate projects**. They share no code, and they are at very
different stages. Don't mistake one for the other.

| | What it is | Status |
|---|---|---|
| [**`wri-engine/`**](wri-engine/) | **Phase 1 of the WRI engine: the fully loaded cost of discipline.** Joins HR records to discipline records and reports what discipline costs, with every dollar traceable to its formula and the assumption behind it. Canonical schema, source adapters, cost engine, assumptions registry, suppression and role gating, FastAPI service, Streamlit demo. 146 tests. | **Current work.** Start here. |
| **the root directory** (`app.py`, `models/`, `utils/`, `data/`) | The **earlier MVP**: a forecasting prototype that predicts unit-level risk 30–90 days out. Described below. | Prototype. Superseded in direction by `wri-engine/`; kept for reference. |

The engine is the product direction. The MVP was an earlier exploration, and its forecasting
and risk-scoring approach is *not* what `wri-engine/` does — Phase 1 is deliberately a cost
baseline with no prediction or scoring in it at all.

---

# The earlier MVP — forecasting prototype

A predictive analytics tool that ingests HR and operational data, forecasts workforce risks 30–90 days out, ranks organizational units by risk level, and generates leadership-ready executive summaries.

---

## What This Does

| Feature | Description |
|---|---|
| **Data Ingestion** | Accepts CSV files in canonical schema (date, unit, metric, value) |
| **Feature Engineering** | Rolling averages (7/30/60-day), % changes, per-100-employee ratios, lag variables |
| **Forecasting** | 30/60/90-day time-series forecasting via Facebook Prophet (linear fallback) |
| **Risk Classification** | Gradient Boosting model classifies units as Low / Medium / High risk |
| **Recommendations** | Rule-based intervention mapping (safety, HR, retention, staffing) |
| **Dashboard** | Streamlit web UI with risk rankings, forecast charts, and unit deep-dives |
| **Executive Summary** | Auto-generated text report with risks, projections, and recommended actions |

---

## Quick Start

### 1. Install dependencies

```bash
cd workforce_risk_platform
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

> **Note:** Prophet requires `pystan`. On some systems you may need:
> ```bash
> pip install pystan==2.19.1.1 prophet
> ```

### 2. Generate sample data (optional)

```bash
python data/generate_synthetic_data.py
# Outputs: workforce_data.csv (8 units × ~130 weeks × 6 metrics ≈ 6,200 rows)
```

### 3. Run the app

```bash
streamlit run app.py
```

The dashboard opens at **http://localhost:8501**

---

## Usage

1. **Select data source** in the sidebar — use built-in sample data or upload your own CSV
2. Click **▶ Run Analysis**
3. Explore the five tabs:
   - **Risk Rankings** — all units sorted by risk with key drivers
   - **Forecasts** — 30/60/90-day projections per unit and metric
   - **Unit Deep-Dive** — detailed view with trend charts and recommendations
   - **Model Insights** — feature importance and risk scatter plot
   - **Executive Summary** — generate and download a leadership brief

---

## CSV Format

Your CSV must have exactly these columns:

```
date,unit,metric,value
2024-01-01,Unit_Alpha,headcount,120
2024-01-01,Unit_Alpha,grievances,2.0
2024-01-01,Unit_Alpha,workers_comp_claims,1.5
...
```

**Supported metrics:**
- `headcount`
- `leave_usage`
- `grievances`
- `workers_comp_claims`
- `attrition`
- `workload_volume`

---

## Project Structure

```
workforce_risk_platform/
├── app.py                          # Main Streamlit dashboard
├── requirements.txt
├── data/
│   └── generate_synthetic_data.py  # Synthetic data generator
├── models/
│   └── risk_model.py               # Forecasting + risk classification
└── utils/
    ├── data_processing.py           # Ingestion, normalization, feature engineering
    ├── recommendations.py           # Rule-based intervention engine
    └── executive_summary.py         # Auto-summary generator
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Language | Python 3.10+ |
| Data processing | Pandas, NumPy |
| ML | scikit-learn (GradientBoostingClassifier) |
| Forecasting | Facebook Prophet |
| Dashboard | Streamlit |
| Charts | Plotly |

---

## Deployment (Post-MVP)

**Streamlit Cloud (easiest):**
```bash
# Push to GitHub → connect at share.streamlit.io
```

**AWS EC2:**
```bash
pip install -r requirements.txt
streamlit run app.py --server.port 80 --server.address 0.0.0.0
```

---

## Scope & Limitations

This is an MVP prototype for validation and demos:

- ✅ Works locally with CSV data
- ✅ Demonstrates full pipeline end-to-end
- ❌ No authentication or multi-tenant support
- ❌ No API integrations (HRIS, payroll systems)
- ❌ No real-time data pipelines
- ❌ Model accuracy not optimized — proof-of-concept only

---

## Estimated Build Time

~3–4 weeks for one mid-level Python developer.

---

*Built to the MVP brief for the Workforce Risk Intelligence Platform.*

---

## License

Copyright (c) 2026 Michael V. Celli. All rights reserved. See [LICENSE](LICENSE).

The code in this repository is **published for viewing, not licensed for use**. `wri-engine/`
carries its own copy of the same license so that directory remains self-contained.
