# Workforce Risk Intelligence — the cost of discipline

**Every agency tracks discipline as paperwork. Nobody can say what it costs.** This tool
answers that question: it joins HR records to discipline records and reports the fully loaded
cost of discipline for a county government — broken down by employee type and misconduct
type, and **traceable to the last dollar**. Click any figure and it decomposes into line
items that each name their own formula, the values used, and the assumption behind them.

> ### All data here is synthetic
> **Harlow County does not exist.** The data is generated from public distributional shapes
> and documented judgement. Nothing is derived from any real agency's records, and no real
> county, agency, union, employer or person is represented. Cost figures reflect documented
> assumptions, **not measured agency costs**. The owner has approved all 186
> assumptions, but approval is not verification: 90 are low-confidence professional
> estimates, and the demo tints every one of them.

> **Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli. All
> rights reserved — see [LICENSE](LICENSE). Reading this code grants no right to use, copy,
> modify or distribute it.

---

## Run it — five commands

```bash
make install          # create the virtual environment and install dependencies
make generate         # build the synthetic county dataset
make test             # the test suite, with a coverage gate
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
4. **Assumptions** — every coefficient with its source and confidence. Move a slider and
   the whole model recomputes.
5. **Data quality** — what failed validation and what that excluded from the totals.

`docs/demo_script.md` walks the five pages as a five-minute presentation.
`docs/cost_methodology.md` explains every formula, including the judgement calls.

---

## Everything below is detail

`make test` runs the test suite with a coverage gate on the cost and aggregation code.

**One folder here is not the product.** [`legacy/`](legacy/) holds the first prototype, from
February 2026: a dashboard that forecast unit-level risk. It is kept for reference, nobody is
developing it, and it shares no code with the engine. Everything else in this repository is
the engine.

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
| Engagement | everyone | Members and roles (engagement lead); new engagements, license keys, the usage log and user accounts (firm administrator) |
| Load data | engagement lead | Template download, upload, column matching, code translation, preview, commit |
| Executive summary, Cost matrix, Data quality | everyone | The results, labelled with the client's name |
| Drill-down | lead and analyst | One action, every line item |
| Patterns | everyone (the working: lead and analyst) | Where a type of misconduct is more frequent than the same job shows elsewhere, and what it costs there |
| Interventions | lead and analyst | What doing something about a pattern would cost, against what the discipline costs there |
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
| How people sign in here | `GET /auth/sso` |
| Sign in through the identity provider | `POST /auth/sso/start`, then `POST /auth/sso/complete` |
| Let an account be taken by a new identity (firm administrator) | `DELETE /users/{id}/sso-link` |
| Whether this installation requires license keys | `GET /license` |
| Add or replace an engagement's license key (firm administrator) | `PUT /engagements/{id}/license` |
| Read or download the usage log (firm administrator) | `GET /license/usage`, `GET /license/usage.jsonl` |
| Load the client's data (see "Loading a client's data" below) | `/engagements/{id}/intake/...` |
| Use the client's own figure for an assumption, with a reason | `PUT /engagements/{id}/assumptions/{assumption_id}/override` |
| Everything in the table under "The API" below | `/engagements/{id}/...` |

What is stored where: accounts, sessions, engagements, memberships and overrides in a
database (`data/wri.db`, SQLite by default; set `WRI_DATABASE_URL` for another). Each
engagement's source files in `data/engagements/<id>/source/`. Nothing is kept only in memory,
so a restart loses no work. Passwords are stored as scrypt hashes and session tokens as
SHA-256 hashes.

### What the client receives

Two files, both from the **Executive summary** page (`Prepare the report`), or from
`GET /engagements/{id}/exports/report.pdf` and `report.xlsx`:

- **A report to read (PDF).** A cover carrying the consulting firm's name and logo, the
  headline figures, where the money goes, the low-to-high range, where the cost
  concentrates, **where discipline clusters** (the flagged patterns, each with what those
  actions cost, and the units rising over the period), **what was found and what could be done** (the interventions weighed, each
  with its cost and saving), what the records could not support, and two appendices: the assumptions
  these figures actually used with the source of each, and the values specific to this
  client with who set them and why.
- **A workbook to work in (Excel).** The same figures, plus every cell of the cost matrix,
  the figures behind each flagged pattern, and every assumption with its bounds.

Both are produced from one content object, so they cannot disagree. Three rules hold for
both:

- **They show what the person asking may see on screen.** The matrix is suppressed before
  it reaches the report, with the same small-group rule and the same disclosure ledger, and
  a withheld cell carries no figure.
- **They never name a person or a case.** Data quality is reported as counts by rule.
- **The patterns in them are the flagged units only.** Each is released under the same
  small-group rule as a matrix cell, whoever asks for the file, because the file is going
  to the client. One that fails is left out and the report says one was. A flag says where
  to look; the report says in as many words that it does not say why. The section can be
  left out (`patterns=false`, or the tick box beside **Prepare the report**).
- **Text a person typed stays text.** An override's reason or a client's name is never run
  as a spreadsheet formula and never read as layout markup.

Where the registry says who estimated a value ("Owner professional estimate"), the report
says "Professional estimate". Only that attribution is reworded; citations and caveats are
printed as the registry has them, and the registry itself is unchanged.

**The working papers are a third file, and not for the client.** The engagement lead and
the analyst, the two roles that already have the drill-down, are also offered the same
workbook with three more sheets: every action, every line item with how it was worked
out and the assumptions it used, and the working behind the patterns, every unit that was
assessed whether flagged or not (`report.xlsx?records=true`). The sheets sum to the cent to
the report's totals. Its file name ends `_working_papers`, its first sheet says what it is,
and a client reader who asks for it is refused by the server. The lead's copy carries the
client's own employee ids; the analyst's carries masked ones, including for a supervisor or
deciding official that a formula names. Whoever downloads the ordinary workbook, lead
included, gets the aggregates-only copy that is safe to send on.

A firm administrator sets the firm's name and logo once, on the **Engagement** page. The
logo must be a PNG or a JPEG of 512 KB at most; it is checked as a picture when uploaded,
and an SVG is refused. With neither set, reports carry the product's name.

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

### License keys

A licensed installation can be made to require a **key for each engagement**. A key is a
short signed statement, issued by the licensor, that says who it was issued to, which client
and which cost model (sector) it is for, the last day it is good, and optionally the largest
employee file it covers. The installation checks a key against a **verification key** it
holds; it never contacts anyone, so it works on a network with no way out.

It is switched on by installing the verification key, at `config/license_public_key.pem` or
wherever `WRI_LICENSE_PUBLIC_KEY_FILE` points. Without one nothing is enforced and the
product runs as before. The synthetic walkthrough is never licensed. The verification key
is not kept in this repository: it ships with the install package, so a checkout of the
source is never licensed by accident.

| An engagement whose key is | Can |
|---|---|
| in date | do everything |
| expired | be read: results, drill-downs and the client's report still open and download. No new data, no changed value, no scenario, until a current key is added |
| missing, altered, or issued for another client or sector | not be opened. Its members and its key can still be managed, so a firm administrator can put it right |

One key opens one engagement. A dataset with more employees than the key covers is refused
before anything is replaced. A license refusal is HTTP `402`, never `403`: the screen shows it
as a license matter, not as a role being denied.

The installation also keeps a **usage log**: engagements opened, keys added, datasets loaded
(as counts of employees and actions) and reports produced. It holds no client data. Each
entry carries a hash of the one before it, so a log with an entry removed, changed or
reordered shows as such when it is checked. A firm administrator downloads it from the
Engagement page.

The licensor's side is one command-line tool, run on the licensor's own machine:

```bash
wri-license keygen --out-dir ~/wri-keys      # ONCE. Writes the signing key and the verification key
wri-license issue --signing-key ~/wri-keys/wri_license_signing_key.pem \
    --licensee "Northgate Advisory LLP" --client "Ridgeline Builders" \
    --sector private_sector --expires 2027-12-31 --max-employees 500
