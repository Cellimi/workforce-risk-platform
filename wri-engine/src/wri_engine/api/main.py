"""FastAPI service -- the engine's interface.

This is the only way into the engine. The Streamlit app talks to these endpoints over HTTP
and never imports `wri_engine`, which is what makes the UI replaceable without touching the
cost logic.

Access control
--------------
Every data endpoint receives a `Ctx` (see `wri_engine.api.context`) that says who is calling,
in which engagement, and with what role. In a licensed installation that comes from the
caller's sign-in and their membership of the engagement in the URL
(`/engagements/{engagement_id}/...`). Enforcement happens here -- an `executive` asking for a
record drill-down gets 403 from this layer, not a hidden button.

In demo mode (`WRI_AUTH_MODE=demo`) the same endpoints are also served without the
engagement prefix, the role comes from the `X-WRI-Role` header, and the only data is the
synthetic county.

Every request is written to the append-only audit log before its response is built.
"""

from __future__ import annotations

import json
import re
import shutil
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    Body,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Query,
    Response,
    UploadFile,
)
from pydantic import BaseModel, Field

from wri_engine.access.audit import AuditLog
from wri_engine.access.roles import (
    CAPABILITIES,
    AccessDenied,
    Capability,
    allows,
    project_identifier,
    require,
)
from wri_engine.adapters.template import (
    SOURCE_ID as TEMPLATE_SOURCE,
)
from wri_engine.adapters.template import (
    MappingError,
    parse_mapping,
    suggest_mapping,
    template_spec,
    template_zip,
)
from wri_engine.aggregation.rollups import (
    DIMENSIONS,
    build_matrix,
    fte_denominators,
    summarize,
)
from wri_engine.api.context import Ctx, Runtime, get_ctx
from wri_engine.api.state import ADAPTERS, NoDataset, Workspaces, make_adapter, write_marker
from wri_engine.costing.assumptions import MissingAssumptionError
from wri_engine.paths import SYNTHETIC_DIR
from wri_engine.settings import Settings
from wri_engine.store import Store

CAPTION = (
    "Synthetic data for a fictional county. Cost figures reflect documented assumptions, "
    "not measured agency costs."
)

router = APIRouter()
ENGAGEMENT_CAPTION = (
    "Cost figures reflect the data supplied for this engagement and its documented "
    "assumptions. Review the assumptions and overrides before relying on a figure."
)
AUDIT = AuditLog()


def _caption(ctx: Ctx) -> str:
    return ENGAGEMENT_CAPTION if ctx.engagement else CAPTION


def _summary(ctx: Ctx, run) -> dict:
    """The headline figures, labelled for what they are: synthetic in the demo, the client's
    own data in an engagement."""
    out = summarize(run, ctx.state.dataset, min_cell_size=ctx.state.min_cell_size)
    out["agency_note"] = _caption(ctx)
    return out


def _agency(ctx: Ctx) -> str:
    return ctx.engagement.client_name if ctx.engagement else ctx.state.agency_name


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
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


def _audit(ctx: Ctx, endpoint: str, **fields) -> None:
    AUDIT.record(
        role=str(ctx.role),
        user=ctx.user_label,
        engagement=ctx.engagement_id,
        endpoint=endpoint,
        **fields,
    )


def _guard(ctx: Ctx, capability: Capability, endpoint: str, filters: dict | None = None) -> None:
    try:
        require(ctx.role, capability)
    except AccessDenied as exc:
        _audit(
            ctx,
            endpoint,
            outcome="denied",
            filters=filters or {},
            detail={"capability": str(capability)},
        )
        raise HTTPException(403, str(exc)) from None


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@router.get("/meta", tags=["meta"])
def meta(ctx: Ctx = Depends(get_ctx)) -> dict:
    role, state = ctx.role, ctx.state
    state.ensure_loaded()
    _audit(ctx, "/meta")
    return {
        "agency": _agency(ctx),
        "synthetic": ctx.engagement is None,
        "engagement": (
            {
                "id": ctx.engagement.id,
                "name": ctx.engagement.name,
                "sector": ctx.engagement.sector,
                "status": ctx.engagement.status,
            }
            if ctx.engagement
            else None
        ),
        "caption": _caption(ctx),
        "as_of": state.as_of.isoformat() if state.as_of else None,
        "source_id": state.source_id,
        "min_cell_size": state.min_cell_size,
        "role": str(role),
        "capabilities": sorted(str(c) for c in CAPABILITIES[role]),
        "dimensions": DIMENSIONS,
        "manifest": state.manifest,
    }


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------
class LoadRequest(BaseModel):
    path: str | None = Field(
        None, description="Demo mode only: a folder inside the synthetic data directory"
    )
    source: str | None = Field(
        None, description="Adapter id. Blank reloads the files the way they were last loaded."
    )


