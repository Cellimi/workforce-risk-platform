# Demo script — 5 minutes, for a CEO candidate

**Setup before they arrive.** Two terminals: `make api`, then `make ui`. Open
<http://localhost:8501> on the Executive summary page, role `executive`, sensitivity `base`.
Have the Assumptions page open in a second tab — you will jump to it at minute four.

**Say this once, at the top, and don't bury it:** *this is synthetic data for a fictional
county.* It is on every page, but say it out loud anyway. The credibility of the whole
conversation rests on them believing the numbers when they are real, so be visibly strict
about it when they are not.

---

## 1. The problem — 30 seconds

> "Every agency in the country tracks discipline. They track it as paperwork: who did what,
> what the penalty was, whether it stuck. Ask any of them what it **cost** and you get a
> shrug — not because they don't care, but because the cost is scattered across six budgets
> that never talk to each other. Overtime is in one place, HR time is nowhere, the academy
> class that replaces the deputy you fired is in the training budget next fiscal year.
>
> Nobody can put a number on it. So it never gets managed."

Don't sell the product yet. Let them sit with the gap for a beat.

---

## 2. The headline — 60 seconds

*Executive summary page.*

> "Harlow County — 2,500 employees, eight departments — spends **$1.76 million a year** on
> discipline. That's **$698 per employee per year**, every year, whether or not anyone is
> looking at it.
>
> And **44% of it is turnover**. Not the discipline process — the cost of replacing the
> people who get removed. Recruiting, background investigations, the academy, field training,
> and the overtime covering the post until the replacement is on the street alone."

Point at the component chart.

> "C1 is staff time on the paperwork. C2 is paid administrative leave. C3 is the overtime
> backfilling minimum-staffing posts. C4 is appeals. C5 is turnover — and it dwarfs the rest."

Point at the red bar.

> "That negative bar matters. Unpaid suspensions genuinely save the county the wage — about
> $487,000 over three years. We show it, we don't net it away quietly. Gross, offset, net,
> always separately. If we hid that, you'd be right not to trust the rest."

**If they ask where $698 comes from:** open the expander. Don't paraphrase it — click it.
That's the differentiator, and it works better shown than described.

---

## 3. The matrix — 60 seconds

*Cost matrix page.*

> "Now the same money, cut by who and by what."

Set **Rows = Department** (the default role-family view is honest but sparse — 20 role
families over 500 actions means most cells are suppressed).

> "Two things jump out. The Sheriff's Office spends **$1,753 per employee per year** on
> discipline; Public Works spends **$214**. Same county, same HR policy, eight times the cost
> per head.
>
> And the biggest single block of actions in the county is **the Detention Center on
> attendance** — 83 actions, about $447,000, roughly $5,400 each. Switch Rows back to
> Employee type and it's Corrections Officers carrying almost all of it."

Then be careful, because this is the honest bit and it lands well:

> "Notice what the product just did and didn't do. It showed you an expensive cell. It did
> **not** tell you there's a problem at the detention center, or why, or what to do. That's
> Phase 2 and Phase 3. Phase 1 earns the right to have that conversation by getting the
> arithmetic right first."

**Point out the suppressed cells (`···`).** Don't skip this — an operating CEO will be asked
about it by counsel within a week of any real deployment:

> "Cells backed by fewer than five distinct employees aren't shown. That isn't a UI
> preference — the API never sends the number. The totals still reconcile, because the hidden
> money sits in an 'Other (suppressed)' bucket. And if hiding one cell would let you back it
> out by subtracting from a row total, we hide a second one."

---

## 4. Show the work — 60 seconds

*Switch role to `hr_analyst` in the sidebar, then the Drill-down page.*

> "Watch what happens when I change role. Aggregates only for an executive; an HR analyst
> gets records, with the employee IDs pseudonymized. That's enforced in the engine — an
> executive asking for this screen gets a 403 from the API, not a greyed-out button."

Pick the Deputy / Time & Attendance falsification cell, then the top action.

> "One removal. **$259,000.** Here is every dollar of it."

Open three line items, in this order:

1. **`C1 supervisor hours`** — "30 hours of supervisor time at $67.27 an hour, and it names
   the sergeant whose rate that is, because the record identified him."
2. **`C2 paid admin leave`** — "62 calendar days of paid leave. The engine converts that to 31
   actual shifts using this role's 12-hour rotation, not 62 days of pay. Then look at the
   next line."
