"""WRI Phase 1 demo UI.

Five pages over the FastAPI service. This module talks HTTP and nothing else -- it must
never import `wri_engine`. If a page needs something the API does not expose, the answer is
a new endpoint, not a shortcut.

Run it with the API already up:

    make api    # terminal one
    make ui     # terminal two
"""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from api_client import ApiError, EngineClient  # noqa: E402
from theme import colorscale, palette, style  # noqa: E402

CAPTION = (
    "Synthetic data for a fictional county. Cost figures reflect documented assumptions, "
    "not measured agency costs."
)
PAGES = [
    "Executive summary",
    "Cost matrix",
    "Drill-down",
    "Assumptions",
    "Data quality",
]
ROLES = ["executive", "hr_analyst", "admin"]
COMPONENT_LABELS = {
    "C1": "C1 Processing labour",
    "C2": "C2 Paid admin leave",
    "C3": "C3 Backfill overtime",
    "C3-offset": "C3-offset Unpaid suspension saved",
    "C4": "C4 Appeals & grievances",
    "C5": "C5 Removal turnover",
}

st.set_page_config(page_title="WRI - Cost of Discipline", page_icon="::", layout="wide")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def money(value, decimals: int = 0) -> str:
    """Currency with the sign outside the symbol: -$486,772, not $-486,772."""
    try:
        amount = Decimal(str(value))
    except Exception:  # noqa: BLE001
        return "n/a"
    sign = "-" if amount < 0 else ""
    return f"{sign}${abs(amount):,.{decimals}f}"


def md_money(value, decimals: int = 0) -> str:
    """Money for Streamlit markdown. A bare `$` starts a LaTeX span, so escape it."""
    return money(value, decimals).replace("$", "\\$")


def client() -> EngineClient:
    return EngineClient(
        base_url=st.session_state["api_url"],
        role=st.session_state["role"],
        session=st.session_state.get("session_id", "streamlit"),
    )


def how_calculated(title: str, body: str) -> None:
    """Required beside every headline figure. No number appears without one."""
    with st.expander(f"How this number was calculated - {title}"):
        st.markdown(body)


def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.markdown(subtitle)
    st.caption(CAPTION)


def mode() -> str:
    return st.session_state["mode"]


def theme() -> str:
    return st.session_state["chart_theme"]


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------
def sidebar() -> str:
    st.sidebar.markdown("### Harlow County")
    st.sidebar.caption("Workforce Risk Intelligence - Phase 1")

    st.session_state.setdefault("api_url", "http://127.0.0.1:8000")
    st.session_state.setdefault("role", "executive")
    st.session_state.setdefault("mode", "base")
    st.session_state.setdefault("chart_theme", "light")

    page = st.sidebar.radio("Page", PAGES, label_visibility="collapsed")
    st.sidebar.divider()

    st.session_state["role"] = st.sidebar.selectbox(
        "Role",
        ROLES,
        index=ROLES.index(st.session_state["role"]),
        help=(
            "The role is sent to the API as a header and enforced there. "
            "No authentication yet - this is the seam where SSO goes."
        ),
    )
    st.session_state["mode"] = st.sidebar.select_slider(
        "Sensitivity",
        options=["low", "base", "high"],
        value=st.session_state["mode"],
        help="Runs the whole engine on the low, base or high value of every assumption.",
    )
    st.session_state["chart_theme"] = st.sidebar.radio(
        "Chart theme",
        ["light", "dark"],
        horizontal=True,
        index=["light", "dark"].index(st.session_state["chart_theme"]),
    )

    with st.sidebar.expander("Connection"):
        st.session_state["api_url"] = st.text_input("API URL", st.session_state["api_url"])
        if st.button("Reload dataset"):
            try:
                report = client().load_dataset()
                st.success(f"Loaded {report['counts']['actions']:,} actions")
            except ApiError as exc:
                st.error(str(exc))
    return page


