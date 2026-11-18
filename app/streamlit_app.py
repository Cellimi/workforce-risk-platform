"""WRI Phase 1 demo UI.

Pages over the FastAPI service. This module talks HTTP and nothing else -- it must never
import `wri_engine`. If a page needs something the API does not expose, the answer is a new
endpoint, not a shortcut.

It works against either kind of server, and asks the server which it is:

* **demo** (`make api`): the synthetic county, a role picker, no sign-in;
* **licensed** (`make serve`): sign-in, engagements, loading a client's data and
  client-specific values. Those screens live in `licensed.py`.

Run it with the API already up:

    make api    # or: make serve
    make ui
"""

from __future__ import annotations

import os
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

import licensed  # noqa: E402
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
    "Patterns",
    "Interventions",
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
    "C5-offset": "C5-offset Vacancy salary saved",
    "C6": "C6 Separation & unemployment",
    "C7": "C7 Liability insurance",
    "C7-offset": "C7-offset Paid by insurer",
}

DEFAULT_API_URL = os.environ.get("WRI_API_URL", "http://127.0.0.1:8000")

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
        token=st.session_state.get("token"),
        engagement_id=st.session_state.get("engagement_id") if licensed.is_licensed() else None,
    )


def current_role() -> str:
    return licensed.role() if licensed.is_licensed() else st.session_state["role"]


def agency() -> str:
    current = licensed.engagement() if licensed.is_licensed() else None
    return current["client_name"] if current else "Harlow County"


def how_calculated(title: str, body: str) -> None:
    """Required beside every headline figure. No number appears without one."""
    with st.expander(f"How this number was calculated - {title}"):
        st.markdown(body)


def page_header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.markdown(subtitle)
    st.caption(licensed.ENGAGEMENT_CAPTION if licensed.is_licensed() else CAPTION)


def mode() -> str:
    return st.session_state["mode"]


def theme() -> str:
    return st.session_state["chart_theme"]


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------
def defaults() -> None:
    st.session_state.setdefault("api_url", DEFAULT_API_URL)
    st.session_state.setdefault("role", "executive")
    st.session_state.setdefault("mode", "base")
    st.session_state.setdefault("chart_theme", "light")


def display_controls() -> None:
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


