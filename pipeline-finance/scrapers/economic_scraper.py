"""
Economic data scraper — FRED API + BLS + public data sources.
Tracks macro indicators: CPI, GDP, unemployment, Fed rate, etc.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional
import json

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"
BLS_BASE = "https://api.bls.gov/publicAPI/v2/timeseries/data/"

# Public fallback — no API key needed (limited to recent data)
FRED_PUBLIC_BASE = "https://api.stlouisfed.org/fred/series/observations"


@dataclass
class EconomicIndicator:
    series_id: str
    name: str
    value: float
    previous_value: Optional[float]
    change: Optional[float]
    change_pct: Optional[float]
    period: str          # e.g. "2026-05"
    unit: str            # e.g. "Percent", "Billions of Dollars"
    frequency: str       # Monthly, Quarterly, etc.
    source: str
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def trend(self) -> str:
        if self.change is None:
            return "unknown"
        if self.change > 0:
            return "rising"
        if self.change < 0:
            return "falling"
        return "flat"

    @property
    def narrative(self) -> str:
        direction = "rose" if self.change and self.change > 0 else "fell"
        if self.change is None:
            return f"{self.name}: {self.value} {self.unit}"
        return (
            f"{self.name} {direction} to {self.value} {self.unit} "
            f"({self.change:+.3f} from {self.previous_value})"
        )


@dataclass
class EconomicSnapshot:
    date: str
    indicators: dict[str, EconomicIndicator]
    fed_next_meeting: Optional[str]
    recent_fed_speeches: list[str]
    surprise_events: list[str]   # data that beat/missed estimates

    def to_narrative(self) -> str:
        lines = [f"=== ECONOMIC DATA — {self.date} ==="]
        for ind in self.indicators.values():
            lines.append(f"  • {ind.narrative}")
        if self.surprise_events:
            lines.append("\nDATA SURPRISES:")
            for s in self.surprise_events:
                lines.append(f"  ⚡ {s}")
        if self.fed_next_meeting:
            lines.append(f"\nNext Fed Meeting: {self.fed_next_meeting}")
        return "\n".join(lines)


def _fetch_fred_series(series_id: str, name: str, unit: str, api_key: str) -> Optional[EconomicIndicator]:
    """Fetch latest observation from FRED."""
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 3,
    }
    for attempt in range(settings.max_retries):
        try:
            resp = requests.get(FRED_BASE, params=params, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            obs = data.get("observations", [])
            if len(obs) < 1:
                logger.warning("No observations for %s", series_id)
                return None

            latest = obs[0]
            prev = obs[1] if len(obs) > 1 else None

            val = float(latest["value"]) if latest["value"] != "." else None
            prev_val = float(prev["value"]) if prev and prev["value"] != "." else None
            if val is None:
                return None

            change = round(val - prev_val, 4) if prev_val is not None else None
            change_pct = round((change / prev_val) * 100, 4) if change and prev_val else None

            return EconomicIndicator(
                series_id=series_id,
                name=name,
                value=val,
                previous_value=prev_val,
                change=change,
                change_pct=change_pct,
                period=latest["date"],
                unit=unit,
                frequency="Monthly",
                source="FRED",
            )
        except requests.RequestException as exc:
            wait = settings.retry_backoff_base ** attempt
            logger.warning("FRED request failed for %s (attempt %d): %s", series_id, attempt + 1, exc)
            time.sleep(wait)
    return None


def _fetch_mock_economic_data() -> dict[str, EconomicIndicator]:
    """
    Fallback mock data when API keys are absent — clearly marked as demo data.
    Used in development / testing only.
    """
    logger.warning("Using MOCK economic data — configure FRED_API_KEY for live data")
    now = datetime.now().strftime("%Y-%m")
    mock = {
        "FEDFUNDS": EconomicIndicator(
            series_id="FEDFUNDS", name="Fed Funds Rate", value=4.25, previous_value=4.50,
            change=-0.25, change_pct=-5.56, period=now, unit="Percent",
            frequency="Monthly", source="MOCK",
        ),
        "CPIAUCSL": EconomicIndicator(
            series_id="CPIAUCSL", name="CPI Inflation (YoY)", value=2.8, previous_value=3.1,
            change=-0.3, change_pct=-9.68, period=now, unit="Percent",
            frequency="Monthly", source="MOCK",
        ),
        "UNRATE": EconomicIndicator(
            series_id="UNRATE", name="Unemployment Rate", value=4.1, previous_value=4.0,
            change=0.1, change_pct=2.5, period=now, unit="Percent",
            frequency="Monthly", source="MOCK",
        ),
        "GDP": EconomicIndicator(
            series_id="GDP", name="GDP Growth (QoQ)", value=2.3, previous_value=2.1,
            change=0.2, change_pct=9.52, period=now, unit="Percent",
            frequency="Quarterly", source="MOCK",
        ),
    }
    return mock


def _detect_surprises(indicators: dict[str, EconomicIndicator]) -> list[str]:
    """Flag notable data beats/misses based on magnitude of change."""
    surprises = []
    thresholds = {
        "CPIAUCSL": 0.3,
        "UNRATE": 0.2,
        "GDP": 0.5,
        "FEDFUNDS": 0.25,
    }
    for sid, ind in indicators.items():
        thresh = thresholds.get(sid)
        if thresh and ind.change and abs(ind.change) >= thresh:
            direction = "beat expectations" if ind.change < 0 and sid == "CPIAUCSL" else "surprised markets"
            surprises.append(
                f"{ind.name} {direction} — {ind.change:+.2f} {ind.unit} move to {ind.value}"
            )
    return surprises


def scrape_economic_data() -> EconomicSnapshot:
    """Main entry — returns full economic snapshot."""
    logger.info("Scraping economic indicators")

    api_key = settings.fred_api_key
    indicators: dict[str, EconomicIndicator] = {}

    if api_key:
        for series_id, name in settings.fred_series.items():
            ind = _fetch_fred_series(series_id, name, "Percent", api_key)
            if ind:
                indicators[series_id] = ind
                logger.debug("Fetched %s: %.3f (%s)", series_id, ind.value, ind.trend)
    else:
        indicators = _fetch_mock_economic_data()

    surprises = _detect_surprises(indicators)

    snapshot = EconomicSnapshot(
        date=datetime.now().strftime("%Y-%m-%d"),
        indicators=indicators,
        fed_next_meeting=None,  # Could be fetched from Fed calendar API
        recent_fed_speeches=[],
        surprise_events=surprises,
    )

    logger.info("Economic scrape complete — %d indicators, %d surprises", len(indicators), len(surprises))
    return snapshot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    snap = scrape_economic_data()
    print(snap.to_narrative())
