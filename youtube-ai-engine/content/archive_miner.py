"""
content/archive_miner.py — Archive & Historical Content Engine
==============================================================
Mines free public archives (Internet Archive, Project Gutenberg,
Library of Congress, Wikipedia, DPLA) to find forgotten primary
sources, historical parallels to today's events, and "100-year-old
wisdom" angles that no competitor is using.

Pipeline:
  ArchiveFetcher → HistoricalParallelFinder → ContentAngleBuilder
  all orchestrated by ArchiveMinerEngine
"""
import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.archive_miner")

HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Historical parallels: modern topics → past events
# ---------------------------------------------------------------------------

CURRENT_PARALLELS: Dict[str, List[Dict]] = {
    "inflation": [
        {"era": "1970s", "event": "Nixon Shock and Great Inflation", "year": 1973,
         "lesson": "Wage-price controls backfired spectacularly"},
        {"era": "Weimar Republic", "event": "German hyperinflation", "year": 1923,
         "lesson": "Printing money destroyed the middle class overnight"},
        {"era": "Post-WWI", "event": "Austrian economic collapse", "year": 1921,
         "lesson": "Gold reserves matter more than promises"},
    ],
    "AI": [
        {"era": "Industrial Revolution", "event": "Luddite movement", "year": 1811,
         "lesson": "Workers destroyed machines to protect jobs — and lost"},
        {"era": "1950s", "event": "Automation fear and the 'useless class'", "year": 1956,
         "lesson": "Every automation wave created more jobs than it destroyed"},
        {"era": "1890s", "event": "Telegraph disrupting journalism", "year": 1890,
         "lesson": "Speed of information changed power structures"},
    ],
    "crypto": [
        {"era": "1600s", "event": "Dutch Tulip Mania", "year": 1637,
         "lesson": "Speculative bubbles follow the same psychology every time"},
        {"era": "1920s", "event": "Florida land boom and bust", "year": 1926,
         "lesson": "New assets attract fraud before regulations catch up"},
        {"era": "1850s", "event": "California Gold Rush banking chaos", "year": 1849,
         "lesson": "The infrastructure companies (picks/shovels) won, not miners"},
    ],
    "housing crisis": [
        {"era": "2008", "event": "Global Financial Crisis", "year": 2008,
         "lesson": "Securitization hid risk until it was too late"},
        {"era": "1989", "event": "Japanese asset bubble collapse", "year": 1989,
         "lesson": "Decades of stagnation followed the crash"},
        {"era": "1930s", "event": "Great Depression bank failures", "year": 1933,
         "lesson": "Deposit guarantees stopped bank runs cold"},
    ],
    "social media": [
        {"era": "1920s", "event": "Radio panic and moral panic", "year": 1925,
         "lesson": "Every new broadcast medium triggered the same fears"},
        {"era": "1800s", "event": "Penny press yellow journalism", "year": 1835,
         "lesson": "Cheap mass media always sensationalizes for profit"},
        {"era": "1960s", "event": "TV's influence on elections", "year": 1960,
         "lesson": "Nixon lost because of how he looked on camera"},
    ],
    "pandemic": [
        {"era": "1918", "event": "Spanish Flu and mask mandates", "year": 1918,
         "lesson": "Cities that locked down longer recovered faster"},
        {"era": "1300s", "event": "Black Death economic aftermath", "year": 1350,
         "lesson": "Labor scarcity after plague raised wages for survivors"},
        {"era": "1793", "event": "Yellow fever in Philadelphia", "year": 1793,
         "lesson": "Disease shaped which cities became dominant ports"},
    ],
    "remote work": [
        {"era": "Industrial Revolution", "event": "Cottage industry to factory transition", "year": 1800,
         "lesson": "Factories weren't inevitable — they were a deliberate power grab"},
        {"era": "1970s", "event": "Telecommuting experiments", "year": 1973,
         "lesson": "Oil crisis sparked the first remote work movement"},
    ],
    "climate change": [
        {"era": "1930s", "event": "Dust Bowl and the Great Plains disaster", "year": 1934,
         "lesson": "Industrial farming destroyed an ecosystem in 10 years"},
        {"era": "Victorian", "event": "London Great Smog", "year": 1952,
         "lesson": "4,000 dead in 4 days forced the world's first air quality laws"},
        {"era": "1960s", "event": "Cuyahoga River catching fire", "year": 1969,
         "lesson": "Rivers catching fire sparked the EPA and Clean Water Act"},
    ],
    "geopolitics": [
        {"era": "1930s", "event": "Appeasement of Hitler's early moves", "year": 1936,
         "lesson": "Ceding territory invited more aggression"},
        {"era": "Cold War", "event": "Cuban Missile Crisis negotiation", "year": 1962,
         "lesson": "Back-channel diplomacy saved the world"},
        {"era": "1800s", "event": "Concert of Europe balance of power", "year": 1815,
         "lesson": "Great power conferences prevented large wars for 99 years"},
    ],
    "education": [
        {"era": "Progressive Era", "event": "One-room schoolhouse to grade levels", "year": 1890,
         "lesson": "Age-based grades were invented for industrial efficiency, not learning"},
        {"era": "1970s", "event": "Open classroom experiment", "year": 1971,
         "lesson": "Progressive education experiments mostly failed without structure"},
    ],
}

