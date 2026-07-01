#!/usr/bin/env python3
"""
first_time_setup.py — Complete one-shot Google Cloud + YouTube setup.

What this script does automatically
-------------------------------------
1.  Reads client_secrets.json you dropped in this folder
2.  Prints an authorisation URL — you open it on your phone and tap Allow
3.  You paste the short code back here
4.  Script calls YouTube API → finds your channel ID → writes .env
5.  Enables the YouTube Data API quota check
6.  Generates today's video
7.  Uploads it to your channel
8.  Installs the daily cron job

The ONE manual prerequisite (≈ 3 minutes, done once for ever)
--------------------------------------------------------------
See GOOGLE_ACCOUNT_SETUP.md  — but the short version:
  a) console.cloud.google.com → New project "mind-fuel-daily"
  b) APIs & Services → Library → enable "YouTube Data API v3"
  c) APIs & Services → Credentials → Create OAuth Client ID
     (Application type: Desktop app, name: daily-drop)
  d) Download JSON → rename to client_secrets.json → paste in this folder
Then run this script.
"""

import json
import os
import pathlib
import subprocess
import sys

# ── ensure dependencies are loaded from the venv ────────────────────────────
HERE = pathlib.Path(__file__).parent.resolve()
VENV_PYTHON = HERE / ".venv" / "bin" / "python"
if str(VENV_PYTHON) != sys.executable and VENV_PYTHON.exists():
    os.execv(str(VENV_PYTHON), [str(VENV_PYTHON)] + sys.argv)

# ── now safe to import third-party libs ─────────────────────────────────────
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",         # needed for channel branding
    "https://www.googleapis.com/auth/youtube.force-ssl",  # needed for posting comments
]

SECRETS = HERE / "client_secrets.json"
TOKEN   = HERE / "youtube_token.json"
ENV     = HERE / ".env"

SEP = "─" * 60


# ───────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────

def banner(msg: str):
    print(f"\n{SEP}\n  {msg}\n{SEP}")


def check_secrets():
    if not SECRETS.exists():
        banner("MISSING: client_secrets.json")
        print("""
  You need to create an OAuth client in Google Cloud Console once.
  It takes about 3 minutes.

  Quick steps:
  ─────────────
  1. Open  https://console.cloud.google.com
     Sign in with  prasad2t@gmail.com

  2. Click the project drop-down (top-left) → New Project
     Name: mind-fuel-daily  → Create

  3. Left menu → APIs & Services → Library
     Search "YouTube Data API v3" → Enable

  4. Left menu → APIs & Services → Credentials
     + Create Credentials → OAuth client ID
     App type: Desktop app
     Name: mind-fuel-daily-uploader
     → Create

  5. In the dialog that appears, click  ↓ Download JSON
     Rename the file to  client_secrets.json
     Move it here:
       {SECRETS}

  6. Re-run this script.
""".format(SECRETS=SECRETS))
        sys.exit(1)
    print(f"  ✓  client_secrets.json found")


def authenticate() -> object:
    """OAuth console flow — prints a URL, user visits it, pastes code back."""
    if TOKEN.exists():
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        if creds.valid:
            print("  ✓  Already authenticated (token loaded)")
            return creds
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            _save_token(creds)
            print("  ✓  Token refreshed")
            return creds

    banner("STEP: Authorise access to your YouTube channel")
    print("""
  This will print a URL.
  Open it on your phone or any browser, sign in with
  prasad2t@gmail.com, tap Allow, then copy-paste the
  authorisation code it shows back here.
  (Your password is NEVER shared with this script.)
""")
    flow = InstalledAppFlow.from_client_secrets_file(str(SECRETS), SCOPES)
    flow.redirect_uri = "urn:ietf:wg:oauth:2.0:oob"
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline")
    print(f"\n  Open this URL in your browser (phone or laptop):\n\n  {auth_url}\n")
    code = input("  Paste the authorisation code shown after you click Allow: ").strip()
    flow.fetch_token(code=code)
    creds = flow.credentials
    _save_token(creds)
    print("  ✓  Authorised — token saved")
    return creds


def _save_token(creds):
    with open(TOKEN, "w") as f:
        f.write(creds.to_json())


def get_channel_id(creds) -> str:
    banner("Fetching your YouTube channel…")
    yt = build("youtube", "v3", credentials=creds)
    resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
    items = resp.get("items", [])
    if not items:
        print("  ✗  No YouTube channel found for prasad2t@gmail.com.")
        print("     Create one at https://www.youtube.com/create_channel")
        cid = input("  Once created, paste your channel ID here: ").strip()
        return cid
    ch = items[0]
    cid  = ch["id"]
    name = ch["snippet"]["title"]
    subs = ch["statistics"].get("subscriberCount", "0")
    print(f"  ✓  Channel found: '{name}'")
    print(f"     ID: {cid}  |  Subscribers: {subs}")
    return cid


