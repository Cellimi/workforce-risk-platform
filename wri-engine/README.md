# Cost of Discipline — WRI Engine, Phase 1

**Every agency tracks discipline as paperwork. Nobody can say what it costs.** This tool
answers that question: it joins HR records to discipline records and reports the fully loaded
cost of discipline for a county government — broken down by employee type and misconduct
type, and **traceable to the last dollar**. Click any figure and it decomposes into line
items that each name their own formula, the values used, and the assumption behind them.

> ### All data here is synthetic
> **Harlow County does not exist.** The data is generated from public distributional shapes
> and documented judgement. Nothing is derived from any real agency's records, and no real
> county, agency, union, employer or person is represented. Cost figures reflect documented
> assumptions, **not measured agency costs** — 151 of the 166 assumptions are still owner
> placeholders, and the demo marks every one of them.

> **Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli. All
> rights reserved — see [LICENSE](LICENSE). Reading this code grants no right to use, copy,
> modify or distribute it.

---

## Run it — four commands

```bash
make install      # create the virtual environment and install dependencies
make generate     # build the synthetic county dataset
make api          # terminal one — the engine  (http://localhost:8000/docs)
make ui           # terminal two — the demo    (http://localhost:8501)
```

Needs Python 3.11 or newer, and nothing else. `make api` and `make ui` run at the same time,
in two terminals. Then open <http://localhost:8501>.

`make generate` is optional the first time — a small sample dataset is committed, so the
engine and the tests run on a fresh clone without it. Run it for the full
2,500-employee county.

## What you'll see

Five pages, and a role selector in the sidebar that changes what each role is allowed to see:

1. **Executive summary** — what discipline costs per year and per employee, where the money
   goes, and a "How this number was calculated" panel beside every headline figure.
2. **Cost matrix** — employee type against misconduct type. Small groups are suppressed to
   protect individuals, and the totals still reconcile.
3. **Drill-down** — one action, every line item, back to the assumption behind it.
4. **Assumptions** — all 166 coefficients with their source and confidence. Move a slider and
   the whole model recomputes.
5. **Data quality** — what failed validation and what that excluded from the totals.

`docs/demo_script.md` walks the five pages as a five-minute presentation.
`docs/cost_methodology.md` explains every formula, including the judgement calls.

---

## Everything below is detail

`make test` runs 146 tests with a coverage gate on the cost and aggregation code.
This project is self-contained and shares no code with the earlier MVP at the root of this
repository.

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
| C3-offset | Unpaid suspension savings | The wage not paid during the suspension, as a **negative** line item, so net never hides it |
| C4 | Appeals and grievances | Internal hours, outside counsel, arbitration fees, back pay, interest, settlements — only where a record exists |
| C5 | Removal turnover | Vacancy coverage, recruiting, screening, academy, field training, ramp-up, equipment, adjusted for recruit washout |
| C5-offset | Vacancy salary savings | The removed employee's salary, which stops on the separation date, credited back over the same shifts the vacancy overtime was charged for |

The two offsets exist because the engine charges **gross**: it bills the overtime that covers
a suspended or vacant post at the rate of whoever works the shift, then credits back the wage
the county stopped paying, at that person's own rate. Only the wage and the payroll taxes on
it are treated as saved — health insurance and retiree health continue either way.

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

---

## License

**Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli. All
rights reserved. See [`LICENSE`](LICENSE).

Publication is not a license. No right to use, copy, modify or distribute is granted, and
cloning this repository does not confer one. Third-party dependencies keep their own terms;
see `pyproject.toml` for the list.

The license also records what this software is not: it produces cost estimates from
configurable assumptions, and those are neither measured agency costs nor legal, actuarial,
financial or HR advice.
