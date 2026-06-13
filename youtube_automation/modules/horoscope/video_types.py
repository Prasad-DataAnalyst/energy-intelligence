"""
modules/horoscope/video_types.py
Defines the 5 video types and their production parameters for the
GetMindFuelNow horoscope YouTube automation system.

Video types:
  daily        — every day at 19:00 UTC
  weekly       — every Monday at 18:00 UTC
  monthly      — first of month at 17:00 UTC
  yearly       — January 1st at 12:00 UTC
  special_event — triggered by astronomical events (eclipses, retrogrades, etc.)
"""

from __future__ import annotations

import datetime
from typing import Any

# ---------------------------------------------------------------------------
# Video type definitions
# ---------------------------------------------------------------------------

VIDEO_TYPES: dict[str, dict[str, Any]] = {
    "daily": {
        "schedule": "every_day",
        "title_format": "Daily Horoscope {month} {day}, {year} — All 12 Signs ✨",
        "duration_per_sign": 60,       # seconds of video per sign
        "intro_duration": 30,          # seconds for opening segment
        "outro_duration": 30,          # seconds for closing segment
        "upload_time": "19:00",        # UTC
        "privacy_status": "public",
        "category_id": "22",           # People & Blogs
        "made_for_kids": False,
        "description_template": "daily",
        "thumbnail_style": "dark_purple_daily",
        "background_style": "cosmic_gradient",
        "music_energy": "calm",
        "voice_tone": "warm",
        "tags": [
            "daily horoscope", "horoscope today", "today's horoscope",
            "daily astrology", "all 12 signs", "zodiac reading",
            "aries horoscope today", "taurus horoscope today",
            "gemini horoscope today", "cancer horoscope today",
            "leo horoscope today", "virgo horoscope today",
            "libra horoscope today", "scorpio horoscope today",
            "sagittarius horoscope today", "capricorn horoscope today",
            "aquarius horoscope today", "pisces horoscope today",
            "astrology today", "daily cosmic guidance",
            "GetMindFuelNow", "horoscope", "astrology", "zodiac",
        ],
        "target_total_duration": 30 + 60 * 12 + 30,  # intro + 12×60s + outro = 780s
    },

    "weekly": {
        "schedule": "every_monday",
        "title_format": "Weekly Horoscope {month} {day}-{end_day}, {year} — All Signs 🌙",
        "duration_per_sign": 120,
        "intro_duration": 45,
        "outro_duration": 45,
        "upload_time": "18:00",
        "privacy_status": "public",
        "category_id": "22",
        "made_for_kids": False,
        "description_template": "weekly",
        "thumbnail_style": "dark_blue_weekly",
        "background_style": "starfield_gradient",
        "music_energy": "medium",
        "voice_tone": "authoritative",
        "tags": [
            "weekly horoscope", "weekly astrology", "week ahead forecast",
            "horoscope this week", "weekly zodiac reading", "all 12 signs",
            "aries weekly", "taurus weekly", "gemini weekly",
            "cancer weekly", "leo weekly", "virgo weekly",
            "libra weekly", "scorpio weekly", "sagittarius weekly",
            "capricorn weekly", "aquarius weekly", "pisces weekly",
            "week horoscope", "astrology this week", "weekly forecast",
            "GetMindFuelNow", "horoscope", "astrology", "zodiac",
        ],
        "target_total_duration": 45 + 120 * 12 + 45,  # = 1530s
    },

    "monthly": {
        "schedule": "first_of_month",
        "title_format": "Monthly Horoscope {month} {year} — All 12 Signs 🌟",
        "duration_per_sign": 240,
        "intro_duration": 60,
        "outro_duration": 60,
        "upload_time": "17:00",
        "privacy_status": "public",
        "category_id": "22",
        "made_for_kids": False,
        "description_template": "monthly",
        "thumbnail_style": "gold_monthly",
        "background_style": "nebula_gradient",
        "music_energy": "epic",
        "voice_tone": "warm",
        "tags": [
            "monthly horoscope", "monthly astrology", "month horoscope",
            "horoscope this month", "monthly zodiac", "astrology forecast",
            "aries monthly", "taurus monthly", "gemini monthly",
            "cancer monthly", "leo monthly", "virgo monthly",
            "libra monthly", "scorpio monthly", "sagittarius monthly",
            "capricorn monthly", "aquarius monthly", "pisces monthly",
            "monthly predictions", "monthly reading", "cosmic forecast",
            "GetMindFuelNow", "horoscope", "astrology", "zodiac",
        ],
        "target_total_duration": 60 + 240 * 12 + 60,  # = 3000s
    },

    "yearly": {
        "schedule": "january_1",
        "title_format": "Yearly Horoscope {year} — All 12 Zodiac Signs Complete Reading 🔮",
        "duration_per_sign": 480,
        "intro_duration": 120,
        "outro_duration": 120,
        "upload_time": "12:00",
        "privacy_status": "public",
        "category_id": "22",
        "made_for_kids": False,
        "description_template": "yearly",
        "thumbnail_style": "cosmic_purple_yearly",
        "background_style": "deep_space_gradient",
        "music_energy": "epic",
        "voice_tone": "authoritative",
        "tags": [
            "yearly horoscope", "annual horoscope", "2026 horoscope",
            "horoscope 2026", "year ahead astrology", "yearly predictions",
            "aries 2026", "taurus 2026", "gemini 2026",
            "cancer 2026", "leo 2026", "virgo 2026",
            "libra 2026", "scorpio 2026", "sagittarius 2026",
            "capricorn 2026", "aquarius 2026", "pisces 2026",
            "2026 zodiac", "annual forecast", "yearly reading",
            "astrology 2026", "2026 predictions",
            "GetMindFuelNow", "horoscope", "astrology", "zodiac",
        ],
        "target_total_duration": 120 + 480 * 12 + 120,  # = 6000s
    },

    "special_event": {
        "schedule": "on_event",
        "title_format": "{event_name} Horoscope {year} — How It Affects Your Sign 🌕",
        "duration_per_sign": 90,
        "intro_duration": 45,
        "outro_duration": 45,
        "upload_time": "20:00",        # upload day-of-event
        "privacy_status": "public",
        "category_id": "22",
        "made_for_kids": False,
        "description_template": "special_event",
        "thumbnail_style": "event_gold",
        "background_style": "eclipse_gradient",
        "music_energy": "mysterious",
        "voice_tone": "warm",
        "tags": [
            "mercury retrograde", "full moon horoscope", "new moon horoscope",
            "lunar eclipse", "solar eclipse", "eclipse horoscope",
            "retrograde horoscope", "full moon astrology", "new moon astrology",
            "celestial event", "planetary alignment", "all 12 signs",
            "aries", "taurus", "gemini", "cancer", "leo", "virgo",
            "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
            "special astrology event", "astronomical event horoscope",
            "GetMindFuelNow", "horoscope", "astrology", "zodiac",
        ],
        "target_total_duration": 45 + 90 * 12 + 45,  # = 1170s
    },
}

