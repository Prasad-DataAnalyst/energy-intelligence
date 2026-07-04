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

Cover the 5 things people actually check in a daily horoscope:
Love & Relationships, Career & Business, Money & Finance, Health & Energy, and Lucky Guidance.

For each sign provide SHORT punchy predictions. Each text field must be MAX 6 words —
it appears on screen for 14 seconds AND is read aloud by a narrator, and all five
categories must fit in the narration. Six words forces the good kind of punchy:
"An old flame texts you tonight" beats a vague full sentence.

Fields per sign:
- love:         romance, relationships, connections today (MAX 6 words)
- career:       job, business, workplace energy today (MAX 6 words)
- money:        income, spending, investments today (MAX 6 words)
- health:       physical energy, stress, mood today (MAX 6 words)
- lucky_number: one integer 1-99
- lucky_color:  one color name (1-2 words)
- best_time:    the luckiest time of day, e.g. "4 PM" or "Early Morning" (1-3 words)
- advice:       one simple actionable tip or remedy for today (MAX 6 words)

Style rules:
- Direct. Specific. No generic fluff.
- Each line must feel personal and true TODAY.
- Vary tone across signs — not all positive, some have warnings.
- lucky_number, lucky_color and best_time must be single values (no lists).

Return ONLY valid raw JSON. No markdown. No explanation. No code fences."""


_REQUIRED_FIELDS = ("love", "career", "money", "health",
                    "lucky_number", "lucky_color", "best_time", "advice")


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
    # 90s per attempt: the runner caps this whole script at 300s, so the SDK's
    # default 600s timeout would let one hung attempt eat the entire budget.
    client   = anthropic.Anthropic(timeout=90)
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
  "title": "Daily Horoscope Today, {today} — All 12 Zodiac Signs (Love, Career, Money)",
  "description": "Complete daily horoscope for all 12 zodiac signs — {today}. Love, career, money, health, lucky number, lucky color and best time of day. Subscribe for daily cosmic guidance. #horoscope #astrology #zodiac #dailyhoroscope",
  "hashtags": ["#horoscope", "#astrology", "#zodiac", "#dailyhoroscope", "#allsigns", "#lovehoroscope", "#careerhoroscope"],
  "tags": ["daily horoscope", "all 12 signs horoscope", "horoscope today", "astrology today", "zodiac reading", "love horoscope today", "health horoscope", "money horoscope today", "daily astrology", "horoscope {today}"],
  "signs": {{
    "aries":       {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 7,  "lucky_color": "Red",         "best_time": "4 PM",  "advice": "..."}},
    "taurus":      {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 4,  "lucky_color": "Forest Green","best_time": "10 AM", "advice": "..."}},
    "gemini":      {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 11, "lucky_color": "Yellow",      "best_time": "2 PM",  "advice": "..."}},
    "cancer":      {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 2,  "lucky_color": "Silver",      "best_time": "9 PM",  "advice": "..."}},
    "leo":         {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 1,  "lucky_color": "Gold",        "best_time": "Noon",  "advice": "..."}},
    "virgo":       {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 6,  "lucky_color": "Navy Blue",   "best_time": "8 AM",  "advice": "..."}},
    "libra":       {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 9,  "lucky_color": "Rose Pink",   "best_time": "5 PM",  "advice": "..."}},
    "scorpio":     {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 8,  "lucky_color": "Deep Red",    "best_time": "11 PM", "advice": "..."}},
    "sagittarius": {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 3,  "lucky_color": "Purple",      "best_time": "3 PM",  "advice": "..."}},
    "capricorn":   {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 10, "lucky_color": "Dark Brown",  "best_time": "7 AM",  "advice": "..."}},
    "aquarius":    {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 5,  "lucky_color": "Electric Blue","best_time": "6 PM", "advice": "..."}},
    "pisces":      {{"love": "...", "career": "...", "money": "...", "health": "...", "lucky_number": 12, "lucky_color": "Sea Green",   "best_time": "Sunset","advice": "..."}}
  }}
}}"""

    print(f"[INFO] Generating all 12 signs via Claude...")

    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=6000,
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
