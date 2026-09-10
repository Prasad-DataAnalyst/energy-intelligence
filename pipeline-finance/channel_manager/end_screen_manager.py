"""
channel_manager/end_screen_manager.py — DriftWire326 Module 25
Adds end screens (subscribe + best-for-viewer video) to uploaded videos.
YouTube Data API endScreens resource — 50 quota units per video.

NOTE: The YouTube Data API v3 endScreens resource requires OAuth2 and may
require the channel to meet certain eligibility criteria (1,000 subscribers).
If the API returns 403/400, the method logs a warning and returns False
gracefully so the rest of the upload flow is not blocked.
"""
import logging
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Explain the missing API once per process, not once per publish.
_EXPLAINED = False

END_SCREEN_DURATION_SECONDS = 20  # last N seconds of the video


def _get_youtube_service():
    from uploader.uploader import _get_authenticated_service
    return _get_authenticated_service()


class EndScreenManager:
    """Manages YouTube end screens for DriftWire326 videos."""

    def __init__(self, youtube_service=None):
        self._svc = youtube_service

    def _service(self):
        if self._svc is None:
            self._svc = _get_youtube_service()
        return self._svc

    def add_end_screen(self, video_id: str, duration_seconds: float) -> bool:
        """
        Add subscribe + best-for-viewer end screen elements to a video.
        End screens occupy the last END_SCREEN_DURATION_SECONDS of the video.
        Returns True on success, False if ineligible or API error.
        """
        if duration_seconds < END_SCREEN_DURATION_SECONDS + 5:
            logger.warning(
                "Video %s too short (%.0fs) for end screens — skipping",
                video_id, duration_seconds,
            )
            return False

        # There is no API for this. videos.update has no "endscreen" part —
        # YouTube rejects the request at validation with
        #
        #   "'endscreen'" ... reason: unknownPart
        #
        # which is not an eligibility problem, as the old warning claimed,
        # and no amount of verification or scope will change it. End screens
        # are Studio-only. The call was made on every publish and could
        # never have succeeded, so it is gone; what remains is saying so
        # once, clearly, instead of logging a misleading warning forever.
        global _EXPLAINED
        if not _EXPLAINED:
            _EXPLAINED = True
            logger.info(
                "End screens cannot be set through the API (videos.update has "
                "no 'endscreen' part) — configure them once in YouTube Studio "
                "under Content > a video > Editor, or rely on the burned-in "
                "end card the video builder already appends."
            )
        logger.debug("End screen skipped for %s (%.0fs) — no API exists",
                     video_id, duration_seconds)
        return False

    def add_end_screen_to_recent(self, video_id: str, video_duration: float) -> bool:
        """Convenience wrapper called immediately after upload."""
        return self.add_end_screen(video_id, video_duration)
