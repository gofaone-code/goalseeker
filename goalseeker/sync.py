"""Data sync jobs. Each is idempotent and logs created / updated / skipped counts.

An API failure is always recorded as an error, never as "no fixtures".
"""
from __future__ import annotations

import csv
import hashlib
import io
import re
from datetime import timedelta

from . import store
from .config import iso, parse_iso
from .history import season_code, season_end, seasons_back
from .sources import footballdata as fd


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def fixture_id(div: str, date_iso: str, home: str, away: str) -> str:
    return f"{div}-{date_iso.replace('-', '')}-{slug(home)}-{slug(away)}"


def log_fetch(job: str, res: fd.FetchResult, now, rows: int | None = None, note: str | None = None):
    store.append_jsonl(store.FETCH_LOG, [{
        "at": iso(now), "job": job, "url": res.url, "ok": res.ok, "http_status": res.status,
        "rows": rows, "error": res.error, "note": note, "seconds": round(res.seconds, 2),
    }])


def log_job(job: str, now, status: str, counts: dict, message: str | None = None) -> dict:
    rec = {"at": iso(now), "job": job, "status": status, "counts": counts, "message": message}
    store.append_jsonl(store.JOB_LOG, [rec])
    return rec


def history_seasons(cfg: dict, now) -> list[str]:
    n_past = int(cfg["model"]["seasons_of_history"])
    if cfg.get("backtest", {}).get("enabled"):
        n_past += int(cfg["backtest"].get("seasons", 1))
    return seasons_back(season_code(now.date()), n_past)


def history_divisions(leagues: list[dict]) -> list[str]:
    """Every division in a group that has at least one active league (keeps promoted teams' history)."""
    groups = {lg["group"] for lg in leagues if lg["active"]}
    return [lg["code"] for lg in leagues if lg["group"] in groups]


# ---------------------------------------------------------------- history

def sync_history(cfg: dict, leagues: list[dict], now) -> dict:
    current = season_code(now.date())
    manifest = store.read_json(store.HISTORY / "manifest.json", {})
    counts = {"downloaded": 0, "unchanged": 0, "cached_final": 0, "not_published": 0, "errors": 0, "rows": 0}
    errors = []
    in_a_row = 0
    for season in history_seasons(cfg, now):
        for div in history_divisions(leagues):
            if in_a_row >= 3:   # source unreachable: stop hammering it, try again next run
                counts["skipped_source_down"] = counts.get("skipped_source_down", 0) + 1
                continue
            key = f"{season}/{div}"
            path = store.history_path(season, div)
            info = manifest.get(key)
            final_after = season_end(season) + timedelta(days=21)
            if season != current and path.exists() and info and \
                    parse_iso(info["downloaded_at"]).date() >= final_after:
                counts["cached_final"] += 1
                continue
            res = fd.fetch(fd.season_url(season, div))
            if res.status == 404:
                in_a_row = 0
                counts["not_published"] += 1
                log_fetch("sync_history", res, now, note="season file not published (yet)")
                continue
            if not res.ok:
                counts["errors"] += 1
                in_a_row += 1
                errors.append(f"{key}: {res.error}")
                log_fetch("sync_history", res, now)
                continue
            in_a_row = 0
            rows = [r for r in fd.parse_csv(res.text) if fd.normalize_row(r)]
            log_fetch("sync_history", res, now, rows=len(rows))
            if not rows:
                counts["not_published"] += 1
                continue
            cols = [c for c in rows[0].keys() if fd.keep_column(c)]
            buf = io.StringIO()
            w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore", lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
            text = buf.getvalue()
            sha = hashlib.sha1(text.encode()).hexdigest()
            if info and info.get("sha1") == sha and path.exists():
                counts["unchanged"] += 1
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                counts["downloaded"] += 1
            counts["rows"] += len(rows)
            manifest[key] = {"downloaded_at": iso(now), "rows": len(rows), "sha1": sha}
    store.write_json(store.HISTORY / "manifest.json", manifest)
    status = "error" if counts["errors"] and not (counts["downloaded"] or counts["unchanged"]) else \
        ("partial" if counts["errors"] else "ok")
    return log_job("sync_history", now, status, counts, "; ".join(errors[:5]) or None)


# ------------------------------------------------------- fixtures + odds

