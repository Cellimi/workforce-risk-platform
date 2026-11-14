"""
Workforce Risk Intelligence Platform — Streamlit Dashboard MVP
Run with: streamlit run app.py
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import plotly.express as px
from datetime import datetime
import warnings
warnings.filterwarnings("ignore")

# ── Local modules
from utils.data_processing import load_csv, pivot_wide, engineer_features, get_latest_snapshot
from models.risk_model import train_risk_model, predict_risk, get_feature_importance, TARGET_METRICS
from utils.recommendations import get_all_recommendations
from utils.executive_summary import generate_executive_summary


# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Workforce Risk Intelligence",
    page_icon="🛡️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Custom CSS
# ─────────────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .main-header {
        font-size: 2rem;
        font-weight: 700;
        color: #1a1a2e;
        margin-bottom: 0.25rem;
    }
    .sub-header {
        font-size: 1rem;
        color: #6c757d;
        margin-bottom: 1.5rem;
    }
    .risk-high {
        background-color: #ffe0e0;
        border-left: 5px solid #dc3545;
        padding: 0.75rem 1rem;
        border-radius: 4px;
        margin-bottom: 0.5rem;
    }
    .risk-medium {
        background-color: #fff3cd;
        border-left: 5px solid #ffc107;
        padding: 0.75rem 1rem;
        border-radius: 4px;
        margin-bottom: 0.5rem;
    }
    .risk-low {
        background-color: #d4edda;
        border-left: 5px solid #28a745;
        padding: 0.75rem 1rem;
        border-radius: 4px;
        margin-bottom: 0.5rem;
    }
    .rec-urgent { color: #dc3545; font-weight: 600; }
    .rec-high   { color: #e07b00; font-weight: 600; }
    .rec-medium { color: #007bff; font-weight: 600; }
    .metric-card {
        background: #f8f9fa;
        border-radius: 8px;
        padding: 1rem;
        text-align: center;
    }
    .stTabs [data-baseweb="tab-list"] { gap: 12px; }
    .stTabs [data-baseweb="tab"] { padding: 8px 20px; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Session state helpers
# ─────────────────────────────────────────────────────────────────────────────
def reset_state():
    for key in ["df_long", "features", "snapshot", "risk_df", "recommendations", "forecasts", "model", "feature_cols", "le"]:
        if key in st.session_state:
            del st.session_state[key]


@st.cache_data(show_spinner="Generating synthetic data…")
def load_synthetic_data():
    """Generate and return synthetic dataset."""
    from data.generate_synthetic_data import generate_data
    return generate_data()


@st.cache_data(show_spinner="Processing data…")
def process_data(df_long_hash):
    return None  # placeholder; actual work done inline


def risk_badge(level: str) -> str:
    colors = {"High": "#dc3545", "Medium": "#ffc107", "Low": "#28a745"}
    text_col = {"High": "#fff", "Medium": "#333", "Low": "#fff"}
    c = colors.get(level, "#6c757d")
    t = text_col.get(level, "#fff")
    return f'<span style="background:{c};color:{t};padding:3px 10px;border-radius:12px;font-size:0.85rem;font-weight:600">{level}</span>'


def priority_badge(p: str) -> str:
    colors = {"Urgent": "#dc3545", "High": "#e07b00", "Medium": "#007bff", "Low": "#6c757d"}
    c = colors.get(p, "#6c757d")
    return f'<span style="background:{c};color:#fff;padding:2px 8px;border-radius:10px;font-size:0.78rem;font-weight:600">{p}</span>'


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.image("https://img.icons8.com/ios-filled/50/1a1a2e/protect.png", width=40)
    st.markdown("### 🛡️ Workforce Risk Intelligence")
    st.markdown("---")

    org_name = st.text_input("Organization Name", value="Acme Corp")
    st.markdown("---")

    st.markdown("**Data Source**")
    data_source = st.radio("", ["Use Sample Data", "Upload CSV"], label_visibility="collapsed")

    uploaded_file = None
    if data_source == "Upload CSV":
        uploaded_file = st.file_uploader(
            "Upload workforce CSV",
            type=["csv"],
            help="Expected columns: date, unit, metric, value"
        )
        st.markdown("""
        **Required metrics:**
        - headcount
        - leave_usage
        - grievances
        - workers_comp_claims
        - attrition
        - workload_volume
        """)

    st.markdown("---")

    run_analysis = st.button("▶ Run Analysis", type="primary", use_container_width=True)

    if "risk_df" in st.session_state:
        st.markdown("---")
        st.markdown("**Quick Stats**")
        rdf = st.session_state["risk_df"]
        h = len(rdf[rdf["risk_level"] == "High"])
        m = len(rdf[rdf["risk_level"] == "Medium"])
        l = len(rdf[rdf["risk_level"] == "Low"])
        st.markdown(f"🔴 High Risk: **{h}** units")
        st.markdown(f"🟡 Medium Risk: **{m}** units")
        st.markdown(f"🟢 Low Risk: **{l}** units")


# ─────────────────────────────────────────────────────────────────────────────
# Header
# ─────────────────────────────────────────────────────────────────────────────
st.markdown('<div class="main-header">🛡️ Workforce Risk Intelligence Platform</div>', unsafe_allow_html=True)
st.markdown(f'<div class="sub-header">Predictive workforce analytics — {org_name} &nbsp;|&nbsp; {datetime.today().strftime("%B %d, %Y")}</div>', unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────────────
# Run analysis pipeline
# ─────────────────────────────────────────────────────────────────────────────
if run_analysis:
    reset_state()
    with st.spinner("Loading and processing data…"):
        try:
            if data_source == "Use Sample Data":
                df_long = load_synthetic_data()
            else:
                if uploaded_file is None:
                    st.error("Please upload a CSV file.")
                    st.stop()
                df_long = load_csv(uploaded_file)

            st.session_state["df_long"] = df_long

            wide = pivot_wide(df_long)
            features = engineer_features(wide)
            snapshot = get_latest_snapshot(features)

            st.session_state["features"] = features
            st.session_state["snapshot"] = snapshot

        except Exception as e:
            st.error(f"Data processing error: {e}")
            st.stop()

    with st.spinner("Training risk model…"):
        model, feature_cols, le = train_risk_model(features)
        st.session_state["model"] = model
        st.session_state["feature_cols"] = feature_cols
        st.session_state["le"] = le

        risk_df = predict_risk(model, feature_cols, le, snapshot)
        st.session_state["risk_df"] = risk_df

    with st.spinner("Generating recommendations…"):
        recommendations = get_all_recommendations(snapshot, risk_df)
        st.session_state["recommendations"] = recommendations

    with st.spinner("Running forecasts (this may take a moment)…"):
        from models.risk_model import forecast_all_units
        forecasts = forecast_all_units(df_long)
        st.session_state["forecasts"] = forecasts

    st.success("✅ Analysis complete!")
    st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Landing / placeholder if no analysis run yet
# ─────────────────────────────────────────────────────────────────────────────
if "risk_df" not in st.session_state:
    st.info("👈 Select a data source in the sidebar and click **▶ Run Analysis** to begin.")

    col1, col2, col3 = st.columns(3)
    with col1:
        st.markdown("#### 📊 Risk Scoring")
        st.markdown("Units ranked by risk level (Low/Medium/High) using ML classification trained on workforce patterns.")
    with col2:
        st.markdown("#### 📈 30–90 Day Forecasting")
        st.markdown("Time-series forecasting of grievances, workers' comp claims, and attrition.")
    with col3:
        st.markdown("#### 🎯 Targeted Recommendations")
        st.markdown("Evidence-based interventions matched to specific risk drivers in each unit.")
    st.stop()


# ─────────────────────────────────────────────────────────────────────────────
# Main dashboard (after analysis)
# ─────────────────────────────────────────────────────────────────────────────
risk_df       = st.session_state["risk_df"]
snapshot      = st.session_state["snapshot"]
features      = st.session_state["features"]
recommendations = st.session_state["recommendations"]
forecasts     = st.session_state["forecasts"]
df_long       = st.session_state["df_long"]
model         = st.session_state["model"]
feature_cols  = st.session_state["feature_cols"]
le            = st.session_state["le"]

# ── KPI row
col1, col2, col3, col4 = st.columns(4)
n_units = len(risk_df)
n_high  = len(risk_df[risk_df["risk_level"] == "High"])
n_med   = len(risk_df[risk_df["risk_level"] == "Medium"])
n_low   = len(risk_df[risk_df["risk_level"] == "Low"])

with col1:
    st.metric("Total Units Analyzed", n_units)
with col2:
    st.metric("🔴 High Risk", n_high, delta=None)
with col3:
    st.metric("🟡 Medium Risk", n_med)
with col4:
    st.metric("🟢 Low Risk", n_low)

st.markdown("---")

# ─────────────────────────────────────────────────────────────────────────────
# Tabs
# ─────────────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🏆 Risk Rankings",
    "📈 Forecasts",
    "🔍 Unit Deep-Dive",
    "⚙️ Model Insights",
    "📋 Executive Summary",
])


# ════════════════════════════════════════════════════════
# TAB 1 — Risk Rankings
# ════════════════════════════════════════════════════════
with tab1:
    st.subheader("Units Ranked by Risk Level")

    # Risk summary bar chart
    risk_counts = risk_df["risk_level"].value_counts().reindex(["High", "Medium", "Low"]).fillna(0)
    fig_bar = go.Figure(go.Bar(
        x=risk_counts.index,
        y=risk_counts.values,
        marker_color=["#dc3545", "#ffc107", "#28a745"],
        text=risk_counts.values.astype(int),
        textposition="outside",
    ))
    fig_bar.update_layout(
        title="Units by Risk Level",
        xaxis_title="Risk Level",
        yaxis_title="# Units",
        height=280,
        margin=dict(t=40, b=20),
        plot_bgcolor="white",
    )
    st.plotly_chart(fig_bar, use_container_width=True)

    st.markdown("---")

    # Unit risk table with details
    merged = risk_df.merge(snapshot, on="unit", how="left")
    for _, row in risk_df.iterrows():
        unit = row["unit"]
        level = row["risk_level"]
        css_class = f"risk-{level.lower()}"

        snap_row = snapshot[snapshot["unit"] == unit]
        metric_str = ""
        if not snap_row.empty:
            r = snap_row.iloc[0]
            parts = []
            if "grievances_per100" in r: parts.append(f"Grievances/100: {r['grievances_per100']:.1f}")
            if "workers_comp_claims_per100" in r: parts.append(f"WC/100: {r['workers_comp_claims_per100']:.1f}")
            if "attrition_pct_chg" in r: parts.append(f"Attrition Δ: {r['attrition_pct_chg']:+.1f}%")
            if "workload_volume_pct_chg" in r: parts.append(f"Workload Δ: {r['workload_volume_pct_chg']:+.1f}%")
            metric_str = "  &nbsp;|&nbsp;  ".join(parts)

        recs = recommendations.get(unit, [])
        rec_str = ", ".join([r["action"] for r in recs[:2]]) if recs else "No immediate actions"

        st.markdown(f"""
        <div class="{css_class}">
            <strong>{unit}</strong> &nbsp; {risk_badge(level)}<br>
            <small>{metric_str}</small><br>
            <small>💡 {rec_str}</small>
        </div>
        """, unsafe_allow_html=True)


# ════════════════════════════════════════════════════════
# TAB 2 — Forecasts
# ════════════════════════════════════════════════════════
with tab2:
    st.subheader("30/60/90-Day Forecasts")

    col_u, col_m = st.columns(2)
    with col_u:
        selected_unit = st.selectbox("Select Unit", risk_df["unit"].tolist(), key="fc_unit")
    with col_m:
        selected_metric = st.selectbox("Select Metric", TARGET_METRICS, key="fc_metric",
                                       format_func=lambda x: x.replace("_", " ").title())

    fc = forecasts.get(selected_unit, {}).get(selected_metric, {})

    if not fc or "history" not in fc:
        st.warning("No forecast data available for this unit/metric combination.")
    else:
        history = fc["history"]
        fig = go.Figure()

        # Historical line
        fig.add_trace(go.Scatter(
            x=history.index,
            y=history.values,
            mode="lines",
            name="Historical",
            line=dict(color="#1a1a2e", width=2),
        ))

        colors_h = {"30d": "#e07b00", "60d": "#007bff", "90d": "#dc3545"}
        for horizon, color in colors_h.items():
            if horizon in fc and not fc[horizon].empty:
                fc_df = fc[horizon]
                fig.add_trace(go.Scatter(
                    x=fc_df["ds"],
                    y=fc_df["yhat"],
                    mode="lines",
                    name=f"Forecast {horizon}",
                    line=dict(color=color, dash="dash", width=2),
                ))
                # Confidence band for 90d
                if horizon == "90d" and "yhat_lower" in fc_df.columns:
                    fig.add_trace(go.Scatter(
                        x=pd.concat([fc_df["ds"], fc_df["ds"][::-1]]),
                        y=pd.concat([fc_df["yhat_upper"], fc_df["yhat_lower"][::-1]]),
                        fill="toself",
                        fillcolor="rgba(220,53,69,0.10)",
                        line=dict(color="rgba(255,255,255,0)"),
                        name="90d CI",
                        showlegend=False,
                    ))

        fig.update_layout(
            title=f"{selected_unit} — {selected_metric.replace('_', ' ').title()} Forecast",
            xaxis_title="Date",
            yaxis_title="Value",
            height=420,
            legend=dict(orientation="h", yanchor="bottom", y=1.02),
            plot_bgcolor="white",
            xaxis=dict(gridcolor="#e9ecef"),
            yaxis=dict(gridcolor="#e9ecef"),
        )
        st.plotly_chart(fig, use_container_width=True)

        # Forecast summary table
        summary_rows = []
        for h in ["30d", "60d", "90d"]:
            if h in fc and not fc[h].empty:
                avg = fc[h]["yhat"].mean()
                peak = fc[h]["yhat"].max()
                summary_rows.append({"Horizon": h, "Avg/Week": f"{avg:.2f}", "Peak/Week": f"{peak:.2f}"})
        if summary_rows:
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)


# ════════════════════════════════════════════════════════
# TAB 3 — Unit Deep-Dive
# ════════════════════════════════════════════════════════
with tab3:
    st.subheader("Unit Deep-Dive")

    unit_sel = st.selectbox("Select Unit", risk_df["unit"].tolist(), key="deep_unit")

    unit_risk = risk_df[risk_df["unit"] == unit_sel].iloc[0]
    level = unit_risk["risk_level"]
    css_class = f"risk-{level.lower()}"

    st.markdown(f"""
    <div class="{css_class}">
        <strong>{unit_sel}</strong> &nbsp; {risk_badge(level)}
    </div>
    """, unsafe_allow_html=True)

    # Metric trend charts
    st.markdown("#### Metric Trends")
    unit_data = df_long[df_long["unit"] == unit_sel]

    metric_cols = st.columns(2)
    for i, metric in enumerate(["grievances", "workers_comp_claims", "attrition", "workload_volume"]):
        metric_data = unit_data[unit_data["metric"] == metric].sort_values("date")
        with metric_cols[i % 2]:
            if not metric_data.empty:
                fig = px.line(
                    metric_data, x="date", y="value",
                    title=metric.replace("_", " ").title(),
                    labels={"value": "Value", "date": ""},
                )
                fig.update_traces(line_color="#1a1a2e")
                fig.update_layout(
                    height=220,
                    margin=dict(t=35, b=10, l=10, r=10),
                    plot_bgcolor="white",
                    xaxis=dict(gridcolor="#e9ecef"),
                    yaxis=dict(gridcolor="#e9ecef"),
                )
                st.plotly_chart(fig, use_container_width=True)

    # Recommendations
    st.markdown("#### Recommended Interventions")
    recs = recommendations.get(unit_sel, [])
    if recs:
        for r in recs:
            st.markdown(
                f"**{priority_badge(r['priority'])} {r['action']}** — {r['category']}<br>"
                f"<small>{r['detail']}</small>",
                unsafe_allow_html=True
            )
            st.markdown("")
    else:
        st.success("No immediate interventions recommended for this unit.")

    # Snapshot metrics table
    st.markdown("#### Latest Metrics Snapshot")
    snap_row = snapshot[snapshot["unit"] == unit_sel]
    if not snap_row.empty:
        display_cols = [c for c in snap_row.columns if any(
            m in c for m in ["grievances", "workers_comp", "attrition", "workload", "leave", "headcount"]
        ) and not c.endswith("_lag30")]
        st.dataframe(
            snap_row[display_cols].T.rename(columns={snap_row.index[0]: "Value"}).round(2),
            use_container_width=True,
        )


# ════════════════════════════════════════════════════════
# TAB 4 — Model Insights
# ════════════════════════════════════════════════════════
with tab4:
    st.subheader("Model Insights & Feature Importance")

    feat_imp = get_feature_importance(model, feature_cols)

    fig_imp = go.Figure(go.Bar(
        x=feat_imp["importance"].head(12),
        y=feat_imp["feature"].head(12),
        orientation="h",
        marker_color="#1a1a2e",
    ))
    fig_imp.update_layout(
        title="Top Risk Drivers (Feature Importance)",
        xaxis_title="Importance",
        yaxis_title="",
        height=420,
        yaxis=dict(autorange="reversed"),
        plot_bgcolor="white",
        xaxis=dict(gridcolor="#e9ecef"),
        margin=dict(l=200),
    )
    st.plotly_chart(fig_imp, use_container_width=True)

    st.markdown("#### Risk Distribution Across Units")
    # Scatter: attrition_pct_chg vs grievances_per100, colored by risk
    plot_df = snapshot.merge(risk_df[["unit", "risk_level"]], on="unit", how="left")
    x_col = "workload_volume_pct_chg" if "workload_volume_pct_chg" in plot_df.columns else plot_df.columns[2]
    y_col = "grievances_per100" if "grievances_per100" in plot_df.columns else plot_df.columns[3]

    color_map = {"High": "#dc3545", "Medium": "#ffc107", "Low": "#28a745"}
    fig_scatter = px.scatter(
        plot_df,
        x=x_col,
        y=y_col,
        color="risk_level",
        text="unit",
        color_discrete_map=color_map,
        title="Workload Change vs Grievances Rate by Unit",
        labels={x_col: "Workload Volume % Change", y_col: "Grievances per 100 Employees"},
        size_max=15,
    )
    fig_scatter.update_traces(textposition="top center", marker_size=12)
    fig_scatter.update_layout(height=400, plot_bgcolor="white")
    st.plotly_chart(fig_scatter, use_container_width=True)

    # Model info
    st.markdown("#### Model Configuration")
    st.info("""
    **Risk Classification Model:** Gradient Boosting Classifier (scikit-learn)
    - Features: rolling averages, % changes, per-100-employee ratios, lag variables
    - Labels: Rule-based (Low/Medium/High) derived from workforce thresholds
    - Forecasting: Facebook Prophet (time-series) with linear fallback
    - This is an MVP prototype — not a production-grade model.
    """)


# ════════════════════════════════════════════════════════
# TAB 5 — Executive Summary
# ════════════════════════════════════════════════════════
with tab5:
    st.subheader("Auto-Generated Executive Summary")

    st.markdown("Click the button below to generate a leadership-ready summary of current workforce risks.")

    if st.button("📋 Generate Executive Summary", type="primary"):
        summary = generate_executive_summary(
            risk_df=risk_df,
            recommendations=recommendations,
            forecasts=forecasts,
            snapshot=snapshot,
            org_name=org_name,
        )
        st.session_state["exec_summary"] = summary

    if "exec_summary" in st.session_state:
        summary = st.session_state["exec_summary"]
        st.text_area("Executive Summary", summary, height=600)

        # Download button
        st.download_button(
            label="⬇ Download Summary (.txt)",
            data=summary,
            file_name=f"workforce_risk_summary_{datetime.today().strftime('%Y%m%d')}.txt",
            mime="text/plain",
        )
