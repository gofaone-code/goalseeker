"""settleResults: grade every pick on a finished (or voided) fixture.

A settlement is a separate, append-only record. The pick itself is never touched, so
the record of what was predicted and what happened can't be rewritten afterwards.
"""
from __future__ import annotations

from . import store
from .config import iso
from .sources.footballdata import SOURCE, extract_prices
from .sync import log_job


def grade(market: str, line, selection: str, h: int, a: int) -> str:
    if market == "ou":
        total = h + a
        if total == line:
            return "void"
        return "won" if (total > line) == (selection == "over") else "lost"
    if market == "1x2":
        res = "home" if h > a else ("draw" if h == a else "away")
        return "won" if res == selection else "lost"
    raise ValueError(f"cannot grade market {market}")


def closing_price(raw: dict, bookmaker: str, market: str, line, selection: str):
    """Closing price from the same bookmaker if published, else the market average."""
    prices = {(bk, mk, ln, sel): p for bk, mk, ln, sel, p in extract_prices(raw, closing=True)}
    for bk in (bookmaker, "Avg"):
        p = prices.get((bk, market, line, selection))
        if p:
            return p, bk
    return None, None


def settle_picks(cfg: dict, results_df, now) -> dict:
    picks = store.read_jsonl(store.PICKS)
    done = {s["pick_id"] for s in store.read_jsonl(store.SETTLEMENTS)}
    fixtures = store.read_json(store.FIXTURES, {})
    by_key = {}
    for row in results_df.itertuples(index=False):
        by_key[(row.div, row.home, row.away, row.date.date().isoformat())] = row
    new = []
    c = {"settled_won": 0, "settled_lost": 0, "settled_void": 0, "awaiting_result": 0, "already_settled": 0}
    for p in picks:
        if p["pick_id"] in done:
            c["already_settled"] += 1
            continue
        fx = fixtures.get(p["fixture"]["fixture_id"])
        if not fx or fx["status"] not in ("finished", "void"):
            c["awaiting_result"] += 1
            continue
        rec = {"pick_id": p["pick_id"], "settled_at": iso(now), "result_source": SOURCE,
               "ft_home": fx.get("ft_home"), "ft_away": fx.get("ft_away"),
               "closing_odds": None, "closing_source": None, "clv": None}
        if fx["status"] == "void":
            rec.update(status="void", profit_units=0.0, level_profit=0.0, note=fx.get("void_reason"))
        else:
            status = grade(p["market"], p["line"], p["selection"], fx["ft_home"], fx["ft_away"])
            eo = p["effective_odds"]
            stake = p["stake_units"]
            profit = {"won": stake * (eo - 1), "lost": -stake, "void": 0.0}[status]
            level = {"won": eo - 1, "lost": -1.0, "void": 0.0}[status]
            rec.update(status=status, profit_units=round(profit, 4), level_profit=round(level, 4))
            row = by_key.get((fx["div"], fx["home"], fx["away"], fx.get("result_date")))
            if row is not None:
                close, src = closing_price(row.raw, p["bookmaker"], p["market"], p["line"], p["selection"])
                if close:
                    rec.update(closing_odds=close, closing_source=src,
                               clv=round(p["odds_at_pick"] / close - 1, 5))
        new.append(rec)
        c[f"settled_{rec['status']}"] += 1
    store.append_jsonl(store.SETTLEMENTS, new)
    return log_job("settle_results", now, "ok", c)
