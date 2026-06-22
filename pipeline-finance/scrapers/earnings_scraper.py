"""
Earnings calendar scraper — tracks upcoming and recent earnings releases.
Primary: yfinance. Enriched with headline sentiment.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class EarningsEvent:
    symbol: str
    company: str
    report_date: str
    report_time: str          # "BMO" (before market open) | "AMC" (after close) | "Unknown"
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    revenue_estimate: Optional[float]
    revenue_actual: Optional[float]
    surprise_pct: Optional[float]     # EPS beat/miss percentage
    guidance: Optional[str]           # "raised" | "lowered" | "maintained" | None
    after_hours_move: Optional[float] # % price move after release

    @property
    def beat_miss(self) -> str:
        if self.surprise_pct is None:
            return "pending"
        if self.surprise_pct >= 5:
            return "strong_beat"
        if self.surprise_pct >= 1:
            return "beat"
        if self.surprise_pct <= -5:
            return "strong_miss"
        if self.surprise_pct <= -1:
            return "miss"
        return "in_line"

    @property
    def headline(self) -> str:
        if self.eps_actual is None:
            est = f"Est. EPS: ${self.eps_estimate:.2f}" if self.eps_estimate else "Reporting soon"
            return f"{self.company} ({self.symbol}) — {est} | Reports {self.report_date} {self.report_time}"
        beat_label = {"strong_beat": "🟢 STRONG BEAT", "beat": "🟢 Beat",
                      "in_line": "🟡 In-Line", "miss": "🔴 Miss", "strong_miss": "🔴 STRONG MISS"}.get(self.beat_miss, "")
        return (
            f"{self.company} ({self.symbol}): {beat_label} | "
            f"EPS ${self.eps_actual:.2f} vs est ${self.eps_estimate:.2f} | "
            f"Surprise: {self.surprise_pct:+.1f}%"
        )


@dataclass
class EarningsCalendar:
    date: str
    upcoming: list[EarningsEvent]     # next 5 trading days
    recent_beats: list[EarningsEvent] # reported in last 2 days, beat
    recent_misses: list[EarningsEvent]
    highlight_events: list[EarningsEvent]  # high-cap, high-interest

    def to_narrative(self) -> str:
        lines = [f"=== EARNINGS CALENDAR — {self.date} ==="]
        if self.recent_beats:
            lines.append("\nRECENT EARNINGS BEATS:")
            for e in self.recent_beats[:3]:
                lines.append(f"  {e.headline}")
        if self.recent_misses:
            lines.append("\nRECENT EARNINGS MISSES:")
            for e in self.recent_misses[:3]:
                lines.append(f"  {e.headline}")
        if self.upcoming:
            lines.append("\nUPCOMING EARNINGS (Next 5 Days):")
            for e in self.upcoming[:5]:
                lines.append(f"  📅 {e.headline}")
        return "\n".join(lines)


# High-interest tickers we always track for earnings
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "NFLX",
    "JPM", "BAC", "GS", "MS", "WMT", "COST", "HD", "DIS", "AMD",
]


def _get_earnings_for_ticker(symbol: str) -> Optional[EarningsEvent]:
    """Fetch earnings data for a single ticker."""
    try:
        t = yf.Ticker(symbol)
        info = t.info
        cal = t.calendar

        company = info.get("shortName", symbol)
        report_date = None
        report_time = "Unknown"

        if cal is not None and not cal.empty:
            # calendar DataFrame has 'Earnings Date' row
            if "Earnings Date" in cal.index:
                ed = cal.loc["Earnings Date"]
                if hasattr(ed, "iloc"):
                    report_date = str(ed.iloc[0])[:10]
                else:
                    report_date = str(ed)[:10]

        # Get EPS data from quarterly financials
        eps_estimate = info.get("forwardEps")
        eps_actual = None
        surprise_pct = None

        hist_earnings = t.earnings_history
        if hist_earnings is not None and not hist_earnings.empty:
            latest = hist_earnings.iloc[0]
            eps_actual = latest.get("epsActual")
            eps_estimate_hist = latest.get("epsEstimate")
            if eps_actual and eps_estimate_hist and eps_estimate_hist != 0:
                surprise_pct = round((eps_actual - eps_estimate_hist) / abs(eps_estimate_hist) * 100, 2)

        if report_date is None and eps_actual is None:
            return None

        return EarningsEvent(
            symbol=symbol,
            company=company,
            report_date=report_date or "TBD",
            report_time=report_time,
            eps_estimate=eps_estimate,
            eps_actual=eps_actual,
            revenue_estimate=info.get("revenueEstimates", {}).get("avg") if isinstance(info.get("revenueEstimates"), dict) else None,
            revenue_actual=None,
            surprise_pct=surprise_pct,
            guidance=None,
            after_hours_move=None,
        )
    except Exception as exc:
        logger.debug("Could not fetch earnings for %s: %s", symbol, exc)
        return None


def scrape_earnings() -> EarningsCalendar:
    """Fetch earnings calendar for watchlist tickers."""
    logger.info("Scraping earnings calendar for %d tickers", len(WATCHLIST))
    today = datetime.now()
    today_str = today.strftime("%Y-%m-%d")
    cutoff_past = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    cutoff_future = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    all_events: list[EarningsEvent] = []
    for sym in WATCHLIST:
        event = _get_earnings_for_ticker(sym)
        if event:
            all_events.append(event)
        time.sleep(0.2)  # polite rate limiting

    upcoming = [e for e in all_events
                if e.report_date > today_str and e.report_date <= cutoff_future]
    recent = [e for e in all_events
              if e.report_date >= cutoff_past and e.report_date <= today_str
              and e.eps_actual is not None]

    beats = [e for e in recent if e.beat_miss in ("beat", "strong_beat")]
    misses = [e for e in recent if e.beat_miss in ("miss", "strong_miss")]

    # Sort upcoming by date
    upcoming.sort(key=lambda e: e.report_date)

    # Highlight = upcoming big-cap reports
    highlight = [e for e in upcoming if e.symbol in ["AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL"]]

    calendar = EarningsCalendar(
        date=today_str,
        upcoming=upcoming,
        recent_beats=sorted(beats, key=lambda e: abs(e.surprise_pct or 0), reverse=True),
        recent_misses=sorted(misses, key=lambda e: abs(e.surprise_pct or 0), reverse=True),
        highlight_events=highlight,
    )

    logger.info("Earnings scrape: %d upcoming, %d recent beats, %d misses",
                len(upcoming), len(beats), len(misses))
    return calendar


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cal = scrape_earnings()
    print(cal.to_narrative())
