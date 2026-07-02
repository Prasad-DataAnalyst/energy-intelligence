"""
channel_manager/playlist_manager.py — DriftWire326 Module 21
Auto-creates and manages the 6 DriftWire326 playlists.
Caches playlist IDs to logs/playlist_ids.json to avoid redundant API calls.
Adds each video to the correct playlist based on video_type and sunday_theme.
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

PLAYLIST_IDS_FILE = settings.logs_dir / "playlist_ids.json"

# Canonical playlist definitions — order is authoritative
PLAYLIST_DEFINITIONS: dict[str, dict] = {
    "Daily Market Recaps": {
        "description": (
            "Every weekday market briefing from @DriftWire326. "
            "Pre-market and post-market analysis, top movers, and economic data. "
            f"{settings.disclaimer_text}"
        ),
        "privacy": "public",
    },
    "Market Shorts": {
        "description": (
            "Quick 60-second market updates from @DriftWire326. "
            "Key numbers, biggest movers, and fast takes on what moved the market. "
            f"{settings.disclaimer_text}"
        ),
        "privacy": "public",
    },
    "Sunday: Investing 101": {
        "description": (
            "Weekly deep-dives into investment banking, equity markets, and Wall Street concepts "
            "explained for Gen Z and Millennial investors. @DriftWire326. "
            f"{settings.disclaimer_text}"
        ),
        "privacy": "public",
    },
    "Sunday: Insurance": {
        "description": (
            "Insurance and financial protection explained simply — life, health, auto, and more. "
            "@DriftWire326 Sunday series. "
            f"{settings.disclaimer_text}"
        ),
        "privacy": "public",
    },
    "Sunday: Savings & Wealth": {
        "description": (
            "Savings strategies, wealth-building habits, and long-term financial planning "
            "for everyday investors. @DriftWire326 Sunday series. "
            f"{settings.disclaimer_text}"
        ),
        "privacy": "public",
    },
    "Sunday: Special Topics": {
        "description": (
            "Rotating Sunday deep-dives: real estate, crypto, tax strategy, retirement, "
            "side income, and macro finance. @DriftWire326. "
            f"{settings.disclaimer_text}"
        ),
        "privacy": "public",
    },
}

# Maps video_type / sunday_theme → playlist name
_ROUTE_MAP: dict[str, str] = {
    "weekday": "Daily Market Recaps",
    "shorts": "Market Shorts",
    "short": "Market Shorts",
    "sunday_investment_banking": "Sunday: Investing 101",
    "sunday_insurance_protection": "Sunday: Insurance",
    "sunday_savings_wealth": "Sunday: Savings & Wealth",
    "sunday_rotating_bonus": "Sunday: Special Topics",
    # fallback for unspecified sunday theme
    "sunday": "Sunday: Special Topics",
}


@dataclass
class PlaylistInfo:
    name: str
    playlist_id: str
    created_at: str
    video_count: int = 0


def _get_youtube_service():
    """Return authenticated YouTube Data API v3 service (reuses uploader auth)."""
    from uploader.uploader import _get_authenticated_service
    return _get_authenticated_service()


class PlaylistManager:
    """Creates and manages DriftWire326's 6 canonical playlists."""

    def __init__(self, youtube_service=None):
        self._svc = youtube_service
        self._cached_ids: dict[str, str] = self._load_cached_ids()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load_cached_ids(self) -> dict[str, str]:
        """Load playlist name → ID mapping from disk."""
        if PLAYLIST_IDS_FILE.exists():
            try:
                return json.loads(PLAYLIST_IDS_FILE.read_text())
            except Exception as exc:
                logger.warning("Could not load playlist_ids.json: %s", exc)
        return {}

    def _save_cached_ids(self) -> None:
        """Atomically persist the playlist ID cache (temp file + rename)."""
        import os
        PLAYLIST_IDS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PLAYLIST_IDS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._cached_ids, indent=2))
        os.replace(tmp, PLAYLIST_IDS_FILE)
        logger.debug("Playlist IDs saved → %s", PLAYLIST_IDS_FILE)

    # ── API helpers ──────────────────────────────────────────────────────────

    def _service(self):
        if self._svc is None:
            self._svc = _get_youtube_service()
        return self._svc

    def _create_playlist(self, name: str, definition: dict) -> Optional[str]:
        """Create a single playlist via API. Returns playlist_id or None."""
        try:
            body = {
                "snippet": {
                    "title": name,
                    "description": definition["description"],
                    "defaultLanguage": "en",
                },
                "status": {
                    "privacyStatus": definition.get("privacy", "public"),
                },
            }
            response = self._service().playlists().insert(
                part="snippet,status", body=body
            ).execute()
            pid = response["id"]
            logger.info("Created playlist '%s' → %s", name, pid)
            return pid
        except Exception as exc:
            logger.error("Failed to create playlist '%s': %s", name, exc)
            return None

    def _fetch_existing_playlists(self) -> dict[str, str]:
        """Fetch all channel playlists and return name → id dict."""
        found: dict[str, str] = {}
        try:
            request = self._service().playlists().list(
                part="snippet",
                mine=True,
                maxResults=50,
            )
            while request:
                response = request.execute()
                for item in response.get("items", []):
                    found[item["snippet"]["title"]] = item["id"]
                request = self._service().playlists().list_next(request, response)
        except Exception as exc:
            logger.error("Failed to fetch existing playlists: %s", exc)
        return found

    # ── Public interface ─────────────────────────────────────────────────────

    def ensure_playlists(self) -> dict[str, str]:
        """
        Ensure all 6 playlists exist on the channel.
        Creates any that are missing. Updates cache. Returns name → id map.
        """
        existing = self._fetch_existing_playlists()
        for name, definition in PLAYLIST_DEFINITIONS.items():
            if name in existing:
                self._cached_ids[name] = existing[name]
                logger.debug("Playlist exists: '%s' → %s", name, existing[name])
            elif name not in self._cached_ids:
                pid = self._create_playlist(name, definition)
                if pid:
                    self._cached_ids[name] = pid
        self._save_cached_ids()
        return dict(self._cached_ids)

    def get_playlist_id(self, name: str) -> Optional[str]:
        """Return cached playlist ID for a given playlist name."""
        return self._cached_ids.get(name)

    def add_video_to_playlist(self, video_id: str, playlist_id: str) -> bool:
        """
        Add video_id to playlist_id via playlistItems.insert.
        Returns True on success.
        """
        try:
            body = {
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                }
            }
            self._service().playlistItems().insert(
                part="snippet", body=body
            ).execute()
            logger.info("Added video %s → playlist %s", video_id, playlist_id)
            return True
        except Exception as exc:
            logger.error("Failed to add video %s to playlist %s: %s", video_id, playlist_id, exc)
            return False

    def route_video_to_playlist(
        self,
        video_id: str,
        video_type: str,
        sunday_theme: Optional[str] = None,
    ) -> Optional[str]:
        """
        Route a video to the correct playlist based on video_type and sunday_theme.

        video_type: "weekday" | "shorts" | "sunday"
        sunday_theme: "investment_banking" | "insurance_protection" |
                      "savings_wealth" | "rotating_bonus"

        Returns playlist_id if successful, None otherwise.
        """
        # Build lookup key
        if video_type == "sunday" and sunday_theme:
            key = f"sunday_{sunday_theme}"
        else:
            key = video_type

        playlist_name = _ROUTE_MAP.get(key) or _ROUTE_MAP.get("sunday")
        if not playlist_name:
            logger.warning("No playlist route for video_type='%s' theme='%s'", video_type, sunday_theme)
            return None

        playlist_id = self.get_playlist_id(playlist_name)
        if not playlist_id:
            # Attempt to create/fetch playlists first
            logger.info("Playlist '%s' not in cache — calling ensure_playlists()", playlist_name)
            self.ensure_playlists()
            playlist_id = self.get_playlist_id(playlist_name)

        if not playlist_id:
            logger.error("Could not resolve playlist ID for '%s'", playlist_name)
            return None

        success = self.add_video_to_playlist(video_id, playlist_id)
        return playlist_id if success else None

    def get_playlist_summary(self) -> list[dict]:
        """Return a list of all known playlists with their IDs and names."""
        return [
            {"name": name, "playlist_id": pid, "url": f"https://www.youtube.com/playlist?list={pid}"}
            for name, pid in self._cached_ids.items()
        ]
