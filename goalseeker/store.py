"""Plain-file storage, committed to git so every number has an audit trail.

Append-only (never rewritten):
  data/picks.jsonl         one line per pick, written once
  data/settlements.jsonl   one line per graded pick, written once
  data/odds/YYYY-MM.csv    every bookmaker price snapshot
  data/model_runs.jsonl    every model run with its parameters
  data/fetch_log.jsonl     every request to the data source and how it went
  data/job_log.jsonl       created / updated / skipped counts for every job

Rewritten (source data, upserted):
  data/fixtures.json       fixture table, upserted by id
  data/history/<season>/<div>.csv   results as published by the source
  data/backtest.json       latest weekly backtest

Rebuilt every run and not committed (data/derived/, data/cache/, site/data/):
  board.json (latest evaluation of every fixture), team stats, fitted-model cache, website data
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Iterable

from .config import ROOT

DATA = ROOT / "data"
SITE_DATA = ROOT / "site" / "data"

PICKS = DATA / "picks.jsonl"
SETTLEMENTS = DATA / "settlements.jsonl"
MODEL_RUNS = DATA / "model_runs.jsonl"
FETCH_LOG = DATA / "fetch_log.jsonl"
JOB_LOG = DATA / "job_log.jsonl"
FIXTURES = DATA / "fixtures.json"
DERIVED = DATA / "derived"            # rebuilt every run, not committed
BOARD = DERIVED / "board.json"
TEAM_STATS = DERIVED / "team_stats.json"
STATE = DATA / "state.json"
HISTORY = DATA / "history"
ODDS_DIR = DATA / "odds"
CACHE = DATA / "cache"
BACKTEST = DATA / "backtest.json"

ODDS_COLUMNS = ["snapshot_id", "batch_id", "fixture_id", "source", "bookmaker",
                "market", "line", "selection", "price", "fetched_at"]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def append_jsonl(path: Path, records: Iterable[dict]) -> int:
    records = list(records)
    if not records:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n")
    return len(records)


def read_json(path: Path, default=None):
    if not path.exists():
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path: Path, obj, compact: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False, default=str)
        else:
            json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False, default=str)
    os.replace(tmp, path)


# ---------- odds snapshots (append-only CSV per month) ----------

def append_odds(rows: list[dict]) -> int:
    if not rows:
        return 0
    by_month: dict[str, list[dict]] = {}
    for r in rows:
        by_month.setdefault(r["fetched_at"][:7], []).append(r)
    ODDS_DIR.mkdir(parents=True, exist_ok=True)
    for month, rs in by_month.items():
        path = ODDS_DIR / f"{month}.csv"
        new = not path.exists()
        with open(path, "a", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=ODDS_COLUMNS)
            if new:
                w.writeheader()
            for r in rs:
                w.writerow({k: r.get(k) for k in ODDS_COLUMNS})
    return len(rows)


def load_odds(fixture_ids: set[str] | None = None, months: int = 3) -> list[dict]:
    if not ODDS_DIR.exists():
        return []
    files = sorted(ODDS_DIR.glob("*.csv"))[-months:]
    out = []
    for path in files:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if fixture_ids is not None and r["fixture_id"] not in fixture_ids:
                    continue
                r["price"] = float(r["price"])
                r["line"] = float(r["line"]) if r["line"] not in ("", "None", None) else None
                out.append(r)
    return out


def history_path(season: str, div: str) -> Path:
    return HISTORY / season / f"{div}.csv"
