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

SYSTEM_PROMPT = """Act as an elite YouTube video producer and cinematic astrologer. Your single task is to generate high-retention video content assets for a horoscope video.

You will receive:
- Target Topic: A zodiac sign and date (e.g., "Scorpio June 2026")
- Target Video Format: Either "Vertical Short (9:16, 130 words)" or "Long-form (16:9, 700 words)"

Output ONLY a valid JSON object. Do not include any introduction, conversational filler, markdown formatting, code fences, or trailing text. The response must be raw JSON that strictly follows this schema:

{
  "hook_on_screen_text": "3-5 punchy words in ALL CAPS to overlay on screen during the first 4 seconds to instantly grab attention.",
  "thumbnail_graphic_text": "2-3 high-impact words designed to be burned onto the thumbnail image.",
  "script": "The full voiceover script. Write it in an atmospheric, mysterious, and deeply engaging tone. Speak directly to the viewer using 'You'. Do not include any speaker tags, section headers, parentheses, or audio-visual bracket cues. Maintain a smooth, continuous narrative flow safe for immediate Text-to-Speech execution. Word count must strictly match the chosen format."
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "topic",
        nargs="*",
        help="Zodiac sign and date: 'Scorpio June 2026' or two args: Scorpio 'June 2026'",
    )
    group.add_argument(
        "--all",
        metavar="DATE",
        help="Generate assets for all 12 signs. Provide the date, e.g. 'June 2026'",
    )
    parser.add_argument(
        "--format",
        choices=["short", "long"],
        default="long",
        help="Video format: short (130w) or long (700w). Default: long",
    )
    return parser.parse_args()


def build_topic(parts: list) -> tuple:
    """Return (combined_topic, zodiac_sign_slug) from 1-2 arg parts."""
    combined = " ".join(parts)
    zodiac_slug = parts[0].lower().replace(" ", "_")
    return combined, zodiac_slug


def call_claude(topic: str, video_format: str, fmt_key: str) -> dict:
    """Call claude-opus-4-6 and return parsed JSON dict."""
    client = anthropic.Anthropic()
    user_message = f"Target Topic: {topic}\nTarget Video Format: {video_format}"
    max_tokens = MAX_TOKENS_MAP[fmt_key]

    try:
        response = client.messages.create(
            model="claude-opus-4-6",
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
    print(f"[INFO] Format: {video_format} | Model: claude-opus-4-6 | max_tokens={MAX_TOKENS_MAP[fmt_key]}")

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
        print(f"[INFO] Format: {video_format} | Model: claude-opus-4-6 | max_tokens={MAX_TOKENS_MAP[fmt_key]}")
        data = call_claude(topic, video_format, fmt_key)
        out_path = write_output(data, zodiac_slug, fmt_key)
        print_results(data, out_path)


if __name__ == "__main__":
    main()
