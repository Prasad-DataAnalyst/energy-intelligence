import os
from dotenv import load_dotenv

load_dotenv()

# Video dimensions
WIDTH  = int(os.getenv("VIDEO_WIDTH",  1920))
HEIGHT = int(os.getenv("VIDEO_HEIGHT", 1080))
FPS    = int(os.getenv("VIDEO_FPS",    24))
DURATION = int(os.getenv("VIDEO_DURATION_SECONDS", 92))    # 8+4+50+18+12

# Directories
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
ASSETS_DIR = "assets"
LOGS_DIR   = "logs"

# YouTube
CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
YOUTUBE_SCOPES      = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",          # needed for channel branding update
]
CHANNEL_ID          = os.getenv("YOUTUBE_CHANNEL_ID", "")
# 27 = Education (highest CPM tier), 26 = How-to & Style, 22 = People & Blogs
CATEGORY_ID         = os.getenv("YOUTUBE_CATEGORY_ID", "27")
PRIVACY_STATUS      = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
TOKEN_FILE          = "youtube_token.json"

# EIA key left for backward compat — not used in the new general audience version
EIA_API_KEY  = os.getenv("EIA_API_KEY", "")
EIA_BASE_URL = "https://api.eia.gov/v2"

# Upload schedule — target US EST peak hours (3 PM EST = good initial velocity)
UPLOAD_TIME = os.getenv("UPLOAD_TIME", "15:00")

# YouTube Shorts — one 45-58s vertical clip per zodiac sign after each daily run
# Each upload costs ~1600 quota units; free daily quota = 10,000 units.
# Default max_uploads=4 keeps total (main + 4 shorts) under the limit.
# Request a free quota increase to upload all 12:
#   https://support.google.com/youtube/contact/yt_api_form
SHORTS_ENABLED      = os.getenv("SHORTS_ENABLED", "true").lower() == "true"
MAX_SHORTS_PER_DAY  = int(os.getenv("MAX_SHORTS_PER_DAY", "4"))
