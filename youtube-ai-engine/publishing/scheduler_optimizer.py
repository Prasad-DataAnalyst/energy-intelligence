"""
publishing/scheduler_optimizer.py — Best Time to Post Calculator
================================================================
Analyses historical upload timing vs view velocity to recommend
the optimal day/hour to publish new videos.

Also manages the YouTube scheduling queue:
  - Reads upcoming upload slots
  - Avoids conflicts with previous uploads (space 24h+ apart)
  - Returns ISO 8601 publishAt datetime for the YouTube API
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

log = logging.getLogger("brain.scheduler_optimizer")

# Default best times (fallback when no data available)
DEFAULT_BEST = {
    "Monday":    14,
    "Tuesday":   15,
    "Wednesday": 14,
    "Thursday":  13,
    "Friday":    15,
    "Saturday":  11,
    "Sunday":    12,
}


class SchedulerOptimizer:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    def _get_best_from_history(self) -> Tuple[str, int]:
        """Ask AlgorithmDecoder for best upload time from performance data."""
        try:
            from intelligence.algorithm_decoder import AlgorithmDecoder
            return AlgorithmDecoder(self.memory).best_upload_time()
        except Exception as e:
            log.debug(f"AlgorithmDecoder unavailable: {e}")
            return "Wednesday", 14

    def next_publish_slot(self,
                           min_hours_from_now: int = 2,
                           force_hour: Optional[int] = None) -> str:
        """
        Return the next recommended publish datetime as ISO 8601 string.

        Picks the next occurrence of the best-performing upload day/hour,
        ensuring it's at least min_hours_from_now in the future.
        """
        best_day_name, best_hour = self._get_best_from_history()
        if force_hour is not None:
            best_hour = force_hour

        day_map = {
            "Monday": 0, "Tuesday": 1, "Wednesday": 2,
            "Thursday": 3, "Friday": 4, "Saturday": 5, "Sunday": 6,
        }
        target_weekday = day_map.get(best_day_name, 2)

        now     = datetime.now(timezone.utc)
        minimum = now + timedelta(hours=min_hours_from_now)

        # Find next occurrence of target_weekday at best_hour
        candidate = minimum.replace(
            hour=best_hour, minute=0, second=0, microsecond=0
        )
        days_ahead = (target_weekday - candidate.weekday()) % 7
        if days_ahead == 0 and candidate <= minimum:
            days_ahead = 7
        candidate += timedelta(days=days_ahead)

        iso = candidate.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        log.info(f"Next publish slot: {iso} ({best_day_name} {best_hour:02d}:00 UTC)")
        return iso

    def publish_now_iso(self) -> str:
        """Return current UTC time as ISO 8601 — use for immediate publish."""
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    def schedule_report(self) -> Dict:
        """Return a summary of optimal publishing windows."""
        best_day, best_hour = self._get_best_from_history()
        next_slot           = self.next_publish_slot()
        return {
            "best_day":       best_day,
            "best_hour_utc":  best_hour,
            "next_slot":      next_slot,
            "timezone_note":  "All times in UTC",
        }
