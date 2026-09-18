# Scoring model — expected points (EP)

_Version 1.0 (Phase 0). Any change to a weight, multiplier or formula must update this file in
the same commit and add a line to `MODEL_LOG.md` (create it with the first change)._

## 1. What EP is

For every player and every upcoming fixture inside the horizon (default: next 5 gameweeks):

```
EP(player, fixture) = base_rate(player)
                    × availability(player)
                    × fdr_multiplier(fixture difficulty for the player's team)
                    × home_multiplier(fixture)
```

`ep_next` sums a player's fixtures in the next gameweek (so a double counts twice);
`ep_horizon` sums all fixtures in the horizon. Both live in `marts.mart_player_horizon`.

EP is a **ranking** device. Its absolute level is roughly calibrated to FPL points, but the
purpose is to order players and swaps, not to forecast a score.

## 2. base_rate — three signals, blended

```
base_rate = 0.45 × form_used + 0.30 × ppg_used + 0.25 × xgi_used
```

| Signal | Source | What it captures | Weakness it brings |
|--------|--------|------------------|--------------------|
| `form_used` | API `form` = average points per match over the last 30 days | Recent output, role changes, hot streaks | Noisy (4–5 matches), zero when a player hasn't featured in 30 days |
| `ppg_used` | API `points_per_game`, **shrunk** towards the position's typical PPG while the sample is small (below) | Stable output level | Slow to react to role changes; includes early-season fixtures |
| `xgi_used` | Computed from per-90 underlying stats (below) | *Process* rather than outcome — players who create chances keep scoring even when finishing runs cold | Per-90 rates lie on small samples |

Why these weights: form gets the most weight because FPL is played week to week and role
changes matter more than long-run averages; ppg is the anchor that stops one big haul from
dominating; xGI is the regression-to-the-mean signal that penalises lucky finishers and rewards
unlucky ones. The 45/30/25 split is a reasoned prior, not a fitted one — Phase 2.4's backtest
exists to fit it.

### The underlying-stats rate, `xgi_points_rate`

Expected FPL points per 90 minutes if the player played the full match, built from the
2025/26 scoring rules:

```
xgi_points_rate = 2                                   appearance (60+ minutes)
                + xG/90   × goal_points[position]     GKP 10, DEF 6, MID 5, FWD 4
                + xA/90   × 3                         assist
                + exp(−xGC/90) × cs_points[position]  GKP 4, DEF 4, MID 1, FWD 0
                + saves/90 ÷ 3                        goalkeepers only
                − xGC/90 ÷ 2                          goalkeepers and defenders only (−1 per 2 conceded)
                + min(bonus ÷ starts, 3)              bonus points per start, capped
```

`exp(−xGC/90)` is the Poisson probability of conceding zero goals when the expected goals
conceded is xGC/90 — a standard clean-sheet approximation.

Not modelled yet: defensive-contribution points (Phase 2.3), penalty-taker uplift
(`penalties_order` is staged, unused), yellow-card drag.

### Small samples: PPG shrinkage

Points-per-game is an average, and an average of one game is not a rate. A defender who keeps
a clean sheet in his only start has `points_per_game = 8.0` — the same number as an elite
full-back after ten starts — and the API's `form` (points ÷ matches in the last 30 days) has the
same problem. The model therefore blends each player's PPG with the **typical PPG for his
position**, weighted by how much he has actually played:

```
games   = minutes / 90
prior   = median points_per_game of players in the position with >= min_minutes_for_rates minutes
          (2.0 — a full game's appearance points — until anyone qualifies)
ppg_used = (season_ppg × games + prior × k) / (games + k)        k = shrinkage_games (3)
```

`k` is the number of "phantom" games of the prior. With a defender prior of ~3.2: one 8-point
game gives `(8×1 + 3.2×3) / 4 = 4.4`; four such games give 6.3; ten give 7.0. So one start
proves little, ten starts prove a lot, and the formula says exactly that. It is textbook
Bayesian shrinkage towards a group mean; set `shrinkage_games = 0` to switch it off. `form` is
**not** shrunk — its window is at most five matches for everyone, so it is uniformly noisy
rather than selectively misleading — and that is a candidate for Phase 2.4's backtest.

### Two guards on the signals

1. **Zero form ≠ bad form.** If `form = 0` but the player has minutes this season, they simply
   haven't played in 30 days (injury, suspension, rotation). The model substitutes `ppg_used`
   so a returning player isn't buried at zero.
2. **Small-sample rates.** `xgi_points_rate` is only trusted once a player has
   `min_minutes_for_rates` (180) minutes. Below that its weight moves onto `ppg_used` — the
   shrunk figure. (An earlier version fell back to the raw PPG, which put 55% of a one-game
   player's base rate on that single game. That is how a one-start defender briefly ranked
   sixth in the league.)

## 3. availability

```
availability = chance_factor × start_share × status_factor

chance_factor = chance_of_playing_next_round / 100      (NULL = no news = 1.0)
start_share   = min(1, starts / team matches played)    (1.0 before the season starts)
status_factor = 0 if status in ('u','n') else 1         unavailable / not in squad
```

