"""End-to-end: synthetic snapshot -> DuckDB raw -> staging -> marts -> report."""

from __future__ import annotations

import pytest

from fpl_analysis.report import build_report
from fpl_analysis.store import connect, discover_models


def test_build_runs_every_model(built, settings):
    names = {m.name for m in discover_models(settings.sql_dir)}
    assert names <= {q.split(".")[1] for q in built["models_built"]}
    assert built["raw_counts"]["players"] == 120
    assert built["raw_counts"]["fixtures"] == 380


def test_staging_types(built, settings):
    con = connect(settings, read_only=True)
    try:
        row = con.execute(
            "SELECT typeof(form), typeof(price_m), typeof(xgi_per_90) FROM staging.stg_players LIMIT 1"
        ).fetchone()
        assert row == ("DOUBLE", "DOUBLE", "DOUBLE")
        # one row per team per fixture
        n = con.execute("SELECT COUNT(*) FROM staging.stg_team_fixtures").fetchone()[0]
        assert n == 760
    finally:
        con.close()


def test_fixture_outlook_respects_horizon(built, settings):
    con = connect(settings, read_only=True)
    try:
        lo, hi = con.execute(
            "SELECT MIN(gameweek), MAX(gameweek) FROM marts.mart_team_fixture_outlook"
        ).fetchone()
        assert lo == 5 and hi == 5 + settings.horizon_gameweeks - 1
        per_team = con.execute(
            "SELECT COUNT(DISTINCT team_id), MIN(fixtures_in_horizon), MAX(fixtures_in_horizon) "
            "FROM marts.mart_team_fixture_summary"
        ).fetchone()
        assert per_team == (20, settings.horizon_gameweeks, settings.horizon_gameweeks)
    finally:
        con.close()


def test_expected_points_are_sane(built, settings):
    con = connect(settings, read_only=True)
    try:
        stats = con.execute(
            "SELECT MIN(expected_points), MAX(expected_points), COUNT(*) "
            "FROM marts.mart_player_expected_points"
        ).fetchone()
        assert stats[0] >= 0
        assert stats[1] < 20  # nobody is modelled at >20 points in a single fixture
        assert stats[2] == 120 * settings.horizon_gameweeks
        # unavailable players carry zero EP
        zero = con.execute(
            "SELECT COUNT(*) FROM marts.mart_player_horizon WHERE status IN ('i','s','u') AND ep_horizon > 0"
        ).fetchone()[0]
        assert zero == 0
    finally:
        con.close()


def test_squad_and_transfer_rules(built, settings):
    con = connect(settings, read_only=True)
    try:
        assert con.execute("SELECT COUNT(*) FROM marts.mart_squad").fetchone()[0] == 15
        assert con.execute("SELECT COUNT(*) FROM marts.mart_squad WHERE is_captain").fetchone()[0] == 1
        bad = con.execute(
            """
            SELECT COUNT(*) FROM marts.mart_transfer_candidates AS t
            JOIN marts.mart_squad AS s ON s.player_id = t.in_player_id
            """
        ).fetchone()[0]
        assert bad == 0, "must never suggest a player already in the squad"
        unaffordable = con.execute(
            "SELECT COUNT(*) FROM marts.mart_transfer_candidates WHERE bank_after_m < -1e-9"
        ).fetchone()[0]
        assert unaffordable == 0
        max_rank = con.execute("SELECT MAX(candidate_rank) FROM marts.mart_transfer_candidates").fetchone()[0]
        assert max_rank <= 5
    finally:
        con.close()


def test_report_renders(built, settings):
    ctx = build_report(settings, write=True)
    assert ctx.gameweek == 5
    assert ctx.path.exists()
    md = ctx.markdown
    for heading in ("## 1. Where I stand", "## 3. Captaincy", "## 4. Transfers", "## 7. Fixture outlook"):
        assert heading in md
    assert "João Mama" in md
    assert "Free transfers | **3**" in md  # 1 transfer in GW3 -> 3 banked for GW5


@pytest.mark.parametrize("flag", ["--no-history"])
def test_build_without_player_history(tmp_path, project_root, monkeypatch, flag):
    """Optional raw tables must exist (empty) so every model still compiles."""
    from fpl_analysis.config import load_settings
    from fpl_analysis.store import build

    from .synthetic import build_snapshot, hermetic_paths

    build_snapshot(tmp_path / "raw", include_history=False)
    for key, value in hermetic_paths(tmp_path).items():
        monkeypatch.setenv(key, value)
    s = load_settings(project_root / "config" / "settings.toml")
    result = build(s)
    assert result["raw_counts"]["player_history"] == 0
    ctx = build_report(s, write=False)
    assert "## 5. Best players by position" in ctx.markdown
