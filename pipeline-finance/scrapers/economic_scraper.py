"""
scrapers/economic_scraper.py — DriftWire326
EconomicScraper class: FRED API data releases, Fed/Treasury RSS feeds,
event significance scoring, and weekly economic calendar.
Uses fredapi library for FRED data; stdlib XML for RSS (no feedparser needed).
FRED_API_KEY loaded from .env — all methods degrade gracefully without it.
"""
import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional, Union

import requests

from config.settings import (
    FRED_API_KEY, FRED_SERIES,
    MAX_RETRIES, RETRY_BACKOFF_BASE,
    OUTPUT_DIR,
)

logger = logging.getLogger(__name__)

# ── FRED API endpoints ────────────────────────────────────────────────────────

FRED_BASE          = "https://api.stlouisfed.org/fred"
FRED_OBS_URL       = f"{FRED_BASE}/series/observations"
FRED_INFO_URL      = f"{FRED_BASE}/series"
FRED_RELEASE_DATES = f"{FRED_BASE}/release/dates"

# ── Series catalogue ──────────────────────────────────────────────────────────
# (series_id → (friendly_name, base_significance_score, typical_freq_days))

SERIES_CATALOGUE: dict[str, tuple[str, int, int]] = {
    "CPIAUCSL": ("CPI Inflation",           90, 30),
    "PCE":      ("PCE Price Index",          75, 30),
    "GDP":      ("Real GDP Growth",          85, 90),
    "UNRATE":   ("Unemployment Rate",        80, 30),
    "FEDFUNDS": ("Fed Funds Rate",          100, 30),
    "RSXFS":    ("Retail Sales",             60, 30),
    "HOUST":    ("Housing Starts",           50, 30),
    "INDPRO":   ("Industrial Production",    45, 30),
}

# ── Significance scoring keywords ─────────────────────────────────────────────

_SCORE_KEYWORDS: list[tuple[int, list[str]]] = [
    (100, ["fomc", "federal reserve", "fed decision", "rate decision", "fedfunds", "FEDFUNDS"]),
    (90,  ["cpi", "consumer price", "inflation", "CPIAUCSL"]),
    (85,  ["gdp", "gross domestic", "GDP"]),
    (80,  ["unemployment", "nonfarm", "jobs report", "UNRATE", "payroll"]),
    (75,  ["pce", "personal consumption", "PCE"]),
    (60,  ["retail sales", "RSXFS", "consumer spending"]),
    (50,  ["housing", "HOUST", "building permits", "existing home"]),
    (45,  ["industrial production", "INDPRO", "manufacturing"]),
]

# ── RSS feed URLs ─────────────────────────────────────────────────────────────

FED_RSS_URL      = "https://www.federalreserve.gov/feeds/press_all.xml"
TREASURY_RSS_URL = "https://home.treasury.gov/news/press-releases.xml"


# ── Surprise thresholds (backward compat) ─────────────────────────────────────

_SURPRISE_THRESHOLDS = {
    "CPIAUCSL": 0.3,
    "FEDFUNDS": 0.25,
    "UNRATE":   0.2,
    "GDP":      0.5,
    "T10YIE":   0.15,
    "DGS10":    0.15,
    "UMCSENT":  5.0,
}


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class EconomicRelease:
    """A FRED data series updated today — the primary output of get_todays_releases()."""
    series_id:          str
    name:               str
    latest_value:       float
    previous_value:     Optional[float]
    change:             Optional[float]
    change_pct:         Optional[float]
    significance_score: int
    release_date:       str    # ISO date of the observation period
    unit:               str
    source:             str = "FRED"

    @property
    def is_market_moving(self) -> bool:
        return self.significance_score >= 80

    def to_dict(self) -> dict:
        return {
            "series_id":          self.series_id,
            "name":               self.name,
            "latest_value":       self.latest_value,
            "previous_value":     self.previous_value,
            "change":             self.change,
            "change_pct":         self.change_pct,
            "significance_score": self.significance_score,
            "release_date":       self.release_date,
            "unit":               self.unit,
            "is_market_moving":   self.is_market_moving,
            "source":             self.source,
        }


# ── Backward-compat dataclasses ───────────────────────────────────────────────

