"""The screens a licensed installation adds: sign-in, engagements, loading a client's data,
and client-specific assumption values.

Like the rest of `app/`, this talks HTTP only. What a person may do is decided by the
server; these screens hide what the server would refuse anyway, so nobody is shown a button
that can only fail, but nothing here is the control.
"""

from __future__ import annotations

import contextlib
import hashlib
import json

import pandas as pd
import streamlit as st

from api_client import ApiError, EngineClient

ENGAGEMENT_CAPTION = (
    "Cost figures reflect the data supplied for this engagement and its documented "
    "assumptions. Review the assumptions and overrides before relying on a figure."
)
ROLE_LABELS = {
    "admin": "Engagement lead (admin)",
    "hr_analyst": "Analyst",
    "executive": "Client reader",
}
ROLE_HELP = (
    "**Engagement lead** loads data, sets client-specific values and manages members. "
    "**Analyst** sees record-level detail with employee ids masked and can run scenarios. "
    "**Client reader** sees totals only."
)
NOT_SUPPLIED = "(not supplied)"
#: Offered when no format was detected. The detected one, if any, is put first.
DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%m/%d/%y", "%m-%d-%Y", "%d-%b-%Y"]
DATE_FORMAT_LABELS = {
    "%Y-%m-%d": "2024-07-31",
    "%m/%d/%Y": "07/31/2024 (month first)",
    "%d/%m/%Y": "31/07/2024 (day first)",
    "%m/%d/%y": "07/31/24",
    "%m-%d-%Y": "07-31-2024",
    "%d-%b-%Y": "31-Jul-2024",
}
#: Employee fields that describe a kind of job. When they come from the job family column,
#: they are decided in one grid, one row per job family.
PER_JOB_FAMILY = ("is_sworn", "minimum_staffing_role", "work_schedule", "turnover_profile")


# ---------------------------------------------------------------------------
# session
# ---------------------------------------------------------------------------
def is_licensed() -> bool:
    return st.session_state.get("auth_mode") == "local"


def signed_in() -> bool:
    return bool(st.session_state.get("token"))


def engagement() -> dict | None:
    return st.session_state.get("engagement")


def role() -> str:
    current = engagement()
    return current["your_role"] if current else "executive"


def can(capability: str) -> bool:
    current = engagement()
    return bool(current) and capability in current["capabilities"]


def is_firm_admin() -> bool:
    return bool(st.session_state.get("user", {}).get("is_firm_admin"))


def sign_out() -> None:
    for key in ("token", "user", "engagements", "engagement", "engagement_id", "page"):
        st.session_state.pop(key, None)
    for key in [k for k in st.session_state if str(k).startswith(("intake_", "mapping_"))]:
        st.session_state.pop(key, None)


def refresh(api: EngineClient) -> None:
    """Reload the signed-in user and the engagements they may open."""
    me = api.me()
    st.session_state["user"] = me["user"]
    st.session_state["engagements"] = me["engagements"]
    chosen = st.session_state.get("engagement_id")
    by_id = {e["id"]: e for e in me["engagements"]}
    if chosen not in by_id:
        chosen = me["engagements"][0]["id"] if me["engagements"] else None
    st.session_state["engagement_id"] = chosen
    st.session_state["engagement"] = by_id.get(chosen)


def header(title: str, subtitle: str = "") -> None:
    st.title(title)
    if subtitle:
        st.markdown(subtitle)
    st.caption(ENGAGEMENT_CAPTION)


def _error(exc: ApiError) -> None:
    detail = exc.detail
    if isinstance(detail, dict):
        detail = detail.get("message", json.dumps(detail))
    st.error(f"{detail}")


