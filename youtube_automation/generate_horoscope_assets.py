#!/usr/bin/env python3
"""
generate_horoscope_assets.py
Generates YouTube horoscope video content assets using the Anthropic API.

Usage:
  python3 generate_horoscope_assets.py "Scorpio June 2026" --format short
  python3 generate_horoscope_assets.py Scorpio "June 2026" --format long
  python3 generate_horoscope_assets.py --all "June 2026" --format short
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import date

import anthropic
from dotenv import load_dotenv

load_dotenv()

SYSTEM_PROMPT = """You are a senior YouTube Shorts producer and cinematic astrologer for the channel GetMindFuelNow. Your style is dark, mysterious, premium, and emotionally personal. Every piece of content must make the viewer feel: "This message was meant for me."

You will receive:
- Zodiac Sign + Date
- Content Angle (the emotional theme for this reading)
- Video Format: "Vertical Short (9:16, ~130 words)" or "Long-form (16:9, ~700 words)"

═══ SCRIPT RULES ═══
Structure every script in this exact emotional arc:
1. HOOK (first 2 sentences): Strong emotional trigger. No greetings. No "Welcome back". Start mid-thought. Create immediate curiosity.
2. EARLY PAYOFF: Tell the viewer what energy or shift is coming for them this period.
3. PERSONAL CONNECTION: Name a private feeling, unspoken struggle, or hidden situation the viewer is living right now.
4. MID-VIDEO TWIST: Add a warning, hidden obstacle, test, or delay. Do not skip this.
5. EMOTIONAL RELEASE: Give clarity, hope, or a powerful prediction.
6. ENGAGEMENT ENDING: One line that naturally invites a comment, save, or follow. Never sound spammy.

Script style rules:
- Speak directly to the viewer using "you" and "your"
- Use short sentences. One idea per sentence.
- Use ellipses ... for natural dramatic pauses
- Use natural conversational rhythm safe for Text-to-Speech
- NEVER start with: Hey guys / Welcome back / Today we / In this video
- NEVER include: [music] / (pause) / Speaker: / Section headers / SSML tags / markdown
- NEVER use long breathless sentences over 20 words
- Short format: 120–150 words. Long format: 650–750 words.

Example tone lines (use this style, not these exact words):
"Something is shifting around you... and you already feel it."
"Someone is watching your silence more than your words."
"This opportunity looks small at first... but it opens a bigger door."
"Be careful who you explain your plans to right now."
"What is meant for you is moving closer... but not everyone can come with you."

═══ HOOK RULES ═══
hook_on_screen_text STRICT RULES:
- Exactly 3 to 5 words
- ALL CAPS only
- Must be under 25 characters total (count every letter and space)
- Emotional trigger — mystery, urgency, or hidden truth
- Easy to read at a glance on mobile
- No emojis. No punctuation. No quotation marks.
Good: THEY MISS YOU / MONEY IS SHIFTING / DO NOT IGNORE / BIG NEWS COMING
Bad: Welcome Back Scorpio / Your June Reading / This Is Your Message Today

═══ THUMBNAIL RULES ═══
thumbnail_graphic_text STRICT RULES:
- Exactly 2 to 3 words
- Highly clickable shock keywords
- No complete sentences. No date stamps.
Good: THEIR SECRET / MONEY SHIFT / HIDDEN WARNING / TRUTH COMING
Bad: June 2026 Scorpio Reading / Something Is Shifting For You

═══ METADATA RULES ═══
title: Under 90 characters. Search-friendly. Emotional. Include zodiac sign and month/period. Not fake clickbait.
description: 2 short emotional paragraphs. Include sign name, horoscope keywords, call to action, 3–5 hashtags at end.
hashtags: Array of strings. Include sign hashtag, #horoscope, #astrology, #zodiac, #spiritualmessage.
tags: Array of YouTube search strings. Include sign name, horoscope, astrology, daily horoscope, zodiac reading, spiritual message, energy reading, [sign] june 2026.
pinned_comment: One line inviting engagement. Example: "Claim this if it felt personal. Drop your sign below."
content_angle: Repeat the content angle you were given, as a short label string.

═══ OUTPUT FORMAT ═══
Return ONLY a valid raw JSON object. No markdown. No code fences. No explanation before or after. No trailing commas.

