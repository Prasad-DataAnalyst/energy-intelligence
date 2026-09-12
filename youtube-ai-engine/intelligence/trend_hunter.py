"""
intelligence/trend_hunter.py — Multi-Source Trend Scraper
==========================================================
Aggregates trending topics from:
  1. YouTube Trending RSS + InnerTube API
  2. Reddit hot posts (7 relevant subreddits)
  3. Google Trends (pytrends)
  4. NewsAPI headlines
  5. Wikipedia current events
  6. Hacker News front page
  7. Exploding Topics RSS

Each source contributes a score; combined scores are normalised 0–1.
Results are deduped and ranked before returning to the Brain.
"""
import json
import logging
import os
import re
import ssl
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brain.trend_hunter")

HERE = Path(__file__).parent.parent

# Subreddits relevant to technology / energy / finance / science audiences
SUBREDDITS = [
    "technology", "artificial", "MachineLearning",
    "energy", "Futurology", "science", "business",
]

YOUTUBE_TRENDING_RSS = "https://www.youtube.com/feeds/videos.xml?chart=mostpopular"

_INNERTUBE_HEADERS = {
    "Content-Type":  "application/json",
    "User-Agent":    "Mozilla/5.0 (compatible; TrendHunter/1.0)",
    "X-YouTube-Client-Name":    "1",
    "X-YouTube-Client-Version": "2.20240101",
}


def _make_session():
    """Requests session with SSL bypass + retry."""
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        sess = requests.Session()
        sess.verify = False
        retry = Retry(total=3, backoff_factor=0.5,
                      status_forcelist=[429, 500, 502, 503, 504])
        adapter = HTTPAdapter(max_retries=retry)
        sess.mount("http://",  adapter)
        sess.mount("https://", adapter)
        return sess
    except ImportError:
        return None


