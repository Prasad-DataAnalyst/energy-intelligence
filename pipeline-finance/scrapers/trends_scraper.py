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
    """
    Build a TrendReq. Raises ImportError if pytrends is not installed.

    Deliberately does NOT pass retries/backoff_factor. pytrends turns those
    into urllib3.Retry(method_whitelist=...), and urllib3 renamed that
    argument to allowed_methods in 2.0 — so asking pytrends to retry makes
    every single query raise TypeError before it reaches the network:

        Trends query failed for 'stock market today':
        Retry.__init__() got an unexpected keyword argument 'method_whitelist'

    Every trends lookup had been failing that way, silently, since the
    scraper was wired in. Retrying is done in _query_with_retry below, where
    it does not depend on a third party's urllib3 compatibility.
    """
    try:
        from pytrends.request import TrendReq
        return TrendReq(hl="en-US", tz=300, timeout=_PYTRENDS_TIMEOUT)
    except ImportError as exc:
        raise ImportError("pytrends is required: pip install pytrends>=4.9.0") from exc


def _query_with_retry(fn, attempts: int = 3, base_delay: float = 1.0):
    """
    Run a Trends call, retrying transient failures with backoff.

    Google rate-limits this endpoint aggressively and answers with a 429 or
    a read timeout rather than anything structured, so the retry is on the
    exception rather than on a status code.
    """
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if attempt == attempts:
                raise
            delay = base_delay * (2 ** (attempt - 1))
            logger.debug("Trends attempt %d/%d failed (%s) — retry in %.1fs",
                         attempt, attempts, exc, delay)
            time.sleep(delay)
    return None


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
                def _fetch(keyword=kw):
                    pt.build_payload([keyword], cat=0, timeframe=timeframe,
                                     geo=geo, gprop="")
                    return pt.related_queries()

                related = _query_with_retry(_fetch) or {}
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


# ── Shared day cache ─────────────────────────────────────────────────────────

TREND_SEEDS = ["stock market today", "S&P 500", "inflation"]
TREND_CACHE_NAME = "trending_queries.json"


def todays_rising_queries(limit: int = 8) -> list:
    """
    The day's rising search queries, fetched at most once per day.

    Lives here rather than in script_gen because more than one consumer
    wants it now: the script leads with what people are already curious
    about, and the title generator needs the same phrases to put IN the
    title — which is the half that earns search traffic. Two copies of the
    cache would mean two fetches against a rate-limited endpoint and two
    chances to disagree about what today's queries are.

    Returns [] on any failure. Google Trends is unofficial and rate-limited,
    and nothing that publishes on a schedule may wait on it or fail with it.
    """
    import json
    from datetime import date
    from config.settings import settings

    cache = settings.logs_dir / TREND_CACHE_NAME
    today = date.today().isoformat()

    try:
        if cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            # Both weekday runs share one fetch: the day's searches do not
            # change enough between them to spend the rate-limit budget.
            if cached.get("date") == today:
                return list(cached.get("queries", []))[:limit]
    except Exception as exc:
        logger.debug("Trend cache unreadable (non-fatal): %s", exc)

    try:
        rising = TrendsScraper().get_rising_queries(keywords=TREND_SEEDS)
        queries = [item["query"] for item in rising if item.get("query")]
    except Exception as exc:
        logger.warning("Google Trends unavailable (non-fatal): %s", exc)
        return []

    if not queries:
        return []
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"date": today, "queries": queries}),
                         encoding="utf-8")
    except Exception as exc:
        logger.debug("Trend cache not written (non-fatal): %s", exc)
    return queries[:limit]


# Words too common in finance to count as a demand signal. Every title in
# the niche contains "stock" or "market", so matching on them would rank
# everything identically and say nothing.
DEMAND_STOPWORDS = {
    "the", "a", "an", "of", "for", "to", "in", "on", "and", "is", "are",
    "stock", "stocks", "market", "markets", "today", "now", "news", "price",
}


def demand_terms(queries: list) -> set:
    """
    The distinctive words in a set of rising queries.

    Lives here because two very different consumers need the same reading of
    the data — the title scorer and the Sunday topic picker — and one of
    them is in scrapers/, which must not import from generators/.
    """
    terms = set()
    for query in queries or []:
        for word in str(query).lower().split():
            cleaned = "".join(ch for ch in word if ch.isalnum())
            if len(cleaned) > 3 and cleaned not in DEMAND_STOPWORDS:
                terms.add(cleaned)
    return terms