# ---------------------------------------------------------------------------
# sign-in
# ---------------------------------------------------------------------------
def sign_in_page(api_url: str) -> None:
    st.title("Workforce Risk Intelligence")
    st.markdown("Sign in to see your engagements.")
    with st.form("sign_in"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary")
    if not submitted:
        return
    api = EngineClient(base_url=api_url)
    try:
        body = api.login(email, password)
    except ApiError as exc:
        _error(exc)
        return
    st.session_state["token"] = body["token"]
    refresh(EngineClient(base_url=api_url, token=body["token"]))
    st.rerun()


# ---------------------------------------------------------------------------
# sidebar
# ---------------------------------------------------------------------------
def pages() -> list[str]:
    if not engagement():
        return ["Engagement"]
    out = ["Executive summary", "Cost matrix"]
    if can("view_records"):
        out.append("Drill-down")
    out += ["Assumptions", "Data quality"]
    if can("manage_data"):
        out.append("Load data")
    out.append("Engagement")
    return out


def sidebar(api_url: str) -> str:
    """The licensed sidebar: who is signed in, which engagement is open, and the pages that
    engagement's role can use. Returns the chosen page."""
    user = st.session_state.get("user", {})
    engagements = st.session_state.get("engagements", [])
    current = engagement()

    st.sidebar.markdown(f"### {current['client_name'] if current else 'No engagement'}")
    st.sidebar.caption("Workforce Risk Intelligence")
    if engagements:
        ids = [e["id"] for e in engagements]
        labels = {e["id"]: f"{e['name']} - {e['client_name']}" for e in engagements}
        chosen = st.sidebar.selectbox(
            "Engagement",
            ids,
            index=ids.index(st.session_state["engagement_id"]),
            format_func=labels.get,
        )
        if chosen != st.session_state["engagement_id"]:
            st.session_state["engagement_id"] = chosen
            st.session_state["engagement"] = next(e for e in engagements if e["id"] == chosen)
            st.rerun()
        st.sidebar.caption(f"Your role: {ROLE_LABELS.get(role(), role())}")

    options = pages()
    # A page can be chosen for the person on the next run (after opening an engagement,
    # go straight to loading its data). It must be set before the radio is drawn.
    wanted = st.session_state.pop("next_page", None)
    if wanted in options:
        st.session_state["page"] = wanted
    elif st.session_state.get("page") not in options:
        st.session_state["page"] = options[0]
    page = st.sidebar.radio("Page", options, key="page", label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption(f"Signed in as {user.get('email', '')}")
    if st.sidebar.button("Sign out"):
        with contextlib.suppress(ApiError):
            EngineClient(base_url=api_url, token=st.session_state.get("token")).logout()
        sign_out()
        st.rerun()
    return page


# ---------------------------------------------------------------------------
# engagement page
# ---------------------------------------------------------------------------
def engagement_page(api: EngineClient) -> None:
    current = engagement()
    if current:
        header(current["name"], f"Client: **{current['client_name']}**")
        cells = st.columns(3)
        cells[0].metric("Your role", ROLE_LABELS.get(current["your_role"], current["your_role"]))
        cells[1].metric("Sector", current["sector"].replace("_", " ").capitalize())
        cells[2].metric("Opened", current["created_at"][:10])
        if current["your_role"] == "admin":
            _members(api)
    else:
        st.title("Engagements")
        if is_firm_admin():
            st.info("There are no engagements yet. Open the first one below.")
        else:
            st.info(
                "You are not a member of any engagement yet. Ask your firm administrator "
                "to add you to one."
            )
    if is_firm_admin():
        st.divider()
        _new_engagement(api)
        st.divider()
        _users(api)


def _members(api: EngineClient) -> None:
    st.subheader("Members")
    st.markdown(ROLE_HELP)
    try:
        members = api.list_members()
    except ApiError as exc:
        _error(exc)
        return
    if members:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Email": m["email"],
                        "Name": m["name"],
                        "Role": ROLE_LABELS.get(m["role"], m["role"]),
                    }
                    for m in members
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption(
            "No members yet. Firm administrators can open every engagement without being members."
        )
    left, right = st.columns(2)
    with left.form("add_member", clear_on_submit=True):
        st.markdown("**Add or change a member**")
        email = st.text_input("Email of an existing user")
        chosen = st.selectbox("Role", list(ROLE_LABELS), format_func=ROLE_LABELS.get)
        if st.form_submit_button("Save member"):
            try:
                api.set_member(email, chosen)
                st.success(f"{email} is now {ROLE_LABELS[chosen].lower()}.")
                st.rerun()
            except ApiError as exc:
                _error(exc)
    if members:
        with right.form("remove_member"):
            st.markdown("**Remove a member**")
            by_email = {m["email"]: m["id"] for m in members}
            target = st.selectbox("Member", list(by_email))
            if st.form_submit_button("Remove"):
                try:
                    api.remove_member(by_email[target])
                    st.rerun()
                except ApiError as exc:
                    _error(exc)


def _sector_choices(api: EngineClient) -> list[dict]:
    """The sectors the server will accept. If the call fails, fall back to the public-sector
    model alone rather than offering a sector the server would refuse."""
    try:
        return api.sectors()
    except ApiError:
        return [
            {
                "id": "public_sector",
                "label": "Public sector",
                "description": "County and municipal employers.",
                "components": [],
            }
        ]


def _new_engagement(api: EngineClient) -> None:
    st.subheader("Open a new engagement")
    with st.form("new_engagement", clear_on_submit=True):
        name = st.text_input("Engagement name", placeholder="FY26 discipline cost review")
        client_name = st.text_input("Client name", placeholder="Pine Valley County")
        packs = _sector_choices(api)
        labels = {p["id"]: p["label"] for p in packs}
        sector = st.selectbox(
            "Sector",
            list(labels),
            format_func=lambda s: labels[s],
            help="Which cost model the engagement runs. It cannot be changed afterwards.",
        )
        chosen = next((p for p in packs if p["id"] == sector), None)
        if chosen:
            st.caption(f"{chosen['description']} Components: {', '.join(chosen['components'])}.")
        if st.form_submit_button("Open engagement", type="primary"):
            try:
                created = api.create_engagement(name, client_name, sector)
            except ApiError as exc:
                _error(exc)
                return
            st.session_state["engagement_id"] = created["id"]
            st.session_state["next_page"] = "Load data"
            refresh(api)
            st.rerun()


def _users(api: EngineClient) -> None:
    st.subheader("Users")
    try:
        users = api.users()
    except ApiError as exc:
        _error(exc)
        return
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Email": u["email"],
                    "Name": u["name"],
                    "Firm administrator": "yes" if u["is_firm_admin"] else "",
                    "Active": "yes" if u["is_active"] else "no",
                }
                for u in users
            ]
        ),
        hide_index=True,
        width="stretch",
    )
    left, right = st.columns(2)
    with left.form("new_user", clear_on_submit=True):
        st.markdown("**Create a user**")
        email = st.text_input("Email")
        name = st.text_input("Name")
        password = st.text_input(
            "Starting password",
            type="password",
            help="At least 12 characters. Give it to the person directly.",
        )
        firm_admin = st.checkbox("Firm administrator")
        if st.form_submit_button("Create user"):
            try:
                api.create_user(email, password, name=name, is_firm_admin=firm_admin)
                st.success(f"Created {email}.")
                st.rerun()
            except ApiError as exc:
                _error(exc)
    me = st.session_state.get("user", {}).get("id")
    others = [u for u in users if u["id"] != me]
    if others:
        with right.form("user_active"):
            st.markdown("**Deactivate or restore**")
            by_email = {u["email"]: u for u in others}
            target = st.selectbox("User", list(by_email))
            active = st.radio("Account", ["active", "deactivated"], horizontal=True)
            st.caption("Deactivating signs the person out everywhere at once.")
            if st.form_submit_button("Save"):
                try:
                    api.set_user_active(by_email[target]["id"], active == "active")
                    st.rerun()
                except ApiError as exc:
                    _error(exc)


