"""Walk-forward backtest on past seasons, kept completely separate from the live record.

Each week the model is re-fitted using only results before that week, then priced against the
pre-match odds the source collected before those games (Friday / Tuesday afternoon), using the
exact same edge, threshold and staking rules as live. Results and closing odds settle it.
It answers one question before you risk money: has this model, with these settings, beaten
real prices on matches it had not seen?
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date

import numpy as np
import pandas as pd

from . import store
from .config import iso
from .edge import confidence_tier, decide, evaluate, selection_label, stake_units
from .history import load_results, season_code, seasons_back, shift_season
from .model import fit_group, predict, seed_for
from .record import _round, summarize
from .settle import closing_price, grade
from .sources.footballdata import bookmaker_name, extract_prices
from .sync import fixture_id, log_job


def _prices_from_row(raw: dict) -> dict:
    sets: dict[tuple, dict] = {}
    for bk, mk, ln, sel, p in extract_prices(raw, closing=False):
        sets.setdefault((bk, mk, ln), {})[sel] = {"price": p, "snapshot_id": "backtest", "fetched_at": "",
                                                   "batch_id": "backtest"}
    need = {"ou": {"over", "under"}, "1x2": {"home", "draw", "away"}}
    return {k: v for k, v in sets.items() if k[1] in need and need[k[1]] <= set(v)}


def run_backtest(cfg: dict, leagues: list[dict], now) -> dict:
    bt, m = cfg["backtest"], cfg["model"]
    current = season_code(now.date())
    n_bt = int(bt.get("seasons", 1))
    first = shift_season(current, -n_bt)
    start = pd.Timestamp(date(2000 + int(first[:2]), 7, 1))
    end = pd.Timestamp(now.date())
    step = pd.Timedelta(days=int(bt.get("refit_every_days", 7)))
    active = {lg["code"] for lg in leagues if lg["active"]}
    groups = sorted({lg["group"] for lg in leagues if lg["active"]})
    lg_by = {lg["code"]: lg for lg in leagues}
    recs, calib = [], []
    counts = defaultdict(int)
    for group in groups:
        divs = [lg["code"] for lg in leagues if lg["group"] == group]
        df = load_results(divs, seasons_back(current, int(m["seasons_of_history"]) + n_bt))
        df = df[df["date"] < end]
        warm = None
        t = start
        while t < end:
            try:
                fit = fit_group(df, group, t, m["half_life_days"], m["prior_sd"], m["shots_weight"], warm=warm)
            except ValueError:
                t += step
                continue
            warm = fit
            week = df[(df["date"] >= t) & (df["date"] < t + step) & df["div"].isin(active)]
            for r in week.itertuples(index=False):
                counts["fixtures"] += 1
                if r.home not in fit.tidx or r.away not in fit.tidx or r.div not in fit.didx or \
                        min(fit.team_recent_matches.get(r.home, 0),
                            fit.team_recent_matches.get(r.away, 0)) < cfg["min_team_matches"]:
                    counts["insufficient_data"] += 1
                    continue
                prices = _prices_from_row(r.raw)
                if not prices:
                    counts["no_odds"] += 1
                    continue
                fid = fixture_id(r.div, r.date.date().isoformat(), r.home, r.away)
                pred = predict(fit, r.div, r.home, r.away, int(bt.get("draws", 200)), m["max_goals"], seed_for(fid))
                y_over = (r.hg + r.ag) > 2.5
                for bk in ("B365", "Avg", "Max"):
                    s = prices.get((bk, "ou", 2.5))
                    if s:
                        io, iu = 1 / s["over"]["price"], 1 / s["under"]["price"]
                        calib.append((pred.markets["ou_2.5_over"], io / (io + iu), y_over))
                        break
                cands = evaluate(pred, prices, cfg)
                chosen, reason = decide(cands)
                counts[f"reason_{reason}"] += 1
                for market, c in chosen.items():
                    status = grade(c.market, c.line, c.selection, int(r.hg), int(r.ag))
                    units, _ = stake_units(c.p_model, c.effective_odds, cfg)
                    eo = c.effective_odds
                    close, src = closing_price(r.raw, c.bookmaker, c.market, c.line, c.selection)
                    recs.append({
                        "pick_id": f"bt-{fid}-{market}", "fixture": {"kickoff_utc": r.kickoff_utc,
                                                                    "league": lg_by[r.div]["name"],
                                                                    "country": lg_by[r.div]["country"]},
                        "market": c.market, "label": selection_label(c.market, c.line, c.selection),
                        "status": status, "edge": c.edge, "odds_at_pick": c.price, "model_probability": c.p_model,
                        "stake_units": units, "confidence": confidence_tier(c.prob_positive_ev),
                        "bookmaker_name": bookmaker_name(c.bookmaker),
                        "level_profit": {"won": eo - 1, "lost": -1.0, "void": 0.0}[status],
                        "profit_units": {"won": units * (eo - 1), "lost": -units, "void": 0.0}[status],
                        "clv": (c.price / close - 1) if close else None,
                    })
            t += step
    graded = [r for r in recs if r["status"] in ("won", "lost")]

    def by(keyf, min_n):
        g = defaultdict(list)
        for r in graded:
            g[keyf(r)].append(r)
        out = []
        for k, rs in sorted(g.items(), key=lambda kv: -len(kv[1])):
            out.append({"key": k, "n": len(rs), "hidden": True} if len(rs) < min_n
                       else {"key": k, "hidden": False, **summarize(rs)})
        return out

    def edge_bucket(r):
        e = r["edge"]
        for lo, hi in ((0.04, 0.06), (0.06, 0.08), (0.08, 0.10), (0.10, 0.15)):
            if lo <= e < hi:
                return f"{int(lo*100)}-{int(hi*100)}%"
        return "other"

    cal = {}
    if calib:
        pm = np.clip(np.array([c[0] for c in calib]), 1e-6, 1 - 1e-6)
        pk = np.clip(np.array([c[1] for c in calib]), 1e-6, 1 - 1e-6)
        y = np.array([c[2] for c in calib], dtype=float)
        cal = {"n": len(calib),
               "model_log_loss": float(-np.mean(y * np.log(pm) + (1 - y) * np.log(1 - pm))),
               "market_log_loss": float(-np.mean(y * np.log(pk) + (1 - y) * np.log(1 - pk))),
               "coin_flip_log_loss": math.log(2),
               "model_brier": float(np.mean((pm - y) ** 2)), "market_brier": float(np.mean((pk - y) ** 2)),
               "actual_over_rate": float(y.mean())}
    series, cum = [], 0.0
    for r in sorted(graded, key=lambda r: r["fixture"]["kickoff_utc"]):
        cum += r["level_profit"]
        series.append({"date": r["fixture"]["kickoff_utc"][:10], "level": cum})
    # weekly thinning keeps the file small
    thin, seen = [], set()
    for s in series:
        wk = pd.Timestamp(s["date"]).strftime("%G-%V")
        if wk in seen:
            thin[-1] = s
        else:
            seen.add(wk)
            thin.append(s)
    min_n = cfg["min_sample_for_breakdown"]
    out = _round({
        "generated_at": iso(now), "period": {"from": str(start.date()), "to": str(end.date())},
        "model_version": m["version"],
        "params": {k: m[k] for k in ("half_life_days", "prior_sd", "shots_weight")},
        "rules": {k: cfg[k] for k in ("edge_threshold", "min_model_probability", "min_odds", "max_edge",
                                       "kelly_fraction", "max_stake_pct", "bankroll_units",
                                       "bettable_bookmakers", "markets")},
        "counts": dict(counts), "overall": summarize(graded), "voids": len(recs) - len(graded),
        "breakdowns": {"league": by(lambda r: f"{r['fixture']['country']} - {r['fixture']['league']}", min_n),
                       "selection": by(lambda r: r["label"], min_n),
                       "edge": by(edge_bucket, min_n),
                       "confidence": by(lambda r: r["confidence"], min_n)},
        "calibration_ou25": cal, "series": thin,
    })
    store.write_json(store.BACKTEST, out)
    log_job("backtest", now, "ok", {"picks": len(recs), "fixtures": counts["fixtures"]})
    return out
