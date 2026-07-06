#!/usr/bin/env python3
"""
generate_topic_assets.py
Daily LONG-FORM astrology topic video content (for monetization).

Picks the day-of-month topic from content_calendar.json and has Claude write a
structured, narrated educational script: a hook, several sections (each with a
short on-screen heading + bullet points + a spoken narration paragraph), and an
outro. Output: topic_YYYYMMDD.json

Usage:
  python3 generate_topic_assets.py "July 2026" 20260706
  python3 generate_topic_assets.py "July 2026" 20260706 --topic "Custom Title::angle"
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).parent
CALENDAR = HERE / "content_calendar.json"

# Target ~7-9 minutes (mid-roll-ad eligible at 8+). Section count + narration
# length drive the final duration (each card shows for its narration).
N_SECTIONS = 8

SYSTEM_PROMPT = """You are the writer and host of a popular, credible astrology YouTube channel.
You are scripting ONE long-form educational video on a single astrology topic.

Write for spoken delivery: warm, confident, engaging, plain-English — never dry or
academic. Hook curiosity early, deliver real value, and keep viewers watching.

Rules:
- Be specific and genuinely informative. No filler, no repeating the same idea.
- Sound like a knowledgeable human host talking to one viewer.
- Each section's "narration" is 110-150 words of flowing spoken paragraphs.
- Each section's "screen" is 2-4 SHORT on-screen bullet lines (max 6 words each) —
  these summarize the section visually while you narrate the detail.
- The "hook" is one punchy spoken sentence that makes people stay.
- Keep it broadly accurate to real astrology; it's entertainment/education, not
  fortune-telling claims of certainty.

Return ONLY valid raw JSON. No markdown, no code fences, no commentary."""


def _load_topic(date_tag: str, override: str = None):
    """Pick the day's topic. Works for ANY calendar length:
    - ~365 topics → indexed by day-of-YEAR (unique topic all year, repeats yearly)
    - ~30 topics  → the modulo cycles it (repeats roughly monthly)
    Prefers content_calendar_365.json if present, else content_calendar.json."""
    if override and "::" in override:
        title, angle = override.split("::", 1)
        return {"title": title.strip(), "angle": angle.strip(), "category": "custom"}
    cal_file = HERE / "content_calendar_365.json"
    if not cal_file.exists():
        cal_file = CALENDAR
    topics = json.loads(cal_file.read_text(encoding="utf-8"))["topics"]
    doy = datetime.strptime(date_tag, "%Y%m%d").timetuple().tm_yday   # 1..366
    return topics[(doy - 1) % len(topics)]


def _validate(data: dict) -> None:
    for k in ("hook", "sections", "outro"):
        if not data.get(k):
            raise ValueError(f"missing '{k}'")
    secs = data["sections"]
    if not isinstance(secs, list) or len(secs) < 5:
        raise ValueError(f"need >=5 sections, got {len(secs) if isinstance(secs, list) else 'none'}")
    for i, s in enumerate(secs):
        if not s.get("heading") or not s.get("narration"):
            raise ValueError(f"section {i}: missing heading/narration")
        if len(str(s["narration"]).split()) < 60:
            raise ValueError(f"section {i}: narration too short")
        if not isinstance(s.get("screen"), list) or not s["screen"]:
            raise ValueError(f"section {i}: 'screen' must be a non-empty list")


def generate(period: str, date_tag: str = None, override: str = None) -> str:
    client = anthropic.Anthropic(timeout=120)
    if not date_tag:
        date_tag = date.today().strftime("%Y%m%d")
    when = datetime.strptime(date_tag, "%Y%m%d").strftime("%B %d, %Y")
    topic = _load_topic(date_tag, override)

    user_msg = f"""Topic for today's video ({when}):
TITLE THEME: {topic['title']}
ANGLE: {topic['angle']}

Write the full video script with EXACTLY {N_SECTIONS} sections.

Return this EXACT JSON shape:
{{
  "title": "SEO YouTube title, <=100 chars, keyword-first, compelling",
  "description": "2-3 sentence YouTube description + relevant hashtags",
  "tags": ["10-15 lowercase search tags"],
  "hook": "one punchy spoken sentence to open the video",
  "sections": [
    {{"heading": "Short on-screen section title",
      "screen": ["bullet under 6 words", "bullet", "bullet"],
      "narration": "110-150 words of warm spoken paragraphs"}}
  ],
  "outro": "closing spoken line: recap value + ask to subscribe + tease that a new astrology topic comes every day"
}}"""

    print(f"[INFO] Topic: {topic['title']}  → generating script via Claude...")
    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
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
        raise RuntimeError(f"Topic script generation failed after 3 attempts: {last_err}")

    data["content_type"] = "topic"
    data["category"]     = topic.get("category", "astrology")
    data["topic_title"]  = topic["title"]
    data["date"]         = when
    # fps is computed dynamically by make_topic_video from the actual
    # narration-driven total duration (mdv.safe_static_fps) — narration length
    # varies day to day, so a fixed value here would be wrong on other days.
    data.setdefault("hashtags", ["#astrology", "#zodiac", "#horoscope"])
    if "title" not in data:
        data["title"] = f"{topic['title']} | Astrology Explained ({when})"
    data.setdefault("pinned_comment",
                    "What should tomorrow's topic be? Comment below! ⬇️\n"
                    "New astrology deep-dive every day — Subscribe 🔔 "
                    "#astrology #zodiac #horoscope")

    filename = f"topic_{date_tag}.json"
    (HERE / filename).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Assets → {filename}  ({len(data['sections'])} sections)")
    return filename


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_topic_assets.py 'July 2026' [YYYYMMDD] [--topic 'Title::angle']")
        sys.exit(1)
    period   = sys.argv[1]
    date_tag = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else None
    override = None
    if "--topic" in sys.argv:
        override = sys.argv[sys.argv.index("--topic") + 1]
    generate(period, date_tag, override)


if __name__ == "__main__":
    main()
