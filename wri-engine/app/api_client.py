"""HTTP client for the WRI engine API.

The UI never imports `wri_engine`. Everything it knows, it learned from an HTTP
response. That is what makes this layer disposable: delete `app/` and the engine is
untouched.

The client works in both of the engine's modes:

* **demo** -- no `token`; the role travels in the `X-WRI-Role` header and every path is
  served at the root.
* **licensed** -- a bearer `token` from `login()`, and an `engagement_id` that prefixes every
  data path with `/engagements/{id}`. The role comes from the server, never from here.
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

DEFAULT_BASE_URL = os.environ.get("WRI_API_URL", "http://127.0.0.1:8000")


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        self.status = status
        self.detail = detail
        super().__init__(f"{status}: {detail}")


class EngineClient:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        role: str = "executive",
        session: str = "streamlit",
        timeout: float = 60.0,
        token: str | None = None,
        engagement_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.role = role
        self.session = session
        self.token = token
        self.engagement_id = engagement_id
        self._client = httpx.Client(timeout=timeout, transport=transport)

    @property
    def headers(self) -> dict:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        return {"X-WRI-Role": self.role, "X-WRI-Session": f"{self.session}:{self.role}"}

    def _scoped(self, path: str) -> str:
        """Data paths live under the engagement in a licensed installation."""
        if self.engagement_id:
            return f"/engagements/{self.engagement_id}{path}"
        return path

    def _request(self, method: str, path: str, *, scoped: bool = True, **kwargs) -> httpx.Response:
        if scoped:
            path = self._scoped(path)
        response = self._client.request(
            method, f"{self.base_url}{path}", headers=self.headers, **kwargs
        )
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except json.JSONDecodeError:
                detail = response.text
            raise ApiError(response.status_code, detail)
        return response

    # -- reads -------------------------------------------------------------
    def health(self) -> dict:
        return self._request("GET", "/health", scoped=False).json()

    def meta(self) -> dict:
        return self._request("GET", "/meta").json()

    def load_dataset(self, path: str | None = None) -> dict:
        body: dict[str, Any] = {"source": "county_hr_csv"}
        if path:
            body["path"] = path
        return self._request("POST", "/datasets/load", json=body).json()

    def validation(self) -> dict:
        return self._request("GET", "/datasets/validation").json()

    def summary(self, mode: str = "base") -> dict:
        return self._request("GET", "/costs/summary", params={"mode": mode}).json()

    def matrix(self, rows: str, cols: str, mode: str = "base", filters: dict | None = None) -> dict:
        params: dict[str, Any] = {"rows": rows, "cols": cols, "mode": mode}
        if filters:
            params["filters"] = json.dumps(filters)
        return self._request("GET", "/costs/matrix", params=params).json()

    def actions(self, filters: dict | None = None, mode: str = "base", limit: int = 200) -> dict:
        params: dict[str, Any] = {"mode": mode, "limit": limit}
        if filters:
            params["filters"] = json.dumps(filters)
        return self._request("GET", "/costs/actions", params=params).json()

    def action(self, action_id: str, mode: str = "base") -> dict:
        return self._request("GET", f"/costs/actions/{action_id}", params={"mode": mode}).json()

    def assumptions(self, mode: str = "base") -> dict:
        return self._request("GET", "/assumptions", params={"mode": mode}).json()

    def run_scenario(self, overrides: dict, mode: str = "base") -> dict:
        return self._request(
            "POST", "/scenarios/run", json={"overrides": overrides, "mode": mode}
        ).json()

    def export_csv(self, rows: str, cols: str, mode: str = "base") -> bytes:
        return self._request(
            "GET",
            "/exports/matrix.csv",
            params={"rows": rows, "cols": cols, "mode": mode},
        ).content

    def export_summary(self, mode: str = "base") -> str:
        return self._request("GET", "/exports/summary.md", params={"mode": mode}).text

    # -- sign-in and accounts (licensed mode) --------------------------------
    def login(self, email: str, password: str) -> dict:
        body = self._request(
            "POST", "/auth/login", scoped=False, json={"email": email, "password": password}
        ).json()
        self.token = body["token"]
        return body

    def logout(self) -> None:
        if self.token:
            self._request("POST", "/auth/logout", scoped=False)
        self.token = None

    def me(self) -> dict:
        return self._request("GET", "/auth/me", scoped=False).json()

    def users(self) -> list[dict]:
        return self._request("GET", "/users", scoped=False).json()["users"]

    def create_user(
        self, email: str, password: str, name: str = "", is_firm_admin: bool = False
    ) -> dict:
        return self._request(
            "POST",
            "/users",
            scoped=False,
            json={
                "email": email,
                "password": password,
                "name": name,
                "is_firm_admin": is_firm_admin,
            },
        ).json()

    def set_user_active(self, user_id: int, active: bool) -> dict:
        return self._request(
            "PUT", f"/users/{user_id}/active", scoped=False, json={"is_active": active}
        ).json()

    # -- engagements -----------------------------------------------------------
    def engagements(self) -> list[dict]:
        return self._request("GET", "/engagements", scoped=False).json()["engagements"]

    def sectors(self) -> list[dict]:
        """The cost models this installation can run, for the sector chooser."""
        return self._request("GET", "/sectors", scoped=False).json()["sectors"]

    def create_engagement(self, name: str, client_name: str, sector: str = "public_sector") -> dict:
        return self._request(
            "POST",
            "/engagements",
            scoped=False,
            json={"name": name, "client_name": client_name, "sector": sector},
        ).json()

    def list_members(self) -> list[dict]:
        return self._request(
            "GET", f"/engagements/{self.engagement_id}/members", scoped=False
        ).json()["members"]

    def set_member(self, email: str, role: str) -> dict:
        return self._request(
            "PUT",
            f"/engagements/{self.engagement_id}/members",
            scoped=False,
            json={"email": email, "role": role},
        ).json()

    def remove_member(self, user_id: int) -> dict:
        return self._request(
            "DELETE", f"/engagements/{self.engagement_id}/members/{user_id}", scoped=False
        ).json()

    # -- loading a client's data --------------------------------------------
    def intake_template(self) -> dict:
        return self._request("GET", "/intake/template").json()

    def intake_template_zip(self) -> bytes:
        return self._request("GET", "/intake/template.zip").content

    def intake(self) -> dict:
        return self._request("GET", "/intake").json()

    def intake_upload(self, files: list[tuple[str, bytes]]) -> dict:
        return self._request(
            "POST",
            "/intake/files",
            files=[("files", (name, data, "text/csv")) for name, data in files],
        ).json()

    def intake_preview(self, mapping: dict) -> dict:
        return self._request("PUT", "/intake/mapping", json={"mapping": mapping}).json()

    def intake_commit(self) -> dict:
        return self._request("POST", "/intake/commit").json()

    def upload_template_files(self, files: list[tuple[str, bytes]]) -> dict:
        return self._request(
            "POST",
            "/datasets/upload",
            params={"source": "wri_template"},
            files=[("files", (name, data, "text/csv")) for name, data in files],
        ).json()

    # -- client-specific assumption values ---------------------------------
    def overrides(self) -> list[dict]:
        return self._request("GET", "/assumptions/overrides").json()["overrides"]

    def set_override(self, assumption_id: str, value: str, reason: str) -> dict:
        return self._request(
            "PUT",
            f"/assumptions/{assumption_id}/override",
            json={"value": str(value), "reason": reason},
        ).json()

    def clear_override(self, assumption_id: str) -> dict:
        return self._request("DELETE", f"/assumptions/{assumption_id}/override").json()
