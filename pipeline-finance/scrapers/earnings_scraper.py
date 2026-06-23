"""
scrapers/earnings_scraper.py — DriftWire326
Earnings calendar from yfinance — tracks upcoming reports and
recent beats/misses for the high-interest watchlist.
"""
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# High-interest tickers — always tracked for earnings
WATCHLIST = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "NFLX",
    "JPM", "BAC", "GS", "MS", "WMT", "COST", "HD", "DIS", "AMD", "INTC",
]

_BEAT_MISS_LABELS = {
    "strong_beat": "🟢 STRONG BEAT",
    "beat":        "🟢 Beat",
    "in_line":     "🟡 In-Line",
    "miss":        "🔴 Miss",
    "strong_miss": "🔴 STRONG MISS",
    "pending":     "⏳ Pending",
}


# ── Data models ─────────────────────────────────────────────────────────────

@dataclass
class EarningsEvent:
    symbol: str
    company: str
    report_date: str       # ISO date "2026-05-01"
    report_time: str       # "BMO" | "AMC" | "Unknown"
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    revenue_estimate: Optional[float]
    revenue_actual: Optional[float]
    surprise_pct: Optional[float]
    guidance: Optional[str]           # "raised" | "lowered" | "maintained"
    after_hours_move: Optional[float] # % price move post-release

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
    def is_newsworthy(self) -> bool:
        """Strong beat/miss is always worth covering on the channel."""
        return self.beat_miss in ("strong_beat", "strong_miss")

    @property
    def headline(self) -> str:
        label = _BEAT_MISS_LABELS.get(self.beat_miss, "")
        if self.eps_actual is None:
            est = f"Est. EPS ${self.eps_estimate:.2f}" if self.eps_estimate else "EPS TBD"
            return f"{self.company} ({self.symbol}) — {est} | Reports {self.report_date} {self.report_time}"
        eps_est_str = f"${self.eps_estimate:.2f}" if self.eps_estimate else "N/A"
        surprise_str = f"{self.surprise_pct:+.1f}%" if self.surprise_pct is not None else "N/A"
        ah = f" | AH move: {self.after_hours_move:+.2f}%" if self.after_hours_move else ""
        return (
            f"{self.company} ({self.symbol}): {label} | "
            f"EPS ${self.eps_actual:.2f} vs est {eps_est_str} | "
            f"Surprise: {surprise_str}{ah}"
        )

    @property
    def script_anchor(self) -> str:
        """One-liner anchor number for script generation."""
        if self.surprise_pct is not None:
            direction = "beat" if self.surprise_pct > 0 else "missed"
            return f"{self.company} {direction} EPS estimates by {abs(self.surprise_pct):.1f}%"
        return f"{self.company} reports on {self.report_date}"


@dataclass
class EarningsCalendar:
    date: str
    upcoming: list[EarningsEvent]
    recent_beats: list[EarningsEvent]
    recent_misses: list[EarningsEvent]
    highlight_events: list[EarningsEvent]   # big-cap, high-interest

    def to_narrative(self) -> str:
        lines = [f"=== EARNINGS CALENDAR — {self.date} ==="]
        if self.recent_beats:
            lines.append("\nRECENT BEATS:")
            for e in self.recent_beats[:3]:
                lines.append(f"  {e.headline}")
        if self.recent_misses:
            lines.append("\nRECENT MISSES:")
            for e in self.recent_misses[:3]:
                lines.append(f"  {e.headline}")
        if self.upcoming:
            lines.append("\nUPCOMING (next 7 days):")
            for e in self.upcoming[:5]:
                lines.append(f"  📅 {e.headline}")
        return "\n".join(lines)


# ── Fetch helpers ────────────────────────────────────────────────────────────

def _get_earnings_event(symbol: str) -> Optional[EarningsEvent]:
    try:
        t = yf.Ticker(symbol)
        info = t.info
        company = info.get("shortName", symbol)

        # Report date from calendar
        report_date: Optional[str] = None
        report_time = "Unknown"
        try:
            cal = t.calendar
            if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                ed = cal.loc["Earnings Date"]
                report_date = str(ed.iloc[0] if hasattr(ed, "iloc") else ed)[:10]
        except Exception:
            pass

        # EPS data from earnings history
        eps_estimate: Optional[float] = info.get("forwardEps")
        eps_actual: Optional[float] = None
        surprise_pct: Optional[float] = None

        try:
            hist = t.earnings_history
            if hist is not None and not hist.empty:
                row = hist.iloc[0]
                eps_actual = row.get("epsActual")
                est_hist   = row.get("epsEstimate")
                if eps_actual is not None and est_hist and est_hist != 0:
                    surprise_pct = round(
                        (eps_actual - est_hist) / abs(est_hist) * 100, 2
                    )
        except Exception:
            pass

        if report_date is None and eps_actual is None:
            return None

        return EarningsEvent(
            symbol=symbol,
            company=company,
            report_date=report_date or "TBD",
            report_time=report_time,
            eps_estimate=eps_estimate,
            eps_actual=eps_actual,
            revenue_estimate=None,
            revenue_actual=None,
            surprise_pct=surprise_pct,
            guidance=None,
            after_hours_move=None,
        )
    except Exception as exc:
        logger.debug("Earnings fetch failed for %s: %s", symbol, exc)
        return None


# ── Public entry point ───────────────────────────────────────────────────────

def scrape_earnings() -> EarningsCalendar:
    logger.info("Scraping earnings for %d tickers", len(WATCHLIST))
    today = datetime.now()
    today_str    = today.strftime("%Y-%m-%d")
    cutoff_past  = (today - timedelta(days=3)).strftime("%Y-%m-%d")
    cutoff_ahead = (today + timedelta(days=7)).strftime("%Y-%m-%d")

    all_events: list[EarningsEvent] = []
    for sym in WATCHLIST:
        ev = _get_earnings_event(sym)
        if ev:
            all_events.append(ev)
        time.sleep(0.2)   # polite rate-limiting

    upcoming = sorted(
        [e for e in all_events if today_str < e.report_date <= cutoff_ahead],
        key=lambda e: e.report_date,
    )
    recent = [
        e for e in all_events
        if cutoff_past <= e.report_date <= today_str and e.eps_actual is not None
    ]

    beats  = sorted(
        [e for e in recent if e.beat_miss in ("beat", "strong_beat")],
        key=lambda e: abs(e.surprise_pct or 0), reverse=True,
    )
    misses = sorted(
        [e for e in recent if e.beat_miss in ("miss", "strong_miss")],
        key=lambda e: abs(e.surprise_pct or 0), reverse=True,
    )
    highlights = [
        e for e in upcoming
        if e.symbol in {"AAPL", "MSFT", "NVDA", "TSLA", "META", "GOOGL", "AMZN"}
    ]

    logger.info(
        "Earnings: %d upcoming | %d beats | %d misses",
        len(upcoming), len(beats), len(misses),
    )
    return EarningsCalendar(
        date=today_str,
        upcoming=upcoming,
        recent_beats=beats,
        recent_misses=misses,
        highlight_events=highlights,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(scrape_earnings().to_narrative())
