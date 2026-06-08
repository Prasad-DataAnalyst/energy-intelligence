"""
Uploads a video file to YouTube using the YouTube Data API v3.
Uses google.auth.transport.requests (not httplib2) to avoid SSL issues.
Token is loaded from youtube_token.json written by first_time_setup / auth flow.
"""

import json
import os
import time
import datetime
from pathlib import Path

import requests as req_lib
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.errors import HttpError

import config

CHUNK = 5 * 1024 * 1024   # 5 MB resumable upload chunks


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_credentials() -> Credentials:
    token_path = Path(config.TOKEN_FILE)
    if not token_path.exists():
        raise FileNotFoundError(
            f"No token found at {token_path}. "
            "Run first_time_setup.py to authenticate."
        )
    creds = Credentials.from_authorized_user_file(str(token_path),
                                                  config.YOUTUBE_SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
    return creds


def _authed_session():
    import google.auth.transport.requests as gtr
    creds = _get_credentials()
    return gtr.AuthorizedSession(creds), creds


# ---------------------------------------------------------------------------
# Upload  (resumable, using raw requests to bypass httplib2 SSL issues)
# ---------------------------------------------------------------------------

def upload_video(video_path: str, content: dict) -> str:
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    session, creds = _authed_session()
    file_size = os.path.getsize(video_path)

    # Step 1 — initiate resumable upload session
    metadata = {
        "snippet": {
            "title":           content["title"],
            "description":     content["description"],
            "tags":            content["tags"],
            "categoryId":      config.CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus":           config.PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    init_resp = session.post(
        "https://www.googleapis.com/upload/youtube/v3/videos"
        "?uploadType=resumable&part=snippet,status",
        headers={"X-Upload-Content-Type": "video/mp4",
                 "X-Upload-Content-Length": str(file_size),
                 "Content-Type": "application/json"},
        data=json.dumps(metadata),
    )
    init_resp.raise_for_status()
    upload_url = init_resp.headers["Location"]

    # Step 2 — upload in chunks
    print(f"Uploading: {content['title']}  ({file_size/1_048_576:.1f} MB)")
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
                except Exception as exc:
                    if attempt == 5:
                        raise
                    time.sleep(2 ** attempt)

            if resp.status_code in (200, 201):
                video_id = resp.json()["id"]
                print(f"\nUpload complete → https://youtu.be/{video_id}")
                _log_upload(video_path, video_id, content)
                return video_id
            elif resp.status_code == 308:   # Resume Incomplete
                uploaded = int(resp.headers.get("Range", f"bytes=0-{end}").split("-")[1]) + 1
                pct = int(uploaded / file_size * 100)
                print(f"  {pct}%", end="\r")
            elif resp.status_code in (500, 502, 503, 504) and retry < 5:
                retry += 1
                time.sleep(2 ** retry)
            elif resp.status_code == 403:
                raise RuntimeError(
                    "YouTube quota exceeded (10,000 units/day free limit).\n"
                    "Quota resets at midnight Pacific time. "
                    "Video saved locally — it will upload tomorrow."
                )
            else:
                raise RuntimeError(f"Upload error {resp.status_code}: {resp.text[:400]}")

    raise RuntimeError("Upload loop ended without completion.")


# ---------------------------------------------------------------------------
# Upload log
# ---------------------------------------------------------------------------

LOG_FILE = Path(config.LOGS_DIR) / "uploads.json"


def _log_upload(video_path: str, video_id: str, content: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    history = []
    if LOG_FILE.exists():
        with open(LOG_FILE) as f:
            history = json.load(f)
    history.append({
        "date":        str(content["date"]),
        "video_id":    video_id,
        "title":       content["title"],
        "topic":       content["topic"],
        "file":        video_path,
        "uploaded_at": datetime.datetime.utcnow().isoformat() + "Z",
        "url":         f"https://youtu.be/{video_id}",
    })
    with open(LOG_FILE, "w") as f:
        json.dump(history, f, indent=2)
    print(f"Logged → {LOG_FILE}")