def sidebar() -> str:
    st.sidebar.markdown("### Harlow County")
    st.sidebar.caption("Workforce Risk Intelligence")

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
    display_controls()

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
def report_downloads(api: EngineClient) -> None:
    """The deliverables: a report to read and a workbook to work in.

    Laying out a report takes a moment, so it is prepared when asked for and then offered
    for download, rather than rebuilt every time this page redraws. What was prepared is
    remembered with the engagement and sensitivity mode it was prepared for, and is dropped
    as soon as either changes.
    """
    st.subheader("Report for the client")
    st.caption(
        "A report to read and a workbook to work in, both from the same figures. They show "
        "what your role may see: small groups are withheld exactly as they are on screen."
    )
    patterns = st.checkbox(
        "Include where discipline clusters and where it is rising (the Patterns page)",
        value=True,
        key="report_patterns",
        help="The flagged units and what they cost, and the units rising over the period. "
        "What was assessed and not flagged goes in the working papers only.",
    )
    interventions = st.checkbox(
        "Include what was found on site and what is proposed (kept on the Interventions page)",
        value=True,
        key="report_interventions",
        help="The client's copy names a cause by its label only: what was typed for "
        '"Other" goes in the working papers. Nothing is added if nothing has been kept.',
    )
    key = (st.session_state.get("engagement_id"), mode(), patterns, interventions)
    prepared = st.session_state.get("report_files")
    if prepared and prepared["key"] != key:
        prepared = None
        st.session_state.pop("report_files", None)
    # Offered to the roles that already have the drill-down. The server decides; an
    # executive who asked anyway would be refused there.
    with_records = current_role() != "executive"
    if st.button("Prepare the report"):
        try:
            prepared = {
                "key": key,
                "pdf": api.report_pdf(mode(), patterns=patterns, interventions=interventions),
                "xlsx": api.report_xlsx(mode(), patterns=patterns, interventions=interventions),
            }
            if with_records:
                prepared["records"] = api.report_xlsx(
                    mode(), records=True, patterns=patterns, interventions=interventions
                )
        except ApiError as exc:
            st.error(str(exc))
            return
        st.session_state["report_files"] = prepared
    if prepared:
        name = agency().lower().replace(" ", "_")
        left, right = st.columns(2)
        left.download_button(
            "Download the report (PDF)",
            prepared["pdf"],
            file_name=f"{name}_cost_of_discipline.pdf",
            mime="application/pdf",
        )
        right.download_button(
            "Download the workbook (Excel)",
            prepared["xlsx"],
            file_name=f"{name}_cost_of_discipline.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        if prepared.get("records"):
            st.markdown("**For the engagement team only**")
            st.caption(
                "The working papers are the same workbook with every action and every line "
                "item added. They are not the client's copy. Send the client the two files "
                "above."
                + (
                    ""
                    if current_role() == "admin"
                    else " Employee identifiers in your copy are masked."
                )
            )
            st.download_button(
                "Download the working papers (Excel, record level)",
                prepared["records"],
                file_name=f"{name}_cost_of_discipline_working_papers.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


def executive_summary(api: EngineClient) -> None:
    page_header(
        f"What discipline costs {agency()}",
        "Every figure here decomposes into line items that name their own formula.",
    )
    data = api.summary(mode())

    takeaway = (
        f"**{data['agency_note'].split('.')[0]}.** Over the last "
        f"{data['window_years']} years {agency()} spent "
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

The two offset components are shown as negative bars on purpose. An unpaid suspension
(C3-offset) and a vacant post after a removal (C5-offset) both genuinely stop the county
paying that person's wage, and burying either inside a net total would hide it. Gross, offset
and net are always reported separately.

Only the wage and the burdens that scale with it are treated as saved. Health insurance and
retiree health carry on, so they are not counted as savings in either case.
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
            width="stretch",
        )
    with right:
        st.subheader("Cost per employee per year")
        department_chart(data)

    st.divider()
    report_downloads(api)
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
    st.plotly_chart(style(fig, theme(), height=340, showlegend=True), width="stretch")


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
    st.plotly_chart(style(fig, theme(), height=340), width="stretch")


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
    if data["totals"].get("withheld"):
        st.warning(
            f"Nothing can be shown for this selection: it covers fewer than "
            f"{suppression['min_cell_size']} people, or with figures already shown to you "
            f"it would let a group that small be worked out."
        )
        return
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
            width="stretch",
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
        width="stretch",
    )


# ---------------------------------------------------------------------------
# patterns (Phase 2): units whose discipline rate stands out
# ---------------------------------------------------------------------------
def _unit_name(unit: dict) -> str:
    """A finding's unit as a reader would say it."""
    where = f"{unit['row_label']} at {unit['col_label']}"
    return f"{unit['topic_label']} among {where}" if unit.get("topic_label") else where


def _unit_table(rows: list[dict]) -> pd.DataFrame:
    by_type = any(r.get("topic_label") for r in rows)
    return pd.DataFrame(
        [
            {
                **({"Type of misconduct": r["topic_label"]} if by_type else {}),
                "Employee type": r["row_label"],
                "Work location": r["col_label"],
                "Actions": r["actions"],
                "People": r["people"],
                "Expected": r["expected_actions"],
                "Times the rate elsewhere": r["ratio"],
                "Cost of these actions": money(r["net"]),
                "Adjusted chance value": round(r["q_value"], 4),
            }
            for r in rows
        ]
    )


PATTERN_VIEWS = {
    "By type of misconduct": "misconduct",
    "All types together": "overall",
}


def patterns_page(api: EngineClient) -> None:
    page_header(
        "Patterns",
        "Where a type of misconduct is more frequent than the same job shows elsewhere, "
        "and what it costs there.",
    )
    view = st.radio("Look at", list(PATTERN_VIEWS), horizontal=True, key="patterns_view")
    _hotspots_section(api, PATTERN_VIEWS[view])
    st.divider()
    _drift_section(api, PATTERN_VIEWS[view])


def _hotspots_section(api: EngineClient, by: str) -> None:
    """Higher than the same job elsewhere, over the whole period."""
    found = api.hotspots(by, mode())
    by_type = found["by"] == "misconduct"
    st.info(found["note"])
    settings = found["settings"]
    if settings["unreviewed"]:
        st.warning(
            "The thresholds behind this page are starting values that have not been "
            "reviewed yet: " + ", ".join(f"`{name}`" for name in settings["unreviewed"]) + "."
        )
    unit = (
        "a type of misconduct, in an employee type, at a work location"
        if by_type
        else "an employee type at a work location"
    )
    st.caption(
        f"{found['comparisons']} comparisons made (each is {unit}), over "
        f"{found['window_start']} to {found['window_end']}. A flag needs an adjusted chance "
        f"value of {settings['detection_false_discovery_rate']:g} or less, a rate at least "
        f"{settings['detection_min_rate_ratio']:g} times the comparison, and at least "
        f"{found['min_people']} people disciplined."
    )

    st.subheader("Flagged")
    if not found["flagged"] and not found["withheld"]:
        st.markdown(
            "No type of misconduct stands out in any unit." if by_type else "No unit stands out."
        )
    for unit in found["flagged"]:
        with st.container(border=True):
            st.markdown(f"**{_unit_name(unit)}**")
            one, two, three, four = st.columns(4)
            withheld = unit.get("comparison_withheld")
            one.metric(
                "Times the rate elsewhere",
                "withheld" if withheld else (f"{unit['ratio']:.2f}" if unit["ratio"] else "-"),
            )
            two.metric(
                "Actions, against expected",
                f"{unit['actions']}"
                if withheld
                else f"{unit['actions']} / {unit['expected_actions']:g}",
            )
            three.metric(
                "Cost of these actions",
                money(unit["net"]),
                help="From the cost baseline: what the records show these actions cost.",
            )
            four.metric(
                "Estimated excess cost",
                money(unit["estimated_excess_cost"]),
                help="An estimate, and not part of the cost baseline.",
            )
            with st.expander("How this was worked out"):
                st.markdown(unit["explanation"])
                if unit["formula"]:
                    st.markdown(f"Estimated excess cost: {unit['formula']}.")
    if found["withheld"]:
        st.caption(
            f"{found['withheld']} more flagged, and withheld from this view: together with "
            "figures already shown to you, it would let a small group of people be worked "
            "out. The engagement lead can see it."
        )

    if found["detail"] != "full":
        st.caption(
            "This view shows the flagged units. What was assessed and not flagged is kept to "
            "the roles that see records, because that list would give away small groups. "
            "That is decided on the server, not on this screen."
        )
        return

    if found["concentrated"]:
        st.subheader("More actions than expected, among few people")
        st.caption(
            "These stand out on the number of actions and not on the number of people. That "
            "is not a pattern in a unit, and they are not flagged."
        )
        st.dataframe(_unit_table(found["concentrated"]), hide_index=True, width="stretch")

    with st.expander(f"Assessed and not flagged ({len(found['not_flagged'])})"):
        if found["not_flagged"]:
            st.dataframe(_unit_table(found["not_flagged"]), hide_index=True, width="stretch")
    if found["not_assessed"]:
        with st.expander(f"Not assessed ({len(found['not_assessed'])})"):
            for unit in found["not_assessed"]:
                st.markdown(f"- **{unit['row_label']} at {unit['col_label']}**: {unit['reason']}")


def _change(value) -> str:
    return "-" if value is None else f"{value:.2f}"


def _drift_table(rows: list[dict]) -> pd.DataFrame:
    by_type = any(r.get("topic_label") for r in rows)
    return pd.DataFrame(
        [
            {
                **({"Type of misconduct": r["topic_label"]} if by_type else {}),
                "Employee type": r["row_label"],
                "Work location": r["col_label"],
                "Actions, earlier": r["earlier_actions"],
                "Actions, later": r["later_actions"],
                "Times its earlier rate": r["own_change"],
                "Times the rise elsewhere": r["ratio"],
                "Cost, later half": money(r["later_net"]),
                "Adjusted chance value": round(r["q_value"], 4),
            }
            for r in rows
        ]
    )


def _drift_section(api: EngineClient, by: str) -> None:
    """Rising: the later half of the period against the earlier half."""
    found = api.drift(by, mode())
    st.subheader("Rising over the period")
    st.info(found["note"])
    if not found["earlier_start"]:
        st.markdown("There are no decided actions to compare.")
        return
    st.caption(
        f"Earlier half: {found['earlier_start']} to {found['earlier_end']}. Later half: "
        f"{found['later_start']} to {found['later_end']}. {found['comparisons']} "
        f"comparisons made: every unit with at least {found['min_people']} actions in the "
        f"period. The thresholds are the same two as above, applied to the unit's own rise "
        f"and to its rise against the rest of the organization."
    )
    if not found["flagged"] and not found["withheld"]:
        st.markdown("Nothing is rising by more than the rest of the organization.")
    for unit in found["flagged"]:
        with st.container(border=True):
            st.markdown(f"**{_unit_name(unit)}**")
            one, two, three, four = st.columns(4)
            one.metric("Times its earlier rate", _change(unit["own_change"]))
            two.metric(
                "Actions, earlier then later",
                f"{unit['earlier_actions']} then {unit['later_actions']}",
            )
            three.metric(
                "Cost, earlier then later",
                f"{money(unit['earlier_net'])} then {money(unit['later_net'])}",
                help="From the cost baseline: what the records show the actions cost in each half.",
            )
            four.metric(
                "Estimated excess cost of the rise",
                "withheld"
                if unit.get("comparison_withheld")
                else money(unit["estimated_excess_cost"]),
                help="An estimate, and not part of the cost baseline.",
            )
            with st.expander("How this was worked out"):
                st.markdown(unit["explanation"])
                if unit["formula"]:
                    st.markdown(f"Estimated excess cost: {unit['formula']}.")
    if found["withheld"]:
        st.caption(
            f"{found['withheld']} more flagged as rising, and withheld from this view: it "
            "would state a figure about a small group of people, or let one be worked out "
            "from figures already shown to you. The engagement lead can see it."
        )
    if found["detail"] != "full":
        return

    if found["concentrated"]:
        st.markdown("**Rising, among few people** (not flagged)")
        st.dataframe(_drift_table(found["concentrated"]), hide_index=True, width="stretch")
    with st.expander(f"Rising: assessed and not flagged ({len(found['not_flagged'])})"):
        if found["not_flagged"]:
            st.dataframe(_drift_table(found["not_flagged"]), hide_index=True, width="stretch")
    if found["not_assessed"]:
        with st.expander(f"Rising: not assessed ({len(found['not_assessed'])})"):
            for unit in found["not_assessed"]:
                st.markdown(f"- **{unit['row_label']} at {unit['col_label']}**: {unit['reason']}")


# ---------------------------------------------------------------------------
# interventions (Phase 3): what doing something would cost, against what it costs now
# ---------------------------------------------------------------------------
def _unit_choices(api: EngineClient, by: str) -> list[tuple[str, dict]]:
    """Every unit the records show, flagged ones first, as (label, unit)."""
    found = api.hotspots(by, mode())
    rising = {(u["row"], u["col"], u["topic"]) for u in api.drift(by, mode())["flagged"]}
    choices, seen = [], set()
    for status in ("flagged", "concentrated", "not_flagged"):
        for u in found[status]:
            key = (u["row"], u["col"], u["topic"])
            if key in seen:
                continue
            seen.add(key)
            marks = [
                m for m, on in (("flagged", status == "flagged"), ("rising", key in rising)) if on
            ]
            label = _unit_name(u) + (f"  ({', '.join(marks)})" if marks else "")
            unit = {
                "role_family": u["row"],
                "work_location": u["col"],
                "misconduct_category": u["topic"],
            }
            choices.append((label, unit, bool(marks)))
    choices.sort(key=lambda c: (not c[2], c[0]))
    return [(label, unit) for label, unit, _ in choices]


def interventions_page(api: EngineClient) -> None:
    page_header(
        "Interventions",
        "What doing something about a pattern would cost, against what the discipline costs.",
    )
    if current_role() == "executive":
        st.warning(
            "This page works on single units, whatever their size, so it is for the roles "
            "that see records. The API returns 403."
            + ("" if licensed.is_licensed() else " Switch role in the sidebar to continue.")
        )
        return
    st.info(
        "Someone has to go and find out what is happening in a unit before anything is "
        "proposed for it. This page does not do that and does not suggest a cause. It "
        "takes what you propose and your own estimate of how well it will work, and sets "
        "the cost against what the cost baseline says the discipline costs."
    )
    view = st.radio("Look at", list(PATTERN_VIEWS), horizontal=True, key="interventions_view")
    choices = _unit_choices(api, PATTERN_VIEWS[view])
    if not choices:
        st.markdown("The records show no discipline actions to work with.")
        return
    labels = [label for label, _ in choices]
    picked = st.selectbox("Where", labels, key="interventions_unit")
    unit = dict(choices)[picked]
    allowed = api.intervention_causes()
    cause_ids = {c["label"]: c["id"] for c in allowed["causes"]}
    library = api.intervention_library()
    _library_starter(library["entries"])
    type_ids = {t["label"]: t["id"] for t in library["misconduct_types"]}

    with st.form("intervention"):
        name = st.text_input("What is proposed", key="iv_name")
        one, two, three = st.columns(3)
        one_time = one.number_input("One-time cost ($)", min_value=0.0, step=1000.0, key="iv_once")
        per_person = two.number_input(
            "Yearly cost per person covered ($)", min_value=0.0, step=10.0, key="iv_each"
        )
        people = three.number_input("People covered", min_value=0, step=1, key="iv_people")
        once_each = st.number_input(
            "One-time cost per person covered ($)", min_value=0.0, step=10.0, key="iv_once_each"
        )
        four, five = st.columns(2)
        other = four.number_input(
            "Other yearly cost ($)", min_value=0.0, step=1000.0, key="iv_other"
        )
        years = five.number_input(
            "Years to look ahead", min_value=1, max_value=10, value=3, key="iv_years"
        )
        st.markdown(
            "**How much of this discipline would it prevent?** Your judgement, three ways. "
            "The product has no evidence of its own for this: a library entry's figures are "
            "placeholders."
        )
        aims = st.multiselect(
            "Aimed at these types of misconduct (none chosen: every type)",
            list(type_ids),
            key="iv_aims",
            help="Only the unit's actions of these types count toward what it could prevent.",
        )
        low, expected, high = st.columns(3)
        r_low = low.number_input("Low (%)", min_value=0.0, max_value=100.0, key="iv_low")
        r_exp = expected.number_input("Expected (%)", min_value=0.0, max_value=100.0, key="iv_exp")
        r_high = high.number_input("High (%)", min_value=0.0, max_value=100.0, key="iv_high")
        basis = st.text_area(
            "What those three figures are based on",
            key="iv_basis",
            help="For example: what the site visit found, a pilot elsewhere, a published "
            "study. It is shown with the result.",
        )
        st.markdown(
            "**What the investigation found.** Needed only to keep this with the "
            "engagement. Choose what you found on site; the product suggests nothing."
        )
        found = st.multiselect("Causes found", list(cause_ids), key="iv_causes")
        other_cause = st.text_input(
            "If Other, what, in a line",
            key="iv_other_cause",
            max_chars=allowed["other_text_max_length"],
            help="Describe the condition, never a person. Do not name anyone.",
        )
        left, right = st.columns(2)
        go = left.form_submit_button("Work it out")
        keep = right.form_submit_button("Work it out and keep")
    if not (go or keep):
        _kept_section(api)
        return

    def share(percent: float) -> str:
        return str(Decimal(str(percent)) / Decimal(100))

    proposal = {
        "name": name,
        "one_time_cost": str(one_time),
        "one_time_cost_per_person": str(once_each),
        "library_id": st.session_state.get("iv_library_id", ""),
        "annual_cost_per_person": str(per_person),
        "people_covered": int(people),
        "other_annual_cost": str(other),
        "years": int(years),
        "reduction_low": share(r_low),
        "reduction_expected": share(r_exp),
        "reduction_high": share(r_high),
        "basis": basis,
        "targets": [type_ids[label] for label in aims],
    }
    try:
        if keep:
            kept = api.keep_intervention(
                unit, proposal, [cause_ids[label] for label in found], other_cause, mode()
            )
            result = kept["appraisal"]
            result["note"] = kept["note"]
            st.success(
                f"Kept with this engagement, as the finding of {kept['finding']['entered_by']}."
            )
        else:
            result = api.appraise(unit, proposal, mode())
    except ApiError as exc:
        problems = exc.detail.get("problems") if isinstance(exc.detail, dict) else None
        if not problems:
            st.error(str(exc))
            return
        st.error("This cannot be kept yet:" if keep else "This cannot be worked out yet:")
        for problem in problems:
            st.markdown(f"- {problem[0].upper()}{problem[1:]}.")
        return
    _show_appraisal(result)
    _kept_section(api)


def _kept_section(api: EngineClient) -> None:
    """The appraisals kept with this engagement, each with whose finding it is."""
    book = api.kept_interventions(mode())
    st.divider()
    st.subheader("Kept with this engagement")
    if not book["kept"]:
        st.markdown("Nothing has been kept yet.")
    for kept in book["kept"]:
        finding, appraisal = kept["finding"], kept["appraisal"]
        proposal = appraisal["intervention"] if appraisal else kept["intervention"]
        with st.container(border=True):
            where = appraisal["unit"]["name"] if appraisal else kept["work_location"]
            st.markdown(f"**{proposal['name']}: {where}**")
            causes = ", ".join(c["label"] for c in finding["causes"])
            st.markdown(f"Found on site: {causes}.")
            if kept.get("aimed_at_label"):
                st.markdown(f"Aimed at: {kept['aimed_at_label']}.")
            if finding["other_cause"]:
                # Words a person typed about a client's workplace: shown as plain text.
                st.text(f"Other: {finding['other_cause']}")
            st.caption(
                f"The finding of {finding['entered_by']}, entered "
                f"{finding['entered_at'][:10]}. Not found or checked by this product."
            )
            placeholder = (appraisal or kept).get("placeholder_note")
            if placeholder:
                st.warning(placeholder)
            if appraisal:
                expected = appraisal["scenarios"][1]
                st.markdown(
                    f"Costs {money(proposal['total_cost'])} over {proposal['years']} years, "
                    f"against {money(appraisal['unit']['annual_net'])} a year of discipline. "
                    f"Net saving as expected: {money(expected['net_saving'])} "
                    f"(a projection). {appraisal['break_even_note']}"
                )
            else:
                st.markdown(
                    "The records now show no discipline action for this unit, so there is "
                    "nothing to set the intervention against. The finding stands."
                )
            if st.button("Withdraw", key=f"withdraw_{kept['id']}"):
                api.withdraw_intervention(kept["id"])
                st.rerun()
    found_for = {
        kept["id"]: ", ".join(c["label"] for c in kept["finding"]["causes"])
        for kept in book["kept"]
    }
    for compared in book["side_by_side"]:
        st.markdown(f"**Side by side: {compared['unit']['name']}**")
        st.caption(
            f"The discipline costs {money(compared['unit']['annual_net'])} a year. Each "
            "proposal is put per year, so that those looking different distances ahead can "
            "be read together."
        )
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Proposed": row["name"],
                        "For": found_for.get(row["key"], ""),
                        "Years": row["years"],
                        "Costs, a year": money(row["yearly_cost"]),
                        "Pays for itself at": _break_even(row),
                        "Net saving a year: low": money(row["yearly_net_saving_low"]),
                        "Net saving a year: expected": money(row["yearly_net_saving_expected"]),
                        "Net saving a year: high": money(row["yearly_net_saving_high"]),
                    }
                    for row in compared["rows"]
                ]
            ),
            hide_index=True,
            width="stretch",
        )
        for note in compared["notes"]:
            st.caption(note)
    if book["withdrawn"]:
        st.caption(
            f"{book['withdrawn']} withdrawn. A withdrawn appraisal is no longer shown; the "
            "record of it stays with the engagement."
        )
    st.caption(book["note"])


