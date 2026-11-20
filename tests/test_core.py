"""Core checks. Run with:  python -m pytest -q   (or: python tests/test_core.py)"""
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from goalseeker.edge import (Candidate, decide, devig, effective_odds, evaluate, kelly_full,  # noqa: E402
                             latest_prices, stake_units)
from goalseeker.history import season_code, seasons_back  # noqa: E402
from goalseeker.model import fit_group, predict  # noqa: E402
from goalseeker.record import build_record, merge  # noqa: E402
from goalseeker.settle import grade  # noqa: E402
from goalseeker.sources.footballdata import extract_prices, normalize_row, parse_price_column  # noqa: E402

CFG = {"bankroll_units": 100, "kelly_fraction": 0.25, "max_stake_pct": 0.03, "stake_step_units": 0.25,
       "edge_threshold": 0.04, "min_model_probability": 0.5, "min_odds": 1.3, "max_edge": 0.15,
       "markets": ["ou"], "bettable_bookmakers": ["B365", "BFE"], "betfair_commission": 0.05}


def test_price_columns():
    assert parse_price_column("B365>2.5") == ("B365", "ou", 2.5, "over", False)
    assert parse_price_column("B365C<2.5") == ("B365", "ou", 2.5, "under", True)
    assert parse_price_column("BFEC>2.5") == ("BFE", "ou", 2.5, "over", True)
    assert parse_price_column("BFDH") == ("BFD", "1x2", None, "home", False)      # Betfred home
    assert parse_price_column("BFD") == ("BF", "1x2", None, "draw", False)        # old Betfair draw
    assert parse_price_column("PSCH") == ("PS", "1x2", None, "home", True)
    assert parse_price_column("PC>2.5") == ("PS", "ou", 2.5, "over", True)
    assert parse_price_column("PPH") == ("PP", "1x2", None, "home", False)
    for col in ("FTHG", "HTAG", "Referee", "AHh", "MaxCAHH", "B365AHH", "HST", "Div"):
        assert parse_price_column(col) is None, col


def test_uk_time_to_utc():
    summer = normalize_row({"Div": "E0", "Date": "22/08/2026", "Time": "15:00", "HomeTeam": "A", "AwayTeam": "B"})
    winter = normalize_row({"Div": "E0", "Date": "21/11/2026", "Time": "15:00", "HomeTeam": "A", "AwayTeam": "B"})
    assert summer["kickoff_utc"] == "2026-08-22T14:00:00Z"   # BST
    assert winter["kickoff_utc"] == "2026-11-21T15:00:00Z"   # GMT
    assert normalize_row({"Div": "", "Date": "x"}) is None


def test_extract_prices_skips_blanks():
    row = {"B365>2.5": "1.90", "B365<2.5": "", "Max>2.5": "abc", "FTHG": "2"}
    assert extract_prices(row) == [("B365", "ou", 2.5, "over", 1.9)]


def test_devig_and_kelly():
    fair, margin = devig({"over": 1.9, "under": 1.9})
    assert abs(fair["over"] - 0.5) < 1e-12 and abs(margin - (2 / 1.9 - 1)) < 1e-12
    assert abs(kelly_full(0.55, 2.0) - 0.10) < 1e-12
    assert kelly_full(0.4, 2.0) < 0
    assert abs(effective_odds("BFE", 2.0, 0.05) - 1.95) < 1e-12
    assert effective_odds("B365", 2.0, 0.05) == 2.0


def test_stake_cap_and_rounding():
    units, share = stake_units(0.70, 2.0, CFG)        # full Kelly 40% -> quarter 10% -> cap 3%
    assert share == 0.03 and units == 3.0
    units, _ = stake_units(0.52, 2.0, CFG)            # full 4% -> quarter 1% -> 1 unit
    assert units == 1.0
    assert stake_units(0.45, 2.0, CFG) == (0.0, 0.0)


def test_grade():
    assert grade("ou", 2.5, "over", 2, 1) == "won"
    assert grade("ou", 2.5, "under", 2, 1) == "lost"
    assert grade("ou", 2.5, "under", 1, 1) == "won"
    assert grade("ou", 2.0, "over", 1, 1) == "void"
    assert grade("1x2", None, "draw", 0, 0) == "won"


def test_latest_prices_needs_complete_same_batch():
    rows = [
        {"bookmaker": "B365", "market": "ou", "line": 2.5, "selection": "over", "price": 1.9, "fetched_at": "2026-01-01T10:00:00Z", "batch_id": "a", "snapshot_id": "1"},
        {"bookmaker": "B365", "market": "ou", "line": 2.5, "selection": "under", "price": 1.9, "fetched_at": "2026-01-01T10:00:00Z", "batch_id": "a", "snapshot_id": "2"},
        {"bookmaker": "B365", "market": "ou", "line": 2.5, "selection": "over", "price": 2.0, "fetched_at": "2026-01-02T10:00:00Z", "batch_id": "b", "snapshot_id": "3"},
    ]
    # newest 'over' is from batch b but 'under' only from batch a -> not a coherent pair -> excluded
    assert latest_prices(rows) == {}
    rows.append({"bookmaker": "B365", "market": "ou", "line": 2.5, "selection": "under", "price": 1.8, "fetched_at": "2026-01-02T10:00:00Z", "batch_id": "b", "snapshot_id": "4"})
    lp = latest_prices(rows)
    assert lp[("B365", "ou", 2.5)]["over"]["price"] == 2.0


