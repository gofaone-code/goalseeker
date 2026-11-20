"""Settings, league list and the clock.

GOALSEEKER_ROOT overrides the project folder (used by tests).
GOALSEEKER_NOW overrides the clock (used by tests and replays only).
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(os.environ.get("GOALSEEKER_ROOT", Path(__file__).resolve().parent.parent))


def now_utc() -> datetime:
    override = os.environ.get("GOALSEEKER_NOW")
    if override:
        return datetime.fromisoformat(override.replace("Z", "+00:00")).astimezone(timezone.utc)
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


_RANGES = {
    "bankroll_units": (1, 1_000_000),
    "kelly_fraction": (0.0, 1.0),
    "max_stake_pct": (0.0, 1.0),
    "stake_step_units": (0.01, 100),
    "edge_threshold": (0.0, 0.5),
    "min_model_probability": (0.0, 1.0),
    "min_odds": (1.0, 100),
    "max_edge": (0.0, 1.0),
    "betfair_commission": (0.0, 0.2),
    "window_days": (1, 14),
    "today_hours": (1, 168),
    "pick_cutoff_minutes": (0, 1440),
    "void_after_days": (2, 60),
    "min_team_matches": (1, 100),
    "min_sample_for_breakdown": (1, 1000),
}


class ConfigError(ValueError):
    pass


def load_config() -> dict:
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    for key, (lo, hi) in _RANGES.items():
        if key not in cfg:
            raise ConfigError(f"config.yaml is missing '{key}'")
        val = cfg[key]
        if not isinstance(val, (int, float)) or not (lo <= val <= hi):
            raise ConfigError(f"config.yaml: '{key}' must be a number between {lo} and {hi} (got {val!r})")
    if cfg["max_edge"] <= cfg["edge_threshold"]:
        raise ConfigError("config.yaml: max_edge must be larger than edge_threshold")
    markets = cfg.get("markets") or []
    bad = [m for m in markets if m not in ("ou", "1x2")]
    if not markets or bad:
        raise ConfigError(f"config.yaml: markets must be a list containing ou and/or 1x2 (got {markets!r})")
    if not cfg.get("bettable_bookmakers"):
        raise ConfigError("config.yaml: bettable_bookmakers must list at least one bookmaker code")
    m = cfg.get("model") or {}
    for key in ("version", "seasons_of_history", "half_life_days", "prior_sd", "shots_weight", "max_goals",
                "uncertainty_draws"):
        if key not in m:
            raise ConfigError(f"config.yaml: model.{key} is missing")
    cfg.setdefault("backtest", {"enabled": False})
    return cfg


def load_leagues() -> list[dict]:
    with open(ROOT / "leagues.yaml", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    leagues = data.get("leagues") or []
    seen = set()
    for lg in leagues:
        for key in ("code", "name", "country", "group", "tier", "active"):
            if key not in lg:
                raise ConfigError(f"leagues.yaml: league {lg!r} is missing '{key}'")
        if lg["code"] in seen:
            raise ConfigError(f"leagues.yaml: duplicate league code {lg['code']}")
        seen.add(lg["code"])
    return leagues


def config_hash(cfg: dict) -> str:
    return hashlib.sha1(json.dumps(cfg, sort_keys=True, default=str).encode()).hexdigest()[:12]
