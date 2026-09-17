"""The pre-deadline squad override: names resolve, ambiguity is rejected, the report reflects it."""

from __future__ import annotations

import pytest

from fpl_analysis.config import load_settings
from fpl_analysis.report import build_report
from fpl_analysis.store import build, connect

from .synthetic import build_snapshot


def _write_override(root, names_by_pos, bank_m=1.2, extra=""):
    (root / "config").mkdir(exist_ok=True)
    gk, defs, mids, fwds, bench = names_by_pos
    starters = [gk, *defs, *mids, *fwds]

    def q(v):
        return f'"{v}"' if isinstance(v, str) else "{ " + ", ".join(f'{k} = "{x}"' for k, x in v.items()) + " }"

    (root / "config" / "squad_override.toml").write_text(
        f"""
enabled = true
gameweek = 5
bank_m = {bank_m}
free_transfers = 2
captain = {q(fwds[0])}
vice_captain = {q(mids[0])}
starters = [{", ".join(q(s) for s in starters)}]
bench = [{", ".join(q(b) for b in bench)}]
{extra}
"""
    )


@pytest.fixture()
def override_project(tmp_path, project_root, monkeypatch):
    """A throwaway project root with the real sql/ + settings but its own config/squad_override.toml."""
    build_snapshot(tmp_path / "raw")
    monkeypatch.setenv("FPL_PATHS_RAW_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("FPL_PATHS_DUCKDB_PATH", str(tmp_path / "fpl.duckdb"))
    monkeypatch.setenv("FPL_PATHS_REPORTS_DIR", str(tmp_path / "reports"))
    # load_settings resolves project_root from cwd -> point cwd at a copy that has pyproject + sql + config
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "settings.toml").write_text((project_root / "config" / "settings.toml").read_text())
    monkeypatch.setenv("FPL_PATHS_SQL_DIR", str(project_root / "sql"))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_override_replaces_api_picks(override_project):
    # synthetic web_names look like ARS-GK1, ARS-DEF2 ...; pick a squad that differs from the API picks
    _write_override(
        override_project,
        (
            "AVL-GK7",
            ["AVL-DEF8", "AVL-DEF9", "BOU-DEF14"],
            ["BOU-MID16", "BOU-MID17", "BRE-MID22", "BRE-MID23"],
            ["BOU-FWD18", "BRE-FWD24", "BHA-FWD30"],
            ["BOU-GK13", "BRE-DEF20", "BRE-DEF21", "BHA-MID28"],
        ),
    )
    s = load_settings()
    result = build(s)
    assert result["squad_override_applied"] is True
    con = connect(s, read_only=True)
    try:
        rows = con.execute(
            "SELECT web_name, squad_position, is_captain, picks_source, bank_m, picks_gameweek "
            "FROM marts.mart_squad ORDER BY squad_position"
        ).fetchall()
    finally:
        con.close()
    assert len(rows) == 15
    assert rows[0][0] == "AVL-GK7" and rows[0][1] == 1
    assert [r[0] for r in rows if r[2]] == ["BOU-FWD18"]
    assert rows[0][3] == "manual override" and rows[0][4] == pytest.approx(1.2) and rows[0][5] == 5

    ctx = build_report(s, write=False)
    assert "squad from `config/squad_override.toml`" in ctx.markdown
    # transfers-made accounting: the override squad shares no player with the API picks except
    # 6 shared -> 9 moves against 2 FTs = 7 hits (-28); squad value falls back to current prices
    assert "| Transfers made this GW | **9**" in ctx.markdown
    assert "2 going into GW5 (squad override file) → **0 left**, **hit taken −28**" in ctx.markdown
    assert "(current prices)" in ctx.markdown
    assert "You have no free transfers left this week (already -28 in hits)" in ctx.markdown


def test_unknown_name_is_rejected(override_project):
    _write_override(
        override_project,
        (
            "NOBODY-GK",
            ["AVL-DEF8", "AVL-DEF9", "BOU-DEF14"],
            ["BOU-MID16", "BOU-MID17", "BRE-MID22", "BRE-MID23"],
            ["BOU-FWD18", "BRE-FWD24", "BHA-FWD30"],
            ["BOU-GK13", "BRE-DEF20", "BRE-DEF21", "BHA-MID28"],
        ),
    )
    s = load_settings()
    with pytest.raises(ValueError, match="no player named 'NOBODY-GK'"):
        build(s)


def test_override_disabled_is_ignored(override_project):
    (override_project / "config" / "squad_override.toml").write_text("enabled = false\n")
    s = load_settings()
    result = build(s)
    assert result["squad_override_applied"] is False


def test_ambiguous_name_needs_qualification(override_project):
    """Two players with the same web_name (a real case: Palmer CHE and Palmer the keeper)."""
    from fpl_analysis.squad_override import SquadOverride, apply_override

    s = load_settings()
    build(s)
    con = connect(s)
    try:
        # give a second player the same web_name as AVL-GK7
        con.execute("UPDATE raw.players SET web_name = 'AVL-GK7' WHERE web_name = 'BOU-GK13'")
        spec = lambda n: {"name": n}  # noqa: E731
        ov = SquadOverride(
            gameweek=5,
            starters=[spec("AVL-GK7"), *map(spec, ["AVL-DEF8", "AVL-DEF9", "BOU-DEF14"]),
                      *map(spec, ["BOU-MID16", "BOU-MID17", "BRE-MID22", "BRE-MID23"]),
                      *map(spec, ["BOU-FWD18", "BRE-FWD24", "BHA-FWD30"])],
            bench=list(map(spec, ["BRE-GK19", "BRE-DEF20", "BRE-DEF21", "BHA-MID28"])),
            captain=spec("BOU-FWD18"), vice_captain=spec("BOU-MID16"),
            bank_m=0.5, free_transfers=None, active_chip=None,
        )
        with pytest.raises(ValueError, match="ambiguous"):
            apply_override(con, ov, "snap", 5)
        # qualifying by team resolves it
        ov_ok = SquadOverride(**{**ov.__dict__, "starters": [{"name": "AVL-GK7", "team": "AVL"}, *ov.starters[1:]]})
        assert apply_override(con, ov_ok, "snap", 5) == 15
    finally:
        con.close()
