# Architecture

_The "why" behind every layer, so it can be explained and defended._

## 1. The shape of the system

```
                 ┌──────────────────────────────┐
  FPL API  ───►  │ ingest.py  (raw snapshots)   │  data/raw/<snapshot_id>/*.json   immutable
                 └──────────────┬───────────────┘
                                │ load_snapshot()
                 ┌──────────────▼───────────────┐
                 │ DuckDB  data/fpl.duckdb      │
                 │   raw.*       verbatim tables│  typed by the JSON reader, strings kept
                 │   history.*   append-only    │  price / ownership / form per snapshot
                 │   staging.*   views          │  rename + cast, one per raw table
                 │   marts.*     tables         │  fixtures, EP model, squad, transfers
                 └──────────────┬───────────────┘
                                │ report.py
                 ┌──────────────▼───────────────┐
                 │ reports/GWxx.md              │  the weekly decision document (committed)
                 └──────────────────────────────┘
                 notebooks/*.ipynb  ── query the same warehouse for exploration
```

Four layers, one direction of flow. Nothing writes upstream.

## 2. Decisions and their reasons

### D1. Immutable dated snapshots as the raw layer

The API only shows the present. Prices, ownership, injury flags and even `form` overwrite
themselves every hour. Anything about *change* — the whole basis of price prediction, form
trajectories and backtesting — has to be manufactured by keeping copies. So every refresh
writes a complete new directory and never modifies an old one. This is the same
landing-zone discipline as an S3/Azure stage feeding Snowflake, and it makes every build
reproducible: `fpl build --snapshot 20260917_101500` rebuilds exactly what you saw that day.

Cost: ~12 MB per full snapshot (bootstrap 3 MB + player history 8 MB). A season of daily
snapshots is ~3 GB. Acceptable; `data/raw` is git-ignored.

### D2. DuckDB rather than Snowflake, Postgres or pandas-only

| Option | Why not (or why) |
|--------|------------------|
| Snowflake | Best fit for the CV, but a personal project has to survive 38 weeks and trial accounts expire. Also introduces credentials into a repo that otherwise needs none. Kept as Phase 4: the SQL is written to move |
| Postgres | A server to run, no JSON-to-table one-liner, dialect further from Snowflake |
| pandas only | Fast to start, but business logic in DataFrame chains is hard to read, review and port; the project's stated goal is SQL-first design |
| **DuckDB** | Embedded single file, zero cost, reads JSON natively, dialect close to Snowflake (CTEs, windows, `QUALIFY`, `TRY_CAST`), returns DataFrames to notebooks. Chosen |

### D3. Schema-on-read raw, schema-on-write staging

`raw.*` tables are created with `read_json_auto` — whatever the API sends, we keep, typed only
as far as JSON typing goes (numbers-as-strings stay strings). `staging.*` views are the single
place where columns are renamed and cast. When the API adds or renames a field (it did in
2025/26 with `defensive_contribution`), raw keeps loading and the fix is one line in one
staging file. Business logic never touches raw.

### D4. A dbt-flavoured runner instead of dbt itself

Models are plain SQL files with `{{ ref('…') }}` and `{{ var('…') }}` rendered by Jinja2 and
executed in filename order. Why not dbt from day one? It would add a project scaffold, profiles,
and a second CLI before there was anything to model. The runner is ~60 lines, and because the
macros mirror dbt's, migrating is `dbt init` plus moving files (Phase 3.5). Filename prefixes
(`010_`, `100_`) make dependency order explicit rather than inferred — a deliberate simplicity
trade-off that is fine at 20 models and would not be at 200.

Materialisation: staging = views (cheap, always fresh), marts = tables (the report queries them
many times; a table also freezes the numbers a report was built from).

### D5. Settings in TOML, overridable by environment

No secrets exist (the API is public), so config is committed. TOML keeps types
(`0.45` is a float, `5` is an int) and nests the FDR multiplier table cleanly. Environment
overrides (`FPL_ANALYSIS_WEIGHT_FORM=0.6`) exist for two reasons: CI, and cheap what-if
sensitivity runs from a notebook without editing the file.

