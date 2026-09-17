# FPL Player Analysis

Premier League player analytics for weekly Fantasy Premier League decisions, built on the free
official FPL API. Python pulls immutable raw snapshots; Snowflake-style SQL models them in a local
DuckDB warehouse; a Markdown gameweek report tells you who to captain, who to transfer and why.

```
FPL API ──► data/raw/<snapshot>/*.json ──► DuckDB (raw → staging → marts) ──► reports/GWxx.md
                                                        ▲
                                              notebooks/*.ipynb (VS Code)
```

## Quick start

```bash
git clone <your-repo-url> fpl-player-analysis
cd fpl-player-analysis
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

fpl run                    # refresh (≈2 min) + build + report → reports/GW05.md
fpl run --no-history       # fast variant: 6 API calls, no per-player history
```

Then open the folder in VS Code, pick the `.venv` interpreter, and open
`notebooks/03_weekly_decision.ipynb`.

Your FPL team ID lives in `config/settings.toml` (`[manager] entry_id`). It is the number in
the URL when you view your team on fantasy.premierleague.com. No login or key is needed.

## Deadline-day gotcha: the API hides your new team until the deadline

The public API only reveals a gameweek's picks and transfers *after* that gameweek's deadline.
If you have already made transfers this week, put your real squad in `config/squad_override.toml`
and set `enabled = true`; the build uses it instead of the (stale) API picks, the report says so,
lists the transfers it implies (override squad vs last API picks) and works out free transfers left
and any hit taken. Set it back to `false` after the deadline.

## Commands

| Command | What it does |
|---------|--------------|
| `fpl refresh [--no-history] [--no-entry]` | Pull the API into a new dated snapshot under `data/raw/` |
| `fpl build [--snapshot ID]` | Load a snapshot into `data/fpl.duckdb`, append history, run all SQL models |
| `fpl report [--snapshot ID] [--stdout]` | Render `reports/GW<next>.md` from the marts |
| `fpl run` | The three above, in order — the Friday one-liner |
| `fpl snapshots` | List snapshots on disk |
| `fpl history-rebuild` | Replay every snapshot into the `history` schema (after a fresh clone) |
| `fpl query "SELECT …"` | Ad-hoc SQL against the warehouse |

`python -m fpl_analysis …` works identically if the `fpl` script is not on your PATH.

## Repository layout

```
config/settings.toml        model weights, horizon, paths, your entry id (env-overridable)
src/fpl_analysis/           api · ingest · store · squad · squad_override · optimiser · report · cli
sql/staging/                one typed view per raw table (no business logic)
sql/marts/                  fixtures outlook, EP model, squad, captaincy, transfers, differentials
notebooks/                  01 explore API · 02 player analysis · 03 weekly decision
reports/                    committed gameweek reports = the decision log
docs/                       PROJECT_PLAN · ARCHITECTURE · SCORING_MODEL · FPL_API_REFERENCE · DATA_DICTIONARY
tests/                      pytest on a synthetic snapshot (offline, seconds)
data/                       raw snapshots + DuckDB file (git-ignored)
```

## The model in one paragraph

For each player and each fixture in the next five gameweeks,
`EP = base_rate × availability × fixture multiplier × home multiplier`, where the base rate
blends recent form (45%), season points-per-game (30%) and a points rate implied by xG/xA/xGC
(25%). Sum over the horizon, rank, and apply the FPL rules (position, budget, three per club)
to get transfer candidates. The Best Free Hit / Best Wildcard sections solve the best legal 15
exactly (integer programme over the same EP). Every number in the report traces back to a row in
`marts.mart_player_expected_points`. Full definition and known limitations:
[`docs/SCORING_MODEL.md`](docs/SCORING_MODEL.md).

## Weekly routine

Thursday evening `fpl refresh`; Friday `fpl build && fpl report`, read the report, make the
calls on the FPL site, `git commit reports/`. Under five minutes. Details and the season plan:
[`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md).

## Development

```bash
pytest                 # 21 tests on a synthetic season, no network
ruff check .           # lint
```

CI (`.github/workflows/ci.yml`) runs both on every push. Snapshots are never committed;
reports are.

## Why DuckDB and SQL rather than pandas?

Because the point is to design a layered warehouse the way you would in Snowflake — raw
landing, typed staging, analytical marts, one place per transformation — and DuckDB lets that
run on a laptop with zero cost or credentials. The SQL is written to lift to Snowflake with
only the raw load changing. Reasoning for every layer:
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Licence

MIT. Data © Fantasy Premier League / Premier League; this project uses their public API for
personal, non-commercial analysis.
