-- Staging: one row per Premier League club.
-- Source: raw.teams (bootstrap-static.teams). No business logic here — rename, type, done.
SELECT
    id                          AS team_id,
    code                        AS team_code,
    name                        AS team_name,
    short_name                  AS team_short_name,
    strength                    AS strength_overall,
    strength_overall_home,
    strength_overall_away,
    strength_attack_home,
    strength_attack_away,
    strength_defence_home,
    strength_defence_away,
    played                      AS matches_played,
    win                         AS wins,
    draw                        AS draws,
    loss                        AS losses,
    points                      AS league_points,
    position                    AS league_position,
    snapshot_id
FROM raw.teams
