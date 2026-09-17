"""Warehouse layer: DuckDB + a small dbt-flavoured SQL model runner.

Why DuckDB
----------
* Embedded, single file (``data/fpl.duckdb``), zero cost, no trial expiry — right for a
  personal project that has to keep running every week of a 38-week season.
* Its SQL dialect is close to Snowflake's (CTEs, window functions, ``QUALIFY``, ``ILIKE``,
  ``TRY_CAST``), so the model files are written Snowflake-style and would lift across with
  little more than a search-and-replace of the raw-layer JSON loading.
* It reads JSON and Parquet natively and returns pandas DataFrames, so notebooks get SQL
  *and* pandas without a server.

Schemas
-------
``raw``      one table per API array, loaded verbatim from the latest snapshot (typed by DuckDB's
             JSON reader, strings left as strings), plus ``raw.snapshot``.
``history``  append-only per-snapshot facts (price, ownership, form) — the only place data
             from *previous* refreshes survives. Rebuilt from all snapshots on disk on demand.
``staging``  one view per raw table: renamed, typed, deduplicated. No business logic.
``marts``    tables with the analytics: fixture outlook, expected points, squad, transfers.

Model runner
------------
Each ``sql/<layer>/<model>.sql`` file becomes ``<layer>.<model>``. Files run in filename order,
so prefix with numbers when order matters. Files are Jinja templates with two helpers that
mirror dbt so a later move to dbt is mechanical:

    {{ ref('stg_players') }}      -> resolves to the qualified name of that model
    {{ var('horizon_gameweeks') }} -> a value from Settings

A leading ``-- materialized: table`` comment makes a model a table; the default is a view for
staging and a table for marts.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import jinja2

from .config import Settings
from .ingest import SnapshotInfo, latest_snapshot, list_snapshots
from .optimiser import build_chip_squads
from .squad_override import apply_override, load_override

log = logging.getLogger(__name__)

LAYERS = ("staging", "marts")
_MATERIALIZED_RE = re.compile(r"^\s*--\s*materialized:\s*(view|table)\s*$", re.IGNORECASE | re.MULTILINE)


# --------------------------------------------------------------------------- connection
def connect(settings: Settings, read_only: bool = False) -> duckdb.DuckDBPyConnection:
    if read_only and not settings.duckdb_path.exists():
        raise FileNotFoundError(
            f"Warehouse {settings.duckdb_path} does not exist yet. "
            "Run `fpl run` (or `fpl refresh` then `fpl build`) from the project root first."
        )
    settings.duckdb_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(settings.duckdb_path), read_only=read_only)
    for schema in ("raw", "history", *LAYERS):
        if not read_only:
            con.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    return con


# --------------------------------------------------------------------------- raw load
@dataclass(frozen=True)
class RawTableSpec:
    """How one raw table is produced from a snapshot file."""

    name: str
    file: str
    # For a file whose top level is an object, the key holding the array to unnest.
    array_key: str | None = None
    required: bool = True


RAW_TABLES: tuple[RawTableSpec, ...] = (
    RawTableSpec("players", "bootstrap_static.json", "elements"),
    RawTableSpec("teams", "bootstrap_static.json", "teams"),
    RawTableSpec("events", "bootstrap_static.json", "events"),
    RawTableSpec("element_types", "bootstrap_static.json", "element_types"),
    RawTableSpec("fixtures", "fixtures.json"),
    RawTableSpec("player_history", "player_history.json", required=False),
    RawTableSpec("player_history_past", "player_history_past.json", required=False),
    RawTableSpec("player_fixtures", "player_fixtures.json", required=False),
    RawTableSpec("entry", "entry.json", required=False),
    RawTableSpec("entry_gameweeks", "entry_history.json", "current", required=False),
    RawTableSpec("entry_chips", "entry_history.json", "chips", required=False),
    RawTableSpec("entry_picks", "entry_picks.json", "picks", required=False),
    RawTableSpec("entry_transfers", "entry_transfers.json", required=False),
)

_JSON_OPTS = "maximum_object_size=268435456, sample_size=-1"

# Optional raw tables must still EXIST (empty, correctly typed) when their snapshot file is
# absent, so that every staging view compiles regardless of which refresh flags were used.
# Column lists mirror the API payloads (docs/FPL_API_REFERENCE.md).
EMPTY_RAW_DDL: dict[str, str] = {
    "player_history": """
        snapshot_id VARCHAR, element INTEGER, fixture INTEGER, opponent_team INTEGER,
        total_points INTEGER, was_home BOOLEAN, kickoff_time VARCHAR, team_h_score INTEGER,
        team_a_score INTEGER, round INTEGER, modified BOOLEAN, minutes INTEGER,
        goals_scored INTEGER, assists INTEGER, clean_sheets INTEGER, goals_conceded INTEGER,
        own_goals INTEGER, penalties_saved INTEGER, penalties_missed INTEGER,
        yellow_cards INTEGER, red_cards INTEGER, saves INTEGER, bonus INTEGER, bps INTEGER,
        influence VARCHAR, creativity VARCHAR, threat VARCHAR, ict_index VARCHAR,
        clearances_blocks_interceptions INTEGER, recoveries INTEGER, tackles INTEGER,
        defensive_contribution INTEGER, starts INTEGER, expected_goals VARCHAR,
        expected_assists VARCHAR, expected_goal_involvements VARCHAR,
        expected_goals_conceded VARCHAR, value INTEGER, transfers_balance INTEGER,
        selected INTEGER, transfers_in INTEGER, transfers_out INTEGER
    """,
    "player_history_past": """
        snapshot_id VARCHAR, element INTEGER, season_name VARCHAR, element_code INTEGER,
        start_cost INTEGER, end_cost INTEGER, total_points INTEGER, minutes INTEGER,
        goals_scored INTEGER, assists INTEGER, clean_sheets INTEGER, goals_conceded INTEGER,
        own_goals INTEGER, penalties_saved INTEGER, penalties_missed INTEGER,
        yellow_cards INTEGER, red_cards INTEGER, saves INTEGER, bonus INTEGER, bps INTEGER,
        influence VARCHAR, creativity VARCHAR, threat VARCHAR, ict_index VARCHAR,
        clearances_blocks_interceptions INTEGER, recoveries INTEGER, tackles INTEGER,
        defensive_contribution INTEGER, starts INTEGER, expected_goals VARCHAR,
        expected_assists VARCHAR, expected_goal_involvements VARCHAR,
        expected_goals_conceded VARCHAR
    """,
    "player_fixtures": """
        snapshot_id VARCHAR, element INTEGER, id INTEGER, code INTEGER, team_h INTEGER,
        team_h_score INTEGER, team_a INTEGER, team_a_score INTEGER, event INTEGER,
        finished BOOLEAN, minutes INTEGER, provisional_start_time BOOLEAN,
        kickoff_time VARCHAR, event_name VARCHAR, is_home BOOLEAN, difficulty INTEGER
    """,
    "entry": """
        snapshot_id VARCHAR, id INTEGER, name VARCHAR, player_first_name VARCHAR,
        player_last_name VARCHAR, started_event INTEGER, summary_overall_points INTEGER,
        summary_overall_rank INTEGER, summary_event_points INTEGER, summary_event_rank INTEGER,
        current_event INTEGER, last_deadline_bank INTEGER, last_deadline_value INTEGER,
        last_deadline_total_transfers INTEGER
    """,
    "entry_gameweeks": """
        snapshot_id VARCHAR, event INTEGER, points INTEGER, total_points INTEGER, rank INTEGER,
        rank_sort INTEGER, overall_rank INTEGER, percentile_rank INTEGER, bank INTEGER,
        value INTEGER, event_transfers INTEGER, event_transfers_cost INTEGER,
        points_on_bench INTEGER
    """,
    "entry_chips": """
        snapshot_id VARCHAR, name VARCHAR, time VARCHAR, event INTEGER
    """,
    "entry_picks": """
        snapshot_id VARCHAR, element INTEGER, position INTEGER, multiplier INTEGER,
        is_captain BOOLEAN, is_vice_captain BOOLEAN, element_type INTEGER
    """,
    "entry_transfers": """
        snapshot_id VARCHAR, element_in INTEGER, element_in_cost INTEGER, element_out INTEGER,
        element_out_cost INTEGER, entry INTEGER, event INTEGER, time VARCHAR
    """,
    "entry_picks_meta": """
        snapshot_id VARCHAR, event INTEGER, active_chip VARCHAR, bank INTEGER, value INTEGER,
        event_transfers INTEGER, event_transfers_cost INTEGER, points_on_bench INTEGER, source VARCHAR
    """,
}


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_empty_array_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8").strip()
    return text in ("[]", "")


def load_snapshot(
    con: duckdb.DuckDBPyConnection, snapshot: SnapshotInfo, settings: Settings | None = None
) -> dict[str, int]:
    """Replace every ``raw.*`` table with the contents of ``snapshot``. Returns row counts."""
    counts: dict[str, int] = {}
    sid = _sql_str(snapshot.snapshot_id)
    array_lengths: dict[str, dict[str, int]] = {}  # file -> key -> len, for object-shaped files

    for spec in RAW_TABLES:
        path = snapshot.path / spec.file
        table = f"raw.{spec.name}"
        is_empty = not path.exists() or _is_empty_array_file(path)
        if not is_empty and spec.array_key:
            if spec.file not in array_lengths:
                payload = json.loads(path.read_text(encoding="utf-8"))
                array_lengths[spec.file] = {
                    k: len(v) for k, v in payload.items() if isinstance(v, list)
                }
            # An empty array gives DuckDB nothing to infer columns from -> use the typed DDL.
            is_empty = array_lengths[spec.file].get(spec.array_key, 0) == 0
        if is_empty:
            if spec.required:
                raise FileNotFoundError(f"Snapshot {snapshot.snapshot_id} is missing {spec.file}")
            con.execute(f"CREATE OR REPLACE TABLE {table} ({EMPTY_RAW_DDL[spec.name]})")
            counts[spec.name] = 0
            continue
        src = f"read_json_auto({_sql_str(str(path))}, {_JSON_OPTS})"
        if spec.array_key:
            select = f"SELECT {sid} AS snapshot_id, UNNEST({spec.array_key}, max_depth := 2) FROM {src}"
        else:
            select = f"SELECT {sid} AS snapshot_id, * FROM {src}"
        con.execute(f"CREATE OR REPLACE TABLE {table} AS {select}")
        counts[spec.name] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # entry_picks: the payload has picks[] + entry_history{} + active_chip; keep the scalars too
    picks_path = snapshot.path / "entry_picks.json"
    con.execute("DROP TABLE IF EXISTS raw.entry_picks_meta")
    if not picks_path.exists():
        con.execute(f"CREATE TABLE raw.entry_picks_meta ({EMPTY_RAW_DDL['entry_picks_meta']})")
    else:
        payload = json.loads(picks_path.read_text(encoding="utf-8"))
        con.execute(
            """
            CREATE TABLE raw.entry_picks_meta AS
            SELECT ? AS snapshot_id, ? AS event, ? AS active_chip,
                   ? AS bank, ? AS value, ? AS event_transfers, ? AS event_transfers_cost,
                   ? AS points_on_bench, 'api' AS source
            """,
            [
                snapshot.snapshot_id,
                payload.get("event"),
                payload.get("active_chip"),
                payload.get("entry_history", {}).get("bank"),
                payload.get("entry_history", {}).get("value"),
                payload.get("entry_history", {}).get("event_transfers"),
                payload.get("entry_history", {}).get("event_transfers_cost"),
                payload.get("entry_history", {}).get("points_on_bench"),
            ],
        )

    # Keep an untouched copy of the API picks: the squad override replaces raw.entry_picks, and the
    # report diffs the two to work out which transfers were made this week.
    con.execute("CREATE OR REPLACE TABLE raw.entry_picks_api AS SELECT * FROM raw.entry_picks")

    con.execute("DROP TABLE IF EXISTS raw.snapshot")
    con.execute(
        """
        CREATE TABLE raw.snapshot AS
        SELECT ? AS snapshot_id, CAST(? AS TIMESTAMPTZ) AS snapshot_ts,
               ? AS current_gameweek, ? AS next_gameweek, ? AS has_player_history
        """,
        [
            snapshot.snapshot_id,
            snapshot.snapshot_ts,
            snapshot.current_gameweek,
            snapshot.next_gameweek,
            snapshot.has_player_history,
        ],
    )
    log.info("Loaded snapshot %s: %s", snapshot.snapshot_id, counts)
    return counts


# --------------------------------------------------------------------------- history
HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS history.player_snapshot (
    snapshot_id          VARCHAR NOT NULL,
    snapshot_ts          TIMESTAMPTZ,
    gameweek             INTEGER,
    player_id            INTEGER NOT NULL,
    team_id              INTEGER,
    position_id          INTEGER,
    now_cost             INTEGER,
    selected_by_percent  DOUBLE,
    form                 DOUBLE,
    total_points         INTEGER,
    minutes              INTEGER,
    status               VARCHAR,
    chance_of_playing_next_round INTEGER,
    transfers_in_event   INTEGER,
    transfers_out_event  INTEGER,
    PRIMARY KEY (snapshot_id, player_id)
)
"""


