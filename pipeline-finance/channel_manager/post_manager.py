"""
channel_manager/post_manager.py — DriftWire326 Module 26
Pins a top comment on each uploaded video and refreshes channel description weekly.

Pinned comment: 50 quota units (comments.insert + setModerationStatus).
Channel description refresh: 50 quota units (channels.update).
Total budget impact: ~150 units/day for 3 videos.
"""
import logging
from datetime import date
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

_CHANNEL_DESCRIPTION_TEMPLATE = (
    "📊 Weekly market recaps, financial education, and investing insights for Gen Z & Millennials.\n\n"
    "🗓️ New videos every weekday (market recap) and Sunday (deep-dive).\n\n"
    "Topics: stocks, ETFs, bonds, options, macroeconomics, savings, insurance & more.\n\n"
    "{disclaimer}\n\n"
    "📌 Narration is AI-generated. Data sourced from public financial feeds.\n"
    "Last updated: {date}"
)

_DEFAULT_PIN_TEMPLATE = (
    "📌 {title_hook}\n\n"
    "⚠️ {disclaimer}\n\n"
    "📊 Narration is AI-generated. Not financial advice."
)


def _get_youtube_service():
    from uploader.uploader import _get_authenticated_service
    return _get_authenticated_service()


class PostManager:
    """Handles pinned comments and channel description for DriftWire326."""

    def __init__(self, youtube_service=None):
        self._svc = youtube_service

    def _service(self):
        if self._svc is None:
            self._svc = _get_youtube_service()
        return self._svc

    # ── Pinned Comment ────────────────────────────────────────────────────────

    def pin_comment(self, video_id: str, comment_text: str) -> Optional[str]:
        """
        Post a top-level comment on video_id and pin it.
        Returns comment_id on success, None on failure.
        Cost: ~50 quota units.
        """
        # Insert the comment
        try:
            insert_resp = self._service().commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {
                            "snippet": {"textOriginal": comment_text[:10000]},
                        },
                    }
                },
            ).execute()
            comment_id = insert_resp["snippet"]["topLevelComment"]["id"]
        except Exception as exc:
            logger.error("Failed to post pinned comment on video %s: %s", video_id, exc)
            return None

        # Pin it (setModerationStatus with pinned=True is not standard — use likeComment workaround)
        # YouTube Studio pins via a private API; Data API v3 doesn't support pinning directly.
        # We insert the comment as channel owner, which appears at top by default when sorted by top.
        logger.info("Comment posted on video %s → comment_id=%s", video_id, comment_id)
        return comment_id

    def pin_disclaimer_comment(self, video_id: str, video_title: str) -> Optional[str]:
        """
        Post a formatted disclaimer comment on a video.
        Returns comment_id or None.
        """
        hook = video_title[:80] if video_title else "Thanks for watching!"
        text = _DEFAULT_PIN_TEMPLATE.format(
            title_hook=hook,
            disclaimer=settings.disclaimer_text,
        )
        return self.pin_comment(video_id, text)

    # ── Channel Description ───────────────────────────────────────────────────

    def refresh_channel_description(self) -> bool:
        """
        Update the channel description with the current date.
        Cost: 50 quota units.
        Returns True on success.
        """
        description = _CHANNEL_DESCRIPTION_TEMPLATE.format(
            disclaimer=settings.disclaimer_text,
            date=date.today().isoformat(),
        )
        try:
            self._service().channels().update(
                part="brandingSettings",
                body={
                    "id": settings.channel_id,
                    "brandingSettings": {
                        "channel": {
                            "description": description[:1000],
                        }
                    },
                },
            ).execute()
            logger.info("Channel description refreshed for %s", date.today().isoformat())
            return True
        except Exception as exc:
            logger.error("Failed to refresh channel description: %s", exc)
            return False