#: Upload names are restricted to plain file names with these extensions.
_UPLOAD_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}\.(csv|json)$")


def _demo_path(raw: str | None) -> Path | None:
    """Demo mode may point the loader at a folder, but only inside the synthetic data
    directory. Anything else on the server's disk is off limits."""
    if raw is None:
        return None
    path = Path(raw).expanduser().resolve()
    root = SYNTHETIC_DIR.resolve()
    if path != root and root not in path.parents:
        raise HTTPException(400, "path must be inside the synthetic data directory")
    return path


def _load_response(ctx: Ctx, report) -> dict:
    state = ctx.state
    return {
        "source_id": state.source_id,
        "path": str(state.source_path),
        "as_of": state.as_of.isoformat(),
        "counts": {
            "employees": len(state.dataset.employees),
            "actions": len(state.dataset.actions),
            "admin_leave_periods": len(state.dataset.leave_periods),
            "appeals": len(state.dataset.appeals),
            "separations": len(state.dataset.separations),
        },
        "validation": report.as_dict(),
    }


@router.post("/datasets/load", tags=["datasets"])
def load_dataset(
    body: LoadRequest = Body(default=LoadRequest()), ctx: Ctx = Depends(get_ctx)
) -> dict:
    """Run the adapter and its validation rules on the files already in this workspace.
    Returns the full validation report."""
    state = ctx.state
    _guard(ctx, Capability.MANAGE_DATA, "/datasets/load")
    _audit(ctx, "/datasets/load", detail=body.model_dump())
    if body.source is not None and body.source not in ADAPTERS:
        raise HTTPException(400, f"unknown source {body.source!r}. Available: {sorted(ADAPTERS)}")
    if ctx.engagement is not None:
        if body.path is not None:
            raise HTTPException(
                400, "an engagement loads its own uploaded files; `path` is not accepted"
            )
        path = None
    else:
        path = _demo_path(body.path)
    try:
        report = state.load(path, body.source)
    except FileNotFoundError as exc:
        raise HTTPException(409, f"source files are missing or incomplete: {exc}") from None
    return _load_response(ctx, report)


async def _read_uploads(ctx: Ctx, files: list[UploadFile]) -> dict[str, bytes]:
    """Read uploaded files into memory after checking their names and sizes."""
    names = [f.filename or "" for f in files]
    bad = [n for n in names if not _UPLOAD_NAME.match(n)]
    if bad or len(set(names)) != len(names):
        raise HTTPException(
            400,
            "file names must be unique plain names ending in .csv or .json; rejected: "
            + ", ".join(repr(n) for n in bad or names),
        )
    payloads: dict[str, bytes] = {}
    for upload in files:
        data = await upload.read(ctx.max_upload_bytes + 1)
        if len(data) > ctx.max_upload_bytes:
            raise HTTPException(413, f"{upload.filename} is larger than the upload limit")
        payloads[upload.filename] = data
    return payloads


