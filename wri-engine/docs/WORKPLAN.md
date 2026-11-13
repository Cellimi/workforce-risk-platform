# Work plan (engineering)

How work on the engine is split and sequenced. This file is the shared ground between the
Claude sessions that work on this repository: they cannot see each other's conversations, and
a Claude Code session cannot read the owner's Claude Project documents. Read this before
starting, and update it in the same pull request as the work it describes.

Business decisions (pricing, ownership, legal structure) are kept outside the repository. Do
not add them here: the repository is public.

Last updated 2026-11-13.

## Working arrangement

Two sessions work on the engine: a chat session and a Claude Code session.

1. **Branches.** Pull requests are stacked and not yet merged:
   `main` <- #2 `claude/assumptions-signoff` <- #3 `claude/login-engagements`
   <- #4 `claude/intake-template` <- #5 `claude/licensed-screen`.
   Start new work on a new branch from the top of the stack (now `claude/licensed-screen`),
   and open its pull request **against that branch**, extending the stack. Once the stack is
   merged, branch from and target `main`.
2. **Retired branch.** `claude/wri-phase1-discipline-cost-n73knl` is retired. Everything on it,
   through `86d7fd0`, is inside `claude/licensed-screen`. Do not push to it.
3. **Claims.** Only one session works on a build item at a time. Claim it in the table below,
   in your first commit, before writing code.
4. **Conventions.** `CLAUDE.md` holds the hard rules (no coefficient in Python, a golden test
   for every cost component, the UI never imports the engine, money is `Decimal`, and so on).
   Run `make test` and `make lint` before every push. Commit messages end with the attribution
   lines the session's own instructions give.
5. **Ask the owner** before anything that cannot be undone, anything that changes a published
   figure's meaning, and any new or changed assumption value. Research values with citations
   and mark them `researched`, never `confirmed`, until the owner has reviewed them.

| Item | Claimed by | Status |
|---|---|---|
| Screen for the licensed product | Chat session | Done, PR #5 |
| Private-sector cost model | Claude Code session | Done, PR against `claude/licensed-screen`, `claude/private-sector-cost-model` |
| Engagement license keys | (unclaimed) | |

## Where things stand

Reference figures on the synthetic Harlow County dataset (508 costed actions), approved
assumption values: net $4.64M at base, $2.99M with every assumption at its low bound, $8.73M
at the high bound. **The private-sector work did not move any of them**, and a property test
asserts it line item by line item. 311 tests, 95.6% coverage.

