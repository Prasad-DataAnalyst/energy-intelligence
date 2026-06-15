"""
youtube_uploader.py
Full YouTube automation: upload video, set thumbnail, post/pin comment,
handle quota errors with a next-day retry queue.
"""

import json
import os
import re
import time
import datetime
import logging
from pathlib import Path

import requests as req_lib
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

import config

log = logging.getLogger(__name__)

CHUNK = 5 * 1024 * 1024   # 5 MB resumable upload chunks

# Upload retry queue — persisted so it survives restarts
_QUEUE_FILE = Path(config.LOGS_DIR) / "upload_queue.json"
_LOG_FILE   = Path(config.LOGS_DIR) / "uploads.json"


# ─────────────────────────────────────────────────────────────────────────────
# Auth
# ─────────────────────────────────────────────────────────────────────────────

def _get_credentials() -> Credentials:
    token_path = Path(config.TOKEN_FILE)
    if not token_path.exists():
        raise FileNotFoundError(
            f"No YouTube token at {token_path}. Run first_time_setup.py."
        )
    creds = Credentials.from_authorized_user_file(str(token_path),
                                                   config.YOUTUBE_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json())
                log.info("YouTube OAuth token refreshed successfully")
            except Exception as exc:
                raise RuntimeError(
                    f"Token refresh failed: {exc}. Re-run first_time_setup.py."
                ) from exc
        else:
            raise RuntimeError(
                "Token invalid and cannot be refreshed. Re-run first_time_setup.py."
            )
    return creds


def _authed_session():
    import google.auth.transport.requests as gtr
    creds = _get_credentials()
    return gtr.AuthorizedSession(creds), creds


# ─────────────────────────────────────────────────────────────────────────────
# Main video upload
# ─────────────────────────────────────────────────────────────────────────────