**Team matches played is counted from finished fixtures** (`stg_team_fixtures WHERE
is_finished`), not taken from the API's `teams[].played`, which is always 0. Until GW5 2026/27
the model used the API field, tripped the divide-by-zero guard and gave *every* player
`start_share = 1.0` — the only mechanism separating regulars from rotation options was switched
off. `tests/test_model_small_samples.py` now pins the synthetic season's `played` to 0 so the
same mistake cannot pass CI again.

This is still the crude part of the model and the first thing Phase 2 improves. It penalises new
signings (few starts, many team games) and gives impact substitutes (minutes but no starts)
an availability of 0 — a minutes-based model (Phase 2.2) is the proper fix. A player flagged
`is_injury_risk` (chance below 75%) is additionally excluded from transfer-in suggestions.

## 4. Fixture multipliers

FPL's own Fixture Difficulty Rating (1 easy … 5 hard) is mapped to a multiplier
(`config/settings.toml → [analysis.fdr_multiplier]`):

| FDR | 1 | 2 | 3 | 4 | 5 |
|-----|---|---|---|---|---|
| multiplier | 1.25 | 1.15 | 1.00 | 0.85 | 0.70 |

Asymmetric on purpose: the drop from a neutral fixture to the hardest is larger than the lift
to the easiest, reflecting that elite defences suppress returns more than weak ones inflate them.
Home fixtures get ×1.04 and away ×0.96 (a modest, well-documented home edge).

A double gameweek is handled naturally — the player has two rows in
`mart_player_expected_points` for that GW and they sum. A blank contributes nothing.

## 5. From EP to decisions

| Decision | Mart | Rule |
|----------|------|------|
| Captain | `mart_captaincy` | Highest `ep_next` among owned players; the overall list shows what you're missing |
| Transfer | `mart_transfer_candidates` | For each owned player, same-position candidates you can afford, not owned, not an injury risk, ≤3 per club after the swap; ranked by `ep_horizon_gain`. Take a hit only when gain clearly exceeds 4 inside the horizon |
| Bench | `mart_squad` | Lowest `ep_next` starters vs highest `ep_next` bench (ordering automation: Phase 2.6) |
| Chips | — | Phase 3.1 |

## 6. Chip squads — Best Free Hit / Best Wildcard team

Sections 9 to 11 of the report pick the best *legal 15* rather than ranking individuals.
That is a constrained optimisation, so it is solved exactly as an integer programme
(`src/fpl_analysis/optimiser.py`, SciPy's bundled HiGHS solver, < 1 s) — the one deliberate
piece of analytics that is Python rather than SQL. Its only input is `mart_player_horizon`;
its only output is `marts.mart_chip_squads`, which the report reads like any other mart.

A third solve, **Best Free Hit team for the following gameweek** (report section 10), maximises
`ep_gw2` — the EP of the gameweek after next. It is informational: a yardstick for how far the
current squad sits from the best possible XI one week out, computed on today's snapshot. It is not
a chip plan (team news, prices and this week's results will move it) and the report says so.

```
maximise   Σ ep_i·y_i  +  Σ ep_i·c_i  +  w_bench · Σ ep_i·(x_i − y_i)

x_i = in squad, y_i = starts (y ≤ x), c_i = captain (c ≤ y, exactly one)
subject to  2 GKP / 5 DEF / 5 MID / 3 FWD in the squad
            11 starters: 1 GKP, 3–5 DEF, 2–5 MID, 1–3 FWD   (from the API's element_types; 5-2-3 is legal)
            ≤ 3 players per club
            Σ price_i·x_i ≤ budget   (squad value + bank; config override)
```

| Chip | `ep_i` | Bench weight | Reading |
|------|--------|--------------|---------|
| Free Hit | `ep_next` (one gameweek) | 0.05 | Best XI for this week alone; the bench barely matters because the squad reverts |
| Wildcard | `ep_horizon` (the horizon) | 0.15 | Best squad to *hold*; bench quality matters because rotation and injuries hit over weeks |

Eligibility: `availability > 0`, status `a` or `d`, and not an injury risk (config
`[chips] exclude_injury_risk`). The report shows the modelled XI EP with the captain doubled
next to your current XI on the same basis, so the **gain** is directly comparable, and marks
which of the 15 you already own so a Wildcard's number of changes is visible.

The objective is the same EP the rest of the report uses, so the chip squads inherit its
limitations (section 2–3) — a crude minutes model will put a rotation risk in the XI just as
readily as it ranks him highly elsewhere.

## 7. Sanity checks to run on live data (Phase 1.4)

- The top 10 by `ep_horizon` should look like the consensus premium picks with good fixtures.
  If a 1-cameo wonder appears, the minutes guard failed.
- `api_ep_next` (FPL's own number) vs model `ep_next`: large disagreements should be explainable
  by fixture or availability. Notebook 02 has the comparison.
- No player with `status in ('i','s','u')` should carry EP > 0 (tested).
