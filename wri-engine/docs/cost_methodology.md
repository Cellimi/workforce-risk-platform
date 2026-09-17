# Cost methodology

How every dollar in the WRI discipline cost baseline is produced, in the order the engine
produces it. If a figure on screen looks wrong, this document plus the action's own line
items should be enough to find out why without reading any Python.

> **Synthetic data for a fictional county. Cost figures reflect documented assumptions, not
> measured agency costs.** Harlow County does not exist. Nothing here is derived from any
> real agency's records.

---

## 1. The rules the model holds itself to

**Gross is charged, and savings are credited back separately.** Where the county keeps paying
someone for shifts they do not work, or pays overtime to cover a post, the engine charges the
full amount — then records what the county stopped paying as a **negative line item** in its
own offset component. Totals always report gross, offset and net separately, so a saving can
never be quietly used to shrink a cost. There are two offsets: `C3-offset` for an unpaid
suspension and `C5-offset` for the salary of a vacant post after a removal. Both are defined
once, in `OFFSET_COMPONENTS`, and everything that needs to know "is this a saving?" asks that
set.

**Every dollar is explainable.** Each `CostLineItem` stores the amount, a human-readable
`formula`, the actual `inputs` used, and the ids of every assumption consumed. Nothing is
aggregated without those travelling with it.

**Actuals over estimates.** A cost is counted only when the record shows the event happened:
an appeal was filed, leave was taken, the employee was removed, the position was refilled.
Nothing in the baseline is probability-weighted. An appeal that looked likely and was never
filed costs zero.

**Incomplete is a state, not an estimate.** Where more cost is still coming -- a pending
appeal, an open filing window, a position not yet refilled -- the engine reports what has
actually been spent and sets `cost_incomplete`. It does not forecast the rest, except for the
one documented case in C5 below, which is flagged the same way.

**No coefficient lives in code.** Every number the engine multiplies by comes from
`config/assumptions.yaml` by id. Asking for an id that is not there is a hard error, never a
silent default.

**Use limitation is enforced in the engine.** Suppression and role gating happen before data
reaches the API response, not in the UI.

---

## 2. Core rates

### Hourly base rate

```
hourly_base_rate = annual_base_salary / annual_paid_hours
```

`annual_paid_hours` comes from the role's **schedule**, not a flat 2,080. A firefighter on a
24/48 rotation is paid for 2,912 hours a year, so the same salary buys a very different
hourly rate than a 2,080-hour civilian post. Getting this wrong would overstate fire costs by
about 40%.

| Schedule | Shift | Annual paid hours | Who is on it |
|---|---|---|---|
| `civilian_5x8` | 8 hrs, Mon-Fri | 2,080 | Civilian staff, lieutenants |
| `shift_12` | 12 hrs, ~every other day | 2,080 | Deputies, corrections, dispatch |
| `fire_24_48` | 24 hrs, one day in three | 2,912 | Fire & Rescue |

### Loaded hourly rate

```
loaded_hourly_rate = hourly_base_rate x benefits_multiplier[benefits_group]
```

| Assumption | Value | Status |
|---|---|---|
| `benefits_multiplier_civilian` | **1.627** | researched |
| `benefits_multiplier_sworn` | **1.75** | `TBD-MIKE` |

The civilian multiplier is the ratio of total compensation to wages for state and local
government workers in the BLS **Employer Costs for Employee Compensation** release for March
2026 (USDL-26-0827, released 2026-06-12), Table 1 "By ownership": total compensation
$66.41 per hour worked against wages and salaries of $40.82 per hour worked, giving
66.41 / 40.82 = **1.627**.

**Two caveats a reader should know about.**

1. *ECEC is per hour **worked**; our base rate is per hour **paid**.* ECEC counts paid leave
   as a benefit, so applying its ratio to a salary-divided-by-2,080 rate double-counts paid
   leave hours slightly. The effect biases loaded cost **upward** by roughly the paid-leave
   share. The cleaner form is a multiplier built from the ECEC benefit lines excluding paid
   leave; that refinement is recorded in the assumption's notes.
2. *The sworn multiplier is not sourced yet.* Public-safety pension normal cost and OPEB
   typically run well above the all-occupations state and local average, which is why the
   base value is higher. The figure to confirm it against is ECEC Table 3 (state and local
   government by occupational group, "protective service"); `bls.gov` was unreachable from
   the build environment, so 1.75 is an owner estimate with **low** confidence and its low
   bound deliberately set to the all-occupations figure. **This is open item 1 for external
   showing.**

### Rates for people who are not in the extract

