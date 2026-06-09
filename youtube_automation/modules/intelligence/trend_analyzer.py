"""
modules/intelligence/trend_analyzer.py
Async multi-source trend scanner. Finds best video opportunity right now.
"""

# ── Inline package bootstrapping ──────────────────────────────────────────────
try:
    import aiohttp
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "aiohttp", "-q"])
    import aiohttp

try:
    import feedparser
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "feedparser", "-q"])
    import feedparser

try:
    from pytrends.request import TrendReq
except ImportError:
    import subprocess
    subprocess.run(["pip", "install", "pytrends", "-q"])
    from pytrends.request import TrendReq

# ── Standard library ──────────────────────────────────────────────────────────
import asyncio
import datetime
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Third-party (always available) ────────────────────────────────────────────
import requests

# ── Logging ───────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
UA = "Mozilla/5.0 (compatible; MindFuelBot/1.0)"
HTTP_TIMEOUT = 10  # seconds

# High-CPM keyword sets (used for category detection & scoring bonuses)
_HIGH_CPM_KEYWORDS = {
    "ai", "artificial intelligence", "machine learning", "chatgpt", "openai",
    "money", "finance", "investing", "stock", "crypto", "bitcoin", "economy",
    "budget", "salary", "wealth", "retire", "401k", "ira", "mortgage",
    "health", "cancer", "diabetes", "fitness", "diet", "mental health",
    "career", "job", "hiring", "layoff", "remote work", "promotion",
}
_POLITICAL_KEYWORDS = {
    "trump", "biden", "democrat", "republican", "congress", "senate",
    "election", "vote", "partisan", "gop", "maga", "liberal", "conservative",
    "white house", "president", "politician", "impeach",
}
_CONTROVERSIAL_KEYWORDS = {
    "ban", "shock", "scandal", "exposed", "secret", "truth", "lie",
    "dangerous", "toxic", "warning", "crisis", "collapse", "fraud",
    "scam", "manipulation", "cover up", "hidden", "reveal",
}

# Category detection map
_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "money":   ["money", "finance", "invest", "stock", "crypto", "bitcoin",
                "economy", "budget", "salary", "wealth", "retire", "401k",
                "ira", "mortgage", "tax", "bank", "fund", "market"],
    "tech":    ["ai", "artificial intelligence", "machine learning", "chatgpt",
                "openai", "software", "app", "cyber", "hack", "robot",
                "automation", "tech", "startup", "algorithm", "data"],
    "health":  ["health", "cancer", "diabetes", "fitness", "diet", "mental",
                "drug", "vaccine", "virus", "medicine", "hospital", "fda",
                "sleep", "nutrition", "weight", "exercise"],
    "science": ["science", "climate", "space", "nasa", "physics", "biology",
                "research", "study", "discover", "quantum", "gene", "dna"],
    "career":  ["career", "job", "hiring", "layoff", "remote", "work",
                "promotion", "salary", "resume", "linkedin", "interview"],
    "mindset": ["mindset", "habit", "motivation", "discipline", "success",
                "psychology", "stoic", "productivity", "focus", "goal"],
}

# RSS feeds — no API key needed
_RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml",
    "https://feeds.feedburner.com/TechCrunch",
]

# Reddit endpoints — public JSON, no auth
_REDDIT_ENDPOINTS = [
    "https://www.reddit.com/r/all.json?limit=50",
    "https://www.reddit.com/r/technology.json?limit=25",
    "https://www.reddit.com/r/worldnews.json?limit=25",
    "https://www.reddit.com/r/science.json?limit=25",
]

# Hacker News
_HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
_HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{}.json"


# ── Dataclass ─────────────────────────────────────────────────────────────────

