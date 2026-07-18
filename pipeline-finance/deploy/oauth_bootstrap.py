#!/usr/bin/env python3
"""
deploy/oauth_bootstrap.py — one-time OAuth token minting for DriftWire326.

RUN THIS ON YOUR LOCAL PC (it opens a browser twice), NOT on the cloud VM.
Afterwards copy the two generated token files to the VM:

    scp config/youtube_token.json config/analytics_token.json \
        <vm-user>@<vm-ip>:/opt/driftwire326/pipeline-finance/config/

Prerequisites:
  1. config/finance_oauth.json — the OAuth "Desktop app" client secrets JSON
     downloaded from Google Cloud Console (APIs & Services → Credentials).
  2. pip install google-auth-oauthlib google-api-python-client

What it mints:
  config/youtube_token.json    — upload + manage scopes (Data API v3)
  config/analytics_token.json  — read-only analytics scopes (Analytics API v2)

Tokens auto-refresh forever afterwards; no browser needed on the VM.
NOTE: while your OAuth consent screen is in "Testing" mode, refresh tokens
expire after 7 days. Publish the consent screen (still private to your
account) to get non-expiring refresh tokens.
"""
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_ROOT / "config"
CLIENT_SECRETS = CONFIG_DIR / "finance_oauth.json"

UPLOAD_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
ANALYTICS_SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def _mint(scopes: list[str], out_path: Path, label: str) -> bool:
    from google_auth_oauthlib.flow import InstalledAppFlow

    print(f"\n=== {label} ===")
    print(f"Scopes: {', '.join(s.rsplit('/', 1)[-1] for s in scopes)}")
    print("A browser window will open — sign in with the Google account that")
    print("owns the DriftWire326 YouTube channel, and approve all permissions.")
    input("Press Enter to continue...")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), scopes)
    creds = flow.run_local_server(port=0)
    out_path.write_text(creds.to_json())
    print(f"✅ Saved: {out_path}")
    return True


def main() -> int:
    if not CLIENT_SECRETS.exists():
        print(f"❌ Client secrets not found: {CLIENT_SECRETS}")
        print("   Download the OAuth 'Desktop app' JSON from Google Cloud Console")
        print("   (APIs & Services → Credentials) and save it to that path.")
        return 1

    # Sanity-check it's a desktop-app client
    try:
        data = json.loads(CLIENT_SECRETS.read_text())
        if "installed" not in data:
            print("⚠️  This client secrets file is not a 'Desktop app' type.")
            print("   Create a Desktop app OAuth client in Google Cloud Console.")
            return 1
    except Exception as exc:
        print(f"❌ Could not parse client secrets: {exc}")
        return 1

    _mint(UPLOAD_SCOPES, CONFIG_DIR / "youtube_token.json", "Token 1/2 — YouTube upload")
    _mint(ANALYTICS_SCOPES, CONFIG_DIR / "analytics_token.json", "Token 2/2 — Analytics (read-only)")

    print("\n🎉 Both tokens minted. Copy them to the VM:")
    print("   scp config/youtube_token.json config/analytics_token.json \\")
    print("       <vm-user>@<vm-ip>:/opt/driftwire326/pipeline-finance/config/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
