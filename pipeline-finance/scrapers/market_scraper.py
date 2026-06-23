"""
scrapers/market_scraper.py — DriftWire326
Market data via yfinance with retry logic, tier classification,
and breakout story detection for script generation.
"""
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional

import yfinance as yf

from config.settings import (
    STORY_TIERS, TRACKED_TICKERS,
    MAX_RETRIES, RETRY_BACKOFF_BASE,
)

logger = logging.getLogger(__name__)

# ── Sector ETF map ──────────────────────────────────────────────────────────
SECTOR_ETFS = {
    "Technology":       "XLK",
    "Healthcare":       "XLV",
    "Financials":       "XLF",
    "Energy":           "XLE",
    "Consumer Disc.":   "XLY",
    "Industrials":      "XLI",
    "Communication":    "XLC",
    "Real Estate":      "XLRE",
    "Utilities":        "XLU",
    "Materials":        "XLB",
    "Consumer Staples": "XLP",
}


# ── Data models ─────────────────────────────────────────────────────────────

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

    @property
    def story_tier(self) -> str:
        """Classify this ticker as a tier1/tier2/tier3 story by absolute move."""
        abs_move = abs(self.change_pct)
        for tier_id in ("tier1", "tier2", "tier3"):
            if abs_move >= STORY_TIERS[tier_id]["min_move_pct"]:
                return tier_id
        return "tier3"

    def to_headline(self) -> str:
        direction = "surged" if self.change_pct > 0 else "dropped"
        return (
            f"{self.name} ({self.symbol}) {direction} {abs(self.change_pct):.2f}% "
            f"to ${self.price:,.2f} | Vol ratio: {self.volume_ratio:.1f}x avg"
        )


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
    market_breadth: dict[str, int]

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def market_tier(self) -> str:
        """Overall market story tier based on S&P 500 move."""
        return self.sp500.story_tier

    @property
    def top_story(self) -> TickerSnapshot:
        """Single most newsworthy ticker today."""
        candidates = self.top_gainers[:3] + self.top_losers[:3]
        return max(candidates, key=lambda t: abs(t.change_pct)) if candidates else self.sp500

    def to_narrative(self) -> str:
        sp = self.sp500
        nq = self.nasdaq
        lines = [
            f"=== MARKET SUMMARY — {self.date} ===",
            f"Overall Tier: {self.market_tier.upper()} | Story: {self.top_story.to_headline()}",
            "",
            f"S&P 500:      ${sp.price:,.2f}  |  {sp.change_pct:+.2f}%  |  {sp.sentiment.upper()}",
            f"Nasdaq:       ${nq.price:,.2f}  |  {nq.change_pct:+.2f}%",
            f"Dow Jones:    ${self.dow.price:,.2f}  |  {self.dow.change_pct:+.2f}%",
            f"Russell 2000: ${self.russell2000.price:,.2f}  |  {self.russell2000.change_pct:+.2f}%",
            f"VIX:          {self.vix.price:.2f}  |  {'ELEVATED FEAR' if self.vix.price > 25 else 'Moderate' if self.vix.price > 18 else 'Calm'}",
            f"10-Yr Yield:  {self.ten_year_yield.price:.3f}%",
            f"Gold:         ${self.gold.price:,.2f}  |  {self.gold.change_pct:+.2f}%",
            f"Bitcoin:      ${self.bitcoin.price:,.2f}  |  {self.bitcoin.change_pct:+.2f}%",
            "",
            "TOP GAINERS:",
        ]
        for g in self.top_gainers[:5]:
            lines.append(f"  ▲ {g.symbol}: {g.change_pct:+.2f}% (${g.price:.2f}) — "
                         f"Vol {g.volume_ratio:.1f}x — {STORY_TIERS[g.story_tier]['label'].upper()}")
        lines.append("TOP LOSERS:")
        for lo in self.top_losers[:5]:
            lines.append(f"  ▼ {lo.symbol}: {lo.change_pct:+.2f}% (${lo.price:.2f}) — "
                         f"Vol {lo.volume_ratio:.1f}x — {STORY_TIERS[lo.story_tier]['label'].upper()}")
        lines.append("\nSECTOR PERFORMANCE:")
        for sector, pct in sorted(self.sector_performance.items(), key=lambda x: x[1], reverse=True):
            bar = "▲" if pct > 0 else "▼"
            lines.append(f"  {bar} {sector}: {pct:+.2f}%")
        breadth = self.market_breadth
        lines.append(
            f"\nMARKET BREADTH: ▲{breadth['advancing']} advancing  "
            f"▼{breadth['declining']} declining  "
            f"—{breadth['unchanged']} unchanged"
        )
        return "\n".join(lines)


