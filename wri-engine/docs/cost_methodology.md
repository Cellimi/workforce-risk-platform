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
never be quietly used to shrink a cost. There are three offsets: `C3-offset` for an unpaid
suspension, `C5-offset` for the salary of a vacant post after a removal, and `C7-offset` for
what an insurer paid of a private employer's settlement. All three are defined once, in
`OFFSET_COMPONENTS`, and everything that needs to know "is this a saving?" asks that set.

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
| `benefits_multiplier_sworn` | **1.75** | confirmed, but low confidence and unsourced |
| `private_benefits_multiplier_all` | **1.4287** | researched; owner reviewed 2026-11-13 |

**Which family of multipliers applies is the sector's decision.** The two public
multipliers belong to the public-sector pack; a private engagement loads each wage with the
private-industry figure for that person's occupational group, or the all-occupations figure
shown here where the file does not give one. Section 8 covers them; the rest of this section
describes the public-sector model.

The civilian multiplier is the ratio of total compensation to wages for state and local
government workers in the BLS **Employer Costs for Employee Compensation** release for March
2026 (USDL-26-0827, released 2026-06-12), Table 1 "By ownership": total compensation
$66.41 per hour worked against wages and salaries of $40.82 per hour worked, giving
66.41 / 40.82 = **1.627**.

**A note on status before the caveats.** As of 2026-11-12 the owner has signed off on every
assumption in the registry, so none is marked `TBD-MIKE`. Sign-off settles *who stands
behind a number*; it does not change *where the number came from*. The `source` and
`confidence` fields still carry that, and the caveats below are unaffected by the sign-off.

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
   bound deliberately set to the all-occupations figure. The owner accepted 1.75 on
   2026-11-12 as the working value and chose not to block the project on sourcing it, so the
   engine runs on it — but it remains an estimate rather than a cited figure, its confidence
   stays **low**, and it scales every public-safety number in the model.

   Because its low bound is the all-occupations multiplier, running the model in **low mode**
   shows what the figures look like if the public-safety premium turns out to be nil. That is
   the honest way to present a number nobody has verified: show the range, not just the
   point.

### Rates for people who are not in the extract

HR and labor relations staff are never in an HR discipline extract, and senior deciding
officials are usually not named on the action. Their hours are costed at a reference annual
salary from the registry, converted at `c1_reference_annual_hours` (2,080) and loaded with
**the sector pack's own default multiplier** -- the civilian one for a county, the private
all-occupations one
for a company. When the record *does* name the person -- a supervisor, or a
deciding official who is an employee in the dataset -- their own loaded rate is used instead,
and the line item's formula says which happened.

---

## 3. C1 -- Processing labor

Every action consumes staff time before it produces any other cost.

```
line item = hours x that person's loaded hourly rate
```

Hours by action type (`c1_hours_<action>_<actor>`, owner-approved professional estimates):

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
(5%) are all owner estimates carrying low confidence -- **open item 2**.

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
Window lengths are owner estimates; in practice they come from the collective bargaining agreement
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

The factor defaults to **0.5**, at low confidence -- **open item 5**.

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
run from 0.10 (standard civilian) to 0.40 (dispatch), all owner estimates -- **open item 4**.

The **productivity ramp is not** washout-adjusted: only the person who stays ramps up.

---

## 8. The private-sector cost model

Everything above describes a county. A licensee's next client is a company, and the arithmetic
does not change: a component looks at a record and, if the record shows the event happened,
produces a line item. Three things do change, and a **sector pack** holds exactly those three
(`src/wri_engine/sectors/`):

| A pack decides | Public sector | Private sector |
|---|---|---|
| Which components can run | C1-C5 | C1-C5, plus **C6** and **C7** |
| Which benefits multiplier loads a wage | `benefits_multiplier_sworn` for sworn staff, `benefits_multiplier_civilian` otherwise | One per broad occupational group, `private_benefits_multiplier_<group>`; `private_benefits_multiplier_all` where the group is not given |
| Which coefficient ids must exist before the engine starts | 6 turnover profiles, 4 challenge forums | 2 turnover profiles, 6 challenge forums |

A pack holds no arithmetic and no numbers: a coefficient still lives in
`config/assumptions.yaml` and is still fetched by id (rule 1). An engagement's sector comes
from its record in the database and never from anything the caller sends, so a client cannot
have their actions priced under another sector's model.

**The two packs deliberately share one coefficient namespace.** Where the sectors genuinely do
the same thing -- an HR officer's hours on a written reprimand -- they share the id and the
value, so there is one number for the owner to confirm instead of two that can drift apart.
Only a genuine difference earns a new id, and every id below is a genuine difference.

