"""Season codes and loading stored results."""
from __future__ import annotations

from datetime import date

import pandas as pd

from . import store
from .sources.footballdata import normalize_row


def season_code(d: date) -> str:
    """Seasons run July-June. 2026-11-20 -> '2627'."""
    y = d.year if d.month >= 7 else d.year - 1
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def shift_season(code: str, k: int) -> str:
    y = 2000 + int(code[:2]) + k
    return f"{y % 100:02d}{(y + 1) % 100:02d}"


def season_end(code: str) -> date:
    return date(2000 + int(code[:2]) + 1, 7, 1)


def seasons_back(current: str, n_past: int) -> list[str]:
    """Current season plus n_past earlier seasons, oldest first."""
    return [shift_season(current, -k) for k in range(n_past, -1, -1)]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def load_results(divs: list[str], seasons: list[str], finished_only: bool = True) -> pd.DataFrame:
    """All stored results for these divisions and seasons, one row per match."""
    records = []
    for s in seasons:
        for d in divs:
            p = store.history_path(s, d)
            if not p.exists():
                continue
            raw = pd.read_csv(p, dtype=str, keep_default_na=False).to_dict("records")
            for r in raw:
                n = normalize_row(r)
                if n is None:
                    continue
                if finished_only and (n["hg"] is None or n["ag"] is None):
                    continue
                n["season"] = s
                n["hst"] = _num(r.get("HST"))
                n["ast"] = _num(r.get("AST"))
                n["raw"] = r
                records.append(n)
    cols = ["div", "date", "home", "away", "kickoff_utc", "time_known", "hg", "ag", "hst", "ast",
            "season", "raw"]
    if not records:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame.from_records(records)[cols]
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "div", "home"]).reset_index(drop=True)
    return df
