"""
modules/intelligence/trend_analyzer.py
Layer: Intelligence — Trend Scanner
Safety: auto-heal-only

Production-grade 15-source trend intelligence engine for the GetMindFuelNow
YouTube automation system.

Architecture
------------
All 15 data sources run concurrently via asyncio.gather().  Each source is an
independent coroutine whose failure is fully isolated — one broken source
never stops the others.

Sources implemented
-------------------
 1. Google Trends      — pytrends realtime + daily trending
 2. Reddit             — PRAW authenticated OR public JSON fallback
 3. Hacker News        — Firebase REST API (top 30 stories)
 4. NewsAPI            — Top headlines (requires NEWSAPI_KEY)
 5. The Guardian       — Newest content (requires GUARDIAN_API_KEY)
 6. RSS Feeds          — BBC Tech, Reuters, NYT, TechCrunch, Our World in Data
 7. arXiv              — Recent AI / climate / health preprints
 8. Wikipedia          — Recent changes to article namespace
 9. YouTube Trending   — Most-popular videos per category (requires YOUTUBE_DATA_API_KEY)
10. NASA               — APOD + Near-Earth Objects feed
11. FRED               — Economic release calendar (requires FRED_API_KEY)
12. PubMed             — Latest biomedical literature via Entrez
13. GDELT              — Global news event stream (no key needed)
14. Product Hunt       — RSS feed of top launches
15. WHO                — World Health Organization news RSS

Public API
----------
    analyzer = TrendAnalyzer()
    opportunities: List[TrendOpportunity] = analyzer.scan_all_trends()   # sync
    best: TrendOpportunity = await analyzer.get_best_opportunity()        # async
    best: TrendOpportunity = analyzer.get_best_opportunity_sync()         # sync, loop-safe
"""

from __future__ import annotations

# ── Inline dependency bootstrap ───────────────────────────────────────────────
import subprocess
import sys

def _ensure(pkg: str, import_as: str | None = None) -> None:
    """Silently install *pkg* if it cannot be imported."""
    target = import_as or pkg
    try:
        __import__(target)
    except ImportError:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", pkg, "-q"],
            check=False,
        )

_ensure("aiohttp")
_ensure("feedparser")
_ensure("praw")
_ensure("pytrends")
_ensure("pyyaml", "yaml")

# ── Standard library ──────────────────────────────────────────────────────────
import asyncio
import concurrent.futures
import datetime
import json
import logging
import os
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ── Third-party ───────────────────────────────────────────────────────────────
import aiohttp
import feedparser
import yaml

# praw imported lazily inside _scan_reddit to keep import errors isolated
# pytrends imported lazily inside _scan_google_trends for the same reason

# ── Project interface ─────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_YOUTUBE_AUTOMATION_DIR = _HERE.parents[2]   # youtube_automation/
sys.path.insert(0, str(_YOUTUBE_AUTOMATION_DIR))

from interfaces.trend_interface import TrendOpportunity, VideoAngle  # noqa: E402

# ── Logging ───────────────────────────────────────────────────────────────────
log = logging.getLogger(__name__)

# ── Config loading ────────────────────────────────────────────────────────────

def _load_config() -> Dict[str, Any]:
    """Load master_config.yaml.  Returns empty dict on failure."""
    cfg_path = _YOUTUBE_AUTOMATION_DIR / "config" / "master_config.yaml"
    try:
        with cfg_path.open("r") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        log.warning("Could not load master_config.yaml: %s", exc)
        return {}


def _load_persona() -> Dict[str, Any]:
    """Load channel_persona.yaml.  Returns empty dict on failure."""
    cfg_path = _YOUTUBE_AUTOMATION_DIR / "config" / "channel_persona.yaml"
    try:
        with cfg_path.open("r") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:
        log.warning("Could not load channel_persona.yaml: %s", exc)
        return {}


_CFG: Dict[str, Any] = _load_config()
_PERSONA: Dict[str, Any] = _load_persona()
_TREND_CFG: Dict[str, Any] = _CFG.get("trend_intelligence", {})
_RPM_TIERS: Dict[str, Any] = _PERSONA.get("rpm_tiers", {})

# ── Runtime constants (all sourced from config) ───────────────────────────────
_HTTP_TIMEOUT: int = 12
_USER_AGENT: str = "Mozilla/5.0 (compatible; MindFuelBot/2.0; +https://getmindfuelnow.com)"
_MAX_TRENDS: int = int(_TREND_CFG.get("max_trends_to_score", 20))
_TOP_N: int = int(_TREND_CFG.get("top_n_to_return", 5))
_MIN_SCORE: float = float(_TREND_CFG.get("min_opportunity_score", 0.40))
_RECENCY_BONUS_HOURS: float = float(_TREND_CFG.get("recency_bonus_hours", 6))
_FIRST_MOVER_BONUS: float = float(_TREND_CFG.get("first_mover_bonus", 0.25))
_FINANCE_RPM_BONUS: float = float(_TREND_CFG.get("finance_rpm_bonus", 0.20))
_CONTROVERSIAL_BONUS: float = float(_TREND_CFG.get("controversial_bonus", 0.15))
_VISUAL_BONUS: float = float(_TREND_CFG.get("visual_potential_bonus", 0.10))
_OVERSATURATED_PENALTY: float = float(_TREND_CFG.get("oversaturated_penalty", -0.25))
_STALE_TOPIC_HOURS: float = float(_TREND_CFG.get("stale_topic_hours", 48))
_STALE_PENALTY: float = float(_TREND_CFG.get("stale_penalty", -0.15))

# YouTube category IDs that the Data API accepts
_YT_CATEGORY_IDS: List[str] = [
    "1", "2", "10", "15", "17", "19", "20",
    "22", "23", "24", "25", "26", "27", "28", "29",
]