HR and labor relations staff are never in an HR discipline extract, and senior deciding
officials are usually not named on the action. Their hours are costed at a reference annual
salary from the registry, converted at `c1_reference_annual_hours` (2,080) and loaded with
the civilian multiplier. When the record *does* name the person -- a supervisor, or a
deciding official who is an employee in the dataset -- their own loaded rate is used instead,
and the line item's formula says which happened.

---

## 3. C1 -- Processing labor

Every action consumes staff time before it produces any other cost.

```
line item = hours x that person's loaded hourly rate
```

Hours by action type (`c1_hours_<action>_<actor>`, all `TBD-MIKE` starting defaults):

| Action type | Supervisor | HR / LR | Deciding official |
|---|---:|---:|---:|
| Documented counseling | 2 | 0.5 | 0 |
| Written reprimand | 4 | 2 | 1 |
| Suspension (any length) | 12 | 10 | 4 |
| Demotion | 20 | 15 | 6 |
| Removal | 30 | 20 | 8 |

A zero-hour entry produces **no line item at all** rather than a $0 one, so the drill-down
does not fill up with noise.

Investigation hours are added when `was_investigated` is true:

| Investigation type | Hours | Costed at |
|---|---:|---|
| Supervisory | 6 | the employee's supervisor's rate |
| HR | 16 | the HR / labor relations reference rate |
| Internal affairs | 40 | the division-head reference rate |

Internal affairs investigators hold supervisory rank, so costing their hours at a
line-employee rate would understate them. That choice is a judgement, and it is visible in
every affected line item's formula.

---

## 4. C2 -- Paid administrative leave

The most literal cost in the model. It comes entirely from the dates on the leave record.

```
cost = scheduled shifts in the leave period x shift hours x loaded hourly rate
```

"Scheduled shifts" comes from the schedule model, not calendar days: a Monday-to-Friday
civilian is not charged for the weekend or for a county holiday, and a 24/48 firefighter is
charged for one shift in three. The line item records the calendar days, the schedule used,
and the resulting shift count, so the conversion is checkable.

Only **paid** leave is counted. Unpaid leave costs nothing.

---

## 5. C3 -- Backfill overtime, and the C3-offset

### Backfill

Applies only when `minimum_staffing_role` is true **and** the action involves a suspension or
paid administrative leave. A post with a minimum staffing requirement has to be filled;
someone else works the shift on overtime.

```
backfill = lost shifts x shift hours x peer base rate x FLSA premium x overtime burden
```

Three things about that formula matter.

**The peer's rate, not the disciplined employee's.** Overtime is worked by whoever covers the
post, so the rate is the average base rate for that role family **at that location**. It
falls back to the role family across all locations, and finally to the employee's own rate,
and the line item says which was used.

**The premium is statutory.** `c3_ot_premium_multiplier` = **1.5**, from the FLSA, 29 U.S.C.
207(a)(1). This is the one coefficient in the registry with high confidence and `confirmed`
status.

**The burden is *not* the full benefits multiplier.** `c3_ot_burden_multiplier` = **1.0765**,
which is employer FICA only (6.2% OASDI + 1.45% HI, 26 U.S.C. 3111). Health insurance and
retiree health do not grow because somebody works an extra shift. Pension is the open
question: where overtime is pensionable, this multiplier should rise by the employer normal
cost rate, and the high bound of 1.35 assumes a public-safety employer contribution around
27% applies. **This is open item 3.**

**Paid administrative leave costs the county twice** -- the leave itself (C2) and the
overtime covering the empty post (C3). That double charge is deliberate and is the single
most counter-intuitive number in the model. The C3 line item says so in its own formula.

### The C3-offset

An unpaid suspension saves the county the wage the employee does not earn. (Its sibling,
`C5-offset` in section 7, does the same job for the salary of a post left vacant by a
removal.) That saving is
recorded as a **negative line item** so the net effect is visible and the headline can never
quietly hide it. Totals always report gross, offset and net separately.

```
offset = -(suspension shifts x shift hours x base rate x wage-scaling burden)
```

The burden here is `c3_unpaid_suspension_burden_multiplier` = 1.0765, for the same reason as
above but in reverse: the county stops paying FICA on wages not earned, but **keeps paying
health and OPEB throughout an unpaid suspension**. Using the full benefits multiplier would
overstate the saving substantially. Whether pension contributions also stop is plan-specific
and is part of open item 3.

### Known simplification: 7(k)

The demo assumes every backfill hour is an overtime hour. Under a 7(k) work period, an
agency may have straight-time capacity inside the period before overtime is owed, so this
overstates backfill for law enforcement, corrections and fire. Modelling 7(k) properly needs
actual hours-worked data by work period, which a discipline extract does not carry.

