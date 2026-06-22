"""
YouTube API quota tracker.
YouTube Data API v3 free tier: 10,000 units/day.
Video upload costs 1,600 units. thumbnails.set costs 50 units.
This module prevents overages and logs all API usage.
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

QUOTA_LOG_PATH = settings.logs_dir / "quota_tracker.json"
DAILY_QUOTA_LIMIT = 10_000   # YouTube free tier

# API operation costs (units)
QUOTA_COSTS = {
    "video.insert": 1600,
    "thumbnail.set": 50,
    "playlist.insert": 50,
    "videos.update": 50,
    "videos.list": 1,
    "channels.list": 1,
    "playlistItems.insert": 50,
}


@dataclass
class QuotaEntry:
    operation: str
    cost: int
    video_id: Optional[str]
    title: Optional[str]
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class DailyQuotaState:
    date: str
    total_used: int
    remaining: int
    entries: list[dict]
    uploads_today: int
    limit: int = DAILY_QUOTA_LIMIT

    @property
    def can_upload(self) -> bool:
        return self.remaining >= QUOTA_COSTS["video.insert"]

    @property
    def utilization_pct(self) -> float:
        return round((self.total_used / self.limit) * 100, 1)


class QuotaTracker:
    """Thread-safe daily quota tracker backed by a JSON file."""

    def __init__(self, log_path: Path = QUOTA_LOG_PATH):
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()

    def _today(self) -> str:
        return date.today().isoformat()

    def _load(self) -> DailyQuotaState:
        today = self._today()
        if self.log_path.exists():
            try:
                data = json.loads(self.log_path.read_text())
                if data.get("date") == today:
                    return DailyQuotaState(
                        date=data["date"],
                        total_used=data["total_used"],
                        remaining=data["remaining"],
                        entries=data["entries"],
                        uploads_today=data["uploads_today"],
                        limit=data.get("limit", DAILY_QUOTA_LIMIT),
                    )
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Quota log corrupted, resetting: %s", exc)

        # Fresh state for today
        return DailyQuotaState(
            date=today,
            total_used=0,
            remaining=DAILY_QUOTA_LIMIT,
            entries=[],
            uploads_today=0,
        )

    def _save(self) -> None:
        self.log_path.write_text(json.dumps(asdict(self._state), indent=2))

    def _ensure_today(self) -> None:
        """Reset state if day has rolled over."""
        if self._state.date != self._today():
            logger.info("New quota day — resetting counters")
            self._state = DailyQuotaState(
                date=self._today(),
                total_used=0,
                remaining=DAILY_QUOTA_LIMIT,
                entries=[],
                uploads_today=0,
            )
            self._save()

    def can_upload(self) -> bool:
        self._ensure_today()
        allowed = (
            self._state.remaining >= QUOTA_COSTS["video.insert"]
            and self._state.uploads_today < settings.daily_upload_quota
        )
        if not allowed:
            logger.warning(
                "Upload blocked — Quota: %d/%d used (%d remaining), Uploads today: %d/%d",
                self._state.total_used, DAILY_QUOTA_LIMIT,
                self._state.remaining,
                self._state.uploads_today, settings.daily_upload_quota,
            )
        return allowed

    def record_upload(self, video_id: str, title: str) -> None:
        self._ensure_today()
        self._record("video.insert", video_id, title)
        self._state.uploads_today += 1
        self._save()
        logger.info("Quota recorded: video.insert (%d units) — %d/%d remaining",
                    QUOTA_COSTS["video.insert"], self._state.remaining, DAILY_QUOTA_LIMIT)

    def record_operation(self, operation: str, video_id: Optional[str] = None) -> None:
        self._ensure_today()
        self._record(operation, video_id, None)
        self._save()

    def _record(self, operation: str, video_id: Optional[str], title: Optional[str]) -> None:
        cost = QUOTA_COSTS.get(operation, 1)
        entry = QuotaEntry(operation=operation, cost=cost, video_id=video_id, title=title)
        self._state.total_used += cost
        self._state.remaining = max(0, DAILY_QUOTA_LIMIT - self._state.total_used)
        self._state.entries.append(asdict(entry))

    def status(self) -> DailyQuotaState:
        self._ensure_today()
        return self._state

    def report(self) -> str:
        s = self.status()
        return (
            f"YouTube API Quota — {s.date}\n"
            f"  Used:      {s.total_used:,} / {s.limit:,} units ({s.utilization_pct}%)\n"
            f"  Remaining: {s.remaining:,} units\n"
            f"  Uploads:   {s.uploads_today} / {settings.daily_upload_quota} today\n"
            f"  Can Upload: {'YES ✅' if s.can_upload else 'NO ❌ — quota exceeded'}"
        )
