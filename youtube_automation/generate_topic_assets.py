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
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).parent
CALENDAR = HERE / "content_calendar.json"

# Target UNDER 3 MINUTES (hard requirement: every long-form type except the
# weekly horoscopes stays <=3min, to keep daily renders fast and the upload
# schedule inside the YouTube API quota). Section count + per-section word
# budget drive the final duration (each card shows for its narration):
# hook (~8s) + 4 x ~65 words (~27s each) + outro (~10s) ≈ 2m10s.
N_SECTIONS = 4

# NOTE: section cards no longer show static bullet lists (that read as a
# PowerPoint slide). The video now burns live word-synced captions generated
# directly from the "narration" audio (via edge-tts word-boundary timing), so
# the on-screen text IS the spoken words, appearing in sync like TikTok/Reels
# captions — "screen" bullets are no longer requested from Claude.
SYSTEM_PROMPT = """You are the writer and host of a popular, credible astrology YouTube channel.
You are scripting ONE long-form educational video on a single astrology topic.
Your audience is in the UNITED STATES — use American framing, examples,
dates, and (where signs are named for current sky events) the tropical
zodiac that mainstream US astrology uses.

Write for spoken delivery: warm, confident, engaging, plain-English — never dry or
academic. Hook curiosity early, deliver real value, and keep viewers watching.

Rules:
- Be specific and genuinely informative. No filler, no repeating the same idea.
- Sound like a knowledgeable human host talking to one viewer, with natural
  rhythm and short punchy sentences — this will be shown as animated captions
  synced word-by-word to your narration, so avoid long run-on sentences.
- Each section's "narration" is 55-75 words of flowing spoken prose —
  tight and punchy; the WHOLE video must run under 3 minutes.
- Each section's "heading" is a SHORT punchy title (max 5 words) shown above
  the captions the whole time the section plays.
- RETENTION CRAFT (this is what keeps people watching):
  * The "hook" is one spoken sentence built on a curiosity gap — promise a
    specific payoff without giving it away ("the third one surprises even
    astrologers"), never a generic welcome.
  * OPEN A LOOP early: the first section should reference something you'll
    only resolve in the last section ("keep that in mind — it changes
    everything at the end").
  * Add ONE pattern interrupt in a middle section — a short turn like
    "but here's the twist" / "now the part nobody talks about".
  * Speak directly to the viewer ("your chart", "you've felt this") —
    second person beats lecture voice.
  * The outro resolves the open loop, then asks to subscribe.
- Keep it broadly accurate to real astrology; it's entertainment/education, not
  fortune-telling claims of certainty.

Return ONLY valid raw JSON. No markdown, no code fences, no commentary."""

# Per-category safety instructions, injected into every script-writing call
# for these categories — deterministic, not dependent on whether the
# calendar's one-sentence "angle" text happens to carry the right framing
# that day (build_calendar.py's guidance steers Claude toward safe topics,
# but the ANGLE it returns per-topic is not guaranteed to restate the
# constraint, and this SYSTEM_PROMPT is otherwise fully generic). A short
# disclaimer is also hardcoded onto the outro/description below — not left
# to chance either.
CATEGORY_SAFETY = {
    "political": (
        "SAFETY: this is a POLITICAL/mundane-astrology topic. Keep it EVERGREEN, "
        "HISTORICAL, and NON-PARTISAN. Do NOT predict outcomes for any current or "
        "upcoming election. Do NOT make speculative, opinionated, or predictive "
        "claims about any specific LIVING politician's character, agenda, or "
        "future actions. Stick to documented historical facts and general "
        "astrological cycle patterns."
    ),
    "stockmarket": (
        "SAFETY: this is a STOCK MARKET astrology topic. Frame everything as "
        "general historical patterns and educational commentary. Do NOT "
        "recommend, predict, or imply any specific real-time stock, ticker, or "
        "trade. This is entertainment/education — NEVER financial advice."
    ),
    "crypto": (
        "SAFETY: this is a CRYPTOCURRENCY astrology topic. Frame everything as "
        "general historical patterns and educational commentary. Do NOT "
        "recommend, predict, or imply any specific real-time coin, token, or "
        "trade. This is entertainment/education — NEVER financial advice."
    ),
    "celebrity": (
        "SAFETY: this is a CELEBRITY astrology topic. Use ONLY publicly known "
        "birth dates and public career facts/achievements. Do NOT speculate "
        "about any real person's private life, relationships, health, or "
        "future. Keep it light, factual, and entertainment-focused."
    ),
}

_DISCLAIMER_SUFFIX = {
    "political":   "This video is for entertainment and historical astrology education only — not a political endorsement or election prediction.",
    "stockmarket": "This video is for entertainment and astrological education only — not financial or trading advice.",
    "crypto":      "This video is for entertainment and astrological education only — not financial or trading advice.",
    "celebrity":   "All commentary uses publicly known birth information for entertainment purposes only.",
}


