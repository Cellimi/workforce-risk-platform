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
| Install package: container install, configuration guide, upgrade path | Claude Code session | Done, PR #15 |
| Disclosure ledgers that survive a restart | Claude Code session | Done, PR #16 |
| An audit log that shows when it has been edited | Claude Code session | Done, PR #17 |
| Fixed dependency versions and a smaller image | Claude Code session | Done, PR #18 |
| Single sign-on (OpenID Connect, any provider) | Claude Code session | Done, PR #19 |
| Phase 2, step 1: hotspot detection (where a type of misconduct clusters, with its cost, shown to leadership) | Claude Code session | Done, PR #20. Reworked 2026-11-16 to the owner's program overview |
| Totals held to the minimum group size, as cells are | Claude Code session | Done, PR #21 |
| Phase 2, step 2: the flagged patterns in the client report | Claude Code session | Done, PR #22 |
| Phase 2, step 3: drift (where a type of misconduct is rising) | Claude Code session | Done, PR #23 |
| Owner's decisions of 2026-11-16: thresholds reviewed, client reader confirmed, small-group rules kept, Phase 3 opened | Claude Code session | Done, PR #24 (built on PR #23) |
| Phase 3, step 1: the cost-benefit of an intervention for a unit | Claude Code session | Done, PR #25 (built on PR #24) |
| Phase 3, step 2: keeping an appraisal with the engagement, with the causes found | Claude Code session | Done, PR #26 (built on PR #25) |
| Phase 3, step 3: the cost-benefit in the client report | Claude Code session | Done, PR #27 |
| Phase 3, step 4: several interventions for one unit, side by side | Claude Code session | Done, PR #28 |
| Phase 2, step 4: rising units in the client report | Claude Code session | Done, PR #29 |
| Show as much of a finding as can safely be shown | Claude Code session | Done, `claude/partial-release` |

## Where things stand

Reference figures on the synthetic Harlow County dataset (508 costed actions), approved
assumption values: net $4.64M at base, $2.99M with every assumption at its low bound, $8.73M
at the high bound. **The private-sector work did not move any of them**, and a property test
asserts it line item by line item. 524 tests, 96.5% coverage.

The sworn benefits multiplier (1.75), which scales every public-safety figure, was checked
against BLS on 2026-11-14. BLS publishes nothing for sworn staff alone; the two nearest
series, state and local service occupations and public administration, bracket it at
1.7473 to 1.7781 for 2026. The owner kept the value and raised its confidence to medium.

