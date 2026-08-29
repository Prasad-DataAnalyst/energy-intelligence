"""
YouTube uploader — handles OAuth2 authentication, video upload,
thumbnail setting, and playlist assignment via YouTube Data API v3.
"""
import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]

CATEGORY_IDS = {
    "Finance": "2",
    "News & Politics": "25",
    "Education": "27",
    "Science & Technology": "28",
    "Entertainment": "24",
}

UPLOAD_CHUNK_SIZE = 4 * 1024 * 1024  # 4MB chunks


@dataclass
class UploadConfig:
    title: str
    description: str
    tags: list[str]
    category: str = "Finance"
    privacy: str = "private"      # "private" → schedule → "public" for safety
    publish_at: Optional[datetime] = None    # UTC datetime for scheduled publish
    playlist_id: Optional[str] = None
    made_for_kids: bool = False
    language: str = "en"
    location: str = "United States"
    video_type: str = "weekday"   # "weekday" | "shorts" | "sunday" — used for playlist routing + manifest
    # What produced this video. Recorded so performance can be attributed
    # back to the choices that made it — otherwise the scoring system has
    # views with nothing to attach them to.
    script_style: str = ""
    script_hook: str = ""

    def _compliance_description(self) -> str:
        """
        Ensure the financial disclaimer, AI disclosure, and full legal/
        copyright block appear in every upload description — long-form AND
        Shorts (channel policy — must never be skipped) — while respecting
        YouTube's 5000-character description limit.
        """
        description = self.description
        footer_parts = [
            p for p in (settings.disclaimer_text, settings.ai_disclosure)
            if p not in description
        ]
        if settings.legal_footer_marker not in description:
            footer_parts.append(settings.legal_footer.strip())
        if not footer_parts:
            return description[:5000]
        footer = "\n\n" + "\n\n".join(footer_parts)
        return description[: 5000 - len(footer)].rstrip() + footer

    def to_youtube_body(self) -> dict:
        body = {
            "snippet": {
                "title": self.title[:100],
                "description": self._compliance_description(),
                "tags": self.tags[:500],
                "categoryId": CATEGORY_IDS.get(self.category, "28"),
                "defaultLanguage": self.language,
                "defaultAudioLanguage": self.language,
            },
            "status": {
                "privacyStatus": "private" if self.publish_at else self.privacy,
                "madeForKids": self.made_for_kids,
                "selfDeclaredMadeForKids": self.made_for_kids,
            },
        }
        if self.publish_at:
            # Must be in RFC 3339 format, expressed in UTC
            publish_at = self.publish_at
            if publish_at.tzinfo is not None:
                publish_at = publish_at.astimezone(timezone.utc)
            body["status"]["publishAt"] = publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            body["status"]["privacyStatus"] = "private"   # required when scheduling
        return body


@dataclass
class UploadResult:
    success: bool
    video_id: Optional[str]
    video_url: Optional[str]
    title: str
    upload_time_seconds: float
    file_size_mb: float
    error: Optional[str] = None
    quota_used: int = 1600          # default cost for an upload
    uploaded_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def youtube_url(self) -> Optional[str]:
        if self.video_id:
            return f"https://www.youtube.com/watch?v={self.video_id}"
        return None