def _load_topic(date_tag: str, override: str = None):
    """Pick the day's topic. Priority:
    1. an explicit --topic override
    2. a REAL astronomical event today (astro_events.py — full/new moon,
       eclipse, retrograde station, season start). US astrology search
       traffic spikes on these exact days, so the event video replaces the
       evergreen calendar topic and rides the spike. Disable with
       ASTRO_EVENTS_ENABLED=false in .env.
    3. the calendar: content_calendar_365.json if present (unique topic per
       day-of-year), else the small starter calendar (cycles ~monthly)."""
    if override and "::" in override:
        title, angle = override.split("::", 1)
        return {"title": title.strip(), "angle": angle.strip(), "category": "custom"}
    if os.getenv("ASTRO_EVENTS_ENABLED", "true").lower() == "true":
        try:
            import astro_events
            ev = astro_events.event_for(date_tag)
            if ev:
                print(f"[INFO] Real astro event today ({ev['kind']}) — overriding "
                      f"calendar topic: {ev['title']}")
                return {"title": ev["title"], "angle": ev["angle"],
                        "category": ev["category"]}
        except Exception as e:
            # An event-detection hiccup must never kill the daily video —
            # fall through to the calendar topic.
            print(f"[WARN] astro_events check failed ({e}) — using calendar topic",
                  file=sys.stderr)
    cal_file = HERE / "content_calendar_365.json"
    if not cal_file.exists():
        cal_file = CALENDAR
    topics = json.loads(cal_file.read_text(encoding="utf-8"))["topics"]
    n = len(topics)
    doy = datetime.strptime(date_tag, "%Y%m%d").timetuple().tm_yday   # 1..366
    # A calendar sized ~365 is meant to give every day a UNIQUE topic. Plain
    # modulo breaks that in a leap year: day 366 -> (366-1)%365 == 0, the same
    # index as day 1 — Dec 31 would collide with New Year's Day's topic
    # instead of getting its own. Cap instead of wrap for a full-length
    # calendar (day 366 repeats day 365 — "yesterday", far less jarring).
    # A small calendar (e.g. the ~30-topic starter) is INTENDED to cycle via
    # modulo (roughly-monthly repeats) — only a full ~365 calendar gets the cap.
    idx = min(doy, n) - 1 if n >= 365 else (doy - 1) % n
    return topics[idx]


def _validate(data: dict) -> None:
    for k in ("hook", "sections", "outro"):
        if not data.get(k):
            raise ValueError(f"missing '{k}'")
    secs = data["sections"]
    # EXACTLY N_SECTIONS — an under-count starves the video, but an OVER-count
    # is worse: narration drives duration, so 6x90-word sections would render
    # ~3.5 min, fail the 200s quality gate AFTER the full render, and drop the
    # day's upload. The old `< 4` check only caught the under-count.
    if not isinstance(secs, list) or len(secs) != N_SECTIONS:
        raise ValueError(f"need EXACTLY {N_SECTIONS} sections, got "
                         f"{len(secs) if isinstance(secs, list) else 'none'}")
    # Hook/outro are narrated too — unbounded, they'd silently stretch the
    # video past the cap the section budget was tuned for.
    for field, hi in (("hook", 30), ("outro", 45)):
        w = len(str(data[field]).split())
        if not (4 <= w <= hi):
            raise ValueError(f"'{field}' must be 4-{hi} spoken words, got {w}")
    for i, s in enumerate(secs):
        if not s.get("heading") or not s.get("narration"):
            raise ValueError(f"section {i}: missing heading/narration")
        words = len(str(s["narration"]).split())
        if words < 45:
            raise ValueError(f"section {i}: narration too short ({words} words)")
        if words > 90:
            raise ValueError(f"section {i}: narration too long ({words} words — "
                             f"the video must stay under 3 minutes)")


def generate(period: str, date_tag: str = None, override: str = None) -> str:
    client = anthropic.Anthropic(timeout=120)
    if not date_tag:
        date_tag = date.today().strftime("%Y%m%d")
    when = datetime.strptime(date_tag, "%Y%m%d").strftime("%B %d, %Y")
    topic = _load_topic(date_tag, override)

    category = topic.get("category", "astrology")
    safety_block = CATEGORY_SAFETY.get(category, "")

    user_msg = f"""Topic for today's video ({when}):
TITLE THEME: {topic['title']}
ANGLE: {topic['angle']}
{f"{chr(10)}{safety_block}{chr(10)}" if safety_block else ""}
Write the full video script with EXACTLY {N_SECTIONS} sections.

Return this EXACT JSON shape:
{{
  "title": "SEO YouTube title, <=100 chars, keyword-first, compelling",
  "description": "2-3 sentence YouTube description + relevant hashtags",
  "tags": ["10-15 lowercase search tags"],
  "hook": "one punchy spoken sentence to open the video",
  "sections": [
    {{"heading": "Short on-screen section title (max 5 words)",
      "narration": "55-75 words of warm spoken prose, short punchy sentences",
      "image_query": "2-4 word stock-photo search phrase matching this section",
      "image_fallback": "1-2 word broader stock-photo phrase"}}
  ],
  "outro": "closing spoken line: recap value + ask to subscribe + tease that a new astrology topic comes every day"
}}"""

    print(f"[INFO] Topic: {topic['title']}  → generating script via Claude...")
    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            # Feed the previous rejection back so the retry actually fixes it
            # (same pattern as generate_tarot_assets — a blind identical retry
            # tends to fail the same validation three times).
            msg = user_msg if last_err is None else (
                f"{user_msg}\n\nYour previous attempt was rejected: {last_err}. "
                f"Fix exactly that and return the corrected JSON.")
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": msg}],
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
    data["category"]     = category
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

    # Hardcoded disclaimer for sensitive categories — appended in code, not
    # left to Claude's discretion, so it's guaranteed present every time
    # regardless of what the script otherwise says (same pattern as the
    # sports-astrology pipeline's DISCLAIMER).
    disclaimer = _DISCLAIMER_SUFFIX.get(category)
    if disclaimer:
        data["outro"] = f"{data.get('outro', '').rstrip('. ')}. {disclaimer}"
        data["description"] = f"{data.get('description', '')}\n\n{disclaimer}"

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
