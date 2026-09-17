-- materialized: table
-- Mart: captaincy candidates for the next gameweek, ranked by ep_next.
-- in_squad lets the report show the best option you OWN next to the best option overall.
SELECT
    h.player_id,
    h.web_name,
    h.team_short_name,
    h.position_code,
    h.price_m,
    h.selected_by_pct,
    h.next_fixture_label,
    h.fixtures_next_gw,
    h.availability,
    h.is_injury_risk,
    h.form_signal,
    h.xgi_per_90,
    h.ep_next,
    h.ep_next * 2                                       AS ep_as_captain,
    h.api_ep_next,
    s.player_id IS NOT NULL                             AS in_squad,
    RANK() OVER (ORDER BY h.ep_next DESC)               AS captaincy_rank,
    h.snapshot_id
FROM {{ ref('mart_player_horizon') }} AS h
LEFT JOIN {{ ref('mart_squad') }} AS s
    ON s.player_id = h.player_id
WHERE h.fixtures_next_gw > 0
  AND h.availability > 0