def _replace_files(directory: Path, payloads: dict[str, bytes]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for old in directory.iterdir():
        if old.is_file():
            old.unlink()
    for name, data in payloads.items():
        (directory / name).write_bytes(data)


def _dry_run(ctx: Ctx, directory: Path, source: str, mapping: dict | None):
    """Load a folder without touching the engagement's current dataset. Returns the adapter
    and what it read."""
    adapter = make_adapter(source, mapping=mapping, client_name=ctx.state.client_name)
    try:
        return adapter, adapter.load(directory)
    except MappingError:
        raise
    except Exception as exc:  # whatever the adapter could not read
        raise HTTPException(
            422, f"the files could not be read; nothing was changed: {exc}"
        ) from None


def _require_usable(adapter, staged) -> None:
    """The adapters report problems rather than raising, so a load with no usable employee
    or discipline records is refused here, with its reasons."""
    if not staged.employees or not staged.actions:
        raise HTTPException(
            422,
            {
                "message": (
                    "there are no usable employee or discipline records; nothing was changed"
                ),
                "counts_by_rule": adapter.report.as_dict()["counts_by_rule"],
                "first_issues": [i.as_dict() for i in adapter.report.issues[:10]],
            },
        )


def _commit(ctx: Ctx, staging: Path, source: str, mapping: dict | None) -> dict:
    """Make the staged files the engagement's dataset, recording how they are read."""
    target = ctx.state.default_source
    for old in target.iterdir():
        if old.is_file():
            old.unlink()
    for new in staging.iterdir():
        if new.is_file():
            shutil.copyfile(new, target / new.name)
    write_marker(target, source, mapping)
    return _load_response(ctx, ctx.state.load())


def _engagement_only(ctx: Ctx, what: str) -> None:
    if ctx.engagement is None:
        raise HTTPException(400, f"{what} belongs to an engagement; demo mode has none")


@router.post("/datasets/upload", tags=["datasets"])
async def upload_dataset(
    files: list[UploadFile] = File(..., description="The source export files"),
    source: str = Query(
        "county_hr_csv",
        description="`wri_template` for files already in the WRI intake template layout",
    ),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    """Replace this engagement's dataset in one step, for files already in a layout the
    engine knows. A client export in its own layout goes through `/intake/...` instead."""
    _guard(ctx, Capability.MANAGE_DATA, "/datasets/upload")
    _engagement_only(ctx, "an upload")
    if source not in ADAPTERS:
        raise HTTPException(400, f"unknown source {source!r}. Available: {sorted(ADAPTERS)}")
    payloads = await _read_uploads(ctx, files)
    _audit(
        ctx,
        "/datasets/upload",
        detail={"files": {n: len(d) for n, d in payloads.items()}, "source": source},
    )
    # Stage the new files and prove they can be read before the engagement's current files
    # are touched, so a bad upload never destroys a good dataset.
    staging = ctx.state.default_source.parent / "incoming"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        _replace_files(staging, payloads)
        adapter, staged = _dry_run(ctx, staging, source, None)
        _require_usable(adapter, staged)
        return _commit(ctx, staging, source, None)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# intake: a client export in its own layout
# ---------------------------------------------------------------------------
@router.get("/intake/template", tags=["intake"])
def intake_template(ctx: Ctx = Depends(get_ctx)) -> dict:
    """The standard intake template: every table and field, what it means, what is required
    and which values are allowed."""
    _guard(ctx, Capability.VIEW_AGGREGATES, "/intake/template")
    return template_spec()


@router.get("/intake/template.zip", tags=["intake"])
def intake_template_zip(ctx: Ctx = Depends(get_ctx)) -> Response:
    """Blank template files and a plain-language field guide, to send to a client."""
    _guard(ctx, Capability.VIEW_AGGREGATES, "/intake/template.zip")
    _audit(ctx, "/intake/template.zip")
    return Response(
        template_zip(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="wri_intake_template.zip"'},
    )


def _draft_mapping(ctx: Ctx) -> dict | None:
    path = ctx.workspaces.draft_mapping_file(ctx.engagement.id)
    return json.loads(path.read_text()) if path.exists() else None


def _intake_view(ctx: Ctx) -> dict:
    directory = ctx.workspaces.intake_dir(ctx.engagement.id)
    if not directory.exists() or not any(directory.glob("*.csv")):
        return {"files": {}, "draft_mapping": _draft_mapping(ctx), "suggestion": None}
    suggestion = suggest_mapping(directory)
    return {
        "files": suggestion.pop("files"),
        "draft_mapping": _draft_mapping(ctx),
        "suggestion": suggestion,
    }


@router.get("/intake", tags=["intake"])
def intake_status(ctx: Ctx = Depends(get_ctx)) -> dict:
    """What has been uploaded for mapping, the saved draft mapping, and a suggested one."""
    _guard(ctx, Capability.MANAGE_DATA, "/intake")
    _engagement_only(ctx, "intake")
    _audit(ctx, "/intake")
    return _intake_view(ctx)


@router.post("/intake/files", tags=["intake"])
async def intake_files(
    files: list[UploadFile] = File(..., description="The client's export, as received"),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    """Upload a client export in whatever layout it arrived in. Nothing is costed and the
    engagement's current dataset is untouched. The reply lists the columns found and a
    suggested mapping onto the template to review."""
    _guard(ctx, Capability.MANAGE_DATA, "/intake/files")
    _engagement_only(ctx, "intake")
    payloads = await _read_uploads(ctx, files)
    if not any(name.lower().endswith(".csv") for name in payloads):
        raise HTTPException(400, "upload at least one .csv file")
    _audit(ctx, "/intake/files", detail={"files": {n: len(d) for n, d in payloads.items()}})
    _replace_files(ctx.workspaces.intake_dir(ctx.engagement.id), payloads)
    ctx.workspaces.draft_mapping_file(ctx.engagement.id).unlink(missing_ok=True)
    return _intake_view(ctx)


class MappingRequest(BaseModel):
    mapping: dict = Field(..., description="See `suggested_mapping` from /intake/files")


def _preview(ctx: Ctx, mapping: dict) -> tuple[dict, bool]:
    directory = ctx.workspaces.intake_dir(ctx.engagement.id)
    if not directory.exists() or not any(directory.glob("*.csv")):
        raise HTTPException(409, "upload the client's files to /intake/files first")
    try:
        adapter, staged = _dry_run(ctx, directory, TEMPLATE_SOURCE, mapping)
    except MappingError as exc:
        raise HTTPException(400, f"the mapping is not valid: {exc}") from None
    report = adapter.report.as_dict()
    usable = bool(staged.employees and staged.actions)
    return (
        {
            "ready_to_commit": usable,
            "counts": {
                "employees": len(staged.employees),
                "actions": len(staged.actions),
                "admin_leave_periods": len(staged.leave_periods),
                "appeals": len(staged.appeals),
                "separations": len(staged.separations),
            },
            "actions_that_would_be_excluded": report["excluded_actions"],
            "validation": {**report, "issues": report["issues"][:200]},
        },
        usable,
    )


@router.put("/intake/mapping", tags=["intake"])
def intake_mapping(body: MappingRequest, ctx: Ctx = Depends(get_ctx)) -> dict:
    """Save a draft mapping and see what it would load. Nothing is committed: repeat until
    the preview is right."""
    _guard(ctx, Capability.MANAGE_DATA, "/intake/mapping")
    _engagement_only(ctx, "intake")
    preview, _ = _preview(ctx, body.mapping)
    normalized = parse_mapping(body.mapping).as_dict()
    ctx.workspaces.draft_mapping_file(ctx.engagement.id).write_text(
        json.dumps(normalized, indent=2, sort_keys=True)
    )
    _audit(ctx, "/intake/mapping", detail={"ready_to_commit": preview["ready_to_commit"]})
    return {"mapping": normalized, **preview}


@router.post("/intake/commit", tags=["intake"])
def intake_commit(ctx: Ctx = Depends(get_ctx)) -> dict:
    """Make the uploaded files, read through the saved mapping, this engagement's dataset."""
    _guard(ctx, Capability.MANAGE_DATA, "/intake/commit")
    _engagement_only(ctx, "intake")
    mapping = _draft_mapping(ctx)
    if mapping is None:
        raise HTTPException(409, "save a mapping with PUT /intake/mapping first")
    directory = ctx.workspaces.intake_dir(ctx.engagement.id)
    adapter, staged = _dry_run(ctx, directory, TEMPLATE_SOURCE, mapping)
    _require_usable(adapter, staged)
    _audit(ctx, "/intake/commit", detail={"files": sorted(p.name for p in directory.iterdir())})
    return _commit(ctx, directory, TEMPLATE_SOURCE, mapping)


@router.get("/datasets/validation", tags=["datasets"])
def validation(ctx: Ctx = Depends(get_ctx)) -> dict:
    """The data quality panel: what failed validation and how many actions it excluded."""
    state = ctx.state
    state.ensure_loaded()
    _audit(ctx, "/datasets/validation")
    run = state.run()
    report = state.report.as_dict()
    report["actions_costed"] = len(run.action_costs)
    report["actions_incomplete_cost"] = run.incomplete_count
    if ctx.engagement is None:
        report["injected_issues_note"] = (
            "The synthetic generator deliberately seeds malformed rows so these rules have "
            "something to catch. See src/wri_engine/generator/README.md."
        )
    return report


# ---------------------------------------------------------------------------
# costs
# ---------------------------------------------------------------------------
@router.get("/costs/matrix", tags=["costs"])
def cost_matrix(
    rows: str = Query("role_family"),
    cols: str = Query("misconduct_category"),
    filters: str | None = Query(None, description='JSON object, e.g. {"year": ["2025"]}'),
    mode: str = Query("base", pattern="^(low|base|high)$"),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    """The cost matrix, with suppression already applied. Suppressed cells carry no money."""
    state = ctx.state
    parsed = _filters(filters)
    _guard(ctx, Capability.VIEW_AGGREGATES, "/costs/matrix", parsed)
    _audit(
        ctx,
        "/costs/matrix",
        filters=parsed,
        detail={"rows": rows, "cols": cols, "mode": mode},
    )
    state.ensure_loaded()
    run = state.run(mode=mode)
    try:
        matrix = build_matrix(
            run,
            rows=rows,
            cols=cols,
            filters=parsed,
            min_cell_size=state.min_cell_size,
            ledger=ctx.ledger(),
            fte_by_row=fte_denominators(state.dataset, rows),
        )
    except KeyError as exc:
        raise HTTPException(400, str(exc)) from None
    return {"caption": _caption(ctx), **matrix.as_dict()}


@router.get("/costs/summary", tags=["costs"])
def cost_summary(
    mode: str = Query("base", pattern="^(low|base|high)$"),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    """Headline figures for the executive summary page."""
    state = ctx.state
    _guard(ctx, Capability.VIEW_AGGREGATES, "/costs/summary")
    _audit(ctx, "/costs/summary", detail={"mode": mode})
    state.ensure_loaded()
    return {
        "caption": _caption(ctx),
        **_summary(ctx, state.run(mode=mode)),
    }


@router.get("/costs/actions", tags=["costs"])
def list_actions(
    filters: str | None = Query(None),
    mode: str = Query("base", pattern="^(low|base|high)$"),
    limit: int = Query(200, ge=1, le=2000),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    """Actions matching a filter, for the drill-down list. Role-gated."""
    role, state = ctx.role, ctx.state
    parsed = _filters(filters)
    _guard(ctx, Capability.VIEW_RECORDS, "/costs/actions", parsed)
    _audit(ctx, "/costs/actions", filters=parsed)
    state.ensure_loaded()
    from wri_engine.aggregation.rollups import matches

    run = state.run(mode=mode)
    selected = [ac for ac in run.action_costs if matches(ac, parsed)]
    selected.sort(key=lambda ac: ac.net, reverse=True)
    return {
        "caption": _caption(ctx),
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


@router.get("/costs/actions/{action_id}", tags=["costs"])
def action_detail(
    action_id: str,
    mode: str = Query("base", pattern="^(low|base|high)$"),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    """Every line item behind one action, with its formula and the assumptions it used."""
    role, state = ctx.role, ctx.state
    _guard(ctx, Capability.VIEW_RECORDS, f"/costs/actions/{action_id}")
    _audit(
        ctx,
        "/costs/actions/{action_id}",
        detail={"action_id": action_id, "mode": mode},
    )
    state.ensure_loaded()
    action = state.run(mode=mode).by_action().get(action_id)
    if action is None:
        raise HTTPException(404, f"action {action_id!r} was not costed (unknown or excluded)")
    assumptions = state.effective_assumptions.with_mode(mode)
    used = sorted({a for item in action.line_items for a in item.assumption_ids})
    return {
        "caption": _caption(ctx),
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
@router.get("/assumptions", tags=["assumptions"])
def list_assumptions(
    mode: str = Query("base", pattern="^(low|base|high)$"),
    ctx: Ctx = Depends(get_ctx),
) -> dict:
    role, state = ctx.role, ctx.state
    _guard(ctx, Capability.VIEW_AGGREGATES, "/assumptions")
    _audit(ctx, "/assumptions", detail={"mode": mode})
    assumptions = state.effective_assumptions.with_mode(mode)
    records = assumptions.as_records()
    return {
        "mode": mode,
        "count": len(records),
        "placeholder_count": sum(1 for r in records if r["status"] == "TBD-MIKE"),
        "override_count": sum(1 for r in records if r["overridden"]),
        "editable": allows(role, Capability.EDIT_ASSUMPTIONS),
        "assumptions": records,
    }


class OverrideRequest(BaseModel):
    value: Decimal
    reason: str = Field(
        ..., min_length=3, max_length=500, description="Where the figure comes from"
    )


@router.get("/assumptions/overrides", tags=["assumptions"])
def list_overrides(ctx: Ctx = Depends(get_ctx)) -> dict:
    """The client-specific values this engagement uses in place of the defaults."""
    _guard(ctx, Capability.VIEW_AGGREGATES, "/assumptions/overrides")
    _audit(ctx, "/assumptions/overrides")
    store = _engagement_store(ctx)
    defaults = ctx.state.assumptions
    return {
        "overrides": [
            {
                "assumption_id": o.assumption_id,
                "value": str(o.value),
                "default_value": str(defaults.get(o.assumption_id).value),
                "unit": defaults.get(o.assumption_id).unit,
                "reason": o.reason,
                "set_by": o.set_by,
                "set_at": o.set_at.isoformat(),
            }
            for o in store.overrides(ctx.engagement.id)
        ]
    }


@router.put("/assumptions/{assumption_id}/override", tags=["assumptions"])
def set_override(assumption_id: str, body: OverrideRequest, ctx: Ctx = Depends(get_ctx)) -> dict:
    """Use the client's own figure for one assumption in this engagement. The default in the
    registry is untouched, and the change is recorded with who made it and why."""
    _guard(ctx, Capability.EDIT_ASSUMPTIONS, "/assumptions/{assumption_id}/override")
    store = _engagement_store(ctx)
    if assumption_id not in ctx.state.assumptions:
        raise HTTPException(404, f"assumption {assumption_id!r} is not in the registry")
    if not body.value.is_finite() or body.value < 0:
        raise HTTPException(400, "an assumption value must be a non-negative number")
    _audit(
        ctx,
        "/assumptions/{assumption_id}/override",
        detail={"assumption_id": assumption_id, "value": str(body.value), "reason": body.reason},
    )
    store.set_override(
        ctx.engagement.id, assumption_id, body.value, reason=body.reason, set_by=ctx.user.id
    )
    ctx.workspaces.refresh_overrides(ctx.engagement.id)
    return {"assumption_id": assumption_id, "value": str(body.value), "overridden": True}


@router.delete("/assumptions/{assumption_id}/override", tags=["assumptions"])
def clear_override(assumption_id: str, ctx: Ctx = Depends(get_ctx)) -> dict:
    """Go back to the default value for one assumption."""
    _guard(ctx, Capability.EDIT_ASSUMPTIONS, "/assumptions/{assumption_id}/override")
    store = _engagement_store(ctx)
    _audit(
        ctx,
        "/assumptions/{assumption_id}/override",
        detail={"assumption_id": assumption_id, "cleared": True},
    )
    if not store.clear_override(ctx.engagement.id, assumption_id):
        raise HTTPException(404, f"no override is set for {assumption_id!r}")
    ctx.workspaces.refresh_overrides(ctx.engagement.id)
    return {"assumption_id": assumption_id, "overridden": False}


def _engagement_store(ctx: Ctx):
    if ctx.engagement is None or ctx.store is None:
        raise HTTPException(400, "overrides belong to an engagement; demo mode has none")
    return ctx.store


class ScenarioRequest(BaseModel):
    overrides: dict[str, float] = Field(
        default_factory=dict, description="assumption id -> value, for this run only"
    )
    mode: str = Field("base", pattern="^(low|base|high)$")
    rows: str = "role_family"
    cols: str = "misconduct_category"


@router.post("/scenarios/run", tags=["assumptions"])
def run_scenario(body: ScenarioRequest, ctx: Ctx = Depends(get_ctx)) -> dict:
    """Re-run the engine with overridden assumptions. Nothing on disk changes."""
    state = ctx.state
    _guard(ctx, Capability.RUN_SCENARIOS, "/scenarios/run")
    _audit(ctx, "/scenarios/run", detail=body.model_dump())
    state.ensure_loaded()
    try:
        scenario = state.run(mode=body.mode, overrides=body.overrides)
    except MissingAssumptionError as exc:
        raise HTTPException(400, str(exc)) from None
    baseline = state.run(mode=body.mode)
    delta = scenario.net - baseline.net
    return {
        "caption": _caption(ctx),
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
        "summary": _summary(ctx, scenario),
    }


# ---------------------------------------------------------------------------
# exports
# ---------------------------------------------------------------------------
@router.get("/exports/matrix.csv", tags=["exports"])
def export_matrix(
    rows: str = Query("role_family"),
    cols: str = Query("misconduct_category"),
    filters: str | None = Query(None),
    mode: str = Query("base", pattern="^(low|base|high)$"),
    ctx: Ctx = Depends(get_ctx),
) -> Response:
    state = ctx.state
    parsed = _filters(filters)
    _guard(ctx, Capability.VIEW_AGGREGATES, "/exports/matrix.csv", parsed)
    _audit(ctx, "/exports/matrix.csv", filters=parsed)
    state.ensure_loaded()
    matrix = build_matrix(
        state.run(mode=mode),
        rows=rows,
        cols=cols,
        filters=parsed,
        min_cell_size=state.min_cell_size,
        ledger=ctx.ledger(),
        fte_by_row=fte_denominators(state.dataset, rows),
    )
    import csv
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([f"# {_caption(ctx)}"])
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


@router.get("/exports/summary.md", tags=["exports"])
def export_summary(
    mode: str = Query("base", pattern="^(low|base|high)$"),
    ctx: Ctx = Depends(get_ctx),
) -> Response:
    state = ctx.state
    _guard(ctx, Capability.VIEW_AGGREGATES, "/exports/summary.md")
    _audit(ctx, "/exports/summary.md")
    state.ensure_loaded()
    s = _summary(ctx, state.run(mode=mode))
    return Response(_summary_markdown(s, _agency(ctx), _caption(ctx)), media_type="text/markdown")


def _summary_markdown(s: dict[str, Any], agency: str, caption: str) -> str:
    def money(text: str | None) -> str:
        return f"${Decimal(text):,.0f}" if text else "n/a"

    lines = [
        f"# {agency} - Cost of Discipline",
        "",
        f"*{caption}*",
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
        "and the assumptions they used. The owner has signed off on every assumption in the "
        "registry, but sign-off is not verification: most values are still professional "
        "estimates carrying low or medium confidence, and the Assumptions view shows the "
        "source and confidence behind each one.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# application
# ---------------------------------------------------------------------------
def _installation_salt(settings: Settings) -> str:
    """Pseudonyms must not be guessable from the source code, so each installation gets its
    own salt the first time it starts. `WRI_PSEUDONYM_SALT` overrides it."""
    import os
    import secrets

    if os.environ.get("WRI_PSEUDONYM_SALT"):
        return os.environ["WRI_PSEUDONYM_SALT"]
    path = Path(settings.data_dir) / "pseudonym.salt"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    demo = settings.auth_mode == "demo"
    runtime = Runtime(settings=settings)

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if runtime.store is not None:
            runtime.store.close()

    app = FastAPI(
        lifespan=lifespan,
        title="WRI Engine - Discipline Cost Baseline",
        version="0.2.0",
        description=(
            "Phase 1 of the Workforce Risk Intelligence engine: the fully loaded cost of "
            "discipline. Every figure is traceable to the line items and assumptions that "
            "produced it." + ("\n\n" + CAPTION if demo else "")
        ),
    )
    app.state.runtime = runtime

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "auth_mode": settings.auth_mode}

    @app.exception_handler(NoDataset)
    def no_dataset(_request, exc: NoDataset):
        from fastapi.responses import JSONResponse

        return JSONResponse({"detail": str(exc)}, status_code=409)

    if demo:
        # The synthetic walkthrough: header-chosen roles, one built-in dataset, old paths.
        app.include_router(router)
        return app

    from wri_engine.access.roles import set_pseudonym_salt
    from wri_engine.api.accounts import router as accounts_router

    set_pseudonym_salt(_installation_salt(settings))
    runtime.store = Store(settings.database_url)
    runtime.workspaces = Workspaces(settings.engagements_dir, runtime.store)
    app.include_router(accounts_router)
    app.include_router(router, prefix="/engagements/{engagement_id}")
    return app


app = create_app()