class TrendHunter:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory  = memory or get_memory()
        self._sess   = _make_session()

    # ── YouTube ────────────────────────────────────────────────────────────

    def _youtube_rss(self) -> List[Dict]:
        if not self._sess:
            return []
        try:
            import feedparser
            feed   = feedparser.parse(YOUTUBE_TRENDING_RSS)
            result = []
            for i, e in enumerate(feed.entries[:20]):
                result.append({
                    "title":  e.get("title", ""),
                    "source": "youtube_rss",
                    "score":  1.0 - (i * 0.04),
                })
            return result
        except Exception as ex:
            log.debug(f"YouTube RSS failed: {ex}")
            return []

    def _youtube_innertube(self) -> List[Dict]:
        if not self._sess:
            return []
        try:
            r = self._sess.post(
                "https://www.youtube.com/youtubei/v1/browse",
                headers=_INNERTUBE_HEADERS,
                json={"browseId": "FEtrending",
                      "context": {"client": {"clientName": "WEB",
                                             "clientVersion": "2.20240101"}}},
                timeout=20,
            )
            if not r.ok:
                return []
            videos = []
            data   = r.json()
            items  = (data.get("contents", {})
                         .get("twoColumnBrowseResultsRenderer", {})
                         .get("tabs", [{}])[0]
                         .get("tabRenderer", {})
                         .get("content", {})
                         .get("sectionListRenderer", {})
                         .get("contents", []))
            for section in items:
                for shelf in (section.get("itemSectionRenderer", {})
                                     .get("contents", [])):
                    for item in (shelf.get("shelfRenderer", {})
                                      .get("content", {})
                                      .get("expandedShelfContentsRenderer", {})
                                      .get("items", [])):
                        vr = item.get("videoRenderer", {})
                        title = (vr.get("title", {})
                                   .get("runs", [{}])[0]
                                   .get("text", ""))
                        if title:
                            videos.append({
                                "title":  title,
                                "source": "youtube_innertube",
                                "score":  0.9,
                            })
            return videos[:20]
        except Exception as ex:
            log.debug(f"YouTube InnerTube failed: {ex}")
            return []

    # ── Reddit ─────────────────────────────────────────────────────────────

    def _reddit_hot(self) -> List[Dict]:
        if not self._sess:
            return []
        results = []
        headers = {"User-Agent": "TrendHunter/1.0 (research bot)"}
        for sub in SUBREDDITS:
            try:
                r = self._sess.get(
                    f"https://www.reddit.com/r/{sub}/hot.json",
                    headers=headers, timeout=15,
                    params={"limit": 10},
                )
                if not r.ok:
                    continue
                for post in r.json().get("data", {}).get("children", []):
                    d = post.get("data", {})
                    if d.get("is_self") or d.get("over_18"):
                        continue
                    score = min(d.get("score", 0) / 50_000, 1.0)
                    results.append({
                        "title":  d.get("title", ""),
                        "source": f"reddit/{sub}",
                        "score":  score,
                    })
                time.sleep(0.5)
            except Exception as ex:
                log.debug(f"Reddit {sub} failed: {ex}")
        return results

    # ── Google Trends ──────────────────────────────────────────────────────

    def _google_trends(self) -> List[Dict]:
        try:
            from pytrends.request import TrendReq
            pt = TrendReq(
                hl="en-US", tz=0,
                requests_args={"verify": False},
                retries=2, backoff_factor=0.5,
            )
            df = pt.trending_searches(pn="united_states")
            results = []
            for i, row in df.iterrows():
                if i >= 20:
                    break
                results.append({
                    "title":  str(row[0]),
                    "source": "google_trends",
                    "score":  1.0 - (i * 0.04),
                })
            return results
        except Exception as ex:
            log.debug(f"Google Trends failed: {ex}")
            return []

    # ── NewsAPI ────────────────────────────────────────────────────────────

    def _newsapi(self) -> List[Dict]:
        api_key = os.getenv("NEWS_API_KEY", "")
        if not api_key or not self._sess:
            return []
        try:
            r = self._sess.get(
                "https://newsapi.org/v2/top-headlines",
                params={"category": "technology", "country": "us",
                        "pageSize": 20, "apiKey": api_key},
                timeout=15,
            )
            if not r.ok:
                return []
            results = []
            for i, art in enumerate(r.json().get("articles", [])):
                results.append({
                    "title":  art.get("title", ""),
                    "source": "newsapi",
                    "score":  0.85 - (i * 0.02),
                })
            return results
        except Exception as ex:
            log.debug(f"NewsAPI failed: {ex}")
            return []

    # ── Wikipedia current events ───────────────────────────────────────────

    def _wikipedia_current_events(self) -> List[Dict]:
        if not self._sess:
            return []
        try:
            today = datetime.now().strftime("%Y_%B_%-d")
            r     = self._sess.get(
                f"https://en.wikipedia.org/wiki/Portal:Current_events/{today}",
                timeout=15,
            )
            if not r.ok:
                return []
            from html.parser import HTMLParser

            class EventParser(HTMLParser):
                def __init__(self):
                    super().__init__()
                    self.events: List[str] = []
                    self._in_li = False

                def handle_starttag(self, tag, attrs):
                    if tag == "li":
                        self._in_li = True
                    if tag == "a" and self._in_li:
                        for k, v in attrs:
                            if k == "title" and v:
                                self.events.append(v)

                def handle_endtag(self, tag):
                    if tag == "li":
                        self._in_li = False

            p = EventParser()
            p.feed(r.text)
            return [{"title": e, "source": "wikipedia", "score": 0.6}
                    for e in p.events[:15]]
        except Exception as ex:
            log.debug(f"Wikipedia current events failed: {ex}")
            return []

    # ── Hacker News ────────────────────────────────────────────────────────

    def _hacker_news(self) -> List[Dict]:
        if not self._sess:
            return []
        try:
            r = self._sess.get(
                "https://hacker-news.firebaseio.com/v0/topstories.json",
                timeout=15,
            )
            if not r.ok:
                return []
            ids = r.json()[:20]
            results = []
            for story_id in ids[:10]:   # limit API calls
                sr = self._sess.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                    timeout=10,
                )
                if sr.ok:
                    d = sr.json()
                    title = d.get("title", "")
                    score = min(d.get("score", 0) / 3000, 1.0)
                    if title:
                        results.append({
                            "title":  title,
                            "source": "hackernews",
                            "score":  score,
                        })
                time.sleep(0.2)
            return results
        except Exception as ex:
            log.debug(f"Hacker News failed: {ex}")
            return []

    # ── Exploding Topics ───────────────────────────────────────────────────

    def _exploding_topics(self) -> List[Dict]:
        if not self._sess:
            return []
        try:
            import feedparser
            feed = feedparser.parse("https://explodingtopics.com/rss-feed")
            return [
                {"title": e.get("title", ""), "source": "exploding_topics", "score": 0.75}
                for e in feed.entries[:10]
            ]
        except Exception as ex:
            log.debug(f"Exploding Topics failed: {ex}")
            return []

    # ── Aggregation ────────────────────────────────────────────────────────

    @staticmethod
    def _normalise(items: List[Dict]) -> List[Dict]:
        """Dedupe by title similarity and normalise scores 0–1."""
        seen:    Dict[str, Dict] = {}
        for item in items:
            title = re.sub(r"\s+", " ", item["title"].strip()).lower()
            if not title or len(title) < 5:
                continue
            key = re.sub(r"[^a-z0-9 ]", "", title)[:50]
            if key in seen:
                # Merge: take higher score, accumulate sources
                seen[key]["score"] = max(seen[key]["score"], item["score"])
                seen[key].setdefault("sources", [seen[key]["source"]])
                seen[key]["sources"].append(item["source"])
            else:
                seen[key] = {**item, "sources": [item["source"]]}

        ranked = sorted(seen.values(), key=lambda x: x["score"], reverse=True)
        # Normalise to 0–1
        if ranked:
            max_s = ranked[0]["score"] or 1.0
            for r in ranked:
                r["score"] = round(r["score"] / max_s, 4)
        return ranked

    def fetch_all(self) -> List[Dict]:
        """Fetch trends from all sources and return ranked, deduped list."""
        log.info("Trend hunter: fetching from all sources…")
        all_items: List[Dict] = []

        sources = [
            ("YouTube RSS",         self._youtube_rss),
            ("YouTube InnerTube",   self._youtube_innertube),
            ("Reddit hot",          self._reddit_hot),
            ("Google Trends",       self._google_trends),
            ("NewsAPI",             self._newsapi),
            ("Wikipedia events",    self._wikipedia_current_events),
            ("Hacker News",         self._hacker_news),
            ("Exploding Topics",    self._exploding_topics),
        ]

        for name, fn in sources:
            try:
                items = fn()
                log.info(f"  {name}: {len(items)} items")
                all_items.extend(items)
            except Exception as e:
                log.warning(f"  {name} crashed: {e}")

        ranked = self._normalise(all_items)
        log.info(f"Trend hunter: {len(ranked)} unique trends after dedup")
        return ranked[:50]   # top 50
