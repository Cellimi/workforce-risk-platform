# Synthetic data generator — distributions and rationale

Everything this generator produces is **synthetic**. Harlow County does not exist. The
distributions below are built from publicly observable patterns in local-government
discipline and staffing, and from documented professional judgement. **No parameter here is
derived from any real agency's data, and no real county, agency, union or person is
represented.**

Every parameter lives in `config/generator_profile.yaml`. This file explains what each one
means and why it has the value it has. Change the YAML, not the code.

Run it:

```bash
python -m wri_engine.generator.cli --seed 20260917 --out data/synthetic
```

The generator is **deterministic**: one seeded `random.Random` drives every draw, so the
same seed plus the same config always produces byte-identical CSVs. The seed and the SHA-256
digests of all three config files are recorded in `manifest.json` beside the output.

## What it writes

Five CSVs in the **county HR system's own format** — its column names (`EMP_NBR`,
`ACTN_CD`), its code values (`SP2`, `IAD`, `RMV`), its `MM/DD/YYYY` dates and its
comma-separated salary strings. It deliberately does *not* write the canonical schema, so
loading the demo exercises `adapters/county_hr_csv.py` the same way a real customer load
would.

| File | Rows (seed 20260917) | Contents |
|---|---|---|
| `hr_empl_master.csv` | 3,073 | Employee master, including historical separations |
| `hr_disc_actn.csv` | 523 | Discipline actions over the 3-year window |
| `hr_admn_leave.csv` | 59 | Paid administrative leave periods |
| `hr_appl_grv.csv` | 45 | Appeals and grievances |
| `hr_sepn.csv` | 29 | Separations linked to a discipline action |

`data/synthetic/sample/` holds a trimmed, committed slice (312 employees, 228 actions) so
the project runs without regenerating.

## Parameters

### `horizon`
| Parameter | Value | Why |
|---|---|---|
| `years` | 3 | Long enough to show year-over-year movement, short enough that pay steps and the pay plan stay coherent. |
| `as_of_date` | 2026-09-17 | The extract date. Everything is generated backwards from here, so "open appeal window" logic has a fixed reference point. |
| `target_action_count` | 520 | About 6.8 actions per 100 FTE per year across 2,500 employees. Tuned so the matrix has enough cells to read but not so many that every cell is noise. |

### `workforce`
| Parameter | Value | Why |
|---|---|---|
| `annual_voluntary_attrition` | 0.07 | Local-government turnover excluding discipline. Produces ~550 historical separation rows, which is what a real 3-year extract looks like. These do **not** generate turnover cost — only discipline-linked separations do. |
| `tenure_years` | triangular(0.2, 5.0, 28.0) | Right-skewed: many mid-tenure employees, a long tail of 20+ year staff, few brand-new hires. |
| `years_per_step` | 2.5 | Drives the pay step from tenure, capped at step 10, so salary correlates with tenure instead of being drawn independently. |

### `action_rate_factor_by_role`
A role family's share of actions is its **headcount share × this factor × the share of the
window the employee was employed**.

Public-safety line roles run highest (deputies 1.5, corrections officers 1.9,
telecommunicators 1.3). Three things push the documented-action rate up in those roles:
continuous public and inmate contact, minimum-staffing rules that turn an absence into an
immediate operational problem, and formal progressive-discipline systems that put conduct on
paper rather than handling it informally. Supervisory and professional roles run lowest
(0.2–0.7): fewer of them, and their conduct issues are more often handled outside the formal
system.

**This is a modeling choice, not a finding about any real workforce.**

### `embedded_pattern` — the deliberate hot cell