def append_history(con: duckdb.DuckDBPyConnection) -> int:
    """Copy the key per-player facts of the currently loaded snapshot into ``history``.

    Idempotent: re-running for the same snapshot inserts nothing.
    """
    con.execute(HISTORY_DDL)
    before = con.execute("SELECT COUNT(*) FROM history.player_snapshot").fetchone()[0]
    con.execute(
        """
        INSERT OR IGNORE INTO history.player_snapshot
        SELECT
            p.snapshot_id,
            s.snapshot_ts,
            s.current_gameweek,
            p.id,
            p.team,
            p.element_type,
            p.now_cost,
            TRY_CAST(p.selected_by_percent AS DOUBLE),
            TRY_CAST(p.form AS DOUBLE),
            p.total_points,
            p.minutes,
            p.status,
            p.chance_of_playing_next_round,
            p.transfers_in_event,
            p.transfers_out_event
        FROM raw.players AS p
        CROSS JOIN raw.snapshot AS s
        """
    )
    after = con.execute("SELECT COUNT(*) FROM history.player_snapshot").fetchone()[0]
    return after - before


def rebuild_history(con: duckdb.DuckDBPyConnection, settings: Settings) -> int:
    """Replay every snapshot on disk into ``history`` (after a fresh clone, say)."""
    con.execute("DROP TABLE IF EXISTS history.player_snapshot")
    total = 0
    for snap in list_snapshots(settings):
        load_snapshot(con, snap, settings)
        total += append_history(con)
    return total