# ---------------------------------------------------------------------------
# load data
# ---------------------------------------------------------------------------
def _key(name: str) -> str:
    return f"{name}_{st.session_state['engagement_id']}"


def _digest(mapping: dict) -> str:
    return hashlib.sha256(json.dumps(mapping, sort_keys=True).encode()).hexdigest()


def load_data_page(api: EngineClient) -> None:
    header(
        "Load data",
        "Bring in the client's export as it arrived. Nothing is costed, and the data already "
        "loaded is untouched, until you commit.",
    )
    spec_key = _key("intake_spec")
    if spec_key not in st.session_state:
        st.session_state[spec_key] = api.intake_template()
    spec = {t["name"]: t for t in st.session_state[spec_key]["tables"]}

    _step_template(api)
    status = _step_upload(api)
    if not status["files"]:
        return
    mapping = _working_mapping(api, status)
    _step_columns(mapping, spec, status)
    preview = st.session_state.get(_key("intake_preview"))
    _step_codes(mapping, spec, preview)
    _step_preview(api, mapping)


def _step_template(api: EngineClient) -> None:
    with st.expander("1. If the client has no clean export, send them the template"):
        st.markdown(
            "Five spreadsheets and a plain-language guide. Only **employees** and "
            "**actions** are required."
        )
        st.download_button(
            "Download the blank template",
            api.intake_template_zip(),
            file_name="wri_intake_template.zip",
            mime="application/zip",
        )