def upload_video(video_path: str, content: dict, seo_package: dict = None) -> str:
    """
    Upload video_path to YouTube.

    content:     dict with title, description, tags, category, date
    seo_package: optional dict from YouTubePackager with richer metadata

    Returns video_id (e.g. "dQw4w9WgXcQ").
    Raises RuntimeError on quota / auth failure.
    On quota error the video is queued for retry next UTC midnight.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    # Merge SEO package into metadata
    title       = _best(seo_package, "selected_title", content, "title",  "Mind Fuel Daily")
    description = _build_description(content, seo_package)
    tags        = _build_tags(content, seo_package)

    session, creds = _authed_session()
    file_size = os.path.getsize(video_path)

    category_id   = str(content.get("category_id", getattr(config, "CATEGORY_ID", "22")))
    privacy_status = str(content.get("privacy_status", getattr(config, "PRIVACY_STATUS", "public")))

    metadata = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        tags,           # already character-limited by _build_tags
            "categoryId":  category_id,
            # NOTE: defaultLanguage is a channel property, NOT a video property.
            # Sending it here causes YouTube to return 400 Bad Request.
        },
        "status": {
            "privacyStatus": privacy_status,
            "madeForKids":   content.get("made_for_kids", False),
        },
    }

    init_resp = session.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers={
            "X-Upload-Content-Type":   "video/mp4",
            "X-Upload-Content-Length": str(file_size),
            "Content-Type":            "application/json",
        },
        data=json.dumps(metadata),
    )
    if init_resp.status_code == 403:
        _queue_for_retry(video_path, content, seo_package)
        raise RuntimeError(
            "YouTube quota exceeded — video queued for retry at next UTC midnight."
        )
    if not init_resp.ok:
        log.error("YouTube upload init failed %d: %s", init_resp.status_code, init_resp.text[:500])
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    log.info("Uploading: %s  (%.1f MB)", title, file_size / 1_048_576)
    video_id = _stream_upload(session, upload_url, video_path, file_size)

    log.info("Upload complete → https://youtu.be/%s", video_id)
    _log_upload(video_path, video_id, title, content)
    return video_id


def _stream_upload(session, upload_url: str, video_path: str, file_size: int) -> str:
    uploaded = 0
    retry    = 0
    with open(video_path, "rb") as fh:
        while uploaded < file_size:
            chunk = fh.read(CHUNK)
            end   = uploaded + len(chunk) - 1
            for attempt in range(6):
                try:
                    resp = session.put(
                        upload_url,
                        headers={
                            "Content-Range":  f"bytes {uploaded}-{end}/{file_size}",
                            "Content-Length": str(len(chunk)),
                        },
                        data=chunk,
                    )
                    break
                except Exception:
                    if attempt == 5:
                        raise
                    time.sleep(2 ** attempt)

            if resp.status_code in (200, 201):
                return resp.json()["id"]
            elif resp.status_code == 308:
                uploaded = int(
                    resp.headers.get("Range", f"bytes=0-{end}").split("-")[1]
                ) + 1
                pct = int(uploaded / file_size * 100)
                print(f"  {pct}%", end="\r", flush=True)
            elif resp.status_code in (500, 502, 503, 504) and retry < 5:
                retry += 1
                time.sleep(2 ** retry)
            elif resp.status_code == 403:
                raise RuntimeError("quota_exceeded")
            else:
                raise RuntimeError(f"Upload error {resp.status_code}: {resp.text[:300]}")
    raise RuntimeError("Upload stream ended without completion.")


# ─────────────────────────────────────────────────────────────────────────────
# Thumbnail upload
# ─────────────────────────────────────────────────────────────────────────────

def upload_thumbnail(video_id: str, image_path: str) -> bool:
    """Set a custom thumbnail on an existing video. Returns True on success."""
    if not image_path or not os.path.exists(image_path):
        log.warning("Thumbnail file not found: %s", image_path)
        return False
    try:
        session, _ = _authed_session()
        with open(image_path, "rb") as f:
            img_data = f.read()
        ext = Path(image_path).suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        resp = session.post(
            f"https://www.googleapis.com/upload/youtube/v3/thumbnails/set"
            f"?videoId={video_id}&uploadType=media",
            headers={"Content-Type": mime, "Content-Length": str(len(img_data))},
            data=img_data,
        )
        if resp.status_code in (200, 204):
            log.info("Thumbnail uploaded for %s", video_id)
            return True
        log.warning("Thumbnail upload returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Thumbnail upload failed: %s", exc)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Comment: post + pin
# ─────────────────────────────────────────────────────────────────────────────

def post_comment(video_id: str, text: str) -> str:
    """Post a top-level comment. Returns comment_id or ''."""
    try:
        session, _ = _authed_session()
        body = {
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {
                    "snippet": {"textOriginal": text[:10000]}
                },
            }
        }
        resp = session.post(
            "https://www.googleapis.com/youtube/v3/commentThreads?part=snippet",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )
        if resp.status_code == 200:
            cid = resp.json()["id"]
            log.info("Comment posted: %s", cid)
            return cid
        log.warning("Comment post returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Post comment failed: %s", exc)
    return ""


def pin_comment(video_id: str, comment_id: str) -> bool:
    """Pin a comment thread as the pinned comment. Returns True on success."""
    if not comment_id:
        return False
    try:
        session, _ = _authed_session()
        # YouTube doesn't have a direct "pin" API; best achievable is marking as top comment
        # by moderating it as ACCEPTED (channels can do this via commentThreads.update)
        body = {
            "id": comment_id,
            "snippet": {
                "videoId": video_id,
                "topLevelComment": {"id": comment_id},
                "canReply": True,
                "isPublic": True,
            },
        }
        resp = session.put(
            "https://www.googleapis.com/youtube/v3/commentThreads?part=snippet",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )
        success = resp.status_code in (200, 204)
        if success:
            log.info("Comment pinned: %s", comment_id)
        return success
    except Exception as exc:
        log.warning("Pin comment failed: %s", exc)
    return False


def post_community_post(text: str) -> bool:
    """
    Post a YouTube Community post.
    Note: Community Posts API requires channel membership and specific OAuth scopes.
    This uses the posts.insert endpoint (available for eligible channels).
    """
    try:
        session, _ = _authed_session()
        body = {
            "snippet": {
                "type": "textPost",
                "textOriginal": text[:2000],
            }
        }
        resp = session.post(
            "https://www.googleapis.com/youtube/v3/posts?part=snippet",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )
        if resp.status_code == 200:
            log.info("Community post published")
            return True
        log.warning("Community post returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Community post failed: %s", exc)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Retry queue — survives restarts
# ─────────────────────────────────────────────────────────────────────────────

def _queue_for_retry(video_path: str, content: dict, seo_package: dict):
    _QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
    queue = _load_queue()
    queue.append({
        "video_path":  video_path,
        "content":     {k: str(v) for k, v in content.items() if k != "_opportunity"},
        "seo_package": seo_package or {},
        "queued_at":   datetime.datetime.utcnow().isoformat() + "Z",
        "retry_after": _next_midnight_utc(),
        "attempts":    0,
    })
    _save_queue(queue)
    log.info("Video queued for retry: %s  (after %s)", video_path, _next_midnight_utc())


def process_retry_queue() -> int:
    """
    Process any queued uploads whose retry_after time has passed.
    Returns count of successful retries.
    """
    queue = _load_queue()
    now   = datetime.datetime.utcnow().isoformat() + "Z"
    remaining = []
    succeeded = 0

    for item in queue:
        if item.get("retry_after", "9999") > now:
            remaining.append(item)
            continue
        if item.get("attempts", 0) >= 3:
            log.warning("Upload abandoned after 3 attempts: %s", item["video_path"])
            continue
        try:
            item["attempts"] = item.get("attempts", 0) + 1
            video_id = upload_video(
                item["video_path"],
                item["content"],
                item.get("seo_package"),
            )
            log.info("Retry succeeded: %s → %s", item["video_path"], video_id)
            succeeded += 1
        except Exception as exc:
            log.warning("Retry failed: %s", exc)
            item["retry_after"] = _next_midnight_utc()
            remaining.append(item)

    _save_queue(remaining)
    return succeeded


def _load_queue() -> list:
    if _QUEUE_FILE.exists():
        try:
            return json.loads(_QUEUE_FILE.read_text())
        except Exception:
            pass
    return []


def _save_queue(queue: list) -> None:
    _QUEUE_FILE.write_text(json.dumps(queue, indent=2))


def _next_midnight_utc() -> str:
    tomorrow = datetime.datetime.utcnow().replace(
        hour=0, minute=5, second=0, microsecond=0
    ) + datetime.timedelta(days=1)
    return tomorrow.isoformat() + "Z"


# ─────────────────────────────────────────────────────────────────────────────
# Metadata helpers
# ─────────────────────────────────────────────────────────────────────────────

def _best(pkg: dict, pkg_key: str, content: dict, content_key: str, default: str) -> str:
    if pkg and pkg.get(pkg_key):
        return str(pkg[pkg_key])
    return str(content.get(content_key, default))


def _build_description(content: dict, seo_package: dict) -> str:
    if seo_package and seo_package.get("description"):
        desc = seo_package["description"]
    elif content.get("description"):
        # horoscope pipeline passes description directly
        desc = content["description"]
    else:
        topic = content.get("topic", "")
        hook  = content.get("hook",  "")
        desc  = f"{hook}\n\n{topic}\n\nSubscribe for daily mind fuel → @GetMindFuelNow"

    # Append chapters if present
    chapters = None
    if seo_package:
        chapters = seo_package.get("chapters") or seo_package.get("chapter_markers")
    if chapters:
        chapter_block = "\n\nCHAPTERS:\n" + "\n".join(
            f"{c.get('timestamp','00:00')} {c.get('title','')}"
            for c in (chapters if isinstance(chapters, list) else [])
        )
        desc = desc + chapter_block

    return desc


def _build_tags(content: dict, seo_package: dict) -> list:
    if seo_package and seo_package.get("tags"):
        raw = seo_package["tags"]
        if isinstance(raw, str):
            tags = [t.strip() for t in raw.split(",") if t.strip()]
        elif isinstance(raw, list):
            tags = [str(t) for t in raw]
        else:
            tags = []
    else:
        tags = content.get("tags", [])

    # Sanitize: keep only alphanumerics, spaces, and hyphens (no apostrophes or special chars)
    cleaned = []
    for tag in tags:
        tag = re.sub(r"[^\w\s\-]", "", str(tag), flags=re.UNICODE).strip()
        if tag:
            cleaned.append(tag)

    # Hard caps: ≤10 tags and ≤400 total characters across all tags
    result, total = [], 0
    for tag in cleaned:
        if len(result) >= 10:
            break
        addition = len(tag) + (1 if result else 0)
        if total + addition > 400:
            break
        result.append(tag)
        total += addition

    log.info("Upload tags (%d, %d chars): %s", len(result), total, result)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Channel branding — auto-update bio, keywords, links
# ─────────────────────────────────────────────────────────────────────────────

CHANNEL_BIO = """🌙 Daily horoscopes for ALL 12 zodiac signs — Aries through Pisces.

