"""
scrapers/market_scraper.py — DriftWire326
MarketScraper class: pre-market futures, close data, S&P 500 top movers,
price history, topic scoring, and market status detection.
All data via yfinance — no paid APIs required.
"""
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Union
try:
    import zoneinfo
    _TZ_ET = zoneinfo.ZoneInfo("America/New_York")
except ImportError:
    from backports import zoneinfo  # type: ignore
    _TZ_ET = zoneinfo.ZoneInfo("America/New_York")

import pandas as pd
import yfinance as yf

from config.settings import (
    STORY_TIERS, TRACKED_TICKERS,
    MAX_RETRIES, RETRY_BACKOFF_BASE,
    MARKET_HOLIDAYS_2026, OUTPUT_DIR, LOG_FILE,
)

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

SECTOR_ETFS: dict[str, str] = {
    "Technology":       "XLK",
    "Financials":       "XLF",
    "Energy":           "XLE",
    "Healthcare":       "XLV",
    "Consumer Disc.":   "XLY",
    "Consumer Staples": "XLP",
    "Industrials":      "XLI",
    "Materials":        "XLB",
    "Utilities":        "XLU",
    "Real Estate":      "XLRE",
    "Communication":    "XLC",
}

INDEX_ETFS: dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "DIA": "Dow Jones",
    "IWM": "Russell 2000",
}

FUTURES_MAP: dict[str, str] = {
    "ES=F": "S&P 500 E-mini",
    "NQ=F": "Nasdaq 100 E-mini",
    "YM=F": "Dow Jones E-mini",
}

PRE_MARKET_WATCHLIST: list[str] = [
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN",
    "META", "GOOGL", "NFLX", "AMD", "INTC",
    "JPM", "BAC", "GS", "XOM", "CVX",
]

# High-profile tickers that attract more news coverage
_HIGH_COVERAGE_TICKERS = {
    "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META",
    "GOOGL", "GOOG", "NFLX", "AMD", "JPM", "GS",
    "SPY", "QQQ", "^VIX", "BTC-USD",
}


# ── Exceptions ────────────────────────────────────────────────────────────────

class MarketDataError(RuntimeError):
    """Raised when all market data fetches fail."""


# ── Data models (kept for backward compatibility) ─────────────────────────────

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

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "price": self.price,
            "change": self.change,
            "change_pct": self.change_pct,
            "volume": self.volume,
            "avg_volume": self.avg_volume,
            "volume_ratio": self.volume_ratio,
            "market_cap": self.market_cap,
            "day_high": self.day_high,
            "day_low": self.day_low,
            "week_52_high": self.week_52_high,
            "week_52_low": self.week_52_low,
            "pe_ratio": self.pe_ratio,
            "sentiment": self.sentiment,
            "story_tier": self.story_tier,
            "timestamp": self.timestamp,
        }


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
        return self.sp500.story_tier

    @property
    def top_story(self) -> TickerSnapshot:
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
            lines.append(
                f"  ▲ {g.symbol}: {g.change_pct:+.2f}% (${g.price:.2f}) — "
                f"Vol {g.volume_ratio:.1f}x — {STORY_TIERS[g.story_tier]['label'].upper()}"
            )
        lines.append("TOP LOSERS:")
        for lo in self.top_losers[:5]:
            lines.append(
                f"  ▼ {lo.symbol}: {lo.change_pct:+.2f}% (${lo.price:.2f}) — "
                f"Vol {lo.volume_ratio:.1f}x — {STORY_TIERS[lo.story_tier]['label'].upper()}"
            )
        lines.append("\nSECTOR PERFORMANCE:")
        for sector, pct in sorted(
            self.sector_performance.items(), key=lambda x: x[1], reverse=True
        ):
            bar = "▲" if pct > 0 else "▼"
            lines.append(f"  {bar} {sector}: {pct:+.2f}%")
        b = self.market_breadth
        lines.append(
            f"\nMARKET BREADTH: ▲{b['advancing']} advancing  "
            f"▼{b['declining']} declining  —{b['unchanged']} unchanged"
        )
        return "\n".join(lines)


