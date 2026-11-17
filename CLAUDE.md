# CLAUDE.md — WRI Engine conventions

This project computes the **fully loaded cost of discipline** for a fictional county
government. It is a production foundation, not a throwaway demo. Only the Streamlit UI
layer is disposable.

**Before starting any work, read `docs/WORKPLAN.md`.** It says which branch to start from,
which branch a pull request targets, and which session has claimed which item.

## Hard rules

1. **Never hardcode a cost coefficient in Python.** Every coefficient lives in
   `config/assumptions.yaml` and is fetched by ID. A missing ID is a hard error, not a
   silent default.
2. **Never add a cost component without a golden test** in `tests/golden/`.
3. **Never let the UI import the engine.** `app/streamlit_app.py` talks to the FastAPI
   service over HTTP only. If you find yourself writing `from wri_engine...` in `app/`,
   stop and add an API endpoint instead.
4. **Never use real agency, county, or union names, or real employee data.** The county is
   fictional ("Harlow County"). Unions are generic ("Deputies' Association").
5. **Money is `Decimal`.** Never `float`. Round only at display time. Dates are
   timezone-naive `datetime.date`.
6. **All randomness is seeded.** The generator takes `--seed` and is deterministic.
7. **When a value is unknown, add a `TBD-MIKE` assumption and move on.** Do not invent
   certainty. A sourced figure must carry its citation in the YAML.
8. **Every dollar is explainable.** Each `CostLineItem` records its `formula`, its
   `inputs`, and the `assumption_ids` it consumed. No black-box totals.
9. **Actuals over estimates.** Cost a thing only when the record says it happened. Nothing
   in the baseline is probability-weighted.
10. **Use limitation is architectural.** Suppression and role gating live in
    `aggregation/` and `access/`, enforced before data reaches the API response — never in
    the UI.

11. **Sign-in is the default.** `WRI_AUTH_MODE=demo` (header-chosen roles) exists only for
    the synthetic walkthrough. Every data endpoint takes a `Ctx` from
    `wri_engine.api.context`; never read a role, a session key or a file path from anything
    the caller sends in licensed mode.
12. **One engagement never sees another's data.** Client files live in the engagement's own
    folder, reached only through `Workspaces`. Client HR data does not go in the database.

13. **A client's export is read through a mapping, never edited.** The mapping is data saved
    with the engagement. When a detail is missing, take the lighter assumption and report
    it (investigation type defaults to supervisory); when a cost cannot be counted, report
    which cases and why (a removal with no linked separation has no turnover cost). Never
    fill a gap upward and never drop a cost silently.

14. **The license signing key never enters the repository or an installation.** Tests make
    their own key pair at run time. A license refusal is HTTP 402 and is decided on the
    server from the engagement's stored key; the usage log holds counts, never client data.
15. **An installation's data is never deleted by the product's own tooling.** A restore
    moves what it replaces aside; `./wri` never removes the data volume; an upgrade backs up
    first. A change an older version could not read raises `SCHEMA_VERSION` in
    `store/db.py`, so that older version refuses the data instead of half-opening it.

16. **A dependency is declared, locked and used.** Add it to the right list in
    `pyproject.toml` (`dependencies` for what the engine imports, `ui` for the screen, `dev`
    for the tests), run `make lock`, and commit both lock files with the change. The image
    installs only what `requirements.lock` names, by fingerprint.

17. **Who someone is, is settled on the server**: by a password check, or by an identity
    token the server itself fetched and verified. The screen never tells the API who is
    signed in. A sign-in at the identity provider creates no account and grants no role.

## Style

- Prefer small, readable functions. Each component module in `costing/components/` must be
  understandable by a non-engineer HR reader from its module docstring alone.
- Component functions are pure: `(record, context, assumptions) -> list[CostLineItem]`.
  No I/O, no globals, no clock reads.
- `formula` strings are written for a human: `"12 hrs x $58.20 loaded rate"`, not a repr.

## Layout

