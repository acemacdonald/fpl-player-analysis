-- materialized: table
-- Mart: one row per player per upcoming fixture in the horizon, with modelled expected points.
--
--   base_rate  = w_form * form + w_ppg * season_ppg + w_xgi * xgi_points_rate
--   ep         = base_rate * availability * fdr_multiplier * home_multiplier
--
-- Guards (each is a deliberate modelling choice, see docs/SCORING_MODEL.md):
--   * form of 0 with minutes > 0 means "hasn't played in 30 days", not "plays badly":
--     substitute season_ppg so a returning player isn't buried.
--   * xgi_points_rate is only trusted once a player has min_minutes_for_rates minutes;
--     before that its weight moves onto season_ppg.
WITH rates AS (
    SELECT
        f.*,
        CASE WHEN f.form_signal = 0 AND f.minutes > 0 THEN f.season_ppg ELSE f.form_signal END AS form_used,
        CASE WHEN f.rates_are_trusted THEN f.xgi_points_rate ELSE f.season_ppg END            AS xgi_used
    FROM {{ ref('mart_player_form') }} AS f
),
base AS (
    SELECT
        r.*,
        {{ var('weight_form') }}       * COALESCE(r.form_used, 0)
        + {{ var('weight_season_ppg') }} * COALESCE(r.season_ppg, 0)
        + {{ var('weight_xgi') }}      * COALESCE(r.xgi_used, 0)                             AS base_rate
    FROM rates AS r
)
SELECT
    b.player_id,
    b.web_name,
    b.team_id,
    b.team_short_name,
    b.position_id,
    b.position_code,
    b.price_m,
    b.selected_by_pct,
    o.gameweek,
    o.horizon_index,
    o.fixture_id,
    o.opponent_short_name,
    o.is_home,
    o.fdr,
    o.fixture_label,
    b.form_used,
    b.season_ppg,
    b.xgi_used,
    b.base_rate,
    b.availability,
    o.fdr_multiplier,
    o.home_multiplier,
    ROUND(b.base_rate * b.availability * o.fdr_multiplier * o.home_multiplier, 2) AS expected_points,
    b.snapshot_id
FROM base AS b
JOIN {{ ref('mart_team_fixture_outlook') }} AS o
    ON o.team_id = b.team_id
