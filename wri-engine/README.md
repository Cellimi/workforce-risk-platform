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
> assumptions, **not measured agency costs**. The owner has approved all 184
> assumptions, but approval is not verification: 91 are low-confidence professional
> estimates, and the demo tints every one of them.

> **Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli. All
> rights reserved — see [LICENSE](LICENSE). Reading this code grants no right to use, copy,
> modify or distribute it.

---

## Run it — five commands

```bash
make install          # create the virtual environment and install dependencies
make generate         # build the synthetic county dataset
make test             # 260 tests, coverage gate
make api              # terminal one: the engine in demo mode (http://localhost:8000/docs)
make ui               # terminal two: the screen (http://localhost:8501)
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

## Two ways to run it

**The synthetic walkthrough (`make api`, demo mode).** No sign-in. The caller picks a role in
an `X-WRI-Role` header and the only data is fictional Harlow County. This is for showing the
method. It must never be reachable by anyone you would not hand the data to.

**A licensed installation (`make serve`, the default mode).** Every request needs a signed-in
user. Work is organised into **engagements**: one client, one dataset, one set of
client-specific assumption overrides. A user sees an engagement only if they are a member,
with the role their membership gives them.

```bash
wri-admin create-user you@firm.example --name "You" --firm-admin   # once, on the server
make serve                                                          # terminal one
make ui                                                             # terminal two
```

Open <http://localhost:8501> and sign in. The screen asks the server which mode it is in, so
the same `make ui` serves the demo and a licensed installation.

| Page | Who sees it | What it does |
|---|---|---|
| Engagement | everyone | Members and roles (engagement lead); new engagements and user accounts (firm administrator) |
| Load data | engagement lead | Template download, upload, column matching, code translation, preview, commit |
| Executive summary, Cost matrix, Data quality | everyone | The results, labelled with the client's name |
| Drill-down | lead and analyst | One action, every line item |
| Assumptions | everyone | The registry; the lead also sets client-specific values, each with a reason |

A page a role cannot use is not offered, but the server is what refuses it. Choices made on
**Load data** are saved to the engagement when the mapping is checked; reloading the page
before then loses them. Reloading also signs you out.

Everything the screen does is also available over the API (`/docs` lists it):

| Step | Endpoint |
|---|---|
| Sign in, get a bearer token | `POST /auth/login` |
| Create consultant and client accounts (firm administrator) | `POST /users` |
| Open an engagement (firm administrator) | `POST /engagements` |
| Add members as `admin`, `hr_analyst` or `executive` | `PUT /engagements/{id}/members` |
| Load the client's data (see "Loading a client's data" below) | `/engagements/{id}/intake/...` |
| Use the client's own figure for an assumption, with a reason | `PUT /engagements/{id}/assumptions/{assumption_id}/override` |
| Everything in the table under "The API" below | `/engagements/{id}/...` |

What is stored where: accounts, sessions, engagements, memberships and overrides in a
database (`data/wri.db`, SQLite by default; set `WRI_DATABASE_URL` for another). Each
engagement's source files in `data/engagements/<id>/source/`. Nothing is kept only in memory,
so a restart loses no work. Passwords are stored as scrypt hashes and session tokens as
SHA-256 hashes.

### Loading a client's data

A client's export arrives in its own layout. It is never edited by hand; a **mapping** says
how to read it, and the mapping is saved with the engagement.

| Step | Endpoint | What happens |
|---|---|---|
| 1. Send the client the template, if they have no clean export | `GET .../intake/template.zip` | Blank CSVs and a plain-language field guide |
| 2. Upload what the client sent | `POST .../intake/files` | Held apart from the engagement's data. The reply is a draft mapping |
| 3. Finish the mapping and preview | `PUT .../intake/mapping` | Shows what would load and every problem. Repeat until right |
| 4. Commit | `POST .../intake/commit` | Becomes the engagement's dataset |

The draft mapping in step 2 does most of the work:

- matches columns by name, synonym and abbreviation (`Worker #`, `Start Dt`, `Case No`);
- guesses the date format, and says when day and month could be swapped;
- lists every code that needs translating, with counts (`Pay Type: E 9, N 31`);
- sets up the fields an export rarely holds -- sworn, minimum staffing, schedule, turnover
  profile -- to be decided **once per job family**, with every job family listed.

