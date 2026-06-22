"""
Market data scraper — yfinance primary, Alpha Vantage fallback.
Fetches prices, movers, sector performance, and volume data.
"""
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional
import json

import yfinance as yf
import requests

from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class TickerSnapshot:
    symbol: str
    name: str
    price: float
    change: float
    change_pct: float
    volume: int
    avg_volume: int
    market_cap: Optional[float]
    day_high: float
    day_low: float
    week_52_high: float
    week_52_low: float
    pe_ratio: Optional[float]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def volume_ratio(self) -> float:
        """Volume vs average — >1.5 signals unusual activity."""
        if self.avg_volume == 0:
            return 0.0
        return round(self.volume / self.avg_volume, 2)

    @property
    def sentiment(self) -> str:
        if self.change_pct >= 2:
            return "strongly_bullish"
        if self.change_pct >= 0.5:
            return "bullish"
        if self.change_pct <= -2:
            return "strongly_bearish"
        if self.change_pct <= -0.5:
            return "bearish"
        return "neutral"


@dataclass
class MarketSummary:
    date: str
    sp500: TickerSnapshot
    nasdaq: TickerSnapshot
    dow: TickerSnapshot
    russell2000: TickerSnapshot
    vix: TickerSnapshot
    ten_year_yield: TickerSnapshot
    gold: TickerSnapshot
    bitcoin: TickerSnapshot
    top_gainers: list[TickerSnapshot]
    top_losers: list[TickerSnapshot]
    high_volume: list[TickerSnapshot]
    sector_performance: dict[str, float]
    market_breadth: dict[str, int]  # advancing, declining, unchanged

    def to_dict(self) -> dict:
        return asdict(self)

    def to_narrative(self) -> str:
        """Human-readable summary for the script generator."""
        sp = self.sp500
        nq = self.nasdaq
        lines = [
            f"=== MARKET SUMMARY — {self.date} ===",
            f"S&P 500: ${sp.price:,.2f} | {sp.change_pct:+.2f}% | Sentiment: {sp.sentiment.upper()}",
            f"Nasdaq: ${nq.price:,.2f} | {nq.change_pct:+.2f}%",
            f"Dow: ${self.dow.price:,.2f} | {self.dow.change_pct:+.2f}%",
            f"Russell 2000: ${self.russell2000.price:,.2f} | {self.russell2000.change_pct:+.2f}%",
            f"VIX (Fear Index): {self.vix.price:.2f} — {'Elevated fear' if self.vix.price > 20 else 'Calm market'}",
            f"10-Year Treasury Yield: {self.ten_year_yield.price:.3f}%",
            f"Gold: ${self.gold.price:,.2f} | {self.gold.change_pct:+.2f}%",
            f"Bitcoin: ${self.bitcoin.price:,.2f} | {self.bitcoin.change_pct:+.2f}%",
            "",
            "TOP GAINERS:",
        ]
        for g in self.top_gainers[:3]:
            lines.append(f"  {g.symbol}: +{g.change_pct:.2f}% (${g.price:.2f}) — Vol ratio: {g.volume_ratio}x")
        lines.append("TOP LOSERS:")
        for l in self.top_losers[:3]:
            lines.append(f"  {l.symbol}: {l.change_pct:.2f}% (${l.price:.2f})")
        lines.append("\nSECTOR PERFORMANCE:")
        for sector, pct in sorted(self.sector_performance.items(), key=lambda x: x[1], reverse=True):
            bar = "▲" if pct > 0 else "▼"
            lines.append(f"  {bar} {sector}: {pct:+.2f}%")
        return "\n".join(lines)


SECTOR_ETFS = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financials": "XLF",
    "Energy": "XLE",
    "Consumer Disc.": "XLY",
    "Industrials": "XLI",
    "Communication": "XLC",
    "Real Estate": "XLRE",
    "Utilities": "XLU",
    "Materials": "XLB",
    "Consumer Staples": "XLP",
}

MAG7 = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL"]


