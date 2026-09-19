# FPL Player Analysis — Project Plan

_Version 1.0 · 17 September 2026 · Owner: Angus Macdonald_

## 1. Purpose

Build a personal analytics system that pulls Premier League player data from the free official
Fantasy Premier League API every week, models it in a Snowflake-style layered SQL warehouse, and
produces a Markdown gameweek report that supports four decisions for entry **234865**: captaincy,
transfers, bench order and chip timing.

Two objectives sit behind the FPL objective:

1. **Portfolio evidence for technical delivery roles.** The repo demonstrates an end-to-end
   pipeline (API → immutable raw snapshots → typed staging → analytical marts → rendered
   output) with tests, docs and CI, in the same layered pattern used in a Snowflake/dbt shop.
2. **Practice designing before building.** Every phase below starts with a design note in
   `docs/` and ends with a testable deliverable. The plan is the contract.

## 2. Scope

**In scope**

- Official FPL API only (`https://fantasy.premierleague.com/api/`, no auth, free).
- All ~660 players across the 20 clubs; the tracked manager's public squad data.
- Local DuckDB warehouse; SQL models written to Snowflake conventions.
- Weekly Markdown report generated into `reports/` (local; regenerable from the snapshot).
- Jupyter notebooks for exploration in VS Code.

**Out of scope (for now)**

- Any paid or scraped data source (Understat, FBref, Opta). Revisit in Phase 5 only if the
  API's own xG/xA fields prove insufficient.
- A hosted dashboard or web app. A Streamlit layer is a Phase 5 option, not a commitment.
- Automated transfer *execution*. The tool recommends; Angus clicks.
- Mini-league / rival analysis. Possible later via the public `leagues-classic` endpoint.

## 3. Success criteria

| # | Criterion | Measure |
|---|-----------|---------|
| S1 | The weekly routine takes under 5 minutes of Angus's time | `fpl run` + read the report + act |
| S2 | Every recommendation is explainable from the report alone | Each table shows the metric that justifies the rank |
| S3 | The model beats a naive baseline over the season | Modelled-XI EP vs "captain the most-owned player, never transfer" — tracked in the report from Phase 3 |
| S4 | Repo is presentable to a hiring manager cold | README, architecture doc, tests green in CI, no secrets |
| S5 | Data survives the season | Every snapshot retained; history schema rebuildable from disk |

## 4. Phases

### Phase 0 — Foundation (DONE, 17 Sep 2026)

Delivered in this repo:

- Package `fpl_analysis` with API client, snapshot ingestion, DuckDB store, model runner,
  free-transfer estimator, report renderer and CLI.
- 10 staging models and 9 marts under `sql/`.
- Report template covering standing, risks, captaincy, transfers, top picks, value,
  differentials, fixture outlook and market movers.
- Three notebooks, 14 passing tests on a synthetic snapshot, GitHub Actions workflow.
- Docs: this plan, architecture, scoring model, API reference, data dictionary.

**Exit criterion:** `fpl run` on Angus's Mac produces `reports/GW05.md` from live data.
That is the first task in Phase 1 — it could not be executed from the build environment
because the API host is not reachable from there, so live-data validation is deliberately
Angus's first commit.

### Phase 1 — First live gameweek (target: GW5 deadline, 19 Sep 2026)

| Task | Detail | Done when |
|------|--------|-----------|
| 1.1 Bootstrap locally | `python -m venv .venv`, `pip install -e ".[dev]"`, `fpl run` | Snapshot on disk, 20 models built, GW05 report rendered |
| 1.2 Validate raw load | Compare `raw.players` count to `bootstrap-static` (659 at time of writing); spot-check 3 known players' prices and xGI | No type errors in staging; numbers match the FPL site |
| 1.3 Validate squad logic | Squad table shows the right 15, captain and bank; estimated FTs match the site (expected 4 for GW5 with 0 transfers made) | Any mismatch logged as an issue with the API payload attached |
| 1.4 Read the report critically | Do the captaincy and transfer suggestions pass the eye test? Note every disagreement in `docs/MODEL_LOG.md` (create it) | First entry in the model log |
| 1.5 Create the GitHub repo | `git init`, push `main`, enable Actions, add the badge to README | CI green on first push |
| 1.6 First live GW05 report | Pipeline proven end to end | `reports/GW05.md` renders from live data |