1. **Hardening (PR #3).** Sign-in with local accounts, roles from engagement membership, a
   database, isolated engagement workspaces, per-engagement assumption overrides recorded with
   who and why. Disclosure ledgers now survive a restart (see "Disclosure ledgers" below).
   The audit log now shows when it has been edited (see "Audit log" below).
   Single sign-on is built (see "Single sign-on" below). Nothing on the hardening list
   remains.
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
8. **Phase 2, pattern detection and escalation.** Opened by the owner on 2026-11-15. Step
   1, hotspots, step 2, the flagged patterns in the client report, and step 3, drift over
   time, are built, and both are in the client report: see "Phase 2: detection" below.
9. **Phase 3** (root-cause investigation; intervention cost-benefit). Opened by the owner
   on 2026-11-16. His program overview describes it as an on-site investigation of a
   flagged unit, then candidate interventions each priced and set against what the
   discipline costs. See "Phase 3" below and "Phase boundary" in `CLAUDE.md`.

## Phase 2: detection

The owner opened Phase 2 on 2026-11-15 with "begin Phase 2". Step 1 was first built from
what this repository said Phase 2 was, because a Claude Code session cannot read his project
documents. On 2026-11-16 he gave the build his program overview and product plan, with
"going forward use these plans as the guide for building this", and step 1 was reworked to
match. (The business parts of those documents stay out of this repository, as before.)

**What the program overview asks of Phase 2**, in substance: analyze the frequency and
clustering of *types of misconduct* across the organization to surface hotspots; call the
pattern out to management; pair it with the Phase 1 cost data, so that leadership sees not
only that a problem exists but what it is costing at that location or unit.

| The overview asks for | First build (2026-11-15) | Now |
|---|---|---|
| Hotspots by type of misconduct | Every type counted together | By type of misconduct is the main view; every type together is the second |
| Called out to management | Engagement lead and analyst only; the client reader was refused | Every role that sees totals sees the flagged units. The working stays with record-level roles |
| Paired with the Phase 1 cost at that unit | An estimated excess cost only | The baseline cost of those actions, and the estimated excess beside it |

### Step 1, built: hotspots

`src/wri_engine/detection/` (`stats.py`, `exposure.py`, `hotspots.py`),
`config/detection.yaml`, `GET /detection/hotspots?by=misconduct|overall`, and the
**Patterns** page. The method is written out for a reader in `docs/cost_methodology.md`
section 11a.

On the full synthetic county, by type of misconduct: 196 comparisons, one flagged, the
planted one (attendance among corrections officers at the main detention center), at 3.40
times the comparison rate; those actions cost $232,626 in the baseline and the estimated
excess is $164,168. Every type together: 29 comparisons, the same unit, 2.07 times, an
estimated excess of $554,786. Tests require exactly that.

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| What is assessed | A unit: a job at a work location, by type of misconduct or overall. Never a person | The program overview's question is about a role, location or unit. Scoring individuals is a different product with different legal exposure, and was not asked for |
| What it is compared with | The same job (and the same type) at every other location | Jobs are disciplined at different rates. Comparing across jobs would flag every jail and no library |
| The measure | Actions per employee-year | Counts alone reward size. Employee-years allow for people joining and leaving |
| The test | Exact (binomial, given the total), then Benjamini-Hochberg across all comparisons | No approximation to defend, sound at small counts, and checkable by hand |
| What counts as a comparison | Each location of a job, for each type that job was disciplined for anywhere | A type nobody in the job was disciplined for cannot stand out. Counting it would only make every real comparison harder to pass |
| `detection_false_discovery_rate` | 0.05. Reviewed and kept by the owner, 2026-11-16 | How many flags may be chance is his call |
| `detection_min_rate_ratio` | 1.5. Reviewed and kept by the owner, 2026-11-16 | What difference is worth a person's attention is his call |
| Fewest people | The engagement's minimum group size (5) | A handful of people is not a pattern in a unit |
| A few people, many actions | Reported as "concentrated in few people", not flagged | It is a different thing, and flagging it would point at individuals |
| Who sees the flags | Every role that sees totals, the client reader included | The overview: the pattern is called out to management. See below for how that is made safe |
| Who sees the working | Engagement lead and analyst only | The list of units not flagged would hand over the small groups |
| Cause | Never stated or suggested | That is Phase 3, and before that a person's job |
| Cost beside a flag | The baseline's net cost of those actions (an actual), and an estimated excess (labelled an estimate, never added to the baseline) | The overview pairs the pattern with the Phase 1 cost. Rule 9 governs the baseline |

**How the client reader's view is made safe.** A flagged unit is a figure about a group of
people, like a cell of the cost matrix, so it goes through the same two rules
(`release_together` in `aggregation/suppression.py`): the group is at least the minimum
size, and with what this reader has already been shown it does not let a smaller group be
worked out by subtraction. A finding gives figures for three groups (the unit, its
comparison, the two together). Where all three pass, it is shown in full. Where the unit
passes and the comparison does not, the unit's own figures are shown, the comparison's
are marked withheld, and the finding says why. Where the unit fails, the finding is
withheld and the reader is told one was. What is released is recorded in the reader's ledger, so a later
request for the cost matrix is checked against it too.

On the synthetic county a client reader is shown the by-type finding even after going
through the cost matrix five ways. The every-type-together finding is shown to a reader who
has seen nothing, and withheld from one who has been through the matrix first. Both are
tested.

The first build read the cost matrix after suppression instead, so that it could show
nothing a client reader could not already see. It could assess 7 units and missed the
planted one, because the matrix withholds 28 of its 32 job-and-location cells. Detection
therefore runs on the full records and the *release* of each finding is what is checked.

**Confirmed by the owner on 2026-11-16:** the client reader sees flagged units on screen
under these rules.

**Found on the way, worth knowing:**

- **The committed sample dataset cannot be used for rates.** It keeps mostly employees who
  were disciplined (223 actions among 321 people, against 521 among 3,073 in the full
  county). Detection tests generate the full county. Anyone demonstrating Patterns must run
  `make generate` first; on the sample it flags nothing, correctly.
- With two locations for a job, "higher than the other" and "the other is lower" are one
  fact. The synthetic pattern is of this kind: main detention center against the annex.

- **By type there are about seven times as many comparisons**, so a difference has to be
  clearer to be flagged. Across nine seeded counties nothing but the planted unit was ever
  flagged; the overall view found it in eight and the by-type view in seven.

### Step 2, built: the patterns in the client report

The overview's "escalation": the pattern put in front of the client's leadership with what
it costs. `reporting/content.py` (`PatternSection`, `build_patterns`), a section in the PDF
("Where discipline clusters"), a **Patterns** sheet in the workbook, and a **Patterns
working** sheet in the working papers. `_report_patterns` in `api/main.py` decides what
each copy gets.

On the full synthetic county the client's report names attendance among corrections
officers at the main detention center: 70 actions against 20.6 expected, $232,626 in the
baseline, an estimated excess of $164,168. Tests hold the report to what the Patterns page
shows the same reader, and to the cent.

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| What the client's copy carries | Flagged units only, both views, each with its arithmetic | A flag is for leadership. The list of units not flagged would give away the small groups |
| Whose rules the client's copy follows | A totals reader's, whoever asks for the file | The file is sent to the client. A lead's wider access is not a reason to put more in it |
| The working | In the working papers only: every unit assessed, and those that could not be | It is record-level work, for the roles that see records |
| Cost | The baseline's cost of those actions, and the estimated excess, with a sentence saying the estimate is in no total | The overview pairs the pattern with the Phase 1 cost. Rule 9 governs the totals |
| Cause | The report says a flag "does not say why the rate is higher" | That is Phase 3 |
| Unreviewed thresholds | The report would say they are starting values still under review | Rule 7. The owner reviewed both on 2026-11-16, so the sentence no longer appears |
| Leaving it out | `patterns=false`, and a tick box on the screen. Included unless asked otherwise | An engagement scoped to cost alone should not have to carry it |
| Nothing flagged | The section stays and says nothing stood out | Silence would read as "not looked at" |

**Worth knowing: the every-type-together flag is usually left out of the client's copy.**
The report's own matrix (job against type of misconduct) is built first, and the group
behind an every-type flag sits a few people away from several of its cells, so the
small-group rule withholds the flag and the report says "1 more stood out and is not
shown". The by-type flag for the same unit is shown. The disclosure ledger treats any two
overlapping groups a few people apart as a risk, including pairs where neither contains
the other and nothing can in fact be subtracted. It is cautious in the safe direction.
Making it exact would show more, here and in the cost matrix. **The owner decided on
2026-11-16 to keep the small-group rules as they are**, so this stays.

### Step 3, built: drift

Which units are getting worse. `detection/drift.py`, `shifted_upper_tail` in
`detection/stats.py`, `GET /detection/drift?by=misconduct|overall`, and a **Rising over the
period** section on the Patterns page. The method is in `docs/cost_methodology.md` section
11b. No new threshold: it uses the hotspot detector's two.

On the full synthetic county nothing is rising, in either view, and five seeded counties
agree. That is right: the generator plants a level, not a change. A test thins one unit's
earlier half and requires that unit, and only it, to be flagged.

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| What "over time" means | The later half of the period against the earlier half, cut by date | It needs no setting and uses every record. "The last twelve months against the year before" would need a threshold of its own |
| What a rise is compared with | The change in the rest of the organization for the same type of misconduct | A real client's early records are often thin. Against its own past alone, every unit would then be "rising" |
| Why not "the same job elsewhere", as hotspots use | Changes are compared, not rates, so jobs can be pooled, and the rest of the organization is a far steadier yardstick than one other location | The synthetic county's planted unit has one other location with 12 such actions |
| Size | The unit's later rate at least 1.5 times its earlier rate, and its rise at least 1.5 times the rest's | The first stops "held steady while others improved" being called a rise. The second stops a general rise being called the unit's |
| Thresholds | The hotspot detector's two, unchanged | A rise should not be held to a different standard from a level. The owner reviewed and kept both on 2026-11-16 |
| What counts as a comparison | Units with at least the minimum group size of actions in the period | A smaller unit can never be flagged. Counting it makes real comparisons harder to pass. On the county this is 21 comparisons, not 162 |
| Date of an action | Its decision date, as everywhere else | Undecided recent misconduct is not counted, so a rise is understated if anything |
| Falls | Listed in the working, never flagged | The question asked was what is getting worse |
| Who sees it | As hotspots. A rise from fewer than five people is withheld from a totals reader; one whose rest-of-organization figures fail is shown with them marked withheld | The finding would state a figure about those few |

**Worth knowing.** Hotspots still count every unit as a comparison, including ones too
small to flag (196 by type on the county). Applying the rule drift uses would make the
by-type hotspot view more sensitive. It was left alone because it changes results the owner
has already seen; say if it should be brought into line.

### Not built

1. ~~**Rising units in the client report.**~~ Built: see "Step 4" below.
2. **A finer view of time**: when a rise began, whether it is still going, units that fell.
3. **Other ways of grouping**: by department, bargaining unit or supervisor.
   Supervisor-level findings are findings about a named person's decisions and need the
   owner's ruling first.
4. **Client-specific thresholds.** The cost registry can be overridden per engagement with a
   reason; the detection settings cannot yet.
5. **Anything about protected characteristics.** The data model carries none, by design.

### Phase 2, step 4, built: rising units in the client report

Asked for by the owner on 2026-11-16. `RisingSection` and `build_rising` in
`reporting/content.py`, a "Rising over the period" part of "Where discipline clusters" in
the PDF, a **Rising** sheet in the workbook and a **Rising working** sheet in the working
papers. `_report_patterns` in `api/main.py` adds drift beside hotspots, decided the same
way: `for_totals_reader` for the client's copy whoever asks, `as_dict` for the working
papers. The patterns option (`patterns=false`, the tick box) covers both.

**Decided by the owner on 2026-11-16: "make it the most transparent way".** A finding is
now released in as much of it as can be. Where the unit's own groups pass the small-group
rules and the comparison's do not, the unit's own figures are shown (its actions, people
and cost, and for a rise its change against its own earlier rate), the comparison's are
marked "withheld", and the finding says which figures are left out and why. The check
itself is unchanged. On the county with a rise planted in it, the client's report now
shows the rising unit (9 actions then 33, $29,523 then $151,348, 3.91 times its earlier
rate) with the rest of the organization's figures withheld, where before it said only "1
more rose and is not shown".

**One option offered to the owner was found unsafe and not built.** "Make the check exact:
withhold only a group that one figure contains and another does not" misses three-figure
subtractions. Given figures for groups C and P that overlap, and for Q, the part of P
outside C, a reader computes C - (P - Q), which is the part of C outside P: possibly two
or three people. The present check, which treats any two groups a few people apart as a
risk, stops the first step. It stays.

## Phase 3: root cause and interventions

Opened by the owner on 2026-11-16 ("open phase 3"). His program overview describes it:
with a hotspot flagged and its cost known, people investigate on site (interviews with HR,
management and employees) to find the causes; candidate solutions are proposed and each is
priced; and management is given a cost-benefit: the discipline costs X, the intervention
would cost Y, it is projected to reduce discipline enough to save Z net. The overview says
the phase was not yet scoped into implementation detail, so the steps below are the
build's proposal and the decisions in them are his to change.

### Step 1, built: the cost-benefit of one intervention

`src/wri_engine/interventions/appraisal.py`, `POST /interventions/appraise`, and the
**Interventions** page. The method is in `docs/cost_methodology.md` section 11c.

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| Where X comes from | The cost baseline: the unit's net cost for that type, per year | The overview: "it currently costs the organization X". Nothing estimated |
| Where Y comes from | The consultant types it: one-time, yearly per person covered, other yearly, years | Every client's costs differ. A library of typical costs would be assumptions needing research and his review |
| How well an intervention works | The consultant's judgement: low, expected, high, with its basis in words. **No default, and refused without the basis** | The product has no evidence on what any intervention achieves. A made-up default would be read as a finding |
| What is shown | Net saving three ways, and the share of actions it must prevent to pay for itself | The break-even share needs no judgement, so it is the one figure nobody can argue with |
| Causes | Not recorded, not suggested | A person finds them on site. Recording them is step 2 |
| Benefits counted | Only the cost of discipline | It is the only benefit the baseline can price. The page says so |
| Discounting | None | It would be a coefficient needing a source. Horizons are capped at ten years |
| Who may use it | Engagement lead and analyst | It works on any unit, whatever its size |
| Saving the result | Not yet: worked out and shown | Where an engagement's investigation notes may be kept is a decision of its own (rule 12) |

### Step 2, built: keeping an appraisal, with what was found

`src/wri_engine/interventions/findings.py`, `config/interventions.yaml`,
`GET/POST /interventions`, `DELETE /interventions/{id}`, `GET /interventions/causes`, and
**Work it out and keep** on the Interventions page.

**The owner's ruling, 2026-11-16**, on whether the product should hold free-text
investigation notes: "short list with selection for Other and free text". So causes are
chosen from a short fixed list, and only "Other" takes words.

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| The list | Commuting burden; childcare or family-care gaps; missing training or education; inflexible scheduling; Other | The four his program overview names, and Other. It is in `config/interventions.yaml` and is his to lengthen |
| How much "Other" may hold | One line, 300 characters | His ruling was a short list, not notes. A line names a condition; a page would be interview notes |
| Must a cause be chosen to keep an appraisal | Yes, at least one | It is kept as the record of a finding. Working one out without keeping it needs none |
| Whose finding | The signed-in person's name and email, and the time, kept with it and shown with it | CLAUDE.md: the screen says whose finding it is |
| Where it is kept | `engagements/<id>/interventions/appraisals.jsonl`, not the database | The words are a client's confidential material (rule 12). A backup carries the folder |
| Changing one | Not possible. Withdraw it and keep another | The file is only ever added to (rule 15), so it is also the history |
| What is kept | Inputs only | The saving is worked out afresh from the baseline each time, so it cannot go stale |
| Who may read or write | Engagement lead and analyst | A kept appraisal gives a unit's cost whatever its size, and "Other" is about a client's workplace |
| The audit log | That a finding was kept, for which unit, with which listed causes. Never the words | The log is the installation's; the words are the client's |
| Stored-data version | Not raised | An older version ignores the new file and can still read everything else |

**Worth knowing.** Nothing stops a person typing a name into "Other". The box says not to,
it is one line, and only the lead and the analyst can read it, but it is not checked.

### Step 3, built: the cost-benefit in the client report

The overview's deliverable to management. `InterventionSection` and `build_interventions`
in `reporting/content.py`, a section in the PDF ("What was found, and what could be done"),
an **Interventions** sheet in the workbook, an **Interventions working** sheet in the
working papers, and `_report_interventions` in `api/main.py`, which decides what each copy
gets.

**The owner's ruling, 2026-11-16**, on whether the client's copy should show the words typed
for "Other" or only the label: "other". So the client's copy shows the word "Other".

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| "Other" in the client's copy | The word, never what was typed. Decided in one place, `build_interventions`, whatever it is handed | His ruling. One place, so no caller can forget |
| A finding about fewer people than the minimum group | Left out of the client's copy and counted | A cause said of three people is said of them |
| A unit's cost in the client's copy | Released like a matrix cell, against the asker's ledger. Where it may not be: the finding and the proposal stay, the money goes, and the report says so | The cost is a figure about a group. The finding is still what management needs to hear |
| The consultant's name, the intervention's name and the basis | Printed in the client's copy as typed | They are the consultant's own statements to the client. The basis is what makes a projected saving honest |
| Withdrawn appraisals | Out of the client's copy. In the working papers, marked, with who withdrew them | The working papers are the engagement's record |
| With nothing kept | No section at all | "Nothing was looked into" is not a finding |
| Leaving it out | `interventions=false`, and a tick box. Included unless asked otherwise | An appraisal may be kept before it is ready for the client |

**Worth knowing.** As with the every-type flag in Phase 2, an appraisal for a unit with
every type of misconduct together usually loses its money in the client's copy: that group
sits a few people away from cells of the report's own matrix. An appraisal for one type of
misconduct in a flagged unit is shown in full. This follows from keeping the small-group
rules as they are.

### Step 4, built: several interventions for one unit, side by side

`src/wri_engine/interventions/comparison.py`, `side_by_side` in `GET /interventions`, a
table on the Interventions page, and the same table in the report and the workbook.

**Decisions taken in the build, for the owner:**

| Decision | As built | Why |
|---|---|---|
| What is compared | Appraisals kept for the same job, location and type of misconduct, two or more | Different units cost different amounts; setting them against each other would say nothing |
| On what terms | Per year: each proposal's own cost and net saving divided by its own years | One may look three years ahead and another five |
| The order | By the share each must prevent to pay for itself, least first | It is the one figure that needs no judgement. Ordering by expected saving would rank one person's estimate over another's |
| A winner | None is named | The product supplies no evidence on what works. The choice is the client's |
| Adding savings | Never. The page and the report say they are alternatives | Two interventions aimed at the same actions do not prevent the sum of what each prevents alone |
| In the client's copy | Only appraisals shown with their money are compared | A comparison gives each cost against the unit's, so it may carry no more than the entries do |

### Not built, in the order proposed

1. **Typical costs and effects of common interventions** (transit, childcare, training,
   flexible hours), researched and cited. Every such figure is an assumption: researched,
   marked as such, and reviewed by the owner before any report uses it.
2. **Checking afterwards whether it worked**: the unit's rate after an intervention began
   against before. Drift already has the arithmetic.
3. **Interventions used together.** What two would save in combination is not the sum,
   and nothing here estimates it.

## Single sign-on

Built in `src/wri_engine/sso/oidc.py` (the OpenID Connect client), `store/db.py` (started
sign-ins, and which identity an account is tied to) and `api/accounts.py`
(`/auth/sso`, `/auth/sso/start`, `/auth/sso/complete`). Generic: any provider that
publishes a discovery document. Off unless `WRI_SSO_ISSUER` is set.

**The flow.** Authorization code with PKCE. The screen asks the API to start; the API
returns the provider's address and a one-time secret (`binding`) that the screen keeps in a
short-lived cookie. The person signs in at the provider and is sent back to the screen with
a code. The screen hands code, state and binding to the API, which exchanges the code
server to server, verifies the identity token and opens the product's own session.

**What is checked, each with a test that sends the forgery and requires a refusal:**
Signature against the provider's published keys (public-key algorithms only;
`HS256` and `none` refused whatever the header says); issuer; audience, and `azp` when
there is more than one; expiry; nonce; that the discovery document is the configured
issuer's; that every provider address is https; that the email is verified. The redirect
address is fixed in settings and never taken from the caller (rule 11).