@dataclass
class EconomicIndicator:
    series_id:      str
    name:           str
    value:          float
    previous_value: Optional[float]
    change:         Optional[float]
    change_pct:     Optional[float]
    period:         str
    unit:           str
    frequency:      str
    source:         str
    last_updated:   str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def trend(self) -> str:
        if self.change is None:
            return "unknown"
        return "rising" if self.change > 0 else "falling" if self.change < 0 else "flat"

    @property
    def is_surprising(self) -> bool:
        thresh = _SURPRISE_THRESHOLDS.get(self.series_id)
        return bool(thresh and self.change and abs(self.change) >= thresh)

    @property
    def narrative(self) -> str:
        if self.change is None:
            return f"{self.name}: {self.value} {self.unit} (as of {self.period})"
        direction = "rose" if self.change > 0 else "fell"
        return (
            f"{self.name} {direction} to {self.value} {self.unit} "
            f"({self.change:+.3f} from prior {self.previous_value}) — {self.period}"
        )


@dataclass
class EconomicSnapshot:
    date:            str
    indicators:      dict[str, EconomicIndicator]
    fed_next_meeting: Optional[str]
    surprise_events: list[str]

    def to_narrative(self) -> str:
        lines = [f"=== ECONOMIC DATA — {self.date} ==="]
        for ind in self.indicators.values():
            marker = " ⚡ SURPRISE" if ind.is_surprising else ""
            lines.append(f"  • {ind.narrative}{marker}")
        if self.surprise_events:
            lines.append("\nDATA SURPRISES (script-worthy):")
            for s in self.surprise_events:
                lines.append(f"  ⚡ {s}")
        if self.fed_next_meeting:
            lines.append(f"\nNext Fed Meeting: {self.fed_next_meeting}")
        return "\n".join(lines)


# ── RSS helpers ───────────────────────────────────────────────────────────────

def _parse_rss_date(date_str: str) -> Optional[datetime]:
    """Parse RFC 2822 pubDate from RSS into an aware datetime."""
    if not date_str:
        return None
    try:
        return parsedate_to_datetime(date_str.strip())
    except Exception:
        return None


