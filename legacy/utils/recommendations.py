"""
Rule-based recommendation engine.
Maps risk signals to targeted intervention recommendations.
"""

import pandas as pd


RECOMMENDATION_RULES = [
    {
        "condition": lambda row: row.get("workers_comp_claims_per100", 0) > 3 or row.get("risk_level") == "High" and row.get("workers_comp_claims_per100", 0) > 1,
        "category": "Safety",
        "priority": "Urgent",
        "action": "Safety Stand-Down",
        "detail": "Conduct unit-level safety stand-down and ergonomic assessment. Review incident logs from past 60 days. Engage EHS team immediately.",
    },
    {
        "condition": lambda row: row.get("grievances_per100", 0) > 3 or (row.get("grievances_pct_chg", 0) > 25),
        "category": "Labor Relations",
        "priority": "High",
        "action": "Supervisory Engagement Review",
        "detail": "Schedule skip-level meetings with frontline supervisors. Review grievance themes for systemic issues. Consider management coaching.",
    },
    {
        "condition": lambda row: row.get("attrition_pct_chg", 0) > 15 or row.get("attrition_per100", 0) > 5,
        "category": "Retention",
        "priority": "High",
        "action": "HR Retention Outreach",
        "detail": "Deploy stay interviews in high-attrition units. Review compensation benchmarks. Identify flight-risk employees for targeted retention conversations.",
    },
    {
        "condition": lambda row: row.get("workload_volume_pct_chg", 0) > 25,
        "category": "Staffing",
        "priority": "Medium",
        "action": "Surge Staffing Assessment",
        "detail": "Evaluate temporary staffing or overtime authorization. Review workload distribution across shifts. Consider cross-training deployment.",
    },
    {
        "condition": lambda row: row.get("leave_usage_pct_chg", 0) > 20,
        "category": "Workforce Health",
        "priority": "Medium",
        "action": "Absence Pattern Review",
        "detail": "Analyze leave usage patterns for burnout indicators. Consider EAP communication or wellness initiatives. Review scheduling practices.",
    },
    {
        "condition": lambda row: row.get("risk_level") == "High",
        "category": "Executive",
        "priority": "Urgent",
        "action": "Leadership Attention Required",
        "detail": "Unit flagged as High Risk across multiple indicators. Recommend senior leadership visit and immediate intervention planning meeting.",
    },
]


def get_recommendations(unit_row: dict) -> list[dict]:
    """Return list of applicable recommendations for a unit."""
    recs = []
    for rule in RECOMMENDATION_RULES:
        try:
            if rule["condition"](unit_row):
                recs.append({
                    "category": rule["category"],
                    "priority": rule["priority"],
                    "action": rule["action"],
                    "detail": rule["detail"],
                })
        except Exception:
            pass
    # Deduplicate and sort by priority
    priority_order = {"Urgent": 0, "High": 1, "Medium": 2, "Low": 3}
    recs = sorted(recs, key=lambda x: priority_order.get(x["priority"], 9))
    seen = set()
    deduped = []
    for r in recs:
        if r["action"] not in seen:
            seen.add(r["action"])
            deduped.append(r)
    return deduped


def get_all_recommendations(snapshot: pd.DataFrame, risk_df: pd.DataFrame) -> dict:
    """
    Returns dict: {unit: [recommendations]}
    snapshot: feature-engineered snapshot (latest row per unit)
    risk_df: output of predict_risk
    """
    merged = snapshot.merge(risk_df[["unit", "risk_level", "risk_score"]], on="unit", how="left")
    result = {}
    for _, row in merged.iterrows():
        row_dict = row.to_dict()
        result[row["unit"]] = get_recommendations(row_dict)
    return result
