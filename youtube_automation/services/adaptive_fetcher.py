"""
services/adaptive_fetcher.py
Adaptive content fetcher — tries every registered source in priority order,
falls back automatically on any failure, and records health in SourceRegistry.

Never raises for the caller. Always returns *something* (even if empty list).
"""
from __future__ import annotations

import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from services.api_key_manager import get_manager as _keys
from services.source_registry import get_registry as _reg

log = logging.getLogger(__name__)

# Simple in-process cache: {cache_key: (timestamp, value)}
_CACHE: Dict[str, tuple] = {}
_CACHE_TTL_S = 600   # 10 minutes


def _from_cache(key: str):
    entry = _CACHE.get(key)
    if entry and time.time() - entry[0] < _CACHE_TTL_S:
        return entry[1]
    return None


def _to_cache(key: str, value) -> None:
    _CACHE[key] = (time.time(), value)


# ─────────────────────────────────────────────────────────────────────────────
# TRENDING TOPICS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_trending_topics(limit: int = 10) -> List[dict]:
    """
    Return up to *limit* trending topics from the best available source.
    Each item: {title, url, source, score}
    """
    cached = _from_cache(f"trending_{limit}")
    if cached:
        return cached

    results: List[dict] = []
    reg = _reg()

    for src in reg.get_sources("trending"):
        if results:
            break
        try:
            items = _fetch_trending_from(src.id)
            if items:
                reg.record_success(src.id)
                results = items[:limit]
                log.info("Trending: fetched %d items from %s", len(results), src.name)
        except Exception as exc:
            log.warning("Trending source %s failed: %s", src.id, exc)
            reg.record_failure(src.id)

    _to_cache(f"trending_{limit}", results)
    return results


def _fetch_trending_from(source_id: str) -> List[dict]:
    import requests

    if source_id == "hackernews":
        r = requests.get(
            "https://hacker-news.firebaseio.com/v0/topstories.json", timeout=8
        )
        ids = r.json()[:20]
        items = []
        for story_id in ids[:8]:
            sr = requests.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json",
                timeout=5,
            )
            d = sr.json()
            if d and d.get("title"):
                items.append({
                    "title": d["title"],
                    "url": d.get("url", ""),
                    "source": "HackerNews",
                    "score": d.get("score", 0),
                })
        return items

    if source_id in ("reddit_all", "reddit_tech"):
        ep = (
            "https://www.reddit.com/r/all/hot.json"
            if source_id == "reddit_all"
            else "https://www.reddit.com/r/technology+science+worldnews/hot.json"
        )
        r = requests.get(ep, timeout=8, headers={"User-Agent": "mind-fuel-bot/1.0"})
        posts = r.json().get("data", {}).get("children", [])
        return [
            {
                "title": p["data"].get("title", ""),
                "url": p["data"].get("url", ""),
                "source": "Reddit",
                "score": p["data"].get("score", 0),
            }
            for p in posts
            if p.get("data", {}).get("title")
        ]

    if source_id == "google_trends":
        try:
            from pytrends.request import TrendReq  # type: ignore
            pt = TrendReq(hl="en-US", tz=0, timeout=(5, 15))
            df = pt.trending_searches(pn="united_states")
            return [
                {"title": str(row), "url": "", "source": "GoogleTrends", "score": 100 - i}
                for i, row in enumerate(df[0].tolist()[:15])
            ]
        except Exception as exc:
            raise RuntimeError(f"pytrends: {exc}") from exc

    if source_id == "gdelt_trending":
        r = requests.get(
            "https://api.gdeltproject.org/api/v2/doc/doc"
            "?mode=artlist&maxrecords=25&format=json",
            timeout=10,
        )
        articles = r.json().get("articles", [])
        return [
            {"title": a.get("title", ""), "url": a.get("url", ""),
             "source": "GDELT", "score": 50}
            for a in articles if a.get("title")
        ]

    if source_id == "producthunt_rss":
        return _fetch_rss("https://www.producthunt.com/feed", "ProductHunt")

    # Generic RSS fallback for any rss-type source
    src = _reg().get_by_id(source_id)
    if src and src.source_type == "rss" and src.endpoint:
        return _fetch_rss(src.endpoint, src.name)

    return []


def _fetch_rss(url: str, source_name: str) -> List[dict]:
    import feedparser  # type: ignore
    feed = feedparser.parse(url)
    return [
        {
            "title": e.get("title", ""),
            "url": e.get("link", ""),
            "source": source_name,
            "score": 50,
        }
        for e in feed.entries
        if e.get("title")
    ]


# ─────────────────────────────────────────────────────────────────────────────
# IMAGES
# ─────────────────────────────────────────────────────────────────────────────