# ── Core fetch logic ─────────────────────────────────────────────────────────

def _fetch_ticker(symbol: str) -> Optional[TickerSnapshot]:
    """Fetch a single ticker with exponential-backoff retry."""
    for attempt in range(MAX_RETRIES):
        try:
            t = yf.Ticker(symbol)
            info = t.fast_info
            hist = t.history(period="2d", interval="1d")
            if hist.empty:
                logger.warning("No history data for %s", symbol)
                return None

            curr_close = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) >= 2 else curr_close
            change = curr_close - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0

            return TickerSnapshot(
                symbol=symbol,
                name=getattr(info, "display_name", symbol),
                price=round(curr_close, 4),
                change=round(change, 4),
                change_pct=round(change_pct, 4),
                volume=int(hist["Volume"].iloc[-1]),
                avg_volume=int(getattr(info, "three_month_average_volume", 0) or 0),
                market_cap=getattr(info, "market_cap", None),
                day_high=float(hist["High"].iloc[-1]),
                day_low=float(hist["Low"].iloc[-1]),
                week_52_high=float(getattr(info, "fifty_two_week_high", 0) or 0),
                week_52_low=float(getattr(info, "fifty_two_week_low", 0) or 0),
                pe_ratio=getattr(info, "pe_ratio", None),
            )
        except Exception as exc:
            wait = RETRY_BACKOFF_BASE ** attempt
            logger.warning("Attempt %d/%d for %s failed: %s — retry in %.1fs",
                           attempt + 1, MAX_RETRIES, symbol, exc, wait)
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    logger.error("All retries exhausted for %s", symbol)
    return None


def _fetch_sector_performance() -> dict[str, float]:
    result: dict[str, float] = {}
    for sector, etf in SECTOR_ETFS.items():
        snap = _fetch_ticker(etf)
        if snap:
            result[sector] = snap.change_pct
    return result


def _fetch_movers(
    symbols: list[str],
) -> tuple[list[TickerSnapshot], list[TickerSnapshot], list[TickerSnapshot]]:
    """Return (gainers, losers, high_volume) from a symbol list."""
    snapshots: list[TickerSnapshot] = []
    for sym in symbols:
        if sym.startswith("^"):
            continue  # skip indices in mover scan
        snap = _fetch_ticker(sym)
        if snap:
            snapshots.append(snap)

    gainers  = sorted(snapshots, key=lambda x: x.change_pct, reverse=True)[:5]
    losers   = sorted(snapshots, key=lambda x: x.change_pct)[:5]
    high_vol = sorted(snapshots, key=lambda x: x.volume_ratio, reverse=True)[:5]
    return gainers, losers, high_vol


# ── Public entry point ───────────────────────────────────────────────────────

def scrape_market() -> MarketSummary:
    """Collect a full market snapshot. Raises RuntimeError if critical tickers fail."""
    logger.info("Starting market data scrape")
    today = datetime.now().strftime("%Y-%m-%d %A")

    index_map = {
        "SPY":   "sp500",
        "QQQ":   "nasdaq",
        "DIA":   "dow",
        "IWM":   "russell2000",
        "^VIX":  "vix",
        "^TNX":  "ten_year_yield",
        "GLD":   "gold",
        "BTC-USD": "bitcoin",
    }

    index_data: dict[str, TickerSnapshot] = {}
    for sym, key in index_map.items():
        snap = _fetch_ticker(sym)
        if snap is None:
            raise RuntimeError(f"Critical ticker {sym} failed — aborting scrape")
        index_data[key] = snap
        logger.debug("Fetched %s: %.4f (%+.2f%%)", sym, snap.price, snap.change_pct)

    gainers, losers, high_vol = _fetch_movers(TRACKED_TICKERS)
    sectors = _fetch_sector_performance()

    advancing = sum(1 for v in sectors.values() if v > 0)
    declining  = sum(1 for v in sectors.values() if v < 0)
    unchanged  = len(sectors) - advancing - declining

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
        market_breadth={"advancing": advancing, "declining": declining, "unchanged": unchanged},
    )

    logger.info(
        "Market scrape complete — S&P: %+.2f%% | Tier: %s | Top: %s",
        summary.sp500.change_pct, summary.market_tier, summary.top_story.symbol,
    )
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(scrape_market().to_narrative())