# ---------------------------------------------------------------------------
# page 1 - executive summary
# ---------------------------------------------------------------------------
def executive_summary(api: EngineClient) -> None:
    page_header(
        "What discipline costs Harlow County",
        "Every figure here decomposes into line items that name their own formula.",
    )
    data = api.summary(mode())

    takeaway = (
        f"**{data['agency_note'].split('.')[0]}.** Over the last "
        f"{data['window_years']} years Harlow County spent "
        f"**{md_money(data['total_net'])}** on discipline - about "
        f"**{md_money(data['annual_net'])} a year**, or "
        f"**{md_money(data['net_per_fte_per_year'], 0)} per employee per year** across "
        f"{data['active_fte']:,} active employees. "
        f"**{Decimal(data['turnover_share']) * 100:.0f}%** of the gross cost is what it "
        f"takes to replace the people who were removed."
    )
    st.info(takeaway)

    left, mid, right, far = st.columns(4)
    left.metric("Net cost per year", money(data["annual_net"]))
    mid.metric("Per employee per year", money(data["net_per_fte_per_year"], 0))
    right.metric("Share from turnover", f"{Decimal(data['turnover_share']) * 100:.0f}%")
    far.metric("Actions costed", f"{data['actions_costed']:,}")

    how_calculated(
        "net cost per year",
        f"""
Net cost is **gross cost plus the offset**. The offset is negative: it is the salary the
county does not pay during an unpaid suspension.

- Gross: **{md_money(data["total_gross"])}**
- Offset: **{md_money(data["total_offset"])}**
- Net: **{md_money(data["total_net"])}** over **{data["window_years"]} years**
  -> **{md_money(data["annual_net"])} per year**

The window is the span between the earliest and latest decided action in the dataset, not a
count of calendar years. {data["actions_excluded"]} actions were excluded for data quality
and contribute nothing; {data["incomplete_actions"]} are still accruing cost and are counted
at their current actual figure only.
""",
    )
    how_calculated(
        "per employee per year",
        f"""
Net cost / window years / **active** headcount.

{md_money(data["total_net"])} / {data["window_years"]} / {data["active_fte"]:,} =
**{md_money(data["net_per_fte_per_year"], 2)}**

The denominator is everyone on the payroll, not the number of people disciplined. It answers
"what does this cost the organisation per head", not "what does a disciplined employee cost".
""",
    )
    how_calculated(
        "share from turnover",
        """
The C5 total divided by the **gross** total. C5 is everything it takes to replace someone
removed for cause: covering the vacancy, recruiting, screening, the academy, field training,
equipment, and the washout adjustment for recruits who do not finish.

It is measured against gross rather than net so the unpaid-suspension offset - which has
nothing to do with turnover - does not inflate the percentage.
""",
    )

    st.subheader("Where the money goes")
    component_chart(data)
    how_calculated(
        "the component split",
        """
Each bar is the sum of every line item carrying that component tag, across every costed
action. Blue is money spent; red is money saved.

C3-offset is shown as a negative bar on purpose. Unpaid suspensions do save the county the
wage, and burying that inside a net total would hide it. Gross, offset and net are always
reported separately.
""",
    )

    left, right = st.columns([3, 2])
    with left:
        st.subheader("Most expensive cells")
        st.caption("Employee type x misconduct type, after suppression.")
        st.dataframe(
            [
                {
                    "Employee type": c["row_label"],
                    "Misconduct type": c["col_label"],
                    "Actions": c["action_count"],
                    "Net cost": money(c["net"]),
                    "Per action": money(c["cost_per_action"]),
                }
                for c in data["top_cells"]
            ],
            hide_index=True,
            use_container_width=True,
        )
    with right:
        st.subheader("Cost per employee per year")
        department_chart(data)

    st.divider()
    st.download_button(
        "Download the one-page summary (Markdown)",
        api.export_summary(mode()),
        file_name="harlow_county_discipline_cost.md",
        mime="text/markdown",
    )