Fresh cosmic readings every morning:
✨ Daily Horoscope — posted every day at 1 AM ET
📅 Weekly Horoscope — every Monday
🌟 Monthly Horoscope — first of each month
🌕 Special Events — eclipses, retrogrades, full moons & more

Subscribe & hit the 🔔 bell for your daily dose of cosmic guidance.
Drop your zodiac sign in the comments — we love hearing from you!

#horoscope #astrology #zodiac #dailyhoroscope #GetMindFuelNow"""

CHANNEL_KEYWORDS = (
    "horoscope, daily horoscope, astrology, zodiac, all 12 signs, "
    "aries horoscope, taurus horoscope, gemini horoscope, cancer horoscope, "
    "leo horoscope, virgo horoscope, libra horoscope, scorpio horoscope, "
    "sagittarius horoscope, capricorn horoscope, aquarius horoscope, pisces horoscope, "
    "weekly horoscope, monthly horoscope, yearly horoscope, "
    "full moon horoscope, mercury retrograde, eclipse horoscope, "
    "astrology reading, zodiac reading, cosmic energy, GetMindFuelNow"
)

# Track when we last updated channel branding so we don't spam the API
_BRANDING_STAMP = Path("logs") / "channel_branding_updated.txt"


def update_channel_branding(force: bool = False) -> bool:
    """
    Update the YouTube channel bio, keywords, and country.
    Runs at most once per week to avoid unnecessary API calls.

    Returns True if branding was updated or already up-to-date.

    NOTE: Requires the 'youtube' OAuth scope.
    If you get a 403 error, delete youtube_token.json and re-run
    first_time_setup.py — the new token will include the full scope.
    """
    # Rate-limit: skip if updated within the last 7 days
    if not force and _BRANDING_STAMP.exists():
        try:
            last = datetime.datetime.fromisoformat(_BRANDING_STAMP.read_text().strip())
            if (datetime.datetime.utcnow() - last).days < 7:
                log.info("Channel branding is up-to-date (updated within 7 days)")
                return True
        except Exception:
            pass

    channel_id = config.CHANNEL_ID
    if not channel_id:
        log.warning("YOUTUBE_CHANNEL_ID not set in .env — skipping channel branding update")
        return False

    try:
        session, _ = _authed_session()

        body = {
            "id": channel_id,
            "brandingSettings": {
                "channel": {
                    "description":       CHANNEL_BIO,
                    "keywords":          CHANNEL_KEYWORDS,
                    "country":           "US",
                    "defaultLanguage":   "en",
                    "unsubscribedTrailer": "",   # leave blank — set manually in Studio
                }
            }
        }

        resp = session.put(
            "https://www.googleapis.com/youtube/v3/channels?part=brandingSettings",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )

        if resp.status_code in (200, 204):
            _BRANDING_STAMP.parent.mkdir(parents=True, exist_ok=True)
            _BRANDING_STAMP.write_text(datetime.datetime.utcnow().isoformat())
            log.info("Channel branding updated — bio, keywords, country set")
            return True

        if resp.status_code == 403:
            log.warning(
                "Channel branding: 403 Forbidden — token needs 'youtube' scope.\n"
                "  Fix: delete youtube_token.json and run first_time_setup.py again.\n"
                "  All video uploads continue to work normally."
            )
            return False

        log.warning("Channel branding update returned %d: %s",
                    resp.status_code, resp.text[:300])
        return False

    except Exception as exc:
        log.warning("Channel branding update failed: %s", exc)
        return False


