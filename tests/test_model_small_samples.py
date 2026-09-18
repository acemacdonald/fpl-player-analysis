"""Small-sample defences in the EP model: start share from real team games, PPG shrinkage.

Motivated by a live case (GW5 2026/27): a defender with ONE 90-minute clean sheet ranked 6th of all
defenders because (a) the API's teams[].played is always 0, so start_share was 1.0 for everyone, and
(b) his 8.0 points-per-game from one match fed 55% of the base rate unshrunk.
"""

from __future__ import annotations

import json

import pytest

from fpl_analysis.config import load_settings
from fpl_analysis.store import build, connect

from .synthetic import CURRENT_GW, build_snapshot, hermetic_paths


def test_team_games_are_counted_from_fixtures(built, settings):
    """teams[].played is 0 in the snapshot (as in the real API); we must count finished fixtures."""
    con = connect(settings, read_only=True)
    try:
        played = con.execute("SELECT DISTINCT played FROM raw.teams").fetchall()
        assert played == [(0,)], "synthetic season should mimic the API's dead field"
        rows = con.execute(
            "SELECT DISTINCT team_matches_played FROM marts.mart_player_form"
        ).fetchall()
        assert rows == [(CURRENT_GW,)]
        # start_share = starts / team games, capped at 1 — and it must actually bite for someone
        bad = con.execute(
            "SELECT COUNT(*) FROM marts.mart_player_form "
            f"WHERE ABS(start_share - LEAST(1.0, starts / {CURRENT_GW}.0)) > 1e-9"
        ).fetchone()[0]
        assert bad == 0
        partial = con.execute(
            "SELECT COUNT(*) FROM marts.mart_player_form WHERE start_share < 1.0"
        ).fetchone()[0]
        assert partial > 0
    finally:
        con.close()


def test_ppg_shrinkage_invariants(built, settings):
    """Shrunk PPG sits between the raw PPG and the position prior, and equals the prior at 0 minutes."""
    k = settings.shrinkage_games
    con = connect(settings, read_only=True)
    try:
        outside = con.execute(
            "SELECT COUNT(*) FROM marts.mart_player_form "
            "WHERE season_ppg_shrunk < LEAST(COALESCE(season_ppg, 0), position_prior_ppg) - 1e-9 "
            "   OR season_ppg_shrunk > GREATEST(COALESCE(season_ppg, 0), position_prior_ppg) + 1e-9"
        ).fetchone()[0]
        assert outside == 0
        unplayed = con.execute(
            "SELECT COUNT(*) FROM marts.mart_player_form "
            "WHERE minutes = 0 AND ABS(season_ppg_shrunk - position_prior_ppg) > 1e-9"
        ).fetchone()[0]
        assert unplayed == 0
        # the formula, checked by hand on a real row with a small sample
        row = con.execute(
            "SELECT season_ppg, minutes, position_prior_ppg, season_ppg_shrunk "
            "FROM marts.mart_player_form WHERE minutes = 45 AND season_ppg IS NOT NULL LIMIT 1"
        ).fetchone()
        assert row is not None
        ppg, minutes, prior, shrunk = row
        games = minutes / 90
        assert shrunk == pytest.approx((ppg * games + prior * k) / (games + k))
        # priors exist for all four positions and are sensible (appearance points at least)
        priors = dict(con.execute(
            "SELECT DISTINCT position_code, position_prior_ppg FROM marts.mart_player_form"
        ).fetchall())
        assert set(priors) == {"GKP", "DEF", "MID", "FWD"} and all(v > 0 for v in priors.values())
        # ppg_used in the EP mart is the shrunk figure, never the raw one
        mismatch = con.execute(
            "SELECT COUNT(*) FROM marts.mart_player_expected_points e "
            "JOIN marts.mart_player_form f USING (player_id) "
            "WHERE ABS(e.ppg_used - f.season_ppg_shrunk) > 1e-9"
        ).fetchone()[0]
        assert mismatch == 0
    finally:
        con.close()


def test_one_game_wonder_ranks_below_a_regular(tmp_path, project_root, monkeypatch):
    """The Affengruber case: one 8-point start must not out-rank four 8-point starts."""
    build_snapshot(tmp_path / "raw", include_history=False)
    snap = next((tmp_path / "raw").iterdir())
    boot_path = snap / "bootstrap_static.json"
    boot = json.loads(boot_path.read_text())
    defs = [e for e in boot["elements"] if e["element_type"] == 2 and e["team"] == 1]
    wonder, regular = defs[0], defs[1]
    for e, minutes, starts, form in ((wonder, 90, 1, "4.0"), (regular, 360, 4, "8.0")):
        e.update({
            "minutes": minutes, "starts": starts, "points_per_game": "8.0", "form": form,
            "total_points": 8 * starts, "status": "a", "chance_of_playing_next_round": None,
            "expected_goals": "0.00", "expected_assists": "0.00",
            "expected_goals_conceded": f"{1.2 * minutes / 90:.2f}", "bonus": 0,
        })
    boot_path.write_text(json.dumps(boot))
    for key, value in hermetic_paths(tmp_path).items():
        monkeypatch.setenv(key, value)
    s = load_settings(project_root / "config" / "settings.toml")
    build(s)
    con = connect(s, read_only=True)
    try:
        ep = dict(con.execute(
            "SELECT player_id, ep_horizon FROM marts.mart_player_horizon WHERE player_id IN (?, ?)",
            [wonder["id"], regular["id"]],
        ).fetchall())
        avail = dict(con.execute(
            "SELECT player_id, availability FROM marts.mart_player_form WHERE player_id IN (?, ?)",
            [wonder["id"], regular["id"]],
        ).fetchall())
    finally:
        con.close()
    assert avail[wonder["id"]] == pytest.approx(1 / CURRENT_GW)
    assert avail[regular["id"]] == pytest.approx(1.0)
    # same club, same fixtures: the gap is purely availability x shrinkage — expect at least 4x
    assert ep[regular["id"]] > 4 * ep[wonder["id"]]
