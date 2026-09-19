# Contributing

This project runs like a small work project: `main` is always releasable, nobody commits to it
directly, and every change arrives as a pull request that the maintainer (Angus) reviews and merges.

## 1. Get running (10 minutes)

```bash
git clone https://github.com/acemacdonald/fpl-player-analysis.git
cd fpl-player-analysis
python3 -m venv .venv && source .venv/bin/activate      # Python 3.11+ recommended (3.10 works)
pip install -e ".[dev]"
pytest                                                   # 21 tests, no network, ~5 s — proves the install
fpl run --no-history                                     # ~10 s: pulls the API, builds, writes reports/GWxx.md
```

If `pytest` is green and `fpl run --no-history` writes a report, you are set up. `fpl run` (with
history, ~4 min) gives the full model. In VS Code, select the `.venv` interpreter/kernel for the
notebooks.

### Analysing your own team

`config/settings.toml` carries the maintainer's FPL entry id. Don't edit it for your own runs —
override it per shell instead, so the file never churns in PRs:

```bash
export FPL_MANAGER_ENTRY_ID=1234567          # your id: the number in the URL when viewing your team
fpl run
```

Same for anything else in settings (`FPL_<SECTION>_<KEY>`). `config/squad_override.toml` is
personal deadline-day state; if you need one, copy it to `config/squad_override.local.toml`
(git-ignored) — the loader prefers the `.local` file when it exists.

## 2. The workflow

```
main  ─────●──────────────●────────────────●──────►   (protected: PR + review + green CI only)
            \            /                  /
             ●──●──●────●   feat/xyz       /            you: branch → commit → push → open PR
                          ●──●───●────────●   fix/abc    reviewer: comment / request changes / approve → merge
```

1. **Start from fresh `main`:** `git checkout main && git pull`.
2. **Branch:** `git checkout -b feat/short-description` (or `fix/…`, `docs/…`, `model/…`).
3. **Work in small commits.** Run `ruff check .` and `pytest` before each push — CI runs the same.
4. **Push and open a PR:** `git push -u origin feat/short-description`, then "Compare & pull
   request" on GitHub. The PR template asks what changed, why, and how you tested it.
5. **Review.** The maintainer is auto-requested (CODEOWNERS). Expect comments; push follow-up
   commits to the same branch — the PR updates itself. Don't force-push a branch under review.
6. **Merge.** The maintainer squash-merges once CI is green and the review is approved, and
   deletes the branch (the "Delete branch" button on the merged PR). You then
   `git checkout main && git pull && git branch -D <branch>` and start the next one — `-D`, because a
   squash-merged branch looks unmerged to git. Never "publish" a branch again after it has merged.

Never `git push` to `main` directly — the branch rule will reject it anyway.

## 3. What a good PR looks like here

- **One concern per PR.** A model change and a report layout change are two PRs.
- **Model changes carry their reasoning.** Any change to weights, multipliers or formulas
  updates `docs/SCORING_MODEL.md` in the same PR and adds a line to `docs/MODEL_LOG.md`
  (create it on first use) with before/after evidence — the report on a saved snapshot
  (`fpl build --snapshot <id> && fpl report --stdout`) is the easiest evidence.
- **SQL follows the house style:** Snowflake conventions, upper-case keywords, one column per
  line, CTEs, `QUALIFY` for window filters, each model file opens with its grain and purpose.
  Staging = rename/cast only; business logic lives in marts.
- **Raw is immutable.** Fix data problems in staging SQL, never by editing snapshots.
- **Tests for behaviour.** The synthetic season in `tests/synthetic.py` runs everything
  offline; extend it rather than adding network-dependent tests.
- **Tests are hermetic.** They must never depend on the live `config/` values (your entry id, your
  squad override) — `tests/conftest.py` redirects every path via `FPL_PATHS_*` env vars. A test that
  breaks when someone changes their own settings is a broken test, not a broken pipeline.
- **Reports and snapshots are local.** `reports/*.md` and `data/raw/*` are git-ignored; never
  commit either. Config flips for a different entry id are working state, not commits.
- **Branch from `main` without tracking it.** `git checkout -b feat/x origin/main` silently sets
  `main` as the branch's push target, so a later `git push` (or Desktop "Push origin") lands on
  `main`. Use `git checkout -b feat/x --no-track origin/main`, or branch from your local `main`,
  and check `git branch -vv` shows no upstream before the first push.

## 4. Things that are not in the repo

`data/` (snapshots + DuckDB), `MEMORY.md` and `*.local.toml` are git-ignored on purpose. Nothing
in the repo is secret — the FPL API needs no key — so there is no `.env` to share.

## 5. Where to read first

`README.md` → `docs/ARCHITECTURE.md` (why each layer exists) → `docs/SCORING_MODEL.md` (the EP
model) → `docs/PROJECT_PLAN.md` (what's next, by phase). The full explainer with the terminology
sheet is `docs/FPL_Player_Analysis_Explainer.pdf`.
