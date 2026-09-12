"""
services/pexels_service.py
Layer: Service Wrapper
Safety: upgradeable
Description: Pexels API wrapper for royalty-free stock footage search and download.
Input:  query (str), duration constraints
Output: local path to downloaded video file
"""
from __future__ import annotations
import logging
import os
import time
import urllib.request
from pathlib import Path
from typing import Optional

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://api.pexels.com/videos"


def is_available() -> bool:
    return bool(os.getenv("PEXELS_API_KEY", ""))


def search_video(
    query: str,
    min_duration: int = 5,
    max_duration: int = 30,
    orientation: str = "landscape",
    per_page: int = 15,
) -> Optional[dict]:
    """Search Pexels for a video matching query. Returns best result dict or None."""
    if not is_available():
        return None

    headers = {"Authorization": os.getenv("PEXELS_API_KEY")}
    params = {
        "query": query,
        "orientation": orientation,
        "size": "medium",
        "per_page": per_page,
    }
    try:
        resp = requests.get(f"{BASE_URL}/search", headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        videos = data.get("videos", [])
        # Filter by duration
        valid = [
            v for v in videos
            if min_duration <= v.get("duration", 0) <= max_duration
        ]
        if not valid:
            valid = videos
        if not valid:
            return None
        # Pick highest resolution HD video
        best = valid[0]
        return best
    except Exception as e:
        log.warning("Pexels search failed for '%s': %s", query, e)
        return None


def download_video(video: dict, out_dir: str, scene_id: int) -> Optional[str]:
    """Download the best quality HD file from a Pexels video result."""
    try:
        files = video.get("video_files", [])
        hd_files = [f for f in files if f.get("quality") in ("hd", "sd")]
        hd_files.sort(key=lambda f: f.get("width", 0), reverse=True)
        if not hd_files:
            return None
        url = hd_files[0]["link"]
        out_path = os.path.join(out_dir, f"stock_scene_{scene_id:03d}.mp4")
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        for attempt in range(3):
            try:
                urllib.request.urlretrieve(url, out_path)
                log.info("Pexels video downloaded: %s (%s)", out_path, url[:60])
                return out_path
            except Exception as e:
                log.warning("Pexels download attempt %d failed: %s", attempt + 1, e)
                if attempt < 2:
                    time.sleep(2 ** attempt)
        return None
    except Exception as e:
        log.warning("Pexels download error: %s", e)
        return None


def get_video_for_scene(query: str, out_dir: str, scene_id: int) -> Optional[str]:
    """Convenience: search + download in one call. Returns local path or None."""
    video = search_video(query)
    if video:
        return download_video(video, out_dir, scene_id)
    return None