def fetch_image(query: str, out_path: str) -> Optional[str]:
    """
    Download a royalty-free image for *query* to *out_path*.
    Tries: Pexels → Pixabay → Unsplash → NASA APOD → Wikimedia → Lorem Picsum.
    Returns out_path on success, None if all fail.
    """
    cached = _from_cache(f"img_{query}")
    if cached and os.path.exists(cached):
        return cached

    reg = _reg()
    for src in reg.get_sources("images"):
        try:
            result = _fetch_image_from(src.id, query, out_path)
            if result:
                reg.record_success(src.id)
                _to_cache(f"img_{query}", result)
                log.info("Image fetched via %s: %s", src.name, result)
                return result
        except Exception as exc:
            log.warning("Image source %s failed (%s)", src.id, exc)
            reg.record_failure(src.id)

    return None


def _fetch_image_from(source_id: str, query: str, out_path: str) -> Optional[str]:
    import requests

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if source_id == "pexels_images":
        key = _keys().get_key("pexels")
        if not key:
            return None
        r = requests.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "per_page": 1, "orientation": "landscape"},
            headers={"Authorization": key},
            timeout=10,
        )
        r.raise_for_status()
        photos = r.json().get("photos", [])
        if photos:
            url = photos[0]["src"]["original"]
            urllib.request.urlretrieve(url, out_path)
            return out_path

    if source_id == "pixabay_images":
        key = _keys().get_key("pixabay")
        if not key:
            return None
        r = requests.get(
            "https://pixabay.com/api/",
            params={"key": key, "q": query, "image_type": "photo",
                    "per_page": 3, "min_width": 1920},
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if hits:
            url = hits[0].get("largeImageURL", hits[0].get("webformatURL"))
            urllib.request.urlretrieve(url, out_path)
            return out_path

    if source_id == "unsplash_images":
        key = _keys().get_key("unsplash")
        if not key:
            return None
        r = requests.get(
            "https://api.unsplash.com/photos/random",
            params={"query": query, "orientation": "landscape"},
            headers={"Authorization": f"Client-ID {key}"},
            timeout=10,
        )
        r.raise_for_status()
        url = r.json()["urls"]["full"]
        urllib.request.urlretrieve(url, out_path)
        return out_path

    if source_id == "nasa_apod":
        r = requests.get(
            "https://api.nasa.gov/planetary/apod",
            params={"api_key": "DEMO_KEY"},
            timeout=10,
        )
        r.raise_for_status()
        url = r.json().get("hdurl") or r.json().get("url")
        if url and url.endswith((".jpg", ".png", ".jpeg")):
            urllib.request.urlretrieve(url, out_path)
            return out_path

    if source_id == "wikimedia_images":
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={"action": "query", "list": "search",
                    "srsearch": f"{query} file:jpg", "srnamespace": "6",
                    "format": "json", "srlimit": "3"},
            timeout=10,
        )
        r.raise_for_status()
        items = r.json().get("query", {}).get("search", [])
        if items:
            title = items[0]["title"].replace(" ", "_")
            img_r = requests.get(
                "https://commons.wikimedia.org/w/api.php",
                params={"action": "query", "titles": title,
                        "prop": "imageinfo", "iiprop": "url",
                        "format": "json"},
                timeout=10,
            )
            pages = img_r.json().get("query", {}).get("pages", {})
            for page in pages.values():
                url = (page.get("imageinfo") or [{}])[0].get("url", "")
                if url:
                    urllib.request.urlretrieve(url, out_path)
                    return out_path

    if source_id == "lorem_picsum":
        url = "https://picsum.photos/1920/1080"
        urllib.request.urlretrieve(url, out_path)
        return out_path

    return None


# ─────────────────────────────────────────────────────────────────────────────
# VIDEO CLIPS
# ─────────────────────────────────────────────────────────────────────────────

def fetch_video_clip(query: str, out_dir: str, scene_id: int = 0) -> Optional[str]:
    """
    Download a royalty-free video clip matching *query* into *out_dir*.
    Tries all registered video sources in priority order.
    """
    reg = _reg()
    for src in reg.get_sources("video"):
        if src.id == "pil_generated":   # local fallback handled elsewhere
            continue
        try:
            result = _fetch_video_from(src.id, query, out_dir, scene_id)
            if result:
                reg.record_success(src.id)
                log.info("Video clip via %s: %s", src.name, result)
                return result
        except Exception as exc:
            log.warning("Video source %s failed (%s)", src.id, exc)
            reg.record_failure(src.id)
    return None


