"""Gameweek report: turn the mart tables into the Markdown decision document.

The report is the product. Everything upstream exists so that, once a week, this file can
answer five questions in order:

1. Where do I stand? (points, rank, bank, estimated free transfers, deadline)
2. Is anyone in my squad a problem? (injury/suspension flags, bad fixture runs)
3. Who do I captain?
4. Which transfer(s), if any, buy the most expected points over the horizon?
5. Who are the best players by position right now, and which cheap differentials exist?

Rendering is a Jinja template (``templates/gameweek_report.md.j2``) fed with pandas frames
already converted to Markdown tables, so the layout can change without touching SQL.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import jinja2
import pandas as pd

from .config import Settings
from .ingest import SnapshotInfo, latest_snapshot
from .squad import estimate_free_transfers
from .squad_override import load_override
from .store import connect

log = logging.getLogger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"


@dataclass
class ReportContext:
    gameweek: int
    path: Path
    markdown: str


def _md(df: pd.DataFrame, columns: dict[str, str], floatfmt: str = ".2f") -> str:
    """Select+rename columns and render as a Markdown table ('—' for an empty frame)."""
    if df.empty:
        return "_No rows._"
    view = df[list(columns)].rename(columns=columns)
    return view.to_markdown(index=False, floatfmt=floatfmt)


def _fmt_deadline(ts: Any, tz: str = "UTC") -> str:
    if ts is None or (isinstance(ts, float) and pd.isna(ts)):
        return "unknown"
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert(tz).strftime("%a %d %b %Y, %H:%M %Z")


def build_report(settings: Settings, snapshot: SnapshotInfo | None = None,
                 write: bool = True) -> ReportContext:
    snapshot = snapshot or latest_snapshot(settings)
    con = connect(settings, read_only=True)
    try:
        ctx = con.execute("SELECT * FROM staging.stg_snapshot").df().iloc[0]
        next_gw = None if pd.isna(ctx["next_gameweek"]) else int(ctx["next_gameweek"])
        if next_gw is None:
            raise RuntimeError("No upcoming gameweek in this snapshot (season over?)")

        gw_row = con.execute(
            "SELECT * FROM staging.stg_gameweeks WHERE gameweek = ?", [next_gw]
        ).df()
        deadline = (_fmt_deadline(gw_row["deadline_ts"].iloc[0], settings.report_timezone)
                    if not gw_row.empty else "unknown")

        squad = con.execute(
            "SELECT * FROM marts.mart_squad ORDER BY squad_position"
        ).df()
        has_squad = not squad.empty

        entry = con.execute("SELECT * FROM raw.entry").df() if _table_exists(con, "raw", "entry") else pd.DataFrame()
        entry_gws = con.execute(
            "SELECT * FROM staging.stg_entry_gameweeks ORDER BY gameweek"
        ).df()
        chips = (
            con.execute("SELECT name, event FROM raw.entry_chips").df()
            if _table_exists(con, "raw", "entry_chips") else pd.DataFrame()
        )

        free_transfers = _free_transfers(settings, entry, entry_gws, chips, next_gw)
        override = load_override(settings)
        if override is not None and override.free_transfers is not None:
            free_transfers = {"value": override.free_transfers, "source": "squad override file"}

        captaincy = con.execute(
            "SELECT * FROM marts.mart_captaincy ORDER BY captaincy_rank LIMIT ?",
            [settings.captaincy_n],
        ).df()
        captaincy_owned = con.execute(
            "SELECT * FROM marts.mart_captaincy WHERE in_squad ORDER BY captaincy_rank LIMIT 5"
        ).df()

        transfers = con.execute(
            """
            SELECT * FROM marts.mart_transfer_candidates
            WHERE candidate_rank <= ?
            ORDER BY out_ep_horizon ASC, candidate_rank
            """,
            [settings.transfer_suggestions_n],
        ).df()
        best_single = con.execute(
            """
            SELECT * FROM marts.mart_transfer_candidates
            WHERE candidate_rank = 1
            ORDER BY ep_horizon_gain DESC
            LIMIT 5
            """
        ).df()

        top_picks = {}
        for code in ("GKP", "DEF", "MID", "FWD"):
            top_picks[code] = con.execute(
                """
                SELECT * FROM marts.mart_player_horizon
                WHERE position_code = ? AND availability > 0
                ORDER BY ep_horizon DESC
                LIMIT ?
                """,
                [code, settings.top_n_per_position],
            ).df()

        value_picks = con.execute(
            """
            SELECT * FROM marts.mart_player_horizon
            WHERE availability > 0 AND minutes >= ? AND NOT is_injury_risk
            ORDER BY ep_horizon_per_m DESC
            LIMIT 10
            """,
            [settings.min_minutes_for_rates],
        ).df()

        differentials = con.execute(
            "SELECT * FROM marts.mart_differentials ORDER BY differential_rank LIMIT 12"
        ).df()

        fixtures = con.execute(
            "SELECT * FROM marts.mart_team_fixture_summary ORDER BY fixture_rank, team_short_name"
        ).df()

        movers = con.execute(
            """
            SELECT h.web_name, h.team_short_name, h.position_code, h.price_m, h.selected_by_pct,
                   p.net_transfers_event, h.ep_next
            FROM marts.mart_player_horizon AS h
            JOIN staging.stg_players AS p
              ON p.player_id = h.player_id
            ORDER BY p.net_transfers_event DESC
            LIMIT 8
            """
        ).df()

        chip_squads = (
            con.execute("SELECT * FROM marts.mart_chip_squads ORDER BY chip, slot").df()
            if _table_exists(con, "marts", "mart_chip_squads") else pd.DataFrame()
        )

        api_picks = (
            con.execute(
                """
                SELECT a.element AS player_id, p.web_name, p.price_m
                FROM raw.entry_picks_api AS a
                JOIN staging.stg_players AS p ON p.player_id = a.element
                """
            ).df()
            if _table_exists(con, "raw", "entry_picks_api") else pd.DataFrame()
        )
    finally:
        con.close()

    chips_ctx = {chip: _chip_context(chip_squads, chip, starters_ep_col)
                 for chip, starters_ep_col in (("freehit", "ep_next"), ("freehit_gw2", "ep_gw2"),
                                               ("wildcard", "ep_horizon"))}

    picks_source = _scalar(squad["picks_source"].iloc[0]) if has_squad else None
    active_chip = _scalar(squad["active_chip"].iloc[0]) if has_squad else None
    weekly = _weekly_transfers(squad, api_picks, picks_source, active_chip, free_transfers) if has_squad else None
    if has_squad and _scalar(squad["squad_value_m"].iloc[0]) is None:
        # override squads carry no API value: use current prices (selling values may differ slightly)
        squad_value_m = round(float(squad["price_m"].sum()), 1)
        squad_value_note = " (current prices)"
    elif has_squad:
        squad_value_m = float(squad["squad_value_m"].iloc[0])
        squad_value_note = ""
    else:
        squad_value_m, squad_value_note = None, ""

    squad_risks = (
        squad[(squad["is_injury_risk"].fillna(False).astype(bool)) | (squad["status"] != "a")]
        if has_squad else pd.DataFrame()
    )
    bench = squad[~squad["is_starter"].astype(bool)] if has_squad else pd.DataFrame()
    starters = squad[squad["is_starter"].astype(bool)] if has_squad else pd.DataFrame()

    template = jinja2.Environment(
        loader=jinja2.FileSystemLoader(TEMPLATE_DIR),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    ).get_template("gameweek_report.md.j2")

    manager = {}
    if not entry.empty:
        e = entry.iloc[0]
        manager = {
            "team_name": _scalar(e.get("name")),
            "manager": " ".join(
                str(_scalar(e.get(k)) or "") for k in ("player_first_name", "player_last_name")
            ).strip(),
            "overall_points": _scalar(e.get("summary_overall_points")),
            "overall_rank": _scalar(e.get("summary_overall_rank")),
            "last_gw_points": _scalar(e.get("summary_event_points")),
        }
    latest_gw = entry_gws.iloc[-1] if not entry_gws.empty else None

    markdown = template.render(
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        snapshot_id=snapshot.snapshot_id,
        gameweek=next_gw,
        deadline=deadline,
        horizon=settings.horizon_gameweeks,
        manager=manager,
        has_squad=has_squad,
        bank_m=float(squad["bank_m"].iloc[0]) if has_squad else None,
        squad_value_m=squad_value_m,
        squad_value_note=squad_value_note,
        weekly=weekly,
        picks_source=picks_source,
        picks_gameweek=_scalar(squad["picks_gameweek"].iloc[0]) if has_squad else None,
        free_transfers=free_transfers,
        active_chip=active_chip,
        chips_used=", ".join(f"{r['name']} (GW{r['event']})" for _, r in chips.iterrows()) or "none",
        points_on_bench=_scalar(latest_gw["points_on_bench"]) if latest_gw is not None else None,
        squad_ep_next=round(float(starters["ep_next"].sum()), 1) if has_squad else None,
        current_xi_ep_next_c=_xi_ep_with_captain(starters, "ep_next") if has_squad else None,
        current_xi_ep_gw2_c=_xi_ep_with_captain(starters, "ep_gw2") if has_squad else None,
        current_xi_ep_horizon_c=_xi_ep_with_captain(starters, "ep_horizon") if has_squad else None,
        chips=chips_ctx,
        starters_md=_md(starters, {
            "position_code": "Pos", "web_name": "Player", "team_short_name": "Team",
            "price_m": "£m", "next_fixture_label": "Next", "form_signal": "Form",
            "xgi_per_90": "xGI/90", "availability": "Avail", "ep_next": "EP next",
            "ep_horizon": f"EP {settings.horizon_gameweeks}GW", "rank_in_position": "Pos rank",
        }),
        bench_md=_md(bench, {
            "squad_position": "Slot", "position_code": "Pos", "web_name": "Player",
            "team_short_name": "Team", "next_fixture_label": "Next", "ep_next": "EP next",
            "ep_horizon": f"EP {settings.horizon_gameweeks}GW",
        }),
        risks_md=_md(squad_risks, {
            "web_name": "Player", "status": "Status", "chance_of_playing_next_round": "Chance %",
            "news": "News",
        }, floatfmt=".0f"),
        captain_now=_captain_names(squad) if has_squad else None,
        captaincy_owned_md=_md(captaincy_owned, {
            "captaincy_rank": "Overall rank", "web_name": "Player", "team_short_name": "Team",
            "next_fixture_label": "Fixture", "form_signal": "Form", "xgi_per_90": "xGI/90",
            "ep_next": "EP", "ep_as_captain": "EP as (C)",
        }),
        captaincy_md=_md(captaincy.assign(in_squad=captaincy["in_squad"].map({True: "yes", False: ""})), {
            "captaincy_rank": "Rank", "web_name": "Player", "team_short_name": "Team",
            "position_code": "Pos", "next_fixture_label": "Fixture", "selected_by_pct": "Own %",
            "form_signal": "Form", "xgi_per_90": "xGI/90", "ep_next": "EP", "in_squad": "Own?",
        }),
        best_single_md=_md(best_single, {
            "out_web_name": "Out", "out_ep_horizon": "Out EP", "in_web_name": "In",
            "in_team": "In team", "in_price_m": "In £m", "in_fixture_run": "In fixtures",
            "in_ep_horizon": "In EP", "ep_horizon_gain": "Gain", "bank_after_m": "Bank after",
        }),
        transfers_md=_md(transfers, {
            "out_web_name": "Out", "position_code": "Pos", "candidate_rank": "#",
            "in_web_name": "In", "in_team": "Team", "in_price_m": "£m",
            "in_selected_by_pct": "Own %", "in_form": "Form", "in_ep_next": "EP next",
            "in_ep_horizon": "In EP", "ep_horizon_gain": "Gain",
        }),
        top_picks_md={
            code: _md(df, {
                "rank_in_position": "#", "web_name": "Player", "team_short_name": "Team",
                "price_m": "£m", "selected_by_pct": "Own %", "form_signal": "Form",
                "xgi_per_90": "xGI/90", "fixture_run": "Fixtures", "ep_next": "EP next",
                "ep_horizon": f"EP {settings.horizon_gameweeks}GW",
            })
            for code, df in top_picks.items()
        },
        value_md=_md(value_picks, {
            "web_name": "Player", "team_short_name": "Team", "position_code": "Pos",
            "price_m": "£m", "selected_by_pct": "Own %", "ep_horizon": "EP",
            "ep_horizon_per_m": "EP per £m",
        }),
        differentials_md=_md(differentials, {
            "differential_rank": "#", "web_name": "Player", "team_short_name": "Team",
            "position_code": "Pos", "price_m": "£m", "selected_by_pct": "Own %",
            "form_signal": "Form", "xgi_per_90": "xGI/90", "fixture_run": "Fixtures",
            "ep_horizon": "EP",
        }),
        fixtures_md=_md(fixtures, {
            "fixture_rank": "#", "team_short_name": "Team", "fixtures_in_horizon": "Games",
            "home_fixtures": "Home", "avg_fdr": "Avg FDR", "fixture_score": "Score",
            "fixture_run": "Run",
        }),
        movers_md=_md(movers, {
            "web_name": "Player", "team_short_name": "Team", "position_code": "Pos",
            "price_m": "£m", "selected_by_pct": "Own %", "net_transfers_event": "Net transfers",
            "ep_next": "EP next",
        }, floatfmt=",.2f"),
        weights=(settings.weight_form, settings.weight_season_ppg, settings.weight_xgi),
        differential_threshold=settings.differential_max_ownership,
    )

    path = settings.reports_dir / f"GW{next_gw:02d}.md"
    if write:
        settings.reports_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        log.info("Wrote %s", path)
    return ReportContext(gameweek=next_gw, path=path, markdown=markdown)


FT_NEUTRAL_CHIPS = {"wildcard", "freehit"}
HIT_COST = 4


def _weekly_transfers(
    squad: pd.DataFrame,
    api_picks: pd.DataFrame,
    picks_source: str | None,
    active_chip: str | None,
    free_transfers: dict[str, Any],
) -> dict[str, Any]:
    """What the override squad implies about this week's transfers and the free-transfer budget.

    ``free_transfers['value']`` is the count GOING INTO the gameweek (estimated from history or
    stated in the override file). Transfers made = players in the override squad that were not in
    the last API picks. Wildcard / Free Hit weeks are free and leave the bank untouched.
    """
    if picks_source != "manual override" or api_picks.empty:
        return {"known": False, "made": 0, "ins": [], "outs": [], "ft_in": free_transfers.get("value"),
                "ft_left": free_transfers.get("value"), "hit": 0, "chip_free": False}
    squad_ids = set(int(x) for x in squad["player_id"])
    api_ids = set(int(x) for x in api_picks["player_id"])
    ins = squad[squad["player_id"].isin(squad_ids - api_ids)]["web_name"].tolist()
    outs = api_picks[api_picks["player_id"].isin(api_ids - squad_ids)]["web_name"].tolist()
    made = max(len(ins), len(outs))
    ft_in = free_transfers.get("value")
    chip_free = (active_chip or "") in FT_NEUTRAL_CHIPS
    if ft_in is None:
        ft_left, hit = None, None
    elif chip_free:
        ft_left, hit = ft_in, 0
    else:
        used = min(made, int(ft_in))
        ft_left = int(ft_in) - used
        hit = HIT_COST * (made - used)
    return {"known": True, "made": made, "ins": ins, "outs": outs, "ft_in": ft_in,
            "ft_left": ft_left, "hit": hit, "chip_free": chip_free}


def _xi_ep_with_captain(starters: pd.DataFrame, ep_col: str) -> float:
    """Sum of the XI's EP with the captain counted twice — the basis the chip squads are scored on."""
    cap = starters["is_captain"].fillna(False).astype(bool)
    return round(float(starters[ep_col].sum() + starters.loc[cap, ep_col].sum()), 2)