**Decisions taken in the build, for the owner to confirm or change:**

| Behaviour | As built | Why |
|---|---|---|
| Who gets an account | Only someone a firm administrator has created an account for | Switching sign-on on must not give the whole firm access. Creating accounts from the provider's directory is not built |
| What ties a person to an account | The provider's permanent id, recorded at first sign-in | An email address can pass to a new starter; the account must not pass with it. A firm administrator can reset the link |
| Passwords after sign-on | An account's password stops working the first time it signs in through the provider | Otherwise a leaver removed at the provider could use the old password |
| Passwords altogether | Still accepted unless `WRI_PASSWORD_SIGN_IN=off` | A password-only administrator is the way back in if the provider is down; a firm can close that too |
| A leaver's open session | Lasts until it expires (`WRI_SESSION_HOURS`), or until the account is deactivated here | The provider cannot reach into this product. Signing out here does not sign out there |
| Unverified email | Refused unless `WRI_SSO_REQUIRE_VERIFIED_EMAIL=off` | Some providers never report it; the firm accepts that knowingly |

**How the sign-in is tied to the browser.** Streamlit cannot set a cookie itself, so the
screen writes it from a small embedded script and reads it back with `st.context.cookies`.
Proven in a real browser. If Streamlit changes how embedded scripts are sandboxed, the
symptom is that every sign-in ends "was not started from this browser"; the API side does
not change. A replacement screen would set the cookie in the ordinary way.