ARCHIVE_SOURCES = {
    "internet_archive": {
        "search_url": "https://archive.org/advancedsearch.php",
        "base_url":   "https://archive.org",
        "description": "Wayback Machine + digitized books, movies, audio",
    },
    "gutenberg": {
        "search_url": "https://www.gutenberg.org/ebooks/search/",
        "base_url":   "https://www.gutenberg.org",
        "description": "60,000 free public domain books",
    },
    "loc_chronicling": {
        "search_url": "https://chroniclingamerica.loc.gov/search/pages/results/",
        "base_url":   "https://chroniclingamerica.loc.gov",
        "description": "Historic US newspapers 1770–1963",
    },
    "wikipedia": {
        "api_url":    "https://en.wikipedia.org/api/rest_v1",
        "search_url": "https://en.wikipedia.org/w/api.php",
        "description": "Wikipedia articles and summaries",
    },
    "dpla": {
        "search_url": "https://api.dp.la/v2/items",
        "description": "Digital Public Library of America",
    },
}

CONTENT_ANGLE_TEMPLATES = [
    "{age} Years Ago, Someone Predicted Exactly What's Happening Now",
    "The {year} Document That Explains Today's {topic} Crisis",
    "History Repeats: How {past_event} Is Happening Again Right Now",
    "What {historical_figure} Got Completely Right About {modern_topic}",
    "The Warning From {year} That Everyone Ignored",
    "This {age}-Year-Old Strategy Still Works Better Than Anything New",
    "Before {modern_thing} Existed, People Used This Instead",
    "The {year} Crash That Nobody Learned From (Until Now)",
    "Why {modern_event} Is Exactly Like {past_event} — With One Key Difference",
    "The Forgotten {era} Solution To Today's {topic} Problem",
    "What Happened Last Time The World Tried This",
    "The {age}-Year Cycle That Explains Everything Happening In {topic}",
]

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; YouTubeAIEngine/1.0; "
        "+https://github.com/energy-intelligence)"
    )
}
_REQUEST_TIMEOUT = 12


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _fetch_json(url: str, params: Dict = None, retries: int = 2) -> Optional[Dict]:
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                return json.loads(raw)
        except Exception as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                log.debug("fetch_json failed %s: %s", url[:80], exc)
    return None


def _fetch_text(url: str, retries: int = 2) -> Optional[str]:
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=REQUEST_HEADERS)
            with urllib.request.urlopen(req, timeout=_REQUEST_TIMEOUT) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            if attempt < retries:
                time.sleep(2 ** attempt)
            else:
                log.debug("fetch_text failed %s: %s", url[:80], exc)
    return None


# ---------------------------------------------------------------------------
# ArchiveFetcher
# ---------------------------------------------------------------------------

