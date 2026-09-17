"""Synthetic data generator for Harlow County.

Produces the county HR system's *source-format* export -- five CSVs with the county's own
column names, code values and date format -- so that loading the demo exercises the adapter
rather than bypassing it.

The data is synthetic. Distribution parameters come from `config/generator_profile.yaml`
and are each explained in `README.md` beside this file. Nothing here is derived from any
real agency's records.

Determinism: every random draw goes through one seeded `random.Random`. The same seed and
the same config always produce byte-identical CSVs.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

from wri_engine.orgconfig import OrgConfig, RoleFamily, default_org_config
from wri_engine.paths import CONFIG_DIR
from wri_engine.generator.profile import GeneratorProfile, load_generator_profile

MAPPING_FILE = CONFIG_DIR / "mapping_county_hr_csv.yaml"
DATE_FMT = "%m/%d/%Y"

EMPLOYEE_COLUMNS = [
    "EMP_NBR", "DEPT_CD", "CLASS_CD", "POSN_TITLE", "GRADE", "STEP", "ANNL_SAL", "FLSA_CD",
    "BARG_CD", "SWORN_IND", "MIN_STAFF_IND", "SHIFT_HRS", "WORK_LOC", "SUPV_EMP_NBR",
    "HIRE_DT", "TERM_DT", "TERM_RSN_CD",
]
ACTION_COLUMNS = [
    "ACTN_NBR", "EMP_NBR", "INCDT_DT", "PROP_DT", "DECN_DT", "MISCND_CD", "MISCND_SUB",
    "ACTN_CD", "SUSP_DAYS", "SUSP_BEG_DT", "INVEST_IND", "INVEST_TYP", "DECIDE_LVL",
    "DECIDE_EMP_NBR", "POSN_ABOLISH_IND",
]
LEAVE_COLUMNS = ["LEAVE_NBR", "EMP_NBR", "ACTN_NBR", "BEG_DT", "END_DT", "PAID_IND"]
APPEAL_COLUMNS = [
    "APPL_NBR", "ACTN_NBR", "FORUM_CD", "FILED_DT", "RESOLV_DT", "OUTCOME_CD",
    "BACKPAY_AMT", "SETTLE_AMT", "OC_HOURS",
]
SEPARATION_COLUMNS = [
    "EMP_NBR", "TERM_DT", "TERM_RSN_CD", "ACTN_NBR", "REFILL_IND", "REFILL_DT", "ABOLISH_IND",
]


@dataclass
class GeneratedExport:
    """The five source tables plus a manifest, as lists of plain dicts."""

    employees: list[dict] = field(default_factory=list)
    actions: list[dict] = field(default_factory=list)
    admin_leave: list[dict] = field(default_factory=list)
    appeals: list[dict] = field(default_factory=list)
    separations: list[dict] = field(default_factory=list)
    manifest: dict = field(default_factory=dict)

    TABLES = {
        "hr_empl_master.csv": ("employees", EMPLOYEE_COLUMNS),
        "hr_disc_actn.csv": ("actions", ACTION_COLUMNS),
        "hr_admn_leave.csv": ("admin_leave", LEAVE_COLUMNS),
        "hr_appl_grv.csv": ("appeals", APPEAL_COLUMNS),
        "hr_sepn.csv": ("separations", SEPARATION_COLUMNS),
    }

    def write(self, out_dir: Path) -> list[Path]:
        import csv

        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for filename, (attr, columns) in self.TABLES.items():
            path = out_dir / filename
            with open(path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=columns)
                writer.writeheader()
                writer.writerows(getattr(self, attr))
            written.append(path)
        manifest_path = out_dir / "manifest.json"
        manifest_path.write_text(json.dumps(self.manifest, indent=2, sort_keys=True) + "\n")
        written.append(manifest_path)
        return written


@dataclass
class _Emp:
    """Internal richer view; only a subset is written to the source CSV."""

    employee_id: str
    role: RoleFamily
    location: str
    pay_step: int
    salary: Decimal
    hire_date: date
    supervisor_id: str | None = None
    separation_date: date | None = None
    separation_reason: str | None = None
    blank_salary: bool = False

    def active_between(self, start: date, end: date) -> tuple[date, date] | None:
        lo = max(start, self.hire_date)
        hi = min(end, self.separation_date or end)
        return (lo, hi) if lo <= hi else None


def _fmt(d: date | None) -> str:
    return d.strftime(DATE_FMT) if d else ""


def _yn(value: bool) -> str:
    return "Y" if value else "N"


def _money(value: Decimal) -> str:
    """The county export writes money with thousands separators and two decimals."""
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}"


def _invert(mapping: dict) -> dict:
    return {v: k for k, v in mapping.items()}


class _Generator:
    def __init__(self, seed: int, org: OrgConfig, profile: GeneratorProfile, mapping: dict):
        self.rng = random.Random(seed)
        self.seed = seed
        self.org = org
        self.p = profile
        self.mapping = mapping
        codes = mapping["codes"]
        self.dept_code = _invert(codes["department"])
        self.flsa_code = _invert(codes["flsa"])
        self.barg_code = _invert(codes["bargaining_unit"])
        self.misconduct_code = _invert(codes["misconduct"])
        self.action_code = _invert(codes["action_type"])
        self.investigation_code = _invert(codes["investigation"])
        self.level_code = _invert(codes["deciding_official_level"])
        self.forum_code = _invert(codes["appeal_forum"])
        self.outcome_code = _invert(codes["appeal_outcome"])
        self.sep_reason_code = _invert(codes["separation_reason"])
        self.bu_code_by_id = {b["id"]: b["id"] for b in org.raw.get("bargaining_units", [])}
        self.as_of = date.fromisoformat(str(profile["horizon"]["as_of_date"]))
        self.window_start = self.as_of - timedelta(days=365 * int(profile["horizon"]["years"]))
        self.employees: list[_Emp] = []
        self.by_id: dict[str, _Emp] = {}
        self._counters: dict[str, int] = {}
        self.actions_raw: list[dict] = []
        self.actions: list[dict] = []
        self.admin_leave: list[dict] = []
        self.appeals: list[dict] = []
        self.separations: list[dict] = []
        self.employee_rows: list[dict] = []

    # -- ids ---------------------------------------------------------------
    def _next(self, prefix: str, width: int = 5) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}{n:0{width}d}"

    def _rand_date(self, lo: date, hi: date) -> date:
        return lo + timedelta(days=self.rng.randint(0, max(0, (hi - lo).days)))

    # -- workforce ---------------------------------------------------------
    def build_workforce(self) -> None:
        horizon_years = int(self.p["horizon"]["years"])
        attrition = float(self.p["workforce"]["annual_voluntary_attrition"])
        loc_weights = [float(w) for w in self.p["location_share_weights"]]

        for role in self.org.role_families.values():
            locations = list(role.locations)
            weights = loc_weights[: len(locations)]
            historical = round(role.headcount * attrition * horizon_years)
            for index in range(role.headcount + historical):
                emp = self._make_employee(role, locations, weights)
                if index >= role.headcount:
                    self._separate_voluntarily(emp)
                self.employees.append(emp)
                self.by_id[emp.employee_id] = emp
        self._assign_supervisors()

    def _make_employee(self, role: RoleFamily, locations: list[str], weights: list[float]) -> _Emp:
        tenure_years = GeneratorProfile.triangular(self.rng, self.p["workforce"]["tenure_years"])
        hire_date = self.as_of - timedelta(days=int(tenure_years * 365.25))
        step = min(
            self.org.pay_steps,
            1 + int(tenure_years / float(self.p["workforce"]["years_per_step"])),
        )
        return _Emp(
            employee_id=self._next("E"),
            role=role,
            location=self.rng.choices(locations, weights=weights, k=1)[0],
            pay_step=step,
            salary=self.org.salary_for(role.pay_grade, step),
            hire_date=hire_date,
        )

    def _separate_voluntarily(self, emp: _Emp) -> None:
        start = max(self.window_start, emp.hire_date + timedelta(days=30))
        emp.separation_date = self._rand_date(min(start, self.as_of), self.as_of)
        emp.separation_reason = GeneratorProfile.weighted_choice(
            self.rng, self.p["voluntary_separation_reason_weights"]
        )

    def _assign_supervisors(self) -> None:
        pools: dict[tuple[str, str], list[str]] = {}
        dept_pools: dict[str, list[str]] = {}
        for emp in self.employees:
            if emp.role.is_supervisory and emp.separation_date is None:
                pools.setdefault((emp.role.department_id, emp.location), []).append(emp.employee_id)
                dept_pools.setdefault(emp.role.department_id, []).append(emp.employee_id)
        for emp in self.employees:
            if emp.role.is_supervisory:
                continue
            pool = pools.get((emp.role.department_id, emp.location)) or dept_pools.get(
                emp.role.department_id
            )
            if pool:
                emp.supervisor_id = self.rng.choice(pool)

    # -- discipline actions ------------------------------------------------
    def build_actions(self) -> None:
        target = int(self.p["horizon"]["target_action_count"])
        pattern = self.p["embedded_pattern"]
        rate_factors = self.p["action_rate_factor_by_role"]

        candidates: list[_Emp] = []
        weights: list[float] = []
        for emp in self.employees:
            span = emp.active_between(self.window_start, self.as_of)
            if span is None:
                continue
            active_share = ((span[1] - span[0]).days + 1) / ((self.as_of - self.window_start).days + 1)
            weight = float(rate_factors.get(emp.role.id, 1.0)) * active_share
            if self._in_pattern(emp, pattern):
                weight *= float(pattern["action_rate_multiplier"])
            if weight > 0:
                candidates.append(emp)
                weights.append(weight)

        attempts = 0
        max_attempts = target * 8
        while len(self.actions) < target and attempts < max_attempts:
            attempts += 1
            emp = self.rng.choices(candidates, weights=weights, k=1)[0]
            self._make_action(emp, pattern)

    def _in_pattern(self, emp: _Emp, pattern: dict) -> bool:
        return emp.role.id == pattern["role_family"] and emp.location == pattern["work_location"]

    def _make_action(self, emp: _Emp, pattern: dict) -> None:
        span = emp.active_between(self.window_start, self.as_of)
        if span is None:
            return
        timing = self.p["timing"]
        # Work backwards from the decision so the decision always lands inside the window.
        incident = self._rand_date(span[0], span[1])
        proposal = incident + timedelta(days=GeneratorProfile.triangular_days(
            self.rng, timing["incident_to_proposal"]))
        decision = proposal + timedelta(days=GeneratorProfile.triangular_days(
            self.rng, timing["proposal_to_decision"]))
        if decision > self.as_of:
            return  # still in progress at the extract date; not yet a decided action

        category = self._pick_category(emp, pattern)
        tier = self.p["category_severity_tier"][category]
        action_type = self._pick_action_type(emp, tier, pattern)
        action_id = self._next("A", 5)

        min_days, max_days = self.org.action_types[action_type]["suspension_days_range"]
        suspension_days = self.rng.randint(min_days, max_days) if max_days else 0
        suspension_start = (
            decision + timedelta(days=GeneratorProfile.triangular_days(
                self.rng, timing["decision_to_suspension_start"]))
            if suspension_days
            else None
        )

        investigated = self.rng.random() < float(
            self.p["investigation"]["probability_by_tier"][tier]
        )
        investigation_type = (
            GeneratorProfile.weighted_choice(
                self.rng,
                self.p["investigation"][
                    "type_weights_sworn" if emp.role.is_sworn else "type_weights_civilian"
                ],
            )
            if investigated
            else None
        )

        severity_rank = int(self.org.action_types[action_type]["severity_rank"])
        level = self.p["deciding_level_by_severity_rank"][severity_rank]
        deciding_id = emp.supervisor_id if level == "supervisor" else None

        subtype = self.rng.choice(list(self.org.misconduct_categories[category].subtypes))

        self.actions_raw.append(
            {
                "action_id": action_id,
                "emp": emp,
                "action_type": action_type,
                "tier": tier,
                "decision": decision,
                "suspension_days": suspension_days,
            }
        )
        self.actions.append(
            {
                "ACTN_NBR": action_id,
                "EMP_NBR": emp.employee_id,
                "INCDT_DT": _fmt(incident),
                "PROP_DT": _fmt(proposal),
                "DECN_DT": _fmt(decision),
                "MISCND_CD": self.misconduct_code[category],
                "MISCND_SUB": subtype,
                "ACTN_CD": self.action_code[action_type],
                "SUSP_DAYS": str(suspension_days),
                "SUSP_BEG_DT": _fmt(suspension_start),
                "INVEST_IND": _yn(investigated),
                "INVEST_TYP": self.investigation_code[investigation_type] if investigated else "",
                "DECIDE_LVL": self.level_code[level],
                "DECIDE_EMP_NBR": deciding_id or "",
                "POSN_ABOLISH_IND": "N",
            }
        )
        self._maybe_admin_leave(emp, action_id, action_type, tier, decision)
        appeal = self._maybe_appeal(emp, action_id, action_type, decision, suspension_days)
        if action_type == "removal":
            self._resolve_removal(emp, action_id, decision, appeal)

    def _pick_category(self, emp: _Emp, pattern: dict) -> str:
        weights = {
            cid: float(w)
            for cid, w in self.p["misconduct_weights"].items()
            if emp.role.is_sworn or not self.org.misconduct_categories[cid].sworn_only
        }
        if self._in_pattern(emp, pattern):
            for cid, mult in pattern["category_weight_multipliers"].items():
                if cid in weights:
                    weights[cid] *= float(mult)
        return GeneratorProfile.weighted_choice(self.rng, weights)

    def _pick_action_type(self, emp: _Emp, tier: str, pattern: dict) -> str:
        action_type = GeneratorProfile.weighted_choice(self.rng, self.p["action_type_weights"][tier])
        if self._in_pattern(emp, pattern) and self.rng.random() < float(pattern["severity_shift"]):
            ranked = sorted(self.org.action_types.values(), key=lambda a: a["severity_rank"])
            ids = [a["id"] for a in ranked]
            idx = ids.index(action_type)
            action_type = ids[min(idx + 1, len(ids) - 1)]
        return action_type

    def _maybe_admin_leave(
        self, emp: _Emp, action_id: str, action_type: str, tier: str, decision: date
    ) -> None:
        spec = self.p["admin_leave"]["probability_by_action"].get(action_type)
        if not spec:
            return
        probability = float(spec["sworn" if emp.role.is_sworn else "civilian"])
        if tier == "serious":
            probability *= float(self.p["admin_leave"]["serious_tier_multiplier"])
        probability = min(probability, float(self.p["admin_leave"]["max_probability"]))
        if self.rng.random() >= probability:
            return
        days = max(1, GeneratorProfile.triangular_days(
            self.rng, self.p["admin_leave"]["duration_days"][action_type]))
        end = decision
        start = end - timedelta(days=days - 1)
        self.admin_leave.append(
            {
                "LEAVE_NBR": self._next("L", 5),
                "EMP_NBR": emp.employee_id,
                "ACTN_NBR": action_id,
                "BEG_DT": _fmt(start),
                "END_DT": _fmt(end),
                "PAID_IND": "Y",
            }
        )

    def _maybe_appeal(
        self, emp: _Emp, action_id: str, action_type: str, decision: date, suspension_days: int
    ) -> dict | None:
        probability = float(self.p["appeals"]["probability_by_action"].get(action_type, 0.0))
        if self.rng.random() >= probability:
            return None
        timing = self.p["timing"]
        filed = decision + timedelta(days=GeneratorProfile.triangular_days(
            self.rng, timing["decision_to_appeal_filed"]))
        if filed > self.as_of:
            return None
        forum = GeneratorProfile.weighted_choice(
            self.rng,
            self.p["appeals"]["forum_weights_removal" if action_type == "removal"
                              else "forum_weights_default"],
        )
        resolution = filed + timedelta(days=GeneratorProfile.triangular_days(
            self.rng, timing["appeal_filed_to_resolution"][forum]))
        recent = (self.as_of - decision).days <= int(self.p["open_window"]["recent_days"])
        left_pending = resolution > self.as_of or (
            recent and self.rng.random() < float(self.p["open_window"]["pending_share"])
        )
        outcome = (
            "pending"
            if left_pending
            else GeneratorProfile.weighted_choice(self.rng, self.p["appeals"]["outcome_weights"])
        )

        back_pay = Decimal("0")
        settlement = Decimal("0")
        if outcome != "pending":
            lost = self._lost_pay(emp, action_type, suspension_days, decision, resolution)
            share = Decimal(str(self.p["appeals"]["back_pay_share"][outcome]))
            back_pay = (lost * share).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if outcome == "settled":
                settlement = Decimal(
                    round(GeneratorProfile.triangular(
                        self.rng, self.p["appeals"]["settlement_amount"]), -2)
                )
        counsel_hours = round(
            GeneratorProfile.triangular(self.rng, self.p["appeals"]["outside_counsel_hours"][forum]),
            1,
        )
        record = {
            "APPL_NBR": self._next("P", 5),
            "ACTN_NBR": action_id,
            "FORUM_CD": self.forum_code[forum],
            "FILED_DT": _fmt(filed),
            "RESOLV_DT": "" if outcome == "pending" else _fmt(resolution),
            "OUTCOME_CD": self.outcome_code[outcome],
            "BACKPAY_AMT": _money(back_pay),
            "SETTLE_AMT": _money(settlement),
            "OC_HOURS": f"{counsel_hours:.1f}",
        }
        self.appeals.append(record)
        return {"outcome": outcome, "forum": forum}

    def _lost_pay(
        self, emp: _Emp, action_type: str, suspension_days: int, decision: date, resolution: date
    ) -> Decimal:
        schedule = self.org.schedules[emp.role.schedule_id]
        hourly = emp.salary / Decimal(schedule.annual_paid_hours)
        if suspension_days:
            return hourly * schedule.shift_hours * Decimal(suspension_days)
        days_out = min((resolution - decision).days, 730)
        return (emp.salary / Decimal("365")) * Decimal(max(days_out, 0))

    def _resolve_removal(
        self, emp: _Emp, action_id: str, decision: date, appeal: dict | None
    ) -> None:
        if appeal and appeal["outcome"] == "overturned":
            return  # reinstated: no separation, and therefore no replacement to hire
        separation_date = decision + timedelta(days=self.rng.randint(0, 5))
        if separation_date > self.as_of:
            separation_date = self.as_of
        reason = (
            "resignation_in_lieu_of_removal"
            if self.rng.random() < float(self.p["separations"]["resignation_in_lieu_share"])
            else "removal_for_cause"
        )
        emp.separation_date = separation_date
        emp.separation_reason = reason

        outcome = GeneratorProfile.weighted_choice(
            self.rng, self.p["separations"]["refill_outcome_weights"]
        )
        refill_date: date | None = None
        abolished = outcome == "abolished"
        if outcome != "abolished":
            days = GeneratorProfile.triangular_days(
                self.rng, self.p["separations"]["days_to_refill"][emp.role.turnover_profile]
            )
            candidate = separation_date + timedelta(days=days)
            if outcome == "refilled" and candidate <= self.as_of:
                refill_date = candidate
        if abolished:
            for row in self.actions:
                if row["ACTN_NBR"] == action_id:
                    row["POSN_ABOLISH_IND"] = "Y"
        self.separations.append(
            {
                "EMP_NBR": emp.employee_id,
                "TERM_DT": _fmt(separation_date),
                "TERM_RSN_CD": self.sep_reason_code[reason],
                "ACTN_NBR": action_id,
                "REFILL_IND": _yn(refill_date is not None),
                "REFILL_DT": _fmt(refill_date),
                "ABOLISH_IND": _yn(abolished),
            }
        )

    # -- source rows -------------------------------------------------------
    def emit_employees(self) -> None:
        for emp in self.employees:
            role = emp.role
            dept = self.org.departments[role.department_id]
            self.employee_rows.append(
                {
                    "EMP_NBR": emp.employee_id,
                    "DEPT_CD": self.dept_code[role.department_id],
                    "CLASS_CD": role.job_class_code,
                    "POSN_TITLE": self.rng.choice(list(role.job_titles)),
                    "GRADE": role.pay_grade,
                    "STEP": str(emp.pay_step),
                    "ANNL_SAL": "" if emp.blank_salary else _money(emp.salary),
                    "FLSA_CD": self.flsa_code[role.flsa_status],
                    "BARG_CD": self.barg_code[dept["bargaining_unit"]],
                    "SWORN_IND": _yn(role.is_sworn),
                    "MIN_STAFF_IND": _yn(role.minimum_staffing_role),
                    "SHIFT_HRS": str(self.org.schedules[role.schedule_id].shift_hours),
                    "WORK_LOC": emp.location,
                    "SUPV_EMP_NBR": emp.supervisor_id or "",
                    "HIRE_DT": _fmt(emp.hire_date),
                    "TERM_DT": _fmt(emp.separation_date),
                    "TERM_RSN_CD": (
                        self.sep_reason_code[emp.separation_reason] if emp.separation_reason else ""
                    ),
                }
            )

    # -- deliberate data quality problems ----------------------------------
    def inject_data_quality_issues(self) -> dict[str, int]:
        spec = self.p["data_quality_injection"]
        injected: dict[str, int] = {}

        def pick(rows: list[dict], predicate, count: int) -> list[dict]:
            eligible = [r for r in rows if predicate(r)]
            self.rng.shuffle(eligible)
            return eligible[:count]

        n = int(spec.get("orphan_action", 0))
        for i in range(n):
            self.actions.append(
                {
                    "ACTN_NBR": self._next("A", 5),
                    "EMP_NBR": f"E99{i:03d}",
                    "INCDT_DT": _fmt(self.as_of - timedelta(days=200 + i)),
                    "PROP_DT": _fmt(self.as_of - timedelta(days=180 + i)),
                    "DECN_DT": _fmt(self.as_of - timedelta(days=165 + i)),
                    "MISCND_CD": "ATT", "MISCND_SUB": "Unscheduled absence",
                    "ACTN_CD": "WRP", "SUSP_DAYS": "0", "SUSP_BEG_DT": "",
                    "INVEST_IND": "N", "INVEST_TYP": "", "DECIDE_LVL": "1",
                    "DECIDE_EMP_NBR": "", "POSN_ABOLISH_IND": "N",
                }
            )
        injected["orphan_action"] = n

        rows = pick(self.actions, lambda r: r["ACTN_CD"] == "SP1",
                    int(spec.get("suspension_days_mismatch", 0)))
        for row in rows:
            row["SUSP_DAYS"] = "22"
        injected["suspension_days_mismatch"] = len(rows)

        rows = pick(self.actions, lambda r: r["INCDT_DT"] and r["DECN_DT"],
                    int(spec.get("decision_before_incident", 0)))
        for row in rows:
            row["INCDT_DT"], row["DECN_DT"] = row["DECN_DT"], row["INCDT_DT"]
        injected["decision_before_incident"] = len(rows)

        acted = {r["EMP_NBR"] for r in self.actions}
        targets = [e for e in self.employees if e.employee_id in acted]
        self.rng.shuffle(targets)
        for emp in targets[: int(spec.get("missing_salary", 0))]:
            emp.blank_salary = True
        injected["missing_salary"] = min(int(spec.get("missing_salary", 0)), len(targets))

        separated = [r for r in self.separations]
        self.rng.shuffle(separated)
        for i, sep in enumerate(separated[: int(spec.get("leave_after_separation", 0))]):
            term = _parse(sep["TERM_DT"])
            self.admin_leave.append(
                {
                    "LEAVE_NBR": self._next("L", 5),
                    "EMP_NBR": sep["EMP_NBR"],
                    "ACTN_NBR": sep["ACTN_NBR"],
                    "BEG_DT": _fmt(term + timedelta(days=5 + i)),
                    "END_DT": _fmt(term + timedelta(days=12 + i)),
                    "PAID_IND": "Y",
                }
            )
        injected["leave_after_separation"] = min(
            int(spec.get("leave_after_separation", 0)), len(separated)
        )

        civilian_ids = {e.employee_id for e in self.employees if not e.role.is_sworn}
        rows = pick(self.actions, lambda r: r["EMP_NBR"] in civilian_ids,
                    int(spec.get("sworn_only_category_misapplied", 0)))
        for row in rows:
            row["MISCND_CD"] = "UOF"
            row["MISCND_SUB"] = "Excessive force"
        injected["sworn_only_category_misapplied"] = len(rows)
        return injected


def _parse(text: str) -> date:
    from datetime import datetime

    return datetime.strptime(text, DATE_FMT).date()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def generate(
    seed: int = 20260917,
    org: OrgConfig | None = None,
    profile: GeneratorProfile | None = None,
    mapping_path: Path | None = None,
) -> GeneratedExport:
    """Build one complete synthetic county HR export. Deterministic in `seed`."""
    org = org or default_org_config()
    profile = profile or load_generator_profile()
    mapping_path = mapping_path or MAPPING_FILE
    with open(mapping_path) as fh:
        mapping = yaml.safe_load(fh)

    gen = _Generator(seed, org, profile, mapping)
    gen.build_workforce()
    gen.build_actions()
    injected = gen.inject_data_quality_issues()
    gen.emit_employees()

    export = GeneratedExport(
        employees=gen.employee_rows,
        actions=gen.actions,
        admin_leave=gen.admin_leave,
        appeals=gen.appeals,
        separations=gen.separations,
    )
    export.manifest = {
        "agency": org.agency_name,
        "synthetic": True,
        "disclaimer": (
            "Synthetic data for a fictional county. Generated from documented distributions; "
            "not derived from any real agency's records."
        ),
        "seed": seed,
        "as_of_date": gen.as_of.isoformat(),
        "window_start": gen.window_start.isoformat(),
        "counts": {
            "employees": len(export.employees),
            "active_employees": sum(1 for r in export.employees if not r["TERM_DT"]),
            "actions": len(export.actions),
            "admin_leave_periods": len(export.admin_leave),
            "appeals": len(export.appeals),
            "separations": len(export.separations),
        },
        "injected_data_quality_issues": injected,
        "config_digests": {
            "org_county.yaml": _file_digest(CONFIG_DIR / "org_county.yaml"),
            "generator_profile.yaml": _file_digest(CONFIG_DIR / "generator_profile.yaml"),
            "mapping_county_hr_csv.yaml": _file_digest(mapping_path),
        },
    }
    return export
