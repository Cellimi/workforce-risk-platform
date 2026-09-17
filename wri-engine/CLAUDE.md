# CLAUDE.md — WRI Engine conventions

This project computes the **fully loaded cost of discipline** for a fictional county
government. It is a production foundation, not a throwaway demo. Only the Streamlit UI
layer is disposable.

## Hard rules

1. **Never hardcode a cost coefficient in Python.** Every coefficient lives in
   `config/assumptions.yaml` and is fetched by ID. A missing ID is a hard error, not a
   silent default.
2. **Never add a cost component without a golden test** in `tests/golden/`.
3. **Never let the UI import the engine.** `app/streamlit_app.py` talks to the FastAPI
   service over HTTP only. If you find yourself writing `from wri_engine...` in `app/`,
   stop and add an API endpoint instead.
4. **Never use real agency, county, or union names, or real employee data.** The county is
   fictional ("Harlow County"). Unions are generic ("Deputies' Association").
5. **Money is `Decimal`.** Never `float`. Round only at display time. Dates are
   timezone-naive `datetime.date`.
6. **All randomness is seeded.** The generator takes `--seed` and is deterministic.
7. **When a value is unknown, add a `TBD-MIKE` assumption and move on.** Do not invent
   certainty. A sourced figure must carry its citation in the YAML.
8. **Every dollar is explainable.** Each `CostLineItem` records its `formula`, its
   `inputs`, and the `assumption_ids` it consumed. No black-box totals.
9. **Actuals over estimates.** Cost a thing only when the record says it happened. Nothing
   in the baseline is probability-weighted.
10. **Use limitation is architectural.** Suppression and role gating live in
    `aggregation/` and `access/`, enforced before data reaches the API response — never in
    the UI.

## Style

- Prefer small, readable functions. Each component module in `costing/components/` must be
  understandable by a non-engineer HR reader from its module docstring alone.
- Component functions are pure: `(record, context, assumptions) -> list[CostLineItem]`.
  No I/O, no globals, no clock reads.
- `formula` strings are written for a human: `"12 hrs x $58.20 loaded rate"`, not a repr.

## Layout

| Path | What lives there |
|---|---|
| `config/` | `assumptions.yaml` (cost coefficients + sources), `org_county.yaml` (departments, grades, taxonomies) |
| `src/wri_engine/schema/` | Canonical Pydantic models. Source formats never appear here. |
| `src/wri_engine/adapters/` | One module per source system. Each returns a `CanonicalDataset`. |
| `src/wri_engine/generator/` | Synthetic data generator, emits *source-format* CSV |
| `src/wri_engine/costing/` | Assumptions loader, components C1-C5, engine orchestration |
| `src/wri_engine/aggregation/` | Rollups + small-group suppression |
| `src/wri_engine/access/` | Role definitions, enforcement, audit log |
| `src/wri_engine/api/` | FastAPI app |
| `app/` | Streamlit UI (HTTP client only) |
| `docs/` | `cost_methodology.md` is the human-readable spec for every formula |

## Commands

```bash
make install     # venv + deps
make generate    # write synthetic CSVs to data/synthetic/
make test        # pytest with coverage gates on costing/ and aggregation/
make lint        # ruff
make api         # uvicorn on :8000
make ui          # streamlit on :8501 (needs the API running)
```

## Phase boundary

Phase 1 is the **cost baseline only**. Do not add pattern detection, hotspot alerting, risk
scoring, or intervention cost-benefit modeling — those are Phases 2 and 3. If the data shows
a hot cell, the matrix surfaces it as an expensive cell, never as an alert.
