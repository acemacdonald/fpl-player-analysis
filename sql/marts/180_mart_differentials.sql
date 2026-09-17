-- materialized: table
-- Mart: low-ownership players the model rates — the "nobody has him" upside plays.
SELECT
    h.*,
    RANK() OVER (ORDER BY h.ep_horizon DESC)            AS differential_rank
FROM {{ ref('mart_player_horizon') }} AS h
WHERE h.selected_by_pct < {{ var('differential_max_ownership') }}
  AND h.availability > 0
  AND NOT h.is_injury_risk
  AND h.fixtures_in_horizon > 0
QUALIFY differential_rank <= 25
