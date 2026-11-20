"""football-data.co.uk: free fixtures, results and bookmaker prices (no API key).

What it publishes (checked November 2026):
  /fixtures.csv                  upcoming matches with pre-match prices
  /mmz4281/<season>/<div>.csv    results for a season, with pre-match and closing prices
Prices are collected on Friday afternoons (weekend games) and Tuesday afternoons
(midweek games). Over/under 2.5 prices come from Bet365 (B365) and Betfair
Exchange (BFE), plus a market maximum and market average across many books.
Kick-off times are UK local time.
"""
from __future__ import annotations

import csv
import io
import os
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import requests

BASE = os.environ.get("GOALSEEKER_SOURCE_BASE", "https://www.football-data.co.uk").rstrip("/")
SOURCE = "football-data.co.uk"
UK = ZoneInfo("Europe/London")
USER_AGENT = "GoalSeeker/2.0 (personal betting tracker; scheduled GitHub Action)"
POLITE_DELAY_S = float(os.environ.get("GOALSEEKER_POLITE_DELAY", "1.0"))

BOOKMAKERS = {
    "B365": "Bet365", "BFE": "Betfair Exchange", "BFD": "Betfred", "BF": "Betfair",
    "BV": "BetVictor", "BW": "Bet&Win", "PP": "Paddy Power", "SKB": "Sky Bet", "SK": "Sky Bet",
    "PS": "Pinnacle", "P": "Pinnacle", "WH": "William Hill", "1XB": "1xBet", "BMGM": "BetMGM",
    "CL": "Coral", "LB": "Ladbrokes", "IW": "Interwetten", "VC": "VC Bet", "SO": "Sporting Odds",
    "SB": "Sportingbet", "SJ": "Stan James", "SY": "Stanleybet", "GB": "Gamebookers",
    "BS": "Blue Square", "Max": "Market maximum", "Avg": "Market average",
    "BbMx": "Market maximum", "BbAv": "Market average",
}
# Two codes for the same bookmaker -> one canonical code
CANONICAL = {"P": "PS", "SK": "SKB", "BbMx": "Max", "BbAv": "Avg"}
AGGREGATES = {"Max", "Avg"}
_CODES = sorted(BOOKMAKERS, key=len, reverse=True)
_OU = re.compile(r"^(C?)([<>])(\d+(?:\.\d+)?)$")
_X12 = re.compile(r"^(C?)([HDA])$")
_X12_SEL = {"H": "home", "D": "draw", "A": "away"}
_column_cache: dict[str, tuple | None] = {}


def bookmaker_name(code: str) -> str:
    return BOOKMAKERS.get(code, code)


def parse_price_column(col: str):
    """'B365>2.5' -> ('B365', 'ou', 2.5, 'over', False); 'PSCH' -> ('PS', '1x2', None, 'home', True).
    Returns None for columns that are not bookmaker prices."""
    if col in _column_cache:
        return _column_cache[col]
    result = None
    for code in _CODES:
        if not col.startswith(code):
            continue
        rest = col[len(code):]
        m = _OU.match(rest)
        if m:
            result = (CANONICAL.get(code, code), "ou", float(m.group(3)),
                      "over" if m.group(2) == ">" else "under", m.group(1) == "C")
            break
        m = _X12.match(rest)
        if m:
            result = (CANONICAL.get(code, code), "1x2", None, _X12_SEL[m.group(2)], m.group(1) == "C")
            break
    _column_cache[col] = result
    return result


def extract_prices(row: dict, closing: bool = False) -> list[tuple]:
    """All bookmaker prices in a row as (bookmaker, market, line, selection, price)."""
    out = []
    for col, raw in row.items():
        if not col or raw in (None, ""):
            continue
        parsed = parse_price_column(col)
        if not parsed or parsed[4] != closing:
            continue
        try:
            price = float(raw)
        except ValueError:
            continue
        if price <= 1.0 or price > 1000:
            continue
        bk, market, line, sel, _ = parsed
        out.append((bk, market, line, sel, price))
    return out


def _parse_date(s: str) -> date | None:
    s = (s or "").strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def normalize_row(row: dict) -> dict | None:
    """One CSV row -> fixture fields, or None if the row is not a usable match."""
    div = (row.get("Div") or "").strip()
    home = (row.get("HomeTeam") or row.get("HT") or "").strip()
    away = (row.get("AwayTeam") or row.get("AT") or "").strip()
    d = _parse_date(row.get("Date"))
    if not (div and home and away and d):
        return None
    t = (row.get("Time") or "").strip()
    time_known = bool(re.match(r"^\d{1,2}:\d{2}$", t))
    hh, mm = (int(t.split(":")[0]), int(t.split(":")[1])) if time_known else (12, 0)
    kickoff = datetime(d.year, d.month, d.day, hh, mm, tzinfo=UK).astimezone(timezone.utc)
    return {
        "div": div, "date": d.isoformat(), "home": home, "away": away,
        "kickoff_utc": kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), "time_known": time_known,
        "hg": _int(row.get("FTHG", row.get("HG"))), "ag": _int(row.get("FTAG", row.get("AG"))),
    }


def parse_csv(text: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    for r in reader:
        r = {(k or "").strip(): (v or "").strip() if isinstance(v, str) else "" for k, v in r.items() if k}
        if any(r.values()):
            rows.append(r)
    return rows


# Columns kept when storing a results file (everything the app needs, nothing else)
def keep_column(col: str) -> bool:
    return col in ("Div", "Date", "Time", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR",
                   "HS", "AS", "HST", "AST") or \
        parse_price_column(col) is not None


@dataclass
class FetchResult:
    url: str
    ok: bool
    status: int | None
    text: str | None
    error: str | None
    seconds: float


_last_request = 0.0


def fetch(url: str, retries: int = 3, timeout: int = 30) -> FetchResult:
    """GET with retries and backoff. 404 is returned at once (it means 'not published')."""
    global _last_request
    start = time.time()
    err = None
    status = None
    for attempt in range(retries):
        wait = POLITE_DELAY_S - (time.time() - _last_request)
        if wait > 0:
            time.sleep(wait)
        _last_request = time.time()
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            status = r.status_code
            if r.status_code == 200:
                raw = r.content
                for enc in ("utf-8-sig", "cp1252", "latin-1"):
                    try:
                        text = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                if text.lstrip().startswith("<"):
                    return FetchResult(url, False, status, None, "source returned a web page instead of CSV",
                                       time.time() - start)
                return FetchResult(url, True, status, text, None, time.time() - start)
            if r.status_code == 404:
                return FetchResult(url, False, 404, None, "not published (404)", time.time() - start)
            err = f"HTTP {r.status_code}"
            if r.status_code in (429, 500, 502, 503, 504):
                retry_after = r.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 5 * (attempt + 1)
                time.sleep(min(delay, 60))
                continue
            break
        except requests.RequestException as e:
            err = f"{type(e).__name__}: {e}"
            time.sleep(3 * (attempt + 1))
    return FetchResult(url, False, status, None, err or "unknown error", time.time() - start)


def fixtures_url() -> str:
    return f"{BASE}/fixtures.csv"


def season_url(season: str, div: str) -> str:
    return f"{BASE}/mmz4281/{season}/{div}.csv"