### Phase 2 — Make the model honest (GW6–GW9)

The Phase 0 model is a defensible first cut, not a calibrated one. This phase closes the
known gaps, in priority order:

| Task | Why it matters | Approach |
|------|---------------|----------|
| 2.0 Authenticated `my-team` pull | The public API hides picks and transfers for the upcoming GW until its deadline, so on deadline day the model sees last week's squad. `config/squad_override.toml` (Phase 0 stop-gap, done 17 Sep) fixes this manually. The authenticated `api/my-team/{entry_id}/` endpoint returns the live pre-deadline squad, bank **and the real free-transfer count** | Cookie-based auth (`pl_profile` from the browser, stored in a git-ignored `.env`); fall back to the override file, then to the public picks. Removes the FT estimate too |
| 2.1 Selling-price logic | Transfer affordability currently uses market price; FPL pays you purchase price + 50% of any rise. Suggestions can be off by £0.1–0.5m | Derive purchase price per squad player from `raw.entry_transfers` (`element_in_cost`) and, for the original squad, `player_history.value` at `started_event`. New column `selling_price_m` in `mart_squad` |
| 2.2 Minutes model v2 | `start_share` penalises new signings and returning players. It also ignores sub appearances | Weight last-5 starts more than season starts (history table already exists); treat `chance_of_playing` 75% as 0.75 × start probability rather than a hard exclusion |
| 2.3 Defensive contribution points | 2025/26 rule: DEF get 2 pts for 10+ CBIT, MID/FWD for 12+ CBIRT. Currently only captured indirectly via ppg | Add `dc_points_rate` to `mart_player_form` from `defensive_contribution_per_90` once the field's exact semantics are verified against a few players' actual GW scores |
| 2.4 Backtest harness | Without it, weight changes are guesses | After each GW, join `mart_player_expected_points` (as built pre-deadline) to actual `player_history.points`. Store per-GW MAE and rank correlation in `marts.mart_model_accuracy`. Requires keeping the pre-deadline snapshot — already guaranteed by immutable snapshots |
| 2.5 Multi-transfer planning | The current mart is one-for-one swaps | Add a two-move search (out A+B, in C+D, same positions, combined budget) limited to the top 30 candidates per position to keep it tractable in SQL |
| 2.6 Bench order + auto-sub EV | The report shows the bench but doesn't order it | Rank bench by `ep_next × P(start)`; flag when a bench player out-scores a starter |

### Phase 3 — Season tooling (GW10–GW19)

| Task | Detail |
|------|--------|
| 3.1 Chip planner | **Partly done 17 Sep:** report sections 9–10 solve the best Free Hit and Wildcard squads (exact ILP over EP) and show the gain over the current XI. Remaining: detect double/blank gameweeks from `stg_team_fixtures` as soon as fixtures are rescheduled; compute Bench Boost / Free Hit / Triple Captain EV per GW and put a "chip watch" section in the report |
| 3.2 Price-change tracker | Daily 02:00 UK snapshot (launchd or GitHub Actions cron); `history.player_snapshot` already stores price and net transfers; add `mart_price_pressure` (net transfers vs ownership) and a "rises/falls likely tonight" section |
| 3.3 Baseline comparison (S3) | Track the naive baseline alongside the model each week; publish the running gap in the report header |
| 3.4 Model log discipline | Every weight/formula change: a `MODEL_LOG.md` entry with before/after backtest numbers |
| 3.5 dbt migration (optional) | Models already use `ref()`/`var()`; moving to `dbt-duckdb` buys tests, docs and lineage graphs. Do it only if the repo is being used as a dbt showcase |

### Phase 4 — Snowflake edition (optional, any time after Phase 2)

