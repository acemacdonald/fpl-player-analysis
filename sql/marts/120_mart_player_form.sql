-- materialized: table
-- Mart: one row per player with every signal the expected-points model needs, side by side.
--
-- Three signals are prepared here (blended in mart_player_expected_points):
--   form_signal      API "form" = avg points per match over the last 30 days (fast, noisy)
--   season_ppg       points per game this season (slow, stable)
--   xgi_points_rate  points per 90 implied by UNDERLYING numbers (xG, xA, xGC, saves, bonus)
-- plus availability: injury news and how often the player actually starts.
--
-- Two small-sample defences live here (docs/SCORING_MODEL.md §2–3):
--   * team matches played is COUNTED FROM FINISHED FIXTURES. The API's teams[].played is always
--     0, which silently made start_share = 1 for every player (every sub looked like a starter).
--   * season_ppg_shrunk pulls a player's points-per-game towards the position's typical level in
--     proportion to how little he has played: (ppg × games + prior × k) / (games + k), with
--     games = minutes / 90 and k = shrinkage_games. One 8-point game is not an 8-PPG player.
--
-- Scoring constants are the 2025/26 FPL rules (docs/SCORING_MODEL.md).
WITH team_games AS (
    SELECT
        team_id,
        COUNT(*)                                        AS matches_played
    FROM {{ ref('stg_team_fixtures') }}
    WHERE is_finished
    GROUP BY team_id
),
position_prior AS (
    -- Shrinkage target: median PPG of players in the position with enough minutes to be believed.
    -- Before anyone qualifies (opening weeks) fall back to 2.0, the appearance points of a full game.
    SELECT
        position_id,
        MEDIAN(points_per_game)                         AS prior_ppg
    FROM {{ ref('stg_players') }}
    WHERE minutes >= {{ var('min_minutes_for_rates') }}
      AND points_per_game IS NOT NULL
    GROUP BY position_id
),
last5 AS (
    -- Rolling recent output from per-fixture history (empty if refresh ran with --no-history).
    SELECT
        player_id,
        AVG(points)                                     AS last5_points_avg,
        AVG(minutes)                                    AS last5_minutes_avg,
        SUM(CASE WHEN minutes >= 60 THEN 1 ELSE 0 END)  AS last5_full_games
    FROM (
        SELECT
            player_id,
            points,
            minutes,
            ROW_NUMBER() OVER (PARTITION BY player_id ORDER BY kickoff_ts DESC) AS rn
        FROM {{ ref('stg_player_gameweek_history') }}
    )
    WHERE rn <= 5
    GROUP BY player_id
),
scoring AS (
    SELECT
        p.player_id,
        p.web_name,
        p.team_id,
        t.team_short_name,
        p.position_id,
        pos.position_code,
        p.price_m,
        p.selected_by_pct,
        p.status,
        p.news,
        p.chance_of_playing_next_round,
        p.total_points,
        p.minutes,
        p.starts,
        COALESCE(g.matches_played, 0)                   AS team_matches_played,
        p.points_per_game                               AS season_ppg,
        p.minutes / 90.0                                AS games_equiv,
        COALESCE(pr.prior_ppg, 2.0)                     AS position_prior_ppg,
        (
            COALESCE(p.points_per_game, 0) * (p.minutes / 90.0)
            + COALESCE(pr.prior_ppg, 2.0) * {{ var('shrinkage_games') }}
        ) / ((p.minutes / 90.0) + {{ var('shrinkage_games') }})  AS season_ppg_shrunk,
        p.form                                          AS form_signal,
        p.api_ep_next,
        p.xg_per_90,
        p.xa_per_90,
        p.xgi_per_90,
        p.xgc_per_90,
        p.saves_per_90,
        p.bonus,
        p.penalties_order,
        l.last5_points_avg,
        l.last5_minutes_avg,
        l.last5_full_games,

        -- Points a goal / clean sheet is worth for this position
        CASE pos.position_code
            WHEN 'GKP' THEN 10 WHEN 'DEF' THEN 6 WHEN 'MID' THEN 5 ELSE 4
        END                                             AS goal_points,
        CASE pos.position_code
            WHEN 'GKP' THEN 4 WHEN 'DEF' THEN 4 WHEN 'MID' THEN 1 ELSE 0
        END                                             AS clean_sheet_points,

        -- Availability: news-driven chance (NULL = fit) x share of team games started
        COALESCE(p.chance_of_playing_next_round, 100) / 100.0 AS chance_factor,
        CASE
            WHEN COALESCE(g.matches_played, 0) = 0 THEN 1.0
            ELSE LEAST(1.0, p.starts * 1.0 / g.matches_played)
        END                                             AS start_share,
        CASE WHEN p.status IN ('u', 'n') THEN 0.0 ELSE 1.0 END AS status_factor,
        p.bonus * 1.0 / NULLIF(p.starts, 0)             AS bonus_per_start,
        p.snapshot_id
    FROM {{ ref('stg_players') }} AS p
    JOIN {{ ref('stg_teams') }} AS t
        ON t.team_id = p.team_id
    JOIN {{ ref('stg_positions') }} AS pos
        ON pos.position_id = p.position_id
    LEFT JOIN team_games AS g
        ON g.team_id = p.team_id
    LEFT JOIN position_prior AS pr
        ON pr.position_id = p.position_id
    LEFT JOIN last5 AS l
        ON l.player_id = p.player_id
)
SELECT
    s.*,
    -- Expected points per 90 from underlying numbers: appearance + attack + defence + bonus.
    -- P(clean sheet) ~ Poisson(0 | xGC per 90) = exp(-xGC per 90).
    2.0
    + COALESCE(s.xg_per_90, 0) * s.goal_points
    + COALESCE(s.xa_per_90, 0) * 3
    + EXP(-COALESCE(s.xgc_per_90, 0)) * s.clean_sheet_points
    + CASE WHEN s.position_code = 'GKP' THEN COALESCE(s.saves_per_90, 0) / 3.0 ELSE 0 END
    - CASE WHEN s.position_code IN ('GKP', 'DEF') THEN COALESCE(s.xgc_per_90, 0) / 2.0 ELSE 0 END
    + LEAST(COALESCE(s.bonus_per_start, 0), 3.0)         AS xgi_points_rate,
    s.minutes >= {{ var('min_minutes_for_rates') }}      AS rates_are_trusted,
    s.chance_factor * s.start_share * s.status_factor    AS availability,
    s.chance_of_playing_next_round IS NOT NULL
        AND s.chance_of_playing_next_round < {{ var('min_chance_of_playing') }} AS is_injury_risk
FROM scoring AS s