**What was run.** 39 tests against a stand-in provider (`tests/oidc_provider.py`), 6 screen
tests, and a real browser against the product and the stand-in provider served on a port:
button, provider, return, signed in; then a second browser given a pasted link, refused.

**Not done, and what the owner needs to know:**

1. **It has not been run against a real provider.** The stand-in follows the standard, but
   real ones differ in what they send. The first licensee to switch it on is the first real
   test; expect to adjust `WRI_SSO_EMAIL_CLAIM` and `WRI_SSO_REQUIRE_VERIFIED_EMAIL`.
2. **Two new libraries**: PyJWT, to verify the provider's signatures rather than hand-roll
   that, and httpx in the engine, to reach the provider. Both are locked.
3. **The server now makes outbound requests**, to the identity provider only, and only when
   sign-on is configured. The install guide says so.
4. Not supported: a provider certificate from a private authority; a shared multi-tenant
   issuer; sign-out at the provider; SAML.
5. The client secret sits in `wri.env`, which the script creates readable by its owner
   only.

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
3. **The image is about 1.0 GB** (225 MB as shipped), down from 1.1 GB and 254 MB. See
   "Fixed versions" below for what was taken out and what was left.
4. **Dependencies are now fixed** by `requirements.lock`. See "Fixed versions" below.
5. **Linux servers only**, built for `linux/amd64` by default (`--platform` changes it).
   The operator's script is bash.
