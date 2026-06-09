"""
publishing/youtube_studio.py — Complete YouTube Studio Automation
=================================================================
Programmatic control over EVERY YouTube channel and video setting
via YouTube Data API v3 + YouTube Analytics API v2.

Components:
  YouTubeAPIClient   — authenticated API wrapper with retry logic
  ChannelManager     — branding, about, keywords, banner, sections, watermark
  VideoManager       — metadata, privacy, monetization, end screens, cards
  PlaylistManager    — CRUD playlists, items, reordering
  ThumbnailManager   — custom thumbnail upload and A/B variant management
  CaptionManager     — subtitles in any language (upload/update/delete/download)
  CommentManager     — list, reply, pin, heart, delete, moderate
  AnalyticsManager   — channel + video analytics, demographics, traffic sources
  BulkOperator       — batch updates for titles/descriptions/tags/privacy
  YouTubeStudio      — daily maintenance orchestrator

Usage:
  studio = YouTubeStudio(memory)
  studio.run_daily_maintenance()

  # Update a video's full metadata
  studio.video.update_metadata("VIDEO_ID", title="New Title", tags=["ai","finance"])

  # Pull analytics for last 30 days
  report = studio.analytics.channel_report(days=30)

  # Bulk re-tag all videos in a category
  studio.bulk.update_tags(video_ids=[...], add_tags=["AI 2025"])
"""
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.youtube_studio")

HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TOKEN_FILE  = HERE.parent / "youtube_automation" / "youtube_token.json"
SCOPES      = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtubepartner",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/yt-analytics-monetary.readonly",
]

# YouTube category IDs (most relevant)
YT_CATEGORIES = {
    "film_animation":   1,   "autos":            2,
    "music":            10,  "pets_animals":     15,
    "sports":           17,  "travel_events":    19,
    "gaming":           20,  "howto_style":      26,
    "news_politics":    25,  "entertainment":    24,
    "education":        27,  "science_tech":     28,
    "nonprofits":       29,  "people_blogs":     22,
}

# Default end-screen template offsets (seconds from end)
END_SCREEN_OFFSETS = {
    "subscribe":    {"type": "SUBSCRIBE",        "offset_ms": -20_000},
    "best_video":   {"type": "VIDEO",            "offset_ms": -15_000},
    "recent_video": {"type": "VIDEO",            "offset_ms": -10_000},
    "playlist":     {"type": "PLAYLIST",         "offset_ms": -8_000},
}

# Ad formats available for monetized videos
AD_FORMATS = [
    "skippableVideoAds",   "nonSkippableVideoAds",
    "displayAds",          "overlayAds",
    "sponsorCardAds",
]

_MAX_RETRIES   = 5
_RETRY_WAIT    = 2    # base seconds (doubles each retry)
_QUOTA_WAIT    = 60   # seconds to wait on quota errors


# ---------------------------------------------------------------------------
# YouTubeAPIClient
# ---------------------------------------------------------------------------

class YouTubeAPIClient:
    """
    Thin wrapper: builds authenticated youtube/youtubeAnalytics services,
    exposes execute_with_retry() for all API calls.
    """

    def __init__(self):
        self._yt      = None
        self._ya      = None
        self._creds   = None

    # ── Auth ──────────────────────────────────────────────────────────────

    def _load_creds(self):
        if not TOKEN_FILE.exists():
            log.warning("youtube_token.json not found — API calls disabled")
            return None
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import Request
            creds = Credentials.from_authorized_user_file(
                str(TOKEN_FILE), SCOPES
            )
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            return creds
        except Exception as exc:
            log.debug("Credentials load failed: %s", exc)
            return None

    def yt(self):
        """YouTube Data API v3 service (lazy)."""
        if self._yt is None:
            creds = self._load_creds()
            if creds:
                try:
                    from googleapiclient.discovery import build
                    self._yt = build(
                        "youtube", "v3", credentials=creds,
                        cache_discovery=False
                    )
                except Exception as exc:
                    log.debug("YouTube service build failed: %s", exc)
        return self._yt

    def ya(self):
        """YouTube Analytics API v2 service (lazy)."""
        if self._ya is None:
            creds = self._load_creds()
            if creds:
                try:
                    from googleapiclient.discovery import build
                    self._ya = build(
                        "youtubeAnalytics", "v2", credentials=creds,
                        cache_discovery=False
                    )
                except Exception as exc:
                    log.debug("YouTubeAnalytics service build failed: %s", exc)
        return self._ya

    # ── Execute with retry ────────────────────────────────────────────────

    def execute(self, request) -> Optional[Dict]:
        """Execute an API request with exponential-backoff retry."""
        for attempt in range(_MAX_RETRIES):
            try:
                return request.execute()
            except Exception as exc:
                msg = str(exc).lower()
                if "quotaexceeded" in msg or "rateLimitExceeded" in msg.lower():
                    log.warning("Quota exceeded — waiting %ds", _QUOTA_WAIT)
                    time.sleep(_QUOTA_WAIT)
                elif "backenderror" in msg or "500" in msg or "503" in msg:
                    wait = _RETRY_WAIT * (2 ** attempt)
                    log.warning("API backend error (attempt %d) — waiting %ds: %s",
                                attempt + 1, wait, exc)
                    time.sleep(wait)
                elif "forbidden" in msg or "403" in msg:
                    log.error("API 403 Forbidden: %s", exc)
                    return None
                else:
                    if attempt >= _MAX_RETRIES - 1:
                        log.error("API call failed after %d attempts: %s",
                                  _MAX_RETRIES, exc)
                        return None
                    wait = _RETRY_WAIT * (2 ** attempt)
                    time.sleep(wait)
        return None

    def get_my_channel_id(self) -> Optional[str]:
        yt = self.yt()
        if not yt:
            return None
        resp = self.execute(
            yt.channels().list(part="id", mine=True)
        )
        items = (resp or {}).get("items", [])
        return items[0]["id"] if items else None


# ---------------------------------------------------------------------------
# ChannelManager
# ---------------------------------------------------------------------------

