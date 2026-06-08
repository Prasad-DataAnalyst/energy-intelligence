"""
data/external_feeds.py — External Data Feed Puller
====================================================
Aggregates content signals from RSS feeds, news APIs, Reddit, Hacker News,
ArXiv, and World Bank.  All network calls are wrapped in try/except; every
public method returns [] or {} on failure.  Results are scored for video
potential and deduplicated before being persisted to Memory.
"""
import json
import logging
import os
import re
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brain.external_feeds")

HERE = Path(__file__).parent

# ── Feed configuration ────────────────────────────────────────────────────────

FEED_CONFIGS: Dict[str, Dict] = {
    # News APIs (require API keys from env)
    "newsapi": {
        "type": "api",
        "base": "https://newsapi.org/v2/top-headlines",
        "env": "NEWS_API_KEY",
        "rate_limit_s": 3600,
    },
    "gnews": {
        "type": "api",
        "base": "https://gnews.io/api/v4/top-headlines",
        "env": "GNEWS_API_KEY",
        "rate_limit_s": 3600,
    },
    "guardian": {
        "type": "api",
        "base": "https://content.guardianapis.com/search",
        "env": "GUARDIAN_API_KEY",
        "rate_limit_s": 3600,
    },
    # Free RSS feeds (no key needed)
    "reuters_rss": {
        "type": "rss",
        "url": "https://feeds.reuters.com/reuters/topNews",
        "rate_limit_s": 900,
    },
    "bbc_rss": {
        "type": "rss",
        "url": "http://feeds.bbci.co.uk/news/rss.xml",
        "rate_limit_s": 900,
    },
    "ap_rss": {
        "type": "rss",
        "url": "https://rsshub.app/apnews/topics/apf-topnews",
        "rate_limit_s": 900,
    },
    "hn_api": {
        "type": "api",
        "base": "https://hacker-news.firebaseio.com/v0",
        "env": None,
        "rate_limit_s": 300,
    },
    "arxiv_api": {
        "type": "api",
        "base": "http://export.arxiv.org/api/query",
        "env": None,
        "rate_limit_s": 3600,
    },
    "reddit_hot": {
        "type": "api",
        "base": "https://www.reddit.com/r/all/hot.json",
        "env": None,
        "rate_limit_s": 1800,
    },
    "worldbank": {
        "type": "api",
        "base": "https://api.worldbank.org/v2",
        "env": None,
        "rate_limit_s": 86400,
    },
    "github_trending": {
        "type": "scrape",
        "url": "https://github.com/trending",
        "rate_limit_s": 3600,
    },
}

TOPIC_SUBREDDITS = [
    "worldnews", "technology", "science", "business", "economics",
    "futurology", "dataisbeautiful", "todayilearned", "explainlikeimfive",
]

_USER_AGENT = "Mozilla/5.0 (compatible; YouTubeAIEngine/1.0; +https://github.com/energy-intelligence)"


