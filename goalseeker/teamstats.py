"""getTeamStats and Layer 2 (context).

Rolling team form from stored results, and short factual context notes for a fixture.
The notes are display-only: they never feed a probability. They are built from the same
stored results as everything else, so they cannot invent an injury or a fixture.
(The spec allowed one LLM call a day for commentary; this version deliberately uses none,
which keeps it free and keeps every sentence traceable to a result in the database.)
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd


def _form_row(team: str, r) -> tuple[int, int, str]:
    if r.home == team:
        gf, ga = int(r.hg), int(r.ag)
    else:
        gf, ga = int(r.ag), int(r.hg)
    res = "W" if gf > ga else ("D" if gf == ga else "L")
    return gf, ga, res


def _block(team: str, rows) -> dict:
    rows = list(rows)
    if not rows:
        return {"played": 0}
    gf = ga = over = btts = 0
    form = ""
    for r in rows:
        f, a, res = _form_row(team, r)
        gf += f
        ga += a
        over += (f + a) > 2.5
        btts += (f > 0 and a > 0)
        form += res
    n = len(rows)
    return {"played": n, "goals_for": gf, "goals_against": ga, "gf_avg": round(gf / n, 2),
            "ga_avg": round(ga / n, 2), "over25": int(over), "btts": int(btts), "form": form}


def team_stats(results: pd.DataFrame, team: str, before: pd.Timestamp, n: int = 10) -> dict:
    played = results[((results["home"] == team) | (results["away"] == team)) & (results["date"] < before)]
    played = played.sort_values("date")
    last = played.tail(n)
    home = played[played["home"] == team].tail(n)
    away = played[played["away"] == team].tail(n)
    out = {
        "team": team,
        "last10": _block(team, last.itertuples(index=False)),
        "home_last10": _block(team, home.itertuples(index=False)),
        "away_last10": _block(team, away.itertuples(index=False)),
        "last_match": None,
    }
    if len(played):
        lr = played.iloc[-1]
        out["last_match"] = {"date": lr["date"].date().isoformat(), "div": lr["div"],
                             "home": lr["home"], "away": lr["away"], "score": f"{int(lr['hg'])}-{int(lr['ag'])}"}
    return out


def context_notes(results: pd.DataFrame, fx: dict, tiers: dict[str, int], season_of) -> list[str]:
    """Plain factual notes. Each one is checkable against the stored results."""
    notes = []
    ko = pd.Timestamp(datetime.fromisoformat(fx["kickoff_utc"].replace("Z", "+00:00")).date())
    current_season = season_of(ko.date())
    for side in ("home", "away"):
        team = fx[side]
        st = team_stats(results, team, ko)
        l10 = st["last10"]
        if l10["played"] >= 5:
            notes.append(f"{team}: {l10['over25']} of last {l10['played']} matches had 3+ goals "
                         f"(scored {l10['gf_avg']}, conceded {l10['ga_avg']} per game).")
        if st["last_match"]:
            rest = (ko - pd.Timestamp(st["last_match"]["date"])).days
            if 0 < rest <= 3:
                notes.append(f"{team} played {rest} day{'s' if rest != 1 else ''} ago (short turnaround).")
        # promoted / relegated: compare last season's division with this fixture's division
        played = results[((results["home"] == team) | (results["away"] == team))]
        prev = played[played["season"] != current_season]
        if len(prev):
            prev_div = prev.sort_values("date").iloc[-1]["div"]
            if prev_div != fx["div"] and prev_div in tiers and fx["div"] in tiers:
                if tiers[prev_div] > tiers[fx["div"]]:
                    notes.append(f"{team} were promoted this season (played in {prev_div} last season).")
                else:
                    notes.append(f"{team} were relegated this season (played in {prev_div} last season).")
    h2h = results[(((results["home"] == fx["home"]) & (results["away"] == fx["away"])) |
                   ((results["home"] == fx["away"]) & (results["away"] == fx["home"]))) &
                  (results["date"] < ko)].sort_values("date")
    if len(h2h):
        r = h2h.iloc[-1]
        notes.append(f"Last meeting: {r['home']} {int(r['hg'])}-{int(r['ag'])} {r['away']} "
                     f"({r['date'].date().isoformat()}).")
    return notes