| Parameter | Value |
|---|---|
| `role_family` / `work_location` | `corrections_officer` at `Main Detention Center` |
| `action_rate_multiplier` | 1.8 |
| `category_weight_multipliers` | attendance ×3.2, tardiness ×1.8 |
| `severity_shift` | 0.15 (that share of the cell's actions move one step up the action ladder) |

One location-and-role combination carries elevated attendance violations, so the cost
baseline surfaces **Corrections Officer × Attendance × Main Detention Center** as the most
expensive cell on its own, from cost alone.

**Phase 1 must never label this an alert, a hotspot, or a risk.** Detection is Phase 2. The
matrix shows an expensive cell; a human draws the conclusion. This paragraph exists so that
nobody mistakes a planted pattern for a discovery.

### `misconduct_weights` and `category_severity_tier`
Attendance (26) and tardiness (16) dominate, as they do in practically every published
local-government discipline summary. Use-of-force (1) is the rarest and is restricted to
sworn employees — for non-sworn employees the sworn-only categories are dropped and the
remaining weights renormalized.

Each category sits in a severity tier (`minor` / `moderate` / `serious`) and the tier, not
the category, decides the action-type mix. That keeps the taxonomy extensible: a new
category needs a tier, not a new distribution.

### `action_type_weights`
Blended against the category mix, these produce **removals at ~5.5% of all actions**, inside
the 5–8% target. Counseling and written reprimands together are ~60% of actions, which is
what a healthy progressive-discipline system looks like.

### `timing`
All triangular(min, mode, max) in calendar days. Incident → proposal has a long right tail
(mode 21 days, max 120) because investigations stall. Arbitration resolution runs
90/210/480 days; court runs 150/400/900. Those long tails are what put recent cases inside
an open appeal window and make the `cost_incomplete` flag meaningful.

### `investigation`
Probability by tier: minor 0.15, moderate 0.45, serious 0.90. Sworn employees draw internal
affairs 50% of the time when investigated; civilians 5%. Internal affairs is the single most
expensive investigation type in C1, so this split is a real cost driver.

### `admin_leave`
Probability depends on action type **and** whether the employee is sworn, times 1.3 for
serious categories, capped at 0.95. A sworn removal is on paid leave 75% of the time; a
civilian removal 35%. Sworn employees are relieved of duty pending outcome far more often —
and because those roles are minimum-staffing, the county then pays twice: the leave (C2) and
the overtime covering the shift (C3).

Leave periods **end on the decision date** and run backwards, which is how relief-of-duty
pending an outcome actually behaves. All leave in this dataset is paid.

### `appeals`
Appeal probability: 12% (1–3 day suspension) rising to 60% (removal); suspensions overall
land near 18%, inside the 15–25% target. Removals go to arbitration or a civil service board
70% of the time; lesser actions stop at a grievance step 55% of the time.

> **Do not judge the removal appeal rate from one seed.** There are only about 30 removals in
> a seed, so the standard deviation on that rate is roughly 9 percentage points. Pooled over
> five fixed seeds (163 removals, filing windows closed) the realized rate is **55.8%**
> against the configured 60% — within noise. But the demo seed **20260917 on its own sits at
> 36%**, about 2.6 sigma low, which looks like a bug and is not one: the draw was audited
> (31 draws, 12 yes) and every other path ruled out. The filing-date cutoff accounts for at
> most one removal, and pruning removed two matched action/appeal pairs, which leaves the
> ratio unchanged.
>
> The consequence is real even though the code is correct: **C4 is genuinely understated on
> seed 20260917**, by roughly seven removal appeals. If the demo needs a representative C4,
> pick a seed whose removal rate is nearer the parameter — that is a presentation choice, not
> a code fix. `tests/test_generator.py::test_appeal_rates_pooled_across_seeds` is the guard
> that would catch an actual regression.

Outcomes: sustained 50, mitigated 20, settled 20, overturned 10. Back pay is a share of the
pay actually lost (mitigated 45%, overturned 100%, settled 35%), capped at two years.

**An overturned removal produces no separation record**: the employee is reinstated, so no
replacement is hired and no C5 turnover cost applies. A mitigated removal is treated as a
partial back-pay award with the separation standing — a simplification, since in practice
mitigation often converts a removal into a suspension.

### `open_window`
Appeals on actions decided within the last 270 days are left `pending` 80% of the time. Plus
any action still inside its forum's filing window (set in `assumptions.yaml`) is flagged by
the engine. Together these put roughly 5% of all actions into the `cost_incomplete` bucket,
which is what the flag logic exists to handle.

### `separations`
18% of removals are recorded as a resignation in lieu of removal. Positions are refilled 85%
of the time, still pending 8%, abolished 7%. Days to refill is profile-specific: a deputy
seat takes a mode of 165 days to fill and reach solo duty; a standard civilian seat 65.
**An abolished position generates no turnover cost**, which is exactly the case the golden
tests pin down.

### `data_quality_injection`
Thirteen deliberately malformed rows, so the adapter's validation rules have something to
catch and the Data Quality page has content:

| Issue | Count | What it exercises |
|---|---|---|
| `orphan_action` | 3 | Action referencing an employee id that is not in the master |
| `suspension_days_mismatch` | 2 | 22 days recorded against a "1–3 day" action code |
| `decision_before_incident` | 2 | Dates swapped |
| `missing_salary` | 2 | Blank `ANNL_SAL`, so no rate can be computed |
| `leave_after_separation` | 2 | Admin leave beginning after the employee left |
| `sworn_only_category_misapplied` | 2 | Use-of-force recorded against a non-sworn employee |

Set every count to `0` for a clean export. Records with **blocking** issues are excluded
from cost totals and the exclusion count is shown in the UI — they are never silently
dropped.

## Known simplifications

- Pay is drawn from the step/grade table only. No longevity pay, shift differential,
  specialty pay, or out-of-class assignment.
- Everyone in a role family shares one schedule. Real agencies have people on modified duty,
  light duty and non-standard rotations.
- The generator has no concept of a repeat offender beyond the fact that an employee can be
  drawn more than once. Progressive discipline is not modeled as a chain.
- Mitigated removals do not convert to suspensions (see above).