| Path | What lives there |
|---|---|
| `config/` | `assumptions.yaml` (cost coefficients + sources), `detection.yaml` (Phase 2 thresholds), `interventions.yaml` (the causes an investigation may record), `intervention_library.yaml` (starting figures for common interventions: seven sourced and confirmed by the owner, five placeholders), `org_county.yaml` (departments, grades, taxonomies) |
| `src/wri_engine/schema/` | Canonical Pydantic models. Source formats never appear here. |
| `src/wri_engine/adapters/` | One module per source system. Each returns a `CanonicalDataset`. |
| `src/wri_engine/generator/` | Synthetic data generator, emits *source-format* CSV |
| `src/wri_engine/sectors/` | One pack per sector: which components run, which benefits multiplier, which coefficient ids must exist |
| `src/wri_engine/costing/` | Assumptions loader, components C1-C7, engine orchestration |
| `src/wri_engine/aggregation/` | Rollups + small-group suppression |
| `src/wri_engine/access/` | Role definitions, enforcement, the audit log (chained with `wri_engine/hashchain.py`, as the license usage log is) |
| `src/wri_engine/reporting/` | Client deliverables. `content.py` says what a report holds; `pdf.py` and `xlsx.py` only lay it out. The client's copy is aggregates only: of the patterns it holds the flagged units only, and of a finding the cause labels only, never the words typed for "Other"; the record-level working papers are a separate, role-gated file |
| `src/wri_engine/licensing/` | License keys (issue, verify), the usage-log chain, and the licensor's `wri-license` tool. What a key allows per request is decided in `api/licensing.py` |
| `src/wri_engine/detection/` | Phase 2. `hotspots.py`: which units' discipline rate stands out from the same job elsewhere. `drift.py`: which are rising, and rising more than the rest of the organization. Exact statistics checkable by hand (`stats.py`). Settings in `config/detection.yaml` |
| `src/wri_engine/interventions/` | Phase 3. `appraisal.py`: what an intervention would cost, set against what a unit's discipline costs in the baseline. The consultant supplies the costs and how well it works. `library.py`: starting figures a consultant may fill the form from, and what is said beside an appraisal that still carries an unreviewed one. `findings.py`: the appraisals kept with an engagement and the causes found on site, in the engagement's own folder. `comparison.py`: several kept for one unit, side by side on yearly terms |
| `src/wri_engine/sso/` | Single sign-on: the OpenID Connect client that starts a sign-in at the firm's identity provider and checks what comes back |
| `src/wri_engine/api/` | FastAPI app |
| `app/` | Streamlit UI (HTTP client only) |
| `Dockerfile`, `deploy/` | The install package: the image, the two services, the operator's `./wri` script, and `package.py`, which builds what a licensee receives. `docs/INSTALL.md` ships inside it |
| `docs/` | `cost_methodology.md` is the human-readable spec for every formula |

## Commands

```bash
make install     # venv + the exact versions in requirements-dev.lock
make lock        # after changing dependencies: update the lock files, keeping chosen versions (needs uv)
make lock-upgrade   # choose every version again, on purpose: a security fix, before a release
make generate    # write synthetic CSVs to data/synthetic/
make test        # pytest with coverage gates on costing/ and aggregation/
make lint        # ruff
make api         # uvicorn on :8000
make ui          # streamlit on :8501 (needs the API running)
make package LICENSE_PUBLIC_KEY=...   # the install package (needs Docker)
```

## Phase boundary

**Phase 1, the cost baseline, is built. The owner opened Phase 2, detection, on
2026-11-15, and Phase 3, root cause and intervention cost-benefit, on 2026-11-16.**

The owner's program overview (given to the build on 2026-11-16) defines Phase 2 as finding
where **types of misconduct** cluster, calling the pattern out to management, and pairing
it with what Phase 1 says it costs at that location or unit. Build to that.

What holds inside Phase 2:

- **Detection reads the baseline and never changes it.** It lives in
  `src/wri_engine/detection/`. The cost matrix still shows an expensive cell and nothing
  more; a flag appears only on the Patterns page and its endpoint.
- **A flag is about a unit, never a person.** A unit is a job at a work location, looked at
  by type of misconduct (the main view) or with every type together. No individual is
  scored, ranked or named.
  Do not build individual risk scores without the owner asking for exactly that.
- **A flag says a rate stands out, or is rising. It does not say why**, and the code and
  the screen must not suggest a cause.