**No component branches on the sector.** C6 and C7 emit a line only when the record carries
the field they need -- a severance amount, an insurer payment -- and public-sector data
carries none of them, so the new components are inert on public data. That is rule 9
(actuals over estimates) doing double duty: it is also what makes the public figures in
section 12 provably unchanged by this work, and a property test asserts it line item by line
item.

### Benefits loading

```
loaded_hourly_rate = hourly_base_rate x benefits_multiplier[benefits_group]
```

A private employer's benefits cost per wage dollar depends on the kind of work, so the
private pack carries **one multiplier per broad occupational group** and the all-occupations
figure as the fallback. Each is total compensation divided by wages and salaries, per hour
worked, for private industry workers in the BLS **Employer Costs for Employee Compensation**
series for 2026 Q2 (the June 2026 reference period), read from `data.bls.gov` on 2026-11-13:

| `occupation_group` on the employee | Total comp. | Wages | Multiplier | BLS series (total / wages) | Status |
|---|---:|---:|---:|---|---|
| `management_professional` | $78.88 | $54.06 | **1.4591** | `CMU2010000100000D` / `CMU2020000100000D` | researched, awaiting review |
| `sales_office` | $36.34 | $26.00 | **1.3977** | `CMU2010000200000D` / `CMU2020000200000D` | researched, awaiting review |
| `service` | $23.73 | $18.26 | **1.2996** | `CMU2010000300000D` / `CMU2020000300000D` | researched, awaiting review |
| `construction_maintenance` | $47.80 | $32.88 | **1.4538** | `CMU2010000400000D` / `CMU2020000400000D` | researched, awaiting review |
| `production_transportation` | $36.70 | $24.99 | **1.4686** | `CMU2010000500000D` / `CMU2020000500000D` | researched, awaiting review |
| *(blank)* -- all occupations | $46.89 | $32.82 | **1.4287** | `CMU2010000000000D` / `CMU2020000000000D` | researched; owner reviewed 2026-11-13 |

The assumption ids are `private_benefits_multiplier_<group>` and
`private_benefits_multiplier_all`. Benefits are 30.0% of employer costs in private industry
against 38.5% in state and local government, which is why even the heaviest private group
sits well below the 1.627 used for county civilians.

**The spread is the reason the groups exist.** Service work is loaded at 1.30 and production
work at 1.47. Loading everyone at the 1.4287 average overstates every hour of a service
worker's cost by about a tenth and understates a production worker's by about 3%. Golden
case 14 shows it on one action: three shifts of paid leave for a line cook cost $561.43 at
the service multiplier and would have been booked at $617.20 at the average.

Things a reader should know:

1. *The group comes from the client's file.* `occupation_group` is an optional column on the
   employee table. Where it is blank the wage is loaded at the all-occupations figure, which
   is the published answer to "occupation unknown", and the load report says how many
   employees that was (`defaulted_occupation_group`) rather than leaving the reader to assume
   none. A public engagement ignores the column.
2. *Each group's bounds are computed, not judged*: the envelope of the group's own ratio over
   the 20 quarters to 2026 Q2 and of its published sub-groups in 2026 Q2. Sales and office is
   the wide one (1.3283 to 1.4564), because sales (1.3283) and clerical work (1.4564) are
   loaded very differently and BLS reports them as one group.
3. *The fallback's own bounds are wrong and are waiting on the owner.* The 1.38 / 1.4835
   bounds on `private_benefits_multiplier_all` were set before the occupational series could
   be read. The high bound rested on a wages figure for the management group that the BLS
   series does not bear out (that group is 1.4591, not 1.4835), and the low bound does not
   reach service at 1.2996. If the bounds are meant to contain the occupational spread they
   should be 1.2996 / 1.4686. They are left as approved, because a bound is not changed
   without asking; the base value 1.4287 was verified against the series and is unaffected.
4. *The hour-worked versus hour-paid caveat in section 2 applies here too*, in the same
   direction and for the same reason.
5. *BLS's news-release tables refuse automated readers*, so the figures were read from the
   BLS data series behind them. The series ids above are the citation: anyone can open
   `https://data.bls.gov/timeseries/<series id>` and see the same numbers.

The reference rates in section 2 -- an HR officer, a senior deciding official, nobody who
appears in a discipline extract -- are loaded with **the pack's own default multiplier**, the
all-occupations figure, not a fixed civilian one. A company's HR officer does not carry a
state pension, and charging one at a government multiplier would have overstated every action
in a private engagement. The all-occupations figure is used because nobody knows what kind of
work a person who is not in the file does.

### C1 -- the outside investigator

A county investigates with internal affairs or a supervisor. A company facing a harassment
complaint often retains a law firm or a specialist investigator, and that invoice is the
largest single processing cost on the action.

```
outside_investigation = outside_investigator_hours x c1_outside_investigator_hourly_rate
```

The hours come from the record. Nothing is counted where the record shows none, so an
in-house investigation costs what it did before.

