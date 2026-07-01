#!/usr/bin/env python3
"""
generate_daily_assets.py
Generates all 12 sign horoscope data in ONE Claude API call.
Output: daily_horoscope_YYYYMMDD.json

Usage:
  python3 generate_daily_assets.py "June 2026"
  python3 generate_daily_assets.py "June 2026" 20260621
"""
import json
import re
import sys
from datetime import date, datetime

import anthropic
from dotenv import load_dotenv

load_dotenv()

SIGNS = [
    "aries","taurus","gemini","cancer","leo","virgo",
    "libra","scorpio","sagittarius","capricorn","aquarius","pisces",
]

SYSTEM_PROMPT = """You are a professional astrologer creating daily horoscope cards for all 12 zodiac signs.

For each sign provide SHORT punchy predictions. Each text field must be MAX 10 words — it appears on screen for 12 seconds.

Fields per sign:
- love:         romantic energy today (max 10 words)
- career:       work energy today (max 10 words)
- money:        financial energy today (max 10 words)
- lucky_number: one integer 1-99
- lucky_color:  one color name (1-2 words)
- note:         one important message today (max 12 words)

Style rules:
- Direct. Specific. No generic fluff.
- Each line must feel personal and true TODAY.
- Vary tone across signs — not all positive, some have warnings.
- lucky_number and lucky_color must be a single value (no lists).

Return ONLY valid raw JSON. No markdown. No explanation. No code fences."""


_REQUIRED_FIELDS = ("love", "career", "money", "lucky_number", "lucky_color", "note")


def _validate(data: dict) -> None:
    """Raise ValueError if any sign or required field is missing/empty.
    Guards against a malformed Claude response shipping '—' placeholder cards."""
    signs = data.get("signs")
    if not isinstance(signs, dict):
        raise ValueError("'signs' object missing")
    missing_signs = [s for s in SIGNS if s not in signs]
    if missing_signs:
        raise ValueError(f"missing signs: {missing_signs}")
    for s in SIGNS:
        f = signs[s]
        if not isinstance(f, dict):
            raise ValueError(f"{s}: not an object")
        for k in _REQUIRED_FIELDS:
            v = f.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise ValueError(f"{s}: empty field '{k}'")
            if isinstance(v, (list, dict)):
                raise ValueError(f"{s}: field '{k}' must be a single value, got {type(v).__name__}")


def generate(period: str, date_tag: str = None) -> str:
    client   = anthropic.Anthropic()
    if date_tag:
        today = datetime.strptime(date_tag, "%Y%m%d").strftime("%B %d, %Y")
    else:
        today    = date.today().strftime("%B %d, %Y")
        date_tag = date.today().strftime("%Y%m%d")

    user_msg = f"""Generate today's horoscope for all 12 signs.
Date: {today}
Period: {period}

Return this EXACT JSON structure (fill in all 12 signs):
{{
  "date": "{today}",
  "title": "Daily Horoscope All 12 Signs — {today}",
  "description": "Complete daily horoscope for all 12 zodiac signs — {today}. Love, career, money, lucky numbers and colors. Subscribe for daily cosmic guidance. #horoscope #astrology #zodiac #dailyhoroscope",
  "hashtags": ["#horoscope", "#astrology", "#zodiac", "#dailyhoroscope", "#allsigns", "#lovehoroscope", "#careerhoroscope"],
  "tags": ["daily horoscope", "all 12 signs horoscope", "horoscope today", "astrology today", "zodiac reading", "love horoscope today", "daily astrology", "horoscope {today}"],
  "signs": {{
    "aries":       {{"love": "...", "career": "...", "money": "...", "lucky_number": 7,  "lucky_color": "Red",        "note": "..."}},
    "taurus":      {{"love": "...", "career": "...", "money": "...", "lucky_number": 4,  "lucky_color": "Forest Green","note": "..."}},
    "gemini":      {{"love": "...", "career": "...", "money": "...", "lucky_number": 11, "lucky_color": "Yellow",     "note": "..."}},
    "cancer":      {{"love": "...", "career": "...", "money": "...", "lucky_number": 2,  "lucky_color": "Silver",     "note": "..."}},
    "leo":         {{"love": "...", "career": "...", "money": "...", "lucky_number": 1,  "lucky_color": "Gold",       "note": "..."}},
    "virgo":       {{"love": "...", "career": "...", "money": "...", "lucky_number": 6,  "lucky_color": "Navy Blue",  "note": "..."}},
    "libra":       {{"love": "...", "career": "...", "money": "...", "lucky_number": 9,  "lucky_color": "Rose Pink",  "note": "..."}},
    "scorpio":     {{"love": "...", "career": "...", "money": "...", "lucky_number": 8,  "lucky_color": "Deep Red",   "note": "..."}},
    "sagittarius": {{"love": "...", "career": "...", "money": "...", "lucky_number": 3,  "lucky_color": "Purple",     "note": "..."}},
    "capricorn":   {{"love": "...", "career": "...", "money": "...", "lucky_number": 10, "lucky_color": "Dark Brown", "note": "..."}},
    "aquarius":    {{"love": "...", "career": "...", "money": "...", "lucky_number": 5,  "lucky_color": "Electric Blue","note":"..."}},
    "pisces":      {{"love": "...", "career": "...", "money": "...", "lucky_number": 12, "lucky_color": "Sea Green",  "note": "..."}}
  }}
}}"""

    print(f"[INFO] Generating all 12 signs via Claude...")

    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            candidate = json.loads(raw)
            _validate(candidate)
            data = candidate
            break
        except Exception as e:
            last_err = e
            print(f"[WARN] Attempt {attempt}/3 failed: {e}", file=sys.stderr)

    if data is None:
        raise RuntimeError(f"Claude asset generation failed after 3 attempts: {last_err}")

    filename = f"daily_horoscope_{date_tag}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Assets → {filename}")
    return filename


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_daily_assets.py 'June 2026' [YYYYMMDD]")
        sys.exit(1)
    period   = sys.argv[1]
    date_tag = sys.argv[2] if len(sys.argv) > 2 else None
    generate(period, date_tag)


if __name__ == "__main__":
    main()