6. **One installation per server.** The volume has a fixed name.
7. **No scheduled backups.** `./wri backup` is a command; running it nightly is for the
   firm's own scheduler. The guide says so.
8. **The version is now 0.3.0** (was 0.2.0, unchanged since before the private-sector
   model). It is read from one place and shown at `/health` and by `./wri status`.
9. **Before each release:** refresh the base image digest, run `make lock-upgrade`, run the tests,
   then `make package`. Nothing does this on a schedule.

## Fixed versions

`pyproject.toml` gives ranges (`fastapi>=0.110`), so two builds a month apart could hold
different libraries, and a developer's environment could differ from the image. Now:

| File | What it fixes | Used by |
|---|---|---|
| `requirements.lock` | The engine, the API and the screen: 56 libraries, each at one version, each with SHA-256 fingerprints | The image (`Dockerfile`) |
| `requirements-dev.lock` | The same versions, plus the tools for testing | `make install` |
| `Dockerfile` `PYTHON_IMAGE` | The base image, by digest | The image |

- **A download that does not match its fingerprint stops the install.** Tried: one changed
  character in a fingerprint and pip refuses the file.
- **`make lock`** brings the locks in line with `pyproject.toml` and keeps every version
  already chosen; **`make lock-upgrade`** chooses them all again (both need `uv`). The
  second is run on purpose: a security fix, before a release. The first, after adding a
  dependency. Either way: then `make install`, `make test`, commit
  both files. `tests/test_dependencies.py` fails if the locks and `pyproject.toml` disagree,
  if the two locks differ on a shared library, or if the environment the tests run in is
  not the locked one.
