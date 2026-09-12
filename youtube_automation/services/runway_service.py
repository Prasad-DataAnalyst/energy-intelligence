"""
services/runway_service.py
Layer: Service Wrapper
Safety: upgradeable
Description: Runway ML Gen-3 Alpha API wrapper for AI video generation.
Input:  scene description prompt (str)
Output: local path to generated MP4 clip
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

API_BASE = "https://api.dev.runwayml.com/v1"
DEFAULT_DURATION = 5
DEFAULT_WIDTH = 1280
DEFAULT_HEIGHT = 768


def is_available() -> bool:
    return bool(os.getenv("RUNWAY_API_KEY", ""))


def generate_clip(
    prompt: str,
    out_path: str,
    duration: int = DEFAULT_DURATION,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
) -> Optional[str]:
    """
    Generate a short AI video clip via Runway Gen-3 Alpha.
    Returns out_path on success, None on failure.
    """
    if not is_available():
        return None

    api_key = os.getenv("RUNWAY_API_KEY")
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-Runway-Version": "2024-11-06",
    }

    # Enhance prompt with cinematic modifiers
    full_prompt = (
        f"{prompt}, cinematic 4K ultra-detailed, "
        "professional color grading, smooth camera movement, "
        "broadcast quality, shallow depth of field"
    )

    payload = {
        "model": "gen3a_turbo",
        "promptText": full_prompt,
        "duration": duration,
        "ratio": f"{width}:{height}",
    }

    try:
        # Submit generation job
        resp = requests.post(
            f"{API_BASE}/image_to_video",
            json=payload, headers=headers, timeout=30
        )
        if resp.status_code == 404:
            # Try text-to-video endpoint
            resp = requests.post(
                f"{API_BASE}/text_to_video",
                json=payload, headers=headers, timeout=30
            )
        resp.raise_for_status()
        task_id = resp.json().get("id")
        if not task_id:
            log.warning("Runway: no task ID in response")
            return None

        log.info("Runway task submitted: %s", task_id)

        # Poll for completion (max 120s)
        for _ in range(40):
            time.sleep(3)
            poll = requests.get(
                f"{API_BASE}/tasks/{task_id}",
                headers=headers, timeout=10
            )
            poll.raise_for_status()
            data = poll.json()
            status = data.get("status", "")
            if status == "SUCCEEDED":
                video_url = data.get("output", [None])[0]
                if video_url:
                    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
                    urllib.request.urlretrieve(video_url, out_path)
                    log.info("Runway clip downloaded: %s", out_path)
                    return out_path
                break
            elif status in ("FAILED", "CANCELLED"):
                log.warning("Runway task %s: %s", task_id, status)
                return None

        log.warning("Runway task %s timed out", task_id)
        return None
    except Exception as e:
        log.warning("Runway generation failed: %s", e)
        return None
