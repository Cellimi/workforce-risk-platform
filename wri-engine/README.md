# WRI Engine — Phase 1: the fully loaded cost of discipline

The first working slice of the Workforce Risk Intelligence engine. It joins HR records to
discipline records for a fictional county government and reports **what discipline actually
costs**, broken down by employee type and misconduct type — with every dollar traceable back
to its formula, its inputs, and the assumption it used.

> **Synthetic data for a fictional county. Cost figures reflect documented assumptions, not
> measured agency costs.** Harlow County does not exist. The data is generated from public
> distributional shapes and documented judgement; nothing in this repository is derived from
> any real agency's records, and no real county, agency, union or person is represented.

This project is self-contained. It shares no code with the Streamlit MVP at the root of this
repository.

---

## Run the demo in under ten minutes

```bash
cd wri-engine
make install          # venv + dependencies (Python 3.11+)
make generate         # write the synthetic county HR export to data/synthetic/
make test             # 146 tests, coverage gate on costing/ and aggregation/
make api              # terminal one: http://localhost:8000  (docs at /docs)
make ui               # terminal two: http://localhost:8501
```

A trimmed sample export is committed at `data/synthetic/sample/`, so the API and tests run
without `make generate`. Use `make generate` for the full 2,500-employee dataset.

Then read `docs/demo_script.md` and walk the five pages in order.

---

## What it does

**Reads a source system, not a schema you have to build.** A county HR flat-file export —
its own column names, code values and date format — is translated by an adapter into one
canonical schema. The engine sees nothing else. A second source system means a second adapter
and a mapping file; the cost logic does not move. The NFC adapter is designed and stubbed with
its full field mapping in `src/wri_engine/adapters/nfc.py`.

**Costs five components, each a pure function.**

| | Component | What it counts |
|---|---|---|
| C1 | Processing labor | Supervisor, HR/labor relations, deciding official and investigator hours |
| C2 | Paid administrative leave | Scheduled shifts of leave × the employee's loaded rate |
| C3 | Backfill overtime | Overtime covering minimum-staffing posts, at the peer's rate and the overtime burden — not the full benefits multiplier |
| C3-offset | Unpaid suspension savings | The wage not paid, as a **negative** line item, so net never hides it |
| C4 | Appeals and grievances | Internal hours, outside counsel, arbitration fees, back pay, interest, settlements — only where a record exists |
| C5 | Removal turnover | Vacancy coverage, recruiting, screening, academy, field training, ramp-up, equipment, adjusted for recruit washout |

**Explains every dollar.** Each line item stores its formula in plain English, the values it
used, and the ids of the assumptions it consumed:

```
Backfill for a 5-shift suspension in a minimum-staffing post: 5 shifts × 12 hrs ×
$48.44/hr overtime ($30.00 base × 1.5 FLSA premium × 1.0765 overtime burden;
average base rate for deputy at North District)
```

**Puts every coefficient in a file, not in code.** 166 assumptions in
`config/assumptions.yaml`, each with a source, a confidence level, low/base/high bounds, an
owner and a status. Asking for an assumption that is not there is a hard error, never a
silent default. 151 are still owner placeholders and are marked `TBD-MIKE`.

**Enforces use limitation in the engine.** Aggregate cells backed by fewer than five distinct
employees are never sent to a caller; complementary suppression stops a hidden cell being
recovered from a row total; a per-session disclosure ledger blocks differencing across
overlapping queries. Roles are enforced by the API — an `executive` asking for a record
drill-down gets a 403, not a hidden button. Every request is written to an append-only audit
log.

---

## What it deliberately does not do

Phase 1 is the cost baseline. **No pattern detection, hotspot alerting or risk scoring** —
that is Phase 2. **No root-cause or intervention cost-benefit modelling** — that is Phase 3.
**Nothing is probability-weighted**: a cost is counted only when the record shows the event
happened.

