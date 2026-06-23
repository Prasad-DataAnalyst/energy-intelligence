"""
scrapers/earnings_scraper.py — DriftWire326
Earnings intelligence from SEC EDGAR 8-K filings + Finnhub calendar.
Tracks today's reports, the week ahead, EPS surprises, and company news.
"""
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Union

import requests
import yfinance as yf

from config.settings import settings

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


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class EarningsEvent:
    symbol: str
    company: str
    report_date: str        # ISO date "2026-05-01"
    report_time: str        # "BMO" | "AMC" | "Unknown"
    eps_estimate: Optional[float]
    eps_actual: Optional[float]
    revenue_estimate: Optional[float]
    revenue_actual: Optional[float]
    surprise_pct: Optional[float]
    guidance: Optional[str]            # "raised" | "lowered" | "maintained"
    after_hours_move: Optional[float]  # % price move post-release

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


# ── Legacy functional helpers (backward-compat) ──────────────────────────────

def _get_earnings_event(symbol: str) -> Optional[EarningsEvent]:
    try:
        t = yf.Ticker(symbol)
        info = t.info
        company = info.get("shortName", symbol)

        report_date: Optional[str] = None
        report_time = "Unknown"
        try:
            cal = t.calendar
            if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                ed = cal.loc["Earnings Date"]
                report_date = str(ed.iloc[0] if hasattr(ed, "iloc") else ed)[:10]
        except Exception:
            pass

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


def scrape_earnings() -> EarningsCalendar:
    """Legacy entry point — returns EarningsCalendar from yfinance WATCHLIST."""
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
        time.sleep(0.2)

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


# ── EarningsScraper class ────────────────────────────────────────────────────

