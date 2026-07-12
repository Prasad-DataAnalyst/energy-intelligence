#!/usr/bin/env python3
"""
sports_data.py
Fetches today's US matches (NFL, NBA, MLB, NHL, MLS) for the daily Sports
Astrology prediction video. The channel is US-only, so events are filtered
to US-hosted games / US leagues. Two sources, checked in order:

1. A manual override file, sports_matches_YYYYMMDD.json, in this directory —
   if present, ALWAYS used instead of the live API (and NOT re-filtered:
   whatever you hand-supply is trusted as-is). This is also the fallback for
   a broken/rate-limited API day.
2. TheSportsDB free tier (eventsday.php), one call per configured sport,
   filtered through _is_us_event().

The response shape (strEvent/strHomeTeam/strVenue/strTime/strCountry, times
in UTC) was VERIFIED against a real production fetch on 2026-07-10 — the
documented schema matched exactly. _normalize_event() stays defensive
regardless (every field via .get()).
"""
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).parent

SPORTSDB_KEY = os.getenv("SPORTSDB_API_KEY", "123")   # "123" = public free test key
SPORTSDB_BASE = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_KEY}"

# internal sport key -> TheSportsDB's own sport-name string for the `s=` param.
# US-ONLY channel: the five sports Americans actually watch, filtered below
# to US-hosted events (the first live fetch without a filter returned Irish
# soccer, English cricket, and a Canadian CFL game — none suitable). Between
# MLB (daily Apr-Oct), NBA/NHL (Oct-Jun) and NFL (Sep-Feb) there is something
# on virtually every day of the year.
SPORT_MAP = {
    "nfl":        "American Football",
    "basketball": "Basketball",
    "baseball":   "Baseball",
    "hockey":     "Ice Hockey",
    "soccer":     "Soccer",
}

# US filter: an event counts as US if its country field says so OR its league
# is a known US league (belt-and-braces — TheSportsDB's country strings for
# US events weren't live-verifiable from the dev sandbox, but league names
# like "NFL"/"NBA"/"MLB" are stable and documented).
_US_COUNTRIES = {"united states", "usa", "us", "united states of america"}
_US_LEAGUE_HINTS = ("nfl", "nba", "mlb", "nhl", "mls", "major league soccer",
                    "major league baseball", "ncaa", "wnba", "usl")


def _is_us_event(m: dict) -> bool:
    if (m.get("country") or "").strip().lower() in _US_COUNTRIES:
        return True
    league = (m.get("league") or "").lower()
    return any(h in league for h in _US_LEAGUE_HINTS)


# How many matches to keep per sport per day, to keep the video a reasonable
# length — the free tier can return many lower-tier fixtures on a busy day.
MAX_PER_SPORT = int(os.getenv("SPORTS_MAX_PER_SPORT", "2"))
REQUEST_TIMEOUT = 15


def _manual_override_path(date_tag: str) -> Path:
    return HERE / f"sports_matches_{date_tag}.json"


def _load_manual_override(date_tag: str):
    path = _manual_override_path(date_tag)
    if not path.exists():
        return None
    try:
        matches = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(matches, dict):
            matches = matches.get("matches", [])
        print(f"[INFO] Using manual match list: {path.name} ({len(matches)} matches)")
        return matches
    except Exception as e:
        print(f"[WARN] Manual override file {path.name} is invalid JSON: {e}", file=sys.stderr)
        return None


def _normalize_event(ev: dict, sport_key: str) -> dict:
    """Defensive normalization — TheSportsDB's exact field set was not
    live-verified from this dev environment (see module docstring). Every
    field is fetched with .get() and coerced, never assumed present."""
    date_str = str(ev.get("dateEvent") or "").strip()
    time_str = str(ev.get("strTime") or "00:00:00").strip()
    try:
        dt = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S")
    except Exception:
        dt = None

    home = str(ev.get("strHomeTeam") or "").strip()
    away = str(ev.get("strAwayTeam") or "").strip()
    match_name = str(ev.get("strEvent") or (f"{home} vs {away}" if home and away else "")).strip()

    return {
        "sport": sport_key,
        "match_name": match_name,
        "team_a": home,
        "team_b": away,
        "datetime_utc": dt.isoformat() if dt else None,
        "venue": str(ev.get("strVenue") or "").strip(),
        "country": str(ev.get("strCountry") or "").strip(),
        "league": str(ev.get("strLeague") or "").strip(),
    }


def _fetch_sport(sport_key: str, sportsdb_name: str, date_str: str) -> list:
    url = f"{SPORTSDB_BASE}/eventsday.php"
    try:
        r = requests.get(url, params={"d": date_str, "s": sportsdb_name}, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[WARN] TheSportsDB fetch failed for {sportsdb_name}: {e}", file=sys.stderr)
        return []

    events = data.get("events") or []
    matches = [_normalize_event(ev, sport_key) for ev in events]
    # Keep only matches with at least both team names and a start time —
    # a malformed/partial entry is worse than no entry for a scripted video.
    matches = [m for m in matches if m["team_a"] and m["team_b"] and m["datetime_utc"]]
    # US-only channel: drop anything not hosted in the US / a US league.
    matches = [m for m in matches if _is_us_event(m)]
    return matches[:MAX_PER_SPORT]


def fetch_today_matches(date_tag: str, sports: list = None) -> list:
    """date_tag: YYYYMMDD. Returns a list of normalized match dicts. Checks
    the manual override first; only calls the live API if no override file
    exists. Returns [] (never raises) if nothing is available — the caller
    is expected to skip the day gracefully, matching how every other content
    type in this pipeline degrades rather than crashes cron."""
    override = _load_manual_override(date_tag)
    if override is not None:
        return override

    sports = sports or list(SPORT_MAP.keys())
    date_str = datetime.strptime(date_tag, "%Y%m%d").strftime("%Y-%m-%d")
    all_matches = []
    for sport_key in sports:
        sportsdb_name = SPORT_MAP.get(sport_key)
        if not sportsdb_name:
            continue
        found = _fetch_sport(sport_key, sportsdb_name, date_str)
        print(f"[INFO] {sport_key}: {len(found)} match(es) for {date_str}")
        all_matches.extend(found)
        time.sleep(1.5)   # stay well under the free tier's 30 req/min

    return all_matches


def main():
    date_tag = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    matches = fetch_today_matches(date_tag)
    print(json.dumps(matches, indent=2, ensure_ascii=False))
    if not matches:
        print(f"\n[INFO] No matches found for {date_tag}. Supply "
              f"sports_matches_{date_tag}.json to override.", file=sys.stderr)


if __name__ == "__main__":
    main()
