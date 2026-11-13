# Workforce Risk Intelligence

> **Published for viewing. Not licensed for use.** Copyright (c) 2026 Michael V. Celli.
> All rights reserved — see [LICENSE](LICENSE). Reading this repository grants no right to
> use, copy, modify or distribute the code.
>
> **All data here is synthetic.** The county, the company and the people in it are invented.
> Nothing in this repository is derived from any real employer's records.

Workforce Risk Intelligence (WRI) reports **what employee discipline actually costs an
employer**, and shows where every dollar came from.

It joins HR records to discipline records and costs each action: the hours spent processing
it, paid leave during an investigation, cover for the absence, challenges to the action, and
the cost of replacing someone who is removed. A cost is counted only when the record shows
the event happened. Nothing is predicted or probability-weighted, and every figure carries
its formula, its inputs and the assumptions behind it.

## Where everything is

**The product is in [`wri-engine/`](wri-engine/). Start with
[`wri-engine/README.md`](wri-engine/README.md).**

| Folder | What it is |
|---|---|
| [`wri-engine/`](wri-engine/) | **The WRI engine: the product.** The cost model for public-sector and private-sector employers, the assumptions registry with a source for every coefficient, intake of a client's own files, sign-in and per-client engagements, the API and the screen. |
| [`legacy/`](legacy/) | The first prototype, from February 2026: a dashboard that forecast unit-level risk. **Not the product and not being developed.** Kept for reference; see [`legacy/README.md`](legacy/README.md). |

```bash
cd wri-engine
make install     # set up the environment
make test        # run the test suite
make demo        # the synthetic walkthrough
```

## Why there is a `legacy/` folder

The February prototype and the engine answer different questions. The prototype asked which
units were *likely* to be in trouble in the next 30 to 90 days, from weekly unit totals. The
engine asks what discipline *did* cost, from individual records, and deliberately contains no
forecasting or risk scoring in its first phase. They share no code.

The prototype is kept so the earlier thinking is on record, and moved out of the top level so
that this repository reads as one project. If forecasting is taken up later it will be built
in the engine, on the engine's data, rather than ported from the prototype.

## One more move to come

The engine will move from `wri-engine/` to the top level of this repository once the pull
requests currently open against it have been merged. Moving it sooner would put every one of
them into conflict. Until then, everything about the engine is under `wri-engine/`.

## License

Copyright (c) 2026 Michael V. Celli. All rights reserved. See [LICENSE](LICENSE).

The code in this repository is **published for viewing, not licensed for use**. `wri-engine/`
carries its own copy of the same license so that directory remains self-contained.