def write_env(channel_id: str):
    banner("Writing .env configuration…")
    env_content = f"""# Auto-generated by first_time_setup.py
# Google account: prasad2t@gmail.com

YOUTUBE_CLIENT_SECRETS_FILE=client_secrets.json
YOUTUBE_CHANNEL_ID={channel_id}
YOUTUBE_CATEGORY_ID=22
YOUTUBE_PRIVACY_STATUS=public

OUTPUT_DIR=output
VIDEO_WIDTH=1280
VIDEO_HEIGHT=720
VIDEO_FPS=15
VIDEO_DURATION_SECONDS=92

# YouTube Shorts — one combined Short per day covering all 12 signs (≤ 2 min)
# Costs 1 upload = ~1600 quota units (well within 10,000 unit free daily limit)
SHORTS_ENABLED=true

# Email alerts — get app password from: myaccount.google.com → Security → App passwords
# GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
# Set to "true" to also get success emails (failures always sent when password is set)
SEND_SUCCESS_EMAIL=false
"""
    ENV.write_text(env_content)
    print(f"  ✓  .env written")


def check_quota(creds):
    """Quick read to confirm the API key is working and quota is available."""
    banner("Checking YouTube API quota…")
    try:
        yt = build("youtube", "v3", credentials=creds)
        resp = yt.videos().list(part="snippet", chart="mostPopular",
                                regionCode="US", maxResults=1).execute()
        print(f"  ✓  YouTube API responding  (quota OK)")
    except HttpError as e:
        print(f"  ⚠  API check returned: {e}")
        print("     If this is a quota error, wait until midnight Pacific time.")


def generate_and_upload(upload: bool = True):
    banner("Generating today's video…")
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    flag = "--now" if upload else "--now --no-upload"
    cmd  = [python, str(HERE / "daily_runner.py")] + flag.split()
    result = subprocess.run(cmd, cwd=str(HERE))
    if result.returncode != 0:
        print("  ✗  Runner exited with error — check logs/")
    else:
        print("  ✓  Done!")


def install_cron():
    banner("Installing daily cron job (3 PM every day)…")
    python = str(VENV_PYTHON) if VENV_PYTHON.exists() else sys.executable
    cron_line = (
        f"0 15 * * * cd {HERE} && {python} daily_runner.py --now "
        f">> {HERE}/logs/cron.log 2>&1\n"
    )
    # Read existing crontab
    existing = subprocess.run(["crontab", "-l"],
                              capture_output=True, text=True).stdout
    if "daily_runner.py" in existing:
        print("  ✓  Cron job already installed")
        return
    new_crontab = existing.rstrip("\n") + "\n" + cron_line
    proc = subprocess.run(["crontab", "-"], input=new_crontab, text=True)
    if proc.returncode == 0:
        print("  ✓  Cron job installed — videos will upload daily at 15:00")
    else:
        print("  ⚠  Could not write crontab automatically.")
        print(f"     Add this line manually:\n     {cron_line}")


# ───────────────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-upload", action="store_true",
                        help="Generate video but skip the YouTube upload")
    args = parser.parse_args()

    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Mind Fuel Daily — First-Time Setup       ║")
    print("║  YouTube channel: prasad2t@gmail.com             ║")
    print("╚══════════════════════════════════════════════════╝")

    os.chdir(HERE)

    check_secrets()
    creds = authenticate()
    channel_id = get_channel_id(creds)
    write_env(channel_id)
    check_quota(creds)
    generate_and_upload(upload=not args.no_upload)
    install_cron()

    banner("Setup complete!")
    print("""
  Your daily video pipeline is now live.

  What happens next
  ─────────────────
  • A new video generates and uploads every day at 3:00 PM
  • Topics rotate across Money, Tech, Career, Health,
    Lifestyle, and Science — all US-focused
  • Every upload is logged to  logs/uploads.json

  YouTube monetisation checklist
  ───────────────────────────────
  ☐  1,000 subscribers   (apply for YouTube Partner Program)
  ☐  4,000 watch hours   (last 12 months)
  ✓  Daily uploads        (automated — done!)

  Commands
  ────────
  Re-run anytime:    python first_time_setup.py
  Manual upload:     python daily_runner.py --now
  View upload log:   cat logs/uploads.json
""")
