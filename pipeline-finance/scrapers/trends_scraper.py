"""
scrapers/trends_scraper.py — DriftWire326 Module 28
Google Trends data via pytrends (free, no API key required).
Returns rising queries and interest-over-time for finance keywords.
Used to identify trending topics for script generation.
"""
import logging
import time
from typing import Optional

import pandas as pd

from config.settings import settings

logger = logging.getLogger(__name__)

# Finance-relevant seed keywords
_DEFAULT_KEYWORDS = [
    "stock market today",
    "S&P 500",
    "interest rates",
    "inflation",
    "recession",
]

_RISING_TOP_N = 10
_PYTRENDS_TIMEOUT = (10, 30)  # (connect, read) seconds


def _get_pytrends_client():
    """Build a TrendReq with retry. Raises ImportError if pytrends not installed."""
    try:
        from pytrends.request import TrendReq
        return TrendReq(hl="en-US", tz=300, timeout=_PYTRENDS_TIMEOUT, retries=2, backoff_factor=0.5)
    except ImportError as exc:
        raise ImportError("pytrends is required: pip install pytrends>=4.9.0") from exc


class TrendsScraper:
    """Fetches Google Trends data for finance topic discovery."""

    def __init__(self):
        self._client = None

    def _client_or_build(self):
        if self._client is None:
            self._client = _get_pytrends_client()
        return self._client

    def get_rising_queries(
        self,
        keywords: Optional[list[str]] = None,
        timeframe: str = "now 1-d",
        geo: str = "US",
    ) -> list[dict]:
        """
        Return rising search queries related to finance keywords.

        Args:
            keywords: Seed terms (defaults to _DEFAULT_KEYWORDS).
            timeframe: pytrends timeframe string ("now 1-d", "now 7-d", etc.)
            geo: Country code ("US").

        Returns:
            List of dicts: [{"query": str, "value": int, "keyword": str}, ...]
            Sorted by value descending.
        """
        if keywords is None:
            keywords = _DEFAULT_KEYWORDS[:5]

        results: list[dict] = []
        try:
            pt = self._client_or_build()
        except Exception as exc:
            logger.warning("Failed to initialize pytrends client: %s", exc)
            return []

        # pytrends only accepts up to 5 keywords at once
        for kw in keywords[:5]:
            try:
                pt.build_payload([kw], cat=0, timeframe=timeframe, geo=geo, gprop="")
                related = pt.related_queries()
                rising_df = related.get(kw, {}).get("rising")
                if rising_df is not None and not rising_df.empty:
                    for _, row in rising_df.head(_RISING_TOP_N).iterrows():
                        results.append({
                            "query": row.get("query", ""),
                            "value": int(row.get("value", 0)),
                            "keyword": kw,
                        })
                time.sleep(1)  # respect rate limit
            except Exception as exc:
                logger.warning("Trends query failed for '%s': %s", kw, exc)

        results.sort(key=lambda x: x["value"], reverse=True)
        logger.info("Google Trends: %d rising queries fetched", len(results))
        return results

    def get_interest_over_time(
        self,
        keywords: Optional[list[str]] = None,
        timeframe: str = "now 7-d",
        geo: str = "US",
    ) -> dict[str, list[float]]:
        """
        Return normalized interest-over-time scores (0-100) for keywords.

        Returns:
            {keyword: [score, score, ...]} keyed by keyword.
        """
        if keywords is None:
            keywords = _DEFAULT_KEYWORDS[:5]

        try:
            pt = self._client_or_build()
            pt.build_payload(keywords[:5], cat=0, timeframe=timeframe, geo=geo, gprop="")
            df: pd.DataFrame = pt.interest_over_time()
            if df.empty:
                return {}
            result: dict[str, list[float]] = {}
            for kw in keywords[:5]:
                if kw in df.columns:
                    result[kw] = df[kw].tolist()
            return result
        except Exception as exc:
            logger.warning("Interest-over-time failed: %s", exc)
            return {}

    def get_top_finance_trends(self) -> list[str]:
        """
        Convenience: return list of top rising query strings for today.
        Used by script_gen to enrich context.
        """
        rising = self.get_rising_queries()
        return [r["query"] for r in rising[:10] if r.get("query")]
