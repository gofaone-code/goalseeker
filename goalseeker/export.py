"""Builds the JSON the website reads. The site shows nothing that isn't in these files."""
from __future__ import annotations

import os

from . import __version__, store
from .config import iso
from .record import build_record, merge
from .sources.footballdata import BOOKMAKERS


def _status() -> dict:
    jobs = store.read_jsonl(store.JOB_LOG)
    latest = {}
    for j in jobs:
        latest[j["job"]] = j
    last_ok = {}
    for j in jobs:
        if j["status"] in ("ok", "empty", "partial"):
            last_ok[j["job"]] = j["at"]
    fetches = store.read_jsonl(store.FETCH_LOG)
    errors = [f for f in fetches if not f["ok"] and f.get("http_status") != 404][-10:]
    runs = store.read_jsonl(store.MODEL_RUNS)
    return {"jobs": latest, "last_success": last_ok, "recent_fetch_errors": errors,
            "last_model_run": runs[-1] if runs else None, "source_state": store.read_json(store.STATE, {}),
            "history_files": len(store.read_json(store.HISTORY / "manifest.json", {}))}


def export_site(cfg: dict, leagues: list[dict], now) -> dict:
    board = store.read_json(store.BOARD, {"fixtures": []})
    picks = store.read_jsonl(store.PICKS)
    settlements = store.read_jsonl(store.SETTLEMENTS)
    merged = merge(picks, settlements)
    merged.sort(key=lambda m: m["fixture"]["kickoff_utc"], reverse=True)
    record = build_record(merged, cfg["min_sample_for_breakdown"])
    public_cfg = {k: v for k, v in cfg.items() if k != "backtest"}
    public_cfg["backtest"] = cfg.get("backtest")
    app = {
        "generated_at": iso(now), "app_version": __version__, "config": public_cfg,
        "leagues": leagues, "bookmakers": BOOKMAKERS, "status": _status(),
        "board": board, "picks": merged, "record": record,
        "team_stats": store.read_json(store.TEAM_STATS, {"teams": {}})["teams"],
    }
    note = os.environ.get("GOALSEEKER_PREVIEW_NOTE")
    if note:  # only ever set by hand for a demo build; the scheduled pipeline never sets it
        app["preview_note"] = note
    store.write_json(store.SITE_DATA / "app.json", app, compact=True)
    bt = store.read_json(store.BACKTEST)
    if bt:
        store.write_json(store.SITE_DATA / "backtest.json", bt, compact=True)
    return {"picks": len(merged), "fixtures": len(board.get("fixtures", [])),
            "record_reconciles": record["counts"]["reconciles"]}