Files already in the template layout can skip the mapping:
`POST .../datasets/upload?source=wri_template`.

The load report is designed to keep a total honest. When a detail is missing the engine
takes the lighter assumption and says so (an investigation of unknown type is costed as a
supervisory inquiry), and when a cost cannot be counted it says which cases and why (a
removal with no recorded separation carries no replacement cost).

Not built yet: single sign-on, the private-sector cost model, engagement license keys, and a
white-labelled client report.

---

## What it does

**Reads a source system, not a schema you have to build.** A county HR flat-file export —
its own column names, code values and date format — is translated by an adapter into one
canonical schema. The engine sees nothing else. A second source system means a second adapter
and a mapping file; the cost logic does not move. The NFC adapter is designed and stubbed with
its full field mapping in `src/wri_engine/adapters/nfc.py`.

**Costs five components in the public sector and seven in the private, each a pure
function.** Which of them can run is the engagement's **sector pack**
(`src/wri_engine/sectors/`): it names the components, the benefits multiplier that loads a
wage, and the coefficient ids that must exist before the engine starts. No component
branches on the sector -- C6 and C7 emit a line only when the record carries the field they
need, and public-sector data carries none of them.

| | Component | What it counts |
|---|---|---|
| C1 | Processing labor | Supervisor, HR/labor relations, deciding official and investigator hours |
| C2 | Paid administrative leave | Scheduled shifts of leave × the employee's loaded rate |
| C3 | Backfill overtime | Overtime covering minimum-staffing posts, at the peer's rate and the overtime burden — not the full benefits multiplier |
| C3-offset | Unpaid suspension savings | The wage not paid during the suspension, as a **negative** line item, so net never hides it |
| C4 | Appeals and grievances | Internal hours, outside counsel, arbitration fees, back pay, interest, settlements — only where a record exists |
| C5 | Removal turnover | Vacancy coverage, recruiting, screening, academy, field training, ramp-up, equipment, adjusted for recruit washout |
| C5-offset | Vacancy salary savings | The removed employee's salary, which stops on the separation date, credited back over the same shifts the vacancy overtime was charged for |
| C6 | Separation and unemployment | *Private sector.* Severance actually paid, the HR and counsel hours behind the agreement, and unemployment benefits charged to the employer's account |
| C7 | Employment practices liability | *Private sector.* The retention the employer bore, where nothing else in the record captured the payout |
| C7-offset | Insurer payments | *Private sector.* What an insurer paid, as a **negative** line item, so gross shows the full exposure and net the employer's own burden |

The offsets exist because the engine charges **gross**: it bills the overtime that covers
a suspended or vacant post at the rate of whoever works the shift, then credits back the wage
the county stopped paying, at that person's own rate. Only the wage and the payroll taxes on
it are treated as saved — health insurance and retiree health continue either way. C7-offset
does the same job for an insured private employer: the money was spent, and net says who
spent it.

**Explains every dollar.** Each line item stores its formula in plain English, the values it
used, and the ids of the assumptions it consumed:

```
Backfill for a 5-shift suspension in a minimum-staffing post: 5 shifts × 12 hrs ×
$48.44/hr overtime ($30.00 base × 1.5 FLSA premium × 1.0765 overtime burden;
average base rate for deputy at North District)
```

**Puts every coefficient in a file, not in code.** 184 assumptions in
`config/assumptions.yaml`, each with a source, a confidence level, low/base/high bounds, an
owner and a status. Asking for an assumption that is not there is a hard error, never a
silent default.

The owner signed off on the 167 entries the public-sector model runs on, on 2026-11-12, and
on the 12 the private-sector model added, on 2026-11-13. That is a sign-off, not a
measurement: 15 of those 179 carry a cited source, the other 164 are professional estimates
the owner stands behind, and 91 remain **low confidence**. `status` says who approved a
value; `confidence` says how much weight it bears, and the two moved independently — sign-off
raised no confidence level, and the 15 sourced entries stayed `researched` rather than being
flattened to `confirmed`, because naming a source is the stronger claim.

