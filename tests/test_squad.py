from fpl_analysis.squad import estimate_free_transfers, picks_summary


def _gws(transfers_by_gw: dict[int, int], upto: int):
    return [{"event": gw, "event_transfers": transfers_by_gw.get(gw, 0)} for gw in range(1, upto + 1)]


def test_no_transfers_banks_one_per_week_capped_at_five():
    # joined GW1, never transferred: GW2=1, GW3=2, GW4=3, GW5=4 ... GW7=5 (cap)
    assert estimate_free_transfers(_gws({}, 4), [], 1, 5) == 4
    assert estimate_free_transfers(_gws({}, 8), [], 1, 9) == 5


def test_transfers_consume_bank():
    # GW3: used 1 of 2 -> 1 left, +1 = 2 for GW4, +1 = 3 for GW5
    assert estimate_free_transfers(_gws({3: 1}, 4), [], 1, 5) == 3


def test_hits_do_not_go_negative():
    # GW2: 1 FT, made 3 transfers (2 hits) -> 0 left, +1 = 1 for GW3
    assert estimate_free_transfers(_gws({2: 3}, 2), [], 1, 3) == 1


def test_wildcard_week_is_neutral():
    # GW3 wildcard with 11 transfers: bank untouched (2) -> 3 for GW4
    chips = [{"name": "wildcard", "event": 3}]
    assert estimate_free_transfers(_gws({3: 11}, 3), chips, 1, 4) == 3


def test_picks_summary():
    picks = [{"element": i, "position": i, "is_captain": i == 8, "is_vice_captain": i == 9} for i in range(1, 16)]
    s = picks_summary(picks)
    assert s["starter_ids"] == list(range(1, 12))
    assert s["bench_ids"] == [12, 13, 14, 15]
    assert (s["captain_id"], s["vice_captain_id"]) == (8, 9)
