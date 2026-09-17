-- Staging: one row per fixture (380 per season). event is NULL for postponed/unscheduled games.
--
-- FDR semantics (easy to get backwards):
--   team_h_difficulty = how hard the fixture is FOR THE HOME TEAM (1 easy .. 5 hard)
--   team_a_difficulty = how hard the fixture is FOR THE AWAY TEAM
SELECT
    id                                          AS fixture_id,
    code                                        AS fixture_code,
    event                                       AS gameweek,
    CAST(kickoff_time AS TIMESTAMPTZ)           AS kickoff_ts,
    team_h                                      AS home_team_id,
    team_a                                      AS away_team_id,
    team_h_score                                AS home_score,
    team_a_score                                AS away_score,
    team_h_difficulty                           AS home_team_fdr,
    team_a_difficulty                           AS away_team_fdr,
    started                                     AS has_started,
    finished                                    AS is_finished,
    finished_provisional                        AS is_finished_provisional,
    minutes                                     AS minutes_played,
    snapshot_id
FROM raw.fixtures