class _Pred:
    def __init__(self, p_over):
        self.markets = {"ou_2.5_over": p_over, "ou_2.5_under": 1 - p_over}
        self.intervals = {k: (v - .05, v + .05) for k, v in self.markets.items()}
        self.samples = {k: np.full(100, v) for k, v in self.markets.items()}


def _prices(over, under, bk="B365"):
    mk = lambda s, p: {"price": p, "snapshot_id": s, "fetched_at": "t", "batch_id": "x"}
    return {(bk, "ou", 2.5): {"over": mk("o", over), "under": mk("u", under)}}


def test_rules():
    chosen, reason = decide(evaluate(_Pred(0.60), _prices(1.95, 1.95), CFG))   # edge 10 pts
    assert reason == "pick" and chosen["ou"].selection == "over"
    _, reason = decide(evaluate(_Pred(0.52), _prices(1.95, 1.95), CFG))        # edge 2 pts
    assert reason == "no_edge"
    _, reason = decide(evaluate(_Pred(0.80), _prices(1.95, 1.95), CFG))        # edge 30 pts
    assert reason == "edge_too_large"
    _, reason = decide(evaluate(_Pred(0.85), _prices(1.25, 4.0), CFG))         # short price
    assert reason == "odds_too_short"
    _, reason = decide(evaluate(_Pred(0.45), _prices(2.6, 1.5), CFG))          # over 45% < floor
    assert reason == "prob_below_floor"
    _, reason = decide(evaluate(_Pred(0.60), _prices(1.95, 1.95, bk="Avg"), CFG))
    assert reason == "no_odds"                                                 # not a bettable book


def test_model_learns_strength():
    rng = np.random.default_rng(0)
    teams = [f"T{i}" for i in range(8)]
    strength = {t: (i - 3.5) * 0.15 for i, t in enumerate(teams)}
    rows = []
    d0 = pd.Timestamp("2025-08-01")
    for k in range(4):
        for h in teams:
            for a in teams:
                if h == a:
                    continue
                lh = np.exp(0.25 + 0.2 + strength[h] - strength[a])
                la = np.exp(0.25 + strength[a] - strength[h])
                rows.append({"div": "X1", "date": d0 + pd.Timedelta(days=7 * k + rng.integers(0, 6)), "home": h, "away": a,
                             "hg": rng.poisson(lh), "ag": rng.poisson(la), "hst": np.nan, "ast": np.nan})
    df = pd.DataFrame(rows)
    fit = fit_group(df, "X", pd.Timestamp("2025-12-01"), 365, 0.3)
    att = dict(zip(fit.teams, fit.theta[:fit.T]))
    assert att["T7"] > att["T0"]
    p = predict(fit, "X1", "T7", "T0", 300, 10, 1)
    assert p.markets["1x2_home"] > p.markets["1x2_away"]
    assert abs(sum(p.total_goals) - 1) < 1e-6
    assert p.intervals["ou_2.5_over"][0] <= p.markets["ou_2.5_over"] <= p.intervals["ou_2.5_over"][1]


def test_record_reconciles_and_hides_small_samples():
    base = {"fixture": {"kickoff_utc": "2026-01-01T15:00:00Z", "league": "L", "country": "C"}, "market": "ou",
            "label": "Over 2.5", "confidence": "Low", "bookmaker_name": "Bet365", "edge": 0.05,
            "odds_at_pick": 2.0, "model_probability": 0.55, "stake_units": 1.0}
    picks = [dict(base, pick_id=str(i)) for i in range(5)]
    setts = [{"pick_id": "0", "status": "won", "profit_units": 1.0, "level_profit": 1.0, "clv": None},
             {"pick_id": "1", "status": "lost", "profit_units": -1.0, "level_profit": -1.0, "clv": None},
             {"pick_id": "2", "status": "void", "profit_units": 0.0, "level_profit": 0.0, "clv": None}]
    rec = build_record(merge(picks, setts), min_sample=20)
    assert rec["counts"] == {"picks_total": 5, "pending": 2, "won": 1, "lost": 1, "void": 1, "reconciles": True}
    assert rec["overall"]["n"] == 2 and rec["overall"]["roi_level"] == 0
    assert rec["breakdowns"]["league"][0]["hidden"] is True


def test_seasons():
    from datetime import date
    assert season_code(date(2026, 11, 20)) == "2627"
    assert season_code(date(2026, 5, 1)) == "2526"
    assert seasons_back("2627", 2) == ["2425", "2526", "2627"]


if __name__ == "__main__":
    fails = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("ok  ", name)
            except Exception as e:  # noqa: BLE001
                fails += 1
                print("FAIL", name, repr(e))
    sys.exit(1 if fails else 0)
