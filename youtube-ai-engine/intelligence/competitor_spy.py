"""
intelligence/competitor_spy.py — Channel Intelligence Scanner
==============================================================
Tracks top YouTube channels in the niche daily.  Extracts:
  - Upload frequency & video length patterns
  - Title formulas (patterns that recur)
  - Thumbnail style observations
  - Hook structure analysis
  - View velocity (views in first 24h)

Builds a "winning formula database" that the script_writer uses to
ensure every video scores above competitor average.
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("brain.competitor_spy")

HERE = Path(__file__).parent.parent

# Default competitor channels (YouTube channel IDs, all public data)
DEFAULT_COMPETITORS = [
    "UCsvn_Po0SmunchJYOWpOxMg",   # Veritasium
    "UCHnyfMqiRRG1u-2MsSQLbXA",   # Vsauce
    "UCWX3yGbOcA4lMXHoAjJixAQ",   # What If
    "UCpCSAcbqs-sjEVfk_hMfY9w",   # Zach Star
    "UC9-y-6csu5WGm29I7JiwpnA",   # Computerphile
]


class CompetitorSpy:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        self._formula_db: Dict[str, Any] = {}

    # ── Data fetch ────────────────────────────────────────────────────────────

    def fetch_channel_videos(self, channel_id: str,
                              max_results: int = 20) -> List[Dict]:
        """Fetch recent videos from a channel via YouTube Data API v3."""
        api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not api_key:
            return self._rss_fallback(channel_id, max_results)

        try:
            import requests, ssl
            sess = requests.Session()
            sess.verify = False
            r = sess.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "part":       "snippet",
                    "channelId":  channel_id,
                    "order":      "date",
                    "type":       "video",
                    "maxResults": max_results,
                    "key":        api_key,
                },
                timeout=20,
            )
            if not r.ok:
                return []
            items = r.json().get("items", [])
            videos = []
            for it in items:
                sn = it.get("snippet", {})
                videos.append({
                    "id":          it["id"].get("videoId", ""),
                    "title":       sn.get("title", ""),
                    "description": sn.get("description", "")[:300],
                    "published":   sn.get("publishedAt", ""),
                    "channel_id":  channel_id,
                    "channel":     sn.get("channelTitle", ""),
                })
            return videos
        except Exception as e:
            log.debug(f"YouTube Data API failed for {channel_id}: {e}")
            return self._rss_fallback(channel_id, max_results)

    def _rss_fallback(self, channel_id: str, max_results: int) -> List[Dict]:
        """Fetch via YouTube channel RSS feed (no API key needed)."""
        try:
            import feedparser, ssl
            url  = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            feed = feedparser.parse(url)
            videos = []
            for entry in feed.entries[:max_results]:
                videos.append({
                    "id":      entry.get("yt_videoid", ""),
                    "title":   entry.get("title", ""),
                    "published": entry.get("published", ""),
                    "channel_id": channel_id,
                    "views":   0,
                })
            return videos
        except Exception as e:
            log.debug(f"RSS fallback failed for {channel_id}: {e}")
            return []

    def fetch_video_stats(self, video_id: str) -> Dict:
        """Get view/like counts for a specific video."""
        api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not api_key:
            return {}
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            r = sess.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics,contentDetails",
                        "id": video_id, "key": api_key},
                timeout=15,
            )
            if not r.ok:
                return {}
            items = r.json().get("items", [])
            if not items:
                return {}
            stats = items[0].get("statistics", {})
            cd    = items[0].get("contentDetails", {})
            dur   = self._parse_duration(cd.get("duration", "PT0S"))
            return {
                "views":    int(stats.get("viewCount", 0)),
                "likes":    int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
                "duration_s": dur,
            }
        except Exception as e:
            log.debug(f"Video stats failed for {video_id}: {e}")
            return {}

    @staticmethod
    def _parse_duration(iso: str) -> int:
        """ISO 8601 duration string → seconds."""
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso)
        if not m:
            return 0
        h, mn, s = (int(v or 0) for v in m.groups())
        return h * 3600 + mn * 60 + s

    # ── Pattern analysis ──────────────────────────────────────────────────────

    def extract_title_patterns(self, videos: List[Dict]) -> List[str]:
        """Find recurring title formulas from a list of videos."""
        titles = [v["title"] for v in videos if v.get("title")]
        patterns = []

        # Number patterns: "5 Things...", "Top 10...", "#1..."
        num_count = sum(1 for t in titles if re.search(r"\b\d+\b", t))
        if num_count > len(titles) * 0.4:
            patterns.append("Uses numbers prominently (5 Things / Top 10 style)")

        # Question patterns
        q_count = sum(1 for t in titles if t.endswith("?"))
        if q_count > len(titles) * 0.2:
            patterns.append("Frequently uses question hooks")

        # "How to" patterns
        howto = sum(1 for t in titles if t.lower().startswith("how to"))
        if howto > 1:
            patterns.append("Uses 'How to' structure frequently")

        # Word length
        avg_words = sum(len(t.split()) for t in titles) / max(len(titles), 1)
        patterns.append(f"Average title length: {avg_words:.1f} words")

        # Capitalisation style
        all_caps = sum(1 for t in titles if t.isupper())
        title_case = sum(1 for t in titles if t.istitle())
        if all_caps > len(titles) * 0.3:
            patterns.append("Prefers ALL CAPS titles")
        elif title_case > len(titles) * 0.5:
            patterns.append("Prefers Title Case")

        return patterns

    def analyse_channel(self, channel_id: str) -> Dict:
        """Full analysis of one competitor channel."""
        videos = self.fetch_channel_videos(channel_id, max_results=25)
        if not videos:
            return {}

        # Upload frequency
        dates = [v["published"] for v in videos if v.get("published")]
        freq  = len(dates)   # uploads in last N items
        patterns = self.extract_title_patterns(videos)

        # Average video length (needs API)
        durations = []
        for v in videos[:5]:   # limit API calls
            stats = self.fetch_video_stats(v["id"])
            if stats.get("duration_s"):
                durations.append(stats["duration_s"])
            time.sleep(0.3)

        avg_dur = sum(durations) / len(durations) if durations else 0

        return {
            "channel_id":    channel_id,
            "upload_freq":   freq,
            "title_patterns": patterns,
            "avg_duration_s": avg_dur,
            "recent_titles": [v["title"] for v in videos[:5]],
            "analysed_at":   datetime.now(timezone.utc).isoformat(),
        }

    # ── Formula database ──────────────────────────────────────────────────────

    def build_formula_db(self, channels: List[str] = None) -> Dict:
        """Analyse all competitors and build the winning formula database."""
        channels = channels or DEFAULT_COMPETITORS
        log.info(f"Competitor spy: analysing {len(channels)} channels")
        results = {}

        for cid in channels:
            log.info(f"  Analysing: {cid[:20]}…")
            results[cid] = self.analyse_channel(cid)
            time.sleep(1.0)

        # Aggregate patterns
        all_patterns: List[str] = []
        avg_lengths:  List[float] = []
        for r in results.values():
            all_patterns.extend(r.get("title_patterns", []))
            if r.get("avg_duration_s"):
                avg_lengths.append(r["avg_duration_s"])

        formula = {
            "channels_analysed":  len(results),
            "common_patterns":    list(set(all_patterns)),
            "avg_competitor_len": sum(avg_lengths) / len(avg_lengths) if avg_lengths else 600,
            "beat_target":        "score above 70% of competitor videos",
            "generated_at":       datetime.now(timezone.utc).isoformat(),
        }

        # Save to data/
        db_path = HERE / "data" / "competitor_db.json"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with open(db_path, "w") as f:
            json.dump({"channels": results, "formula": formula}, f, indent=2)

        log.info(f"  Competitor DB saved → {db_path}")
        self._formula_db = formula
        return formula

    def get_formula(self) -> Dict:
        if not self._formula_db:
            db_path = HERE / "data" / "competitor_db.json"
            if db_path.exists():
                with open(db_path) as f:
                    data = json.load(f)
                self._formula_db = data.get("formula", {})
        return self._formula_db

    def get_target_length(self) -> int:
        """Return recommended video length to beat competitor average."""
        formula = self.get_formula()
        avg = formula.get("avg_competitor_len", 600)
        # Target 10% longer for more watch time
        return int(avg * 1.1)