def _break_even(row: dict) -> str:
    """The share of actions a proposal must prevent to pay for itself, in words where a
    percentage would mislead."""
    if row["break_even"] is None:
        return "nothing to save"
    if not row["can_pay_for_itself"]:
        return "cannot"
    return f"{(Decimal(row['break_even']) * 100).normalize():f}% prevented"


def _fill_from_library(entry: dict) -> None:
    """Put a library entry's starting figures into the form. Runs before the page redraws,
    which is the only moment a form's fields may be set."""
    st.session_state["iv_library_id"] = entry["id"]
    st.session_state["iv_name"] = entry["label"]
    st.session_state["iv_once_each"] = float(entry["one_time_cost_per_person"])
    st.session_state["iv_each"] = float(entry["annual_cost_per_person"])
    st.session_state["iv_low"] = float(Decimal(entry["reduction_low"]) * 100)
    st.session_state["iv_exp"] = float(Decimal(entry["reduction_expected"]) * 100)
    st.session_state["iv_high"] = float(Decimal(entry["reduction_high"]) * 100)
    st.session_state["iv_aims"] = list(entry.get("aims_at_labels", []))


def _library_starter(library: list[dict]) -> None:
    """Offer the library's starting figures. A convenience, and never a suggestion of what
    is wrong in the unit or what to do about it."""
    with st.expander("Start from the library (optional)"):
        st.caption(
            "Starting figures for common interventions. Choosing one fills in the form "
            "below; change any figure to the client's own. It is not a suggestion of what is "
            "causing anything."
        )
        by_label = {entry["label"]: entry for entry in library}
        picked = st.selectbox("Library entry", list(by_label), key="iv_library_pick")
        entry = by_label[picked]
        st.markdown(
            f"{entry['description']} One-time, per person: "
            f"{money(entry['one_time_cost_per_person'])}. Yearly, per person: "
            f"{money(entry['annual_cost_per_person'])}. Prevents: "
            f"{Decimal(entry['reduction_low']) * 100:g}%, "
            f"{Decimal(entry['reduction_expected']) * 100:g}% or "
            f"{Decimal(entry['reduction_high']) * 100:g}% (low, expected, high)."
        )
        if entry["aims_at"]:
            st.caption(
                f"Aimed at attendance and tardiness actions only: the evidence for its share "
                f"prevented measured {entry['evidence']}, not discipline."
            )
        if entry["note"]:
            st.warning(
                entry["note"] + " An appraisal that keeps any of these says so wherever it is "
                "shown, the client's report included."
            )
        with st.expander("Sources"):
            for source in entry["sources"]:
                st.markdown(f"- {source}")
        st.button("Fill in its starting figures", on_click=_fill_from_library, args=(entry,))


