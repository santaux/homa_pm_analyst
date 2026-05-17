"""
Minimal Metabase HTTP client used by the MCP server to let Claude create
dashboard cards from natural-language requests.

Design goals
------------
- Plain requests + stdlib. No SDKs.
- Defaults match the local docker-compose setup so the PM never has to
  configure anything in the happy path.
- Friendly errors: when Metabase is down or auth fails, the error message
  tells the user *what* to fix (start docker compose, etc.), not just
  what HTTP code came back.
- Session token cached in-process — multiple tool calls reuse one login.

Env overrides
-------------
    MB_HOST            default http://localhost:3000
    MB_ADMIN_EMAIL     default admin@homa.local
    MB_ADMIN_PASSWORD  default HomaAdmin1!
    MB_DB_NAME         default Homa Energy
"""

from __future__ import annotations

import os
from typing import Any

import requests


class MetabaseError(RuntimeError):
    """Raised when Metabase is unreachable or an API call fails."""


VALID_CHART_TYPES = {"line", "bar", "table", "funnel", "scalar", "pie", "area", "row"}


class MetabaseClient:
    """Thin wrapper around the few Metabase REST endpoints we need."""

    def __init__(self) -> None:
        self.host       = os.environ.get("MB_HOST", "http://localhost:3000").rstrip("/")
        self.email      = os.environ.get("MB_ADMIN_EMAIL", "admin@homa.local")
        self.password   = os.environ.get("MB_ADMIN_PASSWORD", "HomaAdmin1!")
        self.db_name    = os.environ.get("MB_DB_NAME", "Homa PM Analytics")
        self._token: str | None = None
        self._db_id: int | None = None

    # ── infra ──────────────────────────────────────────────────────────

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._token:
            h["X-Metabase-Session"] = self._token
        return h

    def _request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.host}{path}"
        try:
            r = requests.request(method, url, headers=self._headers(),
                                 timeout=15, **kwargs)
        except requests.ConnectionError as e:
            raise MetabaseError(
                f"Cannot reach Metabase at {self.host}. "
                f"Make sure `docker compose up -d` is running in metabase/. ({e})"
            ) from e
        except requests.RequestException as e:
            raise MetabaseError(f"Metabase request failed: {e}") from e

        if r.status_code == 401:
            # Session expired — clear cache so caller can re-login
            self._token = None
            raise MetabaseError("Metabase session expired; please retry.")
        if not r.ok:
            raise MetabaseError(
                f"{method} {path} → HTTP {r.status_code}: {r.text[:300]}"
            )
        return r.json() if r.text else None

    def _login(self) -> None:
        try:
            r = requests.post(
                f"{self.host}/api/session",
                json={"username": self.email, "password": self.password},
                timeout=10,
            )
        except requests.ConnectionError as e:
            raise MetabaseError(
                f"Cannot reach Metabase at {self.host}. "
                f"Make sure `docker compose up -d` is running. ({e})"
            ) from e
        if r.status_code == 401:
            raise MetabaseError(
                f"Metabase login failed: bad credentials for {self.email}. "
                "Set MB_ADMIN_EMAIL / MB_ADMIN_PASSWORD if you changed them."
            )
        if not r.ok:
            raise MetabaseError(f"Metabase login failed: HTTP {r.status_code}")
        self._token = r.json()["id"]

    def _ensure_session(self) -> None:
        if not self._token:
            self._login()

    def _resolve_db_id(self) -> int:
        if self._db_id is not None:
            return self._db_id
        self._ensure_session()
        data = self._request("GET", "/api/database")
        items = data.get("data", data) if isinstance(data, dict) else data
        for db in items:
            if db.get("name") == self.db_name:
                self._db_id = db["id"]
                return self._db_id
        available = ", ".join(db.get("name", "?") for db in items)
        raise MetabaseError(
            f"Database '{self.db_name}' not found in Metabase. "
            f"Available: {available}. "
            f"Run `python metabase/setup_metabase.py` to create the connection."
        )

    # ── public ─────────────────────────────────────────────────────────

    def list_dashboards(self) -> list[dict]:
        self._ensure_session()
        data = self._request("GET", "/api/dashboard")
        items = data.get("data", data) if isinstance(data, dict) else data
        return [{"id": d["id"], "name": d["name"],
                 "description": d.get("description", "")} for d in items]

    def find_dashboard(self, query: str) -> dict | None:
        """Find a dashboard by case-insensitive name match. Accepts partials
        like 'Product Health' or just 'push'."""
        q = query.lower().strip()
        candidates = self.list_dashboards()
        # exact (normalised) match first
        for d in candidates:
            if d["name"].lower().strip() == q:
                return d
        # then substring
        matches = [d for d in candidates if q in d["name"].lower()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = ", ".join(m["name"] for m in matches)
            raise MetabaseError(
                f"Dashboard '{query}' is ambiguous, matches: {names}"
            )
        return None

    def create_card(self, name: str, sql: str,
                    display: str = "line",
                    description: str = "") -> dict:
        if display not in VALID_CHART_TYPES:
            raise MetabaseError(
                f"Unknown chart_type '{display}'. "
                f"Use one of: {', '.join(sorted(VALID_CHART_TYPES))}"
            )
        self._ensure_session()
        db_id = self._resolve_db_id()
        payload = {
            "name":         name,
            "description":  description,
            "display":      display,
            "dataset_query": {
                "type":     "native",
                "native":   {"query": sql.strip()},
                "database": db_id,
            },
            "visualization_settings": {},
        }
        return self._request("POST", "/api/card", json=payload)

    def add_card_to_dashboard(self, dashboard_query: str, card_id: int,
                              size_x: int = 12, size_y: int = 6) -> dict:
        self._ensure_session()
        dash = self.find_dashboard(dashboard_query)
        if not dash:
            names = ", ".join(d["name"] for d in self.list_dashboards())
            raise MetabaseError(
                f"Dashboard '{dashboard_query}' not found. Available: {names}"
            )
        # Fetch current dashcards so we can append rather than overwrite
        full = self._request("GET", f"/api/dashboard/{dash['id']}")
        existing = full.get("dashcards", full.get("ordered_cards", [])) or []
        max_bottom = max(
            (c.get("row", 0) + c.get("size_y", 0) for c in existing),
            default=0,
        )
        new_spec = {
            "id":       -1,
            "card_id":  card_id,
            "row":      max_bottom,
            "col":      0,
            "size_x":   size_x,
            "size_y":   size_y,
            "parameter_mappings":     [],
            "visualization_settings": {},
        }
        all_cards = list(existing) + [new_spec]
        self._request("PUT", f"/api/dashboard/{dash['id']}/cards",
                      json={"cards": all_cards})
        return {
            "dashboard_id":   dash["id"],
            "dashboard_name": dash["name"],
            "dashboard_url":  f"{self.host}/dashboard/{dash['id']}",
        }

    def delete_card(self, card_id: int) -> None:
        """Soft-archive a card (so it disappears from the UI) — useful for
        tests and idempotent re-runs."""
        self._ensure_session()
        self._request("PUT", f"/api/card/{card_id}", json={"archived": True})


# Module-level cached client so multiple MCP tool calls share one session.
_client_singleton: MetabaseClient | None = None


def get_client() -> MetabaseClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = MetabaseClient()
    return _client_singleton
