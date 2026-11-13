# The February 2026 prototype — kept for reference

> **This is not the product.** It is the first Workforce Risk Intelligence prototype, built in
> February 2026 and superseded by the engine in [`../wri-engine/`](../wri-engine/). It is kept
> so the earlier thinking is on record. Nobody is developing it, and it shares no code with
> the engine.
>
> **Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli. All
> rights reserved — see [`../LICENSE`](../LICENSE). All data here is synthetic.

## How it differs from the engine

| | This prototype | The engine |
|---|---|---|
| Question | Which units are likely to be in trouble in 30–90 days? | What did discipline actually cost, and why? |
| Data | Weekly totals per unit: headcount, leave, grievances, claims, attrition, workload | Individual HR and discipline records |
| Method | Forecasting and a risk classifier | Counts a cost only when the record shows the event happened; nothing is predicted |
| Output | Low / Medium / High risk per unit, with suggested interventions | Dollars per action, each traced to its formula and assumption |

Forecasting, risk scoring and intervention recommendations are exactly what the engine leaves
out of its first phase on purpose — see "Phase boundary" in
[`../wri-engine/CLAUDE.md`](../wri-engine/CLAUDE.md). If that work is taken up later it will be
built in the engine, on its data and with its traceability, not ported from here.

---

## What it does

A predictive analytics tool that ingests HR and operational data, forecasts workforce risks
30–90 days out, ranks organizational units by risk level, and generates executive summaries.

| Feature | Description |
|---|---|
| **Data ingestion** | Accepts CSV files in one layout: `date, unit, metric, value` |
| **Feature engineering** | Rolling averages (7/30/60-day), % changes, per-100-employee ratios, lag variables |
| **Forecasting** | 30/60/90-day projections. Uses Facebook Prophet if it is installed; otherwise a linear trend. Prophet is not in `requirements.txt`, so a plain install uses the linear trend |
| **Risk classification** | A gradient boosting model classifies units as Low / Medium / High risk |
| **Recommendations** | Rule-based intervention mapping (safety, HR, retention, staffing) |
| **Dashboard** | Streamlit web UI with risk rankings, forecast charts and unit deep-dives |
| **Executive summary** | A generated text report with risks, projections and recommended actions |

## Running it

From this folder:

```bash
cd legacy
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

The dashboard opens at **http://localhost:8501**. Choose the built-in sample data or upload a
CSV in the sidebar, click **Run Analysis**, and explore the five tabs: Risk Rankings,
Forecasts, Unit Deep-Dive, Model Insights and Executive Summary.

To regenerate the sample data:

```bash
python data/generate_synthetic_data.py
# writes workforce_data.csv: 8 units x ~130 weeks x 6 metrics
```

## CSV layout

```
date,unit,metric,value
2024-01-01,Unit_Alpha,headcount,120
2024-01-01,Unit_Alpha,grievances,2.0
2024-01-01,Unit_Alpha,workers_comp_claims,1.5
```

Supported metrics: `headcount`, `leave_usage`, `grievances`, `workers_comp_claims`,
`attrition`, `workload_volume`.

## What is in this folder

```
legacy/
├── app.py                          the Streamlit dashboard
├── requirements.txt
├── data/
│   ├── generate_synthetic_data.py  synthetic data generator
│   └── sample_workforce_data.csv   a generated sample
├── models/
│   └── risk_model.py               forecasting and risk classification
└── utils/
    ├── data_processing.py          ingestion, normalization, feature engineering
    ├── recommendations.py          rule-based intervention mapping
    └── executive_summary.py        summary generator
```

## Limits it always had

It was a proof of concept for demonstrations:

- No sign-in and no separation between clients.
- No connection to an HR or payroll system; CSV only.
- The risk model was never tuned or validated, so its Low / Medium / High labels are
  illustrative, not a measured prediction.
- Nothing in it explains a figure the way the engine does.