The synthetic data contains one deliberately planted pattern — elevated attendance violations
for Corrections Officers at the Main Detention Center. The cost baseline surfaces it as an
expensive cell, never as an alert. It is documented in
`src/wri_engine/generator/README.md` so nobody mistakes a planted pattern for a discovery.

---

## Layout

```
wri-engine/
  CLAUDE.md                     conventions, and the rules that must not be broken
  config/
    assumptions.yaml            166 cost coefficients, each with its source
    org_county.yaml             departments, pay plan, schedules, taxonomies
    generator_profile.yaml      every synthetic-data distribution parameter
    mapping_county_hr_csv.yaml  the whole source-to-canonical contract
  src/wri_engine/
    schema/                     canonical Pydantic models
    adapters/                   base.py, county_hr_csv.py, validation.py, nfc.py (stub)
    generator/                  seeded synthetic generator + README of distributions
    costing/                    assumptions, rates, context, components/, engine
    aggregation/                rollups, suppression
    access/                     roles, pseudonymization, audit log
    api/                        FastAPI service
  app/                          Streamlit UI — HTTP only, never imports the engine
  tests/golden/                 nine hand-calculated cases, asserted to the cent
  docs/
    cost_methodology.md         every formula, every simplification, in prose
    demo_script.md              the five-minute walkthrough
```

---

## The API

`http://localhost:8000/docs` for the generated OpenAPI. The role goes in an `X-WRI-Role`
header (`executive`, `hr_analyst`, `admin`).

| Endpoint | |
|---|---|
| `POST /datasets/load` | Run the adapter and its validation rules; returns the report |
| `GET /datasets/validation` | The data quality panel |
| `GET /costs/matrix` | The cost matrix, suppression already applied |
| `GET /costs/summary` | Headline figures |
| `GET /costs/actions/{id}` | Every line item with its formula (role-gated) |
| `GET /assumptions` | The registry with sources and statuses |
| `POST /scenarios/run` | Re-run with overridden assumptions; nothing on disk changes |
| `GET /exports/matrix.csv`, `GET /exports/summary.md` | Exports |

---

## Testing

```bash
make test     # pytest with an 85% coverage gate on costing/ and aggregation/
make lint     # ruff
```

- **Nine golden cases** (`tests/golden/`), each a tiny dataset plus the arithmetic worked out
  by hand in a comment, asserted line item by line item to the cent. They cover an unpaid
  suspension with backfill and offset, a paid-leave removal that went to arbitration and won
  partial back pay, a civilian removal with a refilled position, a sworn removal with academy
  and washout, a pending appeal, an abolished position, a counseling, a pending refill, and an
  open filing window.
- **Property tests**: only the offset may be negative; net equals gross plus offset; rollup
  totals equal the sum of line items; low ≤ base ≤ high.
- **Suppression and differencing tests**, including the two-overlapping-queries attack.
- **Role enforcement**: `executive` gets 403 on every record-level endpoint.
- **Adapter tests**: every validation rule has a fixture built to trip it.
- **A UI boundary test** that parses `app/*.py` and fails if anything there imports the engine.

---

## Before this is shown externally

`config/assumptions.yaml` marks 151 values `TBD-MIKE`. The ones that move the headline most:

1. The **sworn benefits multiplier** (currently an owner estimate; confirm against BLS ECEC
   Table 3, protective service occupations).
2. **C1 processing and investigation hours**.
3. **Outside counsel rate and the county's arbitration cost share**.
4. **Whether overtime is pensionable**, which drives the C3 overtime burden.
5. **C5 turnover inputs by role family** — advertising, background investigation, academy
   length and cost, field training length, washout rates, civilian ramp weeks.
6. The **civilian vacancy productivity-loss factor**.
7. The **minimum cell size** for suppression (default 5).
8. The **final fictional county name** — "Harlow County" is a placeholder.

Every one of them is visible, tinted, on the Assumptions page of the demo.