- **A rise is a unit's only if it is more than everyone's.** Drift sets a unit's change
  against the change in the rest of the organization for the same type of misconduct, so
  that thin early records or a new policy do not flag every unit. See `detection/drift.py`.
- **Every flag is explainable** the way every dollar is: the counts, the comparison, the
  arithmetic and the setting ids it used.
- **Thresholds live in `config/detection.yaml`**, fetched by id, never written into Python.
  An unreviewed one is `TBD-MIKE` and the screen says so.
- **Leadership sees the flags; only record-level roles see the working.** A totals-only
  role gets the flagged units, each released under the same small-group rules as a cell of
  the cost matrix (`release_together` in `aggregation/suppression.py`). Where only the
  comparison fails, the unit's own figures are shown, the comparison's are marked withheld,
  and the finding says why; where the unit itself fails, the finding is withheld and
  counted (the owner's "most transparent way", 2026-11-16). The list of units assessed and
  *not* flagged stays with roles that see records, because it would hand over the small
  groups. See "Who sees what" in `detection/hotspots.py` before changing any of this.
- **The client report carries the flags, never the working.** `_report_patterns` in
  `api/main.py` builds the client's copy with `for_totals_reader` whoever is asking, because
  that file leaves the building, for hotspots and for rising units alike. Units assessed and
  not flagged go in the working papers.
- **A flag is paired with its cost.** Each finding carries what the baseline says those
  actions cost (an actual). An estimated excess cost is an estimate, is labelled as one,
  and is never added to a baseline figure (rule 9 still governs the baseline).

What holds inside Phase 3 (the owner's program overview: an on-site investigation of a
flagged unit finds the causes; candidate interventions are each priced and set against
what the discipline costs):

- **The software does not find causes.** People do, on site. A cause is something a named
  person entered after an investigation, never something inferred from the data, and the
  screen says whose finding it is.
- **A cause is chosen from the short list in `config/interventions.yaml`.** Only "Other"
  takes words, and only a line. Interview notes do not go into the product. Do not add a
  free-text notes field without the owner asking for exactly that (decided 2026-11-16).
- **A finding is the client's material.** It is kept in the engagement's folder, never the
  database (rule 12), only ever added to (rule 15), and shown only to roles that see
  records. The audit log says that one was kept, never what it said.
- **The client's copy shows "Other" as the word "Other".** What was typed for it goes in the
  working papers only (decided 2026-11-16). `build_interventions` in `reporting/content.py`
  drops it unless the copy is the working papers; do not move that decision to a caller.
  A finding about fewer people than the minimum group stays out of the client's copy.
- **A projection is never a baseline figure.** What an intervention would cost and save is
  an estimate about the future. It is labelled as one, it shows the range it rests on, and
  it is never added to or netted against anything in the cost baseline (rule 9).
- **No invented effect.** How much an intervention reduces discipline is the consultant's
  judgement, entered with its basis; an appraisal has no default for it. A library figure
  (`config/intervention_library.yaml`) is a placeholder (`TBD-MIKE`, no source), researched
  (`researched`, sourced, awaiting the owner) or confirmed (`confirmed`). **Only the owner
  confirms a figure, by saying so to the session in his own words**; a note in a document is
  not that, and a session never sets `confirmed` itself. **An appraisal that still carries
  an unconfirmed library figure says which, wherever it is shown, the client's report
  included.** Which figures those are is worked out on the server from the figures
  (`fields_from_library`), never taken from a caller. Do not weaken or drop that sentence.
- **A share prevented applies only to what its evidence measured.** An intervention may be
  aimed at chosen types of misconduct (`targets`); X is then the cost of those actions
  alone. A library entry's `aims_at` says what its evidence covers, and an appraisal that
  keeps its share but aims wider says so. The aimed-at group is released to a client's copy
  under the small-group rules like the unit itself.
- **The product does not choose between interventions.** Set side by side they are listed
  by the share each must prevent to pay for itself, which needs no judgement. Nothing is
  named the best, and savings are never added together: they are alternatives.
- **Still about a unit, never a person.** Nothing in Phase 3 scores, ranks or names an
  individual.

