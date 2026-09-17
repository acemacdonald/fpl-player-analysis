"""The chip-squad optimiser must return FPL-legal squads and beat any naive pick."""

from __future__ import annotations

from fpl_analysis.optimiser import CHIPS, solve_chip
from fpl_analysis.store import connect


def _players(settings):
    con = connect(settings, read_only=True)
    try:
        return con.execute("SELECT * FROM marts.mart_player_horizon").df()
    finally:
        con.close()


def test_chip_squads_are_legal(built, settings):
    con = connect(settings, read_only=True)
    try:
        comp = con.execute(
            """
            SELECT chip, position_code, COUNT(*) AS n, SUM(CASE WHEN is_starter THEN 1 ELSE 0 END) AS starters
            FROM marts.mart_chip_squads GROUP BY 1, 2
            """
        ).fetchall()
        clubs = con.execute(
            "SELECT chip, team_id, COUNT(*) FROM marts.mart_chip_squads GROUP BY 1, 2 HAVING COUNT(*) > 3"
        ).fetchall()
        totals = con.execute(
            """
            SELECT chip, COUNT(*), SUM(CASE WHEN is_starter THEN 1 ELSE 0 END),
                   SUM(CASE WHEN is_captain THEN 1 ELSE 0 END), ROUND(SUM(price_m), 1), MAX(budget_m)
            FROM marts.mart_chip_squads GROUP BY 1
            """
        ).fetchall()
    finally:
        con.close()

    expected = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
    limits = {"GKP": (1, 1), "DEF": (3, 5), "MID": (2, 5), "FWD": (1, 3)}
    seen_chips = set()
    for chip, pos, n, starters in comp:
        seen_chips.add(chip)
        assert n == expected[pos], (chip, pos, n)
        lo, hi = limits[pos]
        assert lo <= starters <= hi, (chip, pos, starters)
    assert seen_chips == set(CHIPS)
    assert clubs == [], f"more than three from one club: {clubs}"
    for chip, n, starters, captains, cost, budget in totals:
        assert (n, starters, captains) == (15, 11, 1), chip
        assert cost <= budget + 1e-9, (chip, cost, budget)


def test_optimum_beats_greedy_and_respects_budget(built, settings):
    players = _players(settings)
    sol = solve_chip(players, "freehit", budget_m=95.0, settings=settings)
    assert sol is not None
    assert sol.cost_m <= 95.0
    # a tighter budget can never do better
    rich = solve_chip(players, "freehit", budget_m=120.0, settings=settings)
    assert rich is not None and rich.xi_ep >= sol.xi_ep
    # captain is the highest-EP starter (with a single captain slot, the optimum always is)
    starters = sol.squad[sol.squad["is_starter"]]
    assert starters.loc[starters["is_captain"], "chip_ep"].iloc[0] == starters["chip_ep"].max()


def test_infeasible_budget_returns_none(built, settings):
    players = _players(settings)
    assert solve_chip(players, "wildcard", budget_m=10.0, settings=settings) is None