# ─────────────────────────────────────────────────────────────────────────────
# Helper: generic HTTP GET
# ─────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, headers: Optional[Dict[str, str]] = None,
              timeout: int = 10) -> bytes:
    """Perform an HTTP GET and return raw bytes; raises on error."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ─────────────────────────────────────────────────────────────────────────────
# Class 1: RSSParser
# ─────────────────────────────────────────────────────────────────────────────

class RSSParser:
    """Parse RSS/Atom XML feeds without external dependencies."""

    def fetch(self, url: str) -> List[Dict]:
        """
        Fetch an RSS/Atom feed and return up to 20 items.

        Each item is a dict with keys: title, link, description, pub_date, source.
        Returns [] on any failure.
        """
        try:
            raw = _http_get(url, timeout=10).decode("utf-8", errors="replace")
        except Exception as exc:
            log.debug("RSSParser.fetch error for %s: %s", url, exc)
            return []

        source = self._source_name(url)
        items: List[Dict] = []

        # Try <item> tags (RSS 2.0)
        item_blocks = re.findall(r"<item[^>]*>(.*?)</item>", raw, re.DOTALL | re.IGNORECASE)
        if not item_blocks:
            # Fallback: try <entry> tags (Atom)
            item_blocks = re.findall(r"<entry[^>]*>(.*?)</entry>", raw, re.DOTALL | re.IGNORECASE)

        for block in item_blocks[:20]:
            title       = self._tag(block, "title")
            link        = self._tag(block, "link") or self._tag(block, "id")
            description = self._tag(block, "description") or self._tag(block, "summary") or self._tag(block, "content")
            pub_date    = (
                self._tag(block, "pubDate")
                or self._tag(block, "published")
                or self._tag(block, "updated")
            )
            if not title:
                continue
            items.append({
                "title":       self._clean(title),
                "link":        self._clean(link),
                "description": self._clean(description)[:500] if description else "",
                "pub_date":    self._clean(pub_date),
                "source":      source,
            })

        log.debug("RSSParser.fetch: %d items from %s", len(items), source)
        return items

    # ── internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _source_name(url: str) -> str:
        """Derive a short source label from a URL."""
        try:
            host = urllib.parse.urlparse(url).netloc
            return host.replace("www.", "").split(".")[0]
        except Exception:
            return "rss"

    @staticmethod
    def _tag(block: str, tag: str) -> str:
        """Extract text content of the first matching XML tag."""
        # Prefer tag with namespace prefix stripped
        patterns = [
            rf"<{tag}[^>]*>\s*(.*?)\s*</{tag}>",
            rf"<[a-z]+:{tag}[^>]*>\s*(.*?)\s*</[a-z]+:{tag}>",
        ]
        for pattern in patterns:
            m = re.search(pattern, block, re.DOTALL | re.IGNORECASE)
            if m:
                return m.group(1).strip()
        # Special case: self-closing <link href="..."/>
        if tag == "link":
            m = re.search(r'<link[^>]+href=["\']([^"\']+)["\']', block, re.IGNORECASE)
            if m:
                return m.group(1)
        return ""

    @staticmethod
    def _clean(text: str) -> str:
        """Strip CDATA wrappers, HTML tags, and common XML entities."""
        if not text:
            return ""
        # CDATA
        text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
        # HTML tags
        text = re.sub(r"<[^>]+>", " ", text)
        # XML entities
        text = (
            text
            .replace("&amp;",  "&")
            .replace("&lt;",   "<")
            .replace("&gt;",   ">")
            .replace("&quot;", '"')
            .replace("&apos;", "'")
            .replace("&#39;",  "'")
            .replace("&nbsp;", " ")
        )
        # Collapse whitespace
        text = re.sub(r"\s{2,}", " ", text).strip()
        return text


# ─────────────────────────────────────────────────────────────────────────────
# Class 2: APIFetcher
# ─────────────────────────────────────────────────────────────────────────────

class APIFetcher:
    """Fetch content from various web APIs."""

    # ── NewsAPI ───────────────────────────────────────────────────────────────

    def fetch_newsapi(self, query: str = "breaking news",
                      page_size: int = 10) -> List[Dict]:
        """Fetch top headlines from NewsAPI.org. Returns [] if no key."""
        api_key = os.getenv("NEWS_API_KEY", "")
        if not api_key:
            log.debug("NEWS_API_KEY not set; skipping NewsAPI")
            return []
        try:
            params = urllib.parse.urlencode({
                "q":        query,
                "pageSize": page_size,
                "language": "en",
                "apiKey":   api_key,
            })
            url  = f"{FEED_CONFIGS['newsapi']['base']}?{params}"
            raw  = _http_get(url, timeout=10).decode("utf-8", errors="replace")
            data = json.loads(raw)
            articles = data.get("articles") or []
            results: List[Dict] = []
            for a in articles[:page_size]:
                results.append({
                    "title":       a.get("title", ""),
                    "description": a.get("description", "") or "",
                    "link":        a.get("url", ""),
                    "pub_date":    a.get("publishedAt", ""),
                    "source":      "newsapi",
                    "author":      a.get("author", ""),
                })
            log.debug("NewsAPI: %d articles", len(results))
            return results
        except Exception as exc:
            log.debug("fetch_newsapi failed: %s", exc)
            return []

    # ── Guardian API ──────────────────────────────────────────────────────────

    def fetch_guardian(self, query: str = "news",
                       page_size: int = 10) -> List[Dict]:
        """Fetch articles from The Guardian API. Returns [] if no key."""
        api_key = os.getenv("GUARDIAN_API_KEY", "")
        if not api_key:
            log.debug("GUARDIAN_API_KEY not set; skipping Guardian")
            return []
        try:
            params = urllib.parse.urlencode({
                "q":          query,
                "page-size":  page_size,
                "show-fields": "trailText",
                "api-key":    api_key,
            })
            url  = f"{FEED_CONFIGS['guardian']['base']}?{params}"
            raw  = _http_get(url, timeout=10).decode("utf-8", errors="replace")
            data = json.loads(raw)
            items = (data.get("response") or {}).get("results") or []
            results: List[Dict] = []
            for item in items[:page_size]:
                fields = item.get("fields") or {}
                results.append({
                    "title":       item.get("webTitle", ""),
                    "description": fields.get("trailText", ""),
                    "link":        item.get("webUrl", ""),
                    "pub_date":    item.get("webPublicationDate", ""),
                    "source":      "guardian",
                    "section":     item.get("sectionName", ""),
                })
            log.debug("Guardian: %d articles", len(results))
            return results
        except Exception as exc:
            log.debug("fetch_guardian failed: %s", exc)
            return []

    # ── GNews ─────────────────────────────────────────────────────────────────

    def fetch_gnews(self, query: str = "news",
                    max_items: int = 10) -> List[Dict]:
        """Fetch headlines from GNews.io API. Returns [] if no key."""
        api_key = os.getenv("GNEWS_API_KEY", "")
        if not api_key:
            log.debug("GNEWS_API_KEY not set; skipping GNews")
            return []
        try:
            params = urllib.parse.urlencode({
                "q":      query,
                "max":    max_items,
                "lang":   "en",
                "token":  api_key,
            })
            url  = f"{FEED_CONFIGS['gnews']['base']}?{params}"
            raw  = _http_get(url, timeout=10).decode("utf-8", errors="replace")
            data = json.loads(raw)
            articles = data.get("articles") or []
            results: List[Dict] = []
            for a in articles[:max_items]:
                results.append({
                    "title":       a.get("title", ""),
                    "description": a.get("description", "") or "",
                    "link":        a.get("url", ""),
                    "pub_date":    a.get("publishedAt", ""),
                    "source":      "gnews",
                })
            log.debug("GNews: %d articles", len(results))
            return results
        except Exception as exc:
            log.debug("fetch_gnews failed: %s", exc)
            return []

    # ── Hacker News ───────────────────────────────────────────────────────────

    def fetch_hacker_news(self, n: int = 30) -> List[Dict]:
        """Fetch top Hacker News stories via Firebase REST API."""
        try:
            base     = FEED_CONFIGS["hn_api"]["base"]
            ids_raw  = _http_get(f"{base}/topstories.json", timeout=10)
            story_ids: List[int] = json.loads(ids_raw)[:n]
        except Exception as exc:
            log.debug("fetch_hacker_news (ids) failed: %s", exc)
            return []

        results: List[Dict] = []
        for sid in story_ids:
            try:
                item_raw = _http_get(
                    f"{FEED_CONFIGS['hn_api']['base']}/item/{sid}.json",
                    timeout=8,
                )
                item = json.loads(item_raw)
                if not item or item.get("type") != "story":
                    continue
                results.append({
                    "title":       item.get("title", ""),
                    "link":        item.get("url", f"https://news.ycombinator.com/item?id={sid}"),
                    "description": "",
                    "pub_date":    datetime.fromtimestamp(
                        item.get("time", 0), tz=timezone.utc
                    ).isoformat() if item.get("time") else "",
                    "source":      "hackernews",
                    "score":       item.get("score", 0),
                    "comments":    item.get("descendants", 0),
                })
            except Exception as exc:
                log.debug("fetch_hacker_news item %s failed: %s", sid, exc)
                continue

        log.debug("HackerNews: %d stories", len(results))
        return results

    # ── ArXiv ─────────────────────────────────────────────────────────────────

    def fetch_arxiv(self, query: str = "artificial intelligence",
                    max_results: int = 10) -> List[Dict]:
        """Fetch recent papers from the ArXiv Atom API."""
        try:
            params = urllib.parse.urlencode({
                "search_query": f"all:{query}",
                "start":        0,
                "max_results":  max_results,
                "sortBy":       "submittedDate",
                "sortOrder":    "descending",
            })
            url = f"{FEED_CONFIGS['arxiv_api']['base']}?{params}"
            raw = _http_get(url, timeout=15).decode("utf-8", errors="replace")
        except Exception as exc:
            log.debug("fetch_arxiv request failed: %s", exc)
            return []

        entries = re.findall(r"<entry>(.*?)</entry>", raw, re.DOTALL)
        results: List[Dict] = []

        for entry in entries[:max_results]:
            title    = re.search(r"<title>(.*?)</title>",   entry, re.DOTALL)
            abstract = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            link     = re.search(r'<id>(.*?)</id>',          entry, re.DOTALL)
            pub      = re.search(r"<published>(.*?)</published>", entry, re.DOTALL)
            authors  = re.findall(r"<name>(.*?)</name>",     entry, re.DOTALL)

            results.append({
                "title":    re.sub(r"\s+", " ", title.group(1)).strip()    if title    else "",
                "abstract": re.sub(r"\s+", " ", abstract.group(1)).strip() if abstract else "",
                "url":      link.group(1).strip()                          if link     else "",
                "pub_date": pub.group(1).strip()                           if pub      else "",
                "authors":  [a.strip() for a in authors[:5]],
                "source":   "arxiv",
                "description": re.sub(r"\s+", " ", abstract.group(1)).strip()[:300] if abstract else "",
                "link":     link.group(1).strip() if link else "",
            })

        log.debug("ArXiv: %d papers for query '%s'", len(results), query)
        return results

    # ── Reddit ────────────────────────────────────────────────────────────────

    def fetch_reddit_hot(self, subreddit: str = "all",
                         limit: int = 25) -> List[Dict]:
        """Fetch hot posts from a subreddit via Reddit JSON API."""
        try:
            url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
            raw = _http_get(
                url,
                headers={"User-Agent": "YouTubeAIEngine/1.0 (content research bot)"},
                timeout=10,
            ).decode("utf-8", errors="replace")
            data = json.loads(raw)
            children = (data.get("data") or {}).get("children") or []
        except Exception as exc:
            log.debug("fetch_reddit_hot(%s) failed: %s", subreddit, exc)
            return []

        results: List[Dict] = []
        for child in children[:limit]:
            post = child.get("data") or {}
            if post.get("stickied"):
                continue
            pub_utc = post.get("created_utc")
            results.append({
                "title":       post.get("title", ""),
                "link":        f"https://www.reddit.com{post.get('permalink', '')}",
                "description": (post.get("selftext") or "")[:300],
                "pub_date":    datetime.fromtimestamp(pub_utc, tz=timezone.utc).isoformat()
                               if pub_utc else "",
                "source":      f"reddit/{subreddit}",
                "score":       post.get("score", 0),
                "comments":    post.get("num_comments", 0),
                "subreddit":   post.get("subreddit", subreddit),
                "url":         post.get("url", ""),
            })

        log.debug("Reddit r/%s: %d posts", subreddit, len(results))
        return results

    # ── World Bank ────────────────────────────────────────────────────────────

    def fetch_worldbank(self, indicator: str = "NY.GDP.MKTP.KD.ZG",
                        countries: str = "all") -> List[Dict]:
        """
        Fetch World Bank indicator data.

        Default indicator: GDP growth (annual %).
        Returns [] on failure.
        """
        try:
            params = urllib.parse.urlencode({
                "format":   "json",
                "per_page": 50,
                "mrv":      5,  # most recent 5 values
            })
            url = f"{FEED_CONFIGS['worldbank']['base']}/country/{countries}/indicator/{indicator}?{params}"
            raw = _http_get(url, timeout=15).decode("utf-8", errors="replace")
            payload = json.loads(raw)
            # World Bank returns [metadata, data_list]
            if not isinstance(payload, list) or len(payload) < 2:
                return []
            records = payload[1] or []
        except Exception as exc:
            log.debug("fetch_worldbank failed: %s", exc)
            return []

        results: List[Dict] = []
        for rec in records:
            if rec.get("value") is None:
                continue
            country_info = rec.get("country") or {}
            results.append({
                "title":       f"{country_info.get('value', 'World')} {indicator} ({rec.get('date', '')}): {rec.get('value', '')}",
                "link":        f"https://data.worldbank.org/indicator/{indicator}",
                "description": f"Indicator: {indicator}, Country: {country_info.get('value','')}, Year: {rec.get('date','')}, Value: {rec.get('value','')}",
                "pub_date":    f"{rec.get('date', '')}-01-01",
                "source":      "worldbank",
                "indicator":   indicator,
                "country":     country_info.get("value", ""),
                "value":       rec.get("value"),
                "year":        rec.get("date", ""),
            })

        log.debug("WorldBank(%s): %d records", indicator, len(results))
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Class 3: ContentScorer
# ─────────────────────────────────────────────────────────────────────────────

_TRENDING_WORDS = frozenset({
    "breaking", "revealed", "shocking", "first", "new", "exclusive",
    "just in", "urgent", "alert", "major", "historic", "record",
    "unprecedented", "surprise", "unexpected", "discovered", "launch",
})

_CATEGORY_BONUSES: Dict[str, int] = {
    "science":       10,
    "technology":    10,
    "tech":          10,
    "finance":       10,
    "business":      10,
    "economics":     10,
    "entertainment":  5,
    "sports":         5,
}


class ContentScorer:
    """Score and rank external content items by YouTube video potential."""

    def score_item(self, item: Dict, category: str = "general") -> float:
        """
        Heuristic scoring 0-100 for video potential.

        Factors:
        - Title word count 8-15  : +15
        - Numbers/stats in title : +20
        - Question mark in title : +10
        - Trending signals       : +15
        - Category bonus         : up to +10
        - Recent (< 24 h)        : +15
        - Long description       : +5
        """
        score = 0.0
        title = str(item.get("title") or "")
        description = str(item.get("description") or "")
        pub_date = str(item.get("pub_date") or "")

        # Title word count bonus
        word_count = len(title.split())
        if 8 <= word_count <= 15:
            score += 15

        # Numbers / statistics in title
        if re.search(r"\b\d[\d,.]*\s*%|\b\d{3,}", title):
            score += 20
        elif re.search(r"\b\d+\b", title):
            score += 10

        # Question in title
        if "?" in title:
            score += 10

        # Trending / high-engagement signals
        title_lower = title.lower()
        if any(word in title_lower for word in _TRENDING_WORDS):
            score += 15

        # Category bonus
        cat_lower = (category or "").lower()
        for cat_key, bonus in _CATEGORY_BONUSES.items():
            if cat_key in cat_lower:
                score += bonus
                break

        # Recency bonus: published in the last 24 hours
        try:
            if pub_date:
                # ISO 8601 or RFC 2822 — try parsing
                pub_dt = self._parse_date(pub_date)
                if pub_dt and (datetime.now(timezone.utc) - pub_dt) < timedelta(hours=24):
                    score += 15
        except Exception:
            pass

        # Long description bonus
        if len(description) > 100:
            score += 5

        return min(score, 100.0)

    def rank_items(self, items: List[Dict],
                   top_n: int = 10) -> List[Dict]:
        """Add 'video_score' to each item, sort descending, return top_n."""
        scored: List[Dict] = []
        for item in items:
            copy = dict(item)
            copy["video_score"] = self.score_item(
                copy, category=copy.get("source", "general")
            )
            scored.append(copy)
        scored.sort(key=lambda x: x["video_score"], reverse=True)
        return scored[:top_n]

    # ── internal ──────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Try several common date formats; return UTC-aware datetime or None."""
        date_str = date_str.strip()
        # ISO 8601 with timezone
        for fmt in (
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%d",
            "%Y-%m-%dT%H:%M",
        ):
            try:
                dt = datetime.strptime(date_str[:25], fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
            except ValueError:
                continue

        # RFC 2822 subset: "Mon, 02 Jan 2006 15:04:05 GMT"
        rfc = re.match(
            r"\w+,\s+(\d{1,2})\s+(\w+)\s+(\d{4})\s+(\d{2}):(\d{2}):(\d{2})",
            date_str,
        )
        if rfc:
            _months = {
                "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            }
            try:
                day, mon, year, h, m, s = (
                    int(rfc.group(1)),
                    _months.get(rfc.group(2).lower(), 1),
                    int(rfc.group(3)),
                    int(rfc.group(4)),
                    int(rfc.group(5)),
                    int(rfc.group(6)),
                )
                return datetime(year, mon, day, h, m, s, tzinfo=timezone.utc)
            except Exception:
                pass

        return None


# ─────────────────────────────────────────────────────────────────────────────
# Class 4: ExternalFeedEngine (orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

class ExternalFeedEngine:
    """Top-level orchestrator for pulling and processing external feeds."""

    def __init__(self, memory=None):
        if memory is None:
            try:
                from core.memory import get_memory  # type: ignore
                memory = get_memory()
            except Exception as exc:
                log.warning("Could not load memory: %s", exc)
        self._memory  = memory
        self._rss     = RSSParser()
        self._api     = APIFetcher()
        self._scorer  = ContentScorer()

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_breaking_news(self, n: int = 20) -> List[Dict]:
        """
        Fetch items from Reuters, BBC and AP RSS feeds.

        Items are scored and ranked; top *n* are returned.
        """
        all_items: List[Dict] = []
        rss_sources = [
            FEED_CONFIGS["reuters_rss"]["url"],
            FEED_CONFIGS["bbc_rss"]["url"],
            FEED_CONFIGS["ap_rss"]["url"],
        ]
        for url in rss_sources:
            try:
                items = self._rss.fetch(url)
                all_items.extend(items)
            except Exception as exc:
                log.debug("fetch_breaking_news rss error (%s): %s", url, exc)

        ranked = self._scorer.rank_items(all_items, top_n=n)
        log.info("fetch_breaking_news: %d total items → top %d", len(all_items), len(ranked))
        return ranked

    def fetch_research_papers(self, topics: Optional[List[str]] = None,
                              n: int = 10) -> List[Dict]:
        """
        Fetch recent ArXiv papers on the given topics.

        Formats results for the content pipeline (adds video_score).
        Returns top *n* papers across all topics.
        """
        if not topics:
            topics = ["artificial intelligence", "energy", "climate change"]

        all_papers: List[Dict] = []
        for topic in topics:
            try:
                papers = self._api.fetch_arxiv(query=topic, max_results=max(5, n // len(topics)))
                all_papers.extend(papers)
            except Exception as exc:
                log.debug("fetch_research_papers(%s) failed: %s", topic, exc)

        # Tag category for scoring
        for p in all_papers:
            p.setdefault("category", "science")

        ranked = self._scorer.rank_items(all_papers, top_n=n)
        log.info("fetch_research_papers: %d papers → top %d", len(all_papers), len(ranked))
        return ranked

    def fetch_social_trends(self, n: int = 20) -> List[Dict]:
        """
        Fetch hot Reddit posts (across TOPIC_SUBREDDITS) and HN top stories.

        Returns top *n* items combined.
        """
        all_items: List[Dict] = []

        # Hacker News
        try:
            hn_items = self._api.fetch_hacker_news(n=30)
            all_items.extend(hn_items)
        except Exception as exc:
            log.debug("fetch_social_trends HN failed: %s", exc)

        # Reddit hot across several subreddits
        for sub in TOPIC_SUBREDDITS[:5]:  # limit to 5 to avoid rate limiting
            try:
                reddit_items = self._api.fetch_reddit_hot(subreddit=sub, limit=10)
                all_items.extend(reddit_items)
            except Exception as exc:
                log.debug("fetch_social_trends reddit/%s failed: %s", sub, exc)

        ranked = self._scorer.rank_items(all_items, top_n=n)
        log.info("fetch_social_trends: %d total → top %d", len(all_items), len(ranked))
        return ranked

    def fetch_api_news(self, query: str = "technology",
                       n: int = 10) -> List[Dict]:
        """
        Try NewsAPI → Guardian → GNews in order; use whichever has an API key.

        Returns top *n* scored items from the first available source.
        """
        # Try each source in preference order
        for fetch_fn, source_name in [
            (lambda: self._api.fetch_newsapi(query=query, page_size=n), "NewsAPI"),
            (lambda: self._api.fetch_guardian(query=query, page_size=n), "Guardian"),
            (lambda: self._api.fetch_gnews(query=query, max_items=n),    "GNews"),
        ]:
            try:
                items = fetch_fn()
                if items:
                    ranked = self._scorer.rank_items(items, top_n=n)
                    log.info("fetch_api_news via %s: %d items", source_name, len(ranked))
                    return ranked
            except Exception as exc:
                log.debug("fetch_api_news %s failed: %s", source_name, exc)

        log.debug("fetch_api_news: no API keys available, returning []")
        return []

    def run_full_fetch(self, save_to_memory: bool = True) -> Dict:
        """
        Run all fetchers, deduplicate results, optionally save to memory.

        Returns a summary dict:
          {total_fetched, sources, top_items[:5]}
        """
        all_items: List[Dict] = []
        sources_hit: List[str] = []

        # Breaking news (RSS)
        try:
            news = self.fetch_breaking_news(n=30)
            if news:
                all_items.extend(news)
                sources_hit.append("rss_news")
        except Exception as exc:
            log.debug("run_full_fetch: breaking_news error: %s", exc)

        # Research papers
        try:
            papers = self.fetch_research_papers(n=10)
            if papers:
                all_items.extend(papers)
                sources_hit.append("arxiv")
        except Exception as exc:
            log.debug("run_full_fetch: research_papers error: %s", exc)

        # Social trends
        try:
            social = self.fetch_social_trends(n=20)
            if social:
                all_items.extend(social)
                sources_hit.append("social")
        except Exception as exc:
            log.debug("run_full_fetch: social_trends error: %s", exc)

        # API-backed news (best available key)
        try:
            api_news = self.fetch_api_news(n=15)
            if api_news:
                all_items.extend(api_news)
                sources_hit.append("api_news")
        except Exception as exc:
            log.debug("run_full_fetch: api_news error: %s", exc)

        # Deduplicate
        unique_items = self._deduplicate(all_items)

        # Ensure all have a video_score
        for item in unique_items:
            if "video_score" not in item:
                item["video_score"] = self._scorer.score_item(item)

        # Sort by score
        unique_items.sort(key=lambda x: x.get("video_score", 0), reverse=True)

        # Persist to memory
        if save_to_memory and self._memory is not None:
            saved_count = 0
            for item in unique_items:
                try:
                    self._memory.save_external_item({
                        "title":        item.get("title", ""),
                        "description":  item.get("description", ""),
                        "link":         item.get("link", ""),
                        "pub_date":     item.get("pub_date", ""),
                        "source":       item.get("source", ""),
                        "video_score":  item.get("video_score", 0),
                        "raw":          json.dumps(item),
                        "fetched_at":   datetime.now(timezone.utc).isoformat(),
                    })
                    saved_count += 1
                except Exception as exc:
                    log.debug("save_external_item failed: %s", exc)
            log.info("run_full_fetch: saved %d items to memory", saved_count)

        result = {
            "total_fetched": len(all_items),
            "unique_items":  len(unique_items),
            "sources":       sources_hit,
            "top_items":     unique_items[:5],
        }
        log.info(
            "run_full_fetch complete: %d raw → %d unique, sources=%s",
            len(all_items), len(unique_items), sources_hit,
        )
        return result

    def _deduplicate(self, items: List[Dict]) -> List[Dict]:
        """
        Remove near-duplicate items using word-overlap on titles.

        If two items share > 60% of their title words (Jaccard), keep the
        one with the higher video_score; if scores are equal, keep the first.
        """
        if not items:
            return []

        def _words(text: str) -> frozenset:
            return frozenset(re.findall(r"[a-z0-9]+", text.lower()))

        scored = []
        for item in items:
            copy = dict(item)
            if "video_score" not in copy:
                copy["video_score"] = self._scorer.score_item(copy)
            copy["_words"] = _words(copy.get("title", ""))
            scored.append(copy)

        kept: List[Dict] = []
        for candidate in scored:
            is_dup = False
            cw = candidate["_words"]
            if not cw:
                kept.append(candidate)
                continue
            for i, existing in enumerate(kept):
                ew = existing["_words"]
                if not ew:
                    continue
                union = cw | ew
                if not union:
                    continue
                jaccard = len(cw & ew) / len(union)
                if jaccard > 0.6:
                    # Duplicate found — keep higher scorer
                    if candidate.get("video_score", 0) > existing.get("video_score", 0):
                        kept[i] = candidate
                    is_dup = True
                    break
            if not is_dup:
                kept.append(candidate)

        # Strip internal helper key
        for item in kept:
            item.pop("_words", None)

        return kept

    def print_report(self, result: Dict) -> None:
        """Print a formatted summary of a full-fetch run."""
        sep = "=" * 65
        print(f"\n{sep}")
        print("  EXTERNAL FEED ENGINE REPORT")
        print(sep)
        print(f"  Raw items fetched : {result.get('total_fetched', 0)}")
        print(f"  Unique items      : {result.get('unique_items', 0)}")
        sources = result.get("sources") or []
        print(f"  Sources hit       : {', '.join(sources) if sources else 'none'}")

        top = result.get("top_items") or []
        if top:
            print(f"\n  Top {len(top)} Items (by video score):")
            for i, item in enumerate(top, 1):
                score = item.get("video_score", 0)
                title = item.get("title", "")
                if len(title) > 70:
                    title = title[:70] + "…"
                src = item.get("source", "?")
                print(f"    {i}. [{score:.0f}] ({src}) {title}")

        if result.get("error"):
            print(f"\n  Error: {result['error']}")

        print(f"{sep}\n")
