# FPL API reference (as used by this project)

Base URL: `https://fantasy.premierleague.com/api/` — free, no key, no login for anything
below. Undocumented and unversioned: field names can change between seasons (verified live on
**17 September 2026**, season 2026/27, GW4 current / GW5 next). Cloudflare fronts it; send a
browser-like `User-Agent` and don't hammer the per-player endpoint.

| Endpoint | Used by | Rows | Notes |
|----------|---------|------|-------|
| `bootstrap-static/` | every refresh | 1 object | The big one (~3 MB): `elements` (players, 659), `teams` (20), `events` (38 gameweeks), `element_types` (4 positions), `game_settings`, `chips`, `phases` |
| `fixtures/` | every refresh | 380 | All fixtures; `event` is `null` until a postponed game is rescheduled. Includes per-match `stats` once played |
| `element-summary/{player_id}/` | `fpl refresh` (not with `--no-history`) | 1 per player | `history` (this season's per-fixture rows), `history_past` (career seasons), `fixtures` (remaining) |
| `entry/{entry_id}/` | squad | 1 object | Public profile: team name, overall points/rank, `started_event`, last-deadline bank/value |
| `entry/{entry_id}/history/` | squad | object | `current` (per-GW rows), `past` (seasons), `chips` (name, event) |
| `entry/{entry_id}/event/{gw}/picks/` | squad | object | `picks` (15 rows), `entry_history` (bank, value, transfers this GW, points on bench), `active_chip`, `automatic_subs` |
| `entry/{entry_id}/transfers/` | squad | list | Every transfer with `element_in_cost` / `element_out_cost` — the basis of selling-price logic in Phase 2 |

Not used yet: `event/{gw}/live/` (live points), `leagues-classic/{id}/standings/`,
`dream-team/{gw}/`, `element-summary` `fixtures` (loaded to `raw.player_fixtures` but
unmodelled).

## Units and gotchas

- **Money is in tenths of £m**: `now_cost = 55` means £5.5m; `bank = 6` means £0.6m. Staging
  divides by 10 into `*_m` columns.
- **Many numbers are strings**: `form`, `points_per_game`, `selected_by_percent`, `ep_next`,
  `ict_index`, `influence`, `creativity`, `threat`, `expected_goals`, `expected_assists`,
  `expected_goal_involvements`, `expected_goals_conceded`, `value_form`, `value_season`.
  The `*_per_90` fields are real numbers. `stg_players` casts all of them with `TRY_CAST`.
- **`chance_of_playing_next_round` is `null` for fit players**, not 100. Treat null as 100.
- **`status` codes**: `a` available, `d` doubtful, `i` injured, `s` suspended, `u` unavailable
  (left the league), `n` not in squad.
- **FDR direction**: `team_h_difficulty` is the difficulty faced *by the home team*.
- **Positions**: `element_type` 1 GKP, 2 DEF, 3 MID, 4 FWD. `element_types` carries the
  squad rules (2/5/5/3 and min/max starters).
- **Gameweek flags** in `events`: exactly one `is_current`, one `is_next` during the season;
  both false pre-season; `is_next` false after GW38.
- **`form`** = average points per match over the previous 30 days (0 if none).
- **`ep_next`** = FPL's own expected points for the next GW — kept as `api_ep_next` for
  benchmarking the model.
- **2025/26 additions** still present in 2026/27: `defensive_contribution`,
  `defensive_contribution_per_90`, `clearances_blocks_interceptions`, `recoveries`, `tackles`,
  `price_change_*` fields, `scout_risks`.
- **Picks `multiplier`**: 0 benched, 1 playing, 2 captain, 3 triple captain.
- **Free transfers are not exposed** anywhere public — see `fpl_analysis.squad`.
- **`teams[].played` is always 0** (as are `win`/`draw`/`loss`/`points`). Count finished fixtures per team
  instead — `mart_player_form` does. Trusting it silently disabled the start-share part of availability.
- **Upcoming-gameweek picks and transfers are hidden until the deadline.** `entry/{id}/event/{next_gw}/picks/`
  returns 404 and `entry/{id}/transfers/` omits the pending moves until the deadline passes (verified live
  17 Sep 2026: GW5 picks 404, GW5 transfers absent, while the site showed them). Before the deadline the
  pipeline therefore sees the *previous* gameweek's squad. Use `config/squad_override.toml` to state your
  real squad; the authenticated `my-team/{id}/` endpoint (login cookie) is the proper fix — Phase 2.0.
  When the override is active the build keeps the API picks in `raw.entry_picks_api`; the report diffs
  the two to list the transfers made this week and works out free transfers left and any hit taken
  (`free_transfers` in the override file / the estimate = the count going INTO the gameweek).

## Fields by table (raw layer)

The raw tables keep the API's own column names. `docs/DATA_DICTIONARY.md` documents the
renamed staging columns. To see the live column list for any raw table:

```sql
DESCRIBE raw.players;
```
