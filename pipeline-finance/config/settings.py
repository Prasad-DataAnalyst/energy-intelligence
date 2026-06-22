"""
config/settings.py — DriftWire326 Pipeline Configuration
All channel settings, API keys, schedule, video specs, and constants.
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# ─────────────────────────────────────────────────────────────────────────────
# 1. ENVIRONMENT LOADING
# ─────────────────────────────────────────────────────────────────────────────

_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_ENV_PATH, override=False)

ANTHROPIC_API_KEY     = os.getenv("ANTHROPIC_API_KEY", "")
YOUTUBE_CLIENT_ID     = os.getenv("YOUTUBE_CLIENT_ID", "")
YOUTUBE_CLIENT_SECRET = os.getenv("YOUTUBE_CLIENT_SECRET", "")
FRED_API_KEY          = os.getenv("FRED_API_KEY", "")
FINNHUB_API_KEY       = os.getenv("FINNHUB_API_KEY", "")
PEXELS_API_KEY        = os.getenv("PEXELS_API_KEY", "")
ELEVENLABS_API_KEY    = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID   = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

# ─────────────────────────────────────────────────────────────────────────────
# 2. CHANNEL SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

CHANNEL_NAME   = "Drift Wire326"
CHANNEL_HANDLE = "@DriftWire326"
CHANNEL_ID     = ""          # filled in after first OAuth setup

# ─────────────────────────────────────────────────────────────────────────────
# 3. POSTING SCHEDULE  (all times in US Eastern Time)
# ─────────────────────────────────────────────────────────────────────────────

WEEKDAY_MAIN_TIMES  = ["07:00", "16:30"]        # Mon–Fri main videos
WEEKDAY_SHORT_TIMES = ["07:00", "12:30", "16:30"]  # Mon–Fri Shorts
SUNDAY_TIMES        = ["10:00", "16:00"]         # Sunday educational
TIMEZONE            = "America/New_York"

# ─────────────────────────────────────────────────────────────────────────────
# 4. VIDEO SPECS
# ─────────────────────────────────────────────────────────────────────────────

VIDEO_DURATION_MIN    = 120   # seconds
VIDEO_DURATION_MAX    = 180   # seconds
VIDEO_DURATION_TARGET = 150   # seconds

SHORTS_DURATION_MAX    = 60   # seconds
SHORTS_DURATION_TARGET = 50   # seconds

VIDEO_RESOLUTION  = (1920, 1080)
SHORTS_RESOLUTION = (1080, 1920)

VIDEO_FPS    = 30
VIDEO_CODEC  = "libx264"
AUDIO_CODEC  = "aac"

MUSIC_VOLUME        = 0.08   # background music volume for main video (8%)
SHORTS_MUSIC_VOLUME = 0.15   # background music volume for Shorts (15%)

# ─────────────────────────────────────────────────────────────────────────────
# 5. TTS SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

TTS_ENGINE   = "edge-tts"
TTS_FALLBACK = "gtts"

# Day-of-week voice rotation — different voice keeps content fresh
TTS_VOICES = {
    "monday":    "en-US-GuyNeural",
    "tuesday":   "en-US-GuyNeural",
    "wednesday": "en-US-JennyNeural",
    "thursday":  "en-US-JennyNeural",
    "friday":    "en-US-ChristopherNeural",
    "sunday":    "en-US-AriaNeural",
}

TTS_RATE   = "+0%"   # adjust with e.g. "+10%" to speed up
TTS_VOLUME = "+0%"   # adjust with e.g. "+5%" to increase gain

# ─────────────────────────────────────────────────────────────────────────────
# 6. CONTENT SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

STORY_TIERS = {
    "tier1": {"min_move_pct": 5.0, "label": "breakout"},
    "tier2": {"min_move_pct": 2.0, "label": "notable"},
    "tier3": {"min_move_pct": 0.0, "label": "routine"},
}

SCRIPT_STYLES = [
    "breaking_news_reporter",
    "smart_friend_texting",
    "shocked_reaction",
    "timeline_breakdown",
    "hot_take_opinion",
]

HOOK_VARIATIONS = [
    "Okay so you are NOT going to believe this...",
    "The market is absolutely losing it over...",
    "Drop everything because this just happened...",
    "Wall Street did not see this coming...",
    "This just broke and everyone is talking about it...",
    "Here is what actually moved the market today...",
    "Nobody is talking about this but they should be...",
    "This number just dropped and it changes everything...",
    "In the last two hours something wild happened...",
    "This stock just made history and here is why...",
    "The Fed just did something unexpected...",
    "Traders are panicking right now because...",
    "This earnings report just shocked Wall Street...",
    "One economic number just came out and markets moved...",
    "Here is the story behind today's biggest mover...",
    "Most investors missed this move today...",
    "This sector just had its biggest day in months...",
    "The data that just dropped changes the picture...",
    "Three things happened today that you need to know...",
    "Here is what the smart money is watching right now...",
]

# ─────────────────────────────────────────────────────────────────────────────
# 7. SUNDAY ROTATION
# ─────────────────────────────────────────────────────────────────────────────

# Week-of-month themes (1-indexed)
SUNDAY_THEMES = {
    1: "investment_banking",
    2: "insurance_protection",
    3: "savings_wealth",
    4: "rotating_bonus",
}

# Cycle order for week-4 bonus theme
ROTATING_BONUS_ORDER = [
    "real_estate",
    "crypto_digital",
    "tax_strategy",
    "retirement_planning",
    "side_income",
    "macro_finance",
]

# ─────────────────────────────────────────────────────────────────────────────
# 8. QUOTA MANAGEMENT
# ─────────────────────────────────────────────────────────────────────────────

DAILY_QUOTA_TOTAL  = 10_000   # YouTube Data API v3 free-tier daily cap
DAILY_QUOTA_BUDGET = 8_100    # safe operating budget (leaves buffer)
DAILY_QUOTA_BUFFER = 1_900    # reserved for unexpected retries / reads

UPLOAD_COST          = 1_600   # videos.insert
THUMBNAIL_COST       = 50      # thumbnails.set
METADATA_UPDATE_COST = 50      # videos.update (title / description)
MIN_QUOTA_TO_UPLOAD  = 1_700   # minimum remaining quota required before uploading

QUOTA_STATE_FILE = "logs/quota_state.json"

# ─────────────────────────────────────────────────────────────────────────────
# 9. US MARKET HOLIDAYS 2026
# ─────────────────────────────────────────────────────────────────────────────

MARKET_HOLIDAYS_2026 = [
    "2026-01-01",  # New Year's Day
    "2026-01-19",  # Martin Luther King Jr. Day
    "2026-02-16",  # Presidents' Day
    "2026-04-03",  # Good Friday
    "2026-05-25",  # Memorial Day
    "2026-07-03",  # Independence Day (observed)
    "2026-09-07",  # Labor Day
    "2026-11-26",  # Thanksgiving Day
    "2026-12-25",  # Christmas Day
]

# ─────────────────────────────────────────────────────────────────────────────
# 10. COMPLIANCE
# ─────────────────────────────────────────────────────────────────────────────

BLOCKED_PHRASES = [
    "you should buy",
    "you should sell",
    "guaranteed return",
    "this will go up",
    "best investment",
    "I recommend buying",
    "do not miss this opportunity",
    "you need to purchase",
    "go all in",
    "this is a sure thing",
]

# At least one of these must appear in every script (credibility anchors)
REQUIRED_PHRASES = [
    "according to",
    "data shows",
    "reported",
    "as of today",
    "markets indicate",
    "analysts note",
    "figures show",
    "sources indicate",
    "market data suggests",
]

DISCLAIMER = (
    "This content is for informational, educational, and entertainment "
    "purposes only and does not constitute financial advice. "
    "Always consult a licensed financial advisor before "
    "making any investment decisions."
)

AI_DISCLOSURE = "Narration is AI-generated."

# ─────────────────────────────────────────────────────────────────────────────
# 11. FILE PATHS
# ─────────────────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).resolve().parent.parent   # pipeline-finance/
ASSETS_DIR = BASE_DIR / "assets"
OUTPUT_DIR = BASE_DIR / "output"
LOGS_DIR   = BASE_DIR / "logs"
CONFIG_DIR = BASE_DIR / "config"

OAUTH_FILE   = CONFIG_DIR / "finance_oauth.json"
LOG_FILE     = LOGS_DIR   / "driftwire326.log"
QUOTA_FILE   = LOGS_DIR   / "quota_state.json"
FAILED_QUEUE = LOGS_DIR   / "failed_queue.json"

# Ensure runtime directories exist
for _dir in (OUTPUT_DIR, LOGS_DIR, ASSETS_DIR,
             OUTPUT_DIR / "videos", OUTPUT_DIR / "shorts",
             OUTPUT_DIR / "thumbnails", OUTPUT_DIR / "scripts",
             OUTPUT_DIR / "charts",    OUTPUT_DIR / "audio"):
    _dir.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# 12. BRAND COLORS
# ─────────────────────────────────────────────────────────────────────────────

BRAND_PURPLE    = "#6B21A8"
BRAND_NEON_GREEN = "#00FF41"
BRAND_WHITE     = "#FFFFFF"
BRAND_DARK      = "#0D0D0D"
TEXT_SHADOW     = "#000000"
POSITIVE_COLOR  = "#00C851"
NEGATIVE_COLOR  = "#FF4444"

# ─────────────────────────────────────────────────────────────────────────────
# 13. AI / API SETTINGS
# ─────────────────────────────────────────────────────────────────────────────

CLAUDE_MODEL       = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
CLAUDE_MAX_TOKENS  = 2048
CLAUDE_TEMPERATURE = 0.7

MAX_RETRIES         = 3
RETRY_BACKOFF_BASE  = 2      # seconds — wait = BASE ** attempt

AUDIO_BITRATE = "192k"
VIDEO_BITRATE = "8000k"

DAILY_UPLOAD_LIMIT  = DAILY_QUOTA_BUDGET // UPLOAD_COST  # ≈5 per day

# ─────────────────────────────────────────────────────────────────────────────
# 14. MARKET WATCHLIST & FRED SERIES
# ─────────────────────────────────────────────────────────────────────────────

TRACKED_TICKERS = [
    "^GSPC", "^IXIC", "^DJI",         # indices
    "AAPL", "MSFT", "GOOGL", "AMZN",  # mega-cap tech
    "NVDA", "META", "TSLA",            # high-beta
    "JPM", "GS", "BAC",               # financials
    "XOM", "CVX",                      # energy
    "SPY", "QQQ", "IWM",              # ETFs
    "^VIX", "^TNX",                   # vol + 10-yr yield
]

FRED_SERIES = {
    "CPIAUCSL":  "CPI (Inflation)",
    "FEDFUNDS":  "Fed Funds Rate",
    "UNRATE":    "Unemployment Rate",
    "GDP":       "Real GDP",
    "T10YIE":    "10-Year Breakeven Inflation",
    "DGS10":     "10-Year Treasury Yield",
    "UMCSENT":   "Consumer Sentiment",
}

# ─────────────────────────────────────────────────────────────────────────────
# 15. BACKWARD-COMPATIBLE NAMESPACE
# Allows existing modules to keep: from config.settings import settings
# ─────────────────────────────────────────────────────────────────────────────

from types import SimpleNamespace  # noqa: E402

settings = SimpleNamespace(
    # API keys
    anthropic_api_key     = ANTHROPIC_API_KEY,
    elevenlabs_api_key    = ELEVENLABS_API_KEY,
    elevenlabs_voice_id   = ELEVENLABS_VOICE_ID,
    fred_api_key          = FRED_API_KEY,
    finnhub_api_key       = FINNHUB_API_KEY,
    pexels_api_key        = PEXELS_API_KEY,
    youtube_client_secrets = OAUTH_FILE,

    # Channel
    channel_name   = CHANNEL_NAME,
    channel_handle = CHANNEL_HANDLE,
    channel_id     = CHANNEL_ID,

    # AI
    claude_model       = CLAUDE_MODEL,
    claude_max_tokens  = CLAUDE_MAX_TOKENS,
    claude_temperature = CLAUDE_TEMPERATURE,

    # Retry
    max_retries        = MAX_RETRIES,
    retry_backoff_base = RETRY_BACKOFF_BASE,

    # Video / audio specs
    video_width   = VIDEO_RESOLUTION[0],
    video_height  = VIDEO_RESOLUTION[1],
    shorts_width  = SHORTS_RESOLUTION[0],
    shorts_height = SHORTS_RESOLUTION[1],
    video_fps     = VIDEO_FPS,
    video_bitrate = VIDEO_BITRATE,
    audio_bitrate = AUDIO_BITRATE,
    shorts_duration_target = SHORTS_DURATION_TARGET,

    # Quota
    daily_upload_quota = DAILY_UPLOAD_LIMIT,

    # Compliance
    compliance_block_phrases = BLOCKED_PHRASES,
    disclaimer_text          = DISCLAIMER,
    ai_disclosure            = AI_DISCLOSURE,

    # Timezone
    timezone = TIMEZONE,

    # Paths
    root_dir   = BASE_DIR,
    assets_dir = ASSETS_DIR,
    output_dir = OUTPUT_DIR,
    logs_dir   = LOGS_DIR,
    config_dir = CONFIG_DIR,
    oauth_file = OAUTH_FILE,

    # Data
    tracked_tickers = TRACKED_TICKERS,
    fred_series     = FRED_SERIES,
)
