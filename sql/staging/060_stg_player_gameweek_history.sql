-- Staging: one row per player per fixture played THIS season.
-- Source: raw.player_history (element-summary.history, one call per player).
-- Optional table: it is empty when `fpl refresh --no-history` was used; marts must LEFT JOIN.
SELECT
    element                                     AS player_id,
    fixture                                     AS fixture_id,
    round                                       AS gameweek,
    opponent_team                               AS opponent_team_id,
    was_home                                    AS is_home,
    CAST(kickoff_time AS TIMESTAMPTZ)           AS kickoff_ts,
    total_points                                AS points,
    minutes,
    starts,
    goals_scored,
    assists,
    clean_sheets,
    goals_conceded,
    bonus,
    bps,
    saves,
    yellow_cards,
    red_cards,
    defensive_contribution,
    TRY_CAST(expected_goals AS DOUBLE)              AS xg,
    TRY_CAST(expected_assists AS DOUBLE)            AS xa,
    TRY_CAST(expected_goal_involvements AS DOUBLE)  AS xgi,
    TRY_CAST(expected_goals_conceded AS DOUBLE)     AS xgc,
    TRY_CAST(ict_index AS DOUBLE)                   AS ict_index,
    value                                       AS price_at_gw_tenths,
    selected                                    AS selected_by_count,
    transfers_in,
    transfers_out,
    snapshot_id
FROM raw.player_history