### D6. The report is Markdown, generated from marts, and kept local

Markdown renders in GitHub, VS Code and any editor; it diffs; it needs no server. The reports
are *not* committed: the file is keyed by gameweek only and the pipeline is run for more than
one manager, so a committed `reports/GW05.md` would be whichever entry ran last. The audit trail
("what did the model say in GW12?") is the dated snapshot under `data/raw/` plus the code at that
commit — the report is a pure function of the two. Rendering goes through a Jinja template so the
layout can change without touching SQL, and every table is a straight projection of a mart —
if a number in the report is wrong, the mart is wrong, and there is exactly one place to look.

### D7. Squad-awareness through the public entry endpoints

`entry/{id}/…` is public and needs no login, which keeps the project credential-free. The one
thing it does not expose is banked free transfers; `squad.estimate_free_transfers` reconstructs
them from the transfer history using the published rules, and the report labels the figure as an
estimate with a config override.

### D8. Tests run on a synthetic snapshot, not the live API

`tests/synthetic.py` fabricates a 20-club, 120-player, 380-fixture season with the *exact*
field names of the real payloads (verified against the live endpoints on 17 Sep 2026). The
suite therefore runs offline, in CI, in seconds, and catches SQL regressions without ever
depending on the API being up or the season being in a particular state.

## 3. Data flow in detail

1. `fpl refresh` → `ingest.create_snapshot()`
   - `bootstrap-static` (1 call), `fixtures` (1), entry endpoints (4), `element-summary` (~660,
     0.15 s apart ≈ 2 min). Writes JSON + `manifest.json`.
2. `fpl build` → `store.build()`
   - `load_snapshot()`: `CREATE OR REPLACE TABLE raw.x AS SELECT … FROM read_json_auto(…)`.
     Optional tables that are absent get an empty, typed table so every view compiles.
   - `append_history()`: `INSERT OR IGNORE` into `history.player_snapshot` (PK snapshot_id,
     player_id) — idempotent.
   - `run_models()`: render each model, `CREATE VIEW|TABLE schema.name AS …` in order.
3. `fpl report` → `report.build_report()`
   - Reads marts into DataFrames, converts to Markdown tables, renders the template, writes
     `reports/GW<next>.md`.

## 4. Model lineage

```
raw.players ─────► stg_players ──┐
raw.teams ───────► stg_teams ────┼─► mart_player_form ─► mart_player_expected_points ─► mart_player_horizon
raw.element_types► stg_positions─┘          ▲                       ▲                        │
raw.player_history► stg_player_gameweek_history                     │                        ├─► mart_captaincy
raw.fixtures ────► stg_fixtures ─► stg_team_fixtures ─► mart_team_fixture_outlook ─► mart_team_fixture_summary
raw.snapshot ────► stg_snapshot ───────────────────────────┘                                 ├─► mart_differentials
raw.entry_picks ─► stg_entry_picks ──────────────────────────────────────────────────────────┼─► mart_squad ─► mart_transfer_candidates
raw.entry_gameweeks► stg_entry_gameweeks (report only: standing, FT estimate)
```

## 5. What a Snowflake version would change

Only the raw load. `read_json_auto` becomes `COPY INTO` from a stage (or a `VARIANT` column
plus `LATERAL FLATTEN`). In the models: `STRING_AGG` → `LISTAGG`, and `typeof` in tests →
`TYPEOF`. `QUALIFY`, `TRY_CAST`, `EXP`, window functions and CTE structure are identical.
Everything in `sql/` was written with that port in mind — see `docs/PROJECT_PLAN.md` Phase 4.

## 6. Known limitations (tracked in the plan)

- Transfer affordability uses market price, not FPL selling price (Phase 2.1).
- Minutes model is season `starts / team games`; harsh on new signings (Phase 2.2).
- Defensive-contribution points are not modelled explicitly (Phase 2.3).
- No backtest yet, so weights are reasoned, not fitted (Phase 2.4).