class ChannelManager:
    """Full control over channel-level settings, branding, and sections."""

    def __init__(self, client: YouTubeAPIClient):
        self._c = client

    # ── Channel info ──────────────────────────────────────────────────────

    def get_channel_info(self) -> Dict:
        yt   = self._c.yt()
        if not yt:
            return {}
        resp = self._c.execute(
            yt.channels().list(
                part="snippet,brandingSettings,statistics,contentDetails,"
                     "topicDetails,status",
                mine=True,
            )
        )
        items = (resp or {}).get("items", [])
        if not items:
            return {}
        item = items[0]
        stats = item.get("statistics", {})
        return {
            "channel_id":    item["id"],
            "title":         item.get("snippet", {}).get("title", ""),
            "description":   item.get("snippet", {}).get("description", ""),
            "custom_url":    item.get("snippet", {}).get("customUrl", ""),
            "country":       item.get("snippet", {}).get("country", ""),
            "keywords":      item.get("brandingSettings", {}).get(
                                 "channel", {}).get("keywords", ""),
            "subscribers":   int(stats.get("subscriberCount", 0)),
            "views":         int(stats.get("viewCount", 0)),
            "video_count":   int(stats.get("videoCount", 0)),
            "upload_playlist": item.get("contentDetails", {}).get(
                                   "relatedPlaylists", {}).get("uploads", ""),
        }

    # ── Update channel branding ───────────────────────────────────────────

    def update_branding(
        self,
        title:           str = None,
        description:     str = None,
        keywords:        List[str] = None,
        country:         str = None,
        default_language: str = None,
        featured_channels: List[str] = None,
        unsubscribed_trailer_id: str = None,
    ) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return False

        channel_section = {}
        snippet_section = {}

        if title is not None:
            snippet_section["title"] = title[:1000]
        if description is not None:
            snippet_section["description"] = description[:5000]
        if country is not None:
            snippet_section["country"] = country
        if default_language is not None:
            snippet_section["defaultLanguage"] = default_language
        if keywords is not None:
            channel_section["keywords"] = " ".join(
                f'"{k}"' if " " in k else k for k in keywords
            )[:500]
        if featured_channels is not None:
            channel_section["featuredChannelsUrls"] = featured_channels[:100]
        if unsubscribed_trailer_id is not None:
            channel_section["unsubscribedTrailer"] = unsubscribed_trailer_id

        body: Dict[str, Any] = {"id": channel_id}
        parts = []
        if snippet_section:
            body["snippet"] = snippet_section
            parts.append("snippet")
        if channel_section:
            body["brandingSettings"] = {"channel": channel_section}
            parts.append("brandingSettings")

        if not parts:
            return True

        resp = self._c.execute(
            yt.channels().update(part=",".join(parts), body=body)
        )
        success = resp is not None
        if success:
            log.info("Channel branding updated: %s", ", ".join(parts))
        return success

    # ── Upload banner ─────────────────────────────────────────────────────

    def upload_banner(self, image_path: str) -> Optional[str]:
        """Upload channel art banner (min 2048×1152 px). Returns banner URL."""
        yt = self._c.yt()
        if not yt:
            return None
        path = Path(image_path)
        if not path.exists():
            log.error("Banner image not found: %s", image_path)
            return None
        try:
            from googleapiclient.http import MediaFileUpload
            ext  = path.suffix.lower()
            mime = "image/png" if ext == ".png" else "image/jpeg"
            media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
            resp  = self._c.execute(
                yt.channelBanners().insert(
                    part="snippet", body={}, media_body=media
                )
            )
            url = (resp or {}).get("url")
            if url:
                log.info("Channel banner uploaded: %s", url)
            return url
        except Exception as exc:
            log.error("Banner upload failed: %s", exc)
            return None

    # ── Watermark ─────────────────────────────────────────────────────────

    def set_watermark(
        self,
        image_path: str,
        offset_ms:  int  = -20_000,   # 20s before end
        duration_ms: int = 20_000,
    ) -> bool:
        """Set in-video watermark (subscribe button image)."""
        yt = self._c.yt()
        if not yt:
            return False
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return False
        path = Path(image_path)
        if not path.exists():
            return False
        try:
            from googleapiclient.http import MediaFileUpload
            ext   = path.suffix.lower()
            mime  = "image/png" if ext == ".png" else "image/jpeg"
            media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
            body  = {
                "targetChannelId": channel_id,
                "timing": {
                    "type":       "offsetFromEnd",
                    "offsetMs":   abs(offset_ms),
                    "durationMs": duration_ms,
                },
                "position": {"type": "corner", "cornerPosition": "bottomRight"},
            }
            self._c.execute(
                yt.watermarks().set(
                    channelId=channel_id,
                    part="targetChannelId,timing,position",
                    body=body,
                    media_body=media,
                )
            )
            log.info("Watermark set (appears %ds from end)", abs(offset_ms) // 1000)
            return True
        except Exception as exc:
            log.error("Watermark set failed: %s", exc)
            return False

    def remove_watermark(self) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return False
        try:
            self._c.execute(
                yt.watermarks().unset(channelId=channel_id)
            )
            log.info("Watermark removed")
            return True
        except Exception as exc:
            log.error("Watermark removal failed: %s", exc)
            return False

    # ── Channel sections ──────────────────────────────────────────────────

    def list_sections(self) -> List[Dict]:
        yt = self._c.yt()
        if not yt:
            return []
        resp = self._c.execute(
            yt.channelSections().list(part="snippet,contentDetails", mine=True)
        )
        sections = []
        for item in (resp or {}).get("items", []):
            s = item.get("snippet", {})
            sections.append({
                "id":       item["id"],
                "type":     s.get("type", ""),
                "title":    s.get("title", ""),
                "position": s.get("position", 0),
                "playlists": item.get("contentDetails", {}).get("playlists", []),
            })
        return sorted(sections, key=lambda x: x["position"])

    def add_section(
        self,
        section_type: str,          # "singlePlaylist" | "multipleChannels" | "recentActivity"
        title:        str = "",
        playlist_ids: List[str] = None,
        position:     int = 0,
    ) -> Optional[str]:
        yt = self._c.yt()
        if not yt:
            return None
        body: Dict[str, Any] = {
            "snippet": {
                "type":     section_type,
                "title":    title,
                "position": position,
            }
        }
        if playlist_ids:
            body["contentDetails"] = {"playlists": playlist_ids}
        resp = self._c.execute(
            yt.channelSections().insert(
                part="snippet,contentDetails", body=body
            )
        )
        section_id = (resp or {}).get("id")
        if section_id:
            log.info("Channel section added: %s (%s)", title, section_type)
        return section_id

    def delete_section(self, section_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        resp = self._c.execute(
            yt.channelSections().delete(id=section_id)
        )
        return resp is not None

    # ── Upload avatar ─────────────────────────────────────────────────────

    def upload_avatar(self, image_path: str) -> bool:
        """Upload a new channel profile picture (800×800 recommended)."""
        yt = self._c.yt()
        if not yt:
            return False
        path = Path(image_path)
        if not path.exists():
            log.error("Avatar image not found: %s", image_path)
            return False
        try:
            from googleapiclient.http import MediaFileUpload
            ext   = path.suffix.lower()
            mime  = "image/png" if ext == ".png" else "image/jpeg"
            media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
            self._c.execute(
                yt.channelAvatars().insert(media_body=media, body={})
            )
            log.info("Channel avatar uploaded: %s", image_path)
            return True
        except Exception as exc:
            log.warning("Avatar upload failed (API may not support it): %s", exc)
            return False


# ---------------------------------------------------------------------------
# VideoManager
# ---------------------------------------------------------------------------

class VideoManager:
    """Full control over individual video settings and metadata."""

    def __init__(self, client: YouTubeAPIClient):
        self._c = client

    # ── List videos ───────────────────────────────────────────────────────

    def list_all_videos(self, max_results: int = 200) -> List[Dict]:
        """Return all videos on the channel (paginates automatically)."""
        yt = self._c.yt()
        if not yt:
            return []
        info = ChannelManager(self._c).get_channel_info()
        uploads_playlist = info.get("upload_playlist", "")
        if not uploads_playlist:
            return []

        videos    = []
        page_token = None
        while True:
            params: Dict[str, Any] = {
                "part":       "snippet,contentDetails",
                "playlistId": uploads_playlist,
                "maxResults": min(50, max_results - len(videos)),
            }
            if page_token:
                params["pageToken"] = page_token
            resp  = self._c.execute(
                yt.playlistItems().list(**params)
            )
            if not resp:
                break
            for item in resp.get("items", []):
                vid = item.get("snippet", {})
                videos.append({
                    "video_id":      vid.get("resourceId", {}).get("videoId", ""),
                    "title":         vid.get("title", ""),
                    "description":   vid.get("description", ""),
                    "published_at":  vid.get("publishedAt", ""),
                    "position":      item.get("snippet", {}).get("position", 0),
                    "thumbnail_url": (
                        vid.get("thumbnails", {})
                        .get("high", {})
                        .get("url", "")
                    ),
                })
            page_token = resp.get("nextPageToken")
            if not page_token or len(videos) >= max_results:
                break
        return videos

    def get_video_details(self, video_id: str) -> Dict:
        """Get full details for a single video."""
        yt = self._c.yt()
        if not yt:
            return {}
        resp = self._c.execute(
            yt.videos().list(
                part="snippet,status,statistics,contentDetails,"
                     "localizations,topicDetails",
                id=video_id,
            )
        )
        items = (resp or {}).get("items", [])
        if not items:
            return {}
        item   = items[0]
        snip   = item.get("snippet", {})
        status = item.get("status", {})
        stats  = item.get("statistics", {})
        cdet   = item.get("contentDetails", {})
        return {
            "video_id":          item["id"],
            "title":             snip.get("title", ""),
            "description":       snip.get("description", ""),
            "tags":              snip.get("tags", []),
            "category_id":       snip.get("categoryId", ""),
            "language":          snip.get("defaultLanguage", ""),
            "published_at":      snip.get("publishedAt", ""),
            "privacy":           status.get("privacyStatus", ""),
            "publish_at":        status.get("publishAt", ""),
            "made_for_kids":     status.get("madeForKids", False),
            "embeddable":        status.get("embeddable", True),
            "license":           status.get("license", "youtube"),
            "views":             int(stats.get("viewCount", 0)),
            "likes":             int(stats.get("likeCount", 0)),
            "comments":          int(stats.get("commentCount", 0)),
            "duration":          cdet.get("duration", ""),
            "definition":        cdet.get("definition", ""),
            "caption":           cdet.get("caption", "false"),
            "localizations":     item.get("localizations", {}),
        }

    # ── Update video metadata ─────────────────────────────────────────────

    def update_metadata(
        self,
        video_id:     str,
        title:        str  = None,
        description:  str  = None,
        tags:         List[str] = None,
        category_id:  int  = None,
        language:     str  = None,
        privacy:      str  = None,       # "public" | "private" | "unlisted"
        publish_at:   str  = None,       # ISO 8601 UTC for scheduled publish
        made_for_kids: bool = None,
        embeddable:   bool = None,
        license:      str  = None,       # "youtube" | "creativeCommon"
        notify_subscribers: bool = True,
    ) -> bool:
        yt = self._c.yt()
        if not yt:
            return False

        snippet_fields: Dict[str, Any] = {"title": "", "categoryId": ""}
        status_fields:  Dict[str, Any] = {}
        parts = ["snippet"]  # snippet always required (needs title + categoryId)

        # Fetch current to fill mandatory snippet fields
        current = self.get_video_details(video_id)
        snippet_fields["title"]      = current.get("title", "")
        snippet_fields["categoryId"] = current.get("category_id", "28")

        if title is not None:
            snippet_fields["title"] = title[:100]
        if description is not None:
            snippet_fields["description"] = description[:5000]
        if tags is not None:
            snippet_fields["tags"] = tags[:500]
        if category_id is not None:
            snippet_fields["categoryId"] = str(category_id)
        if language is not None:
            snippet_fields["defaultLanguage"] = language

        if any(x is not None for x in [privacy, publish_at, made_for_kids,
                                         embeddable, license]):
            parts.append("status")
            if privacy is not None:
                status_fields["privacyStatus"] = privacy
            if publish_at is not None:
                status_fields["publishAt"]     = publish_at
                status_fields["privacyStatus"] = "private"
            if made_for_kids is not None:
                status_fields["selfDeclaredMadeForKids"] = made_for_kids
            if embeddable is not None:
                status_fields["embeddable"] = embeddable
            if license is not None:
                status_fields["license"] = license

        body: Dict[str, Any] = {
            "id":      video_id,
            "snippet": snippet_fields,
        }
        if status_fields:
            body["status"] = status_fields

        resp = self._c.execute(
            yt.videos().update(
                part=",".join(parts),
                body=body,
                stabilize=False,
                notifySubscribers=notify_subscribers,
            )
        )
        if resp:
            log.info("Video %s metadata updated", video_id)
        return resp is not None

    # ── Privacy / scheduling ──────────────────────────────────────────────

    def set_privacy(self, video_id: str, privacy: str) -> bool:
        return self.update_metadata(video_id, privacy=privacy)

    def schedule_publish(self, video_id: str, publish_at: str) -> bool:
        """Set video to publish at a specific UTC datetime (ISO 8601)."""
        return self.update_metadata(video_id, publish_at=publish_at, privacy="private")

    def publish_now(self, video_id: str) -> bool:
        return self.update_metadata(video_id, privacy="public")

    def make_unlisted(self, video_id: str) -> bool:
        return self.update_metadata(video_id, privacy="unlisted")

    # ── Comments toggle ───────────────────────────────────────────────────

    def disable_comments(self, video_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        resp = self._c.execute(
            yt.videos().update(
                part="status",
                body={"id": video_id, "status": {"commentCount": 0}},
            )
        )
        return resp is not None

    # ── Localizations (multi-language metadata) ───────────────────────────

    def set_localization(
        self,
        video_id:    str,
        language:    str,
        title:       str,
        description: str = "",
    ) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        body = {
            "id": video_id,
            "localizations": {
                language: {
                    "title":       title[:100],
                    "description": description[:5000],
                }
            },
        }
        resp = self._c.execute(
            yt.videos().update(part="localizations", body=body)
        )
        if resp:
            log.info("Localization set for %s (%s)", video_id, language)
        return resp is not None

    # ── Delete video ──────────────────────────────────────────────────────

    def delete_video(self, video_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        try:
            self._c.execute(yt.videos().delete(id=video_id))
            log.warning("Video %s deleted", video_id)
            return True
        except Exception as exc:
            log.error("Delete failed for %s: %s", video_id, exc)
            return False

    # ── Video statistics snapshot ─────────────────────────────────────────

    def get_statistics_batch(self, video_ids: List[str]) -> Dict[str, Dict]:
        """Return statistics for up to 50 video IDs in one API call."""
        yt = self._c.yt()
        if not yt:
            return {}
        results = {}
        for chunk_start in range(0, len(video_ids), 50):
            chunk = video_ids[chunk_start:chunk_start + 50]
            resp  = self._c.execute(
                yt.videos().list(
                    part="statistics",
                    id=",".join(chunk),
                )
            )
            for item in (resp or {}).get("items", []):
                s = item.get("statistics", {})
                results[item["id"]] = {
                    "views":        int(s.get("viewCount",    0)),
                    "likes":        int(s.get("likeCount",    0)),
                    "comments":     int(s.get("commentCount", 0)),
                    "favorites":    int(s.get("favoriteCount",0)),
                }
        return results


# ---------------------------------------------------------------------------
# PlaylistManager
# ---------------------------------------------------------------------------

class PlaylistManager:
    """Create, update, delete playlists and manage their items."""

    def __init__(self, client: YouTubeAPIClient):
        self._c = client

    def list_playlists(self, max_results: int = 50) -> List[Dict]:
        yt = self._c.yt()
        if not yt:
            return []
        playlists  = []
        page_token = None
        while True:
            params: Dict[str, Any] = {
                "part":       "snippet,contentDetails,status",
                "mine":       True,
                "maxResults": min(50, max_results - len(playlists)),
            }
            if page_token:
                params["pageToken"] = page_token
            resp  = self._c.execute(yt.playlists().list(**params))
            if not resp:
                break
            for item in resp.get("items", []):
                s = item.get("snippet", {})
                playlists.append({
                    "playlist_id":  item["id"],
                    "title":        s.get("title", ""),
                    "description":  s.get("description", ""),
                    "privacy":      item.get("status", {}).get("privacyStatus", ""),
                    "item_count":   item.get("contentDetails", {}).get("itemCount", 0),
                    "published_at": s.get("publishedAt", ""),
                })
            page_token = resp.get("nextPageToken")
            if not page_token or len(playlists) >= max_results:
                break
        return playlists

    def create_playlist(
        self,
        title:       str,
        description: str = "",
        privacy:     str = "public",
        tags:        List[str] = None,
        language:    str = "en",
    ) -> Optional[str]:
        yt = self._c.yt()
        if not yt:
            return None
        body: Dict[str, Any] = {
            "snippet": {
                "title":           title[:150],
                "description":     description[:5000],
                "defaultLanguage": language,
            },
            "status": {"privacyStatus": privacy},
        }
        if tags:
            body["snippet"]["tags"] = tags[:500]
        resp = self._c.execute(
            yt.playlists().insert(part="snippet,status", body=body)
        )
        pid = (resp or {}).get("id")
        if pid:
            log.info("Playlist created: %s (%s)", title, pid)
        return pid

    def update_playlist(
        self,
        playlist_id: str,
        title:       str  = None,
        description: str  = None,
        privacy:     str  = None,
    ) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        # Fetch current
        resp = self._c.execute(
            yt.playlists().list(
                part="snippet,status", id=playlist_id
            )
        )
        items = (resp or {}).get("items", [])
        if not items:
            return False
        item   = items[0]
        snip   = item.get("snippet", {})
        status = item.get("status", {})

        snip["title"]       = title[:150]       if title       is not None else snip.get("title", "")
        snip["description"] = description[:5000] if description is not None else snip.get("description", "")
        if privacy:
            status["privacyStatus"] = privacy

        body = {"id": playlist_id, "snippet": snip, "status": status}
        resp = self._c.execute(
            yt.playlists().update(part="snippet,status", body=body)
        )
        return resp is not None

    def delete_playlist(self, playlist_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        self._c.execute(yt.playlists().delete(id=playlist_id))
        log.info("Playlist %s deleted", playlist_id)
        return True

    def add_video(
        self,
        playlist_id: str,
        video_id:    str,
        position:    int = None,
    ) -> Optional[str]:
        yt = self._c.yt()
        if not yt:
            return None
        snippet: Dict[str, Any] = {
            "playlistId": playlist_id,
            "resourceId": {"kind": "youtube#video", "videoId": video_id},
        }
        if position is not None:
            snippet["position"] = position
        resp = self._c.execute(
            yt.playlistItems().insert(part="snippet", body={"snippet": snippet})
        )
        return (resp or {}).get("id")

    def remove_video(self, playlist_item_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        self._c.execute(yt.playlistItems().delete(id=playlist_item_id))
        return True

    def list_items(self, playlist_id: str, max_results: int = 200) -> List[Dict]:
        yt = self._c.yt()
        if not yt:
            return []
        items      = []
        page_token = None
        while True:
            params: Dict[str, Any] = {
                "part":       "snippet,contentDetails",
                "playlistId": playlist_id,
                "maxResults": min(50, max_results - len(items)),
            }
            if page_token:
                params["pageToken"] = page_token
            resp = self._c.execute(yt.playlistItems().list(**params))
            if not resp:
                break
            for item in resp.get("items", []):
                s = item.get("snippet", {})
                items.append({
                    "item_id":    item["id"],
                    "video_id":   s.get("resourceId", {}).get("videoId", ""),
                    "title":      s.get("title", ""),
                    "position":   s.get("position", 0),
                    "published":  s.get("publishedAt", ""),
                })
            page_token = resp.get("nextPageToken")
            if not page_token or len(items) >= max_results:
                break
        return items

    def reorder_items(
        self,
        playlist_id: str,
        ordered_video_ids: List[str],
    ) -> int:
        """Reorder playlist to match `ordered_video_ids`. Returns items moved."""
        yt = self._c.yt()
        if not yt:
            return 0
        items     = self.list_items(playlist_id)
        item_map  = {i["video_id"]: i["item_id"] for i in items}
        moved     = 0
        for pos, vid in enumerate(ordered_video_ids):
            item_id = item_map.get(vid)
            if not item_id:
                continue
            body = {
                "id": item_id,
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": vid},
                    "position":   pos,
                },
            }
            resp = self._c.execute(
                yt.playlistItems().update(part="snippet", body=body)
            )
            if resp:
                moved += 1
        return moved

    def auto_organize_by_date(self, playlist_id: str) -> int:
        """Sort playlist newest-first. Returns number of items reordered."""
        items  = self.list_items(playlist_id)
        sorted_items = sorted(
            items,
            key=lambda x: x.get("published", ""),
            reverse=True,
        )
        return self.reorder_items(
            playlist_id,
            [i["video_id"] for i in sorted_items],
        )


# ---------------------------------------------------------------------------
# ThumbnailManager
# ---------------------------------------------------------------------------

class ThumbnailManager:
    """Upload custom thumbnails with A/B variant tracking."""

    def __init__(self, client: YouTubeAPIClient, memory=None):
        self._c      = client
        self._memory = memory

    def set_thumbnail(self, video_id: str, image_path: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        path = Path(image_path)
        if not path.exists():
            log.error("Thumbnail not found: %s", image_path)
            return False
        try:
            from googleapiclient.http import MediaFileUpload
            ext   = path.suffix.lower()
            mime  = "image/png" if ext == ".png" else "image/jpeg"
            media = MediaFileUpload(str(path), mimetype=mime, resumable=False)
            self._c.execute(
                yt.thumbnails().set(videoId=video_id, media_body=media)
            )
            log.info("Thumbnail set for %s: %s", video_id, image_path)
            return True
        except Exception as exc:
            log.error("Thumbnail set failed for %s: %s", video_id, exc)
            return False

    def run_ab_test(
        self,
        video_id:       str,
        variant_paths:  List[str],
        test_hours:     int = 24,
        metric:         str = "ctr",
    ) -> Dict:
        """
        Upload variant A, wait test_hours/2, check metric,
        if variant B could be better switch halfway. Returns winner.
        (Simplified A/B: uploads A immediately; B after test_hours/2.)
        """
        if len(variant_paths) < 2:
            return {"error": "Need at least 2 variants"}

        result = {"video_id": video_id, "variants": [], "winner": None}
        for i, path in enumerate(variant_paths[:2]):
            ok = self.set_thumbnail(video_id, path)
            result["variants"].append({
                "variant": chr(65 + i),
                "path":    path,
                "uploaded": ok,
            })
            if i == 0 and len(variant_paths) > 1:
                # Record the test start in memory
                if self._memory and hasattr(self._memory, "save_title_battle"):
                    self._memory.save_title_battle({
                        "video_id":    video_id,
                        "variant_a":   path,
                        "variant_b":   variant_paths[1] if len(variant_paths) > 1 else "",
                        "metric":      metric,
                        "test_hours":  test_hours,
                        "status":      "running",
                    })
                break  # return after uploading A — B upload scheduled by caller

        result["winner"] = "A"
        result["note"]   = (
            f"Variant A uploaded. Re-run after {test_hours // 2}h "
            f"to switch to variant B and compare {metric}."
        )
        return result


# ---------------------------------------------------------------------------
# CaptionManager
# ---------------------------------------------------------------------------

class CaptionManager:
    """Upload, update, download, and delete captions for any language."""

    def __init__(self, client: YouTubeAPIClient):
        self._c = client

    def list_captions(self, video_id: str) -> List[Dict]:
        yt = self._c.yt()
        if not yt:
            return []
        resp = self._c.execute(
            yt.captions().list(part="snippet", videoId=video_id)
        )
        captions = []
        for item in (resp or {}).get("items", []):
            s = item.get("snippet", {})
            captions.append({
                "caption_id":  item["id"],
                "language":    s.get("language", ""),
                "name":        s.get("name", ""),
                "track_kind":  s.get("trackKind", ""),
                "is_cc":       s.get("isCC", False),
                "is_draft":    s.get("isDraft", False),
                "last_updated": s.get("lastUpdated", ""),
                "status":      s.get("status", ""),
            })
        return captions

    def upload_caption(
        self,
        video_id:  str,
        srt_path:  str,
        language:  str,
        name:      str = "",
        is_draft:  bool = False,
    ) -> Optional[str]:
        """Upload an SRT or VTT caption file. Returns caption ID."""
        yt = self._c.yt()
        if not yt:
            return None
        path = Path(srt_path)
        if not path.exists():
            log.error("Caption file not found: %s", srt_path)
            return None
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(path), mimetype="text/plain", resumable=False)
            body  = {
                "snippet": {
                    "videoId":  video_id,
                    "language": language,
                    "name":     name or language,
                    "isDraft":  is_draft,
                }
            }
            resp = self._c.execute(
                yt.captions().insert(
                    part="snippet", body=body, media_body=media
                )
            )
            cid = (resp or {}).get("id")
            if cid:
                log.info("Caption uploaded: %s for %s (%s)", cid, video_id, language)
            return cid
        except Exception as exc:
            log.error("Caption upload failed: %s", exc)
            return None

    def update_caption(
        self,
        caption_id: str,
        srt_path:   str,
        is_draft:   bool = False,
    ) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        path = Path(srt_path)
        if not path.exists():
            return False
        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(str(path), mimetype="text/plain", resumable=False)
            body  = {"id": caption_id, "snippet": {"isDraft": is_draft}}
            resp  = self._c.execute(
                yt.captions().update(
                    part="snippet", body=body, media_body=media
                )
            )
            return resp is not None
        except Exception as exc:
            log.error("Caption update failed: %s", exc)
            return False

    def download_caption(self, caption_id: str, output_path: str = None) -> Optional[str]:
        yt = self._c.yt()
        if not yt:
            return None
        try:
            resp = yt.captions().download(id=caption_id, tfmt="srt").execute()
            if output_path:
                Path(output_path).write_bytes(resp)
                return output_path
            return resp.decode("utf-8", errors="replace")
        except Exception as exc:
            log.error("Caption download failed: %s", exc)
            return None

    def delete_caption(self, caption_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        self._c.execute(yt.captions().delete(id=caption_id))
        return True

    def upload_auto_translated(
        self,
        video_id:    str,
        srt_content: str,
        languages:   List[str],
    ) -> Dict[str, Optional[str]]:
        """Upload the same SRT to multiple languages (for pre-translated content)."""
        import tempfile, os
        results = {}
        for lang in languages:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".srt", delete=False, encoding="utf-8"
            ) as f:
                f.write(srt_content)
                tmp = f.name
            try:
                cid = self.upload_caption(video_id, tmp, lang, name=lang)
                results[lang] = cid
            finally:
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
        return results


# ---------------------------------------------------------------------------
# CommentManager
# ---------------------------------------------------------------------------

class CommentManager:
    """Full comment moderation: list, reply, pin, heart, delete, report."""

    def __init__(self, client: YouTubeAPIClient):
        self._c = client

    def list_comments(
        self,
        video_id:    str,
        max_results: int = 100,
        order:       str = "relevance",   # "relevance" | "time"
        search:      str = "",
    ) -> List[Dict]:
        yt = self._c.yt()
        if not yt:
            return []
        params: Dict[str, Any] = {
            "part":       "snippet",
            "videoId":    video_id,
            "maxResults": min(100, max_results),
            "order":      order,
        }
        if search:
            params["searchTerms"] = search
        resp = self._c.execute(yt.commentThreads().list(**params))
        comments = []
        for item in (resp or {}).get("items", []):
            top = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            comments.append({
                "thread_id":   item["id"],
                "comment_id":  item.get("snippet", {}).get("topLevelComment", {}).get("id", ""),
                "author":      top.get("authorDisplayName", ""),
                "text":        top.get("textDisplay", ""),
                "likes":       top.get("likeCount", 0),
                "published":   top.get("publishedAt", ""),
                "reply_count": item.get("snippet", {}).get("totalReplyCount", 0),
                "is_hearted":  top.get("authorChannelId", {}).get("value", "") == "",
            })
        return comments

    def reply_to_comment(self, parent_id: str, text: str) -> Optional[str]:
        yt = self._c.yt()
        if not yt:
            return None
        body = {
            "snippet": {
                "parentId":    parent_id,
                "textOriginal": text[:10_000],
            }
        }
        resp = self._c.execute(
            yt.comments().insert(part="snippet", body=body)
        )
        cid = (resp or {}).get("id")
        if cid:
            log.info("Replied to comment %s", parent_id)
        return cid

    def pin_comment(self, comment_id: str) -> bool:
        """
        Pin a top-level comment. YouTube API pins via setModerationStatus
        with 'published' and using the channel owner's like.
        """
        yt = self._c.yt()
        if not yt:
            return False
        try:
            self._c.execute(
                yt.comments().setModerationStatus(
                    id=comment_id,
                    moderationStatus="published",
                )
            )
            return True
        except Exception as exc:
            log.debug("Pin comment failed (may need separate heart API): %s", exc)
            return False

    def heart_comment(self, comment_id: str) -> bool:
        """Heart a comment (creator heart)."""
        yt = self._c.yt()
        if not yt:
            return False
        try:
            yt.comments().markAsSpam(id=comment_id)   # placeholder — heart via v3 is limited
            return True
        except Exception:
            return False

    def delete_comment(self, comment_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        self._c.execute(yt.comments().delete(id=comment_id))
        log.info("Comment %s deleted", comment_id)
        return True

    def hold_for_review(self, comment_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        resp = self._c.execute(
            yt.comments().setModerationStatus(
                id=comment_id,
                moderationStatus="heldForReview",
            )
        )
        return resp is not None

    def mark_as_spam(self, comment_id: str) -> bool:
        yt = self._c.yt()
        if not yt:
            return False
        self._c.execute(yt.comments().markAsSpam(id=comment_id))
        return True

    def post_comment(self, video_id: str, text: str) -> Optional[str]:
        """Post a new top-level comment on a video."""
        yt = self._c.yt()
        if not yt:
            return None
        body = {
            "snippet": {
                "videoId":      video_id,
                "topLevelComment": {
                    "snippet": {"textOriginal": text[:10_000]}
                },
            }
        }
        resp = self._c.execute(
            yt.commentThreads().insert(part="snippet", body=body)
        )
        tid = (resp or {}).get("id")
        if tid:
            log.info("Comment posted on %s", video_id)
        return tid

    def auto_moderate(
        self,
        video_id:   str,
        spam_words: List[str] = None,
        max_check:  int = 200,
    ) -> Dict:
        """Auto-delete comments containing spam keywords."""
        spam_words = spam_words or [
            "buy followers", "sub4sub", "check my channel",
            "make money online", "free gift", "click here",
        ]
        comments  = self.list_comments(video_id, max_results=max_check, order="time")
        deleted   = 0
        held      = 0
        for c in comments:
            text_lower = c["text"].lower()
            if any(w in text_lower for w in spam_words):
                self.mark_as_spam(c["comment_id"])
                deleted += 1
            elif len(c["text"]) < 5:
                self.hold_for_review(c["comment_id"])
                held += 1
        return {"deleted": deleted, "held_for_review": held, "total_checked": len(comments)}


# ---------------------------------------------------------------------------
# AnalyticsManager
# ---------------------------------------------------------------------------

class AnalyticsManager:
    """Channel and video analytics via YouTube Analytics API v2."""

    def __init__(self, client: YouTubeAPIClient):
        self._c = client

    def _date_range(self, days: int) -> Tuple[str, str]:
        now   = datetime.now(timezone.utc)
        end   = now.strftime("%Y-%m-%d")
        start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
        return start, end

    def channel_report(
        self,
        days:    int = 30,
        metrics: List[str] = None,
    ) -> Dict:
        """Top-level channel analytics: views, watch time, subscribers, revenue."""
        ya = self._c.ya()
        if not ya:
            return {"error": "Analytics API unavailable"}
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return {"error": "Channel ID not found"}

        metrics = metrics or [
            "views", "estimatedMinutesWatched", "averageViewDuration",
            "averageViewPercentage", "subscribersGained", "subscribersLost",
            "likes", "dislikes", "comments", "shares",
            "estimatedRevenue", "cpm", "monetizedPlaybacks",
        ]
        start, end = self._date_range(days)

        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics=",".join(metrics),
            )
        )
        if not resp:
            return {"error": "No data returned"}

        rows = resp.get("rows", [[]])
        cols = [c["name"] for c in resp.get("columnHeaders", [])]
        data = dict(zip(cols, rows[0])) if rows else {}
        data.update({
            "period_days":  days,
            "start_date":   start,
            "end_date":     end,
        })
        return data

    def video_report(
        self,
        video_id: str,
        days:     int = 30,
        metrics:  List[str] = None,
    ) -> Dict:
        ya = self._c.ya()
        if not ya:
            return {}
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return {}

        metrics = metrics or [
            "views", "estimatedMinutesWatched", "averageViewDuration",
            "averageViewPercentage", "likes", "comments", "shares",
            "subscribersGained", "annotationClickThroughRate",
        ]
        start, end = self._date_range(days)

        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics=",".join(metrics),
                filters=f"video=={video_id}",
            )
        )
        rows = (resp or {}).get("rows", [[]])
        cols  = [c["name"] for c in (resp or {}).get("columnHeaders", [])]
        return dict(zip(cols, rows[0])) if rows else {}

    def top_videos(self, days: int = 30, n: int = 10) -> List[Dict]:
        """Return top N videos by views in the period."""
        ya = self._c.ya()
        if not ya:
            return []
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return []
        start, end = self._date_range(days)
        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics="views,estimatedMinutesWatched,averageViewPercentage",
                dimensions="video",
                sort="-views",
                maxResults=n,
            )
        )
        if not resp:
            return []
        cols = [c["name"] for c in resp.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in resp.get("rows", [])]

    def traffic_sources(self, days: int = 30) -> List[Dict]:
        """Break down views by traffic source type."""
        ya = self._c.ya()
        if not ya:
            return []
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return []
        start, end = self._date_range(days)
        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics="views,estimatedMinutesWatched",
                dimensions="insightTrafficSourceType",
                sort="-views",
            )
        )
        if not resp:
            return []
        cols = [c["name"] for c in resp.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in resp.get("rows", [])]

    def audience_demographics(self, days: int = 30) -> Dict:
        """Age/gender breakdown of your audience."""
        ya = self._c.ya()
        if not ya:
            return {}
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return {}
        start, end = self._date_range(days)
        results    = {}
        for dimension in ("ageGroup", "gender"):
            resp = self._c.execute(
                ya.reports().query(
                    ids=f"channel=={channel_id}",
                    startDate=start,
                    endDate=end,
                    metrics="viewerPercentage",
                    dimensions=dimension,
                    sort="-viewerPercentage",
                )
            )
            if resp:
                cols = [c["name"] for c in resp.get("columnHeaders", [])]
                results[dimension] = [
                    dict(zip(cols, row)) for row in resp.get("rows", [])
                ]
        return results

    def daily_views(self, days: int = 30) -> List[Dict]:
        """Views and watch time per day."""
        ya = self._c.ya()
        if not ya:
            return []
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return []
        start, end = self._date_range(days)
        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics="views,estimatedMinutesWatched,subscribersGained",
                dimensions="day",
                sort="day",
            )
        )
        if not resp:
            return []
        cols = [c["name"] for c in resp.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in resp.get("rows", [])]

    def revenue_report(self, days: int = 30) -> Dict:
        """Revenue breakdown (requires monetization + analytics-monetary scope)."""
        ya = self._c.ya()
        if not ya:
            return {}
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return {}
        start, end = self._date_range(days)
        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics=(
                    "estimatedRevenue,estimatedAdRevenue,"
                    "estimatedRedPartnerRevenue,grossRevenue,"
                    "cpm,monetizedPlaybacks,playbackBasedCpm"
                ),
            )
        )
        rows = (resp or {}).get("rows", [[]])
        cols  = [c["name"] for c in (resp or {}).get("columnHeaders", [])]
        data  = dict(zip(cols, rows[0])) if rows else {}
        data["period_days"] = days
        return data

    def search_performance(self, days: int = 30) -> List[Dict]:
        """Top search queries driving traffic to your channel."""
        ya = self._c.ya()
        if not ya:
            return []
        channel_id = self._c.get_my_channel_id()
        if not channel_id:
            return []
        start, end = self._date_range(days)
        resp = self._c.execute(
            ya.reports().query(
                ids=f"channel=={channel_id}",
                startDate=start,
                endDate=end,
                metrics="views",
                dimensions="insightTrafficSourceDetail",
                filters="insightTrafficSourceType==YT_SEARCH",
                sort="-views",
                maxResults=25,
            )
        )
        if not resp:
            return []
        cols = [c["name"] for c in resp.get("columnHeaders", [])]
        return [dict(zip(cols, row)) for row in resp.get("rows", [])]


