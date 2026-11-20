"""Layer 3: edge and staking. Pure arithmetic on the model's numbers and real prices.

- Fair (implied) probability: 1/odds for each outcome of one bookmaker's market on one line,
  scaled so the outcomes sum to 100%. This strips that bookmaker's margin.
- Edge = model probability - fair probability.
- Expected value (EV) uses the price you would actually get (after exchange commission).
- Stake = fractional Kelly on the actual price, capped, in units.
- Confidence = share of plausible model settings (from the model's uncertainty) in which the bet
  still has positive expected value.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .model import Prediction, market_key

REASONS = {
    "pick": "Qualifies",
    "no_odds": "No bookmaker price from a bettable bookmaker",
    "insufficient_data": "Insufficient data on one or both teams",
    "no_edge": "No edge at the current price",
    "odds_too_short": "Odds too short",
    "prob_below_floor": "Model probability below the floor",
    "edge_too_large": "Edge implausibly large (more likely a model blind spot than a gift)",
    "too_close_to_kickoff": "Too close to kickoff for a new pick",
    "already_picked": "Already picked (picks are never re-made)",
}

MARKET_SELECTIONS = {"ou": ("over", "under"), "1x2": ("home", "draw", "away")}


def devig(prices: dict[str, float]) -> tuple[dict[str, float], float]:
    inv = {k: 1.0 / v for k, v in prices.items()}
    s = sum(inv.values())
    return {k: x / s for k, x in inv.items()}, s - 1.0


def effective_odds(bookmaker: str, price: float, commission: float) -> float:
    if bookmaker == "BFE":
        return 1.0 + (price - 1.0) * (1.0 - commission)
    return price


def kelly_full(p: float, odds: float) -> float:
    b = odds - 1.0
    return (p * odds - 1.0) / b if b > 0 else 0.0


def stake_units(p: float, odds: float, cfg: dict) -> tuple[float, float]:
    """Returns (stake in units, share of bankroll)."""
    k = max(0.0, kelly_full(p, odds)) * cfg["kelly_fraction"]
    share = min(k, cfg["max_stake_pct"])
    if share <= 0:
        return 0.0, 0.0
    step = cfg["stake_step_units"]
    units = np.floor(share * cfg["bankroll_units"] / step) * step
    units = max(units, step)
    return float(round(units, 4)), float(share)


def confidence_tier(prob_positive: float) -> str:
    if prob_positive >= 0.80:
        return "High"
    if prob_positive >= 0.65:
        return "Medium"
    return "Low"


def selection_label(market: str, line, selection: str, home: str = "", away: str = "") -> str:
    if market == "ou":
        return f"{selection.capitalize()} {line}"
    if market == "1x2":
        return {"home": f"{home} win" if home else "Home win", "draw": "Draw",
                "away": f"{away} win" if away else "Away win"}[selection]
    return f"{market} {selection}"


@dataclass
class Candidate:
    market: str
    line: float | None
    selection: str
    bookmaker: str
    price: float
    effective_odds: float
    snapshot_id: str
    fetched_at: str
    p_model: float
    p10: float
    p90: float
    p_fair: float
    p_raw: float
    overround: float
    edge: float
    ev: float
    kelly_full: float
    prob_positive_ev: float
    fail: str | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        for k, v in d.items():
            if isinstance(v, float):
                d[k] = round(v, 5)
        return d


def latest_prices(odds_rows: list[dict]) -> dict[tuple, dict[str, dict]]:
    """Most recent snapshot per (bookmaker, market, line) -> {selection: row}."""
    best: dict[tuple, dict[str, dict]] = {}
    for r in odds_rows:
        key = (r["bookmaker"], r["market"], r["line"])
        sel = r["selection"]
        cur = best.setdefault(key, {}).get(sel)
        if cur is None or r["fetched_at"] > cur["fetched_at"]:
            best[key][sel] = r
    # keep only complete, same-snapshot sets (over+under from the same fetch)
    out = {}
    for key, sels in best.items():
        need = MARKET_SELECTIONS.get(key[1])
        if not need or set(sels) < set(need):
            continue
        batches = {sels[s]["batch_id"] for s in need}
        if len(batches) == 1:
            out[key] = {s: sels[s] for s in need}
    return out


def evaluate(pred: Prediction, prices: dict[tuple, dict[str, dict]], cfg: dict) -> list[Candidate]:
    """Every (bookmaker, market, line, selection) the model can price, best edge first,
    each tagged with the first rule it fails (None = qualifies)."""
    cands = []
    bettable = set(cfg["bettable_bookmakers"])
    for (bk, market, line), sels in prices.items():
        if market not in cfg["markets"] or bk not in bettable:
            continue
        if market == "ou" and (line is None or (line * 2) % 2 != 1):
            continue  # only half-goal lines (no pushes)
        fair, overround = devig({s: r["price"] for s, r in sels.items()})
        for s, r in sels.items():
            key = market_key(market, line if market == "ou" else None, s)
            if key not in pred.markets:
                continue
            p = pred.markets[key]
            eo = effective_odds(bk, r["price"], cfg["betfair_commission"])
            samples = pred.samples[key]
            c = Candidate(
                market=market, line=line if market == "ou" else None, selection=s, bookmaker=bk,
                price=r["price"], effective_odds=eo, snapshot_id=r["snapshot_id"],
                fetched_at=r["fetched_at"], p_model=p, p10=pred.intervals[key][0],
                p90=pred.intervals[key][1], p_fair=fair[s], p_raw=1.0 / r["price"],
                overround=overround, edge=p - fair[s], ev=p * eo - 1.0, kelly_full=kelly_full(p, eo),
                prob_positive_ev=float(np.mean(samples * eo - 1.0 > 0)),
            )
            c.fail = first_failure(c, cfg)
            cands.append(c)
    cands.sort(key=lambda c: c.edge, reverse=True)
    return cands


def first_failure(c: Candidate, cfg: dict) -> str | None:
    if c.edge < cfg["edge_threshold"] or c.ev <= 0:
        return "no_edge"
    if c.edge > cfg["max_edge"]:
        return "edge_too_large"
    if c.effective_odds < cfg["min_odds"]:
        return "odds_too_short"
    if c.p_model < cfg["min_model_probability"]:
        return "prob_below_floor"
    return None


def decide(cands: list[Candidate]) -> tuple[dict[str, Candidate], str]:
    """Best qualifying candidate per market family, and the reason when there is none."""
    chosen: dict[str, Candidate] = {}
    for c in cands:
        if c.fail is None and c.market not in chosen:
            chosen[c.market] = c
    if chosen:
        return chosen, "pick"
    if not cands:
        return {}, "no_odds"
    return {}, cands[0].fail or "no_edge"
