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

    def to_youtube_body(self) -> dict:
        body = {
            "snippet": {
                "title": self.title[:100],
                "description": self.description[:5000],
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
            # Must be in RFC 3339 format
            body["status"]["publishAt"] = self.publish_at.strftime("%Y-%m-%dT%H:%M:%S.000Z")
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
