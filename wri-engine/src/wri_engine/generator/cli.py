"""Command line entry point for the synthetic data generator.

python -m wri_engine.generator.cli --seed 20260917 --out data/synthetic
"""

from __future__ import annotations

import argparse
from pathlib import Path

from wri_engine.generator.generate import generate
from wri_engine.paths import SYNTHETIC_DIR


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate the Harlow County synthetic HR export.")
    parser.add_argument(
        "--seed",
        type=int,
        default=20260917,
        help="random seed; the same seed always produces identical files",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=SYNTHETIC_DIR,
        help="output directory for the five source CSVs and manifest.json",
    )
    parser.add_argument(
        "--sample-out",
        type=Path,
        default=None,
        help="also write a trimmed, committable sample export to this directory",
    )
    parser.add_argument(
        "--sample-employees",
        type=int,
        default=120,
        help="number of employees to keep in the sample export",
    )
    return parser


def _trim(export, keep: int):
    """A small but internally consistent slice, for committing beside the code.

    Employees are taken round-robin across job classes from those who actually have a
    discipline action, so the sample covers every role family rather than the first 120
    deputies. Supervisors and named deciding officials are pulled in too, so C1 can still
    cost their hours at their own rate.
    """
    from wri_engine.generator.generate import GeneratedExport

    by_id = {r["EMP_NBR"]: r for r in export.employees}
    with_actions: dict[str, list[str]] = {}
    for row in export.actions:
        emp = by_id.get(row["EMP_NBR"])
        if emp:
            with_actions.setdefault(emp["CLASS_CD"], []).append(row["EMP_NBR"])

    chosen: list[str] = []
    buckets = [list(dict.fromkeys(v)) for _, v in sorted(with_actions.items())]
    index = 0
    while len(chosen) < keep and any(index < len(b) for b in buckets):
        for bucket in buckets:
            if index < len(bucket) and len(chosen) < keep:
                chosen.append(bucket[index])
        index += 1

    ids = set(chosen)
    for emp_id in list(ids):
        emp = by_id[emp_id]
        if emp["SUPV_EMP_NBR"] in by_id:
            ids.add(emp["SUPV_EMP_NBR"])
    for row in export.actions:
        if row["EMP_NBR"] in ids and row["DECIDE_EMP_NBR"] in by_id:
            ids.add(row["DECIDE_EMP_NBR"])

    employees = [r for r in export.employees if r["EMP_NBR"] in ids]
    actions = [r for r in export.actions if r["EMP_NBR"] in ids]
    action_ids = {r["ACTN_NBR"] for r in actions}
    sample = GeneratedExport(
        employees=employees,
        actions=actions,
        admin_leave=[r for r in export.admin_leave if r["ACTN_NBR"] in action_ids],
        appeals=[r for r in export.appeals if r["ACTN_NBR"] in action_ids],
        separations=[r for r in export.separations if r["EMP_NBR"] in ids],
    )
    sample.manifest = {
        **export.manifest,
        "sample": True,
        "sample_note": (
            "A trimmed slice of the full export, kept in version control so the project is "
            "runnable without regenerating. Use `make generate` for the full dataset."
        ),
        "full_export_counts": export.manifest["counts"],
        "counts": {
            "employees": len(sample.employees),
            "active_employees": sum(1 for r in sample.employees if not r["TERM_DT"]),
            "actions": len(sample.actions),
            "admin_leave_periods": len(sample.admin_leave),
            "appeals": len(sample.appeals),
            "separations": len(sample.separations),
        },
    }
    return sample


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    export = generate(seed=args.seed)
    written = export.write(args.out)
    counts = export.manifest["counts"]
    print(f"Harlow County synthetic export (seed {args.seed}) -> {args.out}")
    for key, value in counts.items():
        print(f"  {key:>22}: {value:,}")
    injected = sum(export.manifest["injected_data_quality_issues"].values())
    print(f"  {'injected DQ issues':>22}: {injected}")
    for path in written:
        print(f"    wrote {path}")
    if args.sample_out:
        sample = _trim(export, args.sample_employees)
        sample.write(args.sample_out)
        sample_actions = sample.manifest["counts"]["actions"]
        print(f"  sample export -> {args.sample_out} ({sample_actions} actions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
