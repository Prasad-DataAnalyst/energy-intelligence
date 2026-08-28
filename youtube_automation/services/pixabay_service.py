"""
services/pixabay_service.py
Layer: Service Wrapper
Safety: upgradeable
Description: Pixabay API wrapper for royalty-free music and video.
"""
from __future__ import annotations
import logging
import os
import urllib.request
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

MUSIC_BASE = "https://pixabay.com/api/videos/"  # Pixabay uses same endpoint for music type


def is_available() -> bool:
    return bool(os.getenv("PIXABAY_API_KEY", ""))


def search_music(mood: str, duration_min: int = 60, duration_max: int = 300) -> Optional[dict]:
    """Find a royalty-free music track matching the mood."""
    if not is_available():
        return None
    params = {
        "key": os.getenv("PIXABAY_API_KEY"),
        "q": mood,
        "video_type": "film",
        "category": "music",
        "per_page": 10,
        "min_duration": duration_min,
        "max_duration": duration_max,
    }
    try:
        resp = requests.get(MUSIC_BASE, params=params, timeout=10)
        resp.raise_for_status()
        hits = resp.json().get("hits", [])
        return hits[0] if hits else None
    except Exception as e:
        log.warning("Pixabay music search failed for '%s': %s", mood, e)
        return None


def download_music(hit: dict, out_path: str) -> Optional[str]:
    if not hit:
        return None
    try:
        videos = hit.get("videos", {})
        medium = videos.get("medium", {}).get("url") or videos.get("small", {}).get("url")
        if not medium:
            return None
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(medium, out_path)
        log.info("Pixabay music downloaded: %s", out_path)
        return out_path
    except Exception as e:
        log.warning("Pixabay download failed: %s", e)
        return None


def get_music_for_mood(mood: str, out_path: str, duration_s: int = 300) -> Optional[str]:
    hit = search_music(mood, duration_min=max(30, duration_s - 60))
    if hit:
        return download_music(hit, out_path)
    return None
