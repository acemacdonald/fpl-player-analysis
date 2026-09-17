-- materialized: table
-- Mart: for every player in the tracked squad, the best like-for-like replacements.
--
-- Constraints enforced here (the FPL rules that make a transfer legal):
--   * same position
--   * affordable: candidate price <= outgoing player's price + bank
--     (NOTE: uses current price as a proxy for selling price; the 50%-of-profit sell rule is
--      a Phase 2 refinement — see docs/PROJECT_PLAN.md)
--   * max three players per club after the swap
--   * candidate is not already in the squad and is not an injury risk
-- Ranked by ep_horizon_gain: how many extra modelled points over the horizon the swap buys.
WITH squad AS (
    SELECT * FROM {{ ref('mart_squad') }}
),
club_counts AS (
    SELECT team_id, COUNT(*) AS n_in_squad
    FROM squad
    GROUP BY team_id
),
pairs AS (
    SELECT
        s.player_id                                     AS out_player_id,
        s.web_name                                      AS out_web_name,
        s.team_short_name                               AS out_team,
        s.position_code,
        s.price_m                                       AS out_price_m,
        s.ep_horizon                                    AS out_ep_horizon,
        s.ep_next                                       AS out_ep_next,
        s.is_injury_risk                                AS out_is_injury_risk,
        s.bank_m,
        c.player_id                                     AS in_player_id,
        c.web_name                                      AS in_web_name,
        c.team_short_name                               AS in_team,
        c.price_m                                       AS in_price_m,
        c.selected_by_pct                               AS in_selected_by_pct,
        c.fixture_run                                   AS in_fixture_run,
        c.form_signal                                   AS in_form,
        c.xgi_per_90                                    AS in_xgi_per_90,
        c.ep_next                                       AS in_ep_next,
        c.ep_horizon                                    AS in_ep_horizon,
        c.ep_horizon - s.ep_horizon                     AS ep_horizon_gain,
        c.ep_next - s.ep_next                           AS ep_next_gain,
        s.price_m + s.bank_m - c.price_m                AS bank_after_m,
        s.snapshot_id
    FROM squad AS s
    JOIN {{ ref('mart_player_horizon') }} AS c
        ON c.position_id = s.position_id
       AND c.player_id <> s.player_id
    LEFT JOIN squad AS already
        ON already.player_id = c.player_id
    LEFT JOIN club_counts AS cc
        ON cc.team_id = c.team_id
    WHERE already.player_id IS NULL
      AND c.price_m <= s.price_m + s.bank_m
      AND NOT c.is_injury_risk
      AND c.availability > 0
      AND c.status = 'a'
      AND (COALESCE(cc.n_in_squad, 0) - CASE WHEN c.team_id = s.team_id THEN 1 ELSE 0 END) < 3
)
SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY out_player_id ORDER BY ep_horizon_gain DESC, in_price_m) AS candidate_rank
FROM pairs
QUALIFY candidate_rank <= 5
