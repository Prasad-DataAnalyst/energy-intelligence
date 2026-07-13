"""
scheduler/post_upload.py — DriftWire326 reliability layer
Single choke-point for everything that should happen after a successful upload:

  1. Record the upload in the archive manifest  (uploader.record_upload)
  2. Route the video to the correct playlist    (PlaylistManager)
  3. Upload SRT captions from the script        (SubtitleManager)
  4. Add end screens                            (EndScreenManager, best-effort)
  5. Post the pinned disclaimer comment         (PostManager)
  6. Record the topic in the dedup history      (ContentTracker)

Every sub-task is independent and non-fatal: a failure is logged and counted
but never blocks the others — the video is already live, so post-upload
work must degrade gracefully.

Lives in the scheduler layer because it orchestrates uploader +
channel_manager, which must not import each other.
"""
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def finalize_upload(
    video_id: str,
    video_type: str,
    title: str,
    upload_result=None,
    upload_config=None,
    script_text: str = "",
    topic: str = "",
    video_duration_seconds: float = 0.0,
    sunday_theme: Optional[str] = None,
    youtube_service=None,
) -> dict:
    """
    Run all post-upload tasks for a successfully uploaded video.

    Returns a dict of task name → bool/str result, e.g.:
        {"manifest": True, "playlist": "PL...", "captions": True,
         "end_screen": False, "pinned_comment": "Ugz...", "content_tracker": True}

    Never raises — individual failures are logged and reported in the dict.
    """
    results: dict = {}

    # 1. Upload manifest (feeds the dead-man switch — run first)
    try:
        if upload_result is not None and upload_config is not None:
            from uploader.uploader import record_upload
            record_upload(upload_result, upload_config)
            results["manifest"] = True
        else:
            results["manifest"] = False
    except Exception as exc:
        logger.error("post_upload manifest failed: %s", exc)
        results["manifest"] = False

    # 2. Playlist routing
    try:
        from channel_manager.playlist_manager import PlaylistManager
        pm = PlaylistManager(youtube_service=youtube_service)
        playlist_id = pm.route_video_to_playlist(
            video_id, video_type, sunday_theme=sunday_theme
        )
        results["playlist"] = playlist_id or False
    except Exception as exc:
        logger.error("post_upload playlist routing failed: %s", exc)
        results["playlist"] = False

    # 3. Captions (SRT from script) — 400 quota units, skip when script missing
    try:
        if script_text.strip():
            from channel_manager.subtitle_manager import SubtitleManager
            sm = SubtitleManager(youtube_service=youtube_service)
            results["captions"] = sm.upload_captions(video_id, script_text)
        else:
            results["captions"] = False
    except Exception as exc:
        logger.error("post_upload captions failed: %s", exc)
        results["captions"] = False

    # 4. End screens (best-effort — API may reject based on eligibility)
    try:
        if video_duration_seconds > 0:
            from channel_manager.end_screen_manager import EndScreenManager
            esm = EndScreenManager(youtube_service=youtube_service)
            results["end_screen"] = esm.add_end_screen_to_recent(
                video_id, video_duration_seconds
            )
        else:
            results["end_screen"] = False
    except Exception as exc:
        logger.warning("post_upload end screen failed (non-fatal): %s", exc)
        results["end_screen"] = False

    # 5. Pinned disclaimer comment
    try:
        from channel_manager.post_manager import PostManager
        pstm = PostManager(youtube_service=youtube_service)
        comment_id = pstm.pin_disclaimer_comment(video_id, title)
        results["pinned_comment"] = comment_id or False
    except Exception as exc:
        logger.error("post_upload pinned comment failed: %s", exc)
        results["pinned_comment"] = False

    # 6. Content dedup history
    try:
        from channel_manager.content_tracker import ContentTracker
        ct = ContentTracker()
        ct.record_content(topic or title, title=title, video_id=video_id)
        results["content_tracker"] = True
    except Exception as exc:
        logger.error("post_upload content tracker failed: %s", exc)
        results["content_tracker"] = False

    succeeded = sum(1 for v in results.values() if v)
    logger.info(
        "finalize_upload(%s): %d/%d tasks succeeded — %s",
        video_id, succeeded, len(results), results,
    )
    return results


def check_topic_duplicate(topic: str, title: str = "") -> bool:
    """
    Pre-generation dedup gate. Returns True if this topic is too similar
    to something published in the last 14 days. Never raises.
    """
    try:
        from channel_manager.content_tracker import ContentTracker
        is_dup, similarity = ContentTracker().is_duplicate(topic, title)
        if is_dup:
            logger.warning(
                "Topic '%s' duplicates recent content (similarity=%.2f)", topic[:60], similarity
            )
        return is_dup
    except Exception as exc:
        logger.warning("Duplicate check failed (allowing topic): %s", exc)
        return False
