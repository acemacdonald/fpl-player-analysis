-- materialized: table
-- Mart: the tracked manager's current 15, enriched with the model's view of each player.
-- Empty if the snapshot was taken without entry data (fpl refresh --no-entry).
SELECT
    pk.gameweek                                         AS picks_gameweek,
    pk.squad_position,
    pk.is_starter,
    pk.is_captain,
    pk.is_vice_captain,
    pk.multiplier,
    pk.active_chip,
    pk.bank_m,
    pk.squad_value_m,
    pk.picks_source,
    h.player_id,
    h.web_name,
    h.team_id,
    h.team_short_name,
    h.position_id,
    h.position_code,
    h.price_m,
    h.selected_by_pct,
    h.status,
    h.news,
    h.chance_of_playing_next_round,
    h.is_injury_risk,
    h.total_points,
    h.minutes,
    h.season_ppg,
    h.form_signal,
    h.xgi_per_90,
    h.availability,
    h.fixtures_next_gw,
    h.next_fixture_label,
    h.fixture_run,
    h.ep_next,
    h.ep_gw2,
    h.ep_horizon,
    h.rank_in_position,
    h.snapshot_id
FROM {{ ref('stg_entry_picks') }} AS pk
JOIN {{ ref('mart_player_horizon') }} AS h
    ON h.player_id = pk.player_id