The reason the SQL is written Snowflake-style. If a Snowflake trial or personal account is
available, the migration is:

1. Land snapshots in an internal stage, `COPY INTO raw.*` with `STRIP_OUTER_ARRAY` and
   `MATCH_BY_COLUMN_NAME`, or load via `VARIANT` + `LATERAL FLATTEN`.
2. Replace DuckDB-specific bits: `read_json_auto` (raw load only), `TRY_CAST` (same in
   Snowflake), `STRING_AGG` → `LISTAGG`, `typeof` → `TYPEOF`, `EXP` (same).
3. Point the model runner at a Snowflake connection (`snowflake-connector-python`), or run the
   models with dbt-snowflake.
4. Streams + tasks for the daily refresh, if you want to show that pattern.

### Phase 5 — Nice-to-haves (backlog, unscheduled)

- Streamlit dashboard reading the marts (fixture heatmap, player compare, squad EV).
- Mini-league rival tracking (`leagues-classic/{id}/standings/` and rivals' picks).
- External xG source if the API's xG proves too coarse.
- Ownership-weighted "effective ownership" for captaincy risk (needs top-10k sample scraping — check terms first).
- Set-piece taker weighting (`penalties_order`, `corners_and_indirect_freekicks_order` are already staged).

## 5. Weekly operating cadence

| When | What | Command |
|------|------|---------|
| Any day, 02:30 UK (optional, from Phase 3) | Price snapshot | `fpl refresh --no-history` |
| Thursday evening | Full refresh after the pressers start | `fpl refresh` |
| Friday (deadline day) | Build + report, read it, decide, act on the FPL site | `fpl build && fpl report` (or `fpl run`) |
| Monday/Tuesday | Backtest last GW (from Phase 2) | notebook 03, last section |

The whole Friday loop should be under five minutes (S1).

## 6. Risks and mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| API shape changes mid-season (new/renamed fields) | Medium — happened in 2025/26 with `defensive_contribution` | Build breaks | Raw layer is schema-on-read; only staging casts named columns. A rename is a one-line staging fix; tests on the synthetic snapshot catch it |
| Cloudflare blocks the client (403/429) | Low–Medium | No refresh | Browser-like UA, retries with back-off, polite sleep between per-player calls; `--no-history` fallback needs only 6 calls |
| Model over-fits to form | Medium | Bad transfers | Weights are config, backtest harness in Phase 2 makes changes evidence-based |
| Free-transfer estimate drifts | Low | Wrong "hit" advice | Estimate is labelled; `free_transfers_override` in config or `free_transfers` in the squad override file |
| Public API hides pre-deadline picks | Certain, every week | Transfer advice modelled on last week's squad | `config/squad_override.toml` (done); authenticated `my-team` pull (Phase 2.0) |
| Time — the season doesn't wait | High | Phases slip | Phase 0 is already usable; each later phase is independently valuable, so slipping is cheap |
| DuckDB file corruption / laptop loss | Low | Lose history | Raw snapshots are the source of truth; `fpl history-rebuild` replays them. Commit snapshots to a private branch or cloud folder if history matters |

## 7. Repository conventions

- Branch per phase task (`feat/2.1-selling-price`), PR to `main`, CI must be green.
- Reports and snapshots are never committed; the snapshot is the record of what the model saw.
- SQL: Snowflake style (see `CLAUDE.md`). Python: `ruff` clean, type hints, docstrings that
  explain *why*.
- Every model file starts with a comment stating its grain ("one row per …") and purpose.
- Config changes that alter recommendations get a `MODEL_LOG.md` entry.

## 8. Immediate next actions (this week)

1. Open the folder in VS Code, create the venv, `pip install -e ".[dev]"`.
2. `fpl run` → confirm `reports/GW05.md` renders from live data (Phase 1.1).
3. Check the estimated FT figure and the squad table against the FPL site (Phase 1.3).
4. `git init`, first commit, push to GitHub, confirm CI (Phase 1.5).
5. Read the GW5 report, make the Friday decisions.