A second simplification: `suspension_days` is treated as **scheduled shifts lost**. For a
24-hour fire shift that means a "5 day" suspension costs 120 hours. Agencies that express
fire suspensions in hours rather than shifts would need a per-bargaining-unit conversion.

---

## 6. C4 -- Appeals and grievances

Counted only when an appeal or grievance record exists. Up to six line items per record:

| Line item | Formula |
|---|---|
| Internal HR hours | `c4_hr_hours_<forum>` x the HR reference loaded rate |
| Outside counsel | `outside_counsel_hours` from the record x `c4_outside_counsel_hourly_rate` |
| Arbitration fees | `c4_arbitration_flat_cost`, once per arbitration, whatever the outcome |
| Back pay | taken straight from the record |
| Back pay interest | `back_pay x c4_back_pay_interest_annual_rate x days / 365`, simple, from filing to resolution |
| Settlement | taken straight from the record |

Internal hours by forum: grievance step 10, civil service board 30, arbitration 40, court 60.
The outside counsel rate ($325), the arbitration flat cost ($7,500) and the interest rate
(5%) are all `TBD-MIKE` -- **open item 2**.

### Incomplete cost

Two situations mean the number on screen is a floor rather than a total, and both set
`cost_incomplete`:

1. **The appeal is pending**, or carries no resolution date. More cost is coming, and the
   engine does not guess at the outcome: a pending case awards no back pay and no settlement.
2. **Nobody has appealed yet, but the filing window is still open.** This emits a **$0 line
   item** rather than nothing at all, so the flag is visible in the drill-down and countable
   in every rollup.

The window used is the longest one available to that employee -- a grievance step (14 days)
or arbitration (30 days) for represented employees, the civil service board (20 days) for
non-represented ones. **Court is deliberately excluded**: its window runs months, and
including it would flag half a year of actions as incomplete without telling anyone anything.
Window lengths are `TBD-MIKE`; in practice they come from the collective bargaining agreement
or civil service rule.

---

## 7. C5 -- Removal turnover

Usually the largest component, and the one agencies never put on the discipline ledger.
Removing someone does not end the cost; it starts a second one.

Applies when a **discipline-driven separation** is linked to the action -- a removal for
cause, or a resignation in lieu of removal. A resignation in lieu follows exactly the same
path as a removal, offset included. Three rules bound it:

- **Position abolished -> no turnover cost, and no offset either.** Nobody is hired, so
  nothing is spent, and no overtime is worked to cover a post that no longer exists. The
  engine returns no C5 or C5-offset line items at all, not a reduced figure. The salary the
  county stops paying belongs to the abolished position, not to the discipline action.
- **Not yet refilled -> expected costs, flagged incomplete.** The vacancy is costed at
  `c5_expected_vacancy_days_<profile>` and every C5 line carries `cost_incomplete`. This is
  the one place the engine looks forward, and it says so.
- **Reinstated on appeal -> no separation record exists**, so C5 never runs. An overturned
  removal means nobody was replaced.

Each role family maps to a **turnover profile** -- `sworn_deputy`, `corrections`, `dispatch`,
`fire_ems`, `civilian_skilled`, `civilian_standard` -- and every C5 coefficient is keyed to
it.

### Vacancy period

For a **minimum-staffing** post, the seat is covered by overtime from the separation until
the replacement reaches solo duty, at the same overtime rate as C3 — **and the removed
employee's own salary stops, which is credited back as a C5-offset line item.**

```
vacancy overtime = scheduled shifts x shift hours x PEER base rate x 1.5 x ot burden
vacancy offset   = -(same shifts   x shift hours x REMOVED employee base rate x vacancy burden)
```

Two different rates, on purpose. The overtime is worked by whoever covers the post, so it is
priced at the role family's average base rate at that location. The saving is the removed
employee's pay stopping, so it is priced at *their* rate — not the peer average, and not the
replacement's step-1 rate. Both lines read the same `shifts` value by construction, so the
credit covers exactly the period that was charged; a property test asserts it per action.

The burden is `c5_vacancy_salary_burden_multiplier`, which starts equal to the
unpaid-suspension burden (FICA only, 1.0765) but has **its own assumption id** because a
vacancy and a suspension are different events and an employer may stop different things in
each. Health and OPEB obligations attached to the position do not vanish on the separation
date, so they are not counted as saved.

