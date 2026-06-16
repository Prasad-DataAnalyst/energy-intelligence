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

SYSTEM_PROMPT = """Act as an elite YouTube Shorts producer and cinematic astrologer who specialises in high-retention, emotionally triggering horoscope content. Your single task is to generate trending video content assets.

You will receive:
- Target Topic: A zodiac sign and date (e.g., "Scorpio June 2026")
- Target Video Format: Either "Vertical Short (9:16, 130 words)" or "Long-form (16:9, 700 words)"

TRENDING STYLE RULES — follow these exactly:
- hook_on_screen_text: 3–5 words in ALL CAPS. Use sudden emotional triggers and mystery. Examples of the correct style: "THEY ARE HIDING THIS", "WATCH BEFORE TOMORROW", "THIS CHANGES EVERYTHING", "THEIR SECRET IS OUT". NEVER use generic phrases like "Scorpio Reading" or "Weekly Update".
- thumbnail_graphic_text: 2–3 words MAX. High-contrast shock keywords. Examples: "THEIR SECRET", "JUNE WARNING", "IT'S TIME", "RUN NOW". Never use complete sentences or date stamps.
- script: Atmospheric, mysterious, deeply cinematic voiceover. Speak directly to the viewer using "You". Build urgency and emotional tension. No speaker tags, section headers, parentheses, or audio-visual cues. Smooth narrative flow for immediate Text-to-Speech. Word count must strictly match the chosen format.

Output ONLY a valid JSON object — no intro, no filler, no markdown, no code fences:

{
  "hook_on_screen_text": "ALL CAPS emotional trigger, 3–5 words",
  "thumbnail_graphic_text": "2–3 shock words MAX",
  "script": "Full atmospheric voiceover script matching word count for chosen format"
}"""

ALL_SIGNS = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]

FORMAT_MAP = {
    "short": "Vertical Short (9:16, 130 words)",
    "long":  "Long-form (16:9, 700 words)",
}

MAX_TOKENS_MAP = {
    "short": 1500,
    "long":  3000,
}

REQUIRED_KEYS = {"hook_on_screen_text", "thumbnail_graphic_text", "script"}


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


def call_claude(topic: str, video_format: str, fmt_key: str) -> dict:
    """Call claude-sonnet-4-6 and return parsed JSON dict."""
    client = anthropic.Anthropic()
    user_message = f"Target Topic: {topic}\nTarget Video Format: {video_format}"
    max_tokens = MAX_TOKENS_MAP[fmt_key]

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

    # Strip markdown code fences if present
    raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
    raw_text = re.sub(r"\n?```$", "", raw_text)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as e:
        print(f"[ERROR] JSON parse failed: {e}", file=sys.stderr)
        print(f"[DEBUG] Raw API response:\n{raw_text}", file=sys.stderr)
        sys.exit(1)

    missing = REQUIRED_KEYS - set(data.keys())
    if missing:
        raise ValueError(f"Missing required JSON keys: {', '.join(sorted(missing))}")

    return data


def write_output(data: dict, zodiac_slug: str, fmt_key: str) -> str:
    """Write JSON to file and return the file path."""
    today = date.today().strftime("%Y%m%d")
    filename = f"{zodiac_slug}_{fmt_key}_{today}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return filename


def print_results(data: dict, out_path: str) -> None:
    print("\n" + "=" * 60)
    print("HOOK ON-SCREEN TEXT:")
    print(f"  {data['hook_on_screen_text']}")
    print("\nTHUMBNAIL GRAPHIC TEXT:")
    print(f"  {data['thumbnail_graphic_text']}")
    print("\nVOICEOVER SCRIPT:")
    print(f"  {data['script']}")
    print("=" * 60)
    word_count = len(data["script"].split())
    print(f"\n[INFO] Script word count: {word_count}")
    print(f"[INFO] Output written to: {out_path}")


def run_single(sign: str, date_str: str, fmt_key: str) -> None:
    topic = f"{sign} {date_str}"
    zodiac_slug = sign.lower()
    video_format = FORMAT_MAP[fmt_key]

    print(f"\n[INFO] Generating {fmt_key} assets for: {topic}")
    print(f"[INFO] Format: {video_format} | Model: claude-sonnet-4-6 | max_tokens={MAX_TOKENS_MAP[fmt_key]}")

    data = call_claude(topic, video_format, fmt_key)
    out_path = write_output(data, zodiac_slug, fmt_key)
    print_results(data, out_path)


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
