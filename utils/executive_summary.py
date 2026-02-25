"""
Auto-generate executive summary text from risk analysis results.
"""

from datetime import datetime
import pandas as pd


PRIORITY_ORDER = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}


def generate_executive_summary(
    risk_df: pd.DataFrame,
    recommendations: dict,
    forecasts: dict,
    snapshot: pd.DataFrame,
    org_name: str = "Organization",
) -> str:
    """Generate a text executive summary from analysis results."""

    today = datetime.today().strftime("%B %d, %Y")
    high_risk_units = risk_df[risk_df["risk_level"] == "High"]["unit"].tolist()
    med_risk_units = risk_df[risk_df["risk_level"] == "Medium"]["unit"].tolist()

    lines = []
    lines.append("=" * 70)
    lines.append("WORKFORCE RISK INTELLIGENCE — EXECUTIVE SUMMARY")
    lines.append(f"Prepared: {today}  |  Organization: {org_name}")
    lines.append("=" * 70)
    lines.append("")

    # ── Overview
    lines.append("OVERVIEW")
    lines.append("-" * 40)
    n_units = len(risk_df)
    lines.append(
        f"Analysis covers {n_units} organizational units. "
        f"{len(high_risk_units)} unit(s) are currently rated HIGH RISK, "
        f"{len(med_risk_units)} unit(s) MEDIUM RISK. "
        f"Immediate leadership attention is required for high-risk units."
    )
    lines.append("")

    # ── High-Risk Units
    if high_risk_units:
        lines.append("HIGH-RISK UNITS — IMMEDIATE ACTION REQUIRED")
        lines.append("-" * 40)
        for unit in high_risk_units:
            lines.append(f"\n▸ {unit}")

            # Key metrics from snapshot
            unit_snap = snapshot[snapshot["unit"] == unit]
            if not unit_snap.empty:
                row = unit_snap.iloc[0]
                metrics_summary = []
                if "grievances_per100" in row and row["grievances_per100"] > 0:
                    metrics_summary.append(f"Grievances: {row['grievances_per100']:.1f} per 100 employees")
                if "workers_comp_claims_per100" in row and row["workers_comp_claims_per100"] > 0:
                    metrics_summary.append(f"Workers' Comp: {row['workers_comp_claims_per100']:.1f} per 100 employees")
                if "attrition_pct_chg" in row:
                    metrics_summary.append(f"Attrition Trend: {row['attrition_pct_chg']:+.1f}% vs prior period")
                if "workload_volume_pct_chg" in row:
                    metrics_summary.append(f"Workload Change: {row['workload_volume_pct_chg']:+.1f}% vs prior period")
                if metrics_summary:
                    lines.append("  Current Indicators:")
                    for m in metrics_summary:
                        lines.append(f"    • {m}")

            # Forecasts
            unit_forecasts = forecasts.get(unit, {})
            forecast_lines = []
            for metric in ["grievances", "workers_comp_claims", "attrition"]:
                fc = unit_forecasts.get(metric, {})
                if "90d" in fc and not fc["90d"].empty:
                    projected_val = fc["90d"]["yhat"].mean()
                    forecast_lines.append(f"{metric.replace('_', ' ').title()}: ~{projected_val:.1f}/week avg over 90 days")
            if forecast_lines:
                lines.append("  90-Day Projections:")
                for f in forecast_lines:
                    lines.append(f"    • {f}")

            # Recommendations
            recs = recommendations.get(unit, [])
            if recs:
                urgent = [r for r in recs if r["priority"] in ("Urgent", "High")]
                lines.append("  Recommended Interventions:")
                for r in urgent[:3]:
                    lines.append(f"    [{r['priority']}] {r['action']}: {r['detail']}")
        lines.append("")

    # ── Medium-Risk Units
    if med_risk_units:
        lines.append("MEDIUM-RISK UNITS — MONITORING RECOMMENDED")
        lines.append("-" * 40)
        for unit in med_risk_units:
            recs = recommendations.get(unit, [])
            rec_actions = ", ".join([r["action"] for r in recs[:2]]) if recs else "Continue monitoring"
            lines.append(f"  • {unit}: {rec_actions}")
        lines.append("")

    # ── Forecasted Increases (top concerns)
    lines.append("FORECASTED RISK INCREASES (30-DAY OUTLOOK)")
    lines.append("-" * 40)
    forecast_concerns = []
    for unit in risk_df["unit"].tolist():
        for metric in ["grievances", "workers_comp_claims", "attrition"]:
            fc = forecasts.get(unit, {}).get(metric, {})
            if "30d" in fc and not fc["30d"].empty:
                snap_val = None
                unit_snap = snapshot[snapshot["unit"] == unit]
                if not unit_snap.empty and metric in unit_snap.columns:
                    snap_val = unit_snap.iloc[0][metric]
                proj = fc["30d"]["yhat"].mean()
                if snap_val and snap_val > 0:
                    pct_chg = (proj - snap_val) / snap_val * 100
                    if pct_chg > 10:
                        forecast_concerns.append({
                            "unit": unit,
                            "metric": metric,
                            "pct_chg": pct_chg,
                            "proj": proj,
                        })

    if forecast_concerns:
        forecast_concerns.sort(key=lambda x: -x["pct_chg"])
        for c in forecast_concerns[:6]:
            lines.append(
                f"  • {c['unit']} — {c['metric'].replace('_', ' ').title()} "
                f"projected to increase {c['pct_chg']:.0f}% over 30 days "
                f"(avg ~{c['proj']:.1f}/week)"
            )
    else:
        lines.append("  No significant forecasted increases in the 30-day horizon.")
    lines.append("")

    # ── Summary Recommendations
    lines.append("PRIORITY ACTIONS SUMMARY")
    lines.append("-" * 40)
    all_urgent = []
    for unit in high_risk_units:
        for r in recommendations.get(unit, []):
            if r["priority"] in ("Urgent", "High"):
                all_urgent.append((unit, r))

    if all_urgent:
        for unit, r in all_urgent[:5]:
            lines.append(f"  [{r['priority']}] {unit}: {r['action']}")
    else:
        lines.append("  No urgent actions at this time. Continue regular monitoring cadence.")

    lines.append("")
    lines.append("─" * 70)
    lines.append("This summary is auto-generated by the Workforce Risk Intelligence Platform.")
    lines.append("Findings should be reviewed by qualified HR and operations leadership.")
    lines.append("─" * 70)

    return "\n".join(lines)