**A structural bound worth knowing.** Since both sides carry the same burden at base values,
`offset / overtime = own_base / (avg_base x 1.5)`. The 1.5x FLSA premium therefore caps the
ratio, and within one pay grade the widest step spread is `1.025^9 = 1.2489` — so the credit
can reach at most **83%** of the charge and can never overtake it at base assumptions. It
only overtakes once the two burdens diverge, for example the vacancy burden at its 1.35 high
bound while the overtime burden stays at base: `1.2489 x 1.35 / (1.5 x 1.0765) = 1.044`.
Golden case 10 sits exactly at the base-mode ceiling, and `tests/test_properties.py` pins
down both halves.

For every other role the seat simply sits empty:

```
vacancy productivity loss = vacancy work days x loaded daily rate x productivity_loss_factor
```

The factor defaults to **0.5** and is `TBD-MIKE` -- **open item 5**.

**Civilian vacancies get no salary offset, and that is only correct under one reading of this
factor.** It is defined as a **net** figure: the value of the work nobody did, over and above
the salary the county already stopped paying. Minimum-staffing posts need a separate credit
because they are charged *gross* overtime; a civilian post is charged only the shortfall, so
crediting the salary again would double-count it. The assumption's notes ask the owner to
confirm that reading — if the factor is meant as gross lost output, the engine needs a
civilian offset too and the value should come down.

Vacancy cost, charge and credit alike, is *not* washout-adjusted: covering a post is not a
recruiting cost.

### Recruiting and selection

Advertising, HR recruiter hours, written and physical testing, interview panel time
(`panel size x hours each x the supervisory loaded rate`), and pre-employment screening
itemized one line at a time: background investigation, polygraph, psychological evaluation,
medical exam, drug screen. Screening costs differ by an order of magnitude between a sworn
background investigation (a full personal-history investigation, ~$3,500) and a civilian
records check (~$300). Any element priced at zero for a profile produces no line item.

### Onboarding and training

Orientation hours, academy tuition, **the recruit's full loaded salary for the academy's
length**, field training (the trainee's salary plus the trainer's differential), the
productivity ramp for civilian roles, and equipment and uniform issue.

The replacement is costed at **step 1** of the role's pay grade, because that is what a new
hire actually earns -- not the removed employee's step.

Academy lengths are the one researched figure in C5: published state POST and academy program
lengths run roughly 12-26 weeks for basic law enforcement, with detention academies markedly
shorter. The values here (22 weeks sworn, 16 fire, 6 corrections, 3 dispatch) are mid-range
placeholders, not any one agency's program. Published analyses consistently note that
**tuition is a small share of total training cost and recruit salary dominates** -- which is
exactly what the model shows: for a deputy removal the academy salary is about $61,000 and
the tuition about $8,700.

### Attrition adjustment

Not every recruit finishes. Every recruiting and training item is divided by
`(1 - washout_rate)`:

```
adjusted = raw / (1 - washout_rate)
```

because filling one seat means hiring more than one person. The multiplier appears in each
affected line item's `formula` and `inputs`, so it is never buried in a total. Washout rates
run from 0.10 (standard civilian) to 0.40 (dispatch), all `TBD-MIKE` -- **open item 4**.

The **productivity ramp is not** washout-adjusted: only the person who stays ramps up.

---

## 8. Aggregation, suppression and use limitation

### Rollups

The default view is **employee type (role family) x misconduct category**, with department as
a toggle; action type, location, bargaining unit, year and sworn/civilian are also available
on either axis or as filters. Each cell carries action count, distinct employees, gross,
offset, net, cost per action, cost per 100 FTE, the C1-C5 component mix, and how many of its
records are still accruing cost.

Cost per 100 FTE uses **active headcount**, not the number of people disciplined. The
headline "per employee per year" figure divides net cost by the window length and by everyone
on the payroll -- it answers "what does this cost the organisation per head", not "what does
a disciplined employee cost".

The window length is the span between the earliest and latest decided action, in years. It is
not a count of calendar years: a three-year window starting in September touches four
calendar years, and counting those would understate the annual figure by a quarter.

### Suppression, in three layers

1. **Threshold.** A cell backed by fewer than `min_cell_size` distinct employees (default 5,
   **open item 6**) is not released. The API response carries no figure for it at all. Its
   value is pooled into an "Other (suppressed)" bucket so row, column and grand totals still
   reconcile exactly -- the money is never deleted, only hidden.
2. **Complementary suppression.** A single suppressed cell in a row is no protection:
   subtract the visible cells from the row total and there it is. So a row or column left
   with exactly one suppressed cell gives up its next-smallest cell too. Where the line has no
   other cell to give up -- a row with one, suppressed entry -- **the line's total is withheld
   instead**. The same applies matrix-wide: one suppressed cell in the whole view would *be*
   the "Other (suppressed)" bucket, so a second is always suppressed alongside it.
