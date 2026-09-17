"""Raw layer: pull the API and write an immutable, dated snapshot.

Design choice — snapshot everything, every time
------------------------------------------------
The FPL API only ever shows *now*: current prices, current ownership, current injury news.
Anything about *change over time* (price rises, ownership swings, form trajectories) has to be
built by us, from snapshots. So every ``fpl refresh`` writes a complete, timestamped copy of the
raw JSON under ``data/raw/<snapshot_id>/`` and never touches an earlier snapshot. This is the
same landing-zone pattern you'd use with a Snowflake external stage: land raw, load raw, transform
in SQL.

Snapshot layout::

    data/raw/20260917_101500/
        manifest.json               who/when/what; current & next GW; row counts
        bootstrap_static.json       verbatim response
        fixtures.json               verbatim response
        entry.json                  verbatim response (manager profile)
        entry_history.json          verbatim response
        entry_picks.json            verbatim response for the *current* GW (+ "event" key)
        entry_transfers.json        verbatim response
        player_history.json         [optional] rows of element-summary.history for every player
        player_history_past.json    [optional] rows of element-summary.history_past
        player_fixtures.json        [optional] rows of element-summary.fixtures

The three ``player_*`` files come from one call per player (~660 calls). They are the only
expensive part of a refresh, so they are behind ``include_player_history``.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .api import FPLAPIError, FPLClient, current_and_next_gameweek
from .config import Settings

log = logging.getLogger(__name__)

SNAPSHOT_FORMAT = "%Y%m%d_%H%M%S"


@dataclass(frozen=True)
class SnapshotInfo:
    snapshot_id: str
    path: Path
    snapshot_ts: str
    current_gameweek: int | None
    next_gameweek: int | None
    has_player_history: bool


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def create_snapshot(
    settings: Settings,
    client: FPLClient | None = None,
    include_player_history: bool = True,
    include_entry: bool = True,
    progress_every: int = 100,
) -> SnapshotInfo:
    """Pull the API and write a new snapshot directory. Returns its metadata."""
    client = client or FPLClient(
        base_url=settings.base_url,
        timeout=settings.timeout_seconds,
        max_retries=settings.max_retries,
    )
    now = datetime.now(timezone.utc)
    snapshot_id = now.strftime(SNAPSHOT_FORMAT)
    out = settings.raw_dir / snapshot_id
    out.mkdir(parents=True, exist_ok=False)
    log.info("Writing snapshot %s", out)

    bootstrap = client.bootstrap_static()
    _write_json(out / "bootstrap_static.json", bootstrap)
    current_gw, next_gw = current_and_next_gameweek(bootstrap)

    fixtures = client.fixtures()
    _write_json(out / "fixtures.json", fixtures)

    counts: dict[str, int] = {
        "players": len(bootstrap["elements"]),
        "teams": len(bootstrap["teams"]),
        "events": len(bootstrap["events"]),
        "fixtures": len(fixtures),
    }

    if include_entry:
        _ingest_entry(client, settings.entry_id, current_gw, out, counts)

    if include_player_history:
        _ingest_player_history(client, bootstrap, settings, out, counts, progress_every)

    manifest = {
        "snapshot_id": snapshot_id,
        "snapshot_ts": now.isoformat(),
        "base_url": settings.base_url,
        "entry_id": settings.entry_id if include_entry else None,
        "current_gameweek": current_gw,
        "next_gameweek": next_gw,
        "has_player_history": include_player_history,
        "counts": counts,
    }
    _write_json(out / "manifest.json", manifest)
    return SnapshotInfo(
        snapshot_id=snapshot_id,
        path=out,
        snapshot_ts=manifest["snapshot_ts"],
        current_gameweek=current_gw,
        next_gameweek=next_gw,
        has_player_history=include_player_history,
    )


def _ingest_entry(
    client: FPLClient, entry_id: int, current_gw: int | None, out: Path, counts: dict[str, int]
) -> None:
    try:
        entry = client.entry(entry_id)
    except FPLAPIError as exc:
        log.warning("Could not fetch entry %s (%s) — squad-aware features will be empty", entry_id, exc)
        return
    _write_json(out / "entry.json", entry)
    _write_json(out / "entry_history.json", client.entry_history(entry_id))
    _write_json(out / "entry_transfers.json", client.entry_transfers(entry_id))
    if current_gw:
        picks = client.entry_picks(entry_id, current_gw)
        picks["event"] = current_gw  # the payload doesn't repeat the GW; we need it downstream
        _write_json(out / "entry_picks.json", picks)
        counts["entry_picks"] = len(picks.get("picks", []))


def _ingest_player_history(
    client: FPLClient,
    bootstrap: dict[str, Any],
    settings: Settings,
    out: Path,
    counts: dict[str, int],
    progress_every: int,
) -> None:
    history: list[dict[str, Any]] = []
    history_past: list[dict[str, Any]] = []
    upcoming: list[dict[str, Any]] = []
    players = bootstrap["elements"]
    for i, player in enumerate(players, start=1):
        pid = player["id"]
        try:
            summary = client.element_summary(pid)
        except FPLAPIError as exc:
            log.warning("element-summary/%s failed: %s", pid, exc)
            continue
        history.extend(summary.get("history", []))
        for row in summary.get("history_past", []):
            row["element"] = pid  # history_past rows only carry element_code; add the id
            history_past.append(row)
        for row in summary.get("fixtures", []):
            row["element"] = pid
            upcoming.append(row)
        if i % progress_every == 0:
            log.info("  element-summary %d/%d", i, len(players))
        time.sleep(settings.per_player_sleep_seconds)

    _write_json(out / "player_history.json", history)
    _write_json(out / "player_history_past.json", history_past)
    _write_json(out / "player_fixtures.json", upcoming)
    counts["player_history_rows"] = len(history)
    counts["player_history_past_rows"] = len(history_past)
    counts["player_fixture_rows"] = len(upcoming)


def list_snapshots(settings: Settings) -> list[SnapshotInfo]:
    """All snapshots on disk, oldest first."""
    infos: list[SnapshotInfo] = []
    if not settings.raw_dir.exists():
        return infos
    for path in sorted(p for p in settings.raw_dir.iterdir() if p.is_dir()):
        manifest_path = path / "manifest.json"
        if not manifest_path.exists():
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        infos.append(
            SnapshotInfo(
                snapshot_id=m["snapshot_id"],
                path=path,
                snapshot_ts=m["snapshot_ts"],
                current_gameweek=m.get("current_gameweek"),
                next_gameweek=m.get("next_gameweek"),
                has_player_history=bool(m.get("has_player_history")),
            )
        )
    return infos


def latest_snapshot(settings: Settings) -> SnapshotInfo:
    snaps = list_snapshots(settings)
    if not snaps:
        raise FileNotFoundError(
            f"No snapshots under {settings.raw_dir}. Run `fpl refresh` first."
        )
    return snaps[-1]


def get_snapshot(settings: Settings, snapshot_id: str) -> SnapshotInfo:
    for s in list_snapshots(settings):
        if s.snapshot_id == snapshot_id:
            return s
    raise FileNotFoundError(f"Snapshot {snapshot_id} not found under {settings.raw_dir}")