# ── Module-level fetch helper (kept for backward compat) ──────────────────────

def _fetch_ticker(symbol: str) -> Optional[TickerSnapshot]:
    """Fetch a single ticker with exponential-backoff retry. Module-level for test access."""
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
            logger.warning(
                "Attempt %d/%d for %s failed: %s — retry in %.1fs",
                attempt + 1, MAX_RETRIES, symbol, exc, wait,
            )
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    logger.error("All retries exhausted for %s", symbol)
    return None


# ── MarketScraper class ───────────────────────────────────────────────────────

class MarketScraper:
    """
    Central market data collector for DriftWire326.
    All methods save results to output/scripts/market_YYYYMMDD_HH.json.
    Failed calls are retried once after 30 seconds before raising MarketDataError.
    """

    def __init__(
        self,
        output_dir: Optional[Path] = None,
        retry_delay: int = 30,
    ) -> None:
        self._output_dir = (output_dir or OUTPUT_DIR) / "scripts"
        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._retry_delay = retry_delay

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _safe_call(self, func, *args, **kwargs):
        """
        Call func(*args, **kwargs). On any exception, wait retry_delay seconds
        and try once more. Returns None on second failure.
        """
        try:
            return func(*args, **kwargs)
        except Exception as exc:
            logger.warning("%s failed: %s — retrying in %ds", func.__name__, exc, self._retry_delay)
            time.sleep(self._retry_delay)
            try:
                return func(*args, **kwargs)
            except Exception as exc2:
                logger.error("%s failed after retry: %s", func.__name__, exc2)
                return None

    def _fetch_one(self, symbol: str) -> Optional[TickerSnapshot]:
        """Fetch a single ticker, log warning on failure, skip gracefully."""
        snap = _fetch_ticker(symbol)
        if snap is None:
            logger.warning("Skipping %s — data unavailable", symbol)
        return snap

    def _save_json(self, data: dict, label: str) -> Path:
        """Serialize data to output/scripts/market_YYYYMMDD_HH_{label}.json."""
        ts = datetime.now().strftime("%Y%m%d_%H")
        filename = f"market_{ts}_{label}.json"
        path = self._output_dir / filename
        path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
        logger.info("Saved → %s", path)
        return path

    def _get_sp500_tickers(self) -> list[str]:
        """Pull S&P 500 component list from Wikipedia. Falls back to TRACKED_TICKERS."""
        try:
            tables = pd.read_html(
                "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
                attrs={"id": "constituents"},
            )
            tickers = tables[0]["Symbol"].tolist()
            # Yahoo Finance uses dashes not dots (BRK.B → BRK-B)
            return [str(t).replace(".", "-") for t in tickers]
        except Exception as exc:
            logger.warning(
                "Wikipedia S&P 500 fetch failed: %s — falling back to watchlist", exc
            )
            return TRACKED_TICKERS

    def _batch_snapshots(self, symbols: list[str]) -> dict[str, TickerSnapshot]:
        """
        Fetch multiple tickers via yf.download() (batch endpoint — much faster
        than individual calls). Returns {symbol: TickerSnapshot}.
        """
        results: dict[str, TickerSnapshot] = {}
        if not symbols:
            return results

        try:
            raw = yf.download(
                symbols,
                period="2d",
                interval="1d",
                auto_adjust=True,
                progress=False,
                threads=True,
            )
        except Exception as exc:
            logger.error("Batch download failed: %s", exc)
            return results

        # yf.download returns MultiIndex columns when >1 ticker
        multi = isinstance(raw.columns, pd.MultiIndex)

        for sym in symbols:
            try:
                if multi:
                    close = raw["Close"][sym].dropna()
                    volume = raw["Volume"][sym].dropna()
                    high = raw["High"][sym].dropna()
                    low = raw["Low"][sym].dropna()
                else:
                    close = raw["Close"].dropna()
                    volume = raw["Volume"].dropna()
                    high = raw["High"].dropna()
                    low = raw["Low"].dropna()

                if len(close) < 1:
                    continue

                curr = float(close.iloc[-1])
                prev = float(close.iloc[-2]) if len(close) >= 2 else curr
                chg = curr - prev
                chg_pct = (chg / prev * 100) if prev else 0.0

                results[sym] = TickerSnapshot(
                    symbol=sym,
                    name=sym,
                    price=round(curr, 4),
                    change=round(chg, 4),
                    change_pct=round(chg_pct, 4),
                    volume=int(volume.iloc[-1]) if len(volume) > 0 else 0,
                    avg_volume=0,       # not available from batch download
                    market_cap=None,
                    day_high=float(high.iloc[-1]) if len(high) > 0 else curr,
                    day_low=float(low.iloc[-1]) if len(low) > 0 else curr,
                    week_52_high=0.0,
                    week_52_low=0.0,
                    pe_ratio=None,
                )
            except Exception as exc:
                logger.warning("Could not parse batch data for %s: %s", sym, exc)

        return results

    # ── Public methods ───────────────────────────────────────────────────────

    def get_market_status(self) -> str:
        """
        Return current market session: 'pre_market' | 'open' | 'closed' | 'holiday'.
        Evaluated in US Eastern Time against MARKET_HOLIDAYS_2026.
        """
        now = datetime.now(_TZ_ET)
        today_str = now.strftime("%Y-%m-%d")

        if today_str in MARKET_HOLIDAYS_2026:
            return "holiday"

        if now.weekday() >= 5:   # Saturday=5, Sunday=6
            return "closed"

        market_open  = now.replace(hour=9,  minute=30, second=0, microsecond=0)
        market_close = now.replace(hour=16, minute=0,  second=0, microsecond=0)
        pre_start    = now.replace(hour=4,  minute=0,  second=0, microsecond=0)

        if market_open <= now < market_close:
            return "open"
        if pre_start <= now < market_open:
            return "pre_market"
        return "closed"

    def get_premarket_data(self) -> dict:
        """
        Pull futures (ES=F, NQ=F, YM=F) and pre-market movers from major names.
        Returns: {futures: {...}, futures_direction: str, top_movers: [...]}
        """
        logger.info("Fetching pre-market data")

        # ── Futures ────────────────────────────────────────────────────────
        futures_result: dict[str, dict] = {}
        up_count = 0

        for sym, name in FUTURES_MAP.items():
            snap = self._fetch_one(sym)
            if snap:
                direction = "up" if snap.change_pct > 0 else "down"
                if snap.change_pct > 0:
                    up_count += 1
                futures_result[sym] = {
                    "name": name,
                    "price": snap.price,
                    "change": snap.change,
                    "change_pct": snap.change_pct,
                    "direction": direction,
                }
                logger.debug("Future %s: %+.2f%%", sym, snap.change_pct)
            else:
                logger.warning("Futures data unavailable for %s", sym)

        if up_count == len(FUTURES_MAP):
            futures_direction = "up"
        elif up_count == 0:
            futures_direction = "down"
        else:
            futures_direction = "mixed"

        # ── Pre-market movers ─────────────────────────────────────────────
        movers: list[dict] = []
        for sym in PRE_MARKET_WATCHLIST:
            try:
                t = yf.Ticker(sym)
                info = t.info
                pre_price = info.get("preMarketPrice")
                prev_close = info.get("regularMarketPreviousClose") or info.get("regularMarketPrice")
                if pre_price and prev_close and prev_close != 0:
                    chg_pct = (pre_price - prev_close) / prev_close * 100
                    movers.append({
                        "symbol": sym,
                        "name": info.get("shortName", sym),
                        "pre_market_price": round(pre_price, 2),
                        "prev_close": round(prev_close, 2),
                        "change_pct": round(chg_pct, 2),
                        "direction": "up" if chg_pct > 0 else "down",
                    })
            except Exception as exc:
                logger.warning("Pre-market data unavailable for %s: %s", sym, exc)

        movers.sort(key=lambda x: abs(x.get("change_pct", 0)), reverse=True)
        top_movers = movers[:5]

        result = {
            "timestamp": datetime.now().isoformat(),
            "status": self.get_market_status(),
            "futures": futures_result,
            "futures_direction": futures_direction,
            "top_movers": top_movers,
        }

        if not futures_result and not top_movers:
            raise MarketDataError("Pre-market data fetch failed — no futures or movers returned")

        self._save_json(result, "premarket")
        logger.info(
            "Pre-market: futures=%s | movers=%d", futures_direction, len(top_movers)
        )
        return result

    def get_market_close_data(self) -> dict:
        """
        Pull close prices for SPY, QQQ, DIA, IWM and all 11 sector ETFs.
        Returns: {indices: {...}, sector_performance: {...}, market_tier: str, narrative: str}
        """
        logger.info("Fetching market close data")

        # ── Index ETFs ────────────────────────────────────────────────────
        indices: dict[str, dict] = {}
        for sym, name in INDEX_ETFS.items():
            snap = self._fetch_one(sym)
            if snap:
                indices[sym] = {
                    "name": name,
                    "price": snap.price,
                    "change": snap.change,
                    "change_pct": snap.change_pct,
                    "volume": snap.volume,
                    "sentiment": snap.sentiment,
                    "story_tier": snap.story_tier,
                }

        if not indices:
            raise MarketDataError("All index ETF fetches failed — cannot produce close data")

        # ── Sector ETFs ───────────────────────────────────────────────────
        sector_performance: dict[str, dict] = {}
        for sector, etf in SECTOR_ETFS.items():
            snap = self._fetch_one(etf)
            if snap:
                sector_performance[sector] = {
                    "etf": etf,
                    "price": snap.price,
                    "change_pct": snap.change_pct,
                    "direction": "up" if snap.change_pct > 0 else "down",
                }
                logger.debug("Sector %s (%s): %+.2f%%", sector, etf, snap.change_pct)

        # Overall market tier based on S&P proxy (SPY)
        spy_snap = _fetch_ticker("SPY")
        market_tier = spy_snap.story_tier if spy_snap else "tier3"

        # Sector breadth
        advancing = sum(1 for s in sector_performance.values() if s["change_pct"] > 0)
        declining = len(sector_performance) - advancing

        # Best and worst sectors for narrative
        sorted_sectors = sorted(
            sector_performance.items(), key=lambda x: x[1]["change_pct"], reverse=True
        )
        best = sorted_sectors[0] if sorted_sectors else None
        worst = sorted_sectors[-1] if sorted_sectors else None

        narrative_parts = []
        if spy_snap:
            direction = "gained" if spy_snap.change_pct > 0 else "lost"
            narrative_parts.append(
                f"S&P 500 (SPY) {direction} {abs(spy_snap.change_pct):.2f}% today."
            )
        if best:
            narrative_parts.append(
                f"Best sector: {best[0]} ({best[1]['etf']}) +{best[1]['change_pct']:.2f}%."
            )
        if worst and worst != best:
            narrative_parts.append(
                f"Worst sector: {worst[0]} ({worst[1]['etf']}) {worst[1]['change_pct']:+.2f}%."
            )
        narrative_parts.append(
            f"Market breadth: {advancing} sectors advancing, {declining} declining."
        )

        result = {
            "timestamp": datetime.now().isoformat(),
            "date": datetime.now().strftime("%Y-%m-%d"),
            "market_tier": market_tier,
            "indices": indices,
            "sector_performance": sector_performance,
            "market_breadth": {"advancing": advancing, "declining": declining},
            "narrative": " ".join(narrative_parts),
        }

        self._save_json(result, "close")
        logger.info(
            "Close data: tier=%s | sectors=%d/%d advancing",
            market_tier, advancing, len(sector_performance),
        )
        return result

    def get_top_movers(self, n: int = 10) -> dict:
        """
        Pull S&P 500 components from Wikipedia, fetch today's % change for each,
        and return the top N gainers and top N losers.

        Each entry includes: ticker, company_name, price, change_pct, change_dollar,
        volume, avg_volume, volume_ratio.
        """
        logger.info("Fetching S&P 500 top movers (n=%d)", n)

        sp500 = self._get_sp500_tickers()
        logger.info("S&P 500 component list: %d tickers", len(sp500))

        # Batch download is much faster than 500 individual calls
        snapshots = self._batch_snapshots(sp500)

        if not snapshots:
            # Last-resort retry with individual fetches on watchlist only
            logger.warning("Batch fetch returned no data — retrying with TRACKED_TICKERS")
            time.sleep(self._retry_delay)
            snapshots = self._batch_snapshots(TRACKED_TICKERS)

        if not snapshots:
            raise MarketDataError("Top movers fetch failed — no data returned after retry")

        # Build mover records
        movers = []
        for sym, snap in snapshots.items():
            movers.append({
                "ticker":        snap.symbol,
                "company_name":  snap.name,
                "price":         snap.price,
                "change_pct":    snap.change_pct,
                "change_dollar": snap.change,
                "volume":        snap.volume,
                "avg_volume":    snap.avg_volume,
                "volume_ratio":  snap.volume_ratio,
                "story_tier":    snap.story_tier,
            })

        gainers = sorted(movers, key=lambda x: x["change_pct"], reverse=True)[:n]
        losers  = sorted(movers, key=lambda x: x["change_pct"])[:n]

        result = {
            "timestamp":    datetime.now().isoformat(),
            "universe_size": len(snapshots),
            "top_gainers":  gainers,
            "top_losers":   losers,
        }

        self._save_json(result, "movers")
        logger.info(
            "Top movers: %d universe | best=%s +%.2f%% | worst=%s %.2f%%",
            len(snapshots),
            gainers[0]["ticker"] if gainers else "N/A",
            gainers[0]["change_pct"] if gainers else 0,
            losers[0]["ticker"] if losers else "N/A",
            losers[0]["change_pct"] if losers else 0,
        )
        return result

    def get_ticker_history(self, ticker: str, days: int = 30) -> pd.DataFrame:
        """
        Pull OHLCV price history for chart generation.
        Returns a DataFrame with columns: Open, High, Low, Close, Volume.
        """
        logger.info("Fetching %d-day history for %s", days, ticker)

        def _pull():
            t = yf.Ticker(ticker)
            hist = t.history(period=f"{days}d", interval="1d")
            if hist.empty:
                raise ValueError(f"Empty history for {ticker}")
            return hist[["Open", "High", "Low", "Close", "Volume"]]

        result = self._safe_call(_pull)
        if result is None:
            logger.error("History fetch failed for %s after retry", ticker)
            return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

        logger.debug("History for %s: %d rows", ticker, len(result))
        return result

    def score_topic(self, ticker_data: Union[TickerSnapshot, dict]) -> float:
        """
        Score a ticker as a video topic candidate (0-100).

        Formula (weights sum to 1.0):
          change_pct_score   = min(100, abs(change_pct) × 10)   → ×0.35
          volume_ratio_score = min(100, volume_ratio × 20)       → ×0.25
          news_coverage_score (proxy, 0-100)                     → ×0.20
          recency_score       (session timing, 0-100)            → ×0.20
        """
        # ── Extract fields from TickerSnapshot or dict ─────────────────────
        if isinstance(ticker_data, TickerSnapshot):
            symbol     = ticker_data.symbol
            change_pct = ticker_data.change_pct
            vol_ratio  = ticker_data.volume_ratio
            tier       = ticker_data.story_tier
        else:
            symbol     = ticker_data.get("ticker", ticker_data.get("symbol", ""))
            change_pct = float(ticker_data.get("change_pct", 0))
            volume     = int(ticker_data.get("volume", 0))
            avg_volume = int(ticker_data.get("avg_volume", 0))
            vol_ratio  = round(volume / avg_volume, 2) if avg_volume > 0 else 0.0
            abs_move   = abs(change_pct)
            tier = "tier3"
            for t in ("tier1", "tier2", "tier3"):
                if abs_move >= STORY_TIERS[t]["min_move_pct"]:
                    tier = t
                    break

        # ── Sub-scores (each 0-100) ────────────────────────────────────────
        change_pct_score   = min(100.0, abs(change_pct) * 10.0)
        volume_ratio_score = min(100.0, vol_ratio * 20.0)

        # News coverage proxy — based on brand recognition + tier boost
        base_coverage = 70.0 if symbol.upper() in _HIGH_COVERAGE_TICKERS else 40.0
        tier_boost = {"tier1": 30.0, "tier2": 15.0, "tier3": 0.0}.get(tier, 0.0)
        news_coverage_score = min(100.0, base_coverage + tier_boost)

        # Recency proxy — based on current market session
        status = self.get_market_status()
        recency_score = {
            "open":       100.0,
            "pre_market":  85.0,
            "closed":      75.0,
            "holiday":     50.0,
        }.get(status, 75.0)

        # ── Weighted total ──────────────────────────────────────────────────
        score = (
            change_pct_score   * 0.35
            + volume_ratio_score * 0.25
            + news_coverage_score * 0.20
            + recency_score      * 0.20
        )
        return round(score, 2)


