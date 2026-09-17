-- materialized: table
-- Mart: one row per team per UPCOMING fixture inside the analysis horizon.
--
-- Anchored on next_gameweek from the snapshot. A team with two rows in a GW has a double
-- gameweek; a team with none has a blank. fdr_multiplier is the scaling the expected-points
-- model applies for that fixture (config: analysis.fdr_multiplier, see docs/SCORING_MODEL.md).
WITH ctx AS (
    SELECT
        next_gameweek                                           AS first_gw,
        next_gameweek + {{ var('horizon_gameweeks') }} - 1      AS last_gw
    FROM {{ ref('stg_snapshot') }}
)
SELECT
    tf.team_id,
    t.team_short_name,
    tf.gameweek,
    tf.gameweek - ctx.first_gw + 1                              AS horizon_index,     -- 1 = next GW
    tf.fixture_id,
    tf.kickoff_ts,
    tf.opponent_team_id,
    o.team_short_name                                           AS opponent_short_name,
    tf.is_home,
    tf.fdr,
    CASE tf.fdr
        {% for fdr, mult in var('fdr_multiplier').items() %}
        WHEN {{ fdr }} THEN {{ mult }}
        {% endfor %}
        ELSE 1.0
    END                                                         AS fdr_multiplier,
    CASE WHEN tf.is_home THEN 1.04 ELSE 0.96 END                AS home_multiplier,   -- modest, documented home edge
    o.team_short_name || CASE WHEN tf.is_home THEN ' (H)' ELSE ' (A)' END AS fixture_label
FROM {{ ref('stg_team_fixtures') }} AS tf
CROSS JOIN ctx
JOIN {{ ref('stg_teams') }} AS t
    ON t.team_id = tf.team_id
JOIN {{ ref('stg_teams') }} AS o
    ON o.team_id = tf.opponent_team_id
WHERE tf.gameweek BETWEEN ctx.first_gw AND ctx.last_gw
  AND NOT tf.is_finished
