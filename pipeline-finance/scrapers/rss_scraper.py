"""
scrapers/rss_scraper.py — DriftWire326 Module 29
Free RSS feed parsing for financial news headlines.
Uses feedparser (no API key required).
Feeds: Reuters Business, CNBC Markets, MarketWatch, AP Business, Yahoo Finance.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

try:
    import feedparser
except ImportError:
    feedparser = None  # type: ignore

from config.settings import settings

logger = logging.getLogger(__name__)

# Free, stable RSS feeds — no API key required
_DEFAULT_FEEDS: dict[str, str] = {
    "reuters_business": "https://feeds.reuters.com/reuters/businessNews",
    "cnbc_markets": "https://feeds.nbcnews.com/nbcnews/public/business",
    "marketwatch_top": "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
    "ap_business": "https://rsshub.app/apnews/business",
    "yahoo_finance": "https://finance.yahoo.com/news/rssindex",
}

_MAX_ENTRIES_PER_FEED = 20
_FETCH_TIMEOUT = 15  # seconds


@dataclass
class NewsItem:
    title: str
    summary: str
    link: str
    published: str
    source: str
    score: float = 0.0  # relevance score (0-1), set by ranker

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "link": self.link,
            "published": self.published,
            "source": self.source,
            "score": self.score,
        }


# Finance signal keywords for basic relevance scoring
_FINANCE_KEYWORDS = [
    "market", "stock", "S&P", "nasdaq", "dow", "fed", "inflation", "rate",
    "earnings", "gdp", "jobs", "unemployment", "treasury", "yield", "etf",
    "bond", "equity", "trade", "economy", "recession", "rally", "selloff",
]


def _score_relevance(text: str) -> float:
    """Simple keyword-density relevance score (0-1)."""
    text_lower = text.lower()
    hits = sum(1 for kw in _FINANCE_KEYWORDS if kw.lower() in text_lower)
    return min(hits / max(len(_FINANCE_KEYWORDS), 1), 1.0)


def _safe_parse_feed(url: str, source: str) -> list[NewsItem]:
    """Parse a single RSS feed, returning a list of NewsItem objects."""
    try:
        _fp = feedparser
        if _fp is None:
            raise ImportError("feedparser is required: pip install feedparser>=6.0.11")
        feed = _fp.parse(url, request_headers={"User-Agent": "DriftWire326-bot/1.0"})
        items: list[NewsItem] = []
        for entry in getattr(feed, "entries", [])[:_MAX_ENTRIES_PER_FEED]:
            title = entry.get("title", "").strip()
            summary = entry.get("summary", entry.get("description", "")).strip()
            link = entry.get("link", "")
            published = entry.get("published", datetime.now(timezone.utc).isoformat())
            item = NewsItem(
                title=title,
                summary=summary[:500],
                link=link,
                published=published,
                source=source,
                score=_score_relevance(f"{title} {summary}"),
            )
            items.append(item)
        return items
    except ImportError:
        raise ImportError("feedparser is required: pip install feedparser>=6.0.11")
    except Exception as exc:
        logger.warning("RSS feed parse failed for %s (%s): %s", source, url, exc)
        return []


class RssScraper:
    """Scrapes financial news from RSS feeds (free, no API key)."""

    def __init__(self, feeds: Optional[dict[str, str]] = None):
        self._feeds = feeds or _DEFAULT_FEEDS

    def fetch_all(self, min_score: float = 0.0) -> list[NewsItem]:
        """
        Fetch all configured RSS feeds and return merged, ranked list.
        Items with score >= min_score only.
        Returns list sorted by relevance score descending.
        """
        all_items: list[NewsItem] = []
        for source, url in self._feeds.items():
            items = _safe_parse_feed(url, source)
            all_items.extend(items)

        # Filter by relevance
        if min_score > 0:
            all_items = [i for i in all_items if i.score >= min_score]

        # Sort by score desc
        all_items.sort(key=lambda x: x.score, reverse=True)
        logger.info("RSS feeds: %d items fetched across %d feeds", len(all_items), len(self._feeds))
        return all_items

    def get_top_headlines(self, n: int = 10, min_score: float = 0.1) -> list[str]:
        """
        Return top N headline strings for use in script context injection.
        """
        items = self.fetch_all(min_score=min_score)
        return [item.title for item in items[:n] if item.title]

    def fetch_by_source(self, source: str) -> list[NewsItem]:
        """Fetch a single feed by source name."""
        url = self._feeds.get(source)
        if not url:
            logger.warning("Unknown RSS source: %s", source)
            return []
        return _safe_parse_feed(url, source)
