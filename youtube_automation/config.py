import os
from dotenv import load_dotenv

load_dotenv()

# Video dimensions
WIDTH = int(os.getenv("VIDEO_WIDTH", 1920))
HEIGHT = int(os.getenv("VIDEO_HEIGHT", 1080))
FPS = int(os.getenv("VIDEO_FPS", 24))
DURATION = int(os.getenv("VIDEO_DURATION_SECONDS", 180))

# Directories
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
ASSETS_DIR = "assets"
LOGS_DIR = "logs"

# YouTube
CLIENT_SECRETS_FILE = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID", "")
CATEGORY_ID = os.getenv("YOUTUBE_CATEGORY_ID", "28")
PRIVACY_STATUS = os.getenv("YOUTUBE_PRIVACY_STATUS", "public")
TOKEN_FILE = "youtube_token.json"

# EIA API
EIA_API_KEY = os.getenv("EIA_API_KEY", "")
EIA_BASE_URL = "https://api.eia.gov/v2"

# Colour palette — pencil-sketch on cream paper feel
PAPER_BG = (245, 242, 235)      # warm cream
INK_DARK = (30, 25, 20)         # near-black ink
INK_MID = (80, 70, 60)          # mid-tone sketch
INK_LIGHT = (160, 150, 140)     # light pencil
ACCENT_AMBER = (200, 140, 40)   # oil-field amber
ACCENT_BLUE = (50, 100, 160)    # gas-market blue
ACCENT_RED = (180, 50, 40)      # alert red
ACCENT_GREEN = (50, 130, 80)    # positive green

# Upload schedule
UPLOAD_TIME = os.getenv("UPLOAD_TIME", "08:00")
