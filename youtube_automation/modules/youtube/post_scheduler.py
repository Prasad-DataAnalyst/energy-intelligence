"""
post_scheduler.py
Schedule a community post and pinned comment after a video upload.
All pending actions are persisted to config/post_queue.json so they
survive process restarts.
"""

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import config
import youtube_uploader as uploader

log = logging.getLogger(__name__)

_QUEUE_FILE = Path(config.BASE_DIR) / "config" / "post_queue.json"
_lock = threading.Lock()


# ─────────────────────────────────────────────────────────────────────────────
# Queue persistence
# ─────────────────────────────────────────────────────────────────────────────

def _load_queue() -> list:
    try:
        if _QUEUE_FILE.exists():
            return json.loads(_QUEUE_FILE.read_text())
    except Exception:
        pass
    return []


def _save_queue(queue: list) -> None:
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    _QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def _add_to_queue(action: dict) -> None:
    with _lock:
        q = _load_queue()
        q.append(action)
        _save_queue(q)


def _remove_from_queue(action_id: str) -> None:
    with _lock:
        q = _load_queue()
        q = [a for a in q if a.get("id") != action_id]
        _save_queue(q)


# ─────────────────────────────────────────────────────────────────────────────
# Actions
# ─────────────────────────────────────────────────────────────────────────────

def _do_community_post(text: str) -> bool:
    try:
        uploader.post_community_post(text)
        log.info("Community post published: %s…", text[:60])
        return True
    except Exception as exc:
        log.error("Community post failed: %s", exc)
        return False


def _do_pin_comment(video_id: str, text: str) -> bool:
    try:
        comment_id = uploader.post_comment(video_id, text)
        if comment_id:
            uploader.pin_comment(video_id, comment_id)
            log.info("Comment pinned on %s", video_id)
            return True
        return False
    except Exception as exc:
        log.error("Pin comment failed for %s: %s", video_id, exc)
        return False


def _run_action(action: dict) -> None:
    """Execute one queued action and remove it from the queue on success."""
    kind       = action.get("kind")
    action_id  = action.get("id", "")
    video_id   = action.get("video_id", "")
    text       = action.get("text", "")

    success = False
    if kind == "community_post":
        success = _do_community_post(text)
    elif kind == "pin_comment":
        success = _do_pin_comment(video_id, text)
    else:
        log.warning("Unknown post action kind: %s", kind)
        success = True   # remove unknown actions to avoid inf loop

    if success:
        _remove_from_queue(action_id)
    else:
        # Mark retry time +1h
        with _lock:
            q = _load_queue()
            for a in q:
                if a.get("id") == action_id:
                    a["retries"] = a.get("retries", 0) + 1
                    a["next_attempt"] = time.time() + 3600
            _save_queue(q)


def _schedule(action: dict, delay_seconds: float) -> None:
    """Fire _run_action after delay_seconds in a daemon thread."""
    def _task():
        time.sleep(max(0, delay_seconds))
        _run_action(action)

    t = threading.Thread(target=_task, daemon=True, name=f"post_{action['id']}")
    t.start()
    log.info("Scheduled %s in %.0fs (id=%s)", action["kind"], delay_seconds, action["id"])


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def schedule_community_post(
    video_id: str,
    video_url: str,
    text: Optional[str] = None,
    delay_minutes: int = 60,
) -> str:
    """
    Schedule a community post text_minutes after now.
    Returns the action id.
    """
    import uuid
    if not text:
        text = (
            f"New video just dropped! Watch it here: {video_url}\n\n"
            "Drop your thoughts in the comments — what surprised you most? 👇"
        )
    action_id = str(uuid.uuid4())
    action = {
        "id":           action_id,
        "kind":         "community_post",
        "video_id":     video_id,
        "video_url":    video_url,
        "text":         text,
        "created_at":   time.time(),
        "retries":      0,
        "next_attempt": time.time() + delay_minutes * 60,
    }
    _add_to_queue(action)
    _schedule(action, delay_minutes * 60)
    return action_id


def schedule_pinned_comment(
    video_id: str,
    text: Optional[str] = None,
    delay_minutes: int = 5,
) -> str:
    """
    Schedule a pinned comment delay_minutes after now.
    Returns the action id.
    """
    import uuid
    if not text:
        text = (
            "LIKE + COMMENT your biggest takeaway below!\n"
            "Subscribe for daily mind-fuel. New video every day at 15:00 UTC."
        )
    action_id = str(uuid.uuid4())
    action = {
        "id":           action_id,
        "kind":         "pin_comment",
        "video_id":     video_id,
        "text":         text,
        "created_at":   time.time(),
        "retries":      0,
        "next_attempt": time.time() + delay_minutes * 60,
    }
    _add_to_queue(action)
    _schedule(action, delay_minutes * 60)
    return action_id


def load_and_resume_queue() -> int:
    """
    Re-schedule all pending actions from the persisted queue.
    Call this on daemon restart. Returns count of rescheduled actions.
    """
    now = time.time()
    queue = _load_queue()
    rescheduled = 0
    for action in queue:
        if action.get("retries", 0) >= 5:
            log.warning("Dropping stale post action %s after 5 retries", action.get("id"))
            _remove_from_queue(action.get("id", ""))
            continue
        delay = max(0, action.get("next_attempt", now) - now)
        _schedule(action, delay)
        rescheduled += 1
    if rescheduled:
        log.info("Resumed %d pending post actions from queue", rescheduled)
    return rescheduled