def _get_authenticated_service():
    """Return authenticated YouTube Data API v3 service object."""
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build

        creds_path = settings.root_dir / "config" / "youtube_token.json"
        creds = None

        if creds_path.exists():
            creds = Credentials.from_authorized_user_file(str(creds_path), SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not settings.youtube_client_secrets.exists():
                    raise FileNotFoundError(
                        f"YouTube OAuth client secrets not found: {settings.youtube_client_secrets}\n"
                        "Download from Google Cloud Console → APIs & Services → Credentials"
                    )
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(settings.youtube_client_secrets), SCOPES
                )
                creds = flow.run_local_server(port=0)
            creds_path.write_text(creds.to_json())

        return build("youtube", "v3", credentials=creds)

    except ImportError as exc:
        raise ImportError(
            "Google API client libraries not installed. Run:\n"
            "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc


def upload_video(
    video_path: Path,
    config: UploadConfig,
    quota_tracker=None,
) -> UploadResult:
    """
    Upload a video to YouTube. Handles resumable uploads and retries.
    Checks quota before uploading.
    """
    if not video_path.exists():
        return UploadResult(
            success=False, video_id=None, video_url=None,
            title=config.title, upload_time_seconds=0,
            file_size_mb=0, error=f"Video file not found: {video_path}",
        )

    file_size = video_path.stat().st_size / (1024 * 1024)

    # Check quota
    if quota_tracker and not quota_tracker.can_upload():
        return UploadResult(
            success=False, video_id=None, video_url=None,
            title=config.title, upload_time_seconds=0, file_size_mb=file_size,
            error="Daily YouTube API quota exhausted — try again tomorrow",
        )

    logger.info("Uploading '%s' (%.1fMB) → YouTube", config.title, file_size)
    start_time = time.time()

    for attempt in range(settings.max_retries):
        try:
            from googleapiclient.http import MediaFileUpload

            youtube = _get_authenticated_service()
            body = config.to_youtube_body()

            media = MediaFileUpload(
                str(video_path),
                mimetype="video/mp4",
                chunksize=UPLOAD_CHUNK_SIZE,
                resumable=True,
            )

            request = youtube.videos().insert(
                part=",".join(body.keys()),
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    logger.debug("Upload progress: %d%%", progress)

            video_id = response["id"]
            elapsed = time.time() - start_time

            if quota_tracker:
                quota_tracker.record_upload(video_id, config.title)

            # Set thumbnail if available
            result = UploadResult(
                success=True,
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                title=config.title,
                upload_time_seconds=round(elapsed, 2),
                file_size_mb=round(file_size, 2),
            )

            logger.info("Upload complete: %s in %.1fs — %s", video_id, elapsed, result.youtube_url)
            return result

        except Exception as exc:
            wait = settings.retry_backoff_base ** attempt
            logger.warning("Upload attempt %d failed: %s — retry in %.1fs", attempt + 1, exc, wait)
            if attempt < settings.max_retries - 1:
                time.sleep(wait)
            else:
                return UploadResult(
                    success=False, video_id=None, video_url=None,
                    title=config.title, upload_time_seconds=time.time() - start_time,
                    file_size_mb=round(file_size, 2), error=str(exc),
                )

    return UploadResult(
        success=False, video_id=None, video_url=None,
        title=config.title, upload_time_seconds=0,
        file_size_mb=round(file_size, 2), error="Max retries exhausted",
    )


def set_thumbnail(video_id: str, thumbnail_path: Path) -> bool:
    """Set custom thumbnail for an uploaded video."""
    if not thumbnail_path.exists():
        logger.warning("Thumbnail not found: %s", thumbnail_path)
        return False
    try:
        from googleapiclient.http import MediaFileUpload
        youtube = _get_authenticated_service()
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg"),
        ).execute()
        logger.info("Thumbnail set for video %s", video_id)
        return True
    except Exception as exc:
        logger.error("Thumbnail upload failed: %s", exc)
        return False


def add_to_playlist(video_id: str, playlist_id: str) -> bool:
    """Add a video to a YouTube playlist."""
    try:
        youtube = _get_authenticated_service()
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            },
        ).execute()
        logger.info("Video %s added to playlist %s", video_id, playlist_id)
        return True
    except Exception as exc:
        logger.error("Playlist insert failed: %s", exc)
        return False


# ── YouTubeUploader class ─────────────────────────────────────────────────────

from config.settings import MIN_QUOTA_TO_UPLOAD  # noqa: E402 — single source of truth (1700)

UPLOAD_GAP_MINUTES = 30          # minimum gap between consecutive uploads
FAILED_QUEUE_PATH = settings.logs_dir / "failed_queue.json"


def _write_failed_queue(queue: list[dict]) -> None:
    """Atomically write the failed-upload queue (temp file + rename)."""
    FAILED_QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = FAILED_QUEUE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(queue, indent=2))
    os.replace(tmp, FAILED_QUEUE_PATH)