def _fetch_ticker(symbol: str, retries: int = 3) -> Optional[TickerSnapshot]:
    """Fetch a single ticker with retry logic."""
    for attempt in range(retries):
        try:
            t = yf.Ticker(symbol)
            info = t.fast_info
            hist = t.history(period="2d", interval="1d")
            if hist.empty:
                logger.warning("No history for %s", symbol)
                return None

            prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else float(hist["Close"].iloc[-1])
            curr_close = float(hist["Close"].iloc[-1])
            change = curr_close - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            return TickerSnapshot(
                symbol=symbol,
                name=getattr(info, "display_name", symbol),
                price=curr_close,
                change=round(change, 4),
                change_pct=round(change_pct, 4),
                volume=int(hist["Volume"].iloc[-1]),
                avg_volume=int(getattr(info, "three_month_average_volume", 0) or 0),
                market_cap=getattr(info, "market_cap", None),
                day_high=float(hist["High"].iloc[-1]),
                day_low=float(hist["Low"].iloc[-1]),
                week_52_high=getattr(info, "fifty_two_week_high", 0.0),
                week_52_low=getattr(info, "fifty_two_week_low", 0.0),
                pe_ratio=getattr(info, "pe_ratio", None),
            )
        except Exception as exc:
            wait = settings.retry_backoff_base ** attempt
            logger.warning("Attempt %d for %s failed: %s — retrying in %.1fs", attempt + 1, symbol, exc, wait)
            time.sleep(wait)
    logger.error("All retries exhausted for %s", symbol)
    return None


def _fetch_sector_performance() -> dict[str, float]:
    result = {}
    for sector, etf in SECTOR_ETFS.items():
        snap = _fetch_ticker(etf)
        if snap:
            result[sector] = snap.change_pct
    return result


def _fetch_movers(symbols: list[str]) -> tuple[list[TickerSnapshot], list[TickerSnapshot], list[TickerSnapshot]]:
    """Return gainers, losers, high-volume from a list of symbols."""
    snapshots = []
    for sym in symbols:
        s = _fetch_ticker(sym)
        if s:
            snapshots.append(s)

    gainers = sorted(snapshots, key=lambda x: x.change_pct, reverse=True)[:5]
    losers = sorted(snapshots, key=lambda x: x.change_pct)[:5]
    high_vol = sorted(snapshots, key=lambda x: x.volume_ratio, reverse=True)[:5]
    return gainers, losers, high_vol


def scrape_market() -> MarketSummary:
    """Main entry point — collect full market snapshot."""
    logger.info("Starting market data scrape")
    today = datetime.now().strftime("%Y-%m-%d %A")

    indices = {
        "SPY": "sp500",
        "QQQ": "nasdaq",
        "DIA": "dow",
        "IWM": "russell2000",
        "^VIX": "vix",
        "^TNX": "ten_year_yield",
        "GLD": "gold",
        "BTC-USD": "bitcoin",
    }

    index_data = {}
    for sym, key in indices.items():
        snap = _fetch_ticker(sym)
        if snap is None:
            raise RuntimeError(f"Critical ticker {sym} failed to fetch")
        index_data[key] = snap
        logger.debug("Fetched %s: %.2f (%+.2f%%)", sym, snap.price, snap.change_pct)

    gainers, losers, high_vol = _fetch_movers(settings.tracked_tickers)
    sectors = _fetch_sector_performance()

    # Simple breadth estimation from S&P 500 components (use sector ETFs as proxy)
    advancing = sum(1 for v in sectors.values() if v > 0)
    declining = sum(1 for v in sectors.values() if v < 0)

    summary = MarketSummary(
        date=today,
        sp500=index_data["sp500"],
        nasdaq=index_data["nasdaq"],
        dow=index_data["dow"],
        russell2000=index_data["russell2000"],
        vix=index_data["vix"],
        ten_year_yield=index_data["ten_year_yield"],
        gold=index_data["gold"],
        bitcoin=index_data["bitcoin"],
        top_gainers=gainers,
        top_losers=losers,
        high_volume=high_vol,
        sector_performance=sectors,
        market_breadth={"advancing": advancing, "declining": declining, "unchanged": 11 - advancing - declining},
    )

    logger.info("Market scrape complete — S&P: %+.2f%%, VIX: %.2f", summary.sp500.change_pct, summary.vix.price)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    m = scrape_market()
    print(m.to_narrative())
