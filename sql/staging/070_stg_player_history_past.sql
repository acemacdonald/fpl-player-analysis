-- Staging: one row per player per PREVIOUS season (career totals in the FPL era).
-- Useful for priors on players with few minutes this season.
SELECT
    element                                     AS player_id,
    season_name,
    start_cost / 10.0                           AS start_price_m,
    end_cost / 10.0                             AS end_price_m,
    total_points,
    minutes,
    goals_scored,
    assists,
    clean_sheets,
    bonus,
    TRY_CAST(expected_goal_involvements AS DOUBLE) AS xgi,
    TRY_CAST(expected_goals_conceded AS DOUBLE)    AS xgc,
    snapshot_id
FROM raw.player_history_past