def sync_fixtures_and_odds(cfg: dict, leagues: list[dict], now) -> dict:
    """syncFixtures + syncOdds. Both come from the same published file, fetched once."""
    by_code = {lg["code"]: lg for lg in leagues}
    res = fd.fetch(fd.fixtures_url())
    if not res.ok:
        log_fetch("sync_fixtures", res, now)
        return log_job("sync_fixtures_odds", now, "error", {}, f"fixtures source failed: {res.error}")
    rows = fd.parse_csv(res.text)
    log_fetch("sync_fixtures", res, now, rows=len(rows))

    fixtures = store.read_json(store.FIXTURES, {})
    state = store.read_json(store.STATE, {})
    source_hash = hashlib.sha1(res.text.encode()).hexdigest()
    source_changed = state.get("fixtures_csv_sha1") != source_hash

    c = {"fixtures_created": 0, "fixtures_updated": 0, "fixtures_unchanged": 0, "outside_window": 0,
         "inactive_league": 0, "odds_rows_added": 0, "odds_rows_unchanged": 0,
         "fixtures_without_prices": 0}
    unknown = set()
    batch_id = now.strftime("%Y%m%dT%H%M%S")
    odds_rows = []
    horizon = now + timedelta(days=cfg["window_days"])
    for r in rows:
        n = fd.normalize_row(r)
        if not n:
            continue
        lg = by_code.get(n["div"])
        if lg is None:
            unknown.add(n["div"])
            continue
        if not lg["active"]:
            c["inactive_league"] += 1
            continue
        ko = parse_iso(n["kickoff_utc"])
        if ko < now - timedelta(hours=3) or ko > horizon:
            c["outside_window"] += 1
            continue
        fid = fixture_id(n["div"], n["date"], n["home"], n["away"])
        rec = fixtures.get(fid)
        core = {"div": n["div"], "league": lg["name"], "country": lg["country"], "home": n["home"],
                "away": n["away"], "kickoff_utc": n["kickoff_utc"], "time_known": n["time_known"]}
        if rec is None:
            fixtures[fid] = {"fixture_id": fid, **core, "status": "scheduled", "source": fd.SOURCE,
                             "first_seen": iso(now), "last_seen": iso(now),
                             "ft_home": None, "ft_away": None}
            c["fixtures_created"] += 1
        else:
            changed = any(rec.get(k) != v for k, v in core.items())
            rec.update(core)
            rec["last_seen"] = iso(now)
            c["fixtures_updated" if changed else "fixtures_unchanged"] += 1
        prices = fd.extract_prices(r, closing=False)
        if not prices:
            c["fixtures_without_prices"] += 1
        for i, (bk, market, line, sel, price) in enumerate(prices):
            odds_rows.append({"snapshot_id": f"{batch_id}-{fid}-{bk}-{market}-{line}-{sel}",
                              "batch_id": batch_id, "fixture_id": fid, "source": fd.SOURCE,
                              "bookmaker": bk, "market": market, "line": "" if line is None else line,
                              "selection": sel, "price": price, "fetched_at": iso(now)})
    # Store a market's prices when they are new for that fixture or differ from the last stored
    # snapshot. Always store a market's selections together (over + under) so pairs stay coherent.
    # Re-storing identical prices would fake "movement", so unchanged markets are skipped.
    last: dict[tuple, dict] = {}
    for r in store.load_odds({o["fixture_id"] for o in odds_rows}):
        key = (r["fixture_id"], r["bookmaker"], r["market"], r["line"])
        cur = last.get(key)
        if cur is None or r["batch_id"] > cur["batch"]:
            last[key] = {"batch": r["batch_id"], "prices": {}}
            cur = last[key]
        if r["batch_id"] == cur["batch"]:
            cur["prices"][r["selection"]] = r["price"]
    groups: dict[tuple, list] = {}
    for o in odds_rows:
        line = None if o["line"] == "" else float(o["line"])
        groups.setdefault((o["fixture_id"], o["bookmaker"], o["market"], line), []).append(o)
    to_add = []
    for key, rows_ in groups.items():
        new_prices = {o["selection"]: o["price"] for o in rows_}
        prev = last.get(key)
        if prev and prev["prices"] == new_prices:
            c["odds_rows_unchanged"] += len(rows_)
        else:
            to_add.extend(rows_)
    c["odds_rows_added"] = store.append_odds(to_add)
    if source_changed:
        state["fixtures_csv_sha1"] = source_hash
        state["fixtures_csv_changed_at"] = iso(now)
    state["fixtures_csv_checked_at"] = iso(now)
    store.write_json(store.STATE, state)
    store.write_json(store.FIXTURES, fixtures)
    msg = f"source publishes divisions not in leagues.yaml: {sorted(unknown)}" if unknown else None
    status = "ok"
    if c["fixtures_created"] + c["fixtures_updated"] + c["fixtures_unchanged"] == 0:
        status = "empty"   # the source answered, and it has no fixtures for your leagues right now
    return log_job("sync_fixtures_odds", now, status, c, msg)


# ---------------------------------------------------------------- results

def update_results(cfg: dict, leagues: list[dict], results_df, now) -> dict:
    """Attach final scores to kicked-off fixtures; void the ones with no result in time."""
    fixtures = store.read_json(store.FIXTURES, {})
    index: dict[tuple, list] = {}
    for row in results_df.itertuples(index=False):
        index.setdefault((row.div, row.home, row.away), []).append(row)
    c = {"finished": 0, "voided": 0, "awaiting_result": 0, "already_final": 0}
    for fid, fx in fixtures.items():
        if fx["status"] in ("finished", "void"):
            c["already_final"] += 1
            continue
        ko = parse_iso(fx["kickoff_utc"])
        if ko > now:
            continue
        match = None
        for row in index.get((fx["div"], fx["home"], fx["away"]), []):
            delta = (row.date.date() - ko.date()).days
            if -1 <= delta <= cfg["void_after_days"]:
                match = row
                break
        if match is not None:
            fx.update({"status": "finished", "ft_home": int(match.hg), "ft_away": int(match.ag),
                       "result_date": match.date.date().isoformat(), "result_source": fd.SOURCE,
                       "result_seen_at": iso(now)})
            c["finished"] += 1
        elif now - ko > timedelta(days=cfg["void_after_days"]):
            fx.update({"status": "void", "void_reason":
                       f"no result published within {cfg['void_after_days']} days of kickoff "
                       "(postponed, abandoned or missing)", "result_seen_at": iso(now)})
            c["voided"] += 1
        else:
            c["awaiting_result"] += 1
    store.write_json(store.FIXTURES, fixtures)
    return log_job("update_results", now, "ok", c)