# ---------------------------------------------------------------------------
# Special astronomical events for 2026
# ---------------------------------------------------------------------------

SPECIAL_EVENTS_2026: list[dict[str, str]] = [
    {"date": "2026-01-14", "name": "Mercury Retrograde in Capricorn",     "type": "retrograde"},
    {"date": "2026-01-29", "name": "Full Moon in Leo",                    "type": "full_moon"},
    {"date": "2026-02-13", "name": "New Moon in Aquarius",                "type": "new_moon"},
    {"date": "2026-02-28", "name": "Full Moon in Virgo",                  "type": "full_moon"},
    {"date": "2026-03-14", "name": "Lunar Eclipse in Virgo",              "type": "eclipse"},
    {"date": "2026-03-29", "name": "Full Moon in Libra",                  "type": "full_moon"},
    {"date": "2026-04-05", "name": "Mercury Retrograde in Aries",         "type": "retrograde"},
    {"date": "2026-04-13", "name": "New Moon in Aries",                   "type": "new_moon"},
    {"date": "2026-04-28", "name": "Solar Eclipse in Aries",              "type": "eclipse"},
    {"date": "2026-05-12", "name": "Full Moon in Scorpio",                "type": "full_moon"},
    {"date": "2026-05-27", "name": "New Moon in Gemini",                  "type": "new_moon"},
    {"date": "2026-06-11", "name": "Full Moon in Sagittarius",            "type": "full_moon"},
    {"date": "2026-06-21", "name": "Summer Solstice Horoscope",           "type": "solstice"},
    {"date": "2026-07-10", "name": "Full Moon in Capricorn",              "type": "full_moon"},
    {"date": "2026-07-25", "name": "New Moon in Leo",                     "type": "new_moon"},
    {"date": "2026-08-09", "name": "Full Moon in Aquarius",               "type": "full_moon"},
    {"date": "2026-08-23", "name": "Mercury Retrograde in Virgo",         "type": "retrograde"},
    {"date": "2026-09-07", "name": "Lunar Eclipse in Pisces",             "type": "eclipse"},
    {"date": "2026-09-23", "name": "Autumn Equinox Horoscope",            "type": "equinox"},
    {"date": "2026-10-06", "name": "Full Moon in Aries",                  "type": "full_moon"},
    {"date": "2026-10-21", "name": "New Moon in Libra",                   "type": "new_moon"},
    {"date": "2026-11-05", "name": "Full Moon in Taurus",                 "type": "full_moon"},
    {"date": "2026-11-12", "name": "Mercury Retrograde in Scorpio",       "type": "retrograde"},
    {"date": "2026-11-20", "name": "New Moon in Scorpio",                 "type": "new_moon"},
    {"date": "2026-12-04", "name": "Full Moon in Gemini",                 "type": "full_moon"},
    {"date": "2026-12-20", "name": "New Moon in Capricorn",               "type": "new_moon"},
    {"date": "2026-12-21", "name": "Winter Solstice Horoscope",           "type": "solstice"},
]