def _show_appraisal(result: dict) -> None:
    unit, proposal = result["unit"], result["intervention"]
    expected = result["scenarios"][1]
    st.subheader(f"{proposal['name']}: {unit['name']}")
    if result.get("placeholder_note"):
        st.warning(result["placeholder_note"])
    aimed = result.get("aimed_at")
    if aimed:
        st.caption(
            f"Aimed at {aimed['label']}: of the {money(unit['annual_net'])} a year this unit's "
            f"discipline costs, {money(aimed['annual_net'])} is for those actions, and only "
            f"they count toward what it could prevent."
        )
    one, two, three = st.columns(3)
    one.metric(
        "The discipline it is aimed at costs, a year" if aimed else "The discipline costs, a year",
        money(aimed["annual_net"] if aimed else unit["annual_net"]),
        help="From the cost baseline: " + unit["formula"] + ".",
    )
    two.metric(
        f"The intervention costs, over {proposal['years']} years",
        money(proposal["total_cost"]),
        help=proposal["cost_formula"] + ".",
    )
    three.metric(
        "Net saving, as expected",
        money(expected["net_saving"]),
        help="A projection, and not part of the cost baseline. " + expected["formula"] + ".",
    )
    st.markdown(f"**{result['break_even_note']}**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "If it works": s["label"],
                    "Actions prevented": f"{(Decimal(s['reduction']) * 100).normalize():f}%",
                    "Actions prevented, a year": s["actions_prevented_per_year"],
                    "Cost prevented, a year": money(s["prevented_per_year"]),
                    f"Cost prevented, {proposal['years']} years": money(s["prevented_total"]),
                    "Net saving": money(s["net_saving"]),
                }
                for s in result["scenarios"]
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    st.caption(f"Based on: {proposal['basis']}")
    with st.expander("How this was worked out, and what it assumes"):
        st.markdown(f"- The discipline costs, a year: {unit['formula']}.")
        st.markdown(f"- The intervention costs: {proposal['cost_formula']}.")
        for s in result["scenarios"]:
            st.markdown(f"- {s['label']}: {s['formula']}.")
        st.markdown("**It assumes:**")
        for line in result["assumes"]:
            st.markdown(f"- {line}")
    st.caption(result["note"])