@dataclass
class TrendOpportunity:
    topic: str
    angle: str
    hook: str
    key_facts: List[str]
    emotional_trigger: str
    target_audience: str
    predicted_ctr: float
    predicted_rpm: float
    competing_videos_count: int
    opportunity_window_hours: int
    visual_style_recommendation: str
    sources: List[str]
    score: float
    category: str  # "money|tech|health|science|career|mindset"


# ── Helper utilities ──────────────────────────────────────────────────────────

def _detect_category(text: str) -> str:
    """Return the best-fit channel category for a topic string."""
    text_lower = text.lower()
    best_cat = "mindset"
    best_count = 0
    for cat, keywords in _CATEGORY_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in text_lower)
        if count > best_count:
            best_count = count
            best_cat = cat
    return best_cat


def _is_high_cpm(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _HIGH_CPM_KEYWORDS)


def _is_political(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _POLITICAL_KEYWORDS)


def _is_controversial(text: str) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in _CONTROVERSIAL_KEYWORDS)


def _extract_keywords(text: str, top_n: int = 5) -> List[str]:
    """Naive keyword extraction — split, dedupe, drop stop-words."""
    STOP = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "this", "that", "it", "its", "as", "so", "do",
        "up", "out", "how", "why", "what", "who", "when", "will", "can",
        "about", "into", "over", "after", "new", "just", "now",
    }
    words = [w.strip("\"'.,!?:;()[]") for w in text.lower().split()]
    seen: dict = {}
    for w in words:
        if len(w) > 3 and w not in STOP:
            seen[w] = seen.get(w, 0) + 1
    return [k for k, _ in sorted(seen.items(), key=lambda x: -x[1])][:top_n]


def _normalize_0_1(value: float, min_v: float, max_v: float) -> float:
    if max_v <= min_v:
        return 0.5
    return max(0.0, min(1.0, (value - min_v) / (max_v - min_v)))


# ── Video angle generator ─────────────────────────────────────────────────────

def _generate_video_angles(topic: str) -> List[dict]:
    """Generate 5 angles for a given topic, each scored on 3 dimensions."""
    topic_cap = topic.strip().title()
    # Life-area slot varies by detected category
    cat = _detect_category(topic)
    life_area_map = {
        "money": "Finances",
        "tech": "Future",
        "health": "Health",
        "science": "Life",
        "career": "Career",
        "mindset": "Mindset",
    }
    life_area = life_area_map.get(cat, "Life")

    angles = [
        {
            "type": "SHOCKING_TRUTH",
            "title": f"The Truth About {topic_cap} Nobody Is Saying",
            "hook": f"Everyone is talking about {topic_cap}, but nobody is saying this...",
            "search_potential": 0.80,
            "competition_gap": 0.65,
            "emotional_strength": 0.90,
        },
        {
            "type": "DATA",
            "title": f"The Numbers Behind {topic_cap} Are Insane",
            "hook": f"The data on {topic_cap} will change how you see everything.",
            "search_potential": 0.70,
            "competition_gap": 0.72,
            "emotional_strength": 0.75,
        },
        {
            "type": "PREDICTION",
            "title": f"What {topic_cap} Means For Your {life_area}",
            "hook": f"If {topic_cap} keeps trending, here is what changes for you.",
            "search_potential": 0.75,
            "competition_gap": 0.60,
            "emotional_strength": 0.80,
        },
        {
            "type": "EXPLAINER",
            "title": f"Why {topic_cap} Is Happening (Simple Explanation)",
            "hook": f"Here is the clearest explanation of {topic_cap} you will find.",
            "search_potential": 0.85,
            "competition_gap": 0.55,
            "emotional_strength": 0.65,
        },
        {
            "type": "OPINION",
            "title": f"{topic_cap}: Everyone Is Getting This Wrong",
            "hook": f"The mainstream take on {topic_cap} is completely backwards.",
            "search_potential": 0.72,
            "competition_gap": 0.68,
            "emotional_strength": 0.88,
        },
    ]

    # Composite score for each angle
    for a in angles:
        a["composite"] = (
            a["search_potential"] * 0.40
            + a["competition_gap"] * 0.30
            + a["emotional_strength"] * 0.30
        )
    return angles


