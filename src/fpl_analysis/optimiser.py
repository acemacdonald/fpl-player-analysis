"""Chip squads: the best legal 15 for a Free Hit (next GW) and a Wildcard (the horizon).

Why this is Python and not SQL
------------------------------
Every other analytic in this project is a SQL model. Picking the best squad is different in
kind: it is a constrained optimisation (a knapsack with side constraints), and SQL has no
solver. So this module is the one deliberate exception to "logic lives in sql/". It keeps the
project's contract in every other respect: its only input is ``marts.mart_player_horizon``,
its only output is a mart table (``marts.mart_chip_squads``), and the report reads that table
exactly as it reads every other mart.

The model
---------
Binary variables per eligible player *i*:

    x_i  in the 15-man squad
    y_i  in the starting XI              (y_i <= x_i)
    c_i  wears the armband               (c_i <= y_i, exactly one)

Maximise   sum(ep_i * y_i)  +  sum(ep_i * c_i)  +  w_bench * sum(ep_i * (x_i - y_i))

i.e. starting XI points, plus the captain's points again, plus a small weight on the bench so
the four non-starters are the best *cheap* cover rather than random £4.0m filler. FPL's rules
are the constraints: 2 GKP / 5 DEF / 5 MID / 3 FWD in the squad; 11 starters with 1 GKP,
3-5 DEF, 2-5 MID, 1-3 FWD; at most 3 per club; total price within budget.

``ep_i`` is ``ep_next`` for the Free Hit and ``ep_horizon`` for the Wildcard — the same
modelled expected points the rest of the report ranks by (docs/SCORING_MODEL.md).

Solved with SciPy's bundled HiGHS MILP solver: ~650 players x 3 variables solves in well under
a second and the answer is exact, not heuristic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import csr_matrix, lil_matrix

from .config import Settings

log = logging.getLogger(__name__)

SQUAD_BY_POS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
START_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
START_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
POS_ORDER = {"GKP": 1, "DEF": 2, "MID": 3, "FWD": 4}

CHIPS = {
    # chip -> (EP column to maximise, human label)
    "freehit": ("ep_next", "Free Hit"),
    "wildcard": ("ep_horizon", "Wildcard"),
}


@dataclass(frozen=True)
class ChipSolution:
    chip: str
    objective_ep: float          # XI EP + captain EP (+ bench weight) as optimised
    xi_ep: float                 # XI EP with captain doubled
    cost_m: float
    budget_m: float
    squad: pd.DataFrame          # 15 rows: slot 1-15, is_starter, is_captain, ...


def _eligible(players: pd.DataFrame, ep_col: str, settings: Settings) -> pd.DataFrame:
    df = players.copy()
    df = df[df["availability"] > 0]
    df = df[df["status"].isin(["a", "d"])]
    if settings.chips_exclude_injury_risk:
        df = df[~df["is_injury_risk"].fillna(False).astype(bool)]
    df = df[df[ep_col].fillna(0) > 0]
    return df.reset_index(drop=True)


def solve_chip(players: pd.DataFrame, chip: str, budget_m: float, settings: Settings) -> ChipSolution | None:
    """Return the optimal 15 for ``chip`` under ``budget_m``, or None if infeasible."""
    ep_col, _ = CHIPS[chip]
    df = _eligible(players, ep_col, settings)
    n = len(df)
    if n < 15:
        log.warning("chip %s: only %d eligible players — skipping", chip, n)
        return None

    ep = df[ep_col].to_numpy(dtype=float)
    price = df["price_m"].to_numpy(dtype=float)
    pos = df["position_code"].to_numpy()
    club = df["team_id"].to_numpy()
    w_bench = settings.chips_bench_weight_freehit if chip == "freehit" else settings.chips_bench_weight_wildcard

    # variable layout: [x_0..x_n-1, y_0..y_n-1, c_0..c_n-1]
    X, Y, C = 0, n, 2 * n
    nv = 3 * n
    # milp minimises -> negate. objective: ep*y + ep*c + w_bench*ep*(x - y)
    cost = np.zeros(nv)
    cost[X:Y] = -w_bench * ep
    cost[Y:C] = -(1 - w_bench) * ep
    cost[C:] = -ep

    rows: list[tuple[np.ndarray, float, float]] = []  # (coefficient row, lo, hi)

    def add(coef: np.ndarray, lo: float, hi: float) -> None:
        rows.append((coef, lo, hi))

    # squad composition
    for p, k in SQUAD_BY_POS.items():
        r = np.zeros(nv)
        r[X:Y] = pos == p
        add(r, k, k)
    # starters: 11 total, per-position min/max
    r = np.zeros(nv)
    r[Y:C] = 1
    add(r, 11, 11)
    for p in SQUAD_BY_POS:
        r = np.zeros(nv)
        r[Y:C] = pos == p
        add(r, START_MIN[p], START_MAX[p])
    # exactly one captain
    r = np.zeros(nv)
    r[C:] = 1
    add(r, 1, 1)
    # club limit
    for t in np.unique(club):
        r = np.zeros(nv)
        r[X:Y] = club == t
        add(r, 0, MAX_PER_CLUB)
    # budget
    r = np.zeros(nv)
    r[X:Y] = price
    add(r, 0, budget_m + 1e-9)

    # linking constraints y_i <= x_i and c_i <= y_i  (sparse: 2n rows)
    link = lil_matrix((2 * n, nv))
    for i in range(n):
        link[i, Y + i] = 1
        link[i, X + i] = -1
        link[n + i, C + i] = 1
        link[n + i, Y + i] = -1

    A_dense = np.vstack([r for r, _, _ in rows])
    lo = np.array([lo for _, lo, _ in rows])
    hi = np.array([hi for _, _, hi in rows])
    constraints = [
        LinearConstraint(csr_matrix(A_dense), lo, hi),
        LinearConstraint(csr_matrix(link), -np.inf, 0),
    ]
    res = milp(
        c=cost,
        constraints=constraints,
        integrality=np.ones(nv),
        bounds=Bounds(0, 1),
        options={"time_limit": 30},
    )
    if res.x is None or res.status not in (0, 1):
        log.warning("chip %s: solver status %s (%s)", chip, res.status, res.message)
        return None

    x = res.x[X:Y] > 0.5
    y = res.x[Y:C] > 0.5
    c = res.x[C:] > 0.5
    sel = df[x].copy()
    sel["is_starter"] = y[x]
    sel["is_captain"] = c[x]
    sel["chip_ep"] = sel[ep_col]
    # order: starters by position then EP; bench: GKP first, then by EP
    sel["_pos_order"] = sel["position_code"].map(POS_ORDER)
    starters = sel[sel["is_starter"]].sort_values(["_pos_order", "chip_ep"], ascending=[True, False])
    bench = sel[~sel["is_starter"]].sort_values(["_pos_order", "chip_ep"], ascending=[True, False])
    bench_gk = bench[bench["position_code"] == "GKP"]
    bench_out = bench[bench["position_code"] != "GKP"].sort_values("chip_ep", ascending=False)
    ordered = pd.concat([starters, bench_gk, bench_out]).drop(columns="_pos_order")
    ordered["slot"] = range(1, len(ordered) + 1)

    xi_ep = float(starters["chip_ep"].sum() + starters.loc[starters["is_captain"], "chip_ep"].sum())
    return ChipSolution(
        chip=chip,
        objective_ep=float(-res.fun),
        xi_ep=round(xi_ep, 2),
        cost_m=round(float(sel["price_m"].sum()), 1),
        budget_m=round(float(budget_m), 1),
        squad=ordered.reset_index(drop=True),
    )


def _budget(con: duckdb.DuckDBPyConnection, settings: Settings) -> tuple[float, str]:
    """Squad value + bank from the tracked squad; 100.0 if there is no squad. Config can override."""
    if settings.chips_budget_override_m > 0:
        return settings.chips_budget_override_m, "config override"
    row = con.execute(
        """
        SELECT MAX(squad_value_m), MAX(bank_m), SUM(price_m), COUNT(*)
        FROM marts.mart_squad
        """
    ).fetchone()
    if not row or not row[3]:
        return 100.0, "no squad in snapshot — default £100.0m"
    squad_value, bank, price_sum, _ = row
    bank = float(bank or 0.0)
    if squad_value is not None and not pd.isna(squad_value):
        return round(float(squad_value) + bank, 1), "squad value + bank (API)"
    return round(float(price_sum) + bank, 1), "sum of current prices + bank (override squad)"


def build_chip_squads(con: duckdb.DuckDBPyConnection, settings: Settings) -> dict[str, ChipSolution | None]:
    """Solve both chips and write ``marts.mart_chip_squads``. Returns the solutions."""
    players = con.execute(
        """
        SELECT player_id, web_name, team_id, team_short_name, position_code, price_m, selected_by_pct,
               status, is_injury_risk, availability, form_signal, xgi_per_90, next_fixture_label,
               fixture_run, fixtures_next_gw, ep_next, ep_horizon, snapshot_id
        FROM marts.mart_player_horizon
        """
    ).df()
    owned = set(con.execute("SELECT player_id FROM marts.mart_squad").df()["player_id"].tolist())
    budget_m, budget_source = _budget(con, settings)

    solutions: dict[str, ChipSolution | None] = {}
    frames: list[pd.DataFrame] = []
    for chip in CHIPS:
        sol = solve_chip(players, chip, budget_m, settings)
        solutions[chip] = sol
        if sol is None:
            continue
        f = sol.squad.copy()
        f.insert(0, "chip", chip)
        f["in_current_squad"] = f["player_id"].isin(owned)
        f["xi_ep"] = sol.xi_ep
        f["cost_m"] = sol.cost_m
        f["budget_m"] = sol.budget_m
        f["budget_source"] = budget_source
        frames.append(f)
        log.info("chip %s: XI EP %.2f (captain doubled), cost £%.1fm of £%.1fm", chip, sol.xi_ep, sol.cost_m, budget_m)

    con.execute("DROP TABLE IF EXISTS marts.mart_chip_squads")
    if frames:
        out = pd.concat(frames, ignore_index=True)
        con.register("_chip_squads", out)
        con.execute("CREATE TABLE marts.mart_chip_squads AS SELECT * FROM _chip_squads")
        con.unregister("_chip_squads")
    else:
        con.execute(
            """
            CREATE TABLE marts.mart_chip_squads (
                chip VARCHAR, player_id INTEGER, web_name VARCHAR, team_id INTEGER, team_short_name VARCHAR,
                position_code VARCHAR, price_m DOUBLE, selected_by_pct DOUBLE, status VARCHAR,
                is_injury_risk BOOLEAN, availability DOUBLE, form_signal DOUBLE, xgi_per_90 DOUBLE,
                next_fixture_label VARCHAR, fixture_run VARCHAR, fixtures_next_gw INTEGER,
                ep_next DOUBLE, ep_horizon DOUBLE, snapshot_id VARCHAR, is_starter BOOLEAN,
                is_captain BOOLEAN, chip_ep DOUBLE, slot INTEGER, in_current_squad BOOLEAN,
                xi_ep DOUBLE, cost_m DOUBLE, budget_m DOUBLE, budget_source VARCHAR
            )
            """
        )
    return solutions