class ArchiveFetcher:
    """Queries public archive APIs and returns structured result dicts."""

    # ── Internet Archive ──────────────────────────────────────────────────

    def search_internet_archive(
        self, query: str, media_type: str = "texts", n: int = 10
    ) -> List[Dict]:
        params = {
            "q":          f"({query}) AND mediatype:{media_type}",
            "output":     "json",
            "rows":       n,
            "fl[]":       "identifier,title,creator,date,description,subject",
            "sort[]":     "downloads desc",
            "page":       1,
        }
        data = _fetch_json(ARCHIVE_SOURCES["internet_archive"]["search_url"], params)
        if not data:
            return []
        items = []
        for doc in data.get("response", {}).get("docs", []):
            identifier = doc.get("identifier", "")
            items.append({
                "source":      "internet_archive",
                "title":       self._safe_str(doc.get("title")),
                "creator":     self._safe_str(doc.get("creator")),
                "date":        self._safe_str(doc.get("date")),
                "description": self._safe_str(doc.get("description"))[:300],
                "subject":     self._safe_str(doc.get("subject")),
                "url":         f"https://archive.org/details/{identifier}",
                "identifier":  identifier,
                "query":       query,
            })
        return items

    def search_gutenberg(self, query: str, n: int = 10) -> List[Dict]:
        params = {"query": query, "format": "json"}
        url = "https://gutendex.com/books/"
        data = _fetch_json(url, params)
        if not data:
            return []
        items = []
        for book in data.get("results", [])[:n]:
            authors = [a.get("name", "") for a in book.get("authors", [])]
            formats = book.get("formats", {})
            text_url = (
                formats.get("text/plain; charset=utf-8")
                or formats.get("text/plain")
                or ""
            )
            items.append({
                "source":    "gutenberg",
                "title":     book.get("title", ""),
                "creator":   ", ".join(authors),
                "date":      "",
                "subjects":  ", ".join(book.get("subjects", [])[:5]),
                "url":       f"https://www.gutenberg.org/ebooks/{book.get('id','')}",
                "text_url":  text_url,
                "downloads": book.get("download_count", 0),
                "query":     query,
            })
        return items

    def search_loc_newspapers(self, query: str, n: int = 10) -> List[Dict]:
        params = {
            "andtext": query,
            "format":  "json",
            "rows":    n,
        }
        data = _fetch_json(
            ARCHIVE_SOURCES["loc_chronicling"]["search_url"], params
        )
        if not data:
            return []
        items = []
        for item in data.get("items", [])[:n]:
            items.append({
                "source":      "loc_chronicling",
                "title":       item.get("title_normal", item.get("title", "")),
                "creator":     item.get("place_of_publication", ""),
                "date":        item.get("date", ""),
                "description": item.get("ocr_eng", "")[:300],
                "url":         "https://chroniclingamerica.loc.gov" + item.get("id", ""),
                "query":       query,
            })
        return items

    def search_wikipedia(self, query: str, n: int = 5) -> List[Dict]:
        params = {
            "action":  "query",
            "list":    "search",
            "srsearch": query,
            "srlimit": n,
            "format":  "json",
            "utf8":    1,
        }
        data = _fetch_json(
            ARCHIVE_SOURCES["wikipedia"]["search_url"], params
        )
        if not data:
            return []
        items = []
        for result in data.get("query", {}).get("search", []):
            page_id   = result.get("pageid", "")
            title     = result.get("title", "")
            snippet   = re.sub(r"<[^>]+>", "", result.get("snippet", ""))
            items.append({
                "source":      "wikipedia",
                "title":       title,
                "creator":     "Wikipedia",
                "date":        "",
                "description": snippet,
                "url":         f"https://en.wikipedia.org/?curid={page_id}",
                "query":       query,
            })
        return items

    def get_wikipedia_summary(self, title: str) -> Optional[str]:
        encoded = urllib.parse.quote(title.replace(" ", "_"))
        url     = f"{ARCHIVE_SOURCES['wikipedia']['api_url']}/page/summary/{encoded}"
        data    = _fetch_json(url)
        return data.get("extract", "") if data else None

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _safe_str(val) -> str:
        if val is None:
            return ""
        if isinstance(val, list):
            return ", ".join(str(v) for v in val[:3])
        return str(val)


