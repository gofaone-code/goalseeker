"""The honest scoreboard, computed only from Pick + Settlement records.

Every figure carries its sample size. Breakdowns below the minimum sample are hidden,
not shown with a misleading number. Losing streaks and drawdown are shown alongside ROI.
"""
from __future__ import annotations

from collections import defaultdict


def summarize(rows: list[dict]) -> dict:
    """rows: merged pick+settlement dicts with status won/lost (voids excluded)."""
    n = len(rows)
    if n == 0:
        return {"n": 0}
    rows = sorted(rows, key=lambda r: (r["fixture"]["kickoff_utc"], r["pick_id"]))
    wins = sum(r["status"] == "won" for r in rows)
    level = sum(r["level_profit"] for r in rows)
    staked = sum(r["stake_units"] for r in rows)
    kelly = sum(r["profit_units"] for r in rows)
    clv = [r["clv"] for r in rows if r.get("clv") is not None]
    streak = worst = 0
    cum = peak = dd = 0.0
    for r in rows:
        streak = streak + 1 if r["status"] == "lost" else 0
        worst = max(worst, streak)
        cum += r["level_profit"]
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return {
        "n": n, "wins": wins, "losses": n - wins, "hit_rate": wins / n,
        "expected_wins": sum(r["model_probability"] for r in rows),
        "level_profit_units": level, "roi_level": level / n,
        "staked_units": staked, "kelly_profit_units": kelly, "roi_kelly": kelly / staked if staked else None,
        "avg_edge": sum(r["edge"] for r in rows) / n, "avg_odds": sum(r["odds_at_pick"] for r in rows) / n,
        "clv_n": len(clv), "avg_clv": sum(clv) / len(clv) if clv else None,
        "clv_beat_close_share": sum(c > 0 for c in clv) / len(clv) if clv else None,
        "longest_losing_streak": worst, "max_drawdown_level_units": dd,
    }


def merge(picks: list[dict], settlements: list[dict]) -> list[dict]:
    by_id = {s["pick_id"]: s for s in settlements}
    out = []
    for p in picks:
        s = by_id.get(p["pick_id"])
        m = dict(p)
        if s:
            m.update({k: v for k, v in s.items() if k != "pick_id"})
        else:
            m["status"] = "pending"
        out.append(m)
    return out


def _round(d):
    if isinstance(d, dict):
        return {k: _round(v) for k, v in d.items()}
    if isinstance(d, list):
        return [_round(v) for v in d]
    if isinstance(d, float):
        return round(d, 5)
    return d


def build_record(merged: list[dict], min_sample: int) -> dict:
    graded = [m for m in merged if m["status"] in ("won", "lost")]
    breakdowns = {}
    for name, keyf in (("league", lambda m: f"{m['fixture']['country']} - {m['fixture']['league']}"),
                       ("selection", lambda m: m["label"] if m["market"] == "ou" else m["market"].upper()),
                       ("confidence", lambda m: m["confidence"]),
                       ("bookmaker", lambda m: m["bookmaker_name"])):
        groups = defaultdict(list)
        for m in graded:
            groups[keyf(m)].append(m)
        rows = []
        for k, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            if len(rs) < min_sample:
                rows.append({"key": k, "n": len(rs), "hidden": True})
            else:
                rows.append({"key": k, "hidden": False, **summarize(rs)})
        breakdowns[name] = rows
    series = []
    cum_level = cum_kelly = 0.0
    for m in sorted(graded, key=lambda r: (r["fixture"]["kickoff_utc"], r["pick_id"])):
        cum_level += m["level_profit"]
        cum_kelly += m["profit_units"]
        series.append({"date": m["fixture"]["kickoff_utc"][:10], "level": cum_level, "kelly": cum_kelly})
    counts = {
        "picks_total": len(merged),
        "pending": sum(m["status"] == "pending" for m in merged),
        "won": sum(m["status"] == "won" for m in merged),
        "lost": sum(m["status"] == "lost" for m in merged),
        "void": sum(m["status"] == "void" for m in merged),
    }
    counts["reconciles"] = counts["picks_total"] == counts["pending"] + counts["won"] + counts["lost"] + counts["void"]
    return _round({"overall": summarize(graded), "counts": counts, "breakdowns": breakdowns,
                   "series": series, "min_sample": min_sample})