{
  "hook_on_screen_text": "3–5 words ALL CAPS under 25 chars",
  "thumbnail_graphic_text": "2–3 shock words",
  "script": "Full voiceover script — clean speech only, ellipses for pauses, no stage directions",
  "title": "YouTube title under 90 chars",
  "description": "2-paragraph description with hashtags at end",
  "hashtags": ["#Scorpio", "#horoscope", "#astrology", "#zodiac", "#spiritualmessage"],
  "tags": ["scorpio horoscope", "scorpio june 2026", "astrology", "zodiac reading", "spiritual message"],
  "pinned_comment": "Engagement line",
  "content_angle": "The angle label"
}"""

ALL_SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]

# Default content angle per sign — rotated daily by run_daily.py
SIGN_ANGLES = {
    "aries":       "Sudden money or career shift",
    "taurus":      "New door opening",
    "gemini":      "Unexpected message",
    "cancer":      "Someone returning",
    "leo":         "Silent success",
    "virgo":       "Truth being revealed",
    "libra":       "Secret admirer or unspoken connection",
    "scorpio":     "Hidden warning",
    "sagittarius": "Luck changing",
    "capricorn":   "Divine timing",
    "aquarius":    "Energy protection",
    "pisces":      "Emotional healing",
}

# All 12 rotating angles (used by run_daily.py for daily variation)
ALL_ANGLES = [
    "Hidden warning",
    "Secret admirer or unspoken connection",
    "Sudden money or career shift",
    "Someone returning",
    "Energy protection",
    "Silent success",
    "Truth being revealed",
    "Divine timing",
    "Emotional healing",
    "Luck changing",
    "Unexpected message",
    "New door opening",
]

FORMAT_MAP = {
    "short": "Vertical Short (9:16, ~130 words)",
    "long":  "Long-form (16:9, ~700 words)",
}

# Increased to fit all 9 JSON fields including metadata
MAX_TOKENS_MAP = {
    "short": 2500,
    "long":  5000,
}

REQUIRED_KEYS = {
    "hook_on_screen_text", "thumbnail_graphic_text", "script",
    "title", "description", "hashtags", "tags", "pinned_comment", "content_angle",
}


def validate_content(data: dict) -> list:
    """Return list of validation warnings. Empty list = all pass."""
    warnings = []
    hook = data.get("hook_on_screen_text", "")
    hook_words = hook.split()
    if len(hook) > 25:
        warnings.append(f"Hook too long ({len(hook)} chars, max 25): '{hook}'")
    if not (3 <= len(hook_words) <= 5):
        warnings.append(f"Hook word count {len(hook_words)} (need 3–5): '{hook}'")
    thumb = data.get("thumbnail_graphic_text", "")
    if not (2 <= len(thumb.split()) <= 3):
        warnings.append(f"Thumbnail word count {len(thumb.split())} (need 2–3): '{thumb}'")
    script = data.get("script", "")
    if re.search(r"<[^>]+>", script):
        warnings.append("Script contains SSML/HTML tags")
    if "```" in script:
        warnings.append("Script contains markdown code fences")
    if re.search(r"\[.*?\]", script):
        warnings.append("Script may contain stage directions in [ ]")
    return warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate YouTube horoscope video assets via Claude API"
    )
    parser.add_argument(
        "topic",
        nargs="*",
        help="Zodiac sign and date: 'Scorpio June 2026' or two args: Scorpio 'June 2026'",
    )
    parser.add_argument(
        "--all",
        metavar="DATE",
        help="Generate assets for all 12 signs. Provide the date, e.g. 'June 2026'",
    )
    parser.add_argument(
        "--format",
        choices=["short", "long"],
        default="short",
        help="Video format: short (130w) or long (700w). Default: short",
    )
    return parser.parse_args()


def build_topic(parts: list) -> tuple:
    """Return (combined_topic, zodiac_sign_slug) from 1-2 arg parts."""
    combined = " ".join(parts)
    zodiac_slug = parts[0].lower().replace(" ", "_")
    return combined, zodiac_slug


def call_claude(topic: str, video_format: str, fmt_key: str,
                content_angle: str, max_retries: int = 2) -> dict:
    """Call claude-sonnet-4-6 with retry on invalid JSON or missing keys."""
    client = anthropic.Anthropic()
    user_message = (
        f"Zodiac Sign + Date: {topic}\n"
        f"Content Angle: {content_angle}\n"
        f"Video Format: {video_format}"
    )
    max_tokens = MAX_TOKENS_MAP[fmt_key]

    for attempt in range(max_retries + 1):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
        except anthropic.APIError as e:
            print(f"[ERROR] Anthropic API error: {e}", file=sys.stderr)
            sys.exit(1)

        raw_text = response.content[0].text.strip()
        raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
        raw_text = re.sub(r"\n?```$", "", raw_text)

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as e:
            if attempt < max_retries:
                print(f"[WARN] JSON parse failed (attempt {attempt+1}), retrying... {e}")
                time.sleep(2)
                continue
            print(f"[ERROR] JSON parse failed after {max_retries+1} attempts: {e}", file=sys.stderr)
            print(f"[DEBUG] Raw response:\n{raw_text}", file=sys.stderr)
            sys.exit(1)

        missing = REQUIRED_KEYS - set(data.keys())
        if missing:
            if attempt < max_retries:
                print(f"[WARN] Missing keys {sorted(missing)} (attempt {attempt+1}), retrying...")
                time.sleep(2)
                continue
            print(f"[WARN] Missing keys after retries: {sorted(missing)} — continuing anyway")

        issues = validate_content(data)
        if issues:
            for issue in issues:
                print(f"[WARN] Content QC: {issue}")

        return data

    return {}  # unreachable but satisfies linter


def write_output(data: dict, zodiac_slug: str, fmt_key: str) -> str:
    """Write JSON to file and return the file path."""
    today = date.today().strftime("%Y%m%d")
    filename = f"{zodiac_slug}_{fmt_key}_{today}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return filename


def print_results(data: dict, out_path: str) -> None:
    sep = "=" * 60
    print(f"\n{sep}")
    print(f"ANGLE:            {data.get('content_angle', 'N/A')}")
    print(f"HOOK TEXT:        {data.get('hook_on_screen_text', 'N/A')}")
    print(f"THUMBNAIL TEXT:   {data.get('thumbnail_graphic_text', 'N/A')}")
    print(f"TITLE:            {data.get('title', 'N/A')}")
    print(f"\nSCRIPT ({len(data.get('script','').split())} words):")
    print(f"  {data.get('script','')}")
    print(f"\nPINNED COMMENT:   {data.get('pinned_comment', 'N/A')}")
    print(f"{sep}")
    print(f"[INFO] Output written to: {out_path}")


def run_single(sign: str, date_str: str, fmt_key: str,
               content_angle: str = "") -> str:
    """Generate assets for one sign. Returns output file path."""
    topic       = f"{sign} {date_str}"
    zodiac_slug = sign.lower()
    video_format = FORMAT_MAP[fmt_key]
    angle = content_angle or SIGN_ANGLES.get(zodiac_slug, ALL_ANGLES[0])

    print(f"\n[INFO] {sign} | angle: {angle}")
    print(f"[INFO] Format: {video_format} | Model: claude-sonnet-4-6 | max_tokens={MAX_TOKENS_MAP[fmt_key]}")

    data = call_claude(topic, video_format, fmt_key, angle)
    out_path = write_output(data, zodiac_slug, fmt_key)
    print_results(data, out_path)
    return out_path


def main() -> None:
    args = parse_args()
    fmt_key = args.format

    if args.all:
        date_str = args.all
        print(f"[INFO] Generating {fmt_key} assets for ALL 12 signs — {date_str}")
        for i, sign in enumerate(ALL_SIGNS):
            run_single(sign, date_str, fmt_key)
            if i < len(ALL_SIGNS) - 1:
                time.sleep(1)  # brief pause between API calls
        print(f"\n[DONE] Generated assets for all 12 signs.")
    else:
        if not args.topic:
            print("[ERROR] Provide a zodiac sign and date, or use --all DATE", file=sys.stderr)
            sys.exit(1)
        topic, zodiac_slug = build_topic(args.topic)
        video_format = FORMAT_MAP[fmt_key]
        print(f"[INFO] Generating {fmt_key} assets for: {topic}")
        print(f"[INFO] Format: {video_format} | Model: claude-sonnet-4-6 | max_tokens={MAX_TOKENS_MAP[fmt_key]}")
        data = call_claude(topic, video_format, fmt_key)
        out_path = write_output(data, zodiac_slug, fmt_key)
        print_results(data, out_path)


if __name__ == "__main__":
    main()