# ── Backward-compat entry point (used by schedulers) ─────────────────────────

def scrape_market() -> MarketSummary:
    """
    Collect a full market snapshot via _fetch_ticker() calls.
    Raises RuntimeError if critical tickers fail.
    Preserved for backward compatibility — new code should use MarketScraper.
    """
    logger.info("Starting market data scrape (legacy path)")
    today = datetime.now().strftime("%Y-%m-%d %A")

    index_map = {
        "SPY":     "sp500",
        "QQQ":     "nasdaq",
        "DIA":     "dow",
        "IWM":     "russell2000",
        "^VIX":    "vix",
        "^TNX":    "ten_year_yield",
        "GLD":     "gold",
        "BTC-USD": "bitcoin",
    }

    index_data: dict[str, TickerSnapshot] = {}
    for sym, key in index_map.items():
        snap = _fetch_ticker(sym)
        if snap is None:
            raise RuntimeError(f"Critical ticker {sym} failed — aborting scrape")
        index_data[key] = snap

    all_snaps: list[TickerSnapshot] = []
    for sym in TRACKED_TICKERS:
        if sym.startswith("^"):
            continue
        snap = _fetch_ticker(sym)
        if snap:
            all_snaps.append(snap)

    gainers  = sorted(all_snaps, key=lambda x: x.change_pct, reverse=True)[:5]
    losers   = sorted(all_snaps, key=lambda x: x.change_pct)[:5]
    high_vol = sorted(all_snaps, key=lambda x: x.volume_ratio, reverse=True)[:5]

    sector_perf: dict[str, float] = {}
    for sector, etf in SECTOR_ETFS.items():
        snap = _fetch_ticker(etf)
        if snap:
            sector_perf[sector] = snap.change_pct

    advancing = sum(1 for v in sector_perf.values() if v > 0)
    declining = sum(1 for v in sector_perf.values() if v < 0)

    return MarketSummary(
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
        sector_performance=sector_perf,
        market_breadth={"advancing": advancing, "declining": len(sector_perf) - advancing, "unchanged": 0},
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    scraper = MarketScraper()
    print("Market status:", scraper.get_market_status())
    data = scraper.get_market_close_data()
    print(json.dumps(data, indent=2, default=str))
