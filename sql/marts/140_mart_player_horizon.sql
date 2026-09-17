-- materialized: table
-- Mart: one row per player rolled up across the horizon — the table the report ranks from.
WITH agg AS (
    SELECT
        player_id,
        SUM(expected_points)                                            AS ep_horizon,
        SUM(CASE WHEN horizon_index = 1 THEN expected_points ELSE 0 END) AS ep_next,
        COUNT(*)                                                        AS fixtures_in_horizon,
        SUM(CASE WHEN horizon_index = 1 THEN 1 ELSE 0 END)              AS fixtures_next_gw,
        AVG(fdr)                                                        AS avg_fdr,
        STRING_AGG(fixture_label, ', ' ORDER BY gameweek, fixture_id)   AS fixture_run,
        MIN(CASE WHEN horizon_index = 1 THEN fixture_label END)         AS next_fixture_label
    FROM {{ ref('mart_player_expected_points') }}
    GROUP BY player_id
)
SELECT
    f.player_id,
    f.web_name,
    f.team_id,
    f.team_short_name,
    f.position_id,
    f.position_code,
    f.price_m,
    f.selected_by_pct,
    f.status,
    f.news,
    f.chance_of_playing_next_round,
    f.is_injury_risk,
    f.total_points,
    f.minutes,
    f.starts,
    f.season_ppg,
    f.form_signal,
    f.xgi_per_90,
    f.xgc_per_90,
    f.xgi_points_rate,
    f.availability,
    f.api_ep_next,
    f.last5_points_avg,
    COALESCE(a.fixtures_in_horizon, 0)                  AS fixtures_in_horizon,
    COALESCE(a.fixtures_next_gw, 0)                     AS fixtures_next_gw,
    a.avg_fdr,
    a.fixture_run,
    a.next_fixture_label,
    ROUND(COALESCE(a.ep_next, 0), 2)                    AS ep_next,
    ROUND(COALESCE(a.ep_horizon, 0), 2)                 AS ep_horizon,
    ROUND(COALESCE(a.ep_horizon, 0) / NULLIF(f.price_m, 0), 2) AS ep_horizon_per_m,
    RANK() OVER (ORDER BY COALESCE(a.ep_horizon, 0) DESC)                                    AS rank_overall,
    RANK() OVER (PARTITION BY f.position_id ORDER BY COALESCE(a.ep_horizon, 0) DESC)         AS rank_in_position,
    f.snapshot_id
FROM {{ ref('mart_player_form') }} AS f
LEFT JOIN agg AS a
    ON a.player_id = f.player_id