def _step_upload(api: EngineClient) -> dict:
    st.subheader("2. Upload what the client sent")
    uploaded = st.file_uploader(
        "CSV files", type=["csv"], accept_multiple_files=True, key=_key("intake_uploader")
    )
    if uploaded and st.button("Upload these files", type="primary"):
        try:
            api.intake_upload([(f.name, f.getvalue()) for f in uploaded])
        except ApiError as exc:
            _error(exc)
        else:
            for key in ("mapping", "intake_preview"):
                st.session_state.pop(_key(key), None)
            st.rerun()
    status = api.intake()
    if status["files"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {"File": name, "Rows": info["rows"], "Columns": len(info["columns"])}
                    for name, info in status["files"].items()
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("Nothing uploaded yet.")
    return status


def _working_mapping(api: EngineClient, status: dict) -> dict:
    """The mapping being edited lives in the session until it is checked, then on the
    server as the engagement's draft."""
    key = _key("mapping")
    if key not in st.session_state:
        start = status["draft_mapping"] or status["suggestion"]["suggested_mapping"]
        st.session_state[key] = json.loads(json.dumps(start))
        st.session_state[_key("intake_suggestion")] = status["suggestion"]
        _check(api, st.session_state[key])
    return st.session_state[key]


def _step_columns(mapping: dict, spec: dict, status: dict) -> None:
    st.subheader("3. Match the client's columns")
    suggestion = st.session_state.get(_key("intake_suggestion")) or status["suggestion"] or {}
    detected = (suggestion.get("date_format") or {}).get("detected")
    formats = list(dict.fromkeys([mapping.get("date_format") or "%Y-%m-%d", *DATE_FORMATS]))
    mapping["date_format"] = st.selectbox(
        "How the client writes dates",
        formats,
        index=0,
        format_func=lambda f: (
            DATE_FORMAT_LABELS.get(f, f) + ("  - detected" if f == detected else "")
        ),
        key=_key("date_format"),
    )
    note = (suggestion.get("date_format") or {}).get("note")
    if note:
        st.warning(note)

    files = list(status["files"])
    tables = mapping.setdefault("tables", {})
    for name, table in spec.items():
        current = tables.get(name)
        label = name.replace("_", " ").capitalize() + (" (required)" if table["required"] else "")
        with st.expander(label, expanded=table["required"]):
            options = [NOT_SUPPLIED, *files]
            index = options.index(current["file"]) if current and current["file"] in files else 0
            chosen = st.selectbox("Client file", options, index=index, key=_key(f"file_{name}"))
            if chosen == NOT_SUPPLIED:
                tables.pop(name, None)
                if table["required"]:
                    st.error("This table is required.")
                else:
                    st.caption(table["description"])
                continue
            current = tables.setdefault(
                name, {"file": chosen, "columns": {}, "values": {}, "constants": {}}
            )
            current["file"] = chosen
            _column_editor(name, table, current, status["files"][chosen]["columns"])


def _column_editor(name: str, table: dict, current: dict, columns: list[str]) -> None:
    rows = [
        {
            "Field": f["name"],
            "Required": f["required"],
            "Client column": current["columns"].get(f["name"], "")
            if current["columns"].get(f["name"], "") in columns
            else "",
            "Fixed value": current["constants"].get(f["name"], ""),
            "What it means": f["description"],
        }
        for f in table["fields"]
    ]
    edited = st.data_editor(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        disabled=["Field", "Required", "What it means"],
        column_config={
            "Client column": st.column_config.SelectboxColumn(options=["", *columns]),
            "Fixed value": st.column_config.TextColumn(
                help="Use when every row has the same value and the client has no column."
            ),
            "Required": st.column_config.CheckboxColumn(width="small"),
        },
        key=_key(f"columns_{name}"),
    )
    current["columns"] = {
        r["Field"]: r["Client column"] for r in edited.to_dict("records") if r["Client column"]
    }
    current["constants"] = {
        r["Field"]: str(r["Fixed value"]).strip()
        for r in edited.to_dict("records")
        if str(r["Fixed value"] or "").strip()
    }
    missing = [
        r["Field"]
        for r in edited.to_dict("records")
        if r["Required"] and not r["Client column"] and not str(r["Fixed value"] or "").strip()
    ]
    if missing:
        st.warning("Still needed: " + ", ".join(missing))
    unused = [c for c in columns if c not in current["columns"].values()]
    if unused:
        st.caption("Client columns not used: " + ", ".join(unused))


def _step_codes(mapping: dict, spec: dict, preview: dict | None) -> None:
    st.subheader("4. Translate the client's codes")
    st.caption(
        "Choices are saved to the engagement when you check the mapping (step 5). Check "
        "before leaving or reloading this page."
    )
    if preview is None:
        st.caption("Check the mapping below to see which codes need translating.")
        return
    todo = preview.get("codes_to_translate") or {}
    if not todo:
        st.success("Every code in the client's files is understood.")
    standard = [
        c["id"] for c in st.session_state[_key("intake_spec")]["standard_misconduct_categories"]
    ]
    for name, fields in todo.items():
        tables = mapping.get("tables", {})
        if name not in tables:
            continue
        values = tables[name].setdefault("values", {})
        family_column = tables[name]["columns"].get("job_family")
        per_family = {
            f: info
            for f, info in fields.items()
            if name == "employees" and f in PER_JOB_FAMILY and info["column"] == family_column
        }
        if per_family:
            _family_grid(name, per_family, values)
        for field, info in fields.items():
            if field in per_family:
                continue
            allowed = standard if field == "misconduct_category" else info["allowed"]
            _code_editor(name, field, info, allowed, values)
    _translations_so_far(mapping)


def _family_grid(table: str, fields: dict, values: dict) -> None:
    st.markdown(
        "**Decide these once per job family.** The client's export does not say them, so "
        "they are set from the job family."
    )
    families = sorted({code for info in fields.values() for code in info["codes"]})
    frame = pd.DataFrame(
        [
            {"Job family": family, **{f: values.get(f, {}).get(family, "") for f in fields}}
            for family in families
        ]
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        disabled=["Job family"],
        column_config={
            f: st.column_config.SelectboxColumn(options=["", *info["allowed"]])
            for f, info in fields.items()
        },
        key=_key(f"family_{table}"),
    )
    for record in edited.to_dict("records"):
        for field in fields:
            if record[field]:
                values.setdefault(field, {})[record["Job family"]] = record[field]


def _code_editor(table: str, field: str, info: dict, allowed: list[str], values: dict) -> None:
    st.markdown(f"**{field}** - from the client's `{info['column']}` column")
    if field == "misconduct_category":
        st.caption(
            "Leave a code blank to keep it as the client's own category. Translating it lets "
            "results be compared across clients."
        )
    frame = pd.DataFrame(
        [
            {"Client code": code, "Rows": count, "Means": values.get(field, {}).get(code, "")}
            for code, count in info["codes"].items()
        ]
    )
    edited = st.data_editor(
        frame,
        hide_index=True,
        width="stretch",
        disabled=["Client code", "Rows"],
        column_config={"Means": st.column_config.SelectboxColumn(options=["", *allowed])},
        key=_key(f"codes_{table}_{field}"),
    )
    for record in edited.to_dict("records"):
        if record["Means"]:
            values.setdefault(field, {})[str(record["Client code"])] = record["Means"]


def _translations_so_far(mapping: dict) -> None:
    rows = [
        {"Table": table, "Field": field, "Client code": code, "Means": means}
        for table, body in mapping.get("tables", {}).items()
        for field, codes in (body.get("values") or {}).items()
        for code, means in codes.items()
        if means
    ]
    if not rows:
        return
    with st.expander(f"Translations set so far ({len(rows)})"):
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")


def _check(api: EngineClient, mapping: dict) -> dict | None:
    try:
        preview = api.intake_preview(mapping)
    except ApiError as exc:
        _error(exc)
        return None
    st.session_state[_key("intake_preview")] = preview
    st.session_state[_key("intake_checked")] = _digest(mapping)
    return preview


def _step_preview(api: EngineClient, mapping: dict) -> None:
    st.subheader("5. Check, then commit")
    if st.button("Check the mapping", type="primary"):
        _check(api, mapping)
        st.rerun()
    preview = st.session_state.get(_key("intake_preview"))
    if preview is None:
        return
    stale = st.session_state.get(_key("intake_checked")) != _digest(mapping)
    if stale:
        st.info("The mapping has changed since it was last checked. Check it again.")

    report = preview["validation"]
    cells = st.columns(4)
    cells[0].metric("Employees", f"{preview['counts']['employees']:,}")
    cells[1].metric("Actions", f"{preview['counts']['actions']:,}")
    cells[2].metric(
        "Actions excluded",
        preview["actions_that_would_be_excluded"],
        help="Excluded from every total because a blocking problem makes their cost untrustworthy.",
    )
    cells[3].metric("Warnings", report["warnings"])

    if report["issues"]:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Severity": i["severity"],
                        "Rule": i["rule_id"],
                        "Record": i["entity_id"],
                        "Field": i["field"] or "",
                        "What it says": i["message"],
                    }
                    for i in report["issues"]
                ]
            ),
            hide_index=True,
            width="stretch",
            height=min(400, 40 + 35 * len(report["issues"])),
        )

    if not preview["ready_to_commit"]:
        st.error("Nothing usable would load yet. Fix the blocking problems above and check again.")
        return
    if preview["actions_that_would_be_excluded"]:
        st.warning(
            f"{preview['actions_that_would_be_excluded']} action(s) would be left out of every "
            "total. You can commit now and fix them later, or fix them first."
        )
    if st.button("Commit as this engagement's data", disabled=stale):
        try:
            result = api.intake_commit()
        except ApiError as exc:
            _error(exc)
            return
        st.session_state.pop(_key("intake_preview"), None)
        st.success(
            f"Loaded {result['counts']['employees']:,} employees and "
            f"{result['counts']['actions']:,} actions. The other pages now show this data."
        )


