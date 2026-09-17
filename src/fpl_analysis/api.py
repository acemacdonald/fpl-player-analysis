"""Thin client for the official Fantasy Premier League API.

The API is free and unauthenticated. It is also undocumented and rate-limited by
Cloudflare, so this client:

* sends a browser-like User-Agent (the default ``python-requests`` UA is sometimes blocked),
* retries transient failures with exponential back-off,
* exposes one method per endpoint we use, returning parsed JSON and nothing else.

Everything that *interprets* the JSON lives downstream (ingest → SQL). Keeping the client
dumb means a change in the API shape is a staging-SQL fix, not a client rewrite.

Endpoints (see docs/FPL_API_REFERENCE.md):
    bootstrap-static/                -> players, teams, gameweeks, positions, game settings
    fixtures/                        -> all 380 fixtures with FDR and per-match stats
    element-summary/{player_id}/     -> a player's per-gameweek history + upcoming fixtures
    entry/{entry_id}/                -> a manager's public profile
    entry/{entry_id}/history/        -> the manager's gameweek-by-gameweek history and chips
    entry/{entry_id}/event/{gw}/picks/ -> the manager's 15 picks for a gameweek
    entry/{entry_id}/transfers/      -> the manager's transfer log
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 fpl-player-analysis/0.1"
    ),
    "Accept": "application/json",
}


class FPLAPIError(RuntimeError):
    """Raised when the API cannot be reached or returns a non-JSON / error response."""


class FPLClient:
    def __init__(
        self,
        base_url: str = "https://fantasy.premierleague.com/api",
        timeout: int = 30,
        max_retries: int = 3,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    # ------------------------------------------------------------------ core
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/{path.strip('/')}/"
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                if resp.status_code == 404:
                    raise FPLAPIError(f"404 Not Found: {url}")
                if resp.status_code in (429, 500, 502, 503, 504):
                    raise requests.HTTPError(f"{resp.status_code} from {url}", response=resp)
                resp.raise_for_status()
                return resp.json()
            except FPLAPIError:
                raise
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                wait = 2 ** (attempt - 1)
                log.warning("GET %s failed (attempt %d/%d): %s — retrying in %ss",
                            url, attempt, self.max_retries, exc, wait)
                time.sleep(wait)
        raise FPLAPIError(f"Giving up on {url}: {last_error}")

    # ------------------------------------------------------------- endpoints
    def bootstrap_static(self) -> dict[str, Any]:
        return self.get("bootstrap-static")

    def fixtures(self) -> list[dict[str, Any]]:
        return self.get("fixtures")

    def element_summary(self, player_id: int) -> dict[str, Any]:
        return self.get(f"element-summary/{player_id}")

    def entry(self, entry_id: int) -> dict[str, Any]:
        return self.get(f"entry/{entry_id}")

    def entry_history(self, entry_id: int) -> dict[str, Any]:
        return self.get(f"entry/{entry_id}/history")

    def entry_picks(self, entry_id: int, gameweek: int) -> dict[str, Any]:
        return self.get(f"entry/{entry_id}/event/{gameweek}/picks")

    def entry_transfers(self, entry_id: int) -> list[dict[str, Any]]:
        return self.get(f"entry/{entry_id}/transfers")


def current_and_next_gameweek(bootstrap: dict[str, Any]) -> tuple[int | None, int | None]:
    """Return ``(current_gw, next_gw)`` from a bootstrap payload.

    Pre-season both can be ``None``; after GW38 ``next`` is ``None``.
    """
    current = next((e["id"] for e in bootstrap["events"] if e.get("is_current")), None)
    nxt = next((e["id"] for e in bootstrap["events"] if e.get("is_next")), None)
    return current, nxt