# --------------------------------------------------------------------------- models
@dataclass(frozen=True)
class Model:
    layer: str
    name: str
    path: Path
    materialized: str

    @property
    def qualified_name(self) -> str:
        return f"{self.layer}.{self.name}"


def discover_models(sql_dir: Path) -> list[Model]:
    models: list[Model] = []
    for layer in LAYERS:
        layer_dir = sql_dir / layer
        if not layer_dir.exists():
            continue
        default = "view" if layer == "staging" else "table"
        for path in sorted(layer_dir.glob("*.sql")):
            text = path.read_text(encoding="utf-8")
            m = _MATERIALIZED_RE.search(text)
            name = re.sub(r"^\d+_", "", path.stem)  # 010_stg_players.sql -> stg_players
            models.append(Model(layer, name, path, (m.group(1).lower() if m else default)))
    return models


def _jinja_env(models: list[Model], variables: dict[str, Any]) -> jinja2.Environment:
    by_name = {m.name: m.qualified_name for m in models}

    def ref(name: str) -> str:
        try:
            return by_name[name]
        except KeyError as exc:
            raise KeyError(f"ref('{name}') does not match any model in sql/") from exc

    def var(name: str, default: Any = None) -> Any:
        if name in variables:
            return variables[name]
        if default is not None:
            return default
        raise KeyError(f"var('{name}') is not defined in settings")

    env = jinja2.Environment(undefined=jinja2.StrictUndefined, autoescape=False)
    env.globals.update(ref=ref, var=var)
    return env


