"""
Uploads a video file to YouTube using the YouTube Data API v3.

Authentication flow
-------------------
First run:  Opens a browser for OAuth consent → saves token to TOKEN_FILE.
Subsequent: Loads token from TOKEN_FILE automatically (no browser needed).

Prerequisites
-------------
1. Create a project in Google Cloud Console.
2. Enable "YouTube Data API v3".
3. Create OAuth 2.0 credentials (Desktop App type).
4. Download as client_secrets.json and place it next to this file.
"""

import os
import json
import datetime
from pathlib import Path

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

import config


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _get_credentials() -> Credentials:
    creds = None
    token_path = Path(config.TOKEN_FILE)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path),
                                                      config.YOUTUBE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            secrets = Path(config.CLIENT_SECRETS_FILE)
            if not secrets.exists():
                raise FileNotFoundError(
                    f"Missing {config.CLIENT_SECRETS_FILE}. "
                    "Download it from Google Cloud Console → APIs & Services → Credentials."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(secrets), config.YOUTUBE_SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(token_path, "w") as f:
            f.write(creds.to_json())

    return creds


def _build_service():
    creds = _get_credentials()
    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

def upload_video(video_path: str, content: dict) -> str:
    """
    Upload video_path to YouTube using metadata from content dict.
    Returns the YouTube video ID.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")

    service = _build_service()

    body = {
        "snippet": {
            "title": content["title"],
            "description": content["description"],
            "tags": content["tags"],
            "categoryId": config.CATEGORY_ID,
            "defaultLanguage": "en",
        },
        "status": {
            "privacyStatus": config.PRIVACY_STATUS,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024 * 5,   # 5 MB chunks
    )

    print(f"Uploading: {content['title']}")
    request = service.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  Upload progress: {pct}%", end="\r")

    video_id = response["id"]
    print(f"\nUpload complete → https://youtu.be/{video_id}")
    _log_upload(video_path, video_id, content)
    return video_id


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
        "date": str(content["date"]),
        "video_id": video_id,
        "title": content["title"],
        "topic": content["topic"],
        "file": video_path,
        "uploaded_at": datetime.datetime.utcnow().isoformat() + "Z",
        "url": f"https://youtu.be/{video_id}",
    })

    with open(LOG_FILE, "w") as f:
        json.dump(history, f, indent=2)

    print(f"Upload logged → {LOG_FILE}")
