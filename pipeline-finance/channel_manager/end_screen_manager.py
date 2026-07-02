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

        start_ms = int((duration_seconds - END_SCREEN_DURATION_SECONDS) * 1000)
        end_ms = int(duration_seconds * 1000)

        body = {
            "kind": "youtube#video",
            "id": video_id,
            "endscreen": {
                "elements": [
                    {
                        "type": "SUBSCRIBE",
                        "position": {"type": "CORNER", "cornerPosition": "TOP_RIGHT"},
                        "startOffsetMs": start_ms,
                        "widthMs": end_ms - start_ms,
                        "element": {
                            "type": "SUBSCRIBE",
                            "subscribe": {
                                "backgroundColor": 16711680,  # red
                            },
                        },
                    },
                    {
                        "type": "RECOMMENDED_VIDEO",
                        "position": {"type": "CORNER", "cornerPosition": "BOTTOM_LEFT"},
                        "startOffsetMs": start_ms,
                        "widthMs": end_ms - start_ms,
                        "element": {
                            "type": "RECOMMENDED_VIDEO",
                            "recommendedVideo": {"videoId": "featured"},
                        },
                    },
                ]
            },
        }

        try:
            self._service().videos().update(
                part="endscreen",
                body=body,
            ).execute()
            logger.info("End screens added to video %s (start: %dms)", video_id, start_ms)
            return True
        except Exception as exc:
            # 403 = eligibility; 400 = bad format — both are non-fatal
            logger.warning(
                "Could not add end screen to video %s (may require eligibility): %s",
                video_id, exc,
            )
            return False

    def add_end_screen_to_recent(self, video_id: str, video_duration: float) -> bool:
        """Convenience wrapper called immediately after upload."""
        return self.add_end_screen(video_id, video_duration)