# ---------------------------------------------------------------------------
# page 3 - drill-down
# ---------------------------------------------------------------------------
def drill_down(api: EngineClient) -> None:
    page_header("Drill-down", "One action, every line item, back to its assumption.")
    if current_role() == "executive":
        st.warning(
            "The executive role sees aggregates only. The API returns 403 for record-level "
            "requests - this is enforced in the engine, not hidden in the UI."
            + ("" if licensed.is_licensed() else " Switch role in the sidebar to continue.")
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
            width="stretch",
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
            width="stretch",
        )
    _ = dimensions


# ---------------------------------------------------------------------------
# page 4 - assumptions
# ---------------------------------------------------------------------------
def _highlight_uncertain(row) -> list[str]:
    """Tint the rows a reader should not lean on.

    Two different things are worth flagging, and conflating them would be dishonest:

    * `TBD-MIKE` -- nobody has signed off on this value yet. Amber.
    * `confidence: low` -- the owner stands behind it, but it is still an estimate rather
      than a measured or cited figure. Also amber, because for a reader deciding how much
      weight to put on a number, "not signed off" and "signed off but a guess" deserve the
      same caution.

    The owner approved this registry on 2026-11-12, so no row is `TBD-MIKE` today. The
    branch stays because the next customer's registry starts out full of them.
    """
    tint = "#fff4d6" if theme() == "light" else "#3a3320"
    ink = "#0b0b0b" if theme() == "light" else "#ffffff"
    if row["Status"] == "TBD-MIKE" or row["Confidence"] == "low":
        return [f"background-color: {tint}; color: {ink}"] * len(row)
    return [""] * len(row)


