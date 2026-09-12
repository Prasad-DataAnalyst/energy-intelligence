"""
publishing/youtube_publisher.py — YouTube Data API v3 Uploader
===============================================================
Uploads video files to YouTube with:
  - Resumable upload (handles large files / timeouts)
  - Scheduled publishing (privacyStatus=private + publishAt)
  - Custom thumbnail upload
  - Full metadata (title, description, tags, category)
  - Retry on transient errors
"""
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.youtube_publisher")

HERE = Path(__file__).parent.parent


class YouTubePublisher:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    def _yt_service(self):
        """Build authenticated YouTube Data API v3 service."""
        token_file = HERE.parent / "youtube_automation" / "youtube_token.json"
        if not token_file.exists():
            log.warning("No youtube_token.json — upload unavailable")
            return None
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build

            creds = Credentials.from_authorized_user_file(str(token_file))
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            return build("youtube", "v3", credentials=creds,
                         cache_discovery=False)
        except Exception as e:
            log.debug(f"YouTube service build failed: {e}")
            return None

    # ── Upload ─────────────────────────────────────────────────────────────

    def upload(self, video_path: str, metadata: Dict,
                thumbnail: Optional[str] = None,
                schedule_at: Optional[str] = None) -> Optional[str]:
        """
        Upload video to YouTube. Returns YouTube video ID or None.

        schedule_at: ISO 8601 datetime string (UTC) for scheduled publishing.
                     If None, publishes immediately as public.
        """
        if not video_path or not Path(video_path).exists():
            log.error(f"Video file not found: {video_path}")
            return None

        yt = self._yt_service()
        if not yt:
            log.warning("YouTube service unavailable — skipping upload")
            return None

        title       = metadata.get("title", "")[:100]
        description = metadata.get("description", "")[:5000]
        tags        = metadata.get("tags", [])[:500]
        category    = str(metadata.get("category_id", "28"))  # 28 = Science & Technology

        if schedule_at:
            status = {"privacyStatus": "private",
                      "publishAt": schedule_at,
                      "selfDeclaredMadeForKids": False}
        else:
            status = {"privacyStatus": "public",
                      "selfDeclaredMadeForKids": False}

        body = {
            "snippet": {
                "title":       title,
                "description": description,
                "tags":        tags,
                "categoryId":  category,
                "defaultLanguage": "en",
            },
            "status": status,
        }

        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(
                video_path,
                mimetype="video/mp4",
                chunksize=8 * 1024 * 1024,   # 8 MB chunks
                resumable=True,
            )

            request  = yt.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            retry    = 0
            log.info(f"Uploading: {title[:60]!r} ({Path(video_path).stat().st_size // 1024 // 1024} MB)")
            while response is None:
                try:
                    status_obj, response = request.next_chunk()
                    if status_obj:
                        pct = int(status_obj.progress() * 100)
                        log.info(f"  Upload progress: {pct}%")
                except Exception as e:
                    if retry >= 5:
                        raise
                    wait = 2 ** retry
                    log.warning(f"  Upload chunk failed (retry {retry}): {e}. Waiting {wait}s…")
                    time.sleep(wait)
                    retry += 1

            yt_id = response["id"]
            log.info(f"Uploaded: https://youtu.be/{yt_id}")

        except Exception as e:
            log.error(f"Upload failed: {e}")
            return None

        # Set thumbnail
        if thumbnail and Path(thumbnail).exists():
            self._set_thumbnail(yt, yt_id, thumbnail)

        return yt_id

    def _set_thumbnail(self, yt, video_id: str, thumbnail_path: str) -> bool:
        try:
            from googleapiclient.http import MediaFileUpload
            ext     = Path(thumbnail_path).suffix.lower()
            mime    = "image/png" if ext == ".png" else "image/jpeg"
            media   = MediaFileUpload(thumbnail_path, mimetype=mime, resumable=False)
            yt.thumbnails().set(videoId=video_id, media_body=media).execute()
            log.info(f"  Thumbnail set: {thumbnail_path}")
            return True
        except Exception as e:
            log.warning(f"  Thumbnail set failed: {e}")
            return False

    # ── Playlist management ────────────────────────────────────────────────

    def add_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        yt = self._yt_service()
        if not yt:
            return False
        try:
            yt.playlistItems().insert(
                part="snippet",
                body={"snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }},
            ).execute()
            log.info(f"  Added {video_id} → playlist {playlist_id}")
            return True
        except Exception as e:
            log.warning(f"  Playlist add failed: {e}")
            return False

    # ── Status check ───────────────────────────────────────────────────────

    def get_video_status(self, video_id: str) -> Dict:
        yt = self._yt_service()
        if not yt:
            return {}
        try:
            r = yt.videos().list(part="status,statistics", id=video_id).execute()
            items = r.get("items", [])
            if not items:
                return {}
            item = items[0]
            return {
                "status":     item.get("status", {}).get("privacyStatus", ""),
                "views":      int(item.get("statistics", {}).get("viewCount", 0)),
                "likes":      int(item.get("statistics", {}).get("likeCount", 0)),
                "comments":   int(item.get("statistics", {}).get("commentCount", 0)),
            }
        except Exception as e:
            log.debug(f"Video status failed: {e}")
            return {}