def component_chart(data: dict) -> None:
    colours = palette(theme())
    totals = {k: Decimal(v) for k, v in data["component_totals"].items()}
    labels = [COMPONENT_LABELS.get(k, k) for k in totals]
    values = [float(v) for v in totals.values()]
    is_cost = [v >= 0 for v in values]

    fig = go.Figure()
    for flag, name, colour in (
        (True, "Cost", colours["cost"]),
        (False, "Saving", colours["saving"]),
    ):
        selected = [
            (label, value)
            for label, value, f in zip(labels, values, is_cost, strict=True)
            if f is flag
        ]
        if not selected:
            continue
        fig.add_bar(
            y=[s[0] for s in selected],
            x=[s[1] for s in selected],
            orientation="h",
            name=name,
            marker=dict(color=colour, line=dict(width=0)),
            text=[money(s[1]) for s in selected],
            textposition="outside",
            textfont=dict(color=colours["text_secondary"]),
            hovertemplate="%{y}<br>%{x:$,.0f}<extra></extra>",
        )
    fig.update_layout(bargap=0.45)
    fig.update_traces(cliponaxis=False)
    low, high = min(values + [0]), max(values + [0])
    span = high - low or 1
    # Room for the direct labels at each end, proportional to the bar that needs it.
    fig.update_xaxes(
        showgrid=True,
        gridcolor=colours["grid"],
        tickformat="$~s",
        range=[low - (span * 0.10 if low < 0 else 0), high + span * 0.14],
    )
    fig.update_yaxes(showgrid=False, autorange="reversed")
    st.plotly_chart(style(fig, theme(), height=340, showlegend=True), use_container_width=True)


def department_chart(data: dict) -> None:
    colours = palette(theme())
    rows = data["department_cost_per_fte_per_year"][:8]
    fig = go.Figure(
        go.Bar(
            y=[r["department"] for r in rows],
            x=[float(r["net_per_fte_per_year"]) for r in rows],
            orientation="h",
            marker=dict(color=colours["cost"], line=dict(width=0)),
            text=[money(r["net_per_fte_per_year"]) for r in rows],
            textposition="outside",
            textfont=dict(color=colours["text_secondary"]),
            customdata=[r["active_fte"] for r in rows],
            hovertemplate="%{y}<br>%{x:$,.0f} per FTE per year<br>"
            "%{customdata:,} active FTE<extra></extra>",
        )
    )
    fig.update_layout(bargap=0.4)
    fig.update_traces(cliponaxis=False)
    high = max([float(r["net_per_fte_per_year"]) for r in rows] + [0])
    fig.update_xaxes(
        showgrid=True,
        gridcolor=colours["grid"],
        tickformat="$,.0f",
        range=[0, high * 1.25],
    )
    fig.update_yaxes(showgrid=False, autorange="reversed")
    st.plotly_chart(style(fig, theme(), height=340), use_container_width=True)