def assumptions_page(api: EngineClient) -> None:
    page_header(
        "Assumptions",
        "Every coefficient the engine uses, with its source and how much to trust it.",
    )
    data = api.assumptions(mode())
    all_rows = data["assumptions"]
    low_confidence = sum(1 for a in all_rows if a["confidence"] == "low")
    sourced = sum(1 for a in all_rows if a["status"] == "researched")

    left, mid, right, far = st.columns(4)
    left.metric("Assumptions", data["count"])
    mid.metric(
        "Awaiting sign-off",
        data["placeholder_count"],
        help=(
            "Marked TBD-MIKE. The owner signed off on the whole registry on 2026-11-12, so "
            "none are pending. A new customer's registry starts out full of them."
        ),
    )
    right.metric(
        "Backed by a cited source",
        f"{sourced} of {data['count']}",
        help=(
            "Sign-off is not verification. These carry a citation; the rest are the owner's "
            "professional estimates, approved as the working figures."
        ),
    )
    far.metric("Editable here", "yes" if data["editable"] else "no (admin only)")

    st.caption(
        f"Every value is owner-approved, but **{low_confidence} of {data['count']} are still "
        f"low confidence** and are tinted below. Approving a number is not the same as "
        f"measuring it - the Source column says which is which."
    )

    only_uncertain = st.checkbox(
        "Show only the rows to treat with caution (low confidence, or not signed off)",
        value=False,
    )
    search = st.text_input("Filter by id", "")
    rows = [
        a
        for a in all_rows
        if (not only_uncertain or a["confidence"] == "low" or a["status"] == "TBD-MIKE")
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
                **(
                    {"Client value": "yes" if a.get("overridden") else ""}
                    if licensed.is_licensed()
                    else {}
                ),
            }
            for a in rows
        ]
    )
    if table.empty:
        st.info("Nothing matches that filter.")
        return
    st.dataframe(
        table.style.apply(_highlight_uncertain, axis=1),
        hide_index=True,
        width="stretch",
        height=420,
    )

    if not data["editable"]:
        st.info(
            "Only the admin role may run scenarios with adjusted values. The API enforces "
            "this; the sliders below appear for admins only."
        )
        return

    if licensed.is_licensed():
        licensed.overrides_section(api, data["assumptions"])

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

    if report.get("injected_issues_note"):
        st.caption(report["injected_issues_note"])

    st.subheader("By rule")
    st.dataframe(
        [{"Rule": k, "Records": v} for k, v in report["counts_by_rule"].items()],
        hide_index=True,
        width="stretch",
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
        width="stretch",
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
    "Patterns": patterns_page,
    "Interventions": interventions_page,
    "Assumptions": assumptions_page,
    "Data quality": data_quality,
    "Load data": licensed.load_data_page,
    "Engagement": licensed.engagement_page,
}


