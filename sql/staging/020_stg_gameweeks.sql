-- Staging: one row per gameweek ("event" in API terms).
-- Source: raw.events. deadline_time arrives as an ISO-8601 string; cast once here.
SELECT
    id                                          AS gameweek,
    name                                        AS gameweek_name,
    CAST(deadline_time AS TIMESTAMPTZ)          AS deadline_ts,
    finished                                    AS is_finished,
    data_checked                                AS is_data_checked,
    is_previous,
    is_current,
    is_next,
    average_entry_score,
    highest_score,
    most_selected                               AS most_selected_player_id,
    most_transferred_in                         AS most_transferred_in_player_id,
    most_captained                              AS most_captained_player_id,
    top_element                                 AS top_player_id,
    snapshot_id
FROM raw.events