wri-license inspect WRI1.... --verification-key ~/wri-keys/wri_license_public_key.pem
wri-license verify-log wri_usage_log.jsonl   # is a log a licensee sent intact?
```

**The signing key is the one secret.** Whoever holds it can issue keys. It never goes in this
repository, in an installation, or in a message; `keygen` writes it readable by its owner
only and refuses to overwrite one, and a test fails the build if a private key turns up in
the source tree.

What this is and is not. A key cannot be forged or altered without the signing key, and the
log shows tampering after the fact. But the product is Python that the licensee runs on their
own machine: someone willing to edit the code can remove the check. Keys make the licensed
terms visible and countable, and make breaking them a deliberate act rather than an
oversight. They are not copy protection, and the license agreement is what binds.

### Installing it for a firm

A licensee does not run the product from this source tree. They receive an **install
package**: the product as a container image with the license verification key baked in, a
script that looks after it, and a guide.

```bash
make package LICENSE_PUBLIC_KEY=~/wri-keys/wri_license_public_key.pem    # needs Docker
```

That writes `dist/wri-<version>.tar.gz`. The build refuses the signing key if it is handed
the wrong file of the pair, and refuses to build a release with no key at all. On the
licensee's server it is four commands (`./wri install`, `create-admin`, `start`, `status`);
`./wri backup`, `restore`, `upgrade` and `rollback` do the rest.
[`docs/INSTALL.md`](docs/INSTALL.md) is the guide that ships in the package.

What the install is built to guarantee:

- **Only the screen is published, and only to the server itself** until the firm puts its
  own TLS in front. The part that holds the data is never published.
- **Both containers run unprivileged**, on a read-only filesystem, with every Linux
  capability dropped. The only lasting place they can write is the data volume.
- **A backup comes back whole or not at all.** Every file is checked against a fingerprint
  taken when the backup was made, before anything is changed, and a restore moves the
  existing data aside instead of deleting it.
- **An upgrade backs up first, and an older version will not open newer data.** It stops
  and says so. `./wri rollback` returns to the previous image and the data as it was.
- **Two builds of the same commit hold the same libraries.** Every third-party library is
  fixed at an exact version in `requirements.lock`, and each download is checked against a
  fingerprint recorded there; a file that does not match stops the build. The base image
  is fixed the same way. `make install` uses the same versions, so the tests run on what
  ships. `make lock` brings the lock in line after a dependency is added, keeping every
  version already chosen; `make lock-upgrade` chooses them all again, on purpose.

### Single sign-on

A firm can have its people sign in at its own identity provider instead of with a password
kept here. Any provider that speaks OpenID Connect works; nothing is specific to one.
It is off unless `WRI_SSO_ISSUER` is set. [`docs/INSTALL.md`](docs/INSTALL.md) has the
steps.

- **Nothing the provider sends is believed until it is checked**: the signature against the
  provider's published keys, who it was issued by and for, that it has not expired, and that
  it answers this sign-in and not an earlier one. Tokens signed with a shared secret, or not
  signed, are refused whatever their own header claims.
- **A sign-in belongs to the browser that started it.** A link to someone else's
  half-finished sign-in cannot be finished anywhere else.
- **Being known to the provider is not an account.** A firm administrator creates each
  account, and roles still come from engagement membership.
- **An account answers to one identity**, the provider's permanent id for the person, not
  to whoever holds an email address this year.
- **Signing in through the provider ends the account's password**, so removing someone at
  the provider removes them here. `WRI_PASSWORD_SIGN_IN=off` refuses passwords altogether.

It has been tested against a stand-in provider, including in a real browser. It has not yet
been run against a real one.

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

**Puts every coefficient in a file, not in code.** 186 assumptions in
`config/assumptions.yaml`, each with a source, a confidence level, low/base/high bounds, an
owner and a status. Asking for an assumption that is not there is a hard error, never a
silent default.

The owner signed off on the 167 entries the public-sector model runs on, on 2026-11-12, and
on the 12 the private-sector model added, on 2026-11-13. That is a sign-off, not a
measurement: 15 of those 179 carry a cited source, the other 164 are professional estimates
the owner stands behind, and 90 remain **low confidence**. `status` says who approved a
value; `confidence` says how much weight it bears, and the two moved independently — sign-off
raised no confidence level, and the 15 sourced entries stayed `researched` rather than being
flattened to `confirmed`, because naming a source is the stronger claim.

Five more entries were added after that sign-off and reviewed by the owner the same day:
the private-industry benefits multipliers for each broad occupational group, each read from
its own BLS series. They stay `researched` for the same reason. Two more followed on
2026-11-14, splitting sales work from office work, and he reviewed those the same day.
That makes 186 entries, 22 with a cited source, and nothing awaiting review.

**Enforces use limitation in the engine.** No figure about fewer than five distinct
employees is sent to a caller: not a cell, not a row or column total, and not the grand
total of a filtered selection. Complementary suppression stops a hidden cell being
recovered from a row total; a disclosure ledger kept for each person blocks differencing
across overlapping queries, and survives a restart. Roles are enforced by the API — an
`executive` asking for a record drill-down gets a 403, not a hidden button. Every request is
written to an audit log whose entries are chained, so an entry changed, removed or inserted
afterwards shows (`./wri audit`).

---

## Patterns (Phase 2, first step)

The cost baseline says what discipline costs, by employee type and by type of misconduct.
The **Patterns** page says where a type of misconduct clusters, and what it costs there:
"attendance among corrections officers at the main detention center". For each job at each
work location it compares the rate of actions of one type, per employee-year, with the same
job at every other location. A second view counts every type together.

A unit is flagged only when the difference is unlikely to be chance after allowing for how
many comparisons were made, is large enough to matter, and is not down to a few people
disciplined many times. Each flag shows its arithmetic and two costs:

- **the cost of those actions**, straight from the cost baseline: what the records show
  they cost, the same figure the cost matrix gives;
- an **estimated excess cost**: the actions beyond what the comparison rate would give,
  times the unit's own average cost per action.

What holds:

- **A flag is about a unit, never a person.** Nobody is scored, ranked or named.
- **A flag does not say why.** More actions can mean more misconduct, a supervisor quicker
  to write people up, harder conditions, or better record keeping.
- **The estimate is not part of the baseline**, which still counts only what happened.
- **Everyone who sees totals sees the flags.** A flag exists to be put in front of the
  people who run the organization. A client reader gets the flagged units, each released
  under the same small-group rules as a cell of the cost matrix; one that would let a small
  group be worked out from what that reader has already seen is withheld, and the page says
  one was.
- **Only the engagement lead and the analyst see the working**: what was assessed and not
  flagged, and what could not be assessed. That list would give away the two or three
  people in the small job beside each large one.
- **The two thresholds are the owner's** (`config/detection.yaml`, reviewed 2026-11-16). A
  new one starts unreviewed, and the page and the report say so until he has seen it.

On the synthetic county, by type of misconduct, it makes 196 comparisons and flags one:
attendance among corrections officers at the main detention center, 70 actions where the
same job elsewhere would give 20.6, costing $232,597 in the baseline. That is the pattern
the generator plants. With every type together it makes 29 comparisons and flags the same
unit. The statistics are exact, written out in `src/wri_engine/detection/stats.py`, and
checked by hand in the tests. `GET /detection/hotspots` is the endpoint.

### Rising over the period

The same page asks a second question of the same units: is it getting worse? The period
the records cover is cut in half by date, and each unit's later half is set against its
earlier half.

A unit's own rise is not enough. If the early records are thin, or a new policy raised
attendance discipline everywhere, every unit rises and none of them is a finding. So a
unit's change is compared with the change in **the rest of the organization for the same
type of misconduct**. A unit is flagged as rising only when its rate rose more than the
rest's by more than chance, its later rate is at least 1.5 times its own earlier rate, its
rise is at least 1.5 times the rest's, and enough different people stand behind it. The
thresholds are the same two as above.

Each flag shows what the unit's actions cost in each half, from the cost baseline, and an
estimated excess for the rise. Who sees what is the same as above. A unit that went from
two people to twenty is withheld from a client reader, because the finding would state a
figure about the two.

On the synthetic county nothing is rising, which is right: the generator makes one unit's
rate higher throughout and changes no rate over time. A test takes three in four of that
unit's earlier attendance actions out, and the rise is then found and nothing else is.
`GET /detection/drift` is the endpoint.

Rising units also go into the client report and workbook, under "Where discipline
clusters", by the same rules as the flagged patterns: the client's copy carries the flagged
units only, each released under the small-group rules, and says when one is held back; the
working papers carry everything assessed.

**As much of a finding as can be shown, is.** Where a unit's own groups pass the
small-group rules and its comparison's do not, the client sees the unit's own figures with
the comparison's marked "withheld", and the finding says why. On the county with a rise
planted in it, the client's report shows the rising unit (9 actions then 33, its cost in
each half, 3.91 times its earlier rate) with the rest of the organization's figures
withheld, because the rest's earlier half sits a few people away from small cells of the
report's own matrix. A finding whose own unit fails is still withheld and counted.

## Interventions (Phase 3, first step)

Phase 2 says where a type of misconduct clusters and what it costs. Phase 3 starts when
someone goes there, finds out what is going on, and proposes to do something about it. The
**Interventions** page does the arithmetic of that proposal:

- **What the discipline costs** comes from the cost baseline: the net cost of that unit's
  actions of that type, per year.
- **What the intervention costs** is what the consultant enters: a one-time cost, a yearly
  cost for each person covered, any other yearly cost, and how many years to look ahead.
- **How well it works** is the consultant's judgement, entered as a low, an expected and a
  high share of the actions it would prevent, with the basis for those figures in words.
  The product has no evidence of its own for this and will not proceed without the basis.

It then shows the net saving three ways, and one figure that needs no judgement: the share
of actions the intervention must prevent to pay for itself.

What holds:

- **The software does not find causes.** People do, on site. The page says so and suggests
  none.
- **A saving is a projection, and no part of the cost baseline.** The page says what it
  assumes: the unit goes on costing what it did, a prevented action costs the unit's
  average, nothing is discounted, and only the cost of discipline counts as a benefit.
- **For the lead and the analyst.** It works on any unit, whatever its size.
`POST /interventions/appraise` is the endpoint.

### Starting from the library

`config/intervention_library.yaml` holds starting figures for the four interventions the
program overview names: a transit subsidy, a childcare subsidy, targeted training, and
flexible start and end times. Each has a one-time cost per person, a yearly cost per
person, and a low, expected and high share of actions prevented. On the Interventions page
a consultant may fill the form from one and then change any figure to the client's own.

**What stands behind each figure.** The owner's research of 2026-11-17 sourced seven of the
twelve, and he confirmed them the same day; their citations are in the file. The other five
are costs with no published benchmark and stay *placeholders*: a client's real figures
should come from payroll or the benefits administrator. While an appraisal carries a
placeholder:

- the page says, for the chosen entry, which figures are placeholders, and lists the
  sources;
- an appraisal that still carries any library figure says which, on screen, in the kept
  list, and **in the client's report and workbook**;
- changing a figure to the client's own takes it out of that sentence, and nothing else
  does. Which figures still stand is worked out on the server from the figures themselves.

**What it is aimed at.** For the transit subsidy, the childcare subsidy and flexible hours,
the studies behind the share prevented measured absences and lateness, not discipline. So
those entries fill in "aimed at Attendance / AWOL and Tardiness", and the appraisal counts
only those actions: the yearly cost of discipline it sets the intervention against is the
cost of those actions alone. A consultant can aim any intervention at chosen types of
misconduct, or at every type. If the library's share is kept and aimed wider than its
evidence, the appraisal says so. Targeted training is aimed at misconduct itself.

Choosing an entry is a convenience. It suggests nothing about what is causing a pattern.
`GET /interventions/library` is the endpoint.

### Keeping an appraisal, with what was found

An appraisal can be kept with the engagement (**Work it out and keep**). It is kept as the
record of a finding, so it needs one:

- **Causes are chosen from a short fixed list** (`config/interventions.yaml`): commuting
  burden, childcare or family-care gaps, missing training or education, inflexible
  scheduling, or **Other**. A person chooses, after going there. The product offers no
  suggestion and will not keep an appraisal with no cause chosen.
- **Only "Other" takes words, and only a line** (300 characters). It is for describing a
  condition, never a person. Interview notes do not belong in the product.
- **Every finding says whose it is** and when it was entered, and the page says the product
  did not find or check it.
- **It lives in the engagement's own folder**, not the database, because those words are a
  client's confidential material. It survives a restart, is carried by a backup, and is
  never seen from another engagement.
- **Nothing is rewritten or removed.** Withdrawing an appraisal stops it being shown and
  adds a line saying who withdrew it.
- **The saving beside a kept appraisal is always today's.** Only the inputs are kept, so a
  corrected client file or a changed assumption is never contradicted by a stale figure.

`GET /interventions`, `POST /interventions` and `DELETE /interventions/{id}` are the
endpoints, for the lead and the analyst.

### Several proposals for one unit, side by side

When two or more appraisals are kept for the same unit and the same type of misconduct,
the page sets them next to each other on yearly terms: what each costs a year, the share
of actions it must prevent to pay for itself, and its net saving a year at the low,
expected and high reading. Yearly, because one proposal may look three years ahead and
another five.

- **They are listed by what each has to prove, least first**: the share it must prevent to
  pay for itself, which needs no judgement about how well anything works. The expected
  savings are shown beside it and are not ranked against each other, because each rests
  on its own estimate.
- **Nothing is named the best.** The choice is the client's.
- **The savings cannot be added.** They are alternatives: two interventions aimed at the
  same actions do not prevent the sum of what each would prevent alone.

The comparison is part of `GET /interventions` (`side_by_side`) and goes into the client
report under the same rules as the appraisals themselves: a client's copy compares only
those it shows with their money.

### In the client report

Kept appraisals go into the report and the workbook, as a section headed **What was found,
and what could be done**: for each, the unit, the causes found and whose finding they are,
what is proposed, what the discipline costs a year, what the intervention costs, the net
saving three ways, the share it must prevent to pay for itself, and what the expectation is
based on. Three rules decide what the client's copy carries, whoever asks for the file:

- **A cause is named by its label only.** What was typed for "Other" never reaches the
  client's copy: it shows the word "Other". The words are in the working papers.
- **A finding about fewer people than the minimum group is left out and counted.** A cause
  said of three people is said of them.
- **A unit's cost is released like a matrix cell.** Where it may not be shown beside the
  other figures in the report, the entry keeps the finding and the proposal and says the
  money is withheld.

The section says a saving is a projection and adds to no total. With nothing kept there is
no such section. It can be left out (`interventions=false`, or the tick box beside **Prepare
the report**). The working papers add an **Interventions working** sheet: every appraisal
ever kept, what "Other" was said to be, and what was withdrawn and by whom.

## What it deliberately does not do

**No individual risk scoring.** **No finding of causes**: a person does that on site, and
nothing in the product suggests one. **No evidence on how well an intervention works**: that
is the consultant's judgement, entered with its basis, and the library's starting figures
say what stands behind them. **Nothing in the cost baseline is
probability-weighted**: a cost is counted only when the record shows the event happened,
and a projected saving is never added to it.

The synthetic data contains one deliberately planted pattern — elevated attendance violations
for Corrections Officers at the Main Detention Center. The cost baseline surfaces it as an
expensive cell, never as an alert. It is documented in
`src/wri_engine/generator/README.md` so nobody mistakes a planted pattern for a discovery.

---

## Layout

```
workforce-risk-platform/
  CLAUDE.md                     conventions, and the rules that must not be broken
  config/
    assumptions.yaml            186 cost coefficients, each with its source
    org_county.yaml             departments, pay plan, schedules, taxonomies
    generator_profile.yaml      every synthetic-data distribution parameter
    mapping_county_hr_csv.yaml  the whole source-to-canonical contract
  src/wri_engine/
    schema/                     canonical Pydantic models
    adapters/                   base.py, county_hr_csv.py, template.py, validation.py, nfc.py (stub)
    generator/                  seeded synthetic generator + README of distributions
    sectors/                    which components, multipliers and ids a sector runs
    costing/                    assumptions, rates, context, components/, engine
    aggregation/                rollups, suppression
    access/                     roles, pseudonymization, the chained audit log
    api/                        FastAPI service
    reporting/                  the client report (PDF) and workbook (Excel), from one content
    licensing/                  license keys, the usage-log chain, the licensor's wri-license tool
    store/                      sign-in, engagements and overrides (never client HR data)
    backup.py                   back up an installation and put one back
  app/                          Streamlit UI — HTTP only, never imports the engine
  tests/golden/                 fourteen hand-calculated cases, asserted to the cent
  docs/
    cost_methodology.md         every formula, every simplification, in prose
    demo_script.md              the five-minute walkthrough
    WORKPLAN.md                 what is built, what is next, and who is working on what
    INSTALL.md                  the guide that ships in the install package
  Dockerfile                    the installed product: API and screen in one image
  deploy/                       compose file, the operator's ./wri script, the package builder
  legacy/                       the February 2026 prototype; not the product
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
| `GET /detection/hotspots` | Where a type of misconduct clusters, with its cost (`by=misconduct` or `overall`). Flagged units for every role; the working for roles that see records |
| `GET /interventions`, `POST /interventions`, `DELETE /interventions/{id}` | The appraisals kept with the engagement, each with the causes found on site and whose finding it is; keep one; withdraw one (lead and analyst) |
| `GET /interventions/causes` | The short fixed list of causes an investigation may record |
| `POST /interventions/appraise` | What an intervention would cost against what a unit's discipline costs: net saving three ways, and the share it must prevent to pay for itself (lead and analyst) |
| `GET /detection/drift` | Where a type of misconduct is rising: the later half of the period against the earlier, set against the change in the rest of the organization. Same views and same roles |
| `GET /assumptions` | The registry with sources and statuses |
| `POST /scenarios/run` | Re-run with overridden assumptions; nothing on disk changes |
| `GET /exports/matrix.csv`, `GET /exports/summary.md` | Exports |
| `GET /exports/report.pdf`, `GET /exports/report.xlsx` | The report for the client and the workbook beside it |
| `GET /exports/report.xlsx?records=true` | The working papers: the workbook plus every action and line item (role-gated, as the drill-down is) |
| `GET /firm`, `PUT /firm`, `PUT /firm/logo`, `DELETE /firm/logo` | The consulting firm's name and logo (firm administrator sets them) |

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

1. The **sworn benefits multiplier**, 1.75. It is an owner estimate, and it scales every
   public-safety number in the model. BLS publishes no figure for sworn staff on their own,
   but on 2026-11-14 it was checked against the two nearest series BLS does publish for
   state and local government: service occupations (1.7709 to 1.7781 in 2026) and public
   administration (1.7473 to 1.7511). 1.75 lies inside both, so the owner kept it and
   raised its confidence to medium. A client's own pension and retiree-health contribution
   rates would still beat any national average.
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
10. The **separate multipliers for sales work and office work**, 1.3283 and 1.4564,
    reviewed by the owner on 2026-11-14. They are the two halves of the sales and office group,
    each from its own BLS series, added so a client whose staff are mostly one or the other
    is not loaded at the average of both.

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