# ---------------------------------------------------------------------------
# page 2 - cost matrix
# ---------------------------------------------------------------------------
def cost_matrix(api: EngineClient) -> None:
    page_header("Cost matrix", "Where the money actually sits.")
    dimensions = api.meta()["dimensions"]
    keys = list(dimensions)

    controls = st.columns([2, 2, 2, 3])
    rows = controls[0].selectbox(
        "Rows",
        keys,
        index=keys.index("role_family"),
        format_func=lambda k: dimensions[k],
    )
    cols = controls[1].selectbox(
        "Columns",
        keys,
        index=keys.index("misconduct_category"),
        format_func=lambda k: dimensions[k],
    )
    metric = controls[2].selectbox(
        "Colour by",
        ["net", "cost_per_action", "cost_per_100_fte", "action_count"],
        format_func=lambda k: {
            "net": "Net cost",
            "cost_per_action": "Cost per action",
            "cost_per_100_fte": "Cost per 100 FTE",
            "action_count": "Number of actions",
        }[k],
    )
    controls[3].caption(
        f"Sensitivity: **{mode()}**. Change it in the sidebar to see the whole matrix move."
    )

    if rows == cols:
        st.warning("Pick two different dimensions.")
        return

    data = api.matrix(rows, cols, mode())
    suppression = data["suppression"]
    left, mid, right = st.columns(3)
    left.metric("Net cost shown", money(data["totals"]["net"]))
    mid.metric(
        "Suppressed cells",
        suppression["suppressed_cells"],
        help=f"Fewer than {suppression['min_cell_size']} distinct employees, or withheld to "
        f"stop a suppressed cell being recovered by subtraction.",
    )
    right.metric(
        "Records still accruing",
        data["totals"]["incomplete_actions"],
        help="Pending appeals, open filing windows, and positions not yet refilled.",
    )

    heatmap(data, metric)
    if data["suppression"]["suppressed_cells"] > len(data["cells"]) * 0.5:
        st.info(
            "Most cells here are suppressed because 20 role families spread the actions "
            "thin. Switch **Rows** to Department for a view with larger groups - the "
            "totals are identical either way."
        )

    st.caption(
        f"Cells marked \u00b7\u00b7\u00b7 are suppressed; blank cells simply have no "
        f"actions. {money(suppression['other_suppressed_net'])} of net cost sits in the "
        f"'Other (suppressed)' bucket, so the totals still reconcile "
        f"({'they do' if suppression['reconciles'] else 'THEY DO NOT'})."
    )
    how_calculated(
        "a matrix cell",
        """
A cell is the sum of every line item for every action whose employee type and misconduct
type land in it. `net = gross + offset`; `cost per action = net / actions`; `cost per 100
FTE = net / active headcount in that row x 100`.

**Suppression happens in the engine, not here.** A cell backed by fewer than the minimum
number of distinct employees is never sent to this page at all - the API response carries no
figure for it. Where hiding one cell would still let you recover it by subtracting from a
row or column total, a second cell is suppressed as well, or the line total itself is
withheld.
""",
    )

    with st.expander("Table view (every visible cell)"):
        st.dataframe(
            [
                {
                    "Row": c["row_label"],
                    "Column": c["col_label"],
                    "Actions": c["action_count"],
                    "Employees": c["employee_count"],
                    "Gross": money(c["gross"]),
                    "Offset": money(c["offset"]),
                    "Net": money(c["net"]),
                    "Per action": money(c["cost_per_action"]),
                    "Still accruing": c["incomplete_count"],
                }
                for c in data["cells"]
                if not c["suppressed"]
            ],
            hide_index=True,
            use_container_width=True,
        )

    st.download_button(
        "Download this matrix (CSV)",
        api.export_csv(rows, cols, mode()),
        file_name=f"wri_matrix_{rows}_x_{cols}_{mode()}.csv",
        mime="text/csv",
    )