1. **Hardening (PR #3).** Sign-in with local accounts, roles from engagement membership, a
   database, isolated engagement workspaces, per-engagement assumption overrides recorded with
   who and why. Remaining: single sign-on, an audit log the application cannot edit, disclosure
   ledgers that survive a restart.
2. **Data intake (PR #4).** Standard intake template; a mapping saved per engagement so a
   client's file is never edited; staged upload, suggested mapping, preview, commit.
   Loading rules: a missing detail takes the lighter assumption and is reported; a cost that
   cannot be counted is reported with the cases (see `CLAUDE.md` rule 13).
3. **Screen (PR #5).** Sign-in, engagement picker, Engagement page, Load data page,
   client-specific values, results labelled with the client's name.
4. **Private-sector cost model.** Built. See below.
5. **Engagement license keys.** A signed key per engagement, verified offline by the installed
   product, with an exportable usage log.
6. **Client deliverables.** A report carrying the consulting firm's name and logo; PDF and
   spreadsheet export; an appendix of assumptions and overrides.
7. **Install package.** Container install, configuration guide, upgrade path.
8. **Phases 2 and 3** (hotspot detection; intervention cost-benefit). Out of scope until
   Phase 1 is in use: see "Phase boundary" in `CLAUDE.md`.

## Private-sector cost model

Built. A second sector pack beside the public-sector one, on the same principle: count a cost
only when the record shows the event happened. `docs/cost_methodology.md` section 8 is the
methodology; this is what was done and what was deliberately left.

| Component | Public sector | Private sector |
|---|---|---|
| Processing labour | Supervisor, HR, deciding official, investigator hours | Same, plus an **outside investigator** priced from `outside_investigator_hours` on the record |
| Paid leave during investigation | Paid administrative leave | **No change needed.** C2 costs the leave periods the record carries, whatever they are called and however short |
| Coverage for absence | Backfill overtime at minimum-staffing posts | `coverage_method` on the employee routes **overtime, temp agency or none**, and prices C3 and C5's vacancy period the same way, so one empty post is never priced twice over |
| Unpaid suspension offset | Wage not paid, shown as a negative | Same, plus the **FLSA salary-basis rule**: for an exempt employee with no written policy the credit is withheld and a zero-valued `C3-offset` line cites 29 CFR 541.602(b)(5) |
| Challenges to the action | Grievance steps, arbitration, civil service board, court | Grievance and arbitration stay; **agency charge, demand letter and mediation** added, each with its own HR hours and filing window |
| Insurance | Not applicable | **C7**: the retention the employer bore, and `C7-offset` crediting what the insurer paid, so gross is the exposure and net the employer's burden |
| Separation cost | Not modelled | **C6**: severance paid, the HR and counsel hours behind the agreement, and unemployment benefits charged |
| Turnover | Recruiting, screening, academy, field training, equipment, washout | The same C5, on the two civilian profiles, whose academy and polygraph coefficients are already 0. Agency fees arrive through `coverage_method`, not a separate profile |
| Benefits loading | State and local government multipliers | One private-industry multiplier **per broad occupational group**, from the employee's `occupation_group`, with the all-occupations figure as the fallback |

### How it is put together

- **Sector packs** live in `src/wri_engine/sectors/`. A pack names the components, the
  benefits multiplier per benefits group, the turnover profiles and challenge forums whose
  coefficients must exist, and the profile intake falls back to. It holds no arithmetic and
  no numbers.
- **No component branches on the sector.** C6 and C7 emit a line only when the record carries
  the field they need, and public-sector data carries none, so the new components are inert
  on public data. That is what makes the unchanged public figures provable rather than
  asserted.
- **One coefficient namespace, deliberately.** Where the sectors do the same thing they share
  the id and the value, so the owner confirms one number rather than two that drift. Only a
  genuine difference earned a new id, and there are seventeen.
- **The sector reaches the engine from the engagement record.** `EngagementRecord.sector` ->
  `EngineState.sector` -> `run_costing(sector=...)` and the template adapter, never from
  anything a caller sends. `SECTORS` in `src/wri_engine/store/db.py` is now derived from the
  installed packs, and `GET /sectors` serves the list the screen offers, so the server cannot
  refuse a sector the engine can cost or accept one it cannot.
- **Intake carries the new data.** `coverage_method` and `occupation_group` on employees;
  `outside_investigator_hours` and `written_conduct_policy` on actions;
  `insurance_retention_paid` and `insurer_paid_amount` on appeals; `separation_agreement`,
  `severance_amount`, `unemployment_claim_filed` and `unemployment_benefits_charged` on
  separations. All optional, all with synonyms for mapping suggestion, so an existing
  public-sector file loads unchanged.
- **Four golden cases** (11 to 14) hand-check the private pack: a removal with agency
  cover, severance and an unemployment claim; an exempt suspension with no written policy;
  an insured agency charge; and a service worker loaded at the service multiplier beside a
  manager loaded at the management one. Case 13 caught a $45,000 double count of the
  retention.

### Open items

1. ~~**Twelve new coefficients, none reviewed.**~~ **Done — the owner approved all twelve on
   2026-11-13.** The two with a statutory or government source stay `researched`:
   `private_benefits_multiplier_all` (BLS ECEC, private industry, all workers) and
   `c4_filing_window_days_agency_charge` (Title VII, 300 days with 180 as the low bound). The
   other ten moved from `TBD-MIKE` to `confirmed`, keeping their citations, their confidence
   levels and a note of what would settle each one. No value or bound changed, and
   `AWAITING_OWNER_REVIEW` in `tests/test_config_consistency.py` is now empty — kept rather
   than deleted, so the next unreviewed value fails the build.
2. ~~**Benefits loading is not per occupation group.**~~ **Built and approved by the owner
   on 2026-11-13.** The private pack now carries a multiplier for each
   of the five broad BLS occupational groups. **The group is a selection on the engagement**
   (`EngagementRecord.occupation_group`): the user picks it when opening a private-sector
   engagement, the screen shows the multiplier each choice brings, and the engagement's admin
   can change it later through `PUT /engagements/{id}/occupation-group`, which re-reads the
   engagement's files under the new selection. An optional `occupation_group` column on the
   employee table overrides it row by row for a mixed workforce. With nothing selected the
   all-occupations 1.4287 is the fallback, and the load report counts who got which.
   The database gains one nullable column, added on start-up for an installation that
   already has engagements (`Store._upgrade_schema`). The five values stay `researched`, because the cited series
   and not the sign-off is what stands behind them; golden case 14 hand-checks them.
   `RESEARCHED_AWAITING_OWNER_REVIEW` in `tests/test_config_consistency.py` is the list for
   the next researched value nobody has reviewed, and is empty.
   **How the figures were read**, for whoever does this next: BLS's news-release tables on
   `www.bls.gov` refuse automated readers whatever the network allows, so the figures came
   from the series behind them at `https://data.bls.gov/timeseries/<series id>`. Private
   industry is `CMU2` + `01` (total compensation) or `02` (wages and salaries) + `0000` +
   a three-digit occupation code + `000D`; the codes used are 000, 100, 200, 300, 400, 500
   for the groups and 110, 120, 210, 220, 430, 510, 520 for the sub-groups behind the bounds.
   `data.bls.gov` is on the Default environment's allowlist.
   **The fallback's bounds**, settled by the owner on 2026-11-13: 1.2996 / 1.4686, the
   lightest and heaviest of the five groups, replacing 1.38 / 1.4835. The old high bound
   rested on a wages figure for the management group that BLS does not bear out, and the old
   low bound did not reach service. The base value 1.4287 was verified and did not change. A
   test holds the bounds to the groups' own figures.
3. **`c3_temp_agency_markup` is `confirmed`, not `researched`, on purpose.** Staffing markups
   are quotable from trade and vendor sources but are not an official statistic, so the
   citation is recorded and the status is not raised to `researched`. Calling a vendor page
   research would devalue the status for the figures that earned it. The owner's approval
   settles who stands behind the figure, not where it came from.
4. **No state-by-state variation.** Unemployment tax rates genuinely are per state; the
   engine counts benefits charged and shows the experience-rating effect as its own factor,
   base 1.0.

### Not done, and not in scope here

- No pattern detection, no probability weighting: the phase boundary in `CLAUDE.md` applies
  to both sectors.
- No private synthetic dataset. The generator still produces Harlow County only, so the
  private pack is proven by golden cases and intake tests rather than by a 500-action run.
  A private generator profile would be the next piece of work if a demo needs one.
- No separate private intake template file set. The new columns are optional additions to the
  existing five tables rather than new tables, because every one of them attaches to a record
  the template already has. Legal spend and unemployment data arriving as their own exports
  is a mapping problem, which the mapping layer already solves.