### C3 -- how the post is covered, and an unpaid suspension that may not be lawful

A county backfills a minimum-staffing post with a peer on overtime. A company may do that,
hire an agency temp, or leave the post empty. The employee record carries
`coverage_method` -- `overtime`, `temp_agency` or `none` -- and the same routing prices both
C3 (the absence) and C5's vacancy period (the empty post), so one empty post is never priced
two different ways:

| `coverage_method` | Rate used |
|---|---|
| `overtime` | base rate x `c3_ot_premium_multiplier` x `c3_ot_burden_multiplier`, as in section 5 |
| `temp_agency` | the peer's base rate x `c3_temp_agency_markup`; no FLSA premium, because the temp is not working overtime |
| `none` | nothing: the post stands empty and no coverage cost is charged |

Blank means `overtime`, which is what every public-sector record implies, so nothing moves on
existing data.

**The salary-basis rule.** Under the Fair Labor Standards Act regulations, 29 CFR
541.602(b)(5), an employer may dock a salaried exempt employee's pay for a disciplinary
suspension **only in full-day increments and only under a written policy applicable to all
employees**. Where an action records an unpaid suspension of an exempt employee and
`written_conduct_policy` is N, the engine emits a **zero-valued `C3-offset` line** citing the
regulation instead of crediting the wage back. The line is there so the reader sees the
credit was considered and withheld, and why -- a missing credit with no line would look like
an omission. Blank means Y: a deduction recorded as made is treated as properly made, because
assuming otherwise would withhold a credit the employer did take.

### C4 -- three more ways a private action is challenged

A private employer without a union contract has no grievance ladder and no civil service
board. The pack adds three forums, each with its own HR hours and filing window:

| Forum | `c4_hr_hours_*` | `c4_filing_window_days_*` |
|---|---:|---:|
| `agency_charge` | 25 | **300** (researched) |
| `demand_letter` | 6 | 90 |
| `mediation` | 14 | 60 |

The agency-charge window is the one figure here fixed by statute rather than judgement. Title
VII, 42 U.S.C. 2000e-5(e)(1), gives 180 days to file a charge, extended to 300 where the
person first instituted proceedings with a state or local agency with authority to grant
relief. 300 is used as the base and 180 as the low bound, so **low mode shows the figures for
a client in a state with no such agency.** The window matters because, as in section 6, an
action inside its window is reported as still accruing cost rather than as complete.

Arbitration and court stay in the private pack: a unionised private employer grieves and
arbitrates exactly as a county does, and either sector can end up in court.

### C6 -- separation agreements and unemployment

Two costs that follow a private removal out of the door and are almost never on the
discipline ledger.

| Line | Formula |
|---|---|
| `severance_paid` | the amount on the record |
| `agreement_hr_hours` | `c6_separation_agreement_hr_hours` (8) x HR loaded rate |
| `agreement_counsel` | `c6_separation_agreement_counsel_hours` (4) x `c4_outside_counsel_hourly_rate` |
| `unemployment_benefits_charged` | benefits charged x `c6_unemployment_experience_rating_multiplier` |

Severance and benefits charged are **read, never estimated**. An agreement recorded with no
amount is not reconstructed from weeks of service: it is reported as a cost that cannot be
counted, so the total is visibly a floor rather than quietly wrong (rule 13).

The experience-rating multiplier is a separate line of the formula precisely so it can be set
to **1.0 and disappear**, which is its base value. The mechanism is well documented -- FUTA is
6.0% on the first $7,000 of wages with a credit of up to 5.4% for state unemployment tax paid,
and the state tax is experience rated, so benefits charged to an employer's account raise its
later rate. The *size* of that effect for any one employer is not documented, so the base
counts the dollars charged and nothing more, and the high bound of 2.0 shows what a fully
experience-rated employer would see.

C6 runs only where the separation reason is discipline-driven. A resignation for a better job
is not a cost of discipline.

### C7 -- employment practices liability, and what the insurer paid

A private employer facing a discrimination charge usually has an employment practices
liability policy behind it. That changes **who pays**, not whether the money was spent, and a
model reporting only the employer's share would understate what the discipline cost.

So `C7-offset` credits back whatever the insurer paid: **gross shows the full exposure, net
shows the employer's own burden** -- the same shape as C3 and C5. Both figures are read from
the record; neither is estimated. The premium that bought the cover is **not** counted: it is
paid whether or not anyone is disciplined, so it is not a cost of this action (rule 9).

**The retention trap.** The employer's retention and the insurer's payment are two slices of
one payout, and the employer's slice is easy to count twice. If C4 has already booked a
$45,000 settlement and the insurer paid $30,000 of it, the employer's $15,000 is *already*
in the number -- it is precisely the part the offset does not credit back. Booking the
retention again as its own line would overstate the matter by exactly the retention. So the
retention is booked **only when nothing else captured the payout**: a retention recorded with
no settlement and no back pay, which is what an accounts payable export looks like when all
it knows is "we paid our deductible". Where both appear, a validation warning
(`retention_recorded_with_payout`) names the records rather than the engine guessing which
way the policy runs.