Five more entries were added after that sign-off and reviewed by the owner the same day:
the private-industry benefits multipliers for each broad occupational group, each read from
its own BLS series. They stay `researched` for the same reason. That makes 184 entries, 20
with a cited source, and nothing awaiting review.

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
    assumptions.yaml            184 cost coefficients, each with its source
    org_county.yaml             departments, pay plan, schedules, taxonomies
    generator_profile.yaml      every synthetic-data distribution parameter
    mapping_county_hr_csv.yaml  the whole source-to-canonical contract
  src/wri_engine/
    schema/                     canonical Pydantic models
    adapters/                   base.py, county_hr_csv.py, validation.py, nfc.py (stub)
    generator/                  seeded synthetic generator + README of distributions
    sectors/                    which components, multipliers and ids a sector runs
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

`http://localhost:8000/docs` for the generated OpenAPI. In demo mode the role goes in an
`X-WRI-Role` header (`executive`, `hr_analyst`, `admin`) and the paths are as shown. In a
licensed installation the same paths sit under `/engagements/{id}` and the role comes from
the signed-in user's membership.

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

The owner has approved every value in `config/assumptions.yaml`, so the engine is cleared to
run. What approval does **not** do is turn an estimate into a measurement. These are the ones
most worth replacing with the customer's own figures, because they move the headline most and
are the least grounded:

1. The **sworn benefits multiplier**, 1.75. The owner accepted it on 2026-11-12 as the
   working value and chose not to block on sourcing it, so the engine runs on it. It is
   still an estimate rather than a cited figure, still marked low confidence, and it scales
   every public-safety number in the model — the civilian multiplier beside it comes from
   BLS ECEC and this one does not. Verifying it against ECEC Table 3, protective service
   occupations, remains the single highest-value thing anyone could do to this registry.
2. **C1 processing and investigation hours**.
3. **Outside counsel rate and the county's arbitration cost share**.
4. **Whether overtime is pensionable**, which drives the C3 overtime burden.
5. **C5 turnover inputs by role family** — advertising, background investigation, academy
   length and cost, field training length, washout rates, civilian ramp weeks.
6. The **civilian vacancy productivity-loss factor**.
7. The **minimum cell size** for suppression (default 5).
8. The **12 private-sector coefficients**, accepted by the owner on 2026-11-13. Two are
   researched from a statutory or government source — the private benefits multiplier from
   BLS ECEC, and the 300-day agency-charge filing window from Title VII — and keep that
   status. The other ten are owner estimates he now stands behind: the temp-agency markup,
   the outside-investigator rate, the three challenge-forum hour figures, two non-statutory
   filing windows, the two separation-agreement hour figures and the unemployment
   experience-rating multiplier. Each still records that it is not a researched figure and
   what would settle it, usually the client's own invoices. See section 8 of
   `docs/cost_methodology.md`.
9. The **five private benefits multipliers by occupational group**, reviewed by the owner
   on 2026-11-13. They run from 1.2996 for service work to 1.4686 for production and
   transportation, each from its own BLS series. Which one applies is a selection: the user
   picks the client's occupation group when opening a private-sector engagement and can
   change it later, and a row in the client's file that names its own group keeps it. With
   nothing selected the wage is loaded at the all-occupations 1.4287, and the load report
   says for how many people.
10. **Sales and office is one selection, and its two halves differ.** BLS reports sales at
    1.3283 and office and administrative support at 1.4564 but the product offers them as
    one group at 1.3977. A client whose staff are mostly one or the other is better served by
    its own figure for that assumption.

The fictional county name is settled: **Harlow County**, confirmed by the owner on
2026-11-12 in `config/org_county.yaml`. It is invented, and
`tests/test_config_consistency.py` fails the build if the agency is ever marked non-fictional
or if a name from its banned list of real unions and agencies turns up in the configuration.

Every low-confidence value is visible, tinted, on the Assumptions page of the demo, with
its source beside it.

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