# ---------------------------------------------------------------------------
# HistoricalParallelFinder
# ---------------------------------------------------------------------------

class HistoricalParallelFinder:
    """Matches modern topics to pre-configured historical parallels and
    generates video content angles from those parallels."""

    def find_parallels(self, topic: str) -> List[Dict]:
        topic_lower = topic.lower()
        results     = []
        for key, parallels in CURRENT_PARALLELS.items():
            key_lower = key.lower()
            if key_lower in topic_lower or topic_lower in key_lower:
                for p in parallels:
                    results.append({**p, "matched_key": key})
                continue
            # fuzzy: any keyword word matches
            key_words   = set(key_lower.replace("_", " ").split())
            topic_words = set(topic_lower.split())
            if key_words & topic_words:
                for p in parallels:
                    results.append({**p, "matched_key": key})
        # deduplicate by year+event
        seen   = set()
        unique = []
        for r in results:
            k = (r["year"], r["event"][:30])
            if k not in seen:
                seen.add(k)
                unique.append(r)
        return unique

    def build_parallel_angle(
        self, topic: str, parallel: Dict, template_idx: int = 0
    ) -> Dict:
        current_year = datetime.now().year
        age          = current_year - parallel.get("year", current_year)
        template     = CONTENT_ANGLE_TEMPLATES[
            template_idx % len(CONTENT_ANGLE_TEMPLATES)
        ]
        title = (
            template
            .replace("{age}", str(age))
            .replace("{year}", str(parallel.get("year", "")))
            .replace("{topic}", topic)
            .replace("{past_event}", parallel.get("event", ""))
            .replace("{modern_topic}", topic)
            .replace("{modern_event}", topic)
            .replace("{historical_figure}", parallel.get("event", "").split()[0])
            .replace("{modern_thing}", topic)
            .replace("{era}", parallel.get("era", ""))
        )
        return {
            "title":        title,
            "topic":        topic,
            "parallel":     parallel,
            "age_years":    age,
            "content_type": "historical_parallel",
            "hook":         f"In {parallel['year']}, {parallel['event']}. Sound familiar?",
            "lesson":       parallel.get("lesson", ""),
            "angle":        "historical_parallel",
            "era":          parallel.get("era", ""),
            "video_score":  self._score_parallel(parallel, age),
        }

    def _score_parallel(self, parallel: Dict, age_years: int) -> float:
        score = 50.0
        # Sweet spot: 30–100 year old events (living memory + documented)
        if 30 <= age_years <= 100:
            score += 20
        elif age_years > 100:
            score += 10
        # Rich lesson = better
        lesson = parallel.get("lesson", "")
        if len(lesson) > 50:
            score += 10
        # Emotional keywords
        hot_words = {"destroyed", "killed", "crashed", "saved", "changed", "shocked"}
        if any(w in lesson.lower() for w in hot_words):
            score += 10
        return min(100.0, score)


# ---------------------------------------------------------------------------
# ContentAngleBuilder
# ---------------------------------------------------------------------------