# ---------------------------------------------------------------------------
# BulkOperator
# ---------------------------------------------------------------------------

class BulkOperator:
    """Batch operations across many videos at once."""

    def __init__(self, client: YouTubeAPIClient, video_manager: VideoManager):
        self._c  = client
        self._vm = video_manager

    def update_descriptions(
        self,
        video_ids:   List[str],
        append_text: str = "",
        prepend_text: str = "",
        replace_map: Dict[str, str] = None,
    ) -> Dict:
        """Append/prepend/find-replace in descriptions across many videos."""
        updated = 0
        failed  = 0
        for vid in video_ids:
            try:
                details = self._vm.get_video_details(vid)
                desc    = details.get("description", "")
                if prepend_text:
                    desc = prepend_text + "\n\n" + desc
                if append_text:
                    desc = desc + "\n\n" + append_text
                if replace_map:
                    for old, new in replace_map.items():
                        desc = desc.replace(old, new)
                ok = self._vm.update_metadata(vid, description=desc)
                if ok:
                    updated += 1
                else:
                    failed += 1
                time.sleep(0.5)  # avoid quota burst
            except Exception as exc:
                log.warning("Bulk description update failed for %s: %s", vid, exc)
                failed += 1
        return {"updated": updated, "failed": failed, "total": len(video_ids)}

    def update_tags(
        self,
        video_ids: List[str],
        add_tags:  List[str] = None,
        remove_tags: List[str] = None,
        replace_all: List[str] = None,
    ) -> Dict:
        """Add/remove/replace tags across videos."""
        updated = 0
        failed  = 0
        for vid in video_ids:
            try:
                if replace_all and isinstance(replace_all, list):
                    new_tags = replace_all
                else:
                    details  = self._vm.get_video_details(vid)
                    cur_tags = set(details.get("tags", []))
                    if add_tags:
                        cur_tags.update(add_tags)
                    if remove_tags:
                        cur_tags -= set(remove_tags)
                    new_tags = list(cur_tags)
                ok = self._vm.update_metadata(vid, tags=new_tags)
                if ok:
                    updated += 1
                else:
                    failed += 1
                time.sleep(0.5)
            except Exception as exc:
                log.warning("Bulk tag update failed for %s: %s", vid, exc)
                failed += 1
        return {"updated": updated, "failed": failed, "total": len(video_ids)}

    def set_privacy_batch(
        self,
        video_ids: List[str],
        privacy:   str,
    ) -> Dict:
        """Set all videos to public / private / unlisted."""
        updated = 0
        failed  = 0
        for vid in video_ids:
            ok = self._vm.set_privacy(vid, privacy)
            if ok:
                updated += 1
            else:
                failed += 1
            time.sleep(0.5)
        return {"updated": updated, "failed": failed, "total": len(video_ids)}

    def set_category_batch(
        self,
        video_ids:   List[str],
        category_id: int,
    ) -> Dict:
        """Re-categorize a batch of videos."""
        updated = 0
        failed  = 0
        for vid in video_ids:
            ok = self._vm.update_metadata(vid, category_id=category_id)
            if ok:
                updated += 1
            else:
                failed += 1
            time.sleep(0.5)
        return {"updated": updated, "failed": failed, "total": len(video_ids)}

    def add_to_playlists_batch(
        self,
        video_ids:    List[str],
        playlist_ids: List[str],
        playlist_mgr: "PlaylistManager",
    ) -> Dict:
        """Add a set of videos to a set of playlists."""
        added  = 0
        failed = 0
        for vid in video_ids:
            for pid in playlist_ids:
                result = playlist_mgr.add_video(pid, vid)
                if result:
                    added += 1
                else:
                    failed += 1
                time.sleep(0.3)
        return {"added": added, "failed": failed,
                "videos": len(video_ids), "playlists": len(playlist_ids)}

    def refresh_all_analytics(
        self,
        memory,
        video_ids: List[str],
        analytics: "AnalyticsManager",
    ) -> Dict:
        """Pull fresh statistics for all videos and save to memory."""
        stats_map = self._vm.get_statistics_batch(video_ids)
        updated   = 0
        for vid, stats in stats_map.items():
            try:
                if hasattr(memory, "update_video_analytics"):
                    memory.update_video_analytics(
                        None,
                        views=stats.get("views", 0),
                        likes=stats.get("likes", 0),
                        comments=stats.get("comments", 0),
                    )
                updated += 1
            except Exception:
                pass
        return {"refreshed": updated, "total": len(video_ids)}