def _best_angle(topic: str) -> dict:
    angles = _generate_video_angles(topic)
    return max(angles, key=lambda a: a["composite"])


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score_trend(
    topic: str,
    sources_data: Dict[str, Any],
    topic_age_hours: float = 24.0,
) -> float:
    """
    Score formula (0–1 per component, weighted):
        score = (
            google_momentum * 0.30 +
            reddit_score    * 0.25 +
            news_velocity   * 0.20 +
            hn_engagement   * 0.15 +
            recency_bonus   * 0.10
        )
    Plus multiplier bonuses applied after weighting.
    """
    # ── Extract raw signals ───────────────────────────────────────────────────
    google_raw = float(sources_data.get("google_momentum", 0))
    reddit_raw = float(sources_data.get("reddit_score", 0))
    news_raw   = float(sources_data.get("news_velocity", 0))
    hn_raw     = float(sources_data.get("hn_engagement", 0))

    # Normalise each signal to [0, 1]
    # Google: raw value is percentage increase (e.g. 1000 = 1000% rise)
    google_norm = _normalize_0_1(google_raw, 0, 5000)
    # Reddit: raw composite score (upvotes * ratio)
    reddit_norm = _normalize_0_1(reddit_raw, 0, 100_000)
    # News: article count in last 6h
    news_norm   = _normalize_0_1(news_raw, 0, 50)
    # HN: score + descendants combined
    hn_norm     = _normalize_0_1(hn_raw, 0, 5000)
    # Recency: inverse of age — fresher is better
    recency_norm = max(0.0, 1.0 - (topic_age_hours / 48.0))

    base = (
        google_norm * 0.30
        + reddit_norm * 0.25
        + news_norm   * 0.20
        + hn_norm     * 0.15
        + recency_norm * 0.10
    )

    # ── Multiplier bonuses ────────────────────────────────────────────────────
    multiplier = 1.0

    if topic_age_hours < 6:
        multiplier += 0.20   # fresh topic bonus
    if _is_high_cpm(topic):
        multiplier += 0.25   # high-CPM category bonus
    if _is_controversial(topic):
        multiplier += 0.15   # controversy engagement bonus
    if _is_political(topic):
        multiplier -= 0.20   # advertiser-unsafe penalty

    multiplier = max(0.0, multiplier)
    return min(1.0, base * multiplier)


# ── TrendAnalyzer ─────────────────────────────────────────────────────────────