def _fetch_video_from(source_id: str, query: str, out_dir: str, scene_id: int) -> Optional[str]:
    import requests

    Path(out_dir).mkdir(parents=True, exist_ok=True)
    out_path = str(Path(out_dir) / f"clip_{scene_id:02d}_{source_id}.mp4")

    if source_id == "pexels_video":
        key = _keys().get_key("pexels")
        if not key:
            return None
        r = requests.get(
            "https://api.pexels.com/videos/search",
            params={"query": query, "per_page": 3,
                    "min_duration": 5, "max_duration": 30},
            headers={"Authorization": key},
            timeout=12,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if videos:
            files = videos[0].get("video_files", [])
            hd = next(
                (f for f in files if f.get("quality") in ("hd", "sd")), None
            )
            if hd:
                urllib.request.urlretrieve(hd["link"], out_path)
                return out_path

    if source_id == "pixabay_video":
        key = _keys().get_key("pixabay")
        if not key:
            return None
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"key": key, "q": query, "per_page": 3,
                    "min_duration": 5, "max_duration": 30},
            timeout=12,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if hits:
            videos = hits[0].get("videos", {})
            url = (videos.get("medium") or videos.get("small") or {}).get("url")
            if url:
                urllib.request.urlretrieve(url, out_path)
                return out_path

    # Generic RSS/scrape source — try direct URL if source has a known endpoint
    src = _reg().get_by_id(source_id)
    if src and src.source_type == "scrape" and src.endpoint:
        import requests
        r = requests.get(src.endpoint + query, timeout=10)
        if r.status_code == 200:
            # Best-effort: look for .mp4 links in response
            import re
            urls = re.findall(r'https?://[^\s"\']+\.mp4', r.text)
            if urls:
                urllib.request.urlretrieve(urls[0], out_path)
                return out_path

    return None


# ─────────────────────────────────────────────────────────────────────────────
# BACKGROUND MUSIC
# ─────────────────────────────────────────────────────────────────────────────

def fetch_background_music(mood: str, out_path: str, duration_s: int = 300) -> Optional[str]:
    """
    Download royalty-free background music for *mood* to *out_path*.
    Tries: Pixabay → ccMixter RSS → FreePD → procedural synthesis fallback.
    """
    reg = _reg()
    for src in reg.get_sources("music"):
        if src.id == "procedural_music":
            continue
        try:
            result = _fetch_music_from(src.id, mood, out_path, duration_s)
            if result:
                reg.record_success(src.id)
                log.info("Music via %s: %s", src.name, result)
                return result
        except Exception as exc:
            log.warning("Music source %s failed (%s)", src.id, exc)
            reg.record_failure(src.id)
    return None   # caller falls back to procedural


def _fetch_music_from(source_id: str, mood: str, out_path: str, duration_s: int) -> Optional[str]:
    import requests

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if source_id == "pixabay_music":
        key = _keys().get_key("pixabay")
        if not key:
            return None
        r = requests.get(
            "https://pixabay.com/api/music/",
            params={"key": key, "q": mood,
                    "duration_from": max(30, duration_s - 60)},
            timeout=10,
        )
        r.raise_for_status()
        hits = r.json().get("hits", [])
        if hits:
            audio_url = hits[0].get("audio", {}).get("url")
            if audio_url:
                urllib.request.urlretrieve(audio_url, out_path)
                return out_path

    if source_id == "ccmixter_rss":
        import feedparser  # type: ignore
        feed = feedparser.parse(
            f"https://ccmixter.org/api/query?f=rss&type=all&tags={mood}&limit=5"
        )
        for entry in feed.entries:
            links = getattr(entry, "enclosures", [])
            for link in links:
                url = link.get("url", "")
                if url and (".mp3" in url or ".ogg" in url):
                    urllib.request.urlretrieve(url, out_path)
                    return out_path

    return None


# ─────────────────────────────────────────────────────────────────────────────
# NEWS / FACTS for a topic
# ─────────────────────────────────────────────────────────────────────────────

def fetch_news_for_topic(topic: str, limit: int = 10) -> List[dict]:
    """
    Return up to *limit* recent news articles about *topic*.
    Each item: {title, description, url, source, published_at}
    """
    cached = _from_cache(f"news_{topic}_{limit}")
    if cached:
        return cached

    results: List[dict] = []
    reg = _reg()

    for src in reg.get_sources("news"):
        if results:
            break
        try:
            items = _fetch_news_from(src.id, topic, limit)
            if items:
                reg.record_success(src.id)
                results = items
                log.info("News: %d items from %s for '%s'", len(results), src.name, topic)
        except Exception as exc:
            log.warning("News source %s failed (%s)", src.id, exc)
            reg.record_failure(src.id)

    _to_cache(f"news_{topic}_{limit}", results[:limit])
    return results[:limit]