This was caught by hand-checking golden case 13, where it had inflated gross by $45,000.
It is the reason that case exists.

### What the private model does not do

- **No pattern detection and no probability weighting.** The phase boundary in `CLAUDE.md`
  applies to both sectors.
- **No estimated severance, no estimated unemployment charge, no estimated insurer payment.**
  Where the record is silent the cost is reported as uncounted, never filled in.
- **No finer occupational detail than the five broad groups**, and none by industry.
  BLS publishes both; the model stops at the level a client's job families can be
  mapped to without argument.
- **No state-by-state variation.** The agency-charge window is modelled through its low bound
  rather than by asking which state the client is in. Unemployment tax rates, which genuinely
  are per state, are not modelled at all: the engine counts benefits charged.

### The status of every new value

Seventeen coefficients were added for this sector and **no existing value was changed**.
The owner reviewed the first twelve on **2026-11-13**, so none is a placeholder. The five
occupational-group multipliers were researched later the same day and are **awaiting his
review**; they are listed in `RESEARCHED_AWAITING_OWNER_REVIEW` in
`tests/test_config_consistency.py`. The table below is the twelve he has reviewed:

| | Count | Which |
|---|---:|---|
| **researched** (a cited government or statutory source fixes the number) | 2 | `private_benefits_multiplier_all`, `c4_filing_window_days_agency_charge` |
| **confirmed** (the owner stands behind the figure; no authoritative source exists) | 10 | the three C4 forum hour figures, the two non-statutory filing windows, `c3_temp_agency_markup`, `c1_outside_investigator_hourly_rate`, the two C6 agreement-hour figures, `c6_unemployment_experience_rating_multiplier` |

**Read that table the way section 2 says to read `status`.** The two researched entries stay
researched: naming a source is a stronger claim than a sign-off, and an approval must not
flatten it away. The ten confirmed entries are owner estimates the owner stands behind --
approval settles *who* stands behind a number, not *where it came from*. Their `confidence`
levels did not move (five low, five medium), each still records that it is not a researched
figure, and each still names what would settle it, which for most of them is the client's own
invoices or matter records.

`c3_temp_agency_markup` is the one to watch. A staffing markup is quotable -- the American
Staffing Association's own worked example marks a $17.00 pay rate up 51.5% to a $25.76 bill
rate, and 25% to 75% is the commonly quoted range -- but a trade association and a vendor
price sheet are not an official statistic. It therefore carries its citation and is
`confirmed`, not `researched`: calling a vendor page research would devalue the status for
the figures that earned it. Watch the basis too, because a 50% markup on pay is a 33.3%
margin on the bill rate and quotes use both.

The review checklist is `AWAITING_OWNER_REVIEW` in `tests/test_config_consistency.py`. It is
now empty, and deliberately kept rather than deleted: an empty checklist is what makes the
next unreviewed value fail the build. Every entry carries its full source text in
`config/assumptions.yaml`, which is where to read one rather than this summary.

---

## 9. Aggregation, suppression and use limitation

### Rollups

The default view is **employee type (role family) x misconduct category**, with department as
a toggle; action type, location, bargaining unit, year and sworn/civilian are also available
on either axis or as filters. Each cell carries action count, distinct employees, gross,
offset, net, cost per action, cost per 100 FTE, the component mix (C1-C5, and C6 and C7 in
a private engagement), and how many of its records are still accruing cost.

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

## 10. Data quality

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

## 11. What this baseline does not do

Phase 1 is the cost baseline and nothing else.

- **No pattern detection, hotspot alerting or risk scoring.** That is Phase 2. If the data
  shows a hot cell, the matrix surfaces it as an expensive cell and a human draws the
  conclusion. The synthetic data contains one deliberately planted pattern, documented in
  `src/wri_engine/generator/README.md`, precisely so nobody mistakes it for a discovery.
- **No root-cause or intervention cost-benefit modelling.** That is Phase 3.
- **No probability weighting.** Nothing is counted because it was likely.
- **No claim that any figure is a measured agency cost.** The registry holds 184
  assumptions. The owner has approved 179 of them — the 167 the public-sector model runs on,
  on 2026-11-12, and the first 12 the private sector added, on 2026-11-13 — and the 5
  occupational-group multipliers are researched and awaiting his review. Approval clears the
  engine to run; it does not make the numbers measurements. Only 20 carry a cited source, and
  91 of the 184 remain low confidence. The Assumptions page tints the
  low-confidence rows and shows the source beside each value.

---

## 12. Current results

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