# Quick lookup: date-string → event dict
_EVENTS_BY_DATE: dict[str, dict] = {e["date"]: e for e in SPECIAL_EVENTS_2026}


# ---------------------------------------------------------------------------
# Schedule helpers
# ---------------------------------------------------------------------------

def get_todays_video_types(date: datetime.date | None = None) -> list[str]:
    """
    Determine which video type(s) to produce today.

    Returns a list of type keys. 'daily' is ALWAYS included.
    Special events layer on top — they do NOT replace the daily.

    Priority order if multiple non-daily types apply on the same day:
      yearly > monthly > weekly > special_event > daily
    """
    if date is None:
        date = datetime.date.today()

    types = []

    # Yearly: January 1st
    if date.month == 1 and date.day == 1:
        types.append("yearly")

    # Monthly: first of month
    if date.day == 1:
        types.append("monthly")

    # Weekly: every Monday (weekday() == 0)
    if date.weekday() == 0:
        types.append("weekly")

    # Special event: check lookup table
    date_str = date.isoformat()
    if date_str in _EVENTS_BY_DATE:
        types.append("special_event")

    # Daily always runs
    types.append("daily")

    return types


def get_event_for_date(date: datetime.date | None = None) -> dict | None:
    """Return the special event dict for a given date, or None."""
    if date is None:
        date = datetime.date.today()
    return _EVENTS_BY_DATE.get(date.isoformat())


def is_special_event_day(date: datetime.date | None = None) -> bool:
    """Return True if today is a special astronomical event day."""
    if date is None:
        date = datetime.date.today()
    return date.isoformat() in _EVENTS_BY_DATE


def get_upload_time(video_type: str) -> str:
    """Return the configured upload time (HH:MM UTC) for a video type."""
    return VIDEO_TYPES.get(video_type, {}).get("upload_time", "19:00")


def get_total_video_duration(video_type: str) -> int:
    """Return the expected total video duration in seconds."""
    vt = VIDEO_TYPES.get(video_type, {})
    return vt.get("target_total_duration", vt.get("intro_duration", 30) + vt.get("duration_per_sign", 60) * 12 + vt.get("outro_duration", 30))


def get_duration_per_sign(video_type: str) -> int:
    """Return seconds allocated per zodiac sign for a given video type."""
    return VIDEO_TYPES.get(video_type, {}).get("duration_per_sign", 60)


def get_intro_duration(video_type: str) -> int:
    """Return seconds allocated for the intro segment."""
    return VIDEO_TYPES.get(video_type, {}).get("intro_duration", 30)


def get_outro_duration(video_type: str) -> int:
    """Return seconds allocated for the outro segment."""
    return VIDEO_TYPES.get(video_type, {}).get("outro_duration", 30)


def format_video_title(video_type: str, date: datetime.date | None = None,
                       event_name: str = "") -> str:
    """Format the video title using the type's title_format template."""
    if date is None:
        date = datetime.date.today()

    vt = VIDEO_TYPES.get(video_type, {})
    fmt = vt.get("title_format", "Horoscope {year}")

    week_end = date + datetime.timedelta(days=6)
    return fmt.format(
        month=date.strftime("%B"),
        day=date.strftime("%d").lstrip("0"),
        end_day=week_end.strftime("%d").lstrip("0"),
        year=date.year,
        event_name=event_name,
    )