def _fetch_rss(url: str, max_age_hours: int = 24) -> list[dict]:
    """
    Download and parse an RSS 2.0 feed. Returns items published within
    max_age_hours. Never raises — returns [] on any failure.
    """
    try:
        resp = requests.get(
            url,
            timeout=15,
            headers={"User-Agent": "DriftWire326-Scraper/1.0"},
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logger.warning("RSS fetch failed for %s: %s", url, exc)
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items: list[dict] = []

    try:
        root = ET.fromstring(resp.content)
        # Handle both RSS 2.0 and Atom; namespaces vary
        for item in root.findall(".//{*}item") or root.findall(".//item"):
            title   = (item.findtext("{*}title") or item.findtext("title") or "").strip()
            link    = (item.findtext("{*}link")  or item.findtext("link")  or "").strip()
            desc    = (item.findtext("{*}description") or item.findtext("description") or "").strip()
            pub_str = (item.findtext("{*}pubDate") or item.findtext("pubDate") or "").strip()

            pub_dt = _parse_rss_date(pub_str)
            if pub_dt and pub_dt < cutoff:
                continue   # too old

            items.append({
                "title":     title,
                "link":      link,
                "summary":   desc[:600],
                "published": pub_str,
            })
    except ET.ParseError as exc:
        logger.warning("RSS XML parse error for %s: %s", url, exc)

    return items


# ── EconomicScraper class ─────────────────────────────────────────────────────

class EconomicScraper:
    """
    Central economic data collector for DriftWire326.
    All public methods degrade gracefully — they log errors and return
    empty lists rather than crashing the pipeline.
    Output saved to output/scripts/economic_YYYYMMDD.json.
    """

    def __init__(
        self,
        api_key: str = FRED_API_KEY,
        output_dir: Optional[Path] = None,
    ) -> None:
        self._api_key = api_key
        self._fred = None
        self._output_dir = (output_dir or OUTPUT_DIR) / "scripts"
        self._output_dir.mkdir(parents=True, exist_ok=True)

        if api_key:
            try:
                from fredapi import Fred
                self._fred = Fred(api_key=api_key)
                logger.info("fredapi connected (FRED_API_KEY set)")
            except ImportError:
                logger.warning("fredapi not installed — install with: pip install fredapi")
        else:
            logger.warning("FRED_API_KEY not set — economic data will be limited")

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _save_json(self, data: dict) -> Path:
        filename = f"economic_{date.today().strftime('%Y%m%d')}.json"
        path = self._output_dir / filename
        path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
        logger.info("Saved → %s", path)
        return path

    def _fred_series_info(self, series_id: str) -> dict:
        """Fetch series metadata via FRED REST (works without fredapi installed)."""
        try:
            resp = requests.get(
                FRED_INFO_URL,
                params={"series_id": series_id, "api_key": self._api_key, "file_type": "json"},
                timeout=10,
            )
            resp.raise_for_status()
            seriess = resp.json().get("seriess", [])
            return seriess[0] if seriess else {}
        except Exception as exc:
            logger.debug("Series info failed for %s: %s", series_id, exc)
            return {}

    def _fred_latest_obs(self, series_id: str, n: int = 3) -> list[dict]:
        """Return the n most-recent non-missing observations for a series."""
        try:
            resp = requests.get(
                FRED_OBS_URL,
                params={
                    "series_id":  series_id,
                    "api_key":    self._api_key,
                    "file_type":  "json",
                    "sort_order": "desc",
                    "limit":      n + 2,   # extra buffer for missing-value rows
                },
                timeout=10,
            )
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
            return [o for o in obs if o.get("value", ".") != "."][:n]
        except Exception as exc:
            logger.warning("FRED observations failed for %s: %s", series_id, exc)
            return []

    # ── Public methods ───────────────────────────────────────────────────────

    def get_todays_releases(self) -> list[EconomicRelease]:
        """
        Check FRED for series updated today. Returns EconomicRelease objects
        for any series in SERIES_CATALOGUE that published new data today.
        Returns [] if FRED_API_KEY is not set or all requests fail.
        """
        if not self._api_key:
            logger.warning("get_todays_releases: no FRED_API_KEY — returning empty list")
            return []

        today_str = date.today().isoformat()
        releases: list[EconomicRelease] = []

        for series_id, (name, score, _freq) in SERIES_CATALOGUE.items():
            try:
                info = self._fred_series_info(series_id)
                last_updated = str(info.get("last_updated", ""))[:10]

                if last_updated != today_str:
                    logger.debug("%s last updated %s — not today", series_id, last_updated)
                    continue

                obs = self._fred_latest_obs(series_id, n=2)
                if not obs:
                    continue

                latest_val  = float(obs[0]["value"])
                prev_val    = float(obs[1]["value"]) if len(obs) > 1 else None
                change      = round(latest_val - prev_val, 4) if prev_val is not None else None
                change_pct  = (
                    round((change / abs(prev_val)) * 100, 4)
                    if change is not None and prev_val
                    else None
                )

                releases.append(EconomicRelease(
                    series_id          = series_id,
                    name               = name,
                    latest_value       = latest_val,
                    previous_value     = prev_val,
                    change             = change,
                    change_pct         = change_pct,
                    significance_score = score,
                    release_date       = obs[0]["date"],
                    unit               = info.get("units_short", ""),
                ))
                logger.info("Today's release: %s = %.3f (%+.3f)", series_id, latest_val, change or 0)

            except Exception as exc:
                logger.warning("get_todays_releases skipping %s: %s", series_id, exc)
                continue

        releases.sort(key=lambda r: r.significance_score, reverse=True)
        self._save_json({
            "timestamp": datetime.now().isoformat(),
            "releases":  [r.to_dict() for r in releases],
        })
        logger.info("Today's releases: %d series published", len(releases))
        return releases

    def get_fed_statements(self, max_age_hours: int = 24) -> list[dict]:
        """
        Pull Federal Reserve press releases RSS feed and filter for recent items.
        Returns list of {title, link, summary, published}. Returns [] on failure.
        """
        logger.info("Fetching Fed RSS feed")
        items = _fetch_rss(FED_RSS_URL, max_age_hours=max_age_hours)

        # Annotate each item with its significance score
        for item in items:
            item["significance_score"] = self.score_economic_event(item)

        items.sort(key=lambda x: x.get("significance_score", 0), reverse=True)
        logger.info("Fed statements in last %dh: %d", max_age_hours, len(items))
        return items

    def get_treasury_news(self, max_age_hours: int = 24) -> list[dict]:
        """
        Pull US Treasury press releases RSS feed and filter for recent items.
        Returns list of {title, link, summary, published}. Returns [] on failure.
        """
        logger.info("Fetching Treasury RSS feed")
        items = _fetch_rss(TREASURY_RSS_URL, max_age_hours=max_age_hours)

        for item in items:
            item["significance_score"] = self.score_economic_event(item)

        items.sort(key=lambda x: x.get("significance_score", 0), reverse=True)
        logger.info("Treasury releases in last %dh: %d", max_age_hours, len(items))
        return items

    def score_economic_event(self, event: Union[EconomicRelease, dict]) -> int:
        """
        Return a significance score (0-100) for an economic event.

        Scoring hierarchy:
          Fed decision / FOMC statement = 100
          CPI release                   =  90
          GDP release                   =  85
          Jobs / unemployment           =  80
          PCE                           =  75
          Retail sales                  =  60
          Housing starts                =  50
          Industrial production         =  45

        Accepts EconomicRelease objects or dicts with any combination of
        'series_id', 'name', and 'title' fields.
        """
        if isinstance(event, EconomicRelease):
            search_text = f"{event.series_id} {event.name}".lower()
        else:
            parts = [
                event.get("series_id", ""),
                event.get("name", ""),
                event.get("title", ""),
            ]
            search_text = " ".join(parts).lower()

        for score, keywords in _SCORE_KEYWORDS:
            if any(kw.lower() in search_text for kw in keywords):
                return score
        return 30   # default low score for unrecognised events

    def get_economic_calendar_week(self) -> list[dict]:
        """
        Return upcoming economic data releases for the next 5 business days.
        Uses FRED release dates API for each tracked series.
        Falls back to frequency-based estimation if FRED_API_KEY is absent.
        Used by Sunday scheduler for the weekly market preview script.
        """
        today    = date.today()
        end_date = today + timedelta(days=7)
        upcoming: list[dict] = []

        if not self._api_key:
            return self._estimate_calendar(today, end_date)

        for series_id, (name, score, _freq) in SERIES_CATALOGUE.items():
            try:
                # Step 1: get the release ID for this series
                info = self._fred_series_info(series_id)
                release_id = info.get("release_id")

                if release_id:
                    # Step 2: query scheduled release dates from FRED
                    resp = requests.get(
                        FRED_RELEASE_DATES,
                        params={
                            "release_id":  release_id,
                            "api_key":     self._api_key,
                            "file_type":   "json",
                            "realtime_start": today.isoformat(),
                            "realtime_end":   end_date.isoformat(),
                            "include_release_dates_with_no_data": "true",
                        },
                        timeout=10,
                    )
                    if resp.ok:
                        for rd in resp.json().get("release_dates", []):
                            rd_str = rd.get("date", "")
                            if today.isoformat() <= rd_str <= end_date.isoformat():
                                upcoming.append({
                                    "series_id":          series_id,
                                    "name":               name,
                                    "release_date":       rd_str,
                                    "significance_score": score,
                                    "source":             "FRED",
                                })
                        continue  # go to next series

                # Fallback: estimate from last update date + frequency
                upcoming.extend(self._estimate_series_next(series_id, name, score, today, end_date))

            except Exception as exc:
                logger.debug("Calendar lookup failed for %s: %s", series_id, exc)

        upcoming.sort(key=lambda x: (x["release_date"], -x["significance_score"]))
        logger.info("Economic calendar: %d events in next 7 days", len(upcoming))
        return upcoming

    def _estimate_calendar(self, today: date, end_date: date) -> list[dict]:
        """Frequency-based calendar estimation when no API key is available."""
        calendar: list[dict] = []
        for series_id, (name, score, freq_days) in SERIES_CATALOGUE.items():
            calendar.extend(
                self._estimate_series_next(series_id, name, score, today, end_date)
            )
        return sorted(calendar, key=lambda x: x["release_date"])

    def _estimate_series_next(
        self, series_id: str, name: str, score: int,
        today: date, end_date: date,
    ) -> list[dict]:
        """
        Estimate next release date from last_updated + typical frequency.
        Only adds to calendar if the estimated date falls within [today, end_date].
        """
        _freq = SERIES_CATALOGUE.get(series_id, ("", 0, 30))[2]
        try:
            info = self._fred_series_info(series_id) if self._api_key else {}
            last_str = str(info.get("last_updated", ""))[:10]
            last_dt  = date.fromisoformat(last_str) if last_str else today - timedelta(days=_freq)
            next_dt  = last_dt + timedelta(days=_freq)
            if today <= next_dt <= end_date:
                return [{
                    "series_id":          series_id,
                    "name":               name,
                    "release_date":       next_dt.isoformat(),
                    "significance_score": score,
                    "source":             "estimated",
                }]
        except Exception:
            pass
        return []


# ── Backward-compat module-level functions ────────────────────────────────────

def _fetch_fred_series(
    series_id: str,
    name: str,
    unit: str,
    api_key: str,
) -> Optional[EconomicIndicator]:
    """REST-based FRED fetch — kept for backward compatibility."""
    params = {
        "series_id":  series_id,
        "api_key":    api_key,
        "file_type":  "json",
        "sort_order": "desc",
        "limit":      3,
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(FRED_OBS_URL, params=params, timeout=10)
            resp.raise_for_status()
            obs   = [o for o in resp.json().get("observations", []) if o["value"] != "."]
            if not obs:
                return None

            val      = float(obs[0]["value"])
            prev_val = float(obs[1]["value"]) if len(obs) > 1 else None
            change   = round(val - prev_val, 4) if prev_val is not None else None
            change_pct = (
                round((change / abs(prev_val)) * 100, 4)
                if change is not None and prev_val
                else None
            )
            return EconomicIndicator(
                series_id=series_id, name=name,
                value=val, previous_value=prev_val,
                change=change, change_pct=change_pct,
                period=obs[0]["date"],
                unit=unit, frequency="Monthly", source="FRED",
            )
        except requests.RequestException as exc:
            wait = RETRY_BACKOFF_BASE ** attempt
            logger.warning("FRED %s attempt %d: %s — retry %.1fs", series_id, attempt + 1, exc, wait)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    return None


def _mock_economic_data() -> dict[str, EconomicIndicator]:
    logger.warning("FRED_API_KEY not set — using MOCK economic data")
    now = datetime.now().strftime("%Y-%m")
    return {
        "FEDFUNDS": EconomicIndicator("FEDFUNDS", "Fed Funds Rate",   4.25, 4.50, -0.25, -5.56,  now, "Percent",  "Monthly",   "MOCK"),
        "CPIAUCSL": EconomicIndicator("CPIAUCSL", "CPI Inflation",    2.8,  3.1,  -0.3,  -9.68,  now, "Percent",  "Monthly",   "MOCK"),
        "UNRATE":   EconomicIndicator("UNRATE",   "Unemployment Rate",4.1,  4.0,   0.1,   2.50,  now, "Percent",  "Monthly",   "MOCK"),
        "GDP":      EconomicIndicator("GDP",      "Real GDP Growth",  2.3,  2.1,   0.2,   9.52,  now, "Percent",  "Quarterly", "MOCK"),
        "DGS10":    EconomicIndicator("DGS10",    "10-Yr Treasury",   4.42, 4.30,  0.12,  2.79,  now, "Percent",  "Daily",     "MOCK"),
    }


def _build_surprises(indicators: dict[str, EconomicIndicator]) -> list[str]:
    surprises = []
    for ind in indicators.values():
        if ind.is_surprising:
            direction = "fell" if ind.change and ind.change < 0 else "rose"
            surprises.append(
                f"{ind.name} {direction} {abs(ind.change):.2f} {ind.unit} to "
                f"{ind.value} — significant shift as of {ind.period}"
            )
    return surprises


def scrape_economic_data() -> EconomicSnapshot:
    """
    Legacy entry point — returns EconomicSnapshot for backward compatibility.
    New code should use EconomicScraper directly.
    """
    logger.info("Scraping economic indicators (legacy path)")

    if FRED_API_KEY:
        indicators: dict[str, EconomicIndicator] = {}
        for series_id, name in FRED_SERIES.items():
            ind = _fetch_fred_series(series_id, name, "Percent", FRED_API_KEY)
            if ind:
                indicators[series_id] = ind
    else:
        indicators = _mock_economic_data()

    surprises = _build_surprises(indicators)
    return EconomicSnapshot(
        date=datetime.now().strftime("%Y-%m-%d"),
        indicators=indicators,
        fed_next_meeting=None,
        surprise_events=surprises,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = EconomicScraper()
    print("\n=== Today's FRED Releases ===")
    for r in scraper.get_todays_releases():
        print(f"  {r.name}: {r.latest_value} (score={r.significance_score})")
    print("\n=== Fed Statements (24h) ===")
    for s in scraper.get_fed_statements():
        print(f"  {s['title'][:80]}")
    print("\n=== Economic Calendar (7 days) ===")
    for e in scraper.get_economic_calendar_week():
        print(f"  {e['release_date']} — {e['name']} (score={e['significance_score']})")