class ContentAngleBuilder:
    """Takes archive results and historical parallels and builds production-
    ready video angle objects with title, hook, outline, and score."""

    def build_from_archive_item(
        self, item: Dict, current_topic: str = ""
    ) -> Optional[Dict]:
        title       = item.get("title", "")
        description = item.get("description", "")
        date_str    = item.get("date", "")
        creator     = item.get("creator", "")

        year = self._extract_year(date_str)
        if not year:
            # Try to infer from title
            year = self._extract_year(title)
        age = (datetime.now().year - year) if year else None

        if not title:
            return None

        # Score the item
        score = self._score_archive_item(item, age)
        if score < 30:
            return None

        # Select the best template
        template_idx = hash(title) % len(CONTENT_ANGLE_TEMPLATES)
        template     = CONTENT_ANGLE_TEMPLATES[template_idx]

        angle_title = (
            template
            .replace("{age}", str(age) if age else "Decades")
            .replace("{year}", str(year) if year else "")
            .replace("{topic}", current_topic or title[:30])
            .replace("{past_event}", title[:40])
            .replace("{modern_topic}", current_topic or "Today's World")
            .replace("{modern_event}", current_topic or "Current Events")
            .replace("{historical_figure}", creator.split(",")[0][:20] if creator else "Someone")
            .replace("{modern_thing}", current_topic or "Modern Life")
            .replace("{era}", self._year_to_era(year) if year else "the Past")
        )

        hook = (
            f"I found a {age}-year-old document that explains exactly "
            f"what's happening right now with {current_topic or title[:30]}."
            if age
            else f"This archive document from {item.get('source','the past')} "
                 f"reveals something most people don't know."
        )

        return {
            "title":        angle_title,
            "hook":         hook,
            "source_item":  item,
            "year":         year,
            "age_years":    age,
            "creator":      creator,
            "description":  description,
            "url":          item.get("url", ""),
            "content_type": "archive_discovery",
            "angle":        "archive_primary_source",
            "video_score":  score,
            "outline": [
                f"Hook: {hook}",
                f"Context: What was happening in {year or 'that era'}",
                f"The discovery: What this document actually says",
                f"Modern parallel: How this applies to {current_topic or 'today'}",
                f"Lesson: What we can learn and what to do differently",
            ],
        }

    def _score_archive_item(self, item: Dict, age: Optional[int]) -> float:
        score = 40.0
        title       = item.get("title", "").lower()
        description = item.get("description", "").lower()
        text        = title + " " + description

        # Age bonus
        if age and 20 <= age <= 120:
            score += 15
        elif age and age > 120:
            score += 10

        # High-value keywords
        premium_words = {
            "crisis", "revolution", "war", "panic", "discovery", "secret",
            "warning", "collapse", "miracle", "scandal", "prediction",
        }
        score += sum(3 for w in premium_words if w in text)

        # Gutenberg books score well (lots of content)
        if item.get("source") == "gutenberg":
            score += 5
        if item.get("downloads", 0) > 1000:
            score += 10

        return min(100.0, score)

    @staticmethod
    def _extract_year(text: str) -> Optional[int]:
        if not text:
            return None
        m = re.search(r"\b(1[0-9]{3}|20[0-2][0-9])\b", str(text))
        if m:
            return int(m.group(1))
        return None

    @staticmethod
    def _year_to_era(year: int) -> str:
        if year < 1800:
            return "Colonial Era"
        elif year < 1860:
            return "Antebellum Era"
        elif year < 1900:
            return "Gilded Age"
        elif year < 1920:
            return "Progressive Era"
        elif year < 1940:
            return "Interwar Period"
        elif year < 1960:
            return "Post-War Era"
        elif year < 1980:
            return "Cold War Era"
        elif year < 2000:
            return "Late 20th Century"
        else:
            return "Modern Era"


# ---------------------------------------------------------------------------
# ArchiveMinerEngine — orchestrator
# ---------------------------------------------------------------------------