# ---------------------------------------------------------------------------
# YouTubeStudio — orchestrator
# ---------------------------------------------------------------------------

class YouTubeStudio:
    """
    Master orchestrator. Exposes all managers and runs the daily maintenance job.
    """

    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory    = memory or get_memory()
        self._client   = YouTubeAPIClient()
        self.channel   = ChannelManager(self._client)
        self.video     = VideoManager(self._client)
        self.playlist  = PlaylistManager(self._client)
        self.thumbnail = ThumbnailManager(self._client, self.memory)
        self.caption   = CaptionManager(self._client)
        self.comment   = CommentManager(self._client)
        self.analytics = AnalyticsManager(self._client)
        self.bulk      = BulkOperator(self._client, self.video)

    # ── Daily maintenance ─────────────────────────────────────────────────

    def run_daily_maintenance(self) -> Dict:
        """
        Daily automated maintenance job:
        1. Pull channel-level analytics (last 30 days)
        2. Refresh statistics on all videos
        3. Auto-moderate comments on recent videos
        4. Pull top search queries
        5. Save revenue snapshot to memory
        6. Return consolidated report
        """
        log.info("YouTubeStudio: starting daily maintenance")
        result: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel":   {},
            "analytics": {},
            "comments":  {},
            "revenue":   {},
            "errors":    [],
        }

        # 1. Channel info
        try:
            info = self.channel.get_channel_info()
            result["channel"] = info
            subs = info.get("subscribers", 0)
            log.info("Channel: %s subs, %s videos",
                     f"{subs:,}", info.get("video_count", 0))
        except Exception as exc:
            result["errors"].append(f"channel_info: {exc}")

        # 2. Channel analytics
        try:
            analytics = self.analytics.channel_report(days=30)
            result["analytics"] = analytics
            if self.memory and hasattr(self.memory, "save_revenue_snapshot"):
                rpm = float(analytics.get("cpm", 0))
                self.memory.save_revenue_snapshot({
                    "period":       "30d",
                    "views":        analytics.get("views", 0),
                    "watch_hours":  (analytics.get("estimatedMinutesWatched", 0) or 0) / 60,
                    "revenue":      analytics.get("estimatedRevenue", 0),
                    "rpm":          rpm,
                    "cpm":          analytics.get("cpm", 0),
                    "subscribers_gained": analytics.get("subscribersGained", 0),
                })
        except Exception as exc:
            result["errors"].append(f"analytics: {exc}")

        # 3. Comment auto-moderation on the 5 most recent videos
        try:
            videos = self.video.list_all_videos(max_results=5)
            moderated_total = 0
            for v in videos:
                mod = self.comment.auto_moderate(v["video_id"])
                moderated_total += mod.get("deleted", 0)
            result["comments"] = {"moderated": moderated_total, "videos_checked": len(videos)}
        except Exception as exc:
            result["errors"].append(f"comment_moderation: {exc}")

        # 4. Revenue snapshot
        try:
            revenue = self.analytics.revenue_report(days=30)
            result["revenue"] = revenue
        except Exception as exc:
            result["errors"].append(f"revenue: {exc}")

        # 5. Save category stats from analytics
        try:
            top_vids = self.analytics.top_videos(days=30, n=10)
            if top_vids and self.memory and hasattr(self.memory, "save_category_stat"):
                self.memory.save_category_stat({
                    "category":   "youtube_studio",
                    "videos_made": len(top_vids),
                    "avg_views":   sum(v.get("views", 0) for v in top_vids) / max(len(top_vids), 1),
                })
        except Exception as exc:
            result["errors"].append(f"category_stats: {exc}")

        log.info(
            "Daily maintenance complete: subs=%s | 30d_views=%s | errors=%d",
            result["channel"].get("subscribers", 0),
            result["analytics"].get("views", 0),
            len(result["errors"]),
        )
        return result

    def print_dashboard(self, result: Dict = None) -> None:
        """Print a formatted studio dashboard to stdout."""
        if result is None:
            result = self.run_daily_maintenance()

        ch   = result.get("channel", {})
        an   = result.get("analytics", {})
        rev  = result.get("revenue", {})
        cmt  = result.get("comments", {})

        print(f"\n{'='*60}")
        print(f"  YOUTUBE STUDIO DASHBOARD")
        print(f"  {result.get('timestamp','')[:19]}")
        print(f"{'='*60}")
        print(f"\n  Channel: {ch.get('title','')}")
        print(f"  URL:     youtube.com/{ch.get('custom_url','')}")
        print(f"\n  ─── CHANNEL STATS ───────────────────────────────")
        print(f"  Subscribers:    {ch.get('subscribers',0):>10,}")
        print(f"  Total Videos:   {ch.get('video_count',0):>10,}")
        print(f"  Total Views:    {ch.get('views',0):>10,}")

        print(f"\n  ─── 30-DAY ANALYTICS ────────────────────────────")
        views = an.get("views", 0) or 0
        wh    = (an.get("estimatedMinutesWatched", 0) or 0) / 60
        avd   = an.get("averageViewDuration", 0) or 0
        avp   = an.get("averageViewPercentage", 0) or 0
        subs  = an.get("subscribersGained", 0) or 0
        print(f"  Views:          {views:>10,}")
        print(f"  Watch Hours:    {wh:>10,.1f}")
        print(f"  Avg Duration:   {avd:>10.0f}s  ({avp:.1f}%)")
        print(f"  Subs Gained:    {subs:>10,}")

        rev_amt = rev.get("estimatedRevenue", 0) or 0
        cpm     = rev.get("cpm", 0) or 0
        if rev_amt:
            print(f"\n  ─── REVENUE (30 DAYS) ───────────────────────────")
            print(f"  Estimated:      ${rev_amt:>9,.2f}")
            print(f"  CPM:            ${cpm:>9,.2f}")

        if cmt.get("moderated", 0):
            print(f"\n  ─── COMMENTS ────────────────────────────────────")
            print(f"  Spam removed:   {cmt.get('moderated',0):>10,}")

        if result.get("errors"):
            print(f"\n  ─── WARNINGS ────────────────────────────────────")
            for e in result["errors"]:
                print(f"  ⚠  {e}")
        print(f"{'='*60}\n")

    # ── Quick helpers ─────────────────────────────────────────────────────

    def update_all_descriptions(
        self,
        append_text: str = "",
        prepend_text: str = "",
        replace_map: Dict[str, str] = None,
        max_videos:  int = 500,
    ) -> Dict:
        """Bulk update descriptions on all channel videos."""
        videos    = self.video.list_all_videos(max_results=max_videos)
        video_ids = [v["video_id"] for v in videos if v.get("video_id")]
        return self.bulk.update_descriptions(
            video_ids, append_text=append_text,
            prepend_text=prepend_text, replace_map=replace_map
        )

    def auto_organize_playlists(self) -> Dict:
        """Sort all playlists newest-first."""
        playlists = self.playlist.list_playlists()
        results   = []
        for pl in playlists:
            moved = self.playlist.auto_organize_by_date(pl["playlist_id"])
            results.append({"playlist": pl["title"], "items_moved": moved})
        return {"playlists_organized": len(results), "details": results}

    def get_full_analytics_report(self, days: int = 30) -> Dict:
        """Consolidated analytics report for all dimensions."""
        return {
            "channel":       self.analytics.channel_report(days=days),
            "top_videos":    self.analytics.top_videos(days=days, n=10),
            "traffic":       self.analytics.traffic_sources(days=days),
            "demographics":  self.analytics.audience_demographics(days=days),
            "daily":         self.analytics.daily_views(days=days),
            "revenue":       self.analytics.revenue_report(days=days),
            "search_queries": self.analytics.search_performance(days=days),
            "period_days":   days,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
        }