class YouTubeUploader:
    """Class-based YouTube uploader with quota enforcement and retry logic."""

    def __init__(self, quota_tracker=None):
        self._youtube = None
        self._quota = quota_tracker
        self._last_upload_at: Optional[float] = None  # epoch seconds

    def authenticate(self) -> bool:
        """Authenticate and store the YouTube service object. Returns True on success."""
        try:
            self._youtube = _get_authenticated_service()
            logger.info("YouTube authentication successful")
            return True
        except Exception as exc:
            logger.error("YouTube authentication failed: %s", exc)
            return False

    def _enforce_upload_gap(self) -> None:
        """Block if less than 30 minutes since last upload."""
        if self._last_upload_at is not None:
            elapsed = time.time() - self._last_upload_at
            gap = UPLOAD_GAP_MINUTES * 60
            if elapsed < gap:
                wait = gap - elapsed
                logger.info("Enforcing 30-min upload gap — sleeping %.0fs", wait)
                time.sleep(wait)

    def _check_quota(self) -> bool:
        """Return False if quota is too low to safely upload."""
        if self._quota:
            return self._quota.can_upload()
        return True

    def upload_main_video(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        thumbnail_path: Optional[Path] = None,
        publish_at: Optional[datetime] = None,
        playlist_id: Optional[str] = None,
    ) -> UploadResult:
        """
        Upload a main weekday/Sunday video.
        category_id=25 (News & Politics). Enforces 30-min upload gap.
        """
        if not self._check_quota():
            return UploadResult(
                success=False, video_id=None, video_url=None,
                title=title, upload_time_seconds=0, file_size_mb=0,
                error=f"Quota too low (< {MIN_QUOTA_TO_UPLOAD} units) — aborting",
            )

        self._enforce_upload_gap()

        config = UploadConfig(
            title=title[:100],
            description=description[:5000],
            tags=tags,
            category="News & Politics",   # category_id=25
            privacy="private",
            publish_at=publish_at,
            playlist_id=playlist_id,
        )

        result = upload_full(video_path, config, thumbnail_path, self._quota)
        if result.success:
            self._last_upload_at = time.time()
        else:
            self._queue_failed(video_path, config, thumbnail_path)
        return result

    def upload_short(
        self,
        video_path: Path,
        title: str,
        description: str,
        tags: list[str],
        thumbnail_path: Optional[Path] = None,
        publish_at: Optional[datetime] = None,
    ) -> UploadResult:
        """
        Upload a YouTube Short.
        Auto-appends #Shorts to title if missing.
        """
        if not self._check_quota():
            return UploadResult(
                success=False, video_id=None, video_url=None,
                title=title, upload_time_seconds=0, file_size_mb=0,
                error="Quota too low",
            )

        self._enforce_upload_gap()

        # Auto-append #Shorts
        if "#Shorts" not in title and "#shorts" not in title:
            title = (title + " #Shorts")[:100]

        config = UploadConfig(
            title=title,
            description=description[:5000],
            tags=tags + ["#Shorts", "Shorts", "YouTubeShorts"],
            category="News & Politics",
            privacy="private",
            publish_at=publish_at,
            video_type="shorts",
        )
        result = upload_full(video_path, config, thumbnail_path, self._quota)
        if result.success:
            self._last_upload_at = time.time()
            record_upload(result, config)   # manifest feeds the dead-man switch
        else:
            self._queue_failed(video_path, config, thumbnail_path)
        return result

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> bool:
        """Set a custom thumbnail for an uploaded video."""
        return set_thumbnail(video_id, thumbnail_path)

    def verify_upload_status(self, video_id: str) -> dict:
        """Check the processing status of an uploaded video."""
        try:
            if not self._youtube and not self.authenticate():
                return {"status": "error", "error": "YouTube authentication failed"}
            response = self._youtube.videos().list(
                part="status,processingDetails",
                id=video_id,
            ).execute()
            items = response.get("items", [])
            if not items:
                return {"status": "not_found", "video_id": video_id}
            item = items[0]
            return {
                "video_id": video_id,
                "privacy": item["status"].get("privacyStatus"),
                "upload_status": item["status"].get("uploadStatus"),
                "processing_status": item.get("processingDetails", {}).get("processingStatus"),
            }
        except Exception as exc:
            logger.error("verify_upload_status failed: %s", exc)
            return {"status": "error", "error": str(exc)}

    def retry_failed_upload(
        self,
        video_path: Path,
        config: "UploadConfig",
        thumbnail_path: Optional[Path] = None,
    ) -> UploadResult:
        """
        Retry a single failed upload once after a 15-minute wait.
        """
        logger.info("Waiting 15 minutes before retry upload...")
        time.sleep(15 * 60)
        result = upload_full(video_path, config, thumbnail_path, self._quota)
        if result.success:
            self._last_upload_at = time.time()
        return result

    def process_failed_queue(self) -> list[UploadResult]:
        """
        Load failed_queue.json, retry each entry once.
        Clears successfully retried entries from the queue.
        """
        if not FAILED_QUEUE_PATH.exists():
            return []

        try:
            queue = json.loads(FAILED_QUEUE_PATH.read_text())
        except Exception as exc:
            logger.error("Failed to read failed_queue.json: %s", exc)
            return []

        results = []
        remaining = []
        for entry in queue:
            video_path = Path(entry["video_path"])
            thumbnail_path = Path(entry["thumbnail_path"]) if entry.get("thumbnail_path") else None
            config = UploadConfig(
                title=entry["title"],
                description=entry.get("description", ""),
                tags=entry.get("tags", []),
                category=entry.get("category", "News & Politics"),
            )
            result = self.retry_failed_upload(video_path, config, thumbnail_path)
            results.append(result)
            if not result.success:
                remaining.append(entry)

        _write_failed_queue(remaining)
        logger.info("Failed queue processed: %d retried, %d remaining", len(results), len(remaining))
        return results

    def _queue_failed(
        self,
        video_path: Path,
        config: "UploadConfig",
        thumbnail_path: Optional[Path],
    ) -> None:
        """Append a failed upload to failed_queue.json for later retry."""
        try:
            queue = []
            if FAILED_QUEUE_PATH.exists():
                queue = json.loads(FAILED_QUEUE_PATH.read_text())
            queue.append({
                "video_path": str(video_path),
                "thumbnail_path": str(thumbnail_path) if thumbnail_path else None,
                "title": config.title,
                "description": config.description,
                "tags": config.tags,
                "category": config.category,
                "failed_at": datetime.now().isoformat(),
            })
            _write_failed_queue(queue)
            logger.info("Queued failed upload: %s", config.title[:60])
        except Exception as exc:
            logger.error("Failed to queue upload '%s': %s", config.title[:60], exc)


