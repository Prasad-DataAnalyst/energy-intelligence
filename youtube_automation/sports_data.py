#!/usr/bin/env python3
"""
sports_data.py
Fetches today's marquee WORLDWIDE fixtures for the daily World Sports
Astrology prediction video — global football, cricket, basketball, F1,
tennis, rugby, hockey and NFL.

The channel used to be US-only, which filtered out exactly the fixtures
with the largest astrology-receptive audiences on earth (Premier League,
Champions League, IPL/T20 cricket, F1). Dropping that filter alone would
have surfaced obscure lower-tier games, so the US filter is replaced by a
QUALITY filter: _fixture_rank() scores every event by how marquee its
competition is, and fetch_today_matches() returns them best-first, so the
generator's matches[0] is the biggest game on the planet that day.

Two sources, checked in order:

1. A manual override file, sports_matches_YYYYMMDD.json, in this directory —
   if present, ALWAYS used instead of the live API (and NOT re-ranked:
   whatever you hand-supply is trusted as-is, in your order). This is also
   the fallback for a broken/rate-limited API day.
2. TheSportsDB free tier (eventsday.php), one call per configured sport,
   ranked through _fixture_rank().

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
# WORLDWIDE: football (the planet's #1 sport) and cricket (India/Pakistan/
# Australia/England — the largest astrology-following audiences anywhere) lead,
# then the other globally-followed codes. Between them something marquee is on
# essentially every day of the year, in every time zone.
SPORT_MAP = {
    "football":   "Soccer",            # Premier League, La Liga, UCL, Serie A...
    "cricket":    "Cricket",           # IPL, T20/ODI/Test internationals, BBL
    "basketball": "Basketball",        # NBA, EuroLeague
    "motorsport": "Motorsport",        # Formula 1
    "tennis":     "Tennis",            # Grand Slams, ATP/WTA
    "rugby":      "Rugby",             # Six Nations, World Cup, Super Rugby
    "hockey":     "Ice Hockey",        # NHL
    "nfl":        "American Football",  # NFL
}

# Marquee ranking (replaces the old US-only filter). A fixture's score is the
# highest-scoring keyword its LEAGUE name matches; ties fall back to sport
# order above. Everything still qualifies — a quiet Tuesday can legitimately
# be a mid-table league — but the biggest competition of the day always sorts
# to position 0, which is the one the generator turns into a video.
_LEAGUE_TIERS = (
    # tier 3 — planet-stopping events
    (300, ("world cup", "champions league", "olympic", "super bowl",
           "grand slam", "wimbledon", "us open", "french open",
           "australian open", "formula 1", "grand prix", "indian premier league",
           "ipl", "the ashes", "t20 world cup", "euro 20", "copa america",
           "super rugby", "six nations", "nba finals")),
    # tier 2 — top domestic / continental competitions
    (200, ("premier league", "la liga", "serie a", "bundesliga", "ligue 1",
           "europa league", "eredivisie", "primeira liga", "copa libertadores",
           "nba", "nfl", "nhl", "euroleague", "big bash", "the hundred",
           "test match", "one day international", "atp", "wta",
           "saudi pro league", "mls", "major league soccer", "efl cup",
           "fa cup", "copa del rey")),
    # tier 1 — recognised national leagues
    (100, ("championship", "league one", "league two", "serie b", "segunda",
           "2. bundesliga", "j1", "k league", "a-league", "super lig",
           "liga mx", "brasileiro", "scottish", "ncaa", "wnba")),
)


def _fixture_rank(m: dict) -> int:
    """Higher = more marquee. Used to sort the day's global fixtures."""
    league = (m.get("league") or "").lower()
    for score, hints in _LEAGUE_TIERS:
        if any(h in league for h in hints):
            return score
    return 0


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
    # Worldwide: keep this sport's most marquee fixtures of the day (the old
    # US-only filter is replaced by this quality ranking — see _fixture_rank).
    matches.sort(key=_fixture_rank, reverse=True)
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

    # Global best-first: the generator uses matches[0], so the single biggest
    # fixture on earth today becomes the video. Stable sort keeps SPORT_MAP
    # order (football, then cricket, ...) as the tie-break within a tier.
    all_matches.sort(key=_fixture_rank, reverse=True)
    if all_matches:
        top = all_matches[0]
        print(f"[INFO] Top fixture: {top['team_a']} vs {top['team_b']} "
              f"({top.get('league') or top['sport']}, rank {_fixture_rank(top)})")
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
