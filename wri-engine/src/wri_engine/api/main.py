"""FastAPI service -- the engine's interface.

This is the only way into the engine. The Streamlit app talks to these endpoints over HTTP
and never imports `wri_engine`, which is what makes the UI replaceable without touching the
cost logic.

Access control
--------------
The caller's role arrives in the `X-WRI-Role` header. There is no authentication behind it
yet: the demo lets the UI choose. Enforcement is real -- an `executive` asking for a record
drill-down gets 403 from this layer, not a hidden button. `X-WRI-Session` scopes the
disclosure ledger that blocks differencing attacks; it defaults to the role.

Every request is written to the append-only audit log before its response is built.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Body, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from wri_engine.access.audit import AuditLog
from wri_engine.access.roles import (
    CAPABILITIES,
    AccessDenied,
    Capability,
    Role,
    parse_role,
    project_identifier,
    require,
)
from wri_engine.aggregation.rollups import (
    DIMENSIONS,
    build_matrix,
    fte_denominators,
    summarize,
)
from wri_engine.api.state import STATE
from wri_engine.costing.assumptions import MissingAssumptionError

CAPTION = (
    "Synthetic data for a fictional county. Cost figures reflect documented assumptions, "
    "not measured agency costs."
)

app = FastAPI(
    title="WRI Engine - Discipline Cost Baseline",
    version="0.1.0",
    description=(
        "Phase 1 of the Workforce Risk Intelligence engine: the fully loaded cost of "
        "discipline for a fictional county. Every figure is traceable to the line items "
        "and assumptions that produced it.\n\n" + CAPTION
    ),
)
AUDIT = AuditLog()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _role(raw: str | None) -> Role:
    return parse_role(raw)


def _session(role: Role, raw: str | None) -> str:
    return f"{role}:{raw}" if raw else str(role)


def _filters(raw: str | None) -> dict[str, list[str]]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(400, f"filters must be a JSON object: {exc}") from None
    if not isinstance(parsed, dict):
        raise HTTPException(400, "filters must be a JSON object of dimension -> values")
    out: dict[str, list[str]] = {}
    for key, value in parsed.items():
        if key not in DIMENSIONS:
            raise HTTPException(
                400, f"{key!r} is not a filter dimension. Available: {sorted(DIMENSIONS)}"
            )
        out[key] = [str(v) for v in (value if isinstance(value, list) else [value])]
    return out


def _guard(role: Role, capability: Capability, endpoint: str, filters: dict | None = None) -> None:
    try:
        require(role, capability)
    except AccessDenied as exc:
        AUDIT.record(
            role=str(role),
            endpoint=endpoint,
            outcome="denied",
            filters=filters or {},
            detail={"capability": str(capability)},
        )
        raise HTTPException(403, str(exc)) from None


RoleHeader = Annotated[str | None, Header(alias="X-WRI-Role")]
SessionHeader = Annotated[str | None, Header(alias="X-WRI-Session")]


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}


@app.get("/meta", tags=["meta"])
def meta(x_wri_role: RoleHeader = None) -> dict:
    role = _role(x_wri_role)
    STATE.ensure_loaded()
    AUDIT.record(role=str(role), endpoint="/meta")
    return {
        "agency": STATE.agency_name,
        "synthetic": True,
        "caption": CAPTION,
        "as_of": STATE.as_of.isoformat() if STATE.as_of else None,
        "source_id": STATE.source_id,
        "min_cell_size": STATE.min_cell_size,
        "role": str(role),
        "capabilities": sorted(str(c) for c in CAPABILITIES[role]),
        "dimensions": DIMENSIONS,
        "manifest": STATE.manifest,
    }


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------
class LoadRequest(BaseModel):
    path: str | None = Field(None, description="Directory holding the source export")
    source: str = Field("county_hr_csv", description="Adapter id")


@app.post("/datasets/load", tags=["datasets"])
def load_dataset(
    body: LoadRequest = Body(default=LoadRequest()), x_wri_role: RoleHeader = None
) -> dict:
    """Run the adapter and its validation rules. Returns the full validation report."""
    role = _role(x_wri_role)
    AUDIT.record(role=str(role), endpoint="/datasets/load", detail=body.model_dump())
    report = STATE.load(body.path, body.source)
    return {
        "source_id": STATE.source_id,
        "path": str(STATE.source_path),
        "as_of": STATE.as_of.isoformat(),
        "counts": {
            "employees": len(STATE.dataset.employees),
            "actions": len(STATE.dataset.actions),
            "admin_leave_periods": len(STATE.dataset.leave_periods),
            "appeals": len(STATE.dataset.appeals),
            "separations": len(STATE.dataset.separations),
        },
        "validation": report.as_dict(),
    }


@app.get("/datasets/validation", tags=["datasets"])
def validation(x_wri_role: RoleHeader = None) -> dict:
    """The data quality panel: what failed validation and how many actions it excluded."""
    role = _role(x_wri_role)
    STATE.ensure_loaded()
    AUDIT.record(role=str(role), endpoint="/datasets/validation")
    run = STATE.run()
    report = STATE.report.as_dict()
    report["actions_costed"] = len(run.action_costs)
    report["actions_incomplete_cost"] = run.incomplete_count
    report["injected_issues_note"] = (
        "The synthetic generator deliberately seeds malformed rows so these rules have "
        "something to catch. See src/wri_engine/generator/README.md."
    )
    return report


# ---------------------------------------------------------------------------
# costs
# ---------------------------------------------------------------------------
@app.get("/costs/matrix", tags=["costs"])
def cost_matrix(
    rows: str = Query("role_family"),
    cols: str = Query("misconduct_category"),
    filters: str | None = Query(None, description='JSON object, e.g. {"year": ["2025"]}'),
    mode: str = Query("base", pattern="^(low|base|high)$"),
    x_wri_role: RoleHeader = None,
    x_wri_session: SessionHeader = None,
) -> dict:
    """The cost matrix, with suppression already applied. Suppressed cells carry no money."""
    role = _role(x_wri_role)
    parsed = _filters(filters)
    _guard(role, Capability.VIEW_AGGREGATES, "/costs/matrix", parsed)
    AUDIT.record(
        role=str(role),
        endpoint="/costs/matrix",
        filters=parsed,
        detail={"rows": rows, "cols": cols, "mode": mode},
    )
    STATE.ensure_loaded()
    run = STATE.run(mode=mode)
    try:
        matrix = build_matrix(
            run,
            rows=rows,
            cols=cols,
            filters=parsed,
            min_cell_size=STATE.min_cell_size,
            ledger=STATE.ledger(_session(role, x_wri_session)),
            fte_by_row=fte_denominators(STATE.dataset, rows),
        )
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"caption": CAPTION, **matrix.as_dict()}


@app.get("/costs/summary", tags=["costs"])
def cost_summary(
    mode: str = Query("base", pattern="^(low|base|high)$"),
    x_wri_role: RoleHeader = None,
) -> dict:
    """Headline figures for the executive summary page."""
    role = _role(x_wri_role)
    _guard(role, Capability.VIEW_AGGREGATES, "/costs/summary")
    AUDIT.record(role=str(role), endpoint="/costs/summary", detail={"mode": mode})
    STATE.ensure_loaded()
    return {
        "caption": CAPTION,
        **summarize(STATE.run(mode=mode), STATE.dataset, min_cell_size=STATE.min_cell_size),
    }


@app.get("/costs/actions", tags=["costs"])
def list_actions(
    filters: str | None = Query(None),
    mode: str = Query("base", pattern="^(low|base|high)$"),
    limit: int = Query(200, ge=1, le=2000),
    x_wri_role: RoleHeader = None,
) -> dict:
    """Actions matching a filter, for the drill-down list. Role-gated."""
    role = _role(x_wri_role)
    parsed = _filters(filters)
    _guard(role, Capability.VIEW_RECORDS, "/costs/actions", parsed)
    AUDIT.record(role=str(role), endpoint="/costs/actions", filters=parsed)
    STATE.ensure_loaded()
    from wri_engine.aggregation.rollups import matches

    run = STATE.run(mode=mode)
    selected = [ac for ac in run.action_costs if matches(ac, parsed)]
    selected.sort(key=lambda ac: ac.net, reverse=True)
    return {
        "caption": CAPTION,
        "count": len(selected),
        "actions": [
            {
                "action_id": ac.action_id,
                "employee": project_identifier(role, ac.employee_id),
                "net": str(ac.net),
                "gross": str(ac.gross),
                "offset": str(ac.offset),
                "cost_incomplete": ac.cost_incomplete,
                **{k: v for k, v in ac.dimensions.items() if k not in {"employee_id"}},
            }
            for ac in selected[:limit]
        ],
    }


@app.get("/costs/actions/{action_id}", tags=["costs"])
def action_detail(
    action_id: str,
    mode: str = Query("base", pattern="^(low|base|high)$"),
    x_wri_role: RoleHeader = None,
) -> dict:
    """Every line item behind one action, with its formula and the assumptions it used."""
    role = _role(x_wri_role)
    _guard(role, Capability.VIEW_RECORDS, f"/costs/actions/{action_id}")
    AUDIT.record(
        role=str(role),
        endpoint="/costs/actions/{action_id}",
        detail={"action_id": action_id, "mode": mode},
    )
    STATE.ensure_loaded()
    action = STATE.run(mode=mode).by_action().get(action_id)
    if action is None:
        raise HTTPException(404, f"action {action_id!r} was not costed (unknown or excluded)")
    assumptions = STATE.assumptions.with_mode(mode)
    used = sorted({a for item in action.line_items for a in item.assumption_ids})
    return {
        "caption": CAPTION,
        "action_id": action.action_id,
        "employee": project_identifier(role, action.employee_id),
        "dimensions": {k: v for k, v in action.dimensions.items() if k != "employee_id"},
        "gross": str(action.gross),
        "offset": str(action.offset),
        "net": str(action.net),
        "cost_incomplete": action.cost_incomplete,
        "line_items": [
            {
                **item.model_dump(mode="json"),
                "employee_id": project_identifier(role, item.employee_id),
                "component_label": str(item.component),
            }
            for item in action.line_items
        ],
        "assumptions_used": [
            {
                "id": aid,
                "effective_value": str(assumptions.value(aid)),
                "unit": assumptions.get(aid).unit,
                "source": assumptions.get(aid).source,
                "confidence": assumptions.get(aid).confidence,
                "status": assumptions.get(aid).status,
            }
            for aid in used
        ],
    }


# ---------------------------------------------------------------------------
# assumptions and scenarios
# ---------------------------------------------------------------------------
@app.get("/assumptions", tags=["assumptions"])
def list_assumptions(
    mode: str = Query("base", pattern="^(low|base|high)$"),
    x_wri_role: RoleHeader = None,
) -> dict:
    role = _role(x_wri_role)
    _guard(role, Capability.VIEW_AGGREGATES, "/assumptions")
    AUDIT.record(role=str(role), endpoint="/assumptions", detail={"mode": mode})
    assumptions = STATE.assumptions.with_mode(mode)
    records = assumptions.as_records()
    return {
        "mode": mode,
        "count": len(records),
        "placeholder_count": sum(1 for r in records if r["status"] == "TBD-MIKE"),
        "editable": role == Role.ADMIN,
        "assumptions": records,
    }


class ScenarioRequest(BaseModel):
    overrides: dict[str, float] = Field(
        default_factory=dict, description="assumption id -> value, for this run only"
    )
    mode: str = Field("base", pattern="^(low|base|high)$")
    rows: str = "role_family"
    cols: str = "misconduct_category"


@app.post("/scenarios/run", tags=["assumptions"])
def run_scenario(body: ScenarioRequest, x_wri_role: RoleHeader = None) -> dict:
    """Re-run the engine with overridden assumptions. Nothing on disk changes."""
    role = _role(x_wri_role)
    _guard(role, Capability.RUN_SCENARIOS, "/scenarios/run")
    AUDIT.record(role=str(role), endpoint="/scenarios/run", detail=body.model_dump())
    STATE.ensure_loaded()
    try:
        scenario = STATE.run(mode=body.mode, overrides=body.overrides)
    except MissingAssumptionError as exc:
        raise HTTPException(400, str(exc)) from None
    baseline = STATE.run(mode=body.mode)
    delta = scenario.net - baseline.net
    return {
        "caption": CAPTION,
        "mode": body.mode,
        "overrides": body.overrides,
        "baseline_net": str(baseline.net),
        "scenario_net": str(scenario.net),
        "delta": str(delta),
        "delta_pct": (
            str((delta / baseline.net * Decimal(100)).quantize(Decimal("0.01")))
            if baseline.net
            else None
        ),
        "summary": summarize(scenario, STATE.dataset, min_cell_size=STATE.min_cell_size),
    }


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------
@app.get("/exports/matrix.csv", tags=["exports"])
def export_matrix(
    rows: str = Query("role_family"),
    cols: str = Query("misconduct_category"),
    filters: str | None = Query(None),
    mode: str = Query("base", pattern="^(low|base|high)$"),
    x_wri_role: RoleHeader = None,
) -> Response:
    role = _role(x_wri_role)
    parsed = _filters(filters)
    _guard(role, Capability.VIEW_AGGREGATES, "/exports/matrix.csv", parsed)
    AUDIT.record(role=str(role), endpoint="/exports/matrix.csv", filters=parsed)
    STATE.ensure_loaded()
    matrix = build_matrix(
        STATE.run(mode=mode),
        rows=rows,
        cols=cols,
        filters=parsed,
        min_cell_size=STATE.min_cell_size,
        fte_by_row=fte_denominators(STATE.dataset, rows),
    )
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"# {CAPTION}"])
    writer.writerow([f"# mode={mode} min_cell_size={matrix.min_cell_size}"])
    writer.writerow(
        [
            DIMENSIONS[rows],
            DIMENSIONS[cols],
            "actions",
            "employees",
            "gross",
            "offset",
            "net",
            "cost_per_action",
            "cost_per_100_fte",
            "incomplete_records",
            "suppressed",
        ]
    )
    for cell in matrix.cells.values():
        if cell.suppressed:
            writer.writerow(
                [
                    cell.row_label,
                    cell.col_label,
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    "",
                    cell.suppression_reason,
                ]
            )
        else:
            writer.writerow(
                [
                    cell.row_label,
                    cell.col_label,
                    cell.action_count,
                    cell.employee_count,
                    cell.gross,
                    cell.offset,
                    cell.net,
                    cell.cost_per_action.quantize(Decimal("0.01")),
                    cell.cost_per_100_fte.quantize(Decimal("0.01"))
                    if cell.cost_per_100_fte is not None
                    else "",
                    cell.incomplete_count,
                    "",
                ]
            )
    writer.writerow(
        [
            "Other (suppressed)",
            "",
            "",
            "",
            matrix.suppressed_gross,
            "",
            matrix.suppressed_net,
            "",
            "",
            "",
            "",
        ]
    )
    writer.writerow(
        [
            "TOTAL",
            "",
            matrix.total_actions,
            "",
            matrix.total_gross,
            matrix.total_offset,
            matrix.total_net,
            "",
            "",
            matrix.total_incomplete,
            "",
        ]
    )
    return Response(
        buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="wri_cost_matrix.csv"'},
    )


@app.get("/exports/summary.md", tags=["exports"])
def export_summary(
    mode: str = Query("base", pattern="^(low|base|high)$"),
    x_wri_role: RoleHeader = None,
) -> Response:
    role = _role(x_wri_role)
    _guard(role, Capability.VIEW_AGGREGATES, "/exports/summary.md")
    AUDIT.record(role=str(role), endpoint="/exports/summary.md")
    STATE.ensure_loaded()
    s = summarize(STATE.run(mode=mode), STATE.dataset, min_cell_size=STATE.min_cell_size)
    return Response(_summary_markdown(s), media_type="text/markdown")


def _summary_markdown(s: dict[str, Any]) -> str:
    def money(text: str | None) -> str:
        return f"${Decimal(text):,.0f}" if text else "n/a"

    lines = [
        f"# {STATE.agency_name} - Cost of Discipline",
        "",
        f"*{CAPTION}*",
        "",
        f"Window: {s['window_years']} years of decided actions, as of {s['as_of']}. "
        f"Sensitivity mode: **{s['mode']}**.",
        "",
        "## Headline",
        "",
        f"- **{money(s['annual_net'])} per year**, net of unpaid-suspension savings",
        f"- **{money(s['net_per_fte_per_year'])} per employee per year** across "
        f"{s['active_fte']:,} active employees",
        f"- **{Decimal(s['turnover_share']) * 100:.0f}% of gross cost is turnover after "
        f"removals** (C5)",
        f"- {s['actions_costed']:,} actions costed; {s['actions_excluded']} excluded for "
        f"data quality; {s['incomplete_actions']} still accruing cost",
        "",
        "## Where the money goes",
        "",
        "| Component | Total |",
        "|---|---:|",
    ]
    labels = {
        "C1": "C1 Processing labor",
        "C2": "C2 Paid administrative leave",
        "C3": "C3 Backfill overtime",
        "C3-offset": "C3-offset Unpaid suspension savings",
        "C4": "C4 Appeals and grievances",
        "C5": "C5 Removal turnover",
        "C5-offset": "C5-offset Vacancy salary savings",
    }
    for key, label in labels.items():
        lines.append(f"| {label} | {money(s['component_totals'].get(key, '0'))} |")
    lines += [
        f"| **Net** | **{money(s['total_net'])}** |",
        "",
        "## Most expensive cells",
        "",
        "| Employee type | Misconduct type | Actions | Net | Per action |",
        "|---|---|---:|---:|---:|",
    ]
    for cell in s["top_cells"]:
        lines.append(
            f"| {cell['row_label']} | {cell['col_label']} | {cell['action_count']} | "
            f"{money(cell['net'])} | {money(cell['cost_per_action'])} |"
        )
    lines += [
        "",
        "## Cost per employee per year, by department",
        "",
        "| Department | Active FTE | Net per FTE per year |",
        "|---|---:|---:|",
    ]
    for dept in s["department_cost_per_fte_per_year"]:
        lines.append(
            f"| {dept['department']} | {dept['active_fte']:,} | "
            f"{money(dept['net_per_fte_per_year'])} |"
        )
    lines += [
        "",
        "---",
        "",
        "Every figure above decomposes into line items that name their formula, their inputs "
        "and the assumptions they used. Assumptions still marked `TBD-MIKE` are owner "
        "placeholders and must be confirmed before this is shown externally.",
        "",
    ]
    return "\n".join(lines)
