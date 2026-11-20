"""generatePicks: score every fixture in the window, write immutable Pick records.

Rules that keep the record honest:
- A pick is written once, the first time its selection qualifies, and never edited.
- At most one pick per fixture per market family (no Over 1.5 + Over 2.5 on the same game).
- No new picks within pick_cutoff_minutes of kickoff.
- Every pick stores the odds snapshot, model numbers, model run and settings that produced it.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

import numpy as np
import pandas as pd

from . import store
from .config import config_hash, iso, parse_iso
from .edge import (REASONS, confidence_tier, decide, evaluate, latest_prices, selection_label,
                   stake_units)
from .history import load_results, season_code, seasons_back
from .model import GroupFit, data_hash, fit_group, predict, seed_for
from .sources.footballdata import bookmaker_name
from .sync import log_job
from .teamstats import context_notes, team_stats


def _r(x, n=5):
    return None if x is None else round(float(x), n)


# ------------------------------------------------------------- fit cache

def _cache_path(group: str, key: str):
    return store.CACHE / f"fit-{group}-{key}.npz"


def _save_fit(fit: GroupFit, key: str):
    store.CACHE.mkdir(parents=True, exist_ok=True)
    meta = {k: getattr(fit, k) for k in ("group", "asof", "teams", "divisions", "rho", "n_matches",
                                         "team_recent_matches", "team_division", "converged",
                                         "data_hash", "params", "seconds")}
    np.savez_compressed(_cache_path(fit.group, key), theta=fit.theta, cov=fit.cov,
                        meta=np.array(json.dumps(meta)))


def _load_fit(group: str, key: str) -> GroupFit | None:
    p = _cache_path(group, key)
    if not p.exists():
        return None
    try:
        z = np.load(p, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        return GroupFit(theta=z["theta"], cov=z["cov"], **meta)
    except Exception:
        return None


def load_group_results(cfg, leagues, group: str, now, extra_seasons: int = 0) -> pd.DataFrame:
    divs = [lg["code"] for lg in leagues if lg["group"] == group]
    seasons = seasons_back(season_code(now.date()), int(cfg["model"]["seasons_of_history"]) + extra_seasons)
    df = load_results(divs, seasons)
    return df[df["date"] < pd.Timestamp(now.date())]


def fit_for_group(cfg, leagues, group: str, now) -> tuple[GroupFit | None, pd.DataFrame, bool, str | None]:
    """(fit, results, reused_from_cache, error). The fit uses every result up to the latest one,
    so it only changes when new results arrive."""
    df = load_group_results(cfg, leagues, group, now)
    if len(df) < 30:
        return None, df, False, f"only {len(df)} stored results for {group}"
    m = cfg["model"]
    asof = df["date"].max() + pd.Timedelta(days=1)
    key = hashlib.sha1(f"{data_hash(df)}|{m['version']}|{m['half_life_days']}|{m['prior_sd']}|"
                       f"{m['shots_weight']}|{asof.date()}".encode()).hexdigest()[:12]
    fit = _load_fit(group, key)
    if fit is not None:
        return fit, df, True, None
    try:
        fit = fit_group(df, group, asof, m["half_life_days"], m["prior_sd"], m["shots_weight"])
    except Exception as e:  # recorded, never hidden
        return None, df, False, f"{group}: model fit failed: {e}"
    _save_fit(fit, key)
    return fit, df, False, None


# --------------------------------------------------------------- picks

def pick_id(fid: str, market: str, line, selection: str) -> str:
    return hashlib.sha1(f"{fid}|{market}|{line}|{selection}".encode()).hexdigest()[:16]


def generate_picks(cfg: dict, leagues: list[dict], now) -> dict:
    by_code = {lg["code"]: lg for lg in leagues}
    tiers = {lg["code"]: lg["tier"] for lg in leagues}
    fixtures = store.read_json(store.FIXTURES, {})
    horizon = now + timedelta(days=cfg["window_days"])
    window = [fx for fx in fixtures.values()
              if fx["status"] == "scheduled" and now - timedelta(hours=2) <= parse_iso(fx["kickoff_utc"]) <= horizon
              and by_code.get(fx["div"], {}).get("active")]
    window.sort(key=lambda f: f["kickoff_utc"])
    odds = store.load_odds({fx["fixture_id"] for fx in window})
    odds_by_fx: dict[str, list] = {}
    for r in odds:
        odds_by_fx.setdefault(r["fixture_id"], []).append(r)

    existing = store.read_jsonl(store.PICKS)
    picked = {(p["fixture"]["fixture_id"], p["market"]): p for p in existing}

    run_id = "run-" + now.strftime("%Y%m%dT%H%M%SZ")
    m = cfg["model"]
    fits, results, group_info = {}, {}, {}
    for group in sorted({by_code[fx["div"]]["group"] for fx in window}):
        fit, df, reused, err = fit_for_group(cfg, leagues, group, now)
        fits[group], results[group] = fit, df
        group_info[group] = ({**fit.summary(), "reused_from_cache": reused} if fit else {"error": err})

    board, new_picks, reasons, team_cache = [], [], {}, {}
    for fx in window:
        fid = fx["fixture_id"]
        group = by_code[fx["div"]]["group"]
        fit = fits.get(group)
        ko = parse_iso(fx["kickoff_utc"])
        entry = {"fixture": {k: fx[k] for k in ("fixture_id", "div", "league", "country", "home", "away",
                                                 "kickoff_utc", "time_known", "source")},
                 "listed_at_source_until": fx.get("last_seen"),
                 "model": None, "prices": [], "candidates": [], "pick_ids": [], "context": []}
        prices = latest_prices(odds_by_fx.get(fid, []))
        entry["prices"] = [{"bookmaker": bk, "bookmaker_name": bookmaker_name(bk), "market": mk, "line": ln,
                            "fetched_at": max(r["fetched_at"] for r in sels.values()),
                            "prices": {s: r["price"] for s, r in sels.items()}}
                           for (bk, mk, ln), sels in sorted(prices.items(), key=lambda kv: str(kv[0]))]
        reason = None
        pred = None
        if fit is None:
            reason = "insufficient_data"
            entry["reason_detail"] = group_info[group].get("error")
        else:
            thin = [t for t in (fx["home"], fx["away"])
                    if fit.team_recent_matches.get(t, 0) < cfg["min_team_matches"]]
            if thin or fx["div"] not in fit.didx:
                reason = "insufficient_data"
                entry["reason_detail"] = (f"fewer than {cfg['min_team_matches']} matches in the last 400 days: "
                                          + ", ".join(thin)) if thin else f"no stored results for {fx['div']}"
            else:
                pred = predict(fit, fx["div"], fx["home"], fx["away"], m["uncertainty_draws"],
                               m["max_goals"], seed_for(fid))
                entry["model"] = pred.public()
        if pred is not None:
            cands = evaluate(pred, prices, cfg)
            chosen, reason = decide(cands)
            entry["candidates"] = [c.to_dict() for c in cands[:6]]
            for market, c in chosen.items():
                prior = picked.get((fid, market))
                if prior:
                    reason = "already_picked"
                    continue
                if ko - now < timedelta(minutes=cfg["pick_cutoff_minutes"]):
                    reason = "too_close_to_kickoff"
                    continue
                units, share = stake_units(c.p_model, c.effective_odds, cfg)
                pk = {
                    "pick_id": pick_id(fid, c.market, c.line, c.selection), "created_at": iso(now),
                    "fixture": entry["fixture"], "market": c.market, "line": c.line, "selection": c.selection,
                    "label": selection_label(c.market, c.line, c.selection, fx["home"], fx["away"]),
                    "bookmaker": c.bookmaker, "bookmaker_name": bookmaker_name(c.bookmaker),
                    "odds_at_pick": c.price, "effective_odds": _r(c.effective_odds, 4),
                    "odds_snapshot_id": c.snapshot_id, "odds_fetched_at": c.fetched_at,
                    "implied_probability": _r(c.p_fair), "raw_implied_probability": _r(c.p_raw),
                    "overround": _r(c.overround), "model_probability": _r(c.p_model),
                    "model_p10": _r(c.p10), "model_p90": _r(c.p90), "edge": _r(c.edge), "ev": _r(c.ev),
                    "kelly_full": _r(c.kelly_full), "stake_units": units, "stake_share": _r(share),
                    "confidence": confidence_tier(c.prob_positive_ev), "prob_positive_ev": _r(c.prob_positive_ev, 3),
                    "xg_home": _r(pred.lam_home, 3), "xg_away": _r(pred.lam_away, 3),
                    "total_goals": [_r(x, 4) for x in pred.total_goals],
                    "model_run_id": run_id, "model_version": m["version"],
                    "model_params": {k: m[k] for k in ("half_life_days", "prior_sd", "shots_weight")},
                    "fit_asof": fit.asof, "fit_data_hash": fit.data_hash, "config_hash": config_hash(cfg),
                    "thresholds": {k: cfg[k] for k in ("edge_threshold", "min_model_probability", "min_odds",
                                                        "max_edge", "kelly_fraction", "max_stake_pct",
                                                        "bankroll_units", "betfair_commission")},
                }
                new_picks.append(pk)
                picked[(fid, market)] = pk
                reason = "pick"
        # picks for this fixture (made now or in an earlier run)
        entry["pick_ids"] = [p["pick_id"] for (f, _), p in picked.items() if f == fid]
        if entry["pick_ids"] and reason != "pick":
            reason = "already_picked"
        entry["decision"] = "pick" if entry["pick_ids"] else "no_pick"
        entry["reason"] = reason
        entry["reason_text"] = REASONS.get(reason, reason)
        reasons[reason] = reasons.get(reason, 0) + 1
        # context + team stats (display only)
        df = results.get(group)
        if df is not None and len(df):
            entry["context"] = context_notes(df, fx, tiers, season_code)
            for t in (fx["home"], fx["away"]):
                if t not in team_cache:
                    team_cache[t] = team_stats(df, t, pd.Timestamp(ko.date()))
        board.append(entry)

    store.append_jsonl(store.PICKS, new_picks)
    store.write_json(store.BOARD, {"generated_at": iso(now), "run_id": run_id, "fixtures": board}, compact=True)
    store.write_json(store.TEAM_STATS, {"generated_at": iso(now), "teams": team_cache}, compact=True)
    run = {"run_id": run_id, "at": iso(now), "model_version": m["version"],
           "params": {k: m[k] for k in ("half_life_days", "prior_sd", "shots_weight", "uncertainty_draws")},
           "config_hash": config_hash(cfg), "groups": group_info, "fixtures_processed": len(window),
           "fixtures_with_prediction": sum(1 for e in board if e["model"]), "picks_created": len(new_picks),
           "reasons": reasons}
    store.append_jsonl(store.MODEL_RUNS, [run])
    counts = {"fixtures_processed": len(window), "picks_created": len(new_picks),
              "fits_computed": sum(1 for g in group_info.values() if g.get("reused_from_cache") is False),
              "fits_reused": sum(1 for g in group_info.values() if g.get("reused_from_cache")),
              "fit_errors": sum(1 for g in group_info.values() if "error" in g), **{f"reason_{k}": v for k, v in reasons.items()}}
    return log_job("generate_picks", now, "ok" if not counts["fit_errors"] else "partial", counts)