- **`make install` works with `uv` or with plain `pip`**; both were run from scratch and
  gave the same 66 libraries at the same versions.
- **The base image digest does not refresh itself.** Before a release, pull
  `python:3.12-slim`, read its digest, and put it in the `Dockerfile` (the comment there has
  the two commands). That is how operating-system security fixes come in. Left to itself it
  goes stale; that is the trade for a build that is the same every time.

**What came out.** DuckDB was declared and imported nowhere: 65 MB. pip is removed from the
finished image: 13 MB, and nothing should install into a running installation. pandas and
httpx moved from the engine's dependencies to the screen's, where they are used; the image
holds both either way, so that changed the declaration and not the size. A test now holds
each declared library to being imported by the part that declares it.

**What was left, and why.** pyarrow (170 MB), pydeck and altair are required by Streamlit
itself and cannot be dropped while the screen is Streamlit. uvloop and httptools (25 MB) make
the API faster and were left. Stripping test folders out of installed libraries would save
perhaps 40 MB and was not done: it means editing other people's packages after install.
The screen is the disposable layer (see `CLAUDE.md`); replacing Streamlit is what would
halve the image.

**Not fixed.** setuptools, the tool that packs the product's own code during a build, is
fetched at whatever version is current. It is used for that step only and is not in the
image.

