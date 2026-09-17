"""HTTP client for the WRI engine API.

The UI never imports `wri_engine`. Everything it knows, it learned from an HTTP
response. That is what makes this layer disposable: delete `app/` and the engine is
untouched.
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
    ):
        self.base_url = base_url.rstrip("/")
        self.role = role
        self.session = session
        self._client = httpx.Client(timeout=timeout)

    @property
    def headers(self) -> dict:
        return {"X-WRI-Role": self.role, "X-WRI-Session": f"{self.session}:{self.role}"}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
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
        return self._request("GET", "/health").json()

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
