-- Staging: the four FPL positions with squad-selection rules.
-- Source: raw.element_types.
SELECT
    id                          AS position_id,
    singular_name_short         AS position_code,      -- GKP / DEF / MID / FWD
    singular_name               AS position_name,
    squad_select                AS squad_size,         -- how many of this position in a 15-man squad
    squad_min_play              AS min_starters,
    squad_max_play              AS max_starters,
    snapshot_id
FROM raw.element_types
