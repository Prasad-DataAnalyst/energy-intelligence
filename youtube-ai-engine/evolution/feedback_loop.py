"""
evolution/feedback_loop.py — Analytics → Learning → Improvement
================================================================
After every video (at 24h / 72h / 7d / 28d marks):
  1. Pulls YouTube Analytics via Data API v3
  2. Compares this video vs channel and competitor averages
  3. Extracts what worked and what didn't
  4. Updates master_config.yaml, style_profiles.yaml, channel_persona.yaml
  5. Writes correlations to memory DB for the Brain to use

The COMPOUNDING IMPROVEMENT LOOP:
  Video → Analytics → Lesson → Config Update → Better Video → Repeat
"""
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

log = logging.getLogger("brain.feedback_loop")

HERE    = Path(__file__).parent.parent
CFG_DIR = HERE / "config"

# Analytics windows in hours
ANALYTICS_WINDOWS = [24, 72, 168, 672]   # 1d, 3d, 7d, 28d


class FeedbackLoop:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    # ── YouTube Analytics pull ─────────────────────────────────────────────────

    def pull_analytics(self, youtube_video_id: str,
                        days_back: int = 7) -> Optional[Dict]:
        """
        Pull view/CTR/AVD stats from YouTube Analytics API.
        Falls back to YouTube Data API v3 public stats if Analytics unavailable.
        """
        # Try Analytics API first (requires OAuth with analytics.readonly scope)
        analytics = self._pull_analytics_api(youtube_video_id, days_back)
        if analytics:
            return analytics

        # Fallback: public stats via Data API v3
        return self._pull_data_api_stats(youtube_video_id)

    def _pull_analytics_api(self, video_id: str, days: int) -> Optional[Dict]:
        token_file = HERE.parent / "youtube_automation" / "youtube_token.json"
        if not token_file.exists():
            return None
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            scopes = ["https://www.googleapis.com/auth/yt-analytics.readonly"]
            creds  = Credentials.from_authorized_user_file(str(token_file))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())

            yt_analytics = build("youtubeAnalytics", "v2",
                                  credentials=creds, cache_discovery=False)
            end_date   = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now().replace(day=max(1, datetime.now().day - days))
                          .strftime("%Y-%m-%d"))

            response = yt_analytics.reports().query(
                ids="channel==MINE",
                startDate=start_date,
                endDate=end_date,
                metrics="views,estimatedMinutesWatched,averageViewDuration,"
                        "averageViewPercentage,subscribersGained,likes,"
                        "comments,cardClickRate,cardImpressions",
                filters=f"video=={video_id}",
                dimensions="",
            ).execute()

            rows = response.get("rows", [[]])
            if rows:
                r = rows[0]
                return {
                    "views":            int(r[0]),
                    "watch_time_min":   float(r[1]),
                    "avd_seconds":      float(r[2]),
                    "avd_pct":          float(r[3]),
                    "subscribers_gained": int(r[4]),
                    "likes":            int(r[5]),
                    "comments":         int(r[6]),
                    "card_ctr":         float(r[7]),
                    "card_impressions":  int(r[8]),
                }
        except Exception as e:
            log.debug(f"Analytics API unavailable: {e}")
        return None

    def _pull_data_api_stats(self, video_id: str) -> Optional[Dict]:
        """Use YouTube Data API v3 public stats endpoint."""
        api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not api_key:
            return None
        try:
            import requests, ssl
            sess = requests.Session()
            sess.verify = False
            r = sess.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics,contentDetails",
                        "id": video_id, "key": api_key},
                timeout=15,
            )
            if not r.ok:
                return None
            items = r.json().get("items", [])
            if not items:
                return None
            stats = items[0].get("statistics", {})
            return {
                "views":    int(stats.get("viewCount", 0)),
                "likes":    int(stats.get("likeCount", 0)),
                "comments": int(stats.get("commentCount", 0)),
            }
        except Exception as e:
            log.debug(f"Data API stats failed: {e}")
        return None

    # ── Lesson extraction ─────────────────────────────────────────────────────

    def extract_lessons(self, video_data: Dict,
                         analytics: Dict) -> List[str]:
        """Return human-readable lessons from one video's performance."""
        lessons = []
        avg = self.memory.performance_summary()

        ctr     = analytics.get("card_ctr", 0)
        avd_pct = analytics.get("avd_pct", 0)

        if ctr > avg["avg_ctr"] * 1.1:
            lessons.append(
                f"Thumbnail/title CTR {ctr:.2f}% beat channel avg "
                f"({avg['avg_ctr']:.2f}%) — keep "
                f"{video_data.get('thumbnail_variant', 'this variant')}"
            )
        elif ctr < avg["avg_ctr"] * 0.9:
            lessons.append(
                f"CTR underperformed ({ctr:.2f}% vs avg {avg['avg_ctr']:.2f}%) "
                f"— try different thumbnail style next time"
            )

        if avd_pct > avg["avg_avd_pct"] * 1.05:
            lessons.append(
                f"Strong retention {avd_pct:.1f}% — "
                f"{video_data.get('hook_style', 'hook style')} worked well"
            )
        elif avd_pct < avg["avg_avd_pct"] * 0.9:
            lessons.append(
                f"Retention drop at {avd_pct:.1f}% — "
                f"add pattern interrupt earlier in script"
            )

        subs = analytics.get("subscribers_gained", 0)
        if subs > 10:
            lessons.append(
                f"{subs} subscribers gained — "
                f"{video_data.get('topic', 'topic')} resonated strongly"
            )

        return lessons

    # ── Config self-update ────────────────────────────────────────────────────

    def update_configs_from_lessons(self, video_data: Dict,
                                     analytics: Dict) -> None:
        """Propagate performance data into config files and memory."""
        ctr     = analytics.get("card_ctr",  0)
        avd_pct = analytics.get("avd_pct",   0)
        rpm     = analytics.get("rpm",        0.0)

        # Update memory correlations
        for key, val in [
            ("style_preset",   video_data.get("script_style", "")),
            ("hook_style",     video_data.get("hook_style", "")),
            ("thumbnail_variant", video_data.get("thumbnail_variant", "")),
            ("video_length_bucket",
             str(round(video_data.get("video_length_s", 0) / 60) * 60)),
        ]:
            if val:
                self.memory.update_config_performance(
                    key, val, ctr, avd_pct, rpm
                )

        # Let Brain apply the best settings
        from core.brain import get_brain
        get_brain().apply_learned_settings()

    # ── Full cycle ────────────────────────────────────────────────────────────

    def run_for_video(self, video_id: int, youtube_id: str,
                       video_data: Dict) -> Dict:
        """Run the complete feedback cycle for one video."""
        log.info(f"Feedback loop: checking video {youtube_id!r}")

        analytics = self.pull_analytics(youtube_id)
        if not analytics:
            log.warning(f"  No analytics available for {youtube_id} yet")
            return {}

        # Persist to DB
        self.memory.update_video_analytics(video_id, **analytics)

        lessons = self.extract_lessons(video_data, analytics)
        for l in lessons:
            log.info(f"  Lesson: {l}")

        self.update_configs_from_lessons(video_data, analytics)

        return {
            "analytics": analytics,
            "lessons": lessons,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def run_pending_checkpoints(self) -> int:
        """Check all videos that need analytics updates."""
        updated = 0
        videos  = self.memory.get_video_stats(n=30)
        now     = datetime.now(timezone.utc)

        for v in videos:
            if not v.get("youtube_id"):
                continue
            created = datetime.fromisoformat(v["created_at"])
            age_h   = (now - created).total_seconds() / 3600

            # Check at 24h / 72h / 168h / 672h marks (±2h tolerance)
            for window in ANALYTICS_WINDOWS:
                if abs(age_h - window) < 2:
                    self.run_for_video(v["id"], v["youtube_id"], v)
                    updated += 1
                    break

        return updated
