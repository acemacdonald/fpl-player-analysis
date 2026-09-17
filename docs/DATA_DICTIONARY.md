# Data dictionary

Grain and key columns for every model. Raw tables keep API names (see `FPL_API_REFERENCE.md`).

## staging (views)

| Model | Grain | Key | Notes |
|-------|-------|-----|-------|
| `stg_snapshot` | one row | `snapshot_id` | `current_gameweek`, `next_gameweek` anchor every mart |
| `stg_teams` | one row per club | `team_id` | `team_short_name`, strength ratings, league record |
| `stg_gameweeks` | one row per GW | `gameweek` | `deadline_ts` (TIMESTAMPTZ), `is_current`, `is_next`, `is_finished` |
| `stg_positions` | one row per position | `position_id` | `position_code` GKP/DEF/MID/FWD, squad rules |
| `stg_players` | one row per player | `player_id` | Typed: `price_m`, `form`, `points_per_game`, `xg`, `xa`, `xgi`, `xgc`, `*_per_90`, `selected_by_pct`, `api_ep_next`, availability fields |
| `stg_fixtures` | one row per fixture | `fixture_id` | `gameweek` (NULL if unscheduled), `home_team_fdr`, `away_team_fdr`, scores |
| `stg_team_fixtures` | one row per team per fixture | (`team_id`, `fixture_id`) | Unpivoted: `opponent_team_id`, `is_home`, `fdr` **from this team's perspective** |
| `stg_player_gameweek_history` | one row per player per fixture played | (`player_id`, `fixture_id`) | `points`, `minutes`, per-fixture xG/xA/xGC, `price_at_gw_tenths`. Empty with `--no-history` |
| `stg_player_history_past` | one row per player per past season | (`player_id`, `season_name`) | Career priors |
| `stg_entry_picks` | one row per pick (15) | `player_id` | `squad_position` 1–15, `is_starter`, captain flags, `bank_m`, `squad_value_m`, `active_chip` |
| `stg_entry_gameweeks` | one row per GW played by the manager | `gameweek` | `gw_points`, `overall_rank`, `event_transfers`, `points_on_bench` |

## marts (tables)

| Model | Grain | Key | Main columns |
|-------|-------|-----|--------------|
| `mart_team_fixture_outlook` | one row per team per upcoming fixture in horizon | (`team_id`, `fixture_id`) | `horizon_index` (1 = next GW), `fdr`, `fdr_multiplier`, `home_multiplier`, `fixture_label` e.g. `LIV (A)` |
| `mart_team_fixture_summary` | one row per team | `team_id` | `fixtures_in_horizon`, `avg_fdr`, `fixture_score`, `fixture_run`, `fixture_rank` |
| `mart_player_form` | one row per player | `player_id` | Signals: `form_signal`, `season_ppg`, `xgi_points_rate`, `availability`, `is_injury_risk`, `rates_are_trusted`, `last5_*` |
| `mart_player_expected_points` | one row per player per upcoming fixture | (`player_id`, `fixture_id`) | `base_rate`, `expected_points`, all multipliers — the audit trail for any EP figure |
| `mart_player_horizon` | one row per player | `player_id` | `ep_next`, `ep_horizon`, `ep_horizon_per_m`, `rank_overall`, `rank_in_position`, `fixture_run`, `api_ep_next` |
| `mart_squad` | one row per pick | `player_id` | Picks joined to `mart_player_horizon` |
| `mart_captaincy` | one row per player with a fixture next GW | `player_id` | `ep_next`, `ep_as_captain`, `in_squad`, `captaincy_rank` |
| `mart_transfer_candidates` | one row per (out, in) pair, top 5 per out | (`out_player_id`, `in_player_id`) | `ep_horizon_gain`, `ep_next_gain`, `bank_after_m`, `candidate_rank` |
| `mart_differentials` | top 25 low-ownership players | `player_id` | `differential_rank` + all `mart_player_horizon` columns |
| `mart_chip_squads` | one row per player per chip (2 × 15) — **built in Python by the optimiser, not SQL** | (`chip`, `player_id`) | `chip` freehit/wildcard, `slot` 1–15, `is_starter`, `is_captain`, `chip_ep` (the EP optimised), `in_current_squad`, plus squad-level `xi_ep`, `cost_m`, `budget_m`, `budget_source` repeated on every row |

## history (append-only)

| Table | Grain | Key | Columns |
|-------|-------|-----|---------|
| `history.player_snapshot` | one row per player per snapshot | (`snapshot_id`, `player_id`) | `snapshot_ts`, `gameweek`, `now_cost`, `selected_by_percent`, `form`, `total_points`, `minutes`, `status`, `chance_of_playing_next_round`, `transfers_in_event`, `transfers_out_event` |

## Column naming rules

- `*_id` integer keys from the API (`player_id` = API `element`/`id`, `team_id` = `team`).
- `*_m` money in £m (API tenths ÷ 10). `*_pct` percentages as 0–100.
- `*_ts` TIMESTAMPTZ. `is_*` booleans. `*_per_90` per-90-minute rates.
- `ep_*` modelled expected points; `api_ep_*` FPL's own figure.
