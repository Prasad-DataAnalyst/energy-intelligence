#!/usr/bin/env python3
"""
reauth_youtube.py
Re-authenticate YouTube OAuth on a headless VM.

No browser needed on the VM. Steps:
  1. Script prints a URL → open it on your phone/laptop
  2. Sign in, click Allow
  3. Browser tries to load http://localhost:8080/?code=...
     It will say "This site can't be reached" — that's normal!
  4. Copy the full URL from the address bar, paste it here

Usage:
  python3 reauth_youtube.py
"""
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

HERE     = Path(__file__).parent
SECRETS  = HERE / "client_secrets.json"
TOKEN    = HERE / "youtube_token.json"

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

REDIRECT_URI = "http://localhost:8080"


def main():
    if not SECRETS.exists():
        print(f"ERROR: {SECRETS} not found.")
        print("Download it from console.cloud.google.com → APIs & Services → Credentials")
        return

    from google_auth_oauthlib.flow import Flow

    flow = Flow.from_client_secrets_file(str(SECRETS), scopes=SCOPES)
    flow.redirect_uri = REDIRECT_URI

    auth_url, _ = flow.authorization_url(
        prompt="consent",
        access_type="offline",
        include_granted_scopes="true",
    )

    print("\n" + "=" * 60)
    print("  YouTube Re-Authentication — GetMindFuelNow")
    print("=" * 60)
    print("""
STEP 1: Open this URL in your browser (phone or laptop):
""")
    print(auth_url)
    print("""
STEP 2: Sign in as prasad2t@gmail.com → click Allow

STEP 3: Your browser will try to open http://localhost:8080/?code=...
        It will show "This site can't be reached" — that is NORMAL.

        Copy the FULL URL from your browser address bar.
        It looks like:
          http://localhost:8080/?code=4/0AX4XfWi...&scope=https://...

STEP 4: Paste that full URL below.
""")

    redirect_response = input("Paste the full redirect URL here: ").strip()

    try:
        flow.fetch_token(authorization_response=redirect_response)
    except Exception as e:
        print(f"\nERROR fetching token: {e}")
        print("Make sure you copied the FULL URL (starting with http://localhost:8080/?code=)")
        return

    creds = flow.credentials
    TOKEN.write_text(creds.to_json())

    print(f"\n  ✓  Token saved → {TOKEN}")
    print("  ✓  Re-authentication complete!")
    print("\n  You can now run:")
    print("    python3 run_daily.py --date $(date +%Y%m%d) --period \"$(date +'%B %Y')\" --skip-assets --upload\n")


if __name__ == "__main__":
    main()