## Audit log

Every request was already written to `audit_log.jsonl`, one line each. Nothing stopped a line
being changed or removed afterwards without trace. Now each entry carries a sequence number,
the hash of the entry before and its own hash (`src/wri_engine/hashchain.py`, shared with the
license usage log). `wri-admin audit` (`./wri audit` in an installation), `wri-admin status`
and the firm administrator's Engagement page say whether it holds together.

- **What shows:** an entry changed, removed, inserted or moved; a line cut short by a crash
  (reported, and the log carries on after it).
- **What needs a head noted earlier:** entries removed from the end, or the whole log
  rebuilt by someone with full access to the server. `--expect-head SEQ:HASH` checks the
  log still holds an entry noted earlier. The install guide tells the firm to note the head
  somewhere outside the server from time to time.
- **Older logs:** lines written before this change are kept as they are and protected as
  one block: the first chained entry carries a hash of everything before it.
- **Not done: writing the log anywhere the server's administrators cannot reach** (a
  separate log server, write-once storage). That is the step from tamper-evident to
  tamper-proof. It depends on what each firm runs, so it is a configuration item for the
  install package, not engine code. Raise it if a licensee asks.
- One process writes the log. The install runs one API worker, so that holds; a second
  worker would need the chain's position kept somewhere both can see. If the file is not as
  the product left it (put back from a backup, or written to by something else), the next
  entry carries on from what the file holds.
