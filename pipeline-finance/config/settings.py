"""Central configuration — all tunables live here, loaded from .env."""
import os
from pathlib import Path
from typing import Optional
from pydantic import Field
from pydantic_settings import BaseSettings

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    # ── Identity ─────────────────────────────────────────────────────────────
    channel_name: str = "Drift Wire326"
    channel_handle: str = "@DriftWire326"
    channel_niche: str = "US stock market news, economic data, financial education"
    target_audience: str = "US viewers, Gen Z + Millennial investors"
    tone: str = "Punchy, energetic, credible finance news"

    # ── API Keys ──────────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(default="", validation_alias="ANTHROPIC_API_KEY")
    elevenlabs_api_key: str = Field(default="", validation_alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field(default="21m00Tcm4TlvDq8ikWAM", validation_alias="ELEVENLABS_VOICE_ID")
    alpha_vantage_key: str = Field(default="", validation_alias="ALPHA_VANTAGE_KEY")
    fred_api_key: str = Field(default="", validation_alias="FRED_API_KEY")
    youtube_client_secrets: Path = Field(
        default=ROOT / "config" / "finance_oauth.json",
        validation_alias="YOUTUBE_CLIENT_SECRETS",
    )

    # ── AI Model ──────────────────────────────────────────────────────────────
    claude_model: str = "claude-sonnet-4-6"
    claude_max_tokens: int = 4096
    claude_temperature: float = 0.7

    # ── Video Specs ───────────────────────────────────────────────────────────
    video_width: int = 1920
    video_height: int = 1080
    video_fps: int = 30
    shorts_width: int = 1080
    shorts_height: int = 1920
    video_bitrate: str = "8000k"
    audio_bitrate: str = "192k"

    # ── Upload Limits ─────────────────────────────────────────────────────────
    daily_upload_quota: int = 6          # YouTube free quota safe cap
    max_retries: int = 3
    retry_backoff_base: float = 2.0      # seconds, exponential

    # ── Scheduling ────────────────────────────────────────────────────────────
    # Weekday: market-open recap posts at 5 PM ET; pre-market at 8 AM ET
    weekday_publish_times: list[str] = ["08:00", "17:00"]
    sunday_publish_time: str = "11:00"
    timezone: str = "America/New_York"

    # ── Content Lengths ───────────────────────────────────────────────────────
    main_video_duration_target: int = 480   # seconds (8 min sweet spot for mid-roll)
    shorts_duration_target: int = 55        # under 60s
    script_word_target: int = 1200          # ~8 min at 150 wpm

    # ── Brand Colors (hex) ────────────────────────────────────────────────────
    brand_primary: str = "#FF0033"
    brand_secondary: str = "#0A0A0F"
    brand_accent: str = "#FFD700"
    brand_text: str = "#FFFFFF"

    # ── Paths ──────────────────────────────────────────────────────────────────
    root_dir: Path = ROOT
    output_dir: Path = ROOT / "output"
    assets_dir: Path = ROOT / "assets"
    logs_dir: Path = ROOT / "logs"

    # ── Compliance ────────────────────────────────────────────────────────────
    disclaimer_text: str = (
        "This video is for educational and entertainment purposes only. "
        "Nothing here constitutes financial, investment, or legal advice. "
        "Always do your own research before making any investment decisions."
    )
    compliance_block_phrases: list[str] = [
        "guaranteed return",
        "100% safe",
        "risk free",
        "can't lose",
        "buy now",
        "investment advice",
        "you should buy",
        "you should sell",
    ]

    # ── Market Data Sources ───────────────────────────────────────────────────
    tracked_tickers: list[str] = [
        "SPY", "QQQ", "DIA", "IWM",        # major ETFs
        "AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL",  # mag-7
        "BRK-B", "JPM", "GS",               # finance
        "^VIX", "^TNX", "GLD", "BTC-USD",  # macro indicators
    ]
    fred_series: dict[str, str] = {
        "FEDFUNDS": "Fed Funds Rate",
        "CPIAUCSL": "CPI Inflation",
        "UNRATE": "Unemployment Rate",
        "GDP": "GDP Growth",
        "T10YIE": "10yr Breakeven Inflation",
        "DFF": "Effective Fed Funds Rate",
    }

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
        "populate_by_name": True,
    }


settings = Settings()