def model_variables(settings: Settings) -> dict[str, Any]:
    """The subset of settings exposed to SQL via ``var()``."""
    return {
        "entry_id": settings.entry_id,
        "horizon_gameweeks": settings.horizon_gameweeks,
        "differential_max_ownership": settings.differential_max_ownership,
        "min_chance_of_playing": settings.min_chance_of_playing,
        "min_minutes_for_rates": settings.min_minutes_for_rates,
        "weight_form": settings.weight_form,
        "weight_season_ppg": settings.weight_season_ppg,
        "weight_xgi": settings.weight_xgi,
        "fdr_multiplier": settings.fdr_multiplier,
    }


def render_model(model: Model, models: list[Model], variables: dict[str, Any]) -> str:
    env = _jinja_env(models, variables)
    template = env.from_string(model.path.read_text(encoding="utf-8"))
    return template.render()


def _drop_relation(con: duckdb.DuckDBPyConnection, schema: str, name: str) -> None:
    row = con.execute(
        "SELECT table_type FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, name],
    ).fetchone()
    if row is None:
        return
    kind = "VIEW" if row[0].upper() == "VIEW" else "TABLE"
    con.execute(f"DROP {kind} IF EXISTS {schema}.{name}")


def run_models(
    con: duckdb.DuckDBPyConnection,
    settings: Settings,
    only: set[str] | None = None,
) -> list[Model]:
    """Render and execute every model in order. Returns the models that ran."""
    models = discover_models(settings.sql_dir)
    variables = model_variables(settings)
    ran: list[Model] = []
    for model in models:
        if only and model.name not in only:
            continue
        sql = render_model(model, models, variables)
        body = _MATERIALIZED_RE.sub("", sql).strip().rstrip(";")
        kind = "TABLE" if model.materialized == "table" else "VIEW"
        # A view can't replace a table of the same name (and vice versa): drop whatever exists.
        _drop_relation(con, model.layer, model.name)
        try:
            con.execute(f"CREATE {kind} {model.qualified_name} AS\n{body}")
        except duckdb.Error as exc:
            raise RuntimeError(f"Model {model.qualified_name} failed ({model.path}): {exc}") from exc
        ran.append(model)
        log.info("built %s (%s)", model.qualified_name, model.materialized)
    return ran


