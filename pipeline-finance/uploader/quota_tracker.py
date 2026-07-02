"""
YouTube API quota tracker.
YouTube Data API v3 free tier: 10,000 units/day.
Video upload costs 1,600 units. thumbnails.set costs 50 units.
This module prevents overages and logs all API usage.
"""
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from config.settings import MIN_QUOTA_TO_UPLOAD, settings

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
        # MIN_QUOTA_TO_UPLOAD (1700) includes the upload cost plus a buffer
        # for the follow-up thumbnail.set / playlistItems.insert calls.
        return self.remaining >= MIN_QUOTA_TO_UPLOAD

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
        """Atomically persist state: write to a temp file then rename.

        Prevents a corrupted/truncated quota file if the process crashes
        mid-write, which would otherwise reset the day's quota accounting.
        """
        tmp_path = self.log_path.with_suffix(".json.tmp")
        tmp_path.write_text(json.dumps(asdict(self._state), indent=2))
        os.replace(tmp_path, self.log_path)

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
            self._state.remaining >= MIN_QUOTA_TO_UPLOAD
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

    # ── Additional methods ────────────────────────────────────────────────────

    def load_state(self) -> DailyQuotaState:
        """Explicitly reload state from disk (re-reads file)."""
        self._state = self._load()
        return self._state

    def save_state(self) -> None:
        """Explicitly persist current state to disk."""
        self._save()

    def reset_daily(self) -> None:
        """Force-reset daily counters (e.g. for testing or manual override)."""
        self._state = DailyQuotaState(
            date=self._today(),
            total_used=0,
            remaining=DAILY_QUOTA_LIMIT,
            entries=[],
            uploads_today=0,
        )
        self._save()
        logger.info("Quota state manually reset for %s", self._state.date)

    def log_usage(self, operation: str, cost: Optional[int] = None, note: str = "") -> None:
        """
        Log an arbitrary API operation with an optional cost override.
        Useful for manually tracking non-standard API calls.
        """
        self._ensure_today()
        effective_cost = cost if cost is not None else QUOTA_COSTS.get(operation, 1)
        entry = QuotaEntry(
            operation=operation,
            cost=effective_cost,
            video_id=None,
            title=note or None,
        )
        self._state.total_used += effective_cost
        self._state.remaining = max(0, DAILY_QUOTA_LIMIT - self._state.total_used)
        self._state.entries.append(asdict(entry))
        self._save()
        logger.debug("Usage logged: %s (%d units) — note: %s", operation, effective_cost, note)

    def get_remaining(self) -> int:
        """Return remaining quota units for today."""
        self._ensure_today()
        return self._state.remaining

    def get_daily_summary(self) -> dict:
        """Return a structured summary dict for monitoring/alerting."""
        s = self.status()
        return {
            "date": s.date,
            "total_used": s.total_used,
            "remaining": s.remaining,
            "limit": s.limit,
            "utilization_pct": s.utilization_pct,
            "uploads_today": s.uploads_today,
            "max_uploads": settings.daily_upload_quota,
            "can_upload": s.can_upload,
            "entry_count": len(s.entries),
        }

    def alert_low_quota(self, threshold: int = 2000) -> bool:
        """
        Check if remaining quota is below threshold.
        Logs a warning and returns True if low.
        """
        remaining = self.get_remaining()
        if remaining < threshold:
            logger.warning(
                "LOW QUOTA ALERT: only %d units remaining (threshold: %d)",
                remaining, threshold,
            )
            return True
        return False
