#!/usr/bin/env python3
"""
tiktok_uploader.py
Upload videos to TikTok using Content Posting API v2.
Run tiktok_setup.py once first to get OAuth token.

Usage:
  from tiktok_uploader import upload_to_tiktok
  publish_id = upload_to_tiktok(video_path, title, hashtags)
"""
import json
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN_FILE  = Path("tiktok_token.json")
REFRESH_URL = "https://open.tiktokapis.com/v2/oauth/token/"
PUBLISH_URL = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL  = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"


def _load_token() -> dict:
    if not TOKEN_FILE.exists():
        raise FileNotFoundError(
            "tiktok_token.json not found. Run: python3 tiktok_setup.py"
        )
    return json.loads(TOKEN_FILE.read_text())


def _refresh_if_needed(token: dict) -> dict:
    """Auto-refresh access token before it expires."""
    if time.time() < token.get("expires_at", 0) - 300:
        return token
    resp = requests.post(REFRESH_URL, data={
        "client_key":    os.environ.get("TIKTOK_CLIENT_KEY", ""),
        "client_secret": os.environ.get("TIKTOK_CLIENT_SECRET", ""),
        "grant_type":    "refresh_token",
        "refresh_token": token.get("refresh_token", ""),
    })
    resp.raise_for_status()
    new = resp.json().get("data", resp.json())
    new["expires_at"] = time.time() + new.get("expires_in", 86400)
    TOKEN_FILE.write_text(json.dumps(new, indent=2))
    print("[TikTok] Token refreshed")
    return new


def upload_to_tiktok(video_path: str, title: str, hashtags: list) -> str:
    """
    Upload MP4 to TikTok. Returns publish_id.
    Raises RuntimeError on failure.
    """
    token        = _refresh_if_needed(_load_token())
    access_token = token["access_token"]
    video_size   = os.path.getsize(video_path)

    # Build caption: title + hashtags (TikTok max 2200 chars)
    tags    = " ".join(f"#{t.strip().lstrip('#')}" for t in (hashtags or [])[:8])
    caption = f"{title} {tags}".strip()[:2200]

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    # Step 1 — initialise upload session
    init = requests.post(PUBLISH_URL, headers=headers, json={
        "post_info": {
            "title":                     caption,
            "privacy_level":             "PUBLIC_TO_EVERYONE",
            "disable_duet":              False,
            "disable_comment":           False,
            "disable_stitch":            False,
            "video_cover_timestamp_ms":  2000,
        },
        "source_info": {
            "source":            "FILE_UPLOAD",
            "video_size":        video_size,
            "chunk_size":        video_size,
            "total_chunk_count": 1,
        },
    })
    init.raise_for_status()
    data       = init.json()["data"]
    publish_id = data["publish_id"]
    upload_url = data["upload_url"]

    # Step 2 — stream video bytes
    print(f"[TikTok] Uploading {Path(video_path).name}  "
          f"({video_size / 1_048_576:.1f} MB)...")
    with open(video_path, "rb") as fh:
        video_bytes = fh.read()

    put = requests.put(
        upload_url,
        headers={
            "Content-Range":  f"bytes 0-{video_size - 1}/{video_size}",
            "Content-Length": str(video_size),
            "Content-Type":   "video/mp4",
        },
        data=video_bytes,
        timeout=300,
    )
    put.raise_for_status()

    print(f"[TikTok] Done — publish_id: {publish_id}")
    return publish_id


def check_status(publish_id: str) -> str:
    """Poll publish status. Returns 'PUBLISH_COMPLETE', 'PROCESSING_UPLOAD', etc."""
    try:
        token = _load_token()
        resp  = requests.post(
            STATUS_URL,
            headers={
                "Authorization": f"Bearer {token['access_token']}",
                "Content-Type":  "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
        )
        if resp.ok:
            return resp.json().get("data", {}).get("status", "UNKNOWN")
    except Exception:
        pass
    return "UNKNOWN"