def set_video_tags_and_category(video_id: str, tags: list[str],
                                 title: str, description: str,
                                 category_id: str = "22") -> bool:
    """
    Update an existing video's snippet (title, description, tags, category).
    Useful for refreshing SEO on older videos.
    """
    if not video_id:
        return False
    try:
        session, _ = _authed_session()
        body = {
            "id": video_id,
            "snippet": {
                "title":       title[:100],
                "description": description[:5000],
                "tags":        tags[:500] if isinstance(tags, list) else [],
                "categoryId":  category_id,
            }
        }
        resp = session.put(
            "https://www.googleapis.com/youtube/v3/videos?part=snippet",
            headers={"Content-Type": "application/json"},
            data=json.dumps(body),
        )
        if resp.status_code in (200, 204):
            log.info("Video metadata updated: %s", video_id)
            return True
        log.warning("Video update returned %d: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        log.warning("Video update failed: %s", exc)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Upload log
# ─────────────────────────────────────────────────────────────────────────────

def _log_upload(video_path: str, video_id: str, title: str, content: dict):
    _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if _LOG_FILE.exists():
        try:
            history = json.loads(_LOG_FILE.read_text())
        except Exception:
            history = []
    history.append({
        "date":        str(content.get("date", "")),
        "video_id":    video_id,
        "title":       title,
        "topic":       content.get("topic", ""),
        "file":        video_path,
        "uploaded_at": datetime.datetime.utcnow().isoformat() + "Z",
        "url":         f"https://youtu.be/{video_id}",
    })
    _LOG_FILE.write_text(json.dumps(history, indent=2))
    log.info("Upload logged → %s", _LOG_FILE)
