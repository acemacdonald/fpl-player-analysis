-- Staging: the single-row context of the loaded snapshot (which GW is "next", when it was pulled).
-- Every mart anchors "upcoming" on next_gameweek from here rather than on wall-clock time,
-- so a build is reproducible from any snapshot on disk.
SELECT
    snapshot_id,
    snapshot_ts,
    current_gameweek,
    next_gameweek,
    has_player_history
FROM raw.snapshot
