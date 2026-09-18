"""Deterministic synthetic FPL snapshot for tests.

Mirrors the real API shapes (field names verified against the live endpoints in Sept 2026)
with a small league: 20 clubs, 38 gameweeks (GW4 current, GW5 next), a full round-robin
fixture list, 6 players per club, four GWs of per-player history, and a legal 15-man squad
for the tracked entry. Everything is seeded so tests are reproducible.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLUBS = [
    ("Arsenal", "ARS"), ("Aston Villa", "AVL"), ("Bournemouth", "BOU"), ("Brentford", "BRE"),
    ("Brighton", "BHA"), ("Chelsea", "CHE"), ("Crystal Palace", "CRY"), ("Everton", "EVE"),
    ("Fulham", "FUL"), ("Leeds", "LEE"), ("Liverpool", "LIV"), ("Man City", "MCI"),
    ("Man Utd", "MUN"), ("Newcastle", "NEW"), ("Nott'm Forest", "NFO"), ("Sunderland", "SUN"),
    ("Spurs", "TOT"), ("West Ham", "WHU"), ("Wolves", "WOL"), ("Burnley", "BUR"),
]
POSITIONS = [  # id, short, name, squad_select, min_play, max_play
    (1, "GKP", "Goalkeeper", 2, 1, 1),
    (2, "DEF", "Defender", 5, 3, 5),
    (3, "MID", "Midfielder", 5, 2, 5),
    (4, "FWD", "Forward", 3, 1, 3),
]
# 6 players per club: 1 GK, 2 DEF, 2 MID, 1 FWD
CLUB_TEMPLATE = [1, 2, 2, 3, 3, 4]

CURRENT_GW = 4
NEXT_GW = 5
SEASON_START = datetime(2026, 8, 21, 19, 0, tzinfo=timezone.utc)


def _round_robin(n_teams: int) -> list[list[tuple[int, int]]]:
    """Circle method: 38 rounds of 10 fixtures for 20 teams (home/away mirrored)."""
    teams = list(range(1, n_teams + 1))
    rounds: list[list[tuple[int, int]]] = []
    for r in range(n_teams - 1):
        pairs = []
        for i in range(n_teams // 2):
            h, a = teams[i], teams[n_teams - 1 - i]
            pairs.append((h, a) if (r + i) % 2 == 0 else (a, h))
        rounds.append(pairs)
        teams = [teams[0]] + [teams[-1]] + teams[1:-1]
    return rounds + [[(a, h) for h, a in rnd] for rnd in rounds]



def hermetic_paths(workspace: Path) -> dict[str, str]:
    """Env overrides that point every settings path into ``workspace``.

    Tests must never see the live ``config/`` values: the raw dir, warehouse and reports go to the temp
    workspace, and the squad override points at a file that does not exist, so a real
    ``config/squad_override.toml`` (real player names the synthetic season lacks) cannot leak in.
    """
    return {
        "FPL_PATHS_RAW_DIR": str(workspace / "raw"),
        "FPL_PATHS_DUCKDB_PATH": str(workspace / "fpl.duckdb"),
        "FPL_PATHS_REPORTS_DIR": str(workspace / "reports"),
        "FPL_PATHS_SQUAD_OVERRIDE": str(workspace / "no_squad_override.toml"),
    }

def build_snapshot(out_dir: Path, seed: int = 42, include_history: bool = True,
                   include_entry: bool = True, entry_id: int = 234865) -> Path:
    rng = random.Random(seed)
    snapshot_id = "20260917_100000"
    snap = out_dir / snapshot_id
    snap.mkdir(parents=True, exist_ok=True)

    teams = []
    for i, (name, short) in enumerate(CLUBS, start=1):
        strength = rng.randint(2, 5)
        teams.append({
            "code": 100 + i, "draw": rng.randint(0, 2), "form": None, "id": i, "loss": rng.randint(0, 3),
            "name": name, "played": 0, "points": rng.randint(0, 12), "position": i,  # the real API never fills played
            "short_name": short, "strength": strength, "team_division": None, "unavailable": False,
            "win": rng.randint(0, 4), "strength_overall_home": 1000 + strength * 80,
            "strength_overall_away": 980 + strength * 80, "strength_attack_home": 1000 + strength * 70,
            "strength_attack_away": 990 + strength * 70, "strength_defence_home": 1000 + strength * 60,
            "strength_defence_away": 990 + strength * 60, "pulse_id": i,
        })
    strength_by_team = {t["id"]: t["strength"] for t in teams}

    events = []
    for gw in range(1, 39):
        deadline = SEASON_START + timedelta(days=7 * (gw - 1), hours=-1, minutes=30)
        events.append({
            "id": gw, "name": f"Gameweek {gw}", "deadline_time": deadline.isoformat().replace("+00:00", "Z"),
            "release_time": None, "average_entry_score": 55 if gw <= CURRENT_GW else 0,
            "finished": gw < NEXT_GW, "data_checked": gw < NEXT_GW, "highest_scoring_entry": None,
            "deadline_time_epoch": int(deadline.timestamp()), "deadline_time_game_offset": 0,
            "highest_score": None, "is_previous": gw == CURRENT_GW - 1, "is_current": gw == CURRENT_GW,
            "is_next": gw == NEXT_GW, "cup_leagues_created": False, "h2h_ko_matches_created": False,
            "can_enter": False, "can_manage": False, "released": True, "ranked_count": 0,
            "overrides": {"rules": {}, "scoring": {}, "element_types": [], "pick_multiplier": None},
            "chip_plays": [], "most_selected": None, "most_transferred_in": None, "top_element": None,
            "top_element_info": None, "transfers_made": 0, "most_captained": None, "most_vice_captained": None,
        })

    fixtures = []
    fid = 0
    for gw, rnd in enumerate(_round_robin(20), start=1):
        for h, a in rnd:
            fid += 1
            finished = gw <= CURRENT_GW
            hs, as_ = (rng.randint(0, 4), rng.randint(0, 3)) if finished else (None, None)
            fixtures.append({
                "code": 5000 + fid, "event": gw, "finished": finished, "finished_provisional": finished,
                "id": fid, "kickoff_time": (SEASON_START + timedelta(days=7 * (gw - 1))).isoformat().replace("+00:00", "Z"),
                "minutes": 90 if finished else 0, "provisional_start_time": False, "started": finished,
                "team_a": a, "team_a_score": as_, "team_h": h, "team_h_score": hs,
                "stats": [], "team_h_difficulty": strength_by_team[a], "team_a_difficulty": min(5, strength_by_team[h] + 1),
                "pulse_id": 9000 + fid,
            })

    elements = []
    pid = 0
    for t in teams:
        for pos in CLUB_TEMPLATE:
            pid += 1
            quality = rng.random() * (t["strength"] / 5)
            minutes = rng.choice([0, 45, 180, 270, 360])
            starts = minutes // 90
            games = max(starts, 1 if minutes else 0)
            xg90 = round(quality * (0.6 if pos == 4 else 0.35 if pos == 3 else 0.08 if pos == 2 else 0.0), 2)
            xa90 = round(quality * (0.2 if pos in (3, 4) else 0.1), 2)
            xgc90 = round(1.8 - t["strength"] * 0.2 + rng.random() * 0.4, 2)
            goals = int(round(xg90 * minutes / 90 * (0.7 + rng.random() * 0.8)))
            assists = int(round(xa90 * minutes / 90 * (0.7 + rng.random() * 0.8)))
            cs = rng.randint(0, starts) if pos in (1, 2, 3) and starts else 0
            pts = (2 * games + goals * {1: 10, 2: 6, 3: 5, 4: 4}[pos] + assists * 3
                   + cs * {1: 4, 2: 4, 3: 1, 4: 0}[pos] + rng.randint(0, 3))
            price = {1: 45, 2: 45, 3: 55, 4: 55}[pos] + int(quality * 60)
            status = "a"
            chance = None
            if rng.random() < 0.08:
                status, chance = rng.choice([("d", 50), ("d", 75), ("i", 0), ("s", 0)])
            elements.append({
                "can_transact": True, "can_select": True, "chance_of_playing_next_round": chance,
                "chance_of_playing_this_round": chance, "code": 20000 + pid, "cost_change_event": rng.choice([-1, 0, 0, 1]),
                "cost_change_event_fall": 0, "cost_change_start": rng.choice([-2, -1, 0, 1, 2]), "cost_change_start_fall": 0,
                "price_change_percent": None, "price_change_hourly_rate": None, "price_change_projections": None,
                "price_change_locked_until": None, "price_change_calibrating": False, "dreamteam_count": rng.randint(0, 2),
                "element_type": pos, "ep_next": f"{pts / max(games, 1) * 0.9:.1f}", "ep_this": f"{pts / max(games, 1):.1f}",
                "event_points": rng.randint(0, 12) if minutes else 0, "first_name": f"First{pid}", "form": f"{pts / max(games, 1):.1f}",
                "id": pid, "in_dreamteam": False, "news": "Knock - 75% chance" if chance == 75 else ("Injured" if status == "i" else ""),
                "news_added": "2026-09-14T10:00:00Z" if status != "a" else None, "now_cost": price, "photo": f"{pid}.jpg",
                "points_per_game": f"{pts / max(games, 1):.1f}", "removed": False, "second_name": f"{t['short_name']}{pid}",
                "selected_by_percent": f"{rng.random() * (40 if quality > 0.6 else 8):.1f}", "special": False,
                "squad_number": None, "status": status, "team": t["id"], "team_code": t["code"], "total_points": pts,
                "transfers_in": rng.randint(0, 500000), "transfers_in_event": rng.randint(0, 200000), "transfers_out": rng.randint(0, 500000),
                "transfers_out_event": rng.randint(0, 200000), "value_form": "0.5", "value_season": f"{pts / (price / 10):.1f}",
                "web_name": f"{t['short_name']}-{ {1: 'GK', 2: 'DEF', 3: 'MID', 4: 'FWD'}[pos] }{pid}",
                "known_name": None, "region": None, "team_join_date": None, "birth_date": None, "has_temporary_code": False,
                "opta_code": None, "minutes": minutes, "goals_scored": goals, "assists": assists, "clean_sheets": cs,
                "goals_conceded": rng.randint(0, 8), "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
                "yellow_cards": rng.randint(0, 2), "red_cards": 0, "saves": rng.randint(0, 15) if pos == 1 else 0,
                "bonus": rng.randint(0, 6), "bps": rng.randint(0, 120), "influence": "0.0", "creativity": "0.0", "threat": "0.0",
                "ict_index": f"{rng.random() * 30:.1f}", "clearances_blocks_interceptions": rng.randint(0, 30),
                "recoveries": rng.randint(0, 30), "tackles": rng.randint(0, 15), "defensive_contribution": rng.randint(0, 6),
                "starts": starts, "expected_goals": f"{xg90 * minutes / 90:.2f}", "expected_assists": f"{xa90 * minutes / 90:.2f}",
                "expected_goal_involvements": f"{(xg90 + xa90) * minutes / 90:.2f}", "expected_goals_conceded": f"{xgc90 * minutes / 90:.2f}",
                "corners_and_indirect_freekicks_order": None, "corners_and_indirect_freekicks_text": "",
                "direct_freekicks_order": None, "direct_freekicks_text": "", "penalties_order": 1 if pos == 4 and quality > 0.5 else None,
                "penalties_text": "", "scout_risks": None, "scout_news_link": None,
                "influence_rank": pid, "influence_rank_type": pid, "creativity_rank": pid, "creativity_rank_type": pid,
                "threat_rank": pid, "threat_rank_type": pid, "ict_index_rank": pid, "ict_index_rank_type": pid,
                "expected_goals_per_90": xg90, "saves_per_90": round(rng.random() * 4, 2) if pos == 1 else 0.0,
                "expected_assists_per_90": xa90, "expected_goal_involvements_per_90": round(xg90 + xa90, 2),
                "expected_goals_conceded_per_90": xgc90, "goals_conceded_per_90": xgc90,
                "now_cost_rank": pid, "now_cost_rank_type": pid, "form_rank": pid, "form_rank_type": pid,
                "points_per_game_rank": pid, "points_per_game_rank_type": pid, "selected_rank": pid, "selected_rank_type": pid,
                "starts_per_90": round(starts / max(minutes, 1) * 90, 2), "clean_sheets_per_90": round(cs / max(minutes, 1) * 90, 2),
                "defensive_contribution_per_90": round(rng.random() * 8, 2),
            })

    bootstrap = {
        "chips": [], "events": events, "game_settings": {}, "game_config": {}, "phases": [], "teams": teams,
        "total_players": 11000000, "element_stats": [],
        "element_types": [
            {"id": i, "plural_name": n + "s", "plural_name_short": s, "singular_name": n, "singular_name_short": s,
             "squad_select": ss, "squad_min_select": None, "squad_max_select": None, "squad_min_play": mn,
             "squad_max_play": mx, "ui_shirt_specific": False, "sub_positions_locked": [], "element_count": 0}
            for i, s, n, ss, mn, mx in POSITIONS
        ],
        "elements": elements,
    }
    (snap / "bootstrap_static.json").write_text(json.dumps(bootstrap))
    (snap / "fixtures.json").write_text(json.dumps(fixtures))

    counts = {"players": len(elements), "teams": 20, "events": 38, "fixtures": len(fixtures)}

    if include_history:
        history, past, upcoming = [], [], []
        fixtures_by_team_gw = {}
        for f in fixtures:
            fixtures_by_team_gw.setdefault((f["team_h"], f["event"]), []).append((f, True))
            fixtures_by_team_gw.setdefault((f["team_a"], f["event"]), []).append((f, False))
        for e in elements:
            for gw in range(1, CURRENT_GW + 1):
                for f, home in fixtures_by_team_gw[(e["team"], gw)]:
                    mins = 90 if rng.random() < (e["minutes"] / 360) else 0
                    history.append({
                        "element": e["id"], "fixture": f["id"], "opponent_team": f["team_a"] if home else f["team_h"],
                        "total_points": rng.randint(1, 12) if mins else 0, "was_home": home, "kickoff_time": f["kickoff_time"],
                        "team_h_score": f["team_h_score"], "team_a_score": f["team_a_score"], "round": gw, "modified": False,
                        "minutes": mins, "goals_scored": 0, "assists": 0, "clean_sheets": 0, "goals_conceded": 0, "own_goals": 0,
                        "penalties_saved": 0, "penalties_missed": 0, "yellow_cards": 0, "red_cards": 0, "saves": 0, "bonus": 0,
                        "bps": 0, "influence": "0.0", "creativity": "0.0", "threat": "0.0", "ict_index": "0.0",
                        "clearances_blocks_interceptions": 0, "recoveries": 0, "tackles": 0, "defensive_contribution": 0,
                        "starts": 1 if mins else 0, "expected_goals": "0.10", "expected_assists": "0.05",
                        "expected_goal_involvements": "0.15", "expected_goals_conceded": "1.20", "value": e["now_cost"],
                        "transfers_balance": 0, "selected": 100000, "transfers_in": 0, "transfers_out": 0,
                    })
            past.append({
                "season_name": "2025/26", "element_code": e["code"], "start_cost": e["now_cost"], "end_cost": e["now_cost"],
                "total_points": rng.randint(20, 200), "minutes": rng.randint(500, 3000), "goals_scored": 0, "assists": 0,
                "clean_sheets": 0, "goals_conceded": 0, "own_goals": 0, "penalties_saved": 0, "penalties_missed": 0,
                "yellow_cards": 0, "red_cards": 0, "saves": 0, "bonus": 0, "bps": 0, "influence": "0", "creativity": "0",
                "threat": "0", "ict_index": "0", "clearances_blocks_interceptions": 0, "recoveries": 0, "tackles": 0,
                "defensive_contribution": 0, "starts": 0, "expected_goals": "0", "expected_assists": "0",
                "expected_goal_involvements": "0", "expected_goals_conceded": "0", "element": e["id"],
            })
            for gw in range(NEXT_GW, 39):
                for f, home in fixtures_by_team_gw[(e["team"], gw)]:
                    upcoming.append({
                        "id": f["id"], "code": f["code"], "team_h": f["team_h"], "team_h_score": None, "team_a": f["team_a"],
                        "team_a_score": None, "event": gw, "finished": False, "minutes": 0, "provisional_start_time": False,
                        "kickoff_time": f["kickoff_time"], "event_name": f"Gameweek {gw}", "is_home": home,
                        "difficulty": f["team_h_difficulty"] if home else f["team_a_difficulty"], "element": e["id"],
                    })
        (snap / "player_history.json").write_text(json.dumps(history))
        (snap / "player_history_past.json").write_text(json.dumps(past))
        (snap / "player_fixtures.json").write_text(json.dumps(upcoming))
        counts.update(player_history_rows=len(history), player_history_past_rows=len(past), player_fixture_rows=len(upcoming))

    if include_entry:
        # A legal squad: 2 GK, 5 DEF, 5 MID, 3 FWD, max 3 per club, from the first clubs.
        by_pos = {p: [e for e in elements if e["element_type"] == p and e["status"] == "a"] for p in (1, 2, 3, 4)}
        picks_players = by_pos[1][:2] + by_pos[2][:5] + by_pos[3][:5] + by_pos[4][:3]
        # starters: GK, 3 DEF, 4 MID, 3 FWD -> 11; bench: GK, 2 DEF, 1 MID
        order = [by_pos[1][0]] + by_pos[2][:3] + by_pos[3][:4] + by_pos[4][:3] + [by_pos[1][1]] + by_pos[2][3:5] + [by_pos[3][4]]
        assert len(order) == 15 and {p["id"] for p in order} == {p["id"] for p in picks_players}
        picks = []
        for i, e in enumerate(order, start=1):
            picks.append({"element": e["id"], "position": i, "multiplier": 2 if i == 8 else (0 if i > 11 else 1),
                          "is_captain": i == 8, "is_vice_captain": i == 9, "element_type": e["element_type"]})
        (snap / "entry_picks.json").write_text(json.dumps({
            "active_chip": None, "automatic_subs": [], "event": CURRENT_GW,
            "entry_history": {"event": CURRENT_GW, "points": 80, "total_points": 292, "rank": 2398867, "rank_sort": 2401583,
                              "overall_rank": 1113870, "percentile_rank": 25, "bank": 6, "value": 1012, "event_transfers": 0,
                              "event_transfers_cost": 0, "points_on_bench": 1},
            "picks": picks,
        }))
        (snap / "entry.json").write_text(json.dumps({
            "id": entry_id, "name": "João Mama", "player_first_name": "Angus", "player_last_name": "Macdonald",
            "started_event": 1, "summary_overall_points": 292, "summary_overall_rank": 1113855, "summary_event_points": 80,
            "summary_event_rank": 2398867, "current_event": CURRENT_GW, "last_deadline_bank": 6, "last_deadline_value": 1012,
            "last_deadline_total_transfers": 0, "joined_time": "2026-08-01T10:00:00Z", "favourite_team": 11, "leagues": {},
        }))
        (snap / "entry_history.json").write_text(json.dumps({
            "current": [
                {"event": gw, "points": 60 + gw * 5, "total_points": sum(60 + g * 5 for g in range(1, gw + 1)), "rank": 1000000,
                 "rank_sort": 1000000, "overall_rank": 1100000, "percentile_rank": 25, "bank": 6, "value": 1000 + gw * 3,
                 "event_transfers": 1 if gw == 3 else 0, "event_transfers_cost": 0, "points_on_bench": gw}
                for gw in range(1, CURRENT_GW + 1)
            ],
            "past": [{"season_name": "2025/26", "total_points": 2300, "rank": 250000}],
            "chips": [],
        }))
        (snap / "entry_transfers.json").write_text(json.dumps([
            {"element_in": by_pos[3][0]["id"], "element_in_cost": by_pos[3][0]["now_cost"], "element_out": by_pos[3][5]["id"],
             "element_out_cost": by_pos[3][5]["now_cost"], "entry": entry_id, "event": 3, "time": "2026-09-05T10:00:00Z"}
        ]))
        counts["entry_picks"] = 15

    (snap / "manifest.json").write_text(json.dumps({
        "snapshot_id": snapshot_id, "snapshot_ts": "2026-09-17T10:00:00+00:00",
        "base_url": "https://fantasy.premierleague.com/api", "entry_id": entry_id if include_entry else None,
        "current_gameweek": CURRENT_GW, "next_gameweek": NEXT_GW, "has_player_history": include_history, "counts": counts,
    }))
    return snap