def heatmap(data: dict, metric: str) -> None:
    colours = palette(theme())
    row_keys = [r["key"] for r in data["rows"]]
    col_keys = [c["key"] for c in data["cols"]]
    row_labels = [r["label"] for r in data["rows"]]
    col_labels = [c["label"] for c in data["cols"]]

    values: list[list[float | None]] = [[None] * len(col_keys) for _ in row_keys]
    notes: list[list[str]] = [[""] * len(col_keys) for _ in row_keys]
    labels: list[list[str]] = [[""] * len(col_keys) for _ in row_keys]

    by_key = {(c["row"], c["col"]): c for c in data["cells"]}
    for i, row in enumerate(row_keys):
        for j, col in enumerate(col_keys):
            cell = by_key.get((row, col))
            if cell is None:
                notes[i][j] = "no actions"
                continue
            if cell["suppressed"]:
                notes[i][j] = "suppressed (too few employees)"
                labels[i][j] = "···"
                continue
            raw = cell.get(metric)
            if raw is None:
                notes[i][j] = "not available"
                continue
            values[i][j] = float(raw)
            notes[i][j] = (
                f"{cell['action_count']} actions, {cell['employee_count']} employees<br>"
                f"net {money(cell['net'])} - {money(cell['cost_per_action'])} per action"
            )
            if metric == "action_count":
                labels[i][j] = f"{int(float(raw)):,}"
            else:
                labels[i][j] = money(raw)

    # Values are only labelled directly when the grid is small enough to read; the
    # suppression marker is always shown, so a suppressed cell never looks like an empty one.
    small = len(row_keys) * len(col_keys) <= 80
    if not small:
        labels = [
            ["\u00b7\u00b7\u00b7" if text == "\u00b7\u00b7\u00b7" else "" for text in row]
            for row in labels
        ]
    fig = go.Figure(
        go.Heatmap(
            z=values,
            x=col_labels,
            y=row_labels,
            colorscale=colorscale(theme()),
            hoverongaps=False,
            xgap=2,
            ygap=2,  # surface gap between fills
            text=labels,
            texttemplate="%{text}",
            textfont=dict(size=10, color=colours["text_secondary"]),
            customdata=notes,
            hovertemplate="%{y} / %{x}<br>%{customdata}<extra></extra>",
            colorbar=dict(
                thickness=10, outlinewidth=0, tickfont=dict(color=colours["text_secondary"])
            ),
        )
    )
    fig.update_xaxes(showgrid=False, tickangle=-35)
    fig.update_yaxes(showgrid=False, autorange="reversed")
    st.plotly_chart(
        style(fig, theme(), height=max(360, 30 * len(row_keys) + 190)),
        use_container_width=True,
    )


# ---------------------------------------------------------------------------
# page 3 - drill-down
# ---------------------------------------------------------------------------
def drill_down(api: EngineClient) -> None:
    page_header("Drill-down", "One action, every line item, back to its assumption.")
    if st.session_state["role"] == "executive":
        st.warning(
            "The executive role sees aggregates only. The API returns 403 for record-level "
            "requests - this is enforced in the engine, not hidden in the UI. Switch role "
            "in the sidebar to continue."
        )
        return

    dimensions = api.meta()["dimensions"]
    matrix = api.matrix("role_family", "misconduct_category", mode())
    visible = [c for c in matrix["cells"] if not c["suppressed"]]
    visible.sort(key=lambda c: Decimal(c["net"]), reverse=True)
    if not visible:
        st.info("Every cell in this view is suppressed. Widen the filters.")
        return

    choice = st.selectbox(
        "Cell",
        visible,
        format_func=lambda c: (
            f"{c['row_label']} / {c['col_label']} - {money(c['net'])} "
            f"across {c['action_count']} actions"
        ),
    )
    actions = api.actions(
        {"role_family": [choice["row"]], "misconduct_category": [choice["col"]]}, mode()
    )["actions"]

    left, right = st.columns([2, 3])
    with left:
        st.subheader(f"{len(actions)} actions")
        st.dataframe(
            [
                {
                    "Action": a["action_id"],
                    "Employee": a["employee"],
                    "Type": a["action_type_label"],
                    "Decided": a["decision_date"],
                    "Net": money(a["net"]),
                    "Accruing": "yes" if a["cost_incomplete"] else "",
                }
                for a in actions
            ],
            hide_index=True,
            use_container_width=True,
            height=min(460, 40 + 35 * len(actions)),
        )
    with right:
        action_id = st.selectbox("Action", [a["action_id"] for a in actions])
        detail = api.action(action_id, mode())
        st.subheader(f"{action_id} - {money(detail['net'])} net")
        st.caption(
            " / ".join(
                str(detail["dimensions"][k])
                for k in (
                    "role_family_label",
                    "department",
                    "work_location",
                    "misconduct_category_label",
                    "action_type_label",
                )
            )
        )
        if detail["cost_incomplete"]:
            st.warning(
                "This action is still accruing cost - a pending appeal, an open filing "
                "window, or a position not yet refilled. The figure is a floor."
            )
        totals = st.columns(3)
        totals[0].metric("Gross", money(detail["gross"]))
        totals[1].metric("Offset", money(detail["offset"]))
        totals[2].metric("Net", money(detail["net"]))

        st.markdown("#### Line items")
        for item in detail["line_items"]:
            flag = "  *(still accruing)*" if item["cost_incomplete"] else ""
            with st.expander(
                f"{item['component']} - {item['subcomponent'].replace('_', ' ')} - "
                f"{money(item['amount'], 2)}"
            ):
                st.markdown(f"**{item['formula']}**{flag}")
                st.json(item["inputs"], expanded=False)
                if item["assumption_ids"]:
                    st.markdown(
                        "Assumptions used: " + ", ".join(f"`{a}`" for a in item["assumption_ids"])
                    )

        st.markdown("#### Assumptions behind this action")
        st.dataframe(
            [
                {
                    "Assumption": a["id"],
                    "Value": a["effective_value"],
                    "Unit": a["unit"],
                    "Confidence": a["confidence"],
                    "Status": a["status"],
                    "Source": a["source"][:120] + ("..." if len(a["source"]) > 120 else ""),
                }
                for a in detail["assumptions_used"]
            ],
            hide_index=True,
            use_container_width=True,
        )
    _ = dimensions