class EarningsScraper:
    """
    Earnings intelligence from SEC EDGAR 8-K filings + Finnhub calendar.
    """

    EDGAR_SEARCH_URL    = "https://efts.sec.gov/LATEST/search-index?"
    FINNHUB_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"
    FINNHUB_NEWS_URL     = "https://finnhub.io/api/v1/company-news"

    def __init__(
        self,
        api_key: str = "",
        output_dir: Optional["Path"] = None,
    ):
        from pathlib import Path
        if not api_key:
            api_key = settings.finnhub_api_key
        self.api_key = api_key
        self.output_dir = Path(output_dir) if output_dir else (settings.output_dir / "scripts")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._sp500_cache: Optional[set] = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_sp500_set(self) -> set:
        """Return set of S&P 500 tickers (cached per instance)."""
        if self._sp500_cache is not None:
            return self._sp500_cache
        try:
            import pandas as pd
            tables = pd.read_html(
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                attrs={"id": "constituents"},
            )
            tickers = set(tables[0]["Symbol"].str.replace(".", "-", regex=False).tolist())
            if len(tickers) > 400:
                self._sp500_cache = tickers
                return tickers
        except Exception as exc:
            logger.warning("Wikipedia S&P 500 fetch failed: %s", exc)
        self._sp500_cache = set(WATCHLIST)
        return self._sp500_cache

    def _finnhub_get(self, url: str, params: dict) -> Optional[dict]:
        """Single Finnhub REST call with 1s pre-sleep for rate limiting."""
        if not self.api_key:
            return None
        time.sleep(1)
        params = {**params, "token": self.api_key}
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Finnhub call failed (%s): %s", url, exc)
            return None

    def _edgar_todays_8k(self, today_str: str) -> list[dict]:
        """Return SEC EDGAR earnings-related 8-K filings from today."""
        params = {
            "q": "earnings",
            "forms": "8-K",
            "dateRange": "custom",
            "startdt": today_str,
            "enddt": today_str,
        }
        headers = {"User-Agent": "DriftWire326 research@driftwire326.com"}
        for attempt in range(2):
            try:
                resp = requests.get(
                    self.EDGAR_SEARCH_URL,
                    params=params,
                    headers=headers,
                    timeout=30,
                )
                resp.raise_for_status()
                hits = resp.json().get("hits", {}).get("hits", [])
                results = []
                for hit in hits:
                    src = hit.get("_source", {})
                    results.append({
                        "symbol": src.get("ticker", ""),
                        "company": src.get("entity_name", ""),
                        "filed_at": src.get("file_date", today_str),
                        "form_type": src.get("form_type", "8-K"),
                        "description": src.get("period_of_report", ""),
                    })
                return results
            except Exception as exc:
                if attempt == 0:
                    logger.warning("SEC EDGAR attempt 1 failed: %s — retry in 60s", exc)
                    time.sleep(60)
                else:
                    logger.error("SEC EDGAR fetch failed after retry: %s", exc)
        return []

    def _market_cap_score(self, symbol: str) -> int:
        """Fetch market cap from yfinance and return tiered score (100/60/20)."""
        try:
            info = yf.Ticker(symbol).fast_info
            mc = getattr(info, "market_cap", None) or 0
            if mc >= 100_000_000_000:   # ≥$100B
                return 100
            if mc >= 10_000_000_000:    # ≥$10B
                return 60
            return 20
        except Exception:
            return 60   # default mid-tier when lookup fails

    def _save_json(self, data: dict) -> "Path":
        from pathlib import Path
        today_str = datetime.now().strftime("%Y%m%d")
        path = self.output_dir / f"earnings_{today_str}.json"
        path.write_text(json.dumps(data, indent=2, default=str))
        logger.info("Saved earnings data → %s", path)
        return path

    # ── Public API ────────────────────────────────────────────────────────────

    def get_todays_earnings(self) -> EarningsCalendar:
        """
        Fetch today's earnings from Finnhub calendar + SEC EDGAR 8-K filings.
        Returns EarningsCalendar with beats/misses/upcoming populated.
        """
        today_str = datetime.now().strftime("%Y-%m-%d")
        logger.info("Fetching today's earnings — %s", today_str)

        events: dict[str, EarningsEvent] = {}

        # Source 1: Finnhub earnings calendar
        if self.api_key:
            data = self._finnhub_get(
                self.FINNHUB_CALENDAR_URL,
                {"from": today_str, "to": today_str},
            )
            for entry in (data or {}).get("earningsCalendar", []):
                sym = (entry.get("symbol") or "").upper()
                if not sym:
                    continue
                eps_est = entry.get("epsEstimate")
                eps_act = entry.get("epsActual")
                rev_est = entry.get("revenueEstimate")
                rev_act = entry.get("revenueActual")
                surprise_pct: Optional[float] = None
                if eps_act is not None and eps_est and eps_est != 0:
                    surprise_pct = round((eps_act - eps_est) / abs(eps_est) * 100, 2)
                hour = (entry.get("hour") or "unknown").upper()
                report_time = "BMO" if hour in ("BTO", "BMO") else "AMC" if hour == "AMC" else "Unknown"
                events[sym] = EarningsEvent(
                    symbol=sym,
                    company=entry.get("name", sym),
                    report_date=entry.get("date", today_str),
                    report_time=report_time,
                    eps_estimate=eps_est,
                    eps_actual=eps_act,
                    revenue_estimate=rev_est,
                    revenue_actual=rev_act,
                    surprise_pct=surprise_pct,
                    guidance=None,
                    after_hours_move=None,
                )

        # Source 2: SEC EDGAR 8-K filings (supplement only — no EPS data)
        for hit in self._edgar_todays_8k(today_str):
            sym = (hit.get("symbol") or "").upper()
            if sym and sym not in events:
                events[sym] = EarningsEvent(
                    symbol=sym,
                    company=hit.get("company", sym),
                    report_date=today_str,
                    report_time="Unknown",
                    eps_estimate=None, eps_actual=None,
                    revenue_estimate=None, revenue_actual=None,
                    surprise_pct=None, guidance=None, after_hours_move=None,
                )

        all_events = list(events.values())
        recent   = [e for e in all_events if e.eps_actual is not None]
        upcoming = [e for e in all_events if e.eps_actual is None]

        beats = sorted(
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

        calendar = EarningsCalendar(
            date=today_str,
            upcoming=upcoming,
            recent_beats=beats,
            recent_misses=misses,
            highlight_events=highlights,
        )

        self._save_json({
            "date": today_str,
            "source": ["finnhub", "sec_edgar"],
            "total_events": len(all_events),
            "beats": len(beats),
            "misses": len(misses),
            "upcoming": len(upcoming),
            "events": [
                {
                    "symbol": e.symbol,
                    "company": e.company,
                    "report_date": e.report_date,
                    "report_time": e.report_time,
                    "eps_estimate": e.eps_estimate,
                    "eps_actual": e.eps_actual,
                    "surprise_pct": e.surprise_pct,
                    "beat_miss": e.beat_miss,
                    "after_hours_move": e.after_hours_move,
                }
                for e in all_events
            ],
        })
        return calendar

    def get_earnings_week(self) -> list[dict]:
        """
        Return earnings calendar for the next 5 trading days.
        Each dict: {date, symbol, company, report_time, eps_estimate,
                    revenue_estimate, quarter, year}
        """
        today = datetime.now()
        trading_days: list[str] = []
        d = today
        while len(trading_days) < 5:
            d += timedelta(days=1)
            if d.weekday() < 5:
                trading_days.append(d.strftime("%Y-%m-%d"))

        from_dt = trading_days[0]
        to_dt   = trading_days[-1]
        logger.info("Fetching earnings week %s → %s", from_dt, to_dt)

        if self.api_key:
            data = self._finnhub_get(
                self.FINNHUB_CALENDAR_URL,
                {"from": from_dt, "to": to_dt},
            )
            week = []
            for entry in (data or {}).get("earningsCalendar", []):
                sym  = (entry.get("symbol") or "").upper()
                hour = (entry.get("hour") or "").upper()
                report_time = "BMO" if hour in ("BTO", "BMO") else "AMC" if hour == "AMC" else "Unknown"
                week.append({
                    "date": entry.get("date"),
                    "symbol": sym,
                    "company": entry.get("name", sym),
                    "report_time": report_time,
                    "eps_estimate": entry.get("epsEstimate"),
                    "revenue_estimate": entry.get("revenueEstimate"),
                    "quarter": entry.get("quarter"),
                    "year": entry.get("year"),
                })
            return sorted(week, key=lambda x: x.get("date") or "")

        # Fallback: WATCHLIST via yfinance when no Finnhub key
        week_events = []
        for sym in WATCHLIST:
            try:
                t = yf.Ticker(sym)
                cal = t.calendar
                if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                    ed = cal.loc["Earnings Date"]
                    ed_str = str(ed.iloc[0] if hasattr(ed, "iloc") else ed)[:10]
                    if ed_str in trading_days:
                        week_events.append({
                            "date": ed_str,
                            "symbol": sym,
                            "company": t.info.get("shortName", sym),
                            "report_time": "Unknown",
                            "eps_estimate": t.info.get("forwardEps"),
                            "revenue_estimate": None,
                            "quarter": None,
                            "year": None,
                        })
                time.sleep(0.3)
            except Exception as exc:
                logger.debug("yfinance calendar failed for %s: %s", sym, exc)
        return sorted(week_events, key=lambda x: x.get("date") or "")

    def filter_sp500_only(self, earnings_list: list[EarningsEvent]) -> list[EarningsEvent]:
        """Filter earnings list to S&P 500 constituents only."""
        sp500 = self._get_sp500_set()
        return [e for e in earnings_list if e.symbol in sp500]

    def score_earnings_event(self, event: EarningsEvent) -> float:
        """
        Score 0–100: EPS surprise (40%) + market cap tier (30%) + AH reaction (30%).
          eps_score = min(100, abs(surprise_pct) × 2)   — 50%+ surprise → 100
          mc_score  = 100 (≥$100B) | 60 ($10–100B) | 20 (<$10B)
          ah_score  = min(100, abs(after_hours_move) × 5) — 20%+ move → 100
        """
        eps_score = 0.0
        if event.surprise_pct is not None:
            eps_score = min(100.0, abs(event.surprise_pct) * 2)

        mc_score = float(self._market_cap_score(event.symbol))

        ah_score = 0.0
        if event.after_hours_move is not None:
            ah_score = min(100.0, abs(event.after_hours_move) * 5)

        total = (eps_score * 0.40) + (mc_score * 0.30) + (ah_score * 0.30)
        return round(min(100.0, total), 2)

    def get_company_news(self, ticker: str) -> list[dict]:
        """
        Fetch last 24h company news from Finnhub. Returns top 3 headlines.
        Each dict: {headline, source, url, datetime, summary}
        """
        today   = datetime.now()
        from_dt = (today - timedelta(days=1)).strftime("%Y-%m-%d")
        to_dt   = today.strftime("%Y-%m-%d")

        data = self._finnhub_get(
            self.FINNHUB_NEWS_URL,
            {"symbol": ticker, "from": from_dt, "to": to_dt},
        )
        if not data:
            return []

        news = []
        for item in (data if isinstance(data, list) else [])[:3]:
            ts = item.get("datetime")
            news.append({
                "headline": item.get("headline", ""),
                "source":   item.get("source", ""),
                "url":      item.get("url", ""),
                "datetime": datetime.fromtimestamp(ts).isoformat() if ts else None,
                "summary":  (item.get("summary") or "")[:200],
            })
        return news


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = EarningsScraper()
    calendar = scraper.get_todays_earnings()
    print(calendar.to_narrative())