def _chip_context(chip_squads: pd.DataFrame, chip: str, ep_col: str) -> dict[str, Any] | None:
    """Tables and summary figures for one chip section of the report."""
    if chip_squads.empty:
        return None
    df = chip_squads[chip_squads["chip"] == chip].copy()
    if df.empty:
        return None
    df["captain_mark"] = df["is_captain"].map({True: "(C)", False: ""})
    df["owned_mark"] = df["in_current_squad"].map({True: "yes", False: ""})
    starters = df[df["is_starter"].astype(bool)]
    bench = df[~df["is_starter"].astype(bool)]
    ep_label, fixture_col, fixture_label = {
        "ep_next": ("EP next", "next_fixture_label", "Next"),
        "ep_gw2": ("EP GW+1", "gw2_fixture_label", "Fixture"),
        "ep_horizon": ("EP horizon", "fixture_run", "Fixtures"),
    }[ep_col]
    cols = {
        "slot": "#", "position_code": "Pos", "web_name": "Player", "captain_mark": "",
        "team_short_name": "Team", "price_m": "£m", "selected_by_pct": "Own %",
        "form_signal": "Form", "xgi_per_90": "xGI/90",
        fixture_col: fixture_label,
        "chip_ep": ep_label, "owned_mark": "Own?",
    }
    first = df.iloc[0]
    return {
        "xi_ep": _scalar(first["xi_ep"]),
        "cost_m": _scalar(first["cost_m"]),
        "budget_m": _scalar(first["budget_m"]),
        "budget_source": _scalar(first["budget_source"]),
        "bench_ep": round(float(bench["chip_ep"].sum()), 2),
        "owned_count": int(df["in_current_squad"].sum()),
        "captain": _scalar(starters.loc[starters["is_captain"].astype(bool), "web_name"].iloc[0]),
        "starters_md": _md(starters, cols),
        "bench_md": _md(bench, cols),
    }


