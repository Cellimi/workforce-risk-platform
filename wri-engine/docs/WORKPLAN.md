# Work plan (engineering)

How work on the engine is split and sequenced. This file is the shared ground between the
Claude sessions that work on this repository: they cannot see each other's conversations, and
a Claude Code session cannot read the owner's Claude Project documents. Read this before
starting, and update it in the same pull request as the work it describes.

Business decisions (pricing, ownership, legal structure) are kept outside the repository. Do
not add them here: the repository is public.

Last updated 2026-11-12.

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
| Private-sector cost model | Claude Code session | Not started |
| Engagement license keys | (unclaimed) | |

## Where things stand

Reference figures on the synthetic Harlow County dataset (508 costed actions), approved
assumption values: net $4.64M at base, $2.99M with every assumption at its low bound, $8.73M
at the high bound. 260 tests, 95% coverage.

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
4. **Private-sector cost model.** Next. See below.
5. **Engagement license keys.** A signed key per engagement, verified offline by the installed
   product, with an exportable usage log.
6. **Client deliverables.** A report carrying the consulting firm's name and logo; PDF and
   spreadsheet export; an appendix of assumptions and overrides.
7. **Install package.** Container install, configuration guide, upgrade path.
8. **Phases 2 and 3** (hotspot detection; intervention cost-benefit). Out of scope until
   Phase 1 is in use: see "Phase boundary" in `CLAUDE.md`.

## Private-sector cost model

A second sector pack beside the public-sector one. Same principle: count a cost only when the
record shows the event happened.

| Component | Public sector (built) | Private sector (proposed) |
|---|---|---|
| Processing labour | Supervisor, HR, deciding official, investigator hours | Same structure; add outside investigator fees where used |
| Paid leave during investigation | Paid administrative leave | Paid suspension pending investigation (typically shorter) |
| Coverage for absence | Backfill overtime at minimum-staffing posts | Overtime or temp-agency coverage for hourly and shift roles |
| Unpaid suspension offset | Wage not paid, shown as a negative | Same, limited for salaried exempt staff by wage-and-hour rules |
| Challenges to the action | Grievance steps, arbitration, civil service board, court | Grievance and arbitration where a union contract exists; otherwise agency charges, demand letters, litigation defence, settlements and judgments |
| Insurance | Not applicable | Employment practices liability retention or deductible; insurer-paid amounts shown separately |
| Separation cost | Not modelled | Severance and separation agreements; unemployment claim cost |
| Turnover | Recruiting, screening, academy, field training, equipment, washout | Recruiting (including agency fees), onboarding, productivity ramp, vacancy loss; no academy |
| Benefits loading | State and local government multipliers | Private-industry multipliers by broad occupation group |

Things to know before starting:

- **The sector is switched on in two places.** Add `private_sector` to `SECTORS` in
  `src/wri_engine/store/db.py` (the server refuses any other sector today) and to the sector
  list in `_new_engagement` in `app/licensed.py` (the screen offers public sector only).
- **An engagement already carries its sector** (`EngagementRecord.sector`), but nothing yet
  chooses an assumption set or component list by sector. That selection is part of this work.
- **Private-sector data is scattered**: discipline in an HR system or spreadsheets, legal spend
  in accounts payable or matter management, unemployment costs with a third-party
  administrator. The intake template will need tables for these (see
  `src/wri_engine/adapters/template.py`).
- **Every new value is researched with a citation** and marked `researched` for the owner to
  review. Every new component gets a golden test in `tests/golden/`.
