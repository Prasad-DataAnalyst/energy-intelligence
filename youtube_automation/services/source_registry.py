"""
services/source_registry.py
Dynamic registry of every data source the pipeline can use.

Each source has:
  id, name, category, type, url/endpoint, key_service (or None),
  reliability_score (0-1), priority (lower = higher priority),
  last_tested, last_success, capabilities, tags

The registry is seeded from config/source_registry.json and is
continually updated by the SelfEnhancementLoop (can discover new
RSS feeds, public APIs, and scrape-based sources automatically).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional

import requests

from services.api_key_manager import get_manager as _key_mgr

log = logging.getLogger(__name__)

_REGISTRY_FILE = Path(__file__).parent.parent / "config" / "source_registry.json"

# Categories the pipeline cares about
CATEGORIES = {
    "trending":  "Real-time trend and topic discovery",
    "news":      "News articles and current events",
    "images":    "Still images (royalty-free)",
    "video":     "Video clips (royalty-free)",
    "audio_tts": "Text-to-speech voice generation",
    "music":     "Background music (royalty-free)",
    "facts":     "Scientific and factual data",
}


class Source:
    """Represents one data source entry."""

    __slots__ = (
        "id", "name", "category", "source_type",
        "endpoint", "key_service", "priority",
        "reliability", "last_tested", "last_success",
        "capabilities", "tags", "active",
    )

    def __init__(self, data: dict) -> None:
        self.id: str           = data["id"]
        self.name: str         = data["name"]
        self.category: str     = data["category"]
        self.source_type: str  = data.get("type", "api")   # api | rss | scrape | local
        self.endpoint: str     = data.get("endpoint", "")
        self.key_service: Optional[str] = data.get("key_service")  # None → keyless
        self.priority: int     = data.get("priority", 50)
        self.reliability: float = data.get("reliability", 0.8)
        self.last_tested: float = data.get("last_tested", 0.0)
        self.last_success: float = data.get("last_success", 0.0)
        self.capabilities: List[str] = data.get("capabilities", [])
        self.tags: List[str]   = data.get("tags", [])
        self.active: bool      = data.get("active", True)

    @property
    def requires_key(self) -> bool:
        return bool(self.key_service)

    @property
    def key_available(self) -> bool:
        if not self.requires_key:
            return True
        return _key_mgr().is_available(self.key_service)  # type: ignore[arg-type]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name,
            "category": self.category, "type": self.source_type,
            "endpoint": self.endpoint, "key_service": self.key_service,
            "priority": self.priority, "reliability": self.reliability,
            "last_tested": self.last_tested, "last_success": self.last_success,
            "capabilities": self.capabilities, "tags": self.tags,
            "active": self.active,
        }


class SourceRegistry:
    """
    Manages all data sources used by the pipeline.

    Key methods:
      get_sources(category)          → ranked list of available Sources
      record_success(source_id)      → raise reliability score
      record_failure(source_id)      → lower reliability score
      discover_rss_feeds(topic)      → auto-find new RSS feeds, add them
      add_source(data)               → manually register a new source
      save()                         → persist registry to JSON
    """

    def __init__(self) -> None:
        self._sources: Dict[str, Source] = {}
        self._load()

    # ------------------------------------------------------------------ #
    # Retrieval                                                            #
    # ------------------------------------------------------------------ #

    def get_sources(self, category: str, require_available: bool = True) -> List[Source]:
        """
        Return sources for *category* sorted by priority then reliability.
        If *require_available*, only include sources whose key (if any) exists.
        """
        results = [
            s for s in self._sources.values()
            if s.category == category and s.active
            and (not require_available or s.key_available)
        ]
        results.sort(key=lambda s: (s.priority, -s.reliability))
        return results

    def get_by_id(self, source_id: str) -> Optional[Source]:
        return self._sources.get(source_id)

    def all_categories(self) -> List[str]:
        return sorted({s.category for s in self._sources.values()})

    # ------------------------------------------------------------------ #
    # Health tracking                                                      #
    # ------------------------------------------------------------------ #

    def record_success(self, source_id: str) -> None:
        s = self._sources.get(source_id)
        if not s:
            return
        s.last_success = time.time()
        s.last_tested  = time.time()
        s.reliability  = min(1.0, s.reliability * 0.95 + 0.05)  # EMA nudge up
        self.save()

    def record_failure(self, source_id: str) -> None:
        s = self._sources.get(source_id)
        if not s:
            return
        s.last_tested = time.time()
        s.reliability = max(0.0, s.reliability * 0.85)           # EMA decay
        if s.reliability < 0.1:
            s.active = False
            log.warning("Source disabled (low reliability): %s", source_id)
        self.save()

    def reactivate(self, source_id: str) -> None:
        s = self._sources.get(source_id)
        if s:
            s.active = True
            s.reliability = 0.5
            self.save()

    # ------------------------------------------------------------------ #
    # Discovery: RSS feed auto-finder                                     #
    # ------------------------------------------------------------------ #

    def discover_rss_feeds(self, topic: str, max_new: int = 5) -> int:
        """
        Search Google News RSS for *topic*, parse article domains,
        attempt to discover domain RSS feeds, and add them to the registry.
        Returns count of newly registered sources.
        """
        added = 0
        try:
            import feedparser  # type: ignore

            gnews_url = (
                f"https://news.google.com/rss/search?q={requests.utils.quote(topic)}"
                "&hl=en-US&gl=US&ceid=US:en"
            )
            feed = feedparser.parse(gnews_url)
            seen_domains: set = set()

            for entry in feed.entries[:20]:
                link = getattr(entry, "link", "")
                if not link:
                    continue
                # Extract domain
                from urllib.parse import urlparse
                domain = urlparse(link).netloc.replace("www.", "")
                if domain in seen_domains or len(seen_domains) >= max_new:
                    continue
                seen_domains.add(domain)

                # Candidate RSS URLs to probe
                for path in ["/rss", "/feed", "/rss.xml", "/feed.xml",
                              "/atom.xml", "/feeds/posts/default"]:
                    candidate = f"https://{domain}{path}"
                    if self._probe_rss(candidate):
                        sid = f"rss_auto_{domain.replace('.', '_')}"
                        if sid not in self._sources:
                            self.add_source({
                                "id": sid,
                                "name": f"{domain} (auto-discovered)",
                                "category": "news",
                                "type": "rss",
                                "endpoint": candidate,
                                "priority": 70,
                                "reliability": 0.6,
                                "capabilities": ["news", "trending"],
                                "tags": ["auto", topic.lower()],
                            })
                            added += 1
                            log.info("Discovered RSS feed: %s", candidate)
                        break
        except Exception as exc:
            log.debug("RSS discovery error for '%s': %s", topic, exc)
        return added

    def discover_public_apis(self) -> int:
        """
        Probe a curated list of known public/keyless API endpoints.
        Adds any that respond successfully.
        Returns count of newly activated sources.
        """
        candidates = [
            # Keyless trend/news sources
            {"id": "hn_firebase",
             "name": "HackerNews Firebase",
             "category": "trending", "type": "api",
             "endpoint": "https://hacker-news.firebaseio.com/v0/topstories.json",
             "priority": 10},
            {"id": "reddit_public",
             "name": "Reddit Public JSON",
             "category": "trending", "type": "api",
             "endpoint": "https://www.reddit.com/r/technology/hot.json",
             "priority": 15},
            {"id": "devto_api",
             "name": "Dev.to API",
             "category": "news", "type": "api",
             "endpoint": "https://dev.to/api/articles?top=1",
             "priority": 40},
            {"id": "gdelt_doc",
             "name": "GDELT DocCloud",
             "category": "news", "type": "api",
             "endpoint": "https://api.gdeltproject.org/api/v2/doc/doc?query=AI&mode=artlist&maxrecords=5&format=json",
             "priority": 30},
            {"id": "arxiv_api",
             "name": "arXiv API",
             "category": "facts", "type": "api",
             "endpoint": "https://export.arxiv.org/api/query?search_query=all:AI&max_results=5",
             "priority": 25},
            {"id": "openlibrary",
             "name": "Open Library",
             "category": "facts", "type": "api",
             "endpoint": "https://openlibrary.org/search.json?q=science&limit=3",
             "priority": 60},
            {"id": "wikimedia_commons",
             "name": "Wikimedia Commons",
             "category": "images", "type": "api",
             "endpoint": "https://commons.wikimedia.org/w/api.php?action=query&list=categorymembers&cmtitle=Category:Featured_pictures&format=json&cmlimit=5",
             "priority": 25},
            {"id": "nasa_apod",
             "name": "NASA APOD",
             "category": "images", "type": "api",
             "endpoint": "https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY",
             "priority": 20},
            {"id": "freesound_preview",
             "name": "Freesound Preview",
             "category": "music", "type": "api",
             "endpoint": "https://freesound.org/apiv2/search/text/?query=ambient&fields=previews&format=json&token=",
             "priority": 40},
        ]
        added = 0
        for cand in candidates:
            sid = cand["id"]
            if sid in self._sources:
                continue
            ep = cand.get("endpoint", "")
            if ep and self._probe_url(ep):
                cand.setdefault("capabilities", [cand["category"]])
                cand.setdefault("reliability", 0.7)
                cand.setdefault("tags", ["auto-verified"])
                self.add_source(cand)
                added += 1
        return added

    # ------------------------------------------------------------------ #
    # Management                                                           #
    # ------------------------------------------------------------------ #

    def add_source(self, data: dict) -> Source:
        src = Source(data)
        self._sources[src.id] = src
        self.save()
        return src

    def save(self) -> None:
        _REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        payload = [s.to_dict() for s in self._sources.values()]
        try:
            _REGISTRY_FILE.write_text(json.dumps(payload, indent=2))
        except Exception as exc:
            log.debug("Registry save failed: %s", exc)

    def health_summary(self) -> dict:
        summary: dict = {}
        for cat in self.all_categories():
            srcs = self.get_sources(cat)
            summary[cat] = {
                "total": len(srcs),
                "active": sum(1 for s in srcs if s.active),
                "keyless": sum(1 for s in srcs if not s.requires_key),
                "avg_reliability": (
                    sum(s.reliability for s in srcs) / len(srcs) if srcs else 0.0
                ),
            }
        return summary

    # ------------------------------------------------------------------ #
    # Internals                                                            #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if _REGISTRY_FILE.exists():
            try:
                data = json.loads(_REGISTRY_FILE.read_text())
                for entry in data:
                    s = Source(entry)
                    self._sources[s.id] = s
                log.debug("Source registry loaded: %d sources", len(self._sources))
                return
            except Exception as exc:
                log.warning("Registry load failed (%s); using defaults.", exc)
        # Seed with defaults if file missing
        self._seed_defaults()
        self.save()

    def _seed_defaults(self) -> None:
        """Seed the registry with all known sources."""
        defaults = _DEFAULT_SOURCES
        for entry in defaults:
            s = Source(entry)
            self._sources[s.id] = s
        log.info("Source registry seeded with %d entries.", len(self._sources))

    def _probe_rss(self, url: str, timeout: int = 4) -> bool:
        try:
            r = requests.get(url, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            return r.status_code == 200 and (
                "xml" in r.headers.get("content-type", "")
                or r.text.strip().startswith("<")
            )
        except Exception:
            return False

    def _probe_url(self, url: str, timeout: int = 5) -> bool:
        try:
            r = requests.get(url, timeout=timeout,
                             headers={"User-Agent": "Mozilla/5.0"})
            return r.status_code < 400
        except Exception:
            return False


# ─────────────────────────────────────────────────────────────────────────────
# Default source definitions (seeded on first run)
# ─────────────────────────────────────────────────────────────────────────────
_DEFAULT_SOURCES = [
    # ── TRENDING ─────────────────────────────────────────────────────────────
    {"id": "google_trends",      "name": "Google Trends",         "category": "trending",
     "type": "api",  "endpoint": "", "priority": 5,  "reliability": 0.90,
     "capabilities": ["trending", "topics"], "tags": ["keyless"]},
    {"id": "hackernews",         "name": "Hacker News Firebase",  "category": "trending",
     "type": "api",  "endpoint": "https://hacker-news.firebaseio.com/v0/topstories.json",
     "priority": 8,  "reliability": 0.95, "capabilities": ["trending", "tech"], "tags": ["keyless"]},
    {"id": "reddit_all",         "name": "Reddit /r/all",         "category": "trending",
     "type": "api",  "endpoint": "https://www.reddit.com/r/all/hot.json",
     "priority": 10, "reliability": 0.88, "capabilities": ["trending"], "tags": ["keyless"]},
    {"id": "reddit_tech",        "name": "Reddit Tech/Science",   "category": "trending",
     "type": "api",  "endpoint": "https://www.reddit.com/r/technology+science+worldnews/hot.json",
     "priority": 12, "reliability": 0.88, "capabilities": ["trending", "tech"], "tags": ["keyless"]},
    {"id": "gdelt_trending",     "name": "GDELT Trending",        "category": "trending",
     "type": "api",  "endpoint": "https://api.gdeltproject.org/api/v2/doc/doc?mode=artlist&maxrecords=25&format=json",
     "priority": 20, "reliability": 0.80, "capabilities": ["trending", "global"], "tags": ["keyless"]},
    {"id": "producthunt_rss",    "name": "Product Hunt RSS",      "category": "trending",
     "type": "rss",  "endpoint": "https://www.producthunt.com/feed",
     "priority": 30, "reliability": 0.82, "capabilities": ["trending", "tech"], "tags": ["keyless"]},

    # ── NEWS ─────────────────────────────────────────────────────────────────
    {"id": "bbc_tech_rss",       "name": "BBC Technology RSS",    "category": "news",
     "type": "rss",  "endpoint": "https://feeds.bbci.co.uk/news/technology/rss.xml",
     "priority": 10, "reliability": 0.95, "capabilities": ["news", "tech"], "tags": ["keyless"]},
    {"id": "bbc_science_rss",    "name": "BBC Science RSS",       "category": "news",
     "type": "rss",  "endpoint": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
     "priority": 10, "reliability": 0.95, "capabilities": ["news", "science"], "tags": ["keyless"]},
    {"id": "reuters_rss",        "name": "Reuters Top News RSS",  "category": "news",
     "type": "rss",  "endpoint": "https://feeds.reuters.com/reuters/topNews",
     "priority": 12, "reliability": 0.90, "capabilities": ["news"], "tags": ["keyless"]},
    {"id": "techcrunch_rss",     "name": "TechCrunch RSS",        "category": "news",
     "type": "rss",  "endpoint": "https://techcrunch.com/feed/",
     "priority": 15, "reliability": 0.88, "capabilities": ["news", "tech"], "tags": ["keyless"]},
    {"id": "ars_technica_rss",   "name": "Ars Technica RSS",      "category": "news",
     "type": "rss",  "endpoint": "https://feeds.arstechnica.com/arstechnica/index",
     "priority": 16, "reliability": 0.87, "capabilities": ["news", "tech", "science"], "tags": ["keyless"]},
    {"id": "wired_rss",          "name": "Wired RSS",             "category": "news",
     "type": "rss",  "endpoint": "https://www.wired.com/feed/rss",
     "priority": 17, "reliability": 0.85, "capabilities": ["news", "tech"], "tags": ["keyless"]},
    {"id": "mit_tech_rss",       "name": "MIT Tech Review RSS",   "category": "news",
     "type": "rss",  "endpoint": "https://www.technologyreview.com/feed/",
     "priority": 18, "reliability": 0.85, "capabilities": ["news", "tech", "science"], "tags": ["keyless"]},
    {"id": "guardian_rss",       "name": "Guardian Tech RSS",     "category": "news",
     "type": "rss",  "endpoint": "https://www.theguardian.com/uk/technology/rss",
     "priority": 20, "reliability": 0.87, "capabilities": ["news", "tech"], "tags": ["keyless"]},
    {"id": "devto_rss",          "name": "Dev.to RSS",            "category": "news",
     "type": "rss",  "endpoint": "https://dev.to/feed",
     "priority": 35, "reliability": 0.80, "capabilities": ["news", "tech"], "tags": ["keyless"]},
    {"id": "who_rss",            "name": "WHO News RSS",          "category": "news",
     "type": "rss",  "endpoint": "https://www.who.int/rss-feeds/news-english.xml",
     "priority": 22, "reliability": 0.85, "capabilities": ["news", "health"], "tags": ["keyless"]},
    {"id": "newsapi",            "name": "NewsAPI",               "category": "news",
     "type": "api",  "endpoint": "https://newsapi.org/v2/top-headlines",
     "key_service": "newsapi",
     "priority": 8,  "reliability": 0.90, "capabilities": ["news"], "tags": ["paid"]},
    {"id": "guardian_api",       "name": "Guardian API",          "category": "news",
     "type": "api",  "endpoint": "https://content.guardianapis.com/search",
     "key_service": "guardian",
     "priority": 9,  "reliability": 0.90, "capabilities": ["news"], "tags": ["free-tier"]},
    {"id": "gnews_rss",          "name": "Google News RSS",       "category": "news",
     "type": "rss",  "endpoint": "https://news.google.com/rss",
     "priority": 14, "reliability": 0.88, "capabilities": ["news"], "tags": ["keyless"]},

    # ── FACTS ─────────────────────────────────────────────────────────────────
    {"id": "arxiv_api",          "name": "arXiv API",             "category": "facts",
     "type": "api",  "endpoint": "https://export.arxiv.org/api/query",
     "priority": 10, "reliability": 0.90, "capabilities": ["science", "facts"], "tags": ["keyless"]},
    {"id": "pubmed",             "name": "PubMed NCBI API",       "category": "facts",
     "type": "api",  "endpoint": "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
     "priority": 12, "reliability": 0.88, "capabilities": ["health", "science"], "tags": ["keyless"]},
    {"id": "nasa_api",           "name": "NASA Open APIs",        "category": "facts",
     "type": "api",  "endpoint": "https://api.nasa.gov",
     "key_service": "nasa",  # has demo key
     "priority": 15, "reliability": 0.85, "capabilities": ["science", "facts"], "tags": ["demo-key"]},
    {"id": "fred_api",           "name": "FRED Economic Data",    "category": "facts",
     "type": "api",  "endpoint": "https://api.stlouisfed.org/fred/releases",
     "key_service": "fred",
     "priority": 20, "reliability": 0.85, "capabilities": ["finance", "economics"], "tags": ["free-tier"]},
    {"id": "worldbank_api",      "name": "World Bank Open Data",  "category": "facts",
     "type": "api",  "endpoint": "https://api.worldbank.org/v2/country/all/indicator/NY.GDP.MKTP.CD?format=json&mrv=1",
     "priority": 25, "reliability": 0.82, "capabilities": ["finance", "facts"], "tags": ["keyless"]},
    {"id": "owid_rss",           "name": "Our World in Data RSS", "category": "facts",
     "type": "rss",  "endpoint": "https://ourworldindata.org/atom.xml",
     "priority": 18, "reliability": 0.85, "capabilities": ["science", "facts"], "tags": ["keyless"]},
    {"id": "wikipedia_events",   "name": "Wikipedia Events API",  "category": "facts",
     "type": "api",  "endpoint": "https://en.wikipedia.org/w/api.php",
     "priority": 22, "reliability": 0.88, "capabilities": ["facts"], "tags": ["keyless"]},

    # ── IMAGES ────────────────────────────────────────────────────────────────
    {"id": "pexels_images",      "name": "Pexels Images",         "category": "images",
     "type": "api",  "endpoint": "https://api.pexels.com/v1/search",
     "key_service": "pexels",
     "priority": 5,  "reliability": 0.92, "capabilities": ["images"], "tags": ["free-tier"]},
    {"id": "pixabay_images",     "name": "Pixabay Images",        "category": "images",
     "type": "api",  "endpoint": "https://pixabay.com/api/",
     "key_service": "pixabay",
     "priority": 8,  "reliability": 0.90, "capabilities": ["images"], "tags": ["free-tier"]},
    {"id": "unsplash_images",    "name": "Unsplash Images",       "category": "images",
     "type": "api",  "endpoint": "https://api.unsplash.com/photos/random",
     "key_service": "unsplash",
     "priority": 6,  "reliability": 0.90, "capabilities": ["images"], "tags": ["free-tier"]},
    {"id": "nasa_apod",          "name": "NASA APOD",             "category": "images",
     "type": "api",  "endpoint": "https://api.nasa.gov/planetary/apod?api_key=DEMO_KEY",
     "priority": 15, "reliability": 0.85, "capabilities": ["images", "science"], "tags": ["demo-key"]},
    {"id": "wikimedia_images",   "name": "Wikimedia Commons",     "category": "images",
     "type": "api",  "endpoint": "https://commons.wikimedia.org/w/api.php",
     "priority": 20, "reliability": 0.82, "capabilities": ["images"], "tags": ["keyless"]},
    {"id": "flickr_public",      "name": "Flickr Public Photos",  "category": "images",
     "type": "api",  "endpoint": "https://api.flickr.com/services/rest/",
     "key_service": "flickr",
     "priority": 18, "reliability": 0.80, "capabilities": ["images"], "tags": ["free-tier"]},
    {"id": "lorem_picsum",       "name": "Lorem Picsum (fallback)","category": "images",
     "type": "api",  "endpoint": "https://picsum.photos/1920/1080",
     "priority": 90, "reliability": 0.95, "capabilities": ["images"], "tags": ["keyless", "fallback"]},

    # ── VIDEO ─────────────────────────────────────────────────────────────────
    {"id": "runway_video",       "name": "Runway ML Gen-3",       "category": "video",
     "type": "api",  "endpoint": "https://api.dev.runwayml.com/v1",
     "key_service": "runway",
     "priority": 1,  "reliability": 0.80, "capabilities": ["ai-video"], "tags": ["paid"]},
    {"id": "replicate_sdxl",     "name": "Replicate SDXL",        "category": "video",
     "type": "api",  "endpoint": "https://api.replicate.com/v1/predictions",
     "key_service": "replicate",
     "priority": 3,  "reliability": 0.85, "capabilities": ["ai-image", "animate"], "tags": ["paid"]},
    {"id": "pexels_video",       "name": "Pexels Video",          "category": "video",
     "type": "api",  "endpoint": "https://api.pexels.com/videos/search",
     "key_service": "pexels",
     "priority": 5,  "reliability": 0.88, "capabilities": ["stock-video"], "tags": ["free-tier"]},
    {"id": "pixabay_video",      "name": "Pixabay Video",         "category": "video",
     "type": "api",  "endpoint": "https://pixabay.com/api/videos/",
     "key_service": "pixabay",
     "priority": 8,  "reliability": 0.85, "capabilities": ["stock-video"], "tags": ["free-tier"]},
    {"id": "coverr_rss",         "name": "Coverr Free Videos",    "category": "video",
     "type": "api",  "endpoint": "https://coverr.co/s?query=",
     "priority": 15, "reliability": 0.75, "capabilities": ["stock-video"], "tags": ["keyless"]},
    {"id": "pil_generated",      "name": "PIL Generated Frames",  "category": "video",
     "type": "local","endpoint": "",
     "priority": 99, "reliability": 1.00, "capabilities": ["generated"], "tags": ["keyless", "fallback"]},

    # ── TTS ───────────────────────────────────────────────────────────────────
    {"id": "elevenlabs_tts",     "name": "ElevenLabs TTS",        "category": "audio_tts",
     "type": "api",  "endpoint": "https://api.elevenlabs.io/v1/text-to-speech",
     "key_service": "elevenlabs",
     "priority": 1,  "reliability": 0.90, "capabilities": ["tts", "natural"], "tags": ["paid"]},
    {"id": "openai_tts",         "name": "OpenAI TTS",            "category": "audio_tts",
     "type": "api",  "endpoint": "https://api.openai.com/v1/audio/speech",
     "key_service": "openai",
     "priority": 2,  "reliability": 0.92, "capabilities": ["tts", "natural"], "tags": ["paid"]},
    {"id": "azure_tts",          "name": "Azure Neural TTS",      "category": "audio_tts",
     "type": "api",  "endpoint": "https://eastus.tts.speech.microsoft.com",
     "key_service": "azure_speech",
     "priority": 3,  "reliability": 0.88, "capabilities": ["tts", "natural"], "tags": ["free-tier"]},
    {"id": "edge_tts",           "name": "Edge TTS (free)",       "category": "audio_tts",
     "type": "local","endpoint": "",
     "priority": 10, "reliability": 0.80, "capabilities": ["tts"], "tags": ["keyless"]},
    {"id": "gtts",               "name": "gTTS (Google)",         "category": "audio_tts",
     "type": "local","endpoint": "",
     "priority": 15, "reliability": 0.75, "capabilities": ["tts"], "tags": ["keyless"]},
    {"id": "espeak_tts",         "name": "eSpeak-NG (local)",     "category": "audio_tts",
     "type": "local","endpoint": "",
     "priority": 20, "reliability": 0.99, "capabilities": ["tts"], "tags": ["keyless", "fallback"]},

    # ── MUSIC ─────────────────────────────────────────────────────────────────
    {"id": "pixabay_music",      "name": "Pixabay Music",         "category": "music",
     "type": "api",  "endpoint": "https://pixabay.com/api/music/",
     "key_service": "pixabay",
     "priority": 5,  "reliability": 0.85, "capabilities": ["music", "royalty-free"], "tags": ["free-tier"]},
    {"id": "freepd_rss",         "name": "FreePD (CC0 Music)",    "category": "music",
     "type": "scrape","endpoint": "https://freepd.com/",
     "priority": 15, "reliability": 0.70, "capabilities": ["music", "royalty-free"], "tags": ["keyless"]},
    {"id": "ccmixter_rss",       "name": "ccMixter CC Music",     "category": "music",
     "type": "rss",  "endpoint": "https://ccmixter.org/api/query?f=rss&type=all&tags=ambient&limit=10",
     "priority": 18, "reliability": 0.72, "capabilities": ["music", "royalty-free"], "tags": ["keyless"]},
    {"id": "procedural_music",   "name": "Procedural Synthesis",  "category": "music",
     "type": "local","endpoint": "",
     "priority": 99, "reliability": 1.00, "capabilities": ["music"], "tags": ["keyless", "fallback"]},
]


# Module-level singleton
_registry: Optional[SourceRegistry] = None


def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        _registry = SourceRegistry()
    return _registry