def _scalar(value: Any) -> Any:
    """pandas returns NA/NaN/numpy scalars; templates want plain Python values or None."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(value, "item"):  # numpy scalar -> python
        return value.item()
    return value


def _table_exists(con, schema: str, table: str) -> bool:
    row = con.execute(
        "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = ? AND table_name = ?",
        [schema, table],
    ).fetchone()
    return bool(row and row[0])


def _captain_names(squad: pd.DataFrame) -> dict[str, str | None]:
    cap = squad[squad["is_captain"].fillna(False).astype(bool)]["web_name"]
    vice = squad[squad["is_vice_captain"].fillna(False).astype(bool)]["web_name"]
    return {
        "captain": cap.iloc[0] if not cap.empty else None,
        "vice": vice.iloc[0] if not vice.empty else None,
    }


def _free_transfers(settings: Settings, entry: pd.DataFrame, entry_gws: pd.DataFrame,
                    chips: pd.DataFrame, next_gw: int) -> dict[str, Any]:
    if settings.free_transfers_override >= 0:
        return {"value": settings.free_transfers_override, "source": "config override"}
    if entry.empty or entry_gws.empty:
        return {"value": None, "source": "unknown (no entry data)"}
    gws = [
        {"event": int(r["gameweek"]), "event_transfers": int(r["event_transfers"])}
        for _, r in entry_gws.iterrows()
    ]
    chip_rows = [{"name": r["name"], "event": int(r["event"])} for _, r in chips.iterrows()]
    started = entry.iloc[0].get("started_event")
    started = int(started) if started is not None and not pd.isna(started) else None
    value = estimate_free_transfers(gws, chip_rows, started, next_gw)
    return {"value": value, "source": "estimated from transfer history"}


def dump_json(settings: Settings, table: str, path: Path) -> None:
    """Utility: export a mart as JSON (handy for feeding a future dashboard)."""
    con = connect(settings, read_only=True)
    try:
        df = con.execute(f"SELECT * FROM {table}").df()
    finally:
        con.close()
    path.write_text(json.dumps(json.loads(df.to_json(orient="records")), indent=2))