def unreachable() -> None:
    st.error(
        f"The engine API is not reachable at {st.session_state['api_url']}.\n\n"
        "Start it with `make api` (demo) or `make serve` (licensed), then reload this page. "
        "The UI talks to the engine over HTTP only - it has no way to compute anything itself."
    )
    with st.expander("Connection"):
        st.session_state["api_url"] = st.text_input("API URL", st.session_state["api_url"])


def main() -> None:
    defaults()
    try:
        health = EngineClient(base_url=st.session_state["api_url"]).health()
    except Exception:  # noqa: BLE001
        unreachable()
        return
    st.session_state["auth_mode"] = health.get("auth_mode", "demo")

    if licensed.is_licensed():
        if not licensed.signed_in():
            licensed.sign_in_page(st.session_state["api_url"])
            return
        page = licensed.sidebar(st.session_state["api_url"])
        display_controls()
    else:
        page = sidebar()

    api = client()
    try:
        PAGE_FUNCTIONS[page](api)
    except ApiError as exc:
        if exc.status == 401 and licensed.is_licensed():
            licensed.sign_out()
            st.warning("Your session has ended. Sign in again.")
            st.rerun()
        elif exc.status == 409 and licensed.is_licensed():
            st.info(
                "No data has been loaded for this engagement yet."
                + (
                    " Open **Load data** in the sidebar to bring in the client's export."
                    if licensed.can("manage_data")
                    else " The engagement lead loads it."
                )
            )
        elif exc.status == 402:
            # A license refusal. It is nobody's role, so it is not reported as one.
            st.warning(str(exc.detail))
        elif exc.status == 403:
            st.error(f"The `{current_role()}` role is not permitted to see this. {exc.detail}")
        else:
            st.error(str(exc))


if __name__ == "__main__":
    main()
