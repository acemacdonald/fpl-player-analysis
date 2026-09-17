-- Staging: fixtures unpivoted to ONE ROW PER TEAM PER FIXTURE.
-- Every downstream "who does X play next" question is easier from this shape than from the
-- home/away shape, and it makes double gameweeks (two rows for one team in one GW) and blank
-- gameweeks (zero rows) fall out naturally.
WITH home AS (
    SELECT
        fixture_id,
        gameweek,
        kickoff_ts,
        home_team_id                AS team_id,
        away_team_id                AS opponent_team_id,
        TRUE                        AS is_home,
        home_team_fdr               AS fdr,
        home_score                  AS goals_for,
        away_score                  AS goals_against,
        is_finished,
        snapshot_id
    FROM {{ ref('stg_fixtures') }}
),
away AS (
    SELECT
        fixture_id,
        gameweek,
        kickoff_ts,
        away_team_id                AS team_id,
        home_team_id                AS opponent_team_id,
        FALSE                       AS is_home,
        away_team_fdr               AS fdr,
        away_score                  AS goals_for,
        home_score                  AS goals_against,
        is_finished,
        snapshot_id
    FROM {{ ref('stg_fixtures') }}
)
SELECT * FROM home
UNION ALL
SELECT * FROM away