def upload_full(
    video_path: Path,
    config: UploadConfig,
    thumbnail_path: Optional[Path] = None,
    quota_tracker=None,
) -> UploadResult:
    """
    Full upload pipeline: upload video → set thumbnail → add to playlist.
    """
    result = upload_video(video_path, config, quota_tracker)

    if result.success and result.video_id:
        if thumbnail_path:
            set_thumbnail(result.video_id, thumbnail_path)
        if config.playlist_id:
            add_to_playlist(result.video_id, config.playlist_id)

    return result


# ── Upload Archive Manifest ───────────────────────────────────────────────────

_UPLOAD_MANIFEST_PATH = settings.logs_dir / "upload_manifest.jsonl"


def _attribution(config, field: str) -> str:
    """A string, or nothing. Never something json cannot serialise."""
    value = getattr(config, field, "")
    return value if isinstance(value, str) else ""


def record_upload(result: "UploadResult", config: "UploadConfig") -> None:
    """
    Append a JSONL record to the upload archive manifest on successful upload.
    Each line is a standalone JSON object for easy querying.
    """
    if not result.success or not result.video_id:
        return
    record = {
        "video_id": result.video_id,
        "title": config.title,
        "video_type": config.video_type,
        # Coerced, not just defaulted: a non-string here would make the whole
        # record unserialisable and drop it. The manifest is what the
        # dead-man switch reads, so losing an entry to an attribution field
        # would be a bad trade.
        "script_style": _attribution(config, "script_style"),
        "script_hook": _attribution(config, "script_hook"),
        "uploaded_at": datetime.now().isoformat(),
        "url": f"https://youtu.be/{result.video_id}",
        "playlist_id": config.playlist_id,
        "tags": config.tags[:10] if config.tags else [],
        "quota_units_used": result.quota_used,
    }
    try:
        _UPLOAD_MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_UPLOAD_MANIFEST_PATH, "a", encoding="utf-8") as f:
            import json as _json
            f.write(_json.dumps(record) + "\n")
        logger.info("Upload archived → manifest: %s (%s)", result.video_id, config.title[:50])
    except Exception as exc:
        logger.warning("Could not write upload manifest: %s", exc)


def load_upload_manifest() -> list[dict]:
    """
    Load all JSONL records from the upload manifest.
    Returns list of dicts, newest first (JSONL is append-order, so reverse).
    """
    import json as _json
    if not _UPLOAD_MANIFEST_PATH.exists():
        return []
    records: list[dict] = []
    try:
        for line in _UPLOAD_MANIFEST_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    records.append(_json.loads(line))
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("Could not read upload manifest: %s", exc)
    return list(reversed(records))
