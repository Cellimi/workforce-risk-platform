# Work plan (engineering)

How work on the engine is split and sequenced. This file is the shared ground between the
Claude sessions that work on this repository: they cannot see each other's conversations, and
a Claude Code session cannot read the owner's Claude Project documents. Read this before
starting, and update it in the same pull request as the work it describes.

Business decisions (pricing, ownership, legal structure) are kept outside the repository. Do
not add them here: the repository is public.

Last updated 2026-11-15.

## Working arrangement

Two sessions work on the engine: a chat session and a Claude Code session.

1. **Branches.** The stacked pull requests #2 to #8 were merged into `main` on 2026-11-14,
   by the owner's instruction. Start new work on a new branch from `main` and open its pull
   request against `main`. If one piece of work has to build on another that is not merged
   yet, branch from that one and target it, and say so here.
2. **The engine is at the top level.** It used to live in `wri-engine/`; every path in an
   older branch, note or command needs that prefix removed. Run `make` from the repository
   root. A branch cut before the move must be rebased or merged onto `main` before it is
   worked on, or every file it touches will conflict.
   `claude/wri-phase1-discipline-cost-n73knl` is retired; do not push to it.
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
| One project in the repository (retire the February prototype) | Claude Code session | Done: prototype in `legacy/` (PR #7), engine at the top level (`claude/engine-to-top-level`) |
| Check the sworn benefits multiplier against BLS | Claude Code session | Done, `claude/sworn-multiplier-evidence`. 1.75 kept; confidence raised to medium |
| Offer sales and office work as separate occupation selections | Claude Code session | Done (PR #10). The owner reviewed the two values on 2026-11-14 |
| Client deliverables: branded report, PDF and spreadsheet export | Claude Code session | Done, `claude/client-deliverables` (built on `claude/split-sales-office`) |
| Engagement license keys | Claude Code session | Done, PR #13 |
| Record-level working papers; client wording in Appendix A; where the verification key ships | Claude Code session | Done, PR #14 |
| Install package: container install, configuration guide, upgrade path | Claude Code session | Done, `claude/install-package` |

## Where things stand

Reference figures on the synthetic Harlow County dataset (508 costed actions), approved
assumption values: net $4.64M at base, $2.99M with every assumption at its low bound, $8.73M
at the high bound. **The private-sector work did not move any of them**, and a property test
asserts it line item by line item. 399 tests, 96.0% coverage.

The sworn benefits multiplier (1.75), which scales every public-safety figure, was checked
against BLS on 2026-11-14. BLS publishes nothing for sworn staff alone; the two nearest
series, state and local service occupations and public administration, bracket it at
1.7473 to 1.7781 for 2026. The owner kept the value and raised its confidence to medium.

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
5. **Engagement license keys.** Built: a signed key per engagement, verified offline by the
   installed product, with an exportable usage log. See "Engagement license keys" below.
6. **Client deliverables.** Built: a report carrying the consulting firm's name and logo
   (PDF), a workbook beside it (Excel), and appendices of the assumptions used and the
   client's own values. See "Client deliverables" below.
7. **Install package.** Built: a container image with the license verification key baked
   in, an operator's script, a configuration guide and an upgrade path with a way back. See
   "Install package" below.
8. **Phases 2 and 3** (hotspot detection; intervention cost-benefit). Out of scope until
   Phase 1 is in use: see "Phase boundary" in `CLAUDE.md`.

## Install package

What a licensee receives is built by `make package LICENSE_PUBLIC_KEY=<the verification
key>` (needs Docker) and written to `dist/wri-<version>.tar.gz`. The pieces:

| Piece | Where | What it does |
|---|---|---|
| The image | `Dockerfile`, `deploy/entrypoint.sh` | API and screen in one image, run as two services. Unprivileged user, one API worker |
| The services | `deploy/compose.yaml` | Read-only filesystem, all capabilities dropped. Only the screen is published, to `127.0.0.1` by default; the API never is. One volume holds everything |
| The operator's script | `deploy/wri` | `install`, `start`, `stop`, `status`, `create-admin`, `backup`, `restore`, `upgrade`, `rollback`, `logs` |
| The builder | `deploy/package.py` | Checks the verification key, builds and checks the image, writes the package and its checksums |
| The guide | `docs/INSTALL.md` | Ships in the package as `INSTALL.md` |
| Backup and restore | `src/wri_engine/backup.py`, `wri-admin backup/restore/status` | See below |

**The verification key.** By the owner's decision it ships only here. The builder refuses a
file that holds a private key (that is the signing key), refuses to build a release with no
key, and tags a deliberately keyless build `-unlicensed`. `.dockerignore` keeps every `.pem`
out of the build, so the key reaches the image only through its own build context.

**Backup and restore.** One `.tar.gz` of the database (copied with SQLite's online backup,
so it is consistent while running), the pseudonym salt, every engagement's files and the
audit log, with a manifest of SHA-256 fingerprints. A restore unpacks to a holding folder
and checks everything before touching the installation; it refuses a damaged or altered
backup, a file the product would not have written, and a backup from a newer product. It
never deletes: what it replaces is moved into `replaced-<date>/`.

**Upgrade path.** `SCHEMA_VERSION` in `store/db.py` (now 1) is recorded in the database.
A product that finds a higher number than it knows refuses to start and changes nothing.
`./wri upgrade` stops the product, backs up with the old image, switches and starts;
`./wri rollback` goes back to the previous image and that backup together. **Whoever makes
a change an older version could not read must raise `SCHEMA_VERSION`** and add the step to
`_upgrade_schema`.

**What was run for real, on 2026-11-15, with a throwaway key pair** (Docker 29, image on
Python 3.12): the package was built by `deploy/package.py`; unpacked into an empty folder
and its checksums verified; `./wri install`, `create-admin` (typed at a terminal), `start`,
`status`; an engagement opened with a license key, a dataset loaded, the report and working
papers produced, the screen opened in a browser; `backup`, `restore`; an upgrade to a
stand-in 0.3.1 with a higher data version; the old image pointed at the upgraded data
(refused, as designed); `rollback`. All passed. The key pair was deleted afterwards.

**Not done, and what the owner needs to know:**

1. **The real package has not been built**, because only the owner has the real
   verification key. `make package LICENSE_PUBLIC_KEY=...` on a machine with Docker does it.
2. **TLS is the firm's to supply.** The product speaks plain HTTP and is published only to
   the server itself. The guide says what a reverse proxy must do and shows a Caddy
   configuration as an illustration; that configuration was **not** tested.
3. **The image is about 1.1 GB** (254 MB as shipped). Most of it is pandas, DuckDB,
   Streamlit and Plotly. Not slimmed.
4. **Dependencies are not pinned in `pyproject.toml`.** The image is the pinned artefact:
   `requirements.lock` in the package lists what it holds. Two builds a month apart can
   differ. Pinning is a separate decision.
5. **Linux servers only**, built for `linux/amd64` by default (`--platform` changes it).
   The operator's script is bash.
6. **One installation per server.** The volume has a fixed name.
7. **No scheduled backups.** `./wri backup` is a command; running it nightly is for the
   firm's own scheduler. The guide says so.
8. **The version is now 0.3.0** (was 0.2.0, unchanged since before the private-sector
   model). It is read from one place and shown at `/health` and by `./wri status`.

## Engagement license keys

Built in `src/wri_engine/licensing/` (the key, the log's chain, the licensor's tool) and
`src/wri_engine/api/licensing.py` (what a key allows on each request).

**How it works.** A key is `WRI1.<what it says>.<signature>`, signed with Ed25519. It says
who it was issued to, the client, the sector, the day it was issued, the last day it is good
and, optionally, the largest employee file it covers. The installation holds only the
verification key and checks every request against the engagement's stored key; it contacts
nobody. The check sits in `get_ctx`, after membership is settled, so every data endpoint is
covered by construction and someone outside an engagement learns nothing about its key.

**What was decided in the build.** These are behaviours, each one a line or two to change.
The owner has not ruled on them yet:

| Behaviour | As built | Why |
|---|---|---|
| When keys are enforced | Only when a verification key is installed; never in the walkthrough | An installation with no key to check against cannot enforce anything. It also means **nothing is enforced until the owner makes his key pair and the verification key is installed** |
| What a key is tied to | Client name (case, spacing and punctuation ignored) and sector | So a key bought for one client cannot open another. A renamed client needs a new key |
| How many engagements per key | One, per installation | A second engagement for the same client needs a second key |
| An expired key | Read-only: results and the report still open; no new data, value or scenario | A firm must be able to open what it delivered |
| No key, or a key that fails | Engagement closed; members and the key can still be managed | Otherwise it could never be put right |
| Employee limit | Optional. A larger file is refused before anything is replaced | Checked at load, where the count is known |
| Who adds a key | The firm administrator only | Same person who opens engagements |
| Usage log | Kept in every licensed installation, enforced or not; counts only | A record from before enforcement began is still a record |

**What it does not do, stated plainly.**

- It is not copy protection. The product is Python on the licensee's own machine; a licensee
  who edits the code can remove the check. Keys make the terms visible and countable.
- The usage log is tamper-*evident*, not tamper-proof. An edited log fails `wri-license
  verify-log`; a log deleted whole and started again passes on its own, and is caught only
  by comparing its keys and dates with what was issued.
- "One key, one engagement" is per installation. Two installations do not know about each
  other. The log from each is how reuse is seen.
- There is no revocation. A key is good until its last day. Short terms are the control.
- The clock is the machine's. A licensee who sets the date back extends a key.

**The owner's steps, once.** These cannot be done from a Claude session and must not be: the
signing key should never exist anywhere but the owner's own machine and its backup.

  1. `wri-license keygen --out-dir <a folder outside any repository>`.
  2. Back up `wri_license_signing_key.pem` somewhere offline. If it is lost, no new key can
     be issued for installed products; if it is copied, anyone can issue them.
  3. Give installations `wri_license_public_key.pem` as `config/license_public_key.pem`.
     **Decided by the owner on 2026-11-15: it ships only in the install package and is not
     committed here.** `.gitignore` lists it, so a checkout of the source is never licensed
     by accident.

**Done in the install package (item 7):** the verification key is baked into the image
when the package is built, and `./wri status` shows whether keys are enforced.

## Client deliverables

Built in `src/wri_engine/reporting/`. `content.py` assembles a `ReportContent`; `pdf.py` and
`xlsx.py` each lay that out and decide nothing about what it says. Change a figure or a
sentence in `content.py` and both files change together.

- **Where it is served.** `GET /exports/report.pdf` and `GET /exports/report.xlsx`, for any
  role that may view aggregates, in an engagement or in the synthetic walkthrough. The
  **Executive summary** page prepares both on request and offers them for download.
- **The firm's name and logo** live in a `firm_profile` table (one row), set by a firm
  administrator through `PUT /firm` and `PUT /firm/logo` or on the **Engagement** page. A
  logo is checked as a picture on upload (`reporting/branding.py`): PNG or JPEG only, 512 KB
  at most. SVG is refused on purpose.
- **Use limitation holds** (`CLAUDE.md` rule 10). The matrix is built by the API with the
  caller's disclosure ledger and suppressed before `build_report` sees it. Data quality is
  counts by rule; no employee or action id is in a deliverable.
- **Nothing typed is trusted.** Workbook strings are stored as strings whatever they start
  with, so a reason beginning `=` is not a formula; PDF text is escaped before layout.
- **Dependencies added:** `reportlab` (PDF), `openpyxl` (Excel), `pillow` (reading a logo),
  and `pypdf` for the tests only.

Found while building it and fixed in the same change: the summary's component breakdown
(`summarize` in `aggregation/rollups.py`, and so the screen's chart and `summary.md`) listed
only the seven public-sector components, so a private engagement's separation and insurance
costs were in the totals but missing from the breakdown. A component is now listed whenever
the run has money against it. Public output is unchanged.

Decided by the owner on 2026-11-15 and built in `claude/report-record-sheet`:

1. **Record-level export: yes.** Built as the **working papers**, a separate download and
   not a sheet in the client's copy. `report.xlsx?records=true` adds an Actions sheet and a
   Line items sheet for a caller with `view_records` (engagement lead and analyst); a
   client reader gets 403. The ordinary workbook stays aggregates only for everyone, the
   lead included, so there is always a copy that is safe to send on and the two cannot be
   mistaken: the file name ends `_working_papers` and the first sheet says what it is.
   Identifiers follow the drill-down's rule: real for the lead, masked for the analyst.
   Why separate: one workbook with a record sheet would have meant the lead deleting a
   sheet by hand before every send, which is use limitation left to memory.
2. **Wording in Appendix A: "Professional estimate".** `client_wording` in
   `reporting/content.py` rewords the attribution on the way into a report; the registry
   keeps "Owner professional estimate" as its record of who estimated the value. Two other
   pieces of internal wording still reach Appendix A unchanged and are the owner's to rule
   on: "(Section 5.2 starting defaults)" on 22 values and "OPEN ITEM 2 - ..." on 2.

Found and fixed in the same change, because the working papers would have multiplied it:
**the drill-down showed an analyst a real employee id.** A C1 formula names the supervisor
or the deciding official ("supervisor E00483 (Sheriff's Sergeant): ..."). The subject's id
was masked for an analyst and that one was not. Both are now masked, in the formula and in
the stored inputs, with the same code that person carries everywhere else
(`project_named_colleagues` in `access/roles.py`). Only the exact phrase that names them is
rewritten, so a client whose ids are plain numbers never has "30 hrs" turned into a code.
The engagement lead's view is unchanged. Not masked, and worth knowing: the colleague's job
title stays in the sentence, as the subject's department and role already do.

Not done:

3. **One layout.** No choice of colours, fonts or page size per firm; the logo and name are
   the branding.
4. **The report's matrix is always employee type by misconduct type**, the screen's default.

## One project in the repository

The repository used to hold two projects side by side: the engine in `wri-engine/` and, at
the top level, the first prototype from February 2026 (a dashboard forecasting unit-level
risk). The owner asked for the repository to show one WRI, keeping the prototype for
reference.

- **Step 1, done (PR #7).** The prototype moved to `legacy/` with its own README. It was
  run from its new folder to check it still works.
- **Step 2, done (`claude/engine-to-top-level`).** Once the stack had merged, every tracked
  file under `wri-engine/` moved up one level, unchanged. The engine's README became the
  repository's, the two `.gitignore` files merged, and the duplicate `LICENSE` was dropped.
  The environment was rebuilt at the top level and the whole suite run there.
- **What a person has to do after pulling it.** Git moves only the files it tracks, so
  anything a local copy made for itself is left behind in a `wri-engine/` folder:
  1. Run `make install` again from the repository root; the old virtual environment stays
     in `wri-engine/.venv` and is no longer used.
  2. If that copy was ever run as a licensed installation, move its own records across
     **before deleting anything**: `wri-engine/data/wri.db`, `wri-engine/data/pseudonym.salt`
     and `wri-engine/data/engagements/` go into `data/`. Without them the product starts
     with an empty database and no engagements, and the salt is what keeps pseudonymized
     employee ids stable.
  3. Then the leftover `wri-engine/` folder can be deleted.
- **The prototype's features are not being ported.** Forecasting, risk scoring and
  intervention recommendations are Phases 2 and 3 (see "Phase boundary" in `CLAUDE.md`). If
  they are taken up, they are built in the engine on its data, not lifted from `legacy/`.

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
  genuine difference earned a new id, and there are nineteen.
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
