"""
Phase 1 — Trend Fetcher
========================
Pulls trending topics from YouTube RSS, Reddit public API, and Google Trends.
All sources are attempted; failures are logged and skipped gracefully.
"""
import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
import xml.etree.ElementTree as ET

from utils.api_client import get_session, RateLimiter

log = logging.getLogger("viral_pipeline.trends.fetcher")

_rl = RateLimiter(calls_per_second=0.5)


@dataclass
class RawTrend:
    title: str
    source: str                 # youtube | reddit | google
    score: float = 0.0
    views: int = 0
    comments: int = 0
    upvotes: int = 0
    velocity: float = 0.0      # normalised 0-1
    category: str = "general"
    url: str = ""
    fetched_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ── YouTube ───────────────────────────────────────────────────────────────────

def fetch_youtube_trending(region: str = "US",
                           max_items: int = 20) -> List[RawTrend]:
    """
    Scrape YouTube trending via the public Atom feed.
    Falls back to the InnerTube /browse endpoint if the feed fails.
    """
    results: List[RawTrend] = []
    sess = get_session()

    # Method 1: YouTube RSS trending feed
    try:
        _rl.wait()
        url = f"https://www.youtube.com/feeds/videos.xml?chart=mostpopular&hl=en&gl={region}"
        resp = sess.get(url, timeout=15)
        if resp.ok:
            root = ET.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom",
                  "yt": "http://www.youtube.com/xml/schemas/2015",
                  "media": "http://search.yahoo.com/mrss/"}
            for entry in root.findall("atom:entry", ns)[:max_items]:
                title_el = entry.find("atom:title", ns)
                title = title_el.text if title_el is not None else ""
                link_el = entry.find("atom:link", ns)
                url_ = link_el.get("href", "") if link_el is not None else ""
                stats = entry.find(".//yt:statistics", ns)
                views = int(stats.get("viewCount", 0)) if stats is not None else 0
                results.append(RawTrend(
                    title=title, source="youtube",
                    views=views, url=url_,
                    velocity=min(1.0, views / 10_000_000),
                ))
            log.info(f"YouTube RSS: fetched {len(results)} trending videos")
            return results
    except Exception as e:
        log.warning(f"YouTube RSS failed: {e}")

    # Method 2: InnerTube browse (no API key needed)
    try:
        _rl.wait()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0",
        }
        body = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20240101",
                    "hl": "en",
                    "gl": region,
                }
            },
            "browseId": "FEtrending",
        }
        r = sess.post(
            "https://www.youtube.com/youtubei/v1/browse"
            "?key=AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8",
            json=body, headers=headers, timeout=20,
        )
        if r.ok:
            data = r.json()
            items = _extract_innertube_titles(data)
            for title in items[:max_items]:
                results.append(RawTrend(
                    title=title, source="youtube", velocity=0.6
                ))
            log.info(f"YouTube InnerTube: found {len(items)} trending titles")
    except Exception as e:
        log.warning(f"YouTube InnerTube failed: {e}")

    return results


def _extract_innertube_titles(data: dict) -> List[str]:
    titles = []
    def walk(obj):
        if isinstance(obj, dict):
            if "videoRenderer" in obj:
                vr = obj["videoRenderer"]
                runs = vr.get("title", {}).get("runs", [])
                if runs:
                    titles.append(runs[0].get("text", ""))
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for item in obj:
                walk(item)
    walk(data)
    return titles


# ── Reddit ────────────────────────────────────────────────────────────────────

SUBREDDITS = [
    ("r/trending",      1.0),
    ("r/technology",    0.9),
    ("r/worldnews",     0.85),
    ("r/science",       0.8),
    ("r/artificial",    0.9),
    ("r/finance",       0.75),
    ("r/health",        0.7),
]


def fetch_reddit_trends(max_per_sub: int = 5) -> List[RawTrend]:
    results: List[RawTrend] = []
    sess = get_session()
    sess.headers.update({"User-Agent": "ViralPipeline/1.0 trend-bot"})

    for sub, weight in SUBREDDITS:
        try:
            _rl.wait()
            url = f"https://www.reddit.com/{sub}/hot.json?limit={max_per_sub}"
            r = sess.get(url, timeout=15)
            if not r.ok:
                continue
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                d = post.get("data", {})
                title = d.get("title", "")
                score = d.get("score", 0)
                comments = d.get("num_comments", 0)
                # Skip stickied/mod posts
                if d.get("stickied") or not title:
                    continue
                velocity = min(1.0, (score / 50000) * weight)
                results.append(RawTrend(
                    title=title, source="reddit",
                    upvotes=score, comments=comments,
                    velocity=velocity,
                    category=sub.split("/")[-1],
                    url=f"https://reddit.com{d.get('permalink', '')}",
                ))
        except Exception as e:
            log.warning(f"Reddit {sub} failed: {e}")

    log.info(f"Reddit: fetched {len(results)} trending posts")
    return results


# ── Google Trends ─────────────────────────────────────────────────────────────

def fetch_google_trends(geo: str = "US",
                        timeframe: str = "now 1-d") -> List[RawTrend]:
    results: List[RawTrend] = []
    try:
        from pytrends.request import TrendReq
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        pt = TrendReq(hl="en-US", tz=300, timeout=(10, 30),
                      requests_args={"verify": False})
        df = pt.trending_searches(pn="united_states")
        for i, row in df.iterrows():
            title = str(row.get(0, row.iloc[0] if len(row) else ""))
            if title:
                results.append(RawTrend(
                    title=title, source="google",
                    velocity=max(0.1, 1.0 - i * 0.05),
                    category="trending",
                ))
        log.info(f"Google Trends: fetched {len(results)} trending searches")
    except ImportError:
        log.warning("pytrends not installed — skipping Google Trends")
    except Exception as e:
        log.warning(f"Google Trends failed: {e}")

    return results


# ── Aggregator ────────────────────────────────────────────────────────────────

async def fetch_all_trends() -> List[RawTrend]:
    """Fetch from all sources concurrently."""
    loop = asyncio.get_event_loop()
    tasks = [
        loop.run_in_executor(None, fetch_youtube_trending),
        loop.run_in_executor(None, fetch_reddit_trends),
        loop.run_in_executor(None, fetch_google_trends),
    ]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)
    all_trends: List[RawTrend] = []
    for r in results_list:
        if isinstance(r, Exception):
            log.warning(f"Source error: {r}")
        elif isinstance(r, list):
            all_trends.extend(r)

    log.info(f"Total raw trends fetched: {len(all_trends)}")
    return all_trends
