-- Staging: the tracked manager's gameweek-by-gameweek record this season.
SELECT
    event                                       AS gameweek,
    points                                      AS gw_points,
    total_points,
    rank                                        AS gw_rank,
    overall_rank,
    bank / 10.0                                 AS bank_m,
    value / 10.0                                AS squad_value_m,
    event_transfers,
    event_transfers_cost,
    points_on_bench,
    snapshot_id
FROM raw.entry_gameweeks