3. **`C3 backfill admin leave`** — "The same 31 shifts, again, as overtime. **The county pays
   twice.** Once to the deputy for not working, once to whoever covers the post. That number
   is the one that makes chiefs sit up, and it's the one no existing system produces."

Then scroll to the C5 block.

> "And this is the replacement: $76,000 of vacancy overtime, $61,000 of academy salary,
> $39,000 of field training. Every one of them divided by one-minus-the-washout-rate, because
> you don't hire one recruit to fill one seat — you hire 1.33."

> "Every line item names its formula, its inputs, and the assumptions it used. There is no
> step in this product where a number appears that you can't take apart."

---

## 5. Change an assumption — 45 seconds

*Switch role to `admin`. Assumptions page.*

> "Here's the part that decides whether this is a consulting deliverable or a product.
>
> 166 coefficients. Every one carries its source, its confidence, and who owns it. The amber
> rows — 151 of them — are still my estimates, and they're marked as mine until a customer
> confirms them. The benefits multiplier isn't: that's BLS Employer Costs for Employee
> Compensation, March 2026, with the table and release date in the file."

Drag `c5_washout_rate_sworn_deputy` from 0.25 toward 0.40 and hit **Run scenario**.

> "Every customer's cost structure is different. Their academy is longer, their washout rate
> is worse, their arbitrations cost more. So none of it is hardcoded — I move a slider, the
> engine recomputes every line item from scratch, and you see before, after and the
> difference. Nothing on disk changed; that was one run."

If you have a spare beat, drag the sensitivity slider in the sidebar from `base` to `high`:

> "And the whole model runs at low, base and high on every assumption at once. When I show a
> customer a number I can show them the range around it."

---

## 6. What's next — 45 seconds

> "This is the foundation, and it's deliberately the least exciting part.
>
> **Phase 2 is detection** — patterns, hotspots, which units are drifting. That only means
> anything if you can already say what a pattern costs, which is what we just built.
>
> **Phase 3 is intervention cost-benefit** — you can't tell an agency a supervisor training
> program pays for itself until you have a baseline it can pay for itself *against*.
>
> The engine is source-agnostic by design. Today it reads a county HR export. The adapter for
> a federal payroll system is designed and stubbed — the field mapping is written down. The
> cost logic doesn't change when the source does, because the engine only ever sees one
> canonical schema."

Close on the thing that actually matters:

> "The differentiator isn't that we can produce a number. Anyone can produce a number. It's
> that every number decomposes to a formula, an input and a cited assumption — so when a
> county administrator or a union or a county attorney pushes back, we open the drill-down
> instead of defending a black box."

---

## Questions you should expect, and honest answers

**"Are these real numbers?"**
No. Synthetic data, fictional county, and 151 of 166 coefficients are my estimates awaiting
confirmation. The engine, the schema and the assumptions layer are production foundations;
the values are not. The one sourced figure is the benefits multiplier, and it carries its
citation.

**"Isn't paying twice for administrative leave double counting?"**
No — it's two different payments. The employee is paid not to work, and a second employee is
paid overtime to cover the post. The only way to avoid the second one is to leave the post
unstaffed, which a minimum-staffing rule forbids. Where there is no minimum staffing
requirement, the engine doesn't charge backfill at all.

**"What about 7(k)?"**
A real simplification, and it's documented. The demo treats every backfill hour as overtime.
Under a 7(k) work period there can be straight-time capacity inside the period first, so this
overstates backfill for law enforcement, corrections and fire. Fixing it needs hours-worked
data by work period, which a discipline extract doesn't carry.

**"Why is the sworn benefits multiplier a guess when the civilian one is sourced?"**
Because I could reach ECEC Table 1 and not Table 3. The public-safety figure is in Table 3,
protective service occupations. It's flagged low confidence, its low bound is the
all-occupations number, and it's open item 1 before this is shown to a customer.

**"Can I see one employee's record?"**
Depends who you are, and the engine decides, not the screen. Executive: no. HR analyst:
pseudonymized. Admin: yes. Every request is written to an append-only audit log first.

**"How long to point this at our data?"**
One adapter plus a mapping file. The canonical schema doesn't move, the cost engine doesn't
move, and the validation rules come along for free. The longer job isn't the integration —
it's sitting down with your HR director and turning 151 amber rows green.