3. **Differencing guard.** Two overlapping queries can isolate a small group even when neither
   query suppresses anything: ask for 2024-2026, ask again for 2024-2025, subtract. A
   per-session **disclosure ledger** remembers the set of employee ids behind every value
   released so far and refuses to release a cohort whose difference from any earlier one is
   smaller than the threshold. Cohorts stay server-side and never appear in a response.

**The limits of layer 3, stated plainly.** The ledger is a pairwise residual check, not a
full query audit. It cannot see combinations of three or more earlier answers, and it is
per-ledger: a caller who starts a fresh session starts fresh. Full disclosure auditing is not
Phase 1 work and this demo should not claim otherwise.

### Roles

| Role | Sees |
|---|---|
| `executive` | Aggregates only, with suppression applied. No record drill-down. |
| `hr_analyst` | Aggregates, plus record-level drill-down with employee IDs pseudonymized. |
| `admin` | Everything, including real identifiers and scenario runs. |

Pseudonyms are a stable, non-reversible hash, so an analyst can follow one person through a
dataset without learning who they are. The role arrives in an `X-WRI-Role` header and is
enforced by the API -- an `executive` requesting a drill-down gets a 403 from the engine, not
a hidden button. There is no authentication behind the header yet; that is the seam where SSO
goes.

Every request is written to an append-only audit log before its response is built: role,
endpoint, filters, timestamp, and whether it was allowed.

---

## 9. Data quality

The adapter reports what it could not translate rather than raising. Records with **blocking**
issues are excluded from every total and the exclusion count is always shown -- they are never
silently dropped. **Warnings** mean the record looks odd but still costs correctly.

An employee-level problem, such as a missing salary, blocks every action belonging to that
person, and the root cause is reported once rather than as a cascade of orphan records.

Current rules: orphan action; unknown misconduct category or subtype; suspension days outside
the action type's range; a suspension with zero days; decision before incident; proposal after
decision; suspension starting before the decision; leave running past separation; a sworn-only
category on a non-sworn employee; orphan appeal; orphan separation; separation pointing at an
unknown action; a resolved appeal with no resolution date.

---

## 10. What this baseline does not do

Phase 1 is the cost baseline and nothing else.

- **No pattern detection, hotspot alerting or risk scoring.** That is Phase 2. If the data
  shows a hot cell, the matrix surfaces it as an expensive cell and a human draws the
  conclusion. The synthetic data contains one deliberately planted pattern, documented in
  `src/wri_engine/generator/README.md`, precisely so nobody mistakes it for a discovery.
- **No root-cause or intervention cost-benefit modelling.** That is Phase 3.
- **No probability weighting.** Nothing is counted because it was likely.
- **No claim that any figure is a measured agency cost.** 151 of the 166 assumptions are
  still owner placeholders. The Assumptions page tints every one of them.

---

## 11. Current results

From `data/synthetic/` at seed 20260917, base mode, as of 2026-09-17:

| | |
|---|---:|
| Actions costed | 508 |
| Actions excluded for data quality | 13 |
| Actions still accruing cost | 13 |
| Window | 2.91 years |
| Gross cost | $5,596,426 |
| Unpaid suspension offset (C3-offset) | -$486,772 |
| Vacancy salary offset (C5-offset) | -$466,671 |
| **Total offsets** | **-$953,442** |
| **Net cost** | **$4,642,984** |
| **Net cost per year** | **$1,595,527** |
| Active headcount | 2,515 |
| **Net cost per employee per year** | **$634** |
| Share of gross from turnover (C5) | 44% |

| Component | Total | Share of gross |
|---|---:|---:|
| C1 Processing labor | $825,309 | 15% |
| C2 Paid administrative leave | $499,631 | 9% |
| C3 Backfill overtime | $1,082,132 | 19% |
| C4 Appeals and grievances | $704,532 | 13% |
| C5 Removal turnover | $2,484,822 | 44% |
| C3-offset Unpaid suspension savings | -$486,772 | |
| C5-offset Vacancy salary savings | -$466,671 | |

Within C5, the vacancy period charges **$705,525** of overtime across 17 minimum-staffing
vacancies and credits back **$466,671** of salary, a net vacancy cost of **$238,855**.
Charging the overtime without the credit would overstate the vacancy by a factor of three.

These figures move with the assumptions. That is the point: change a coefficient on the
Assumptions page and the whole model recomputes from the line items up.