class ArchiveMinerEngine:
    """Orchestrates archive mining: fetches sources, finds historical parallels,
    builds video angles, and persists to memory."""

    def __init__(self, memory=None):
        self.memory   = memory
        self.fetcher  = ArchiveFetcher()
        self.parallel = HistoricalParallelFinder()
        self.builder  = ContentAngleBuilder()

    # ── Public API ────────────────────────────────────────────────────────

    def mine_for_topic(
        self, topic: str, sources: List[str] = None, n_per_source: int = 5
    ) -> Dict:
        """Mine all archive sources for content angles on a given topic."""
        sources = sources or list(ARCHIVE_SOURCES.keys())
        all_items:  List[Dict] = []
        all_angles: List[Dict] = []

        # 1. Fetch from archives
        for src in sources:
            try:
                items = self._fetch_from_source(src, topic, n_per_source)
                all_items.extend(items)
            except Exception as exc:
                log.warning("Archive source %s failed for %r: %s", src, topic, exc)

        # 2. Build angles from archive items
        for item in all_items:
            angle = self.builder.build_from_archive_item(item, topic)
            if angle:
                all_angles.append(angle)

        # 3. Find historical parallels
        parallels = self.parallel.find_parallels(topic)
        for i, p in enumerate(parallels):
            angle = self.parallel.build_parallel_angle(topic, p, i)
            all_angles.append(angle)

        # 4. Sort by score
        all_angles.sort(key=lambda x: x.get("video_score", 0), reverse=True)

        # 5. Save to memory
        saved = 0
        for angle in all_angles[:20]:
            try:
                if self.memory and hasattr(self.memory, "save_archive_item"):
                    self.memory.save_archive_item({
                        "topic":        topic,
                        "title":        angle.get("title", ""),
                        "source":       angle.get("source_item", {}).get("source", "historical_parallel"),
                        "url":          angle.get("url", ""),
                        "year":         angle.get("year"),
                        "age_years":    angle.get("age_years"),
                        "content_type": angle.get("content_type", ""),
                        "angle":        angle.get("angle", ""),
                        "hook":         angle.get("hook", ""),
                        "lesson":       angle.get("lesson", ""),
                        "video_score":  angle.get("video_score", 0),
                        "metadata":     json.dumps({
                            "outline": angle.get("outline", []),
                            "parallel": angle.get("parallel", {}),
                        }),
                    })
                    saved += 1
            except Exception as exc:
                log.warning("Failed to save archive item: %s", exc)

        return {
            "topic":        topic,
            "total_fetched": len(all_items),
            "total_angles":  len(all_angles),
            "top_angles":    all_angles[:10],
            "parallels":     parallels,
            "saved":         saved,
        }

    def run_daily_mining(
        self, topics: List[str] = None, n_per_topic: int = 5
    ) -> Dict:
        """Daily job: mine archives for a list of topics (or auto-detect from memory)."""
        if not topics:
            topics = self._get_recent_topics()
        if not topics:
            topics = list(CURRENT_PARALLELS.keys())[:5]

        results   = []
        total_new = 0
        for topic in topics[:10]:
            try:
                r = self.mine_for_topic(topic, n_per_source=n_per_topic)
                results.append(r)
                total_new += r.get("saved", 0)
                time.sleep(1)  # be polite to public APIs
            except Exception as exc:
                log.error("Daily mining failed for %r: %s", topic, exc)

        return {
            "topics_mined": len(results),
            "total_angles_saved": total_new,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": [
                {
                    "topic":   r["topic"],
                    "angles":  r["total_angles"],
                    "top_title": r["top_angles"][0]["title"] if r["top_angles"] else "none",
                }
                for r in results
            ],
        }

    def get_best_angles(self, n: int = 10) -> List[Dict]:
        """Return top-scored archive angles from memory."""
        if self.memory and hasattr(self.memory, "get_archive_items"):
            return self.memory.get_archive_items(n=n)
        return []

    def enrich_with_wikipedia(self, topic: str) -> Optional[str]:
        """Fetch a Wikipedia summary for extra context."""
        return self.fetcher.get_wikipedia_summary(topic)

    # ── Private helpers ───────────────────────────────────────────────────

    def _fetch_from_source(
        self, source: str, query: str, n: int
    ) -> List[Dict]:
        if source == "internet_archive":
            return self.fetcher.search_internet_archive(query, n=n)
        elif source == "gutenberg":
            return self.fetcher.search_gutenberg(query, n=n)
        elif source == "loc_chronicling":
            return self.fetcher.search_loc_newspapers(query, n=n)
        elif source == "wikipedia":
            return self.fetcher.search_wikipedia(query, n=n)
        elif source == "dpla":
            return []  # DPLA requires an API key; skip gracefully
        return []

    def _get_recent_topics(self) -> List[str]:
        if not self.memory:
            return []
        try:
            if hasattr(self.memory, "get_recent_topics"):
                return self.memory.get_recent_topics(days=7)
        except Exception:
            pass
        return []
