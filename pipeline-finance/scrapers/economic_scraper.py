"""
scrapers/economic_scraper.py — DriftWire326
FRED API economic indicators with mock fallback.
Detects data surprises that become script material.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import requests

from config.settings import (
    FRED_API_KEY, FRED_SERIES,
    MAX_RETRIES, RETRY_BACKOFF_BASE,
)

logger = logging.getLogger(__name__)

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Magnitude thresholds that make a data point "surprising" enough for a video
_SURPRISE_THRESHOLDS = {
    "CPIAUCSL": 0.3,   # CPI   — 0.3pp move is significant
    "FEDFUNDS": 0.25,  # Fed   — any rate move is news
    "UNRATE":   0.2,   # UE    — 0.2pp spike matters
    "GDP":      0.5,   # GDP   — miss/beat by 0.5pp
    "T10YIE":   0.15,  # Breakeven inflation move
    "DGS10":    0.15,  # 10-yr yield move
    "UMCSENT":  5.0,   # Consumer sentiment — 5 point swing
}


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class EconomicIndicator:
    series_id: str
    name: str
    value: float
    previous_value: Optional[float]
    change: Optional[float]
    change_pct: Optional[float]
    period: str          # "2026-05"
    unit: str            # "Percent", "Billions of Dollars"
    frequency: str       # Monthly, Quarterly
    source: str
    last_updated: str = field(default_factory=lambda: datetime.now().isoformat())

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
    date: str
    indicators: dict[str, EconomicIndicator]
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


# ── FRED fetch ───────────────────────────────────────────────────────────────

def _fetch_fred_series(
    series_id: str,
    name: str,
    unit: str,
    api_key: str,
) -> Optional[EconomicIndicator]:
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "desc",
        "limit": 3,
    }
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(FRED_BASE, params=params, timeout=10)
            resp.raise_for_status()
            obs = resp.json().get("observations", [])
            if not obs:
                logger.warning("No FRED observations for %s", series_id)
                return None

            # Skip missing-value placeholders (".")
            valid = [o for o in obs if o["value"] != "."]
            if not valid:
                return None

            latest = valid[0]
            prev   = valid[1] if len(valid) > 1 else None
            val      = float(latest["value"])
            prev_val = float(prev["value"]) if prev else None
            change   = round(val - prev_val, 4) if prev_val is not None else None
            change_pct = (
                round((change / abs(prev_val)) * 100, 4)
                if change is not None and prev_val
                else None
            )

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
            wait = RETRY_BACKOFF_BASE ** attempt
            logger.warning("FRED %s attempt %d failed: %s — retry in %.1fs",
                           series_id, attempt + 1, exc, wait)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)
    return None


# ── Mock fallback ────────────────────────────────────────────────────────────

def _mock_economic_data() -> dict[str, EconomicIndicator]:
    logger.warning("FRED_API_KEY not set — using MOCK economic data")
    now = datetime.now().strftime("%Y-%m")
    return {
        "FEDFUNDS": EconomicIndicator(
            series_id="FEDFUNDS", name="Fed Funds Rate",
            value=4.25, previous_value=4.50,
            change=-0.25, change_pct=-5.56,
            period=now, unit="Percent", frequency="Monthly", source="MOCK",
        ),
        "CPIAUCSL": EconomicIndicator(
            series_id="CPIAUCSL", name="CPI Inflation (YoY)",
            value=2.8, previous_value=3.1,
            change=-0.3, change_pct=-9.68,
            period=now, unit="Percent", frequency="Monthly", source="MOCK",
        ),
        "UNRATE": EconomicIndicator(
            series_id="UNRATE", name="Unemployment Rate",
            value=4.1, previous_value=4.0,
            change=0.1, change_pct=2.5,
            period=now, unit="Percent", frequency="Monthly", source="MOCK",
        ),
        "GDP": EconomicIndicator(
            series_id="GDP", name="GDP Growth (QoQ)",
            value=2.3, previous_value=2.1,
            change=0.2, change_pct=9.52,
            period=now, unit="Percent", frequency="Quarterly", source="MOCK",
        ),
        "DGS10": EconomicIndicator(
            series_id="DGS10", name="10-Year Treasury Yield",
            value=4.42, previous_value=4.30,
            change=0.12, change_pct=2.79,
            period=now, unit="Percent", frequency="Daily", source="MOCK",
        ),
    }


def _build_surprises(indicators: dict[str, EconomicIndicator]) -> list[str]:
    surprises = []
    for ind in indicators.values():
        if ind.is_surprising:
            direction = "fell" if ind.change and ind.change < 0 else "rose"
            surprises.append(
                f"{ind.name} {direction} {abs(ind.change):.2f} {ind.unit} to "
                f"{ind.value} — data shows a significant shift as of {ind.period}"
            )
    return surprises


# ── Public entry point ───────────────────────────────────────────────────────

def scrape_economic_data() -> EconomicSnapshot:
    logger.info("Scraping economic indicators")

    if FRED_API_KEY:
        indicators: dict[str, EconomicIndicator] = {}
        for series_id, name in FRED_SERIES.items():
            ind = _fetch_fred_series(series_id, name, "Percent", FRED_API_KEY)
            if ind:
                indicators[series_id] = ind
                logger.debug("FRED %s: %.3f (%s)", series_id, ind.value, ind.trend)
    else:
        indicators = _mock_economic_data()

    surprises = _build_surprises(indicators)

    snapshot = EconomicSnapshot(
        date=datetime.now().strftime("%Y-%m-%d"),
        indicators=indicators,
        fed_next_meeting=None,
        surprise_events=surprises,
    )
    logger.info("Economic scrape: %d indicators, %d surprises", len(indicators), len(surprises))
    return snapshot


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(scrape_economic_data().to_narrative())