# RSS feed URLs (source 6)
_RSS_FEEDS: List[Tuple[str, str]] = [
    ("bbc_tech",          "http://feeds.bbci.co.uk/news/technology/rss.xml"),
    ("reuters_top",       "https://feeds.reuters.com/reuters/topNews"),
    ("nyt_home",          "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
    ("techcrunch",        "https://techcrunch.com/feed/"),
    ("our_world_in_data", "https://ourworldindata.org/atom.xml"),
]

# ── Topic keyword sets (no hardcoded values — kept minimal for detection) ─────
_TIER1_KEYWORDS = {
    "finance", "investing", "stock", "crypto", "bitcoin", "economy", "budget",
    "salary", "wealth", "retire", "401k", "ira", "mortgage", "tax", "bank",
    "fund", "market", "insurance", "legal", "real estate", "business",
    "ai", "artificial intelligence", "machine learning", "chatgpt", "openai",
    "software", "cyber", "hack", "robot", "automation", "tech", "startup",
    "algorithm", "data", "llm", "gpt",
}
_TIER2_KEYWORDS = {
    "health", "cancer", "diabetes", "fitness", "diet", "mental",
    "drug", "vaccine", "virus", "medicine", "hospital", "fda", "sleep",
    "nutrition", "weight", "exercise", "science", "climate", "space",
    "nasa", "physics", "biology", "research", "study", "discover",
    "quantum", "gene", "dna", "education", "history", "psychology",
    "true crime", "criminal",
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
_CATEGORY_KEYWORD_MAP: Dict[str, List[str]] = {
    "Finance": [
        "money", "finance", "invest", "stock", "crypto", "bitcoin", "economy",
        "budget", "salary", "wealth", "retire", "401k", "ira", "mortgage",
        "tax", "bank", "fund", "market", "insurance", "real estate",
    ],
    "Technology": [
        "ai", "artificial intelligence", "machine learning", "chatgpt", "openai",
        "software", "app", "cyber", "hack", "robot", "automation", "tech",
        "startup", "algorithm", "data", "llm", "gpt", "semiconductor", "cloud",
    ],
    "Health": [
        "health", "cancer", "diabetes", "fitness", "diet", "mental", "drug",
        "vaccine", "virus", "medicine", "hospital", "fda", "sleep", "nutrition",
        "weight", "exercise", "longevity", "aging",
    ],
    "Science": [
        "science", "climate", "space", "nasa", "physics", "biology", "research",
        "study", "discover", "quantum", "gene", "dna", "astronomy", "geology",
    ],
    "Business": [
        "career", "job", "hiring", "layoff", "remote", "work", "promotion",
        "resume", "linkedin", "interview", "entrepreneur", "startup",
    ],
    "Psychology": [
        "mindset", "habit", "motivation", "discipline", "success", "psychology",
        "stoic", "productivity", "focus", "goal", "dopamine", "anxiety",
    ],
    "History": [
        "history", "war", "ancient", "empire", "civilization", "world war",
        "revolution", "historical",
    ],
}

# Visual style per RPM tier / category (colour-grade names match production pipeline)
_COLOR_GRADE_MAP: Dict[str, str] = {
    "Finance":    "gold_dark",
    "Technology": "tech_dark",
    "Health":     "clean_bright",
    "Science":    "space_epic",
    "Business":   "corporate_blue",
    "Psychology": "minimal_warm",
    "History":    "sepia_epic",
}

# ── Utility helpers ───────────────────────────────────────────────────────────

def _detect_category(text: str) -> str:
    """Return the best-fit content category for *text* using keyword overlap."""
    t = text.lower()
    best_cat = "Psychology"
    best_count = 0
    for cat, kws in _CATEGORY_KEYWORD_MAP.items():
        count = sum(1 for kw in kws if kw in t)
        if count > best_count:
            best_count = count
            best_cat = cat
    return best_cat


def _detect_rpm_tier(category: str) -> Tuple[str, float]:
    """
    Return (tier_label, rpm_midpoint) for *category* using channel_persona.yaml
    data.  Falls back to tier_3 with a conservative estimate if config is absent.
    """
    for tier_label, tier_data in _RPM_TIERS.items():
        cats: List[str] = tier_data.get("categories", [])
        if any(category.lower() == c.lower() for c in cats):
            rpm_range: str = tier_data.get("rpm_range", "$3-8")
            # Parse "$15-50" → midpoint 32.5
            try:
                stripped = rpm_range.replace("$", "").replace(",", "")
                low_s, high_s = stripped.split("-")
                midpoint = (float(low_s) + float(high_s)) / 2.0
            except Exception:
                midpoint = 5.0
            return tier_label, midpoint
    return "tier_3", 5.0


def _normalize(value: float, lo: float, hi: float) -> float:
    """Clamp and normalise *value* to [0, 1]."""
    if hi <= lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _extract_keywords(text: str, top_n: int = 5) -> List[str]:
    """Naive keyword extraction — split on whitespace, drop stop-words."""
    STOP = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "this", "that", "it", "its", "as", "so", "do",
        "up", "out", "how", "why", "what", "who", "when", "will", "can",
        "about", "into", "over", "after", "new", "just", "now", "says", "said",
        "get", "got", "also", "than", "more", "most", "not", "no", "us", "we",
    }
    words = [w.strip("\"'.,!?:;()[]{}") for w in text.lower().split()]
    freq: Dict[str, int] = {}
    for w in words:
        if len(w) > 3 and w not in STOP:
            freq[w] = freq.get(w, 0) + 1
    return [k for k, _ in sorted(freq.items(), key=lambda x: -x[1])][:top_n]


def _topic_age_hours(published_parsed: Any | None, fallback_hours: float = 24.0) -> float:
    """Convert a feedparser *published_parsed* struct to elapsed hours."""
    if not published_parsed:
        return fallback_hours
    try:
        pub_dt = datetime.datetime(*published_parsed[:6])
        delta = datetime.datetime.utcnow() - pub_dt
        return max(0.0, delta.total_seconds() / 3600.0)
    except Exception:
        return fallback_hours


# ── Scoring engine ────────────────────────────────────────────────────────────

def _compute_opportunity_score(
    youtube_velocity: float,
    google_breakout_rate: float,
    reddit_momentum: float,
    news_article_density: float,
    social_spread_rate: float,
    topic: str,
    age_hours: float,
    sources_hit: int,
) -> float:
    """
    Core scoring formula from spec:
        score = youtube_velocity      * 0.30
              + google_breakout_rate  * 0.25
              + reddit_momentum       * 0.20
              + news_article_density  * 0.15
              + social_spread_rate    * 0.10

    Bonus multipliers are then applied from master_config.yaml values.
    All inputs are expected to be pre-normalised to [0, 1].
    """
    base = (
        youtube_velocity       * 0.30
        + google_breakout_rate * 0.25
        + reddit_momentum      * 0.20
        + news_article_density * 0.15
        + social_spread_rate   * 0.10
    )

    multiplier = 1.0
    t = topic.lower()

    # First-mover bonus: topic is very fresh
    if age_hours < _RECENCY_BONUS_HOURS:
        multiplier += _FIRST_MOVER_BONUS

    # Finance / high-RPM category bonus
    if any(kw in t for kw in _TIER1_KEYWORDS):
        multiplier += _FINANCE_RPM_BONUS

    # Controversy engagement bonus
    if any(kw in t for kw in _CONTROVERSIAL_KEYWORDS):
        multiplier += _CONTROVERSIAL_BONUS

    # Visual potential bonus: topics with strong imagery
    if any(kw in t for kw in {"space", "nasa", "video", "photo", "image", "visual"}):
        multiplier += _VISUAL_BONUS

    # Advertiser-unsafe / political penalty
    if any(kw in t for kw in _POLITICAL_KEYWORDS):
        multiplier -= 0.20

    # Stale content penalty
    if age_hours > _STALE_TOPIC_HOURS:
        multiplier += _STALE_PENALTY   # value is negative in config

    # Oversaturation penalty: covered by many sources but age is high
    if sources_hit >= 8 and age_hours > 12:
        multiplier += _OVERSATURATED_PENALTY  # value is negative in config

    multiplier = max(0.0, multiplier)
    return min(1.0, base * multiplier)


# ── Video angle generator ─────────────────────────────────────────────────────

def _build_five_angles(topic: str, rpm_tier: str, category: str) -> List[VideoAngle]:
    """
    Generate all five VideoAngle objects for *topic*.
    Scores are derived from angle archetype characteristics.
    """
    t = topic.strip().title()
    cat = _detect_category(topic)

    life_area_map = {
        "Finance":    "Finances",
        "Technology": "Future",
        "Health":     "Health",
        "Science":    "Life",
        "Business":   "Career",
        "Psychology": "Mindset",
        "History":    "Worldview",
    }
    life_area = life_area_map.get(cat, "Life")
    year = datetime.datetime.utcnow().year + 1

    # Base visual potential — data/tech topics photograph better
    vis_base = 0.80 if cat in ("Technology", "Science", "Finance") else 0.65

    angles: List[VideoAngle] = [
        VideoAngle(
            angle_type="shocking_truth",
            title=f"The Truth About {t} Nobody Is Saying",
            hook=f"Everyone is talking about {t}, but nobody is saying this...",
            search_volume_score=0.80,
            competition_gap_score=0.65,
            emotional_trigger_score=0.90,
            monetization_tier=rpm_tier,
            visual_potential_score=vis_base,
            composite_score=0.0,
        ),
        VideoAngle(
            angle_type="data_explainer",
            title=f"The Numbers Behind {t} Are Insane",
            hook=f"The data on {t} will change how you see everything.",
            search_volume_score=0.70,
            competition_gap_score=0.72,
            emotional_trigger_score=0.75,
            monetization_tier=rpm_tier,
            visual_potential_score=min(1.0, vis_base + 0.10),
            composite_score=0.0,
        ),
        VideoAngle(
            angle_type="future_prediction",
            title=f"What {t} Means For Your {life_area} In {year}",
            hook=f"If {t} keeps trending, here is exactly what changes for you.",
            search_volume_score=0.75,
            competition_gap_score=0.60,
            emotional_trigger_score=0.80,
            monetization_tier=rpm_tier,
            visual_potential_score=vis_base,
            composite_score=0.0,
        ),
        VideoAngle(
            angle_type="simple_explanation",
            title=f"Why {t} Is Happening (The Simple Truth)",
            hook=f"Here is the clearest explanation of {t} you will ever find.",
            search_volume_score=0.85,
            competition_gap_score=0.55,
            emotional_trigger_score=0.65,
            monetization_tier=rpm_tier,
            visual_potential_score=vis_base,
            composite_score=0.0,
        ),
        VideoAngle(
            angle_type="contrarian",
            title=f"{t}: Everyone Is Getting This Completely Wrong",
            hook=f"The mainstream take on {t} is completely backwards — and here is why.",
            search_volume_score=0.72,
            competition_gap_score=0.68,
            emotional_trigger_score=0.88,
            monetization_tier=rpm_tier,
            visual_potential_score=vis_base,
            composite_score=0.0,
        ),
    ]

    # Compute composite score for each angle
    for angle in angles:
        angle.composite_score = round(
            angle.search_volume_score     * 0.35
            + angle.competition_gap_score   * 0.25
            + angle.emotional_trigger_score * 0.25
            + angle.visual_potential_score  * 0.15,
            4,
        )

    return angles


def _select_best_angle(angles: List[VideoAngle]) -> VideoAngle:
    return max(angles, key=lambda a: a.composite_score)


# ── Candidate data structure ──────────────────────────────────────────────────
# Raw signals accumulate in a plain dict before being assembled into
# TrendOpportunity objects.  Field semantics:
#
#   youtube_velocity    — 0-1 normalised view velocity signal
#   google_breakout     — 0-1 normalised Google Trends breakout signal
#   reddit_momentum     — 0-1 normalised Reddit engagement signal
#   news_density        — 0-1 normalised article count signal
#   social_spread       — 0-1 catch-all social/other signal
#   age_hours           — minimum observed topic age in hours
#   sources             — list of source names that surfaced this topic
#   full_title          — best human-readable title seen

def _empty_candidate(topic: str, title: str | None = None) -> Dict[str, Any]:
    return {
        "topic": topic,
        "full_title": title or topic,
        "youtube_velocity":  0.0,
        "google_breakout":   0.0,
        "reddit_momentum":   0.0,
        "news_density":      0.0,
        "social_spread":     0.0,
        "age_hours":         24.0,
        "sources":           [],
    }


# ── TrendAnalyzer ─────────────────────────────────────────────────────────────

class TrendAnalyzer:
    """
    Async 15-source trend intelligence engine.

    Usage (async context)::

        best = await TrendAnalyzer().get_best_opportunity()

    Usage (sync context)::

        best = TrendAnalyzer().get_best_opportunity_sync()
        all_opps = TrendAnalyzer().scan_all_trends()
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    # ── HTTP helpers ──────────────────────────────────────────────────────────

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=False, limit=40)
            timeout = aiohttp.ClientTimeout(total=_HTTP_TIMEOUT)
            self._session = aiohttp.ClientSession(
                connector=connector,
                headers={"User-Agent": _USER_AGENT},
                timeout=timeout,
            )
        return self._session

    async def _close_session(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _get_json(self, url: str, **kwargs: Any) -> Optional[Any]:
        """GET *url* and decode JSON.  Returns None on any error."""
        try:
            session = await self._get_session()
            async with session.get(url, **kwargs) as resp:
                if resp.status == 200:
                    return await resp.json(content_type=None)
                log.info("_get_json %s → HTTP %d", url, resp.status)
        except asyncio.TimeoutError:
            log.info("_get_json timeout: %s", url)
        except Exception as exc:
            log.info("_get_json error %s: %s", url, exc)
        return None

    async def _get_text(self, url: str) -> Optional[str]:
        """GET *url* and return raw text.  Returns None on any error."""
        try:
            session = await self._get_session()
            async with session.get(url) as resp:
                if resp.status == 200:
                    return await resp.text(errors="replace")
                log.info("_get_text %s → HTTP %d", url, resp.status)
        except asyncio.TimeoutError:
            log.info("_get_text timeout: %s", url)
        except Exception as exc:
            log.info("_get_text error %s: %s", url, exc)
        return None

    # ── Source 1: Google Trends ───────────────────────────────────────────────

    async def _scan_google_trends(self) -> Dict[str, Dict[str, Any]]:
        """
        pytrends — realtime_trending_searches(pn='US') + trending_searches.
        Runs in an executor because pytrends is synchronous.
        """
        def _sync() -> Dict[str, Dict[str, Any]]:
            results: Dict[str, Dict[str, Any]] = {}
            try:
                from pytrends.request import TrendReq
                pt = TrendReq(hl="en-US", tz=360)

                # Realtime trending (last few hours)
                try:
                    rt = pt.realtime_trending_searches(pn="US")
                    if rt is not None and not rt.empty:
                        for col in ("title", "entityNames", "query"):
                            if col in rt.columns:
                                for raw in rt[col].tolist()[:25]:
                                    topic = str(raw).strip()[:120]
                                    if topic and topic not in results:
                                        results[topic] = {
                                            "google_breakout": 0.85,
                                            "age_hours": 3.0,
                                            "source": "google_realtime",
                                            "full_title": topic,
                                        }
                                break
                except Exception as exc:
                    log.info("Google Trends realtime error: %s", exc)

                # Daily trending (past 24 hours)
                try:
                    df = pt.trending_searches(pn="united_states")
                    for topic in df[0].tolist()[:20]:
                        topic = str(topic).strip()
                        if topic and topic not in results:
                            results[topic] = {
                                "google_breakout": 0.60,
                                "age_hours": 12.0,
                                "source": "google_daily",
                                "full_title": topic,
                            }
                except Exception as exc:
                    log.info("Google Trends daily error: %s", exc)

            except Exception as exc:
                log.info("Google Trends outer error: %s", exc)
            return results

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _sync),
                timeout=30.0,
            )
        except Exception as exc:
            log.info("_scan_google_trends failed: %s", exc)
            return {}

    # ── Source 2: Reddit ──────────────────────────────────────────────────────

    async def _scan_reddit(self) -> Dict[str, Dict[str, Any]]:
        """
        PRAW if credentials present, else public JSON fallback.
        Endpoints: r/all/hot + r/technology+science+worldnews+finance/hot
        """
        results: Dict[str, Dict[str, Any]] = {}

        async def _fetch_public(url: str) -> None:
            data = await self._get_json(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            )
            if not data:
                return
            children = data.get("data", {}).get("children", [])
            for post in children:
                pd = post.get("data", {})
                score: int = pd.get("score", 0)
                ratio: float = pd.get("upvote_ratio", 0.0)
                title: str = pd.get("title", "")
                created: float = pd.get("created_utc", 0)
                num_comments: int = pd.get("num_comments", 0)
                if ratio < 0.80 or score < 3000 or not title:
                    continue
                age = (time.time() - created) / 3600.0 if created else 24.0
                composite = score * ratio + num_comments * 2
                norm = _normalize(composite, 0, 150_000)
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    key = kw
                    if key not in results:
                        results[key] = {
                            "reddit_momentum": norm,
                            "age_hours": age,
                            "source": "reddit",
                            "full_title": title,
                        }
                    else:
                        results[key]["reddit_momentum"] = max(
                            results[key]["reddit_momentum"], norm
                        )
                        results[key]["age_hours"] = min(results[key]["age_hours"], age)

        # Try PRAW first
        client_id = os.getenv("REDDIT_CLIENT_ID", "")
        client_secret = os.getenv("REDDIT_SECRET", "")
        if client_id and client_secret:
            def _praw_fetch() -> Dict[str, Dict[str, Any]]:
                praw_results: Dict[str, Dict[str, Any]] = {}
                try:
                    import praw
                    reddit = praw.Reddit(
                        client_id=client_id,
                        client_secret=client_secret,
                        user_agent=_USER_AGENT,
                    )
                    for sub_name in ("all", "technology+science+worldnews+finance"):
                        sub = reddit.subreddit(sub_name)
                        for post in sub.hot(limit=50):
                            if post.upvote_ratio < 0.80 or post.score < 3000:
                                continue
                            age = (time.time() - post.created_utc) / 3600.0
                            composite = post.score * post.upvote_ratio
                            norm = _normalize(composite, 0, 150_000)
                            kws = _extract_keywords(post.title, top_n=3)
                            for kw in kws:
                                if kw not in praw_results:
                                    praw_results[kw] = {
                                        "reddit_momentum": norm,
                                        "age_hours": age,
                                        "source": "reddit_praw",
                                        "full_title": post.title,
                                    }
                                else:
                                    praw_results[kw]["reddit_momentum"] = max(
                                        praw_results[kw]["reddit_momentum"], norm
                                    )
                except Exception as exc:
                    log.info("PRAW error: %s", exc)
                return praw_results

            loop = asyncio.get_event_loop()
            try:
                results = await asyncio.wait_for(
                    loop.run_in_executor(None, _praw_fetch),
                    timeout=25.0,
                )
                if results:
                    return results
            except Exception as exc:
                log.info("PRAW executor failed: %s — falling back to public JSON", exc)

        # Public JSON fallback
        try:
            await asyncio.gather(
                _fetch_public("https://www.reddit.com/r/all/hot.json?limit=50"),
                _fetch_public(
                    "https://www.reddit.com/r/technology+science+worldnews+finance/hot.json?limit=50"
                ),
                return_exceptions=True,
            )
        except Exception as exc:
            log.info("Reddit public JSON error: %s", exc)

        return results

    # ── Source 3: Hacker News ─────────────────────────────────────────────────

    async def _scan_hackernews(self) -> Dict[str, Dict[str, Any]]:
        """Firebase API: top 30 HN stories with engagement score."""
        results: Dict[str, Dict[str, Any]] = {}
        try:
            story_ids = await self._get_json(
                "https://hacker-news.firebaseio.com/v0/topstories.json"
            )
            if not isinstance(story_ids, list):
                return results
            tasks = [
                self._get_json(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
                for sid in story_ids[:30]
            ]
            stories = await asyncio.gather(*tasks, return_exceptions=True)
            now = time.time()
            for story in stories:
                if not story or isinstance(story, Exception):
                    continue
                title: str = story.get("title", "")
                score: int = story.get("score", 0)
                comments: int = story.get("descendants", 0)
                created: float = story.get("time", 0)
                if not title:
                    continue
                age = (now - created) / 3600.0 if created else 24.0
                engagement = score + comments * 2
                norm = _normalize(engagement, 0, 6000)
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "social_spread": norm,
                            "age_hours": age,
                            "source": "hackernews",
                            "full_title": title,
                        }
                    else:
                        results[kw]["social_spread"] = max(results[kw]["social_spread"], norm)
                        results[kw]["age_hours"] = min(results[kw]["age_hours"], age)
        except Exception as exc:
            log.info("_scan_hackernews error: %s", exc)
        return results

    # ── Source 4: NewsAPI ─────────────────────────────────────────────────────

    async def _scan_newsapi(self) -> Dict[str, Dict[str, Any]]:
        """Top US headlines via NewsAPI.  Skipped if NEWSAPI_KEY is unset."""
        key = os.getenv("NEWSAPI_KEY", "")
        if not key:
            log.info("_scan_newsapi: NEWSAPI_KEY not set — skipping")
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        try:
            url = (
                f"https://newsapi.org/v2/top-headlines"
                f"?country=us&pageSize=30&apiKey={key}"
            )
            data = await self._get_json(url)
            if not data:
                return results
            articles: List[Dict] = data.get("articles", [])
            now = datetime.datetime.utcnow()
            for art in articles:
                title: str = art.get("title", "") or ""
                published: str = art.get("publishedAt", "") or ""
                if not title:
                    continue
                age = 24.0
                try:
                    pub_dt = datetime.datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    age = max(0.0, (now - pub_dt).total_seconds() / 3600.0)
                except Exception:
                    pass
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "news_density": 0.50,
                            "age_hours": age,
                            "source": "newsapi",
                            "full_title": title,
                        }
                    else:
                        results[kw]["news_density"] = min(
                            1.0, results[kw]["news_density"] + 0.10
                        )
                        results[kw]["age_hours"] = min(results[kw]["age_hours"], age)
        except Exception as exc:
            log.info("_scan_newsapi error: %s", exc)
        return results

    # ── Source 5: The Guardian ────────────────────────────────────────────────

    async def _scan_guardian(self) -> Dict[str, Dict[str, Any]]:
        """The Guardian newest content.  Skipped if GUARDIAN_API_KEY is unset."""
        key = os.getenv("GUARDIAN_API_KEY", "")
        if not key:
            log.info("_scan_guardian: GUARDIAN_API_KEY not set — skipping")
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        try:
            url = (
                f"https://content.guardianapis.com/search"
                f"?order-by=newest&page-size=20&api-key={key}"
            )
            data = await self._get_json(url)
            if not data:
                return results
            items = data.get("response", {}).get("results", [])
            now = datetime.datetime.utcnow()
            for item in items:
                title: str = item.get("webTitle", "") or ""
                pub: str = item.get("webPublicationDate", "") or ""
                if not title:
                    continue
                age = 24.0
                try:
                    pub_dt = datetime.datetime.fromisoformat(
                        pub.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    age = max(0.0, (now - pub_dt).total_seconds() / 3600.0)
                except Exception:
                    pass
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "news_density": 0.50,
                            "age_hours": age,
                            "source": "guardian",
                            "full_title": title,
                        }
                    else:
                        results[kw]["news_density"] = min(
                            1.0, results[kw]["news_density"] + 0.08
                        )
                        results[kw]["age_hours"] = min(results[kw]["age_hours"], age)
        except Exception as exc:
            log.info("_scan_guardian error: %s", exc)
        return results

    # ── Source 6: RSS Feeds ───────────────────────────────────────────────────

    async def _scan_rss_feeds(self) -> Dict[str, Dict[str, Any]]:
        """
        feedparser on BBC Tech, Reuters, NYT, TechCrunch, Our World in Data.
        Runs in executor (feedparser is synchronous).
        """
        def _parse() -> Dict[str, Dict[str, Any]]:
            pool: Dict[str, Dict[str, Any]] = {}
            for feed_name, feed_url in _RSS_FEEDS:
                try:
                    feed = feedparser.parse(feed_url)
                    for entry in feed.entries:
                        title: str = getattr(entry, "title", "") or ""
                        if not title:
                            continue
                        age = _topic_age_hours(
                            getattr(entry, "published_parsed", None),
                            fallback_hours=12.0,
                        )
                        if age > 36:
                            continue
                        kws = _extract_keywords(title, top_n=3)
                        for kw in kws:
                            if kw not in pool:
                                pool[kw] = {
                                    "news_density": 0.40,
                                    "age_hours": age,
                                    "source": f"rss_{feed_name}",
                                    "full_title": title,
                                }
                            else:
                                pool[kw]["news_density"] = min(
                                    1.0, pool[kw]["news_density"] + 0.08
                                )
                                pool[kw]["age_hours"] = min(pool[kw]["age_hours"], age)
                except Exception as exc:
                    log.info("RSS %s error: %s", feed_url, exc)
            return pool

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _parse),
                timeout=25.0,
            )
        except Exception as exc:
            log.info("_scan_rss_feeds error: %s", exc)
            return {}

    # ── Source 7: arXiv ───────────────────────────────────────────────────────

    async def _scan_arxiv(self) -> Dict[str, Dict[str, Any]]:
        """Recent AI / climate / health preprints from arXiv Atom feed."""
        results: Dict[str, Dict[str, Any]] = {}
        url = (
            "https://export.arxiv.org/api/query"
            "?search_query=all:AI+OR+all:climate+OR+all:health"
            "&sortBy=submittedDate&sortOrder=descending&max_results=20"
        )
        text = await self._get_text(url)
        if not text:
            return results
        try:
            root = ET.fromstring(text)
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            now = datetime.datetime.utcnow()
            for entry in root.findall("atom:entry", ns):
                title_el = entry.find("atom:title", ns)
                published_el = entry.find("atom:published", ns)
                if title_el is None:
                    continue
                title = (title_el.text or "").strip().replace("\n", " ")
                age = 24.0
                if published_el is not None and published_el.text:
                    try:
                        pub_dt = datetime.datetime.fromisoformat(
                            published_el.text.replace("Z", "+00:00")
                        ).replace(tzinfo=None)
                        age = max(0.0, (now - pub_dt).total_seconds() / 3600.0)
                    except Exception:
                        pass
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "social_spread": 0.45,
                            "age_hours": age,
                            "source": "arxiv",
                            "full_title": title,
                        }
                    else:
                        results[kw]["social_spread"] = min(
                            1.0, results[kw]["social_spread"] + 0.05
                        )
                        results[kw]["age_hours"] = min(results[kw]["age_hours"], age)
        except Exception as exc:
            log.info("_scan_arxiv parse error: %s", exc)
        return results

    # ── Source 8: Wikipedia Recent Changes ───────────────────────────────────

    async def _scan_wikipedia_current_events(self) -> Dict[str, Dict[str, Any]]:
        """Wikipedia recentchanges API — article namespace edits."""
        results: Dict[str, Dict[str, Any]] = {}
        url = (
            "https://en.wikipedia.org/w/api.php"
            "?action=query&list=recentchanges&rcnamespace=0"
            "&rclimit=50&rcprop=title|timestamp&format=json"
        )
        data = await self._get_json(url)
        if not data:
            return results
        try:
            changes = data.get("query", {}).get("recentchanges", [])
            title_counts: Dict[str, int] = {}
            title_ages: Dict[str, float] = {}
            now = datetime.datetime.utcnow()
            for change in changes:
                title: str = change.get("title", "")
                ts_str: str = change.get("timestamp", "")
                if not title:
                    continue
                age = 24.0
                try:
                    ts = datetime.datetime.fromisoformat(
                        ts_str.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    age = max(0.0, (now - ts).total_seconds() / 3600.0)
                except Exception:
                    pass
                title_counts[title] = title_counts.get(title, 0) + 1
                title_ages[title] = min(title_ages.get(title, 999.0), age)

            for title, count in title_counts.items():
                if count < 2:
                    continue
                norm = _normalize(count, 2, 15)
                kws = _extract_keywords(title, top_n=2)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "social_spread": norm,
                            "age_hours": title_ages[title],
                            "source": "wikipedia",
                            "full_title": title,
                        }
                    else:
                        results[kw]["social_spread"] = max(results[kw]["social_spread"], norm)
        except Exception as exc:
            log.info("_scan_wikipedia_current_events parse error: %s", exc)
        return results

    # ── Source 9: YouTube Trending ────────────────────────────────────────────

    async def _scan_youtube_trending(self) -> Dict[str, Dict[str, Any]]:
        """
        YouTube Data API v3 videos.list for 15 category IDs.
        Skipped entirely if YOUTUBE_DATA_API_KEY is unset.
        """
        key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not key:
            log.info("_scan_youtube_trending: YOUTUBE_DATA_API_KEY not set — skipping")
            return {}
        results: Dict[str, Dict[str, Any]] = {}

        async def _fetch_category(cat_id: str) -> None:
            url = (
                f"https://www.googleapis.com/youtube/v3/videos"
                f"?part=snippet,statistics&chart=mostPopular"
                f"&regionCode=US&videoCategoryId={cat_id}"
                f"&maxResults=10&key={key}"
            )
            data = await self._get_json(url)
            if not data:
                return
            items = data.get("items", [])
            now = datetime.datetime.utcnow()
            for item in items:
                snippet = item.get("snippet", {})
                stats = item.get("statistics", {})
                title: str = snippet.get("title", "")
                published: str = snippet.get("publishedAt", "")
                view_count: int = int(stats.get("viewCount", 0) or 0)
                if not title:
                    continue
                age = 24.0
                try:
                    pub_dt = datetime.datetime.fromisoformat(
                        published.replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                    age = max(0.0, (now - pub_dt).total_seconds() / 3600.0)
                except Exception:
                    pass
                velocity = _normalize(view_count, 0, 10_000_000)
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "youtube_velocity": velocity,
                            "age_hours": age,
                            "source": "youtube_trending",
                            "full_title": title,
                        }
                    else:
                        results[kw]["youtube_velocity"] = max(
                            results[kw]["youtube_velocity"], velocity
                        )
                        results[kw]["age_hours"] = min(results[kw]["age_hours"], age)

        tasks = [_fetch_category(cid) for cid in _YT_CATEGORY_IDS]
        await asyncio.gather(*tasks, return_exceptions=True)
        return results

    # ── Source 10: NASA ───────────────────────────────────────────────────────

    async def _scan_nasa(self) -> Dict[str, Dict[str, Any]]:
        """NASA APOD + NEO feed."""
        nasa_key = os.getenv("NASA_API_KEY", "DEMO_KEY")
        results: Dict[str, Dict[str, Any]] = {}
        try:
            apod_data, neo_data = await asyncio.gather(
                self._get_json(f"https://api.nasa.gov/planetary/apod?api_key={nasa_key}"),
                self._get_json(
                    f"https://api.nasa.gov/neo/rest/v1/feed?api_key={nasa_key}"
                ),
                return_exceptions=True,
            )

            # APOD
            if isinstance(apod_data, dict):
                title: str = apod_data.get("title", "") or ""
                date_str: str = apod_data.get("date", "") or ""
                if title:
                    age = 24.0
                    try:
                        pub_dt = datetime.datetime.strptime(date_str, "%Y-%m-%d")
                        delta = datetime.datetime.utcnow() - pub_dt
                        age = delta.total_seconds() / 3600.0
                    except Exception:
                        pass
                    kws = _extract_keywords(title, top_n=3)
                    for kw in kws:
                        results[kw] = {
                            "social_spread": 0.55,
                            "age_hours": age,
                            "source": "nasa_apod",
                            "full_title": title,
                        }

            # NEO — count near-Earth objects today as a signal
            if isinstance(neo_data, dict):
                near_earth_objects = neo_data.get("near_earth_objects", {})
                today = datetime.date.today().isoformat()
                today_neos: List[Any] = near_earth_objects.get(today, [])
                if today_neos:
                    hazardous = [n for n in today_neos if n.get("is_potentially_hazardous_asteroid")]
                    neo_title = (
                        f"{len(hazardous)} Potentially Hazardous Asteroids Passing Earth Today"
                        if hazardous
                        else f"{len(today_neos)} Near-Earth Asteroids Tracked Today by NASA"
                    )
                    kws = _extract_keywords(neo_title, top_n=3)
                    for kw in kws:
                        if kw not in results:
                            results[kw] = {
                                "social_spread": 0.60 if hazardous else 0.40,
                                "age_hours": 2.0,
                                "source": "nasa_neo",
                                "full_title": neo_title,
                            }
        except Exception as exc:
            log.info("_scan_nasa error: %s", exc)
        return results

    # ── Source 11: FRED ───────────────────────────────────────────────────────

    async def _scan_fred(self) -> Dict[str, Dict[str, Any]]:
        """FRED economic release dates.  Skipped if FRED_API_KEY is unset."""
        key = os.getenv("FRED_API_KEY", "")
        if not key:
            log.info("_scan_fred: FRED_API_KEY not set — skipping")
            return {}
        results: Dict[str, Dict[str, Any]] = {}
        try:
            url = (
                f"https://api.stlouisfed.org/fred/releases/dates"
                f"?api_key={key}&file_type=json&limit=10"
            )
            data = await self._get_json(url)
            if not data:
                return results
            release_dates = data.get("release_dates", [])
            today = datetime.date.today()
            for rd in release_dates:
                release_date: str = rd.get("date", "")
                release_name: str = rd.get("release_name", "") or ""
                if not release_name or not release_date:
                    continue
                try:
                    rd_date = datetime.date.fromisoformat(release_date)
                    days_diff = abs((rd_date - today).days)
                    if days_diff > 1:
                        continue
                    age = days_diff * 24.0
                except Exception:
                    age = 24.0
                norm = 0.70 if release_date == today.isoformat() else 0.50
                kws = _extract_keywords(release_name, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "news_density": norm,
                            "age_hours": age,
                            "source": "fred",
                            "full_title": f"FRED: {release_name}",
                        }
        except Exception as exc:
            log.info("_scan_fred error: %s", exc)
        return results

    # ── Source 12: PubMed ─────────────────────────────────────────────────────

    async def _scan_pubmed(self) -> Dict[str, Dict[str, Any]]:
        """Latest biomedical literature via NCBI Entrez."""
        results: Dict[str, Dict[str, Any]] = {}
        try:
            search_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
                "?db=pubmed&term=trending&sort=date&retmax=20&retmode=json"
            )
            data = await self._get_json(search_url)
            if not data:
                return results
            ids: List[str] = data.get("esearchresult", {}).get("idlist", [])
            if not ids:
                return results
            summary_url = (
                "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
                f"?db=pubmed&id={','.join(ids[:10])}&retmode=json"
            )
            summary_data = await self._get_json(summary_url)
            if not summary_data:
                return results
            docsum = summary_data.get("result", {})
            now = datetime.datetime.utcnow()
            for pmid, doc in docsum.items():
                if pmid == "uids":
                    continue
                title: str = doc.get("title", "") or ""
                pub_date: str = doc.get("pubdate", "") or ""
                if not title:
                    continue
                age = 24.0
                try:
                    pub_dt = datetime.datetime.strptime(pub_date[:11].strip(), "%Y %b %d")
                    age = max(0.0, (now - pub_dt).total_seconds() / 3600.0)
                except Exception:
                    pass
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "social_spread": 0.35,
                            "age_hours": age,
                            "source": "pubmed",
                            "full_title": title,
                        }
                    else:
                        results[kw]["social_spread"] = min(
                            1.0, results[kw]["social_spread"] + 0.05
                        )
        except Exception as exc:
            log.info("_scan_pubmed error: %s", exc)
        return results

    # ── Source 13: GDELT ──────────────────────────────────────────────────────

    async def _scan_gdelt(self) -> Dict[str, Dict[str, Any]]:
        """GDELT 2.0 document API — top 25 global news events (no key needed)."""
        results: Dict[str, Dict[str, Any]] = {}
        try:
            url = (
                "https://api.gdeltproject.org/api/v2/doc/doc"
                "?query=&mode=artlist&maxrecords=25&format=json"
            )
            data = await self._get_json(url)
            if not data:
                return results
            articles = data.get("articles", [])
            for art in articles:
                title: str = art.get("title", "") or ""
                if not title:
                    continue
                kws = _extract_keywords(title, top_n=3)
                for kw in kws:
                    if kw not in results:
                        results[kw] = {
                            "news_density": 0.45,
                            "age_hours": 6.0,
                            "source": "gdelt",
                            "full_title": title,
                        }
                    else:
                        results[kw]["news_density"] = min(
                            1.0, results[kw]["news_density"] + 0.07
                        )
        except Exception as exc:
            log.info("_scan_gdelt error: %s", exc)
        return results

    # ── Source 14: Product Hunt ───────────────────────────────────────────────

    async def _scan_product_hunt(self) -> Dict[str, Dict[str, Any]]:
        """Product Hunt RSS feed via feedparser (executor)."""
        def _parse() -> Dict[str, Dict[str, Any]]:
            pool: Dict[str, Dict[str, Any]] = {}
            try:
                feed = feedparser.parse("https://www.producthunt.com/feed")
                for entry in feed.entries:
                    title: str = getattr(entry, "title", "") or ""
                    if not title:
                        continue
                    age = _topic_age_hours(
                        getattr(entry, "published_parsed", None), fallback_hours=12.0
                    )
                    kws = _extract_keywords(title, top_n=3)
                    for kw in kws:
                        if kw not in pool:
                            pool[kw] = {
                                "social_spread": 0.40,
                                "age_hours": age,
                                "source": "product_hunt",
                                "full_title": title,
                            }
                        else:
                            pool[kw]["social_spread"] = min(
                                1.0, pool[kw]["social_spread"] + 0.06
                            )
                            pool[kw]["age_hours"] = min(pool[kw]["age_hours"], age)
            except Exception as exc:
                log.info("Product Hunt RSS error: %s", exc)
            return pool

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _parse),
                timeout=20.0,
            )
        except Exception as exc:
            log.info("_scan_product_hunt error: %s", exc)
            return {}

    # ── Source 15: WHO RSS ────────────────────────────────────────────────────

    async def _scan_who_rss(self) -> Dict[str, Dict[str, Any]]:
        """WHO news RSS feed via feedparser (executor)."""
        def _parse() -> Dict[str, Dict[str, Any]]:
            pool: Dict[str, Dict[str, Any]] = {}
            try:
                feed = feedparser.parse("https://www.who.int/rss-feeds/news-english.xml")
                for entry in feed.entries:
                    title: str = getattr(entry, "title", "") or ""
                    if not title:
                        continue
                    age = _topic_age_hours(
                        getattr(entry, "published_parsed", None), fallback_hours=24.0
                    )
                    kws = _extract_keywords(title, top_n=3)
                    for kw in kws:
                        if kw not in pool:
                            pool[kw] = {
                                "news_density": 0.55,
                                "age_hours": age,
                                "source": "who_rss",
                                "full_title": title,
                            }
                        else:
                            pool[kw]["news_density"] = min(
                                1.0, pool[kw]["news_density"] + 0.08
                            )
                            pool[kw]["age_hours"] = min(pool[kw]["age_hours"], age)
            except Exception as exc:
                log.info("WHO RSS error: %s", exc)
            return pool

        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(None, _parse),
                timeout=20.0,
            )
        except Exception as exc:
            log.info("_scan_who_rss error: %s", exc)
            return {}

    # ── Source aggregation ────────────────────────────────────────────────────

    def _merge_all_sources(
        self, source_results: List[Dict[str, Dict[str, Any]]]
    ) -> List[Dict[str, Any]]:
        """
        Merge all per-source dicts into a unified candidate pool.
        Signal values are maxed; age_hours is minned; sources list is unioned.
        """
        pool: Dict[str, Dict[str, Any]] = {}

        signal_fields = (
            "youtube_velocity",
            "google_breakout",
            "reddit_momentum",
            "news_density",
            "social_spread",
        )

        for source_dict in source_results:
            if not isinstance(source_dict, dict):
                continue
            for key, signals in source_dict.items():
                if not isinstance(signals, dict):
                    continue
                topic = signals.get("full_title") or key
                norm_key = key.lower().strip()
                if not norm_key or len(norm_key) < 3:
                    continue

                if norm_key not in pool:
                    pool[norm_key] = _empty_candidate(key, topic)

                entry = pool[norm_key]
                for sf in signal_fields:
                    if sf in signals:
                        entry[sf] = max(entry[sf], float(signals[sf]))

                age = signals.get("age_hours")
                if age is not None:
                    entry["age_hours"] = min(entry["age_hours"], float(age))

                src = signals.get("source", "")
                if src and src not in entry["sources"]:
                    entry["sources"].append(src)

                ft = signals.get("full_title")
                if ft and ft != key:
                    entry["full_title"] = ft

        return list(pool.values())

    # ── Opportunity builder ───────────────────────────────────────────────────

    def _build_opportunity(self, candidate: Dict[str, Any]) -> TrendOpportunity:
        """Convert a merged candidate dict into a fully-scored TrendOpportunity."""
        full_title: str = candidate.get("full_title") or candidate.get("topic", "Unknown Topic")
        age_hours: float = candidate.get("age_hours", 24.0)
        sources: List[str] = candidate.get("sources", [])

        youtube_velocity: float = candidate.get("youtube_velocity", 0.0)
        google_breakout: float  = candidate.get("google_breakout", 0.0)
        reddit_momentum: float  = candidate.get("reddit_momentum", 0.0)
        news_density: float     = candidate.get("news_density", 0.0)
        social_spread: float    = candidate.get("social_spread", 0.0)

        opportunity_score = _compute_opportunity_score(
            youtube_velocity=youtube_velocity,
            google_breakout_rate=google_breakout,
            reddit_momentum=reddit_momentum,
            news_article_density=news_density,
            social_spread_rate=social_spread,
            topic=full_title,
            age_hours=age_hours,
            sources_hit=len(sources),
        )

        category = _detect_category(full_title)
        rpm_tier, rpm_estimate = _detect_rpm_tier(category)
        color_grade = _COLOR_GRADE_MAP.get(category, "tech_dark")

        all_five = _build_five_angles(full_title, rpm_tier, category)
        best_angle = _select_best_angle(all_five)

        # Emotional trigger
        t_lower = full_title.lower()
        if any(kw in t_lower for kw in _CONTROVERSIAL_KEYWORDS):
            emotional_trigger = "urgency"
        elif category in ("Finance", "Health"):
            emotional_trigger = "fear"
        elif category == "Technology":
            emotional_trigger = "curiosity"
        elif category == "Business":
            emotional_trigger = "desire"
        else:
            emotional_trigger = "curiosity"

        # Competition level derived from source breadth
        if len(sources) >= 8:
            competition_level = "saturated"
        elif len(sources) >= 5:
            competition_level = "high"
        elif len(sources) >= 3:
            competition_level = "medium"
        else:
            competition_level = "low"

        # Predicted CTR range per category
        ctr_map: Dict[str, Tuple[str, str]] = {
            "Finance":    ("5.5", "9.0"),
            "Technology": ("5.0", "8.5"),
            "Health":     ("4.5", "7.5"),
            "Science":    ("4.0", "7.0"),
            "Business":   ("4.8", "8.0"),
            "Psychology": ("3.8", "7.0"),
            "History":    ("3.5", "6.5"),
        }
        ctr_low, ctr_high = ctr_map.get(category, ("4.0", "7.0"))
        predicted_ctr_range = f"{ctr_low}-{ctr_high}%"

        opportunity_window = max(4, int(48 - age_hours))
        recommended_length = int(_CFG.get("script", {}).get("target_duration_seconds", 480))
        shorts_potential = (
            opportunity_score >= 0.5
            or category in ("Technology", "Finance", "Health")
        )

        key_facts: List[str] = [
            f"Topic surfaced across {len(sources)} independent source(s): {', '.join(sources[:5])}",
            f"Estimated topic age: {age_hours:.1f} hours",
            f"Opportunity window: approximately {opportunity_window} hours",
            f"Trend opportunity score: {opportunity_score:.3f}",
            f"Recommended angle: {best_angle.title}",
        ]

        supporting_stats: List[str] = [
            f"YouTube velocity score: {youtube_velocity:.3f}",
            f"Google breakout rate: {google_breakout:.3f}",
            f"Reddit momentum: {reddit_momentum:.3f}",
            f"News article density: {news_density:.3f}",
            f"Social spread rate: {social_spread:.3f}",
        ]

        target_audience = (
            _PERSONA.get("channel", {})
            .get("target_audience", {})
            .get("age_range", "18-45")
        )
        if isinstance(target_audience, str) and "-" in target_audience:
            target_audience = f"{target_audience} US adults"

        return TrendOpportunity(
            topic=full_title,
            category=category,
            selected_angle=best_angle,
            hook_sentence=best_angle.hook,
            key_facts=key_facts,
            data_sources=sources,
            supporting_stats=supporting_stats,
            emotional_trigger=emotional_trigger,
            target_audience=target_audience,
            predicted_ctr_range=predicted_ctr_range,
            rpm_category=rpm_tier,
            rpm_estimate=round(rpm_estimate, 2),
            competition_level=competition_level,
            opportunity_score=round(opportunity_score, 4),
            opportunity_window_hours=opportunity_window,
            recommended_visual_style=color_grade,
            recommended_video_length=recommended_length,
            shorts_potential=shorts_potential,
            color_grade=color_grade,
            all_five_angles=all_five,
            sources_used=sources,
            timestamp_fetched=datetime.datetime.utcnow().isoformat() + "Z",
            is_fallback=False,
        )

    # ── Fallback opportunity ──────────────────────────────────────────────────

    def _build_fallback_opportunity(self) -> TrendOpportunity:
        """
        Called when all 15 sources fail or return no scoreable candidates.
        1. Tries adaptive_fetcher for additional trending topics.
        2. Falls back to content_generator.build_daily_content().
        3. Last resort: hard-coded safety-net topic.
        """
        log.info("All live sources exhausted — trying adaptive fetcher")
        try:
            from services.adaptive_fetcher import fetch_trending_topics
            items = fetch_trending_topics(limit=10)
            if items:
                # Pick highest-scored item
                best = max(items, key=lambda x: x.get("score", 0))
                topic = best.get("title", "")
                if topic:
                    log.info("Adaptive fetcher provided topic: %s", topic)
                    category = "Technology"
                    rpm_tier, rpm_estimate = _detect_rpm_tier(category)
                    all_five = _build_five_angles(topic, rpm_tier, category)
                    best_angle = _select_best_angle(all_five)
                    return TrendOpportunity(
                        topic=topic,
                        category=category,
                        selected_angle=best_angle,
                        hook_sentence=best_angle.hook,
                        key_facts=[topic],
                        data_sources=[best.get("source", "adaptive_fetcher")],
                        supporting_stats=[],
                        emotional_trigger="curiosity",
                        target_audience="general",
                        predicted_ctr_range="3-6%",
                        rpm_category=rpm_tier,
                        rpm_estimate=rpm_estimate,
                        competition_level="medium",
                        opportunity_score=0.5,
                        opportunity_window_hours=12,
                        recommended_visual_style="tech_dark",
                        recommended_video_length=480,
                        shorts_potential=True,
                        color_grade="tech_dark",
                        all_five_angles=all_five,
                        sources_used=["adaptive_fetcher"],
                        timestamp_fetched=__import__("datetime").datetime.utcnow().isoformat(),
                        is_fallback=True,
                    )
        except Exception as exc:
            log.debug("Adaptive fetcher fallback failed: %s", exc)

        log.info("Adaptive fetcher exhausted — activating static content fallback")
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "content_generator",
                _YOUTUBE_AUTOMATION_DIR / "content_generator.py",
            )
            if spec is None or spec.loader is None:
                raise ImportError("Cannot locate content_generator.py")
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            content = mod.build_daily_content()

            topic: str = content.get("topic") or content.get("title") or "Daily Mind Fuel"
            category_raw: str = content.get("category", "Psychology")
            cat_map = {
                "money":   "Finance",
                "finance": "Finance",
                "tech":    "Technology",
                "health":  "Health",
                "science": "Science",
                "career":  "Business",
                "mindset": "Psychology",
            }
            category = cat_map.get(category_raw.lower(), "Psychology")
            rpm_tier, rpm_estimate = _detect_rpm_tier(category)
            all_five = _build_five_angles(topic, rpm_tier, category)
            best_angle = _select_best_angle(all_five)

            return TrendOpportunity(
                topic=topic,
                category=category,
                selected_angle=best_angle,
                hook_sentence=content.get("hook", best_angle.hook),
                key_facts=content.get("bullets", [best_angle.hook])[:5],
                data_sources=["content_generator_fallback"],
                supporting_stats=[],
                emotional_trigger="curiosity",
                target_audience="18-45 US adults",
                predicted_ctr_range="4.0-7.0%",
                rpm_category=rpm_tier,
                rpm_estimate=round(rpm_estimate, 2),
                competition_level="medium",
                opportunity_score=0.0,
                opportunity_window_hours=24,
                recommended_visual_style=_COLOR_GRADE_MAP.get(category, "tech_dark"),
                recommended_video_length=int(
                    _CFG.get("script", {}).get("target_duration_seconds", 480)
                ),
                shorts_potential=True,
                color_grade=_COLOR_GRADE_MAP.get(category, "tech_dark"),
                all_five_angles=all_five,
                sources_used=["content_generator_fallback"],
                timestamp_fetched=datetime.datetime.utcnow().isoformat() + "Z",
                is_fallback=True,
            )

        except Exception as exc:
            log.warning(
                "content_generator fallback failed: %s — using hardcoded safety net", exc
            )

        # Absolute last-resort hardcoded opportunity
        topic = "5 Money Habits That Will Change Your Life"
        rpm_tier, rpm_estimate = _detect_rpm_tier("Finance")
        all_five = _build_five_angles(topic, rpm_tier, "Finance")
        best_angle = _select_best_angle(all_five)
        return TrendOpportunity(
            topic=topic,
            category="Finance",
            selected_angle=best_angle,
            hook_sentence=(
                "The financial habits they never taught you in school "
                "are making someone else rich."
            ),
            key_facts=[
                "The average American saves less than 5% of their income",
                "Compound interest makes the wealthy wealthier — and it can work for anyone",
                "Automating savings removes willpower from the equation entirely",
                "One financial habit changed in your 20s is worth $200k by retirement",
                "The wealthiest Americans don't earn more — they spend differently",
            ],
            data_sources=["hardcoded_safety_net"],
            supporting_stats=[],
            emotional_trigger="fear",
            target_audience="18-45 US adults",
            predicted_ctr_range="5.5-9.0%",
            rpm_category=rpm_tier,
            rpm_estimate=round(rpm_estimate, 2),
            competition_level="high",
            opportunity_score=0.0,
            opportunity_window_hours=24,
            recommended_visual_style="gold_dark",
            recommended_video_length=480,
            shorts_potential=True,
            color_grade="gold_dark",
            all_five_angles=all_five,
            sources_used=["hardcoded_safety_net"],
            timestamp_fetched=datetime.datetime.utcnow().isoformat() + "Z",
            is_fallback=True,
        )

    # ── Main async scan ───────────────────────────────────────────────────────

    async def _run_async_scan(self) -> List[TrendOpportunity]:
        """
        Fire all 15 sources concurrently via asyncio.gather.
        Build and rank TrendOpportunity objects.
        Returns the top-N list sorted by opportunity_score descending.
        """
        log.info("TrendAnalyzer: launching 15-source concurrent scan")

        raw_results = await asyncio.gather(
            self._scan_google_trends(),
            self._scan_reddit(),
            self._scan_hackernews(),
            self._scan_newsapi(),
            self._scan_guardian(),
            self._scan_rss_feeds(),
            self._scan_arxiv(),
            self._scan_wikipedia_current_events(),
            self._scan_youtube_trending(),
            self._scan_nasa(),
            self._scan_fred(),
            self._scan_pubmed(),
            self._scan_gdelt(),
            self._scan_product_hunt(),
            self._scan_who_rss(),
            return_exceptions=True,
        )

        source_names = [
            "google_trends", "reddit", "hackernews", "newsapi", "guardian",
            "rss_feeds", "arxiv", "wikipedia", "youtube_trending", "nasa",
            "fred", "pubmed", "gdelt", "product_hunt", "who_rss",
        ]
        valid_results: List[Dict[str, Dict[str, Any]]] = []
        for idx, result in enumerate(raw_results):
            name = source_names[idx]
            if isinstance(result, Exception):
                log.info("Source %s raised exception: %s", name, result)
            elif isinstance(result, dict):
                log.info("Source %s returned %d signals", name, len(result))
                valid_results.append(result)
            else:
                log.info("Source %s returned unexpected type: %s", name, type(result))

        candidates = self._merge_all_sources(valid_results)
        log.info("Merged candidate pool: %d topics", len(candidates))

        # Pre-sort by raw composite strength then cap at _MAX_TRENDS
        def _raw_strength(c: Dict[str, Any]) -> float:
            return (
                c["youtube_velocity"]  * 0.30
                + c["google_breakout"]   * 0.25
                + c["reddit_momentum"]   * 0.20
                + c["news_density"]      * 0.15
                + c["social_spread"]     * 0.10
            )

        candidates.sort(key=_raw_strength, reverse=True)
        candidates = candidates[:_MAX_TRENDS]

        opportunities: List[TrendOpportunity] = []
        for candidate in candidates:
            try:
                opp = self._build_opportunity(candidate)
                opportunities.append(opp)
            except Exception as exc:
                log.info(
                    "Failed to build opportunity for '%s': %s",
                    candidate.get("topic"),
                    exc,
                )

        opportunities.sort(key=lambda o: o.opportunity_score, reverse=True)
        log.info(
            "Scored %d opportunities; top score: %.4f",
            len(opportunities),
            opportunities[0].opportunity_score if opportunities else 0.0,
        )

        await self._close_session()
        return opportunities

    # ── scan_all_trends (sync public entry-point) ─────────────────────────────

    def scan_all_trends(self) -> List[TrendOpportunity]:
        """
        Synchronous public entry-point.  Runs the full 15-source scan and
        returns a ranked list of TrendOpportunity objects, highest score first.
        Appends a fallback opportunity if nothing scores above _MIN_SCORE.
        """
        try:
            asyncio.get_running_loop()
            # Running inside an existing event loop — spawn dedicated thread
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, self._run_async_scan())
                opportunities = future.result(timeout=95)
        except RuntimeError:
            opportunities = asyncio.run(self._run_async_scan())
        except Exception as exc:
            log.warning("scan_all_trends failed: %s — using fallback", exc)
            return [self._build_fallback_opportunity()]

        if not opportunities or opportunities[0].opportunity_score < _MIN_SCORE:
            log.info(
                "No opportunity exceeded min_score=%.2f — appending fallback", _MIN_SCORE
            )
            opportunities.append(self._build_fallback_opportunity())

        return opportunities

    # ── get_best_opportunity (async public entry-point) ───────────────────────

    async def get_best_opportunity(self) -> TrendOpportunity:
        """
        Async public entry-point.  Scans all 15 sources and returns the
        single highest-scoring TrendOpportunity.  Hard timeout: 90 seconds.
        """
        try:
            opportunities = await asyncio.wait_for(
                self._run_async_scan(),
                timeout=90.0,
            )
        except asyncio.TimeoutError:
            log.warning("get_best_opportunity: 90s timeout — activating fallback")
            await self._close_session()
            return self._build_fallback_opportunity()
        except Exception as exc:
            log.warning("get_best_opportunity error: %s — activating fallback", exc)
            await self._close_session()
            return self._build_fallback_opportunity()

        valid = [o for o in opportunities if o.opportunity_score > _MIN_SCORE]
        if valid:
            return valid[0]
        if opportunities:
            # Return best available even if below threshold
            return opportunities[0]
        return self._build_fallback_opportunity()

    # ── get_best_opportunity_sync (sync, event-loop-safe) ────────────────────

    def get_best_opportunity_sync(self) -> TrendOpportunity:
        """
        Synchronous, event-loop-safe wrapper around get_best_opportunity().

        Works in all contexts:
        - Inside a running event loop (Jupyter, async host): spawns a
          ThreadPoolExecutor thread with its own dedicated event loop.
        - Outside any loop: calls asyncio.run() directly.
        """
        try:
            asyncio.get_running_loop()
            # A loop is running — must delegate to a separate thread
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(
                    asyncio.run, TrendAnalyzer().get_best_opportunity()
                )
                return future.result(timeout=95)
        except RuntimeError:
            # No running loop — safe to call asyncio.run() directly
            return asyncio.run(self.get_best_opportunity())
        except Exception as exc:
            log.warning("get_best_opportunity_sync failed: %s", exc)
            return self._build_fallback_opportunity()


# ── Module-level convenience aliases (mirrors old API surface) ────────────────

def get_best_opportunity_sync() -> TrendOpportunity:
    """Module-level sync shortcut — drop-in replacement for old API."""
    return TrendAnalyzer().get_best_opportunity_sync()


async def get_best_opportunity() -> TrendOpportunity:
    """Module-level async shortcut — drop-in replacement for old API."""
    return await TrendAnalyzer().get_best_opportunity()


# ── CLI smoke-test ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    print("GetMindFuelNow — Trend Intelligence Engine (15 sources)")
    print("Scanning… (90s timeout)")
    opp = get_best_opportunity_sync()
    div = "=" * 60
    print(f"\n{div}")
    print("BEST OPPORTUNITY")
    print(div)
    print(f"  Topic          : {opp.topic}")
    print(f"  Category       : {opp.category}")
    print(f"  RPM Tier       : {opp.rpm_category}  (est. ${opp.rpm_estimate:.2f})")
    print(f"  Opp. Score     : {opp.opportunity_score:.4f}")
    print(f"  Competition    : {opp.competition_level}")
    print(f"  Window         : {opp.opportunity_window_hours}h")
    print(f"  Is Fallback    : {opp.is_fallback}")
    print(f"  Fetched At     : {opp.timestamp_fetched}")
    print(f"\n  Selected Angle : [{opp.selected_angle.angle_type}]")
    print(f"  Title          : {opp.selected_angle.title}")
    print(f"  Hook           : {opp.hook_sentence}")
    print(f"\n  Sources Used   : {', '.join(opp.sources_used) or 'none'}")
    print(f"\n  Key Facts:")
    for fact in opp.key_facts:
        print(f"    • {fact}")
    print(f"\n  All Five Angles:")
    for a in opp.all_five_angles:
        print(f"    [{a.angle_type:22s}] composite={a.composite_score:.3f}  {a.title}")
    print(div)