- **A version from before this change, run against a chained log, appends unchained lines,
  and the check reports each one.** That is accurate, and it will happen to a developer who
  runs an older checkout against the same `data/` folder. It is why the tests now write to
  a log of their own (`tests/conftest.py`) and never to `data/audit_log.jsonl`. Found the
  hard way: a full test run on an older branch left such lines in the local log, and a
  screen test on the newer branch then failed on it.
- The API now writes the log where `wri-admin audit` and the backup read it (the
  installation's data directory, or `WRI_AUDIT_LOG`). Before, the two agreed only when the
  data directory came from the environment, which it does in the container.

## Small groups: totals as well as cells

**Found on 2026-11-16, in what was already merged.** A reader who sees totals only could
filter the cost matrix down to a single action. Every cell came back withheld, and the
grand total and the "Other (suppressed)" line beside them carried that one person's cost,
on screen and in the CSV download. Nothing suggests anyone did this; the data so far is
synthetic. It broke the product's own rule, so it was fixed without waiting.

**The rule now**, in `aggregation/rollups.py`: every figure is about a group of people, and
none is released for fewer than the engagement's minimum.

- **The grand total of a selection.** Fewer than the minimum number of people selected:
  nothing about the selection is released, no cell, no line total, no grand total, no
  bucket. The response says `"withheld": true` and carries no money.
- **A filtered total and the disclosure ledger.** The unfiltered total is on the summary
  page for every role, so it always counts as seen. A filtered selection that leaves out
  one to four people, or sits that close to anything else the person has been shown, is
  withheld in the same way.
- **Row and column totals.** A total about fewer people than the minimum is withheld. Two
  withheld cells in a row can be the same one person in two years.
- **The summary's department list** leaves out a department whose total is withheld.
- A group a person has already been given a figure for may be shown to them again.

**What this does not settle, for the owner.** The matrix protects a withheld cell by
making sure no row or column holds exactly one of them. That is a rule about the number of
cells, not the number of people: two withheld cells can still add up to two or three
people, and their combined figure can be worked out from the totals around them. One
withheld row total can likewise be worked out from the grand total and the other rows.
Closing those properly means deciding what may be released by people, not by cells, across
every figure on a page at once. It would withhold noticeably more of the matrix, so it is
his decision and its own piece of work. Also: the ledger compares groups of people, so it
cannot see two questions about the same people that differ by one action.

**Decided by the owner on 2026-11-16: keep the rules as they are.** Neither tightened as
above nor loosened (see the ledger's caution under "Phase 2: detection"). The limits in
this section therefore stand, and are stated here so that nobody takes them for closed.

## Disclosure ledgers

The differencing guard's record of what each person has been shown used to live in memory
only, so a restart wiped it and two overlapping questions asked either side of one could be
subtracted. It is now kept on disk: `src/wri_engine/aggregation/ledger_file.py`, one file
per person per engagement in `engagements/<id>/disclosure/`, written and flushed before the
figures leave the server. If it cannot be written the request fails and nothing is released.

- **In the engagement folder, not the database.** The record is a list of the client's
  employee ids, which is client data (rule 12). Backups carry it with the engagement.
- **It lasts as long as the engagement**, by the owner's decisions of 2026-11-15. It
  carries across a new or corrected client file, a changed client-specific value and a
  different occupation group: each moves some figures and leaves others exactly as they
  were, so a person shown figures before and after could otherwise subtract the parts that
  did not move. Only a different minimum cell size (or sector) starts a new record; earlier
  entries are kept on disk.
- **The cost, which a consultant will meet.** A person sees more cells held back as an
  engagement goes on. After a corrected file, every cell whose group changed by one to four
  people is held back for everyone who saw it before the correction, for good; a person who
  had not seen it is shown it. **Nothing resets the record.** If that proves too strict in
  use, the remedy to build is a reset by the firm administrator that is written to the
  audit log, not a quiet loosening of the rule.
- **Check and record are now one step per person.** Two requests from the same person at
  the same moment used to be able to both pass the check before either recorded what it
  released. Fixed in the same change.
- Tested by asking the same two overlapping questions in one sitting and across a restart
  and requiring identical answers; with persistence switched off that test fails.

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