def _fetch_news_from(source_id: str, topic: str, limit: int) -> List[dict]:
    import requests

    if source_id == "newsapi":
        key = _keys().get_key("newsapi")
        if not key:
            return []
        r = requests.get(
            "https://newsapi.org/v2/everything",
            params={"q": topic, "language": "en", "sortBy": "publishedAt",
                    "pageSize": limit, "apiKey": key},
            timeout=10,
        )
        r.raise_for_status()
        return [
            {"title": a["title"], "description": a.get("description", ""),
             "url": a["url"], "source": a["source"]["name"],
             "published_at": a.get("publishedAt", "")}
            for a in r.json().get("articles", [])
        ]

    if source_id == "guardian_api":
        key = _keys().get_key("guardian")
        if not key:
            return []
        r = requests.get(
            "https://content.guardianapis.com/search",
            params={"q": topic, "api-key": key, "page-size": limit,
                    "order-by": "newest", "show-fields": "headline,trailText"},
            timeout=10,
        )
        r.raise_for_status()
        results = r.json().get("response", {}).get("results", [])
        return [
            {"title": item["webTitle"],
             "description": item.get("fields", {}).get("trailText", ""),
             "url": item["webUrl"], "source": "The Guardian",
             "published_at": item.get("webPublicationDate", "")}
            for item in results
        ]

    if source_id == "gnews_rss":
        import feedparser  # type: ignore
        url = f"https://news.google.com/rss/search?q={requests.utils.quote(topic)}&hl=en-US&gl=US&ceid=US:en"
        feed = feedparser.parse(url)
        return [
            {"title": e.get("title", ""), "description": e.get("summary", ""),
             "url": e.get("link", ""), "source": "Google News",
             "published_at": e.get("published", "")}
            for e in feed.entries[:limit]
        ]

    # Generic RSS sources
    src = _reg().get_by_id(source_id)
    if src and src.source_type == "rss" and src.endpoint:
        import feedparser  # type: ignore
        feed = feedparser.parse(src.endpoint)
        keyword = topic.lower()
        matches = [
            {"title": e.get("title", ""), "description": e.get("summary", ""),
             "url": e.get("link", ""), "source": src.name,
             "published_at": e.get("published", "")}
            for e in feed.entries
            if keyword in e.get("title", "").lower() or keyword in e.get("summary", "").lower()
        ]
        return matches[:limit] if matches else [
            {"title": e.get("title", ""), "description": e.get("summary", ""),
             "url": e.get("link", ""), "source": src.name,
             "published_at": e.get("published", "")}
            for e in feed.entries[:limit]
        ]

    return []


# ─────────────────────────────────────────────────────────────────────────────
# TTS voice — supplement the audio engine's own fallback chain
# ─────────────────────────────────────────────────────────────────────────────

def fetch_tts(text: str, out_path: str, tone: str = "authoritative") -> Optional[str]:
    """
    Generate TTS audio, using the adaptive source chain.
    Supplements cinematic_audio_engine which already has its own fallbacks.
    Returns out_path or None.
    """
    reg = _reg()
    for src in reg.get_sources("audio_tts"):
        try:
            result = _fetch_tts_from(src.id, text, out_path, tone)
            if result and os.path.exists(result) and os.path.getsize(result) > 0:
                reg.record_success(src.id)
                return result
        except Exception as exc:
            log.warning("TTS source %s failed (%s)", src.id, exc)
            reg.record_failure(src.id)
    return None


def _fetch_tts_from(source_id: str, text: str, out_path: str, tone: str) -> Optional[str]:
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if source_id == "elevenlabs_tts":
        from services.elevenlabs_service import generate
        return generate(text, out_path, tone)

    if source_id == "openai_tts":
        from services.openai_tts_service import generate
        return generate(text, out_path, tone)

    if source_id == "gtts":
        try:
            from gtts import gTTS  # type: ignore
            tts = gTTS(text=text, lang="en", slow=False)
            mp3_path = out_path.replace(".wav", ".mp3")
            tts.save(mp3_path)
            # Convert mp3 → wav via pydub
            try:
                from pydub import AudioSegment  # type: ignore
                AudioSegment.from_mp3(mp3_path).export(out_path, format="wav")
                os.remove(mp3_path)
            except Exception:
                os.rename(mp3_path, out_path)
            return out_path
        except ImportError:
            return None

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Source health summary (for logs/reports)
# ─────────────────────────────────────────────────────────────────────────────

def source_health_report() -> str:
    lines = ["=== ADAPTIVE FETCHER SOURCE HEALTH ==="]
    for category, info in _reg().health_summary().items():
        lines.append(
            f"  {category:<12} total={info['total']:>2}  active={info['active']:>2}  "
            f"keyless={info['keyless']:>2}  avg_reliability={info['avg_reliability']:.0%}"
        )
    km = _keys()
    lines.append("\n=== API KEY STATUS ===")
    for svc, status in km.get_service_status().items():
        icon = "✓" if status["available"] else ("~" if status["demo_key"] else "✗")
        lines.append(f"  {icon} {svc:<20} {status['env_var']}")
    return "\n".join(lines)
