"""Command line: python -m goalseeker <command>

  run        everything, in order: sync -> results -> settle -> picks -> export (+ weekly backtest)
  sync       download results history, fixtures and prices
  settle     attach results and grade picks
  picks      run the model and write new picks
  export     rebuild the website data
  backtest   re-run the walk-forward backtest now
"""
from __future__ import annotations

import json
import sys
from datetime import timedelta

from . import store
from .config import ConfigError, load_config, load_leagues, now_utc, parse_iso
from .history import load_results, season_code, seasons_back


def _print(rec):
    print(json.dumps(rec, default=str), flush=True)


def _all_recent_results(cfg, leagues, now):
    from .sync import history_divisions
    divs = history_divisions(leagues)
    seasons = seasons_back(season_code(now.date()), 1)
    df = load_results(divs, seasons)
    import pandas as pd
    return df[df["date"] < pd.Timestamp(now.date()) + pd.Timedelta(days=1)]


def cmd_sync(cfg, leagues, now):
    from .sync import sync_fixtures_and_odds, sync_history
    _print(sync_history(cfg, leagues, now))
    _print(sync_fixtures_and_odds(cfg, leagues, now))


def cmd_settle(cfg, leagues, now):
    from .settle import settle_picks
    from .sync import update_results
    res = _all_recent_results(cfg, leagues, now)
    _print(update_results(cfg, leagues, res, now))
    _print(settle_picks(cfg, res, now))


def cmd_picks(cfg, leagues, now):
    from .picks import generate_picks
    _print(generate_picks(cfg, leagues, now))


def cmd_export(cfg, leagues, now):
    from .export import export_site
    _print({"job": "export", **export_site(cfg, leagues, now)})


def cmd_backtest(cfg, leagues, now):
    from .backtest import run_backtest
    out = run_backtest(cfg, leagues, now)
    _print({"job": "backtest", "overall": out["overall"], "calibration": out["calibration_ou25"]})


def backtest_stale(cfg, now) -> bool:
    if not cfg.get("backtest", {}).get("enabled"):
        return False
    from .backtest import VERSION
    bt = store.read_json(store.BACKTEST)
    return not bt or bt.get("version") != VERSION or now - parse_iso(bt["generated_at"]) > timedelta(days=6)


def main(argv=None):
    argv = argv or sys.argv[1:]
    cmd = argv[0] if argv else "run"
    try:
        cfg, leagues = load_config(), load_leagues()
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        return 2
    now = now_utc()
    if cmd == "run":
        cmd_sync(cfg, leagues, now)
        cmd_settle(cfg, leagues, now)
        cmd_picks(cfg, leagues, now)
        if backtest_stale(cfg, now):
            cmd_backtest(cfg, leagues, now)
        cmd_export(cfg, leagues, now)
    elif cmd in ("sync", "settle", "picks", "export", "backtest"):
        globals()[f"cmd_{cmd}"](cfg, leagues, now)
        if cmd != "export":
            cmd_export(cfg, leagues, now)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
