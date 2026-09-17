"""Manual squad override for the upcoming gameweek.

Why this exists
---------------
The public FPL API only reveals a manager's picks and transfers for a gameweek *after* that
gameweek's deadline. Before the deadline — the only time a decision tool is useful — the API
still shows last week's team. So on deadline day the report would model transfers against a
squad you no longer have.

``config/squad_override.toml`` lets you state the squad you actually hold for the upcoming
gameweek. When ``enabled = true`` the build replaces ``raw.entry_picks`` (and the bank) with it,
stamps ``source = 'manual override'`` on the meta row, and the report says so in its header.
Names resolve against ``raw.players`` by ``web_name``; ambiguous names (two "Palmer"s) must be
qualified with a team or position:  ``{ name = "Palmer", team = "IPS" }``.

The proper long-term fix is the authenticated ``my-team`` endpoint (project plan, Phase 2.0),
which also exposes your real free-transfer count. That needs a login cookie; this file needs
nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import duckdb

from .config import Settings

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[no-redef]

log = logging.getLogger(__name__)

OVERRIDE_FILE = "squad_override.toml"


@dataclass(frozen=True)
class SquadOverride:
    gameweek: int | None
    starters: list[dict[str, Any]]
    bench: list[dict[str, Any]]
    captain: dict[str, Any]
    vice_captain: dict[str, Any]
    bank_m: float
    free_transfers: int | None
    active_chip: str | None


def _as_spec(value: Any) -> dict[str, Any]:
    """Accept "Saka" or { name = "Palmer", team = "IPS" } / { name = "Palmer", position = "GKP" }."""
    if isinstance(value, str):
        return {"name": value}
    if isinstance(value, dict) and "name" in value:
        return {k: v for k, v in value.items() if k in ("name", "team", "position")}
    raise ValueError(f"Bad player entry in squad override: {value!r}")


def load_override(settings: Settings) -> SquadOverride | None:
    path = settings.project_root / "config" / OVERRIDE_FILE
    if not path.exists():
        return None
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    if not cfg.get("enabled", False):
        return None
    starters = [_as_spec(v) for v in cfg.get("starters", [])]
    bench = [_as_spec(v) for v in cfg.get("bench", [])]
    if len(starters) != 11 or len(bench) != 4:
        raise ValueError(f"squad override needs 11 starters and 4 bench players, got {len(starters)} + {len(bench)}")
    ft = cfg.get("free_transfers", -1)
    return SquadOverride(
        gameweek=cfg.get("gameweek"),
        starters=starters,
        bench=bench,
        captain=_as_spec(cfg["captain"]),
        vice_captain=_as_spec(cfg["vice_captain"]),
        bank_m=float(cfg.get("bank_m", 0.0)),
        free_transfers=None if ft is None or int(ft) < 0 else int(ft),
        active_chip=cfg.get("active_chip") or None,
    )


def _resolve(con: duckdb.DuckDBPyConnection, spec: dict[str, Any]) -> tuple[int, int]:
    """Return (player_id, position_id) for a spec, or raise with the candidates listed."""
    sql = """
        SELECT p.id, p.element_type, t.short_name, et.singular_name_short, p.web_name
        FROM raw.players AS p
        JOIN raw.teams AS t ON t.id = p.team
        JOIN raw.element_types AS et ON et.id = p.element_type
        WHERE LOWER(p.web_name) = LOWER(?)
    """
    params: list[Any] = [spec["name"]]
    if spec.get("team"):
        sql += " AND UPPER(t.short_name) = UPPER(?)"
        params.append(spec["team"])
    if spec.get("position"):
        sql += " AND UPPER(et.singular_name_short) = UPPER(?)"
        params.append(spec["position"])
    rows = con.execute(sql, params).fetchall()
    if len(rows) == 1:
        return int(rows[0][0]), int(rows[0][1])
    if not rows:
        near = con.execute(
            "SELECT web_name FROM raw.players WHERE LOWER(web_name) LIKE LOWER(?) LIMIT 8",
            [f"%{spec['name']}%"],
        ).fetchall()
        hint = ", ".join(r[0] for r in near) or "no similar names"
        raise ValueError(f"squad override: no player named {spec['name']!r} (similar: {hint})")
    cands = "; ".join(f"{r[4]} ({r[2]} {r[3]})" for r in rows)
    raise ValueError(
        f"squad override: {spec['name']!r} is ambiguous — qualify it with team or position. Candidates: {cands}"
    )


def apply_override(
    con: duckdb.DuckDBPyConnection, ov: SquadOverride, snapshot_id: str, next_gameweek: int | None
) -> int:
    """Replace raw.entry_picks + raw.entry_picks_meta with the override. Returns the number of picks."""
    gw = ov.gameweek or next_gameweek
    cap_id, _ = _resolve(con, ov.captain)
    vice_id, _ = _resolve(con, ov.vice_captain)
    rows: list[tuple[Any, ...]] = []
    seen: set[int] = set()
    for pos, spec in enumerate([*ov.starters, *ov.bench], start=1):
        pid, ptype = _resolve(con, spec)
        if pid in seen:
            raise ValueError(f"squad override: {spec['name']!r} appears twice")
        seen.add(pid)
        is_cap, is_vice = pid == cap_id, pid == vice_id
        multiplier = 0 if pos > 11 else (3 if is_cap and ov.active_chip == "3xc" else 2 if is_cap else 1)
        rows.append((snapshot_id, pid, pos, multiplier, is_cap, is_vice, ptype))
    if cap_id not in seen or vice_id not in seen:
        raise ValueError("squad override: captain and vice-captain must be in the 15")

    con.execute("DROP TABLE IF EXISTS raw.entry_picks")
    con.execute(
        """
        CREATE TABLE raw.entry_picks (
            snapshot_id VARCHAR, element INTEGER, position INTEGER, multiplier INTEGER,
            is_captain BOOLEAN, is_vice_captain BOOLEAN, element_type INTEGER
        )
        """
    )
    con.executemany("INSERT INTO raw.entry_picks VALUES (?, ?, ?, ?, ?, ?, ?)", rows)

    con.execute("DROP TABLE IF EXISTS raw.entry_picks_meta")
    con.execute(
        """
        CREATE TABLE raw.entry_picks_meta AS
        SELECT ? AS snapshot_id, ? AS event, ? AS active_chip, ? AS bank, NULL::INTEGER AS value,
               NULL::INTEGER AS event_transfers, NULL::INTEGER AS event_transfers_cost,
               NULL::INTEGER AS points_on_bench, 'manual override' AS source
        """,
        [snapshot_id, gw, ov.active_chip, int(round(ov.bank_m * 10))],
    )
    log.info("Squad override applied for GW%s: %d picks, bank £%.1fm", gw, len(rows), ov.bank_m)
    return len(rows)