# ---------------------------------------------------------------------------
# page 4 - assumptions
# ---------------------------------------------------------------------------
def _highlight_placeholders(row) -> list[str]:
    """Rows still marked TBD-MIKE are tinted, so an unconfirmed number is never quiet."""
    tint = "#fff4d6" if theme() == "light" else "#3a3320"
    ink = "#0b0b0b" if theme() == "light" else "#ffffff"
    if row["Status"] == "TBD-MIKE":
        return [f"background-color: {tint}; color: {ink}"] * len(row)
    return [""] * len(row)


def assumptions_page(api: EngineClient) -> None:
    page_header(
        "Assumptions",
        "Every coefficient the engine uses, with its source and how much to trust it.",
    )
    data = api.assumptions(mode())
    left, mid, right = st.columns(3)
    left.metric("Assumptions", data["count"])
    mid.metric(
        "Awaiting owner confirmation",
        data["placeholder_count"],
        help="Marked TBD-MIKE. These must be confirmed before the demo is shown externally.",
    )
    right.metric("Editable here", "yes" if data["editable"] else "no (admin only)")

    show_placeholders = st.checkbox("Show only rows awaiting confirmation", value=False)
    search = st.text_input("Filter by id", "")
    rows = [
        a
        for a in data["assumptions"]
        if (not show_placeholders or a["status"] == "TBD-MIKE")
        and search.lower() in a["id"].lower()
    ]
    table = pd.DataFrame(
        [
            {
                "Status": a["status"],
                "Assumption": a["id"],
                "Low": a["low"],
                "Value": a["effective_value"],
                "High": a["high"],
                "Unit": a["unit"],
                "Confidence": a["confidence"],
                "Source": a["source"],
            }
            for a in rows
        ]
    )
    if table.empty:
        st.info("Nothing matches that filter.")
        return
    st.dataframe(
        table.style.apply(_highlight_placeholders, axis=1),
        hide_index=True,
        use_container_width=True,
        height=420,
    )

    if not data["editable"]:
        st.info(
            "Only the admin role may run scenarios with adjusted values. The API enforces "
            "this; the sliders below appear for admins only."
        )
        return

    st.divider()
    st.subheader("Scenario: change an assumption and rerun")
    st.caption(
        "Nothing on disk changes. The override applies to one run, and the engine recomputes "
        "every line item from scratch."
    )
    tunable = [
        a
        for a in data["assumptions"]
        if a["unit"] not in {"role_family"} and Decimal(a["high"]) > Decimal(a["low"])
    ]
    defaults = [
        "c5_washout_rate_sworn_deputy",
        "c5_academy_tuition_sworn_deputy",
        "c4_outside_counsel_hourly_rate",
    ]
    chosen = st.multiselect(
        "Assumptions to adjust",
        [a["id"] for a in tunable],
        default=[d for d in defaults if any(a["id"] == d for a in tunable)],
    )
    overrides: dict[str, float] = {}
    for assumption_id in chosen:
        record = next(a for a in tunable if a["id"] == assumption_id)
        low, high = float(record["low"]), float(record["high"])
        current = float(record["effective_value"])
        overrides[assumption_id] = st.slider(
            f"{assumption_id} ({record['unit']})",
            min_value=low,
            max_value=high,
            value=current,
            step=max((high - low) / 100, 1e-4),
            help=record["source"],
        )

    if overrides and st.button("Run scenario", type="primary"):
        result = api.run_scenario(overrides, mode())
        left, mid, right = st.columns(3)
        left.metric("Before", money(result["baseline_net"]))
        mid.metric("After", money(result["scenario_net"]))
        right.metric(
            "Difference",
            money(result["delta"]),
            delta=f"{result['delta_pct']}%" if result["delta_pct"] else None,
        )
        st.caption(
            "This is the whole point: the numbers are transparent and tunable to each "
            "customer's own cost structure."
        )