# --------------------------------------------------------------------------- convenience
def build(settings: Settings, snapshot: SnapshotInfo | None = None) -> dict[str, Any]:
    """Load the latest (or given) snapshot into raw, append history, run all models."""
    snapshot = snapshot or latest_snapshot(settings)
    con = connect(settings)
    override_applied = False
    try:
        counts = load_snapshot(con, snapshot, settings)
        override = load_override(settings)
        if override is not None:
            counts["entry_picks"] = apply_override(con, override, snapshot.snapshot_id, snapshot.next_gameweek)
            override_applied = True
        new_history_rows = append_history(con)
        models = run_models(con, settings)
        chips = build_chip_squads(con, settings)  # Python-solved mart, reads mart_player_horizon
    finally:
        con.close()
    return {
        "snapshot_id": snapshot.snapshot_id,
        "raw_counts": counts,
        "history_rows_added": new_history_rows,
        "models_built": [m.qualified_name for m in models] + ["marts.mart_chip_squads"],
        "squad_override_applied": override_applied,
        "chip_squads": {k: (None if v is None else v.xi_ep) for k, v in chips.items()},
    }


def query(settings: Settings, sql: str, params: list[Any] | None = None):
    """Run a read-only query and return a pandas DataFrame (the notebook workhorse)."""
    con = connect(settings, read_only=True)
    try:
        return con.execute(sql, params or []).df()
    finally:
        con.close()
