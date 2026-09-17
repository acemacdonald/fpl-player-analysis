-- materialized: table
-- Mart: one row per team summarising the horizon — how many games, how easy, and the run itself.
--
-- fixture_score is the SUM of fdr multipliers, so a team with a double gameweek scores higher
-- than a team with one easy game: that is deliberate — more fixtures = more points on offer.
WITH per_team AS (
    SELECT
        team_id,
        team_short_name,
        COUNT(*)                                        AS fixtures_in_horizon,
        CAST(SUM(CASE WHEN is_home THEN 1 ELSE 0 END) AS INTEGER) AS home_fixtures,
        AVG(fdr)                                        AS avg_fdr,
        SUM(fdr_multiplier * home_multiplier)           AS fixture_score,
        STRING_AGG(fixture_label, ', ' ORDER BY gameweek, kickoff_ts) AS fixture_run,
        MIN(CASE WHEN horizon_index = 1 THEN fixture_label END)       AS next_fixture_label,
        CAST(SUM(CASE WHEN horizon_index = 1 THEN 1 ELSE 0 END) AS INTEGER) AS fixtures_next_gw
    FROM {{ ref('mart_team_fixture_outlook') }}
    GROUP BY team_id, team_short_name
)
SELECT
    t.team_id,
    t.team_short_name,
    t.team_name,
    t.league_position,
    COALESCE(p.fixtures_in_horizon, 0)                  AS fixtures_in_horizon,
    COALESCE(p.home_fixtures, 0)                        AS home_fixtures,
    p.avg_fdr,
    COALESCE(p.fixture_score, 0)                        AS fixture_score,
    p.fixture_run,
    p.next_fixture_label,
    COALESCE(p.fixtures_next_gw, 0)                     AS fixtures_next_gw,
    RANK() OVER (ORDER BY COALESCE(p.fixture_score, 0) DESC) AS fixture_rank
FROM {{ ref('stg_teams') }} AS t
LEFT JOIN per_team AS p
    ON p.team_id = t.team_id
