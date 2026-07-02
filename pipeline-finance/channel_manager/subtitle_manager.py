"""
channel_manager/subtitle_manager.py — DriftWire326 Module 27
Uploads SRT captions to YouTube via captions.insert.
Generates SRT from script text at ~140 WPM (average narration pace).
Cost: 400 quota units per caption track — budget 1,200/day for 3 videos.

SRT format:
    1
    00:00:00,000 --> 00:00:05,000
    First subtitle line.

    2
    00:00:05,000 --> 00:00:10,000
    Second line.
"""
import logging
import math
import re
import io
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

WORDS_PER_SECOND = 140 / 60  # ~2.33 wps at 140 WPM narration pace
MAX_LINE_WORDS = 12           # wrap at 12 words per SRT line
MAX_LINE_CHARS = 72           # YouTube SRT line char limit


def _get_youtube_service():
    from uploader.uploader import _get_authenticated_service
    return _get_authenticated_service()


def _ms_to_srt_time(ms: int) -> str:
    """Convert milliseconds → SRT timestamp HH:MM:SS,mmm."""
    h = ms // 3_600_000
    ms %= 3_600_000
    m = ms // 60_000
    ms %= 60_000
    s = ms // 1_000
    ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _clean_script(text: str) -> str:
    """Strip SSML tags, remove compliance markers, normalize whitespace."""
    # Remove SSML XML tags
    text = re.sub(r"<[^>]+>", "", text)
    # Remove markdown-style headers
    text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


def generate_srt(script_text: str, offset_seconds: float = 0.0) -> str:
    """
    Generate SRT caption content from a script.
    Timings are derived from word count at WORDS_PER_SECOND pace.

    Args:
        script_text: Raw script (may contain SSML or markdown).
        offset_seconds: Offset in seconds to shift all timestamps.

    Returns:
        SRT-formatted string.
    """
    text = _clean_script(script_text)
    words = text.split()
    if not words:
        return ""

    lines: list[str] = []
    current_line_words: list[str] = []
    blocks: list[str] = []  # one SRT line chunk per block
    char_count = 0

    for word in words:
        current_line_words.append(word)
        char_count += len(word) + 1
        if len(current_line_words) >= MAX_LINE_WORDS or char_count >= MAX_LINE_CHARS:
            blocks.append(" ".join(current_line_words))
            current_line_words = []
            char_count = 0

    if current_line_words:
        blocks.append(" ".join(current_line_words))

    srt_parts: list[str] = []
    offset_ms = int(offset_seconds * 1000)

    for idx, block in enumerate(blocks, start=1):
        word_count = len(block.split())
        duration_ms = max(int(word_count / WORDS_PER_SECOND * 1000), 1500)
        start_ms = offset_ms
        end_ms = offset_ms + duration_ms
        offset_ms = end_ms + 50  # 50ms gap between blocks

        srt_parts.append(
            f"{idx}\n{_ms_to_srt_time(start_ms)} --> {_ms_to_srt_time(end_ms)}\n{block}\n"
        )

    return "\n".join(srt_parts)


class SubtitleManager:
    """Uploads SRT captions to YouTube for DriftWire326 videos."""

    def __init__(self, youtube_service=None):
        self._svc = youtube_service

    def _service(self):
        if self._svc is None:
            self._svc = _get_youtube_service()
        return self._svc

    def upload_captions(
        self,
        video_id: str,
        script_text: str,
        language: str = "en",
        name: str = "English",
    ) -> bool:
        """
        Generate SRT from script_text and upload as caption track.
        Returns True on success. Cost: 400 quota units.
        """
        srt_content = generate_srt(script_text)
        if not srt_content:
            logger.warning("Empty SRT generated for video %s — skipping captions", video_id)
            return False

        srt_bytes = srt_content.encode("utf-8")
        try:
            from googleapiclient.http import MediaIoBaseUpload
            media = MediaIoBaseUpload(
                io.BytesIO(srt_bytes),
                mimetype="application/octet-stream",
                resumable=False,
            )
            self._service().captions().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "language": language,
                        "name": name,
                        "isDraft": False,
                    }
                },
                media_body=media,
            ).execute()
            logger.info(
                "Captions uploaded for video %s (%d chars SRT, %d quota units)",
                video_id, len(srt_content), 400,
            )
            return True
        except Exception as exc:
            logger.error("Failed to upload captions for video %s: %s", video_id, exc)
            return False

    def upload_captions_from_file(self, video_id: str, srt_path: str) -> bool:
        """Upload an existing .srt file directly."""
        try:
            from pathlib import Path
            from googleapiclient.http import MediaIoBaseUpload
            srt_bytes = Path(srt_path).read_bytes()
            media = MediaIoBaseUpload(
                io.BytesIO(srt_bytes),
                mimetype="application/octet-stream",
                resumable=False,
            )
            self._service().captions().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "language": "en",
                        "name": "English",
                        "isDraft": False,
                    }
                },
                media_body=media,
            ).execute()
            logger.info("Captions uploaded from file %s → video %s", srt_path, video_id)
            return True
        except Exception as exc:
            logger.error("Failed to upload captions from file %s: %s", srt_path, exc)
            return False
