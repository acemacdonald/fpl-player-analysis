-- Staging: the tracked manager's 15 picks for the CURRENT gameweek.
-- position 1-11 = starting XI in formation order, 12-15 = bench order. multiplier 2 = captain (3 = triple captain), 0 = benched.
SELECT
    m.event                                     AS gameweek,
    p.element                                   AS player_id,
    p.position                                  AS squad_position,
    p.position <= 11                            AS is_starter,
    p.multiplier,
    p.is_captain,
    p.is_vice_captain,
    m.active_chip,
    m.bank / 10.0                               AS bank_m,
    m.value / 10.0                              AS squad_value_m,
    m.event_transfers,
    m.event_transfers_cost,
    m.source                                    AS picks_source,   -- 'api' or 'manual override'
    p.snapshot_id
FROM raw.entry_picks AS p
CROSS JOIN raw.entry_picks_meta AS m
