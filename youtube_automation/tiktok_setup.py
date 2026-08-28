#!/usr/bin/env python3
"""
tiktok_setup.py
One-time TikTok OAuth setup. Run this ONCE on your VM to authorize posting.

Prerequisites (do these first):
  1. Go to https://developers.tiktok.com/apps/ and create a new app
  2. Add the "Content Posting API" product to your app
  3. Set Redirect URI to: http://localhost:8080/callback
  4. Copy your Client Key and Client Secret
  5. Run: python3 tiktok_setup.py

Usage:
  python3 tiktok_setup.py
"""
import base64
import hashlib
import http.server
import json
import os
import secrets
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests
from dotenv import load_dotenv, set_key

load_dotenv()

REDIRECT_URI = "http://localhost:8080/callback"
TOKEN_FILE   = Path("tiktok_token.json")
ENV_FILE     = Path(".env")
SCOPES       = "user.info.basic,video.publish,video.upload"
AUTH_URL     = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL    = "https://open.tiktokapis.com/v2/oauth/token/"


def _pkce_pair() -> tuple:
    verifier  = secrets.token_urlsafe(96)
    digest    = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def main():
    client_key    = os.environ.get("TIKTOK_CLIENT_KEY", "").strip()
    client_secret = os.environ.get("TIKTOK_CLIENT_SECRET", "").strip()

    if not client_key:
        print("\n" + "=" * 55)
        print("  TikTok Developer App Setup")
        print("=" * 55)
        print("\nStep 1: Go to https://developers.tiktok.com/apps/")
        print("Step 2: Create a new app (any name)")
        print("Step 3: Add 'Content Posting API' product")
        print("Step 4: Add redirect URI: http://localhost:8080/callback")
        print("Step 5: Copy your Client Key + Client Secret\n")
        client_key    = input("Paste Client Key:    ").strip()
        client_secret = input("Paste Client Secret: ").strip()
        set_key(str(ENV_FILE), "TIKTOK_CLIENT_KEY",    client_key)
        set_key(str(ENV_FILE), "TIKTOK_CLIENT_SECRET", client_secret)
        print("\n[OK] Saved to .env\n")

    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(16)

    params = {
        "client_key":            client_key,
        "scope":                 SCOPES,
        "response_type":         "code",
        "redirect_uri":          REDIRECT_URI,
        "state":                 state,
        "code_challenge":        challenge,
        "code_challenge_method": "S256",
    }
    auth_link = AUTH_URL + "?" + urllib.parse.urlencode(params)

    code_holder: dict = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            code_holder["code"]  = qs.get("code",  [""])[0]
            code_holder["state"] = qs.get("state", [""])[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"<h2>TikTok authorised! Return to your terminal.</h2>")
        def log_message(self, *_): pass

    srv = http.server.HTTPServer(("localhost", 8080), _Handler)
    t   = threading.Thread(target=srv.handle_request, daemon=True)
    t.start()

    print("Opening TikTok auth in browser...")
    print(f"If browser doesn't open, visit:\n{auth_link}\n")
    webbrowser.open(auth_link)
    t.join(timeout=120)
    srv.server_close()

    if not code_holder.get("code"):
        print("[ERROR] No auth code received. Try again.")
        return

    resp = requests.post(TOKEN_URL, data={
        "client_key":    client_key,
        "client_secret": client_secret,
        "code":          code_holder["code"],
        "grant_type":    "authorization_code",
        "redirect_uri":  REDIRECT_URI,
        "code_verifier": verifier,
    })
    resp.raise_for_status()
    token_data = resp.json().get("data", resp.json())

    import time
    token_data["expires_at"] = time.time() + token_data.get("expires_in", 86400)
    TOKEN_FILE.write_text(json.dumps(token_data, indent=2))

    print(f"\n[OK] Token saved to {TOKEN_FILE}")
    print(f"     Open ID: {token_data.get('open_id', '?')}")
    print(f"\nSetup complete! You can now use --tiktok in run_daily.py")


if __name__ == "__main__":
    main()