class TrendAnalyzer:
    """Async multi-source trend scanner."""

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    # ── Session helpers ───────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers={"User-Agent": UA},
                timeout=aiohttp.ClientTimeout(total=HTTP_TIMEOUT),
            )
        return self._session

    async def _close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _fetch_json(self, url: str) -> Optional[Any]:
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                log.debug("fetch_json %s → HTTP %s", url, resp.status)
        except asyncio.TimeoutError:
            log.debug("fetch_json timeout: %s", url)
        except Exception as exc:
            log.debug("fetch_json error %s: %s", url, exc)
        return None

    # ── Source 1: Google Trends ───────────────────────────────────────────────

    async def _source_google_trends(self) -> Dict[str, Dict[str, Any]]:
        """
        Fetch trending searches from pytrends (runs in executor to avoid
        blocking the event loop — pytrends is synchronous).
        Returns {topic: {google_momentum, age_hours}}.
        """
        def _sync_fetch() -> Dict[str, Dict[str, Any]]:
            results: Dict[str, Dict[str, Any]] = {}
            try:
                pytrends = TrendReq(hl="en-US", tz=360)

                # Daily trending searches
                try:
                    df = pytrends.trending_searches(pn="united_states")
                    for topic in df[0].tolist()[:20]:
                        if topic and isinstance(topic, str):
                            results[topic] = {
                                "google_momentum": 1500,  # trending = high momentum
                                "age_hours": 12,
                                "source": "google_trends_daily",
                            }
                except Exception as exc:
                    log.debug("Google Trends daily error: %s", exc)

                # Realtime trending
                try:
                    rt = pytrends.realtime_trending_searches(pn="US")
                    if rt is not None and not rt.empty:
                        for idx, row in rt.iterrows():
                            title_col = None
                            for col in ("title", "entityNames", "query"):
                                if col in rt.columns:
                                    title_col = col
                                    break
                            if title_col is None:
                                break
                            topic = str(row[title_col])[:100]
                            if topic and topic not in results:
                                results[topic] = {
                                    "google_momentum": 2500,
                                    "age_hours": 4,
                                    "source": "google_trends_realtime",
                                }
                            if len(results) >= 30:
                                break
                except Exception as exc:
                    log.debug("Google Trends realtime error: %s", exc)

            except Exception as exc:
                log.debug("Google Trends outer error: %s", exc)

            return results

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _sync_fetch),
                timeout=25,
            )
        except asyncio.TimeoutError:
            log.debug("Google Trends timed out")
            return {}
        except Exception as exc:
            log.debug("Google Trends executor error: %s", exc)
            return {}

    # ── Source 2: Reddit ──────────────────────────────────────────────────────

    async def _source_reddit(self) -> Dict[str, Dict[str, Any]]:
        """
        Fetch hot posts from Reddit public JSON endpoints (no auth needed).
        Returns {topic_keyword: {reddit_score, age_hours, upvote_ratio}}.
        """
        results: Dict[str, Dict[str, Any]] = {}

        for endpoint in _REDDIT_ENDPOINTS:
            try:
                data = await self._fetch_json(endpoint)
                if not data:
                    continue
                children = data.get("data", {}).get("children", [])
                for post in children:
                    pdata = post.get("data", {})
                    score = pdata.get("score", 0)
                    ratio = pdata.get("upvote_ratio", 0.0)
                    title = pdata.get("title", "")
                    created_utc = pdata.get("created_utc", 0)

                    # Filter: high quality posts only
                    if ratio < 0.85 or score < 5000 or not title:
                        continue

                    age_hours = (time.time() - created_utc) / 3600 if created_utc else 24
                    composite = score * ratio

                    # Extract keywords and register each as a potential topic
                    keywords = _extract_keywords(title, top_n=3)
                    for kw in keywords:
                        if kw in results:
                            results[kw]["reddit_score"] = max(
                                results[kw]["reddit_score"], composite
                            )
                        else:
                            results[kw] = {
                                "reddit_score": composite,
                                "age_hours": age_hours,
                                "source": "reddit",
                                "full_title": title,
                            }

            except Exception as exc:
                log.debug("Reddit endpoint %s error: %s", endpoint, exc)

        return results

    # ── Source 3: News RSS ────────────────────────────────────────────────────

    async def _source_news_rss(self) -> Dict[str, Dict[str, Any]]:
        """
        Parse RSS feeds and count article velocity per topic in last 6 hours.
        Returns {topic: {news_velocity, age_hours}}.
        """
        def _parse_feeds() -> Dict[str, Dict[str, Any]]:
            topic_counts: Dict[str, Dict[str, Any]] = {}
            now = datetime.datetime.utcnow()
            cutoff = now - datetime.timedelta(hours=6)

            for feed_url in _RSS_FEEDS:
                try:
                    feed = feedparser.parse(feed_url)
                    for entry in feed.entries:
                        # Parse publication date
                        pub_dt = None
                        if hasattr(entry, "published_parsed") and entry.published_parsed:
                            try:
                                pub_dt = datetime.datetime(*entry.published_parsed[:6])
                            except Exception:
                                pass
                        if pub_dt is None:
                            pub_dt = now  # assume recent if unparseable

                        if pub_dt < cutoff:
                            continue

                        title = getattr(entry, "title", "")
                        if not title:
                            continue

                        age_hours = max(0.0, (now - pub_dt).total_seconds() / 3600)
                        keywords = _extract_keywords(title, top_n=3)
                        for kw in keywords:
                            if kw in topic_counts:
                                topic_counts[kw]["news_velocity"] += 1
                                topic_counts[kw]["age_hours"] = min(
                                    topic_counts[kw]["age_hours"], age_hours
                                )
                            else:
                                topic_counts[kw] = {
                                    "news_velocity": 1,
                                    "age_hours": age_hours,
                                    "source": "news_rss",
                                    "full_title": title,
                                }
                except Exception as exc:
                    log.debug("RSS feed %s error: %s", feed_url, exc)

            # Only keep topics with >10 articles (high velocity)
            return {k: v for k, v in topic_counts.items() if v["news_velocity"] > 10}

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _parse_feeds),
                timeout=20,
            )
        except asyncio.TimeoutError:
            log.debug("RSS parsing timed out")
            return {}
        except Exception as exc:
            log.debug("RSS error: %s", exc)
            return {}

    # ── Source 4: Hacker News ─────────────────────────────────────────────────

    async def _source_hacker_news(self) -> Dict[str, Dict[str, Any]]:
        """
        Fetch top HN stories and return high-engagement topics.
        Returns {topic: {hn_engagement, age_hours}}.
        """
        results: Dict[str, Dict[str, Any]] = {}
        try:
            story_ids = await self._fetch_json(_HN_TOP_URL)
            if not story_ids or not isinstance(story_ids, list):
                return results

            top_ids = story_ids[:30]

            # Fetch story details concurrently
            tasks = [
                self._fetch_json(_HN_ITEM_URL.format(sid))
                for sid in top_ids
            ]
            stories = await asyncio.gather(*tasks, return_exceptions=True)

            now = time.time()
            for story in stories:
                if not story or isinstance(story, Exception):
                    continue
                title = story.get("title", "")
                score = story.get("score", 0)
                descendants = story.get("descendants", 0)  # comment count
                created = story.get("time", 0)

                if not title:
                    continue

                age_hours = (now - created) / 3600 if created else 24
                engagement = score + (descendants * 2)  # comments weighted higher

                keywords = _extract_keywords(title, top_n=3)
                for kw in keywords:
                    if kw in results:
                        results[kw]["hn_engagement"] = max(
                            results[kw]["hn_engagement"], engagement
                        )
                    else:
                        results[kw] = {
                            "hn_engagement": engagement,
                            "age_hours": age_hours,
                            "source": "hackernews",
                            "full_title": title,
                        }

        except Exception as exc:
            log.debug("Hacker News error: %s", exc)

        return results

    # ── Merge all sources into unified candidates ─────────────────────────────

    def _merge_sources(
        self,
        google_data: Dict[str, Any],
        reddit_data: Dict[str, Any],
        news_data: Dict[str, Any],
        hn_data: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """
        Merge all source dictionaries into a unified list of candidates,
        each with all signal fields populated (defaulting to 0 if missing).
        """
        all_topics: Dict[str, Dict[str, Any]] = {}

        def _register(topic: str, signals: Dict[str, Any]) -> None:
            topic_key = topic.lower().strip()
            if not topic_key or len(topic_key) < 3:
                return
            if topic_key not in all_topics:
                all_topics[topic_key] = {
                    "topic": topic,
                    "google_momentum": 0,
                    "reddit_score": 0,
                    "news_velocity": 0,
                    "hn_engagement": 0,
                    "age_hours": 24.0,
                    "sources": [],
                    "full_title": topic,
                }
            entry = all_topics[topic_key]
            # Update signals (take max of seen values)
            for field_name in ("google_momentum", "reddit_score", "news_velocity", "hn_engagement"):
                if field_name in signals:
                    entry[field_name] = max(entry[field_name], signals[field_name])
            if "age_hours" in signals:
                entry["age_hours"] = min(entry["age_hours"], signals["age_hours"])
            src = signals.get("source", "")
            if src and src not in entry["sources"]:
                entry["sources"].append(src)
            if "full_title" in signals and signals["full_title"] != topic:
                entry["full_title"] = signals["full_title"]

        for topic, data in google_data.items():
            _register(topic, data)
        for topic, data in reddit_data.items():
            _register(topic, data)
        for topic, data in news_data.items():
            _register(topic, data)
        for topic, data in hn_data.items():
            _register(topic, data)

        return list(all_topics.values())

    # ── Build TrendOpportunity from candidate ─────────────────────────────────

    def _build_opportunity(self, candidate: Dict[str, Any]) -> TrendOpportunity:
        topic = candidate.get("full_title") or candidate.get("topic", "Unknown Topic")
        topic_short = candidate.get("topic", topic)
        age_hours = candidate.get("age_hours", 24.0)

        score = _score_trend(topic, candidate, topic_age_hours=age_hours)
        category = _detect_category(topic)
        best_angle = _best_angle(topic_short)

        # Derive predicted CTR / RPM from category + score
        ctr_base = {"money": 5.5, "tech": 5.0, "health": 4.5,
                    "career": 4.8, "science": 4.0, "mindset": 3.8}
        rpm_base = {"money": 22.0, "tech": 18.0, "health": 14.0,
                    "career": 16.0, "science": 12.0, "mindset": 10.0}
        predicted_ctr = round(ctr_base.get(category, 4.0) * (0.7 + score * 0.6), 2)
        predicted_rpm = round(rpm_base.get(category, 12.0) * (0.8 + score * 0.4), 2)

        # Estimate competition saturation from news velocity
        news_vel = candidate.get("news_velocity", 0)
        competing = max(10, int(news_vel * 8 + candidate.get("google_momentum", 0) / 100))

        # Opportunity window — fresher = bigger window
        window_hours = max(4, int(48 - age_hours))

        # Emotional trigger selection
        if _is_controversial(topic):
            emotional_trigger = "outrage / curiosity"
        elif category == "money":
            emotional_trigger = "fear of missing out / financial anxiety"
        elif category == "health":
            emotional_trigger = "fear / desire for longevity"
        elif category == "tech":
            emotional_trigger = "awe / urgency"
        elif category == "career":
            emotional_trigger = "ambition / status anxiety"
        else:
            emotional_trigger = "curiosity / self-improvement desire"

        # Visual style
        visual_map = {
            "money":   "Dark background, gold accent text, financial charts, urgent countdown graphic",
            "tech":    "Blue/purple gradient, futuristic UI elements, code snippets, AI imagery",
            "health":  "Clean white + green, body diagrams, data callouts, doctor-aesthetic",
            "science": "Space/lab imagery, infographic overlays, animated statistics",
            "career":  "Professional office aesthetic, LinkedIn-style cards, salary number reveals",
            "mindset": "Minimalist, handwritten fonts, calm colour palette, motivational overlays",
        }
        visual_style = visual_map.get(category, "Bold text on dark background, data-driven overlays")

        # Key facts from topic + angle
        key_facts = [
            f"Topic is trending across {len(candidate.get('sources', []))} independent source(s)",
            f"Estimated topic age: {age_hours:.0f} hours",
            f"Opportunity window: ~{window_hours} hours remaining",
            f"Trend score: {score:.2f} / 1.00",
            best_angle["hook"],
        ]

        # Target audience
        audience_map = {
            "money":   "18–40 year-old Americans interested in personal finance",
            "tech":    "18–45 professionals and tech enthusiasts in the US",
            "health":  "25–55 health-conscious Americans",
            "science": "18–45 curious learners and science enthusiasts",
            "career":  "22–40 career-focused professionals in the US",
            "mindset": "18–40 self-improvement seekers",
        }
        target_audience = audience_map.get(category, "18–40 US general audience")

        return TrendOpportunity(
            topic=topic,
            angle=best_angle["title"],
            hook=best_angle["hook"],
            key_facts=key_facts,
            emotional_trigger=emotional_trigger,
            target_audience=target_audience,
            predicted_ctr=predicted_ctr,
            predicted_rpm=predicted_rpm,
            competing_videos_count=competing,
            opportunity_window_hours=window_hours,
            visual_style_recommendation=visual_style,
            sources=candidate.get("sources", []),
            score=score,
            category=category,
        )

    # ── Select best opportunity from list ─────────────────────────────────────

    def _select_best_opportunity(
        self, opportunities: List[TrendOpportunity]
    ) -> TrendOpportunity:
        """
        Return the highest-scoring opportunity. If the list is empty or all
        scores are zero, fall back to content_generator.build_daily_content().
        """
        valid = [o for o in opportunities if o.score > 0.01]
        if valid:
            return max(valid, key=lambda o: o.score)

        log.debug("No real trends found — using fallback content library")
        return self._fallback_opportunity()

    def _fallback_opportunity(self) -> TrendOpportunity:
        """Build a TrendOpportunity from the static content_generator library."""
        try:
            # Dynamically resolve path so this works regardless of cwd
            base_dir = Path(__file__).resolve().parents[2]  # youtube_automation/
            sys.path.insert(0, str(base_dir))
            from content_generator import build_daily_content  # type: ignore
            content = build_daily_content()

            category_raw = content.get("category", "mindset").lower()
            category = category_raw if category_raw in (
                "money", "tech", "health", "science", "career", "mindset"
            ) else "mindset"

            topic = content.get("topic", content.get("title", "Daily Self-Improvement"))
            angle = _best_angle(topic)

            return TrendOpportunity(
                topic=topic,
                angle=angle["title"],
                hook=content.get("hook", angle["hook"]),
                key_facts=content.get("bullets", [angle["hook"]])[:5],
                emotional_trigger="curiosity / self-improvement desire",
                target_audience="18–40 US general audience",
                predicted_ctr=4.2,
                predicted_rpm=12.0,
                competing_videos_count=500,
                opportunity_window_hours=24,
                visual_style_recommendation=(
                    "Bold text on dark background, animated statistics, energetic pacing"
                ),
                sources=["content_library_fallback"],
                score=0.0,
                category=category,
            )
        except Exception as exc:
            log.debug("Fallback content generation error: %s", exc)
            # Absolute last-resort hardcoded opportunity
            return TrendOpportunity(
                topic="5 Money Habits That Will Change Your Life",
                angle="The Truth About Money Habits Nobody Is Saying",
                hook="The financial habits they never taught you in school are making someone else rich.",
                key_facts=[
                    "The average American saves less than 5% of their income",
                    "Compound interest makes the rich richer — and it can work for you too",
                    "Automating savings removes willpower from the equation entirely",
                    "One financial habit changed in your 20s is worth $200k by retirement",
                    "The wealthiest Americans don't earn more — they spend differently",
                ],
                emotional_trigger="financial anxiety / fear of missing out",
                target_audience="18–40 year-old Americans interested in personal finance",
                predicted_ctr=5.2,
                predicted_rpm=20.0,
                competing_videos_count=800,
                opportunity_window_hours=24,
                visual_style_recommendation=(
                    "Dark background, gold accent text, financial charts, urgent countdown graphic"
                ),
                sources=["hardcoded_fallback"],
                score=0.0,
                category="money",
            )

    # ── Main scanner ──────────────────────────────────────────────────────────

    async def scan_all_trends(self) -> List[TrendOpportunity]:
        """
        Scan all four sources concurrently and return a ranked list of
        TrendOpportunity objects (highest score first).
        """
        log.debug("TrendAnalyzer: starting parallel source scan")

        google_task  = asyncio.create_task(self._source_google_trends())
        reddit_task  = asyncio.create_task(self._source_reddit())
        news_task    = asyncio.create_task(self._source_news_rss())
        hn_task      = asyncio.create_task(self._source_hacker_news())

        results = await asyncio.gather(
            google_task, reddit_task, news_task, hn_task,
            return_exceptions=True,
        )

        google_data = results[0] if isinstance(results[0], dict) else {}
        reddit_data = results[1] if isinstance(results[1], dict) else {}
        news_data   = results[2] if isinstance(results[2], dict) else {}
        hn_data     = results[3] if isinstance(results[3], dict) else {}

        log.debug(
            "Sources returned: google=%d reddit=%d news=%d hn=%d",
            len(google_data), len(reddit_data), len(news_data), len(hn_data),
        )

        candidates = self._merge_sources(google_data, reddit_data, news_data, hn_data)
        log.debug("Merged candidates: %d", len(candidates))

        opportunities: List[TrendOpportunity] = []
        for candidate in candidates:
            try:
                opp = self._build_opportunity(candidate)
                opportunities.append(opp)
            except Exception as exc:
                log.debug("Failed to build opportunity for %s: %s", candidate, exc)

        # Sort by score descending
        opportunities.sort(key=lambda o: o.score, reverse=True)
        log.debug("Opportunities ranked: %d", len(opportunities))

        await self._close()
        return opportunities


# ── Public API ────────────────────────────────────────────────────────────────

async def get_best_opportunity() -> TrendOpportunity:
    """
    Main async entry point. Scans all sources and returns the single best
    TrendOpportunity. Times out after 60 seconds and falls back to the
    static content library if needed.
    """
    analyzer = TrendAnalyzer()
    try:
        opportunities = await asyncio.wait_for(
            analyzer.scan_all_trends(),
            timeout=60,
        )
        return analyzer._select_best_opportunity(opportunities)
    except asyncio.TimeoutError:
        log.debug("get_best_opportunity: 60s timeout reached — using fallback")
        await analyzer._close()
        return analyzer._fallback_opportunity()
    except Exception as exc:
        log.debug("get_best_opportunity error: %s — using fallback", exc)
        await analyzer._close()
        return analyzer._fallback_opportunity()


def get_best_opportunity_sync() -> TrendOpportunity:
    """
    Synchronous wrapper for use from the daily pipeline (daily_runner.py).
    Safe to call from any context — creates a new event loop if needed.
    """
    try:
        # If there is already a running event loop (e.g. in Jupyter / async
        # host process), run in a thread executor to avoid nest_asyncio issues.
        loop = asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, get_best_opportunity())
            return future.result(timeout=70)
    except RuntimeError:
        # No running event loop — safe to call asyncio.run() directly
        return asyncio.run(get_best_opportunity())


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    print("Scanning trends...")
    opp = get_best_opportunity_sync()
    print("\n=== BEST OPPORTUNITY ===")
    print(f"Topic    : {opp.topic}")
    print(f"Category : {opp.category}")
    print(f"Score    : {opp.score:.3f}")
    print(f"Angle    : {opp.angle}")
    print(f"Hook     : {opp.hook}")
    print(f"CTR est  : {opp.predicted_ctr:.1f}%")
    print(f"RPM est  : ${opp.predicted_rpm:.2f}")
    print(f"Window   : {opp.opportunity_window_hours}h")
    print(f"Sources  : {', '.join(opp.sources) or 'fallback'}")
    print(f"Audience : {opp.target_audience}")
    print(f"Trigger  : {opp.emotional_trigger}")
    print(f"Visual   : {opp.visual_style_recommendation}")
    print("\nKey facts:")
    for f in opp.key_facts:
        print(f"  - {f}")
