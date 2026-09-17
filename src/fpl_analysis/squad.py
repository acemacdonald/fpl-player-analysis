"""Squad-aware helpers that are awkward in SQL.

Free-transfer estimation
------------------------
The public API never tells you how many free transfers (FT) a manager has banked — that
number only exists behind login. We reconstruct it from the manager's gameweek history using
the rules in force since 2024/25:

* You start with 1 FT for the first gameweek *after* the one you joined in.
* Each subsequent gameweek adds 1 FT, banked up to a maximum of 5.
* Transfers made in a gameweek consume FTs first; any beyond that cost 4 points each and
  don't touch the bank (they can't — it's already 0).
* In a Wildcard or Free Hit gameweek, transfers are free and the FT bank is untouched.

The estimate can drift if the rules change or if a chip was refunded, so it is labelled an
estimate in the report and can be overridden in ``config/settings.toml``.
"""

from __future__ import annotations

from typing import Any

MAX_FREE_TRANSFERS = 5
FT_NEUTRAL_CHIPS = {"wildcard", "freehit"}


def estimate_free_transfers(
    gameweeks: list[dict[str, Any]],
    chips: list[dict[str, Any]],
    started_event: int | None,
    next_gameweek: int | None,
) -> int:
    """Estimate FTs available for ``next_gameweek``.

    ``gameweeks`` is ``entry/{id}/history``'s ``current`` list (one row per GW played, with
    ``event`` and ``event_transfers``); ``chips`` is its ``chips`` list (``name``, ``event``).
    """
    if not gameweeks or next_gameweek is None:
        return 1
    start = started_event or min(g["event"] for g in gameweeks)
    transfers_by_gw = {g["event"]: int(g.get("event_transfers") or 0) for g in gameweeks}
    chip_by_gw = {c["event"]: c["name"] for c in chips or []}

    ft = 1  # available entering the first GW after joining
    gw = start + 1
    while gw < next_gameweek:
        if chip_by_gw.get(gw) not in FT_NEUTRAL_CHIPS:
            used = min(transfers_by_gw.get(gw, 0), ft)
            ft -= used
        ft = min(MAX_FREE_TRANSFERS, ft + 1)  # roll into the next GW
        gw += 1
    return max(ft, 1)


def picks_summary(picks: list[dict[str, Any]]) -> dict[str, Any]:
    """Split the 15 picks into starters/bench and identify captain/vice."""
    starters = [p for p in picks if p["position"] <= 11]
    bench = [p for p in picks if p["position"] > 11]
    captain = next((p["element"] for p in picks if p.get("is_captain")), None)
    vice = next((p["element"] for p in picks if p.get("is_vice_captain")), None)
    return {
        "starter_ids": [p["element"] for p in starters],
        "bench_ids": [p["element"] for p in sorted(bench, key=lambda p: p["position"])],
        "captain_id": captain,
        "vice_captain_id": vice,
    }