# ---------------------------------------------------------------------------
# page 5 - data quality
# ---------------------------------------------------------------------------
def data_quality(api: EngineClient) -> None:
    page_header(
        "Data quality",
        "What the adapter could not translate, and what that excluded from the totals.",
    )
    report = api.validation()
    cells = st.columns(4)
    cells[0].metric("Issues found", report["total_issues"])
    cells[1].metric("Blocking", report["blocking"])
    cells[2].metric(
        "Actions excluded",
        report["excluded_actions"],
        help="Excluded from every total. The count is always shown - records are never "
        "silently dropped.",
    )
    cells[3].metric(
        "Still accruing cost",
        report["actions_incomplete_cost"],
        help="Costed at their current actual figure and flagged, not estimated forward.",
    )

    st.caption(report["injected_issues_note"])

    st.subheader("By rule")
    st.dataframe(
        [{"Rule": k, "Records": v} for k, v in report["counts_by_rule"].items()],
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Every issue")
    st.dataframe(
        [
            {
                "Severity": i["severity"],
                "Rule": i["rule_id"],
                "Record type": i["entity"],
                "Record": i["entity_id"],
                "Field": i["field"] or "",
                "Blocked actions": ", ".join(i["blocked_actions"]),
                "What is wrong": i["message"],
            }
            for i in report["issues"]
        ],
        hide_index=True,
        use_container_width=True,
        height=420,
    )
    how_calculated(
        "the exclusion count",
        """
A **blocking** issue means the record's cost cannot be trusted, so its action contributes
nothing to any total. A **warning** means the record looks odd but still costs correctly.

An employee-level problem - a missing salary, for instance - blocks every action belonging to
that person, and the root cause is reported once rather than as a cascade of orphan records.
""",
    )


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
PAGE_FUNCTIONS = {
    "Executive summary": executive_summary,
    "Cost matrix": cost_matrix,
    "Drill-down": drill_down,
    "Assumptions": assumptions_page,
    "Data quality": data_quality,
}


def main() -> None:
    page = sidebar()
    api = client()
    try:
        api.health()
    except Exception:  # noqa: BLE001
        st.error(
            f"The engine API is not reachable at {st.session_state['api_url']}.\n\n"
            "Start it with `make api`, then reload this page. The UI talks to the engine "
            "over HTTP only - it has no way to compute anything itself."
        )
        return
    try:
        PAGE_FUNCTIONS[page](api)
    except ApiError as exc:
        if exc.status == 403:
            st.error(
                f"The `{st.session_state['role']}` role is not permitted to see this. {exc.detail}"
            )
        else:
            st.error(str(exc))


if __name__ == "__main__":
    main()
