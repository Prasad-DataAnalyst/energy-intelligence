#!/usr/bin/env python3
"""
generate_daily_assets.py
Generates all 12 sign horoscope data in ONE Claude API call.
Output: daily_horoscope_YYYYMMDD.json

Usage:
  python3 generate_daily_assets.py "June 2026"
"""
import json
import re
import sys
from datetime import date

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


def generate(period: str) -> str:
    client   = anthropic.Anthropic()
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
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    raw = response.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw)
    raw = re.sub(r"\n?```$", "", raw)

    data     = json.loads(raw)
    filename = f"daily_horoscope_{date_tag}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Assets → {filename}")
    return filename


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_daily_assets.py 'June 2026'")
        sys.exit(1)
    generate(" ".join(sys.argv[1:]))


if __name__ == "__main__":
    main()