# ---------------------------------------------------------------------------
# client-specific assumption values
# ---------------------------------------------------------------------------
def overrides_section(api: EngineClient, assumptions: list[dict]) -> None:
    st.divider()
    st.subheader("Client-specific values")
    st.caption(
        "Use the client's own figure in place of a default, for this engagement only. Every "
        "change is recorded with who made it and why, and the defaults are untouched."
    )
    current = api.overrides()
    if current:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Assumption": o["assumption_id"],
                        "Client value": o["value"],
                        "Default": o["default_value"],
                        "Unit": o["unit"],
                        "Why": o["reason"],
                        "Set by": o["set_by"],
                        "When": o["set_at"][:16].replace("T", " "),
                    }
                    for o in current
                ]
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("None yet. Every figure uses the approved default.")

    by_id = {a["id"]: a for a in assumptions}
    left, right = st.columns(2)
    with left:
        st.markdown("**Set a client value**")
        # Outside the form, so the default and range below follow the choice at once.
        chosen = st.selectbox("Assumption", list(by_id), key="override_assumption")
        record = by_id[chosen]
        st.caption(
            f"Default {record['value']} {record['unit']} (range {record['low']} to "
            f"{record['high']}). {record['source'][:200]}"
        )
    with left.form("set_override", clear_on_submit=True):
        value = st.text_input("Client value", help="A plain number, such as 1.85 or 425.")
        reason = st.text_input(
            "Where the figure comes from", placeholder="2026 actuarial valuation, p. 14"
        )
        if st.form_submit_button("Save client value", type="primary"):
            try:
                api.set_override(chosen, value.replace(",", "").replace("$", "").strip(), reason)
                st.rerun()
            except ApiError as exc:
                _error(exc)
    if current:
        with right.form("clear_override"):
            st.markdown("**Go back to the default**")
            target = st.selectbox("Client value to remove", [o["assumption_id"] for o in current])
            if st.form_submit_button("Use the default"):
                try:
                    api.clear_override(target)
                    st.rerun()
                except ApiError as exc:
                    _error(exc)
