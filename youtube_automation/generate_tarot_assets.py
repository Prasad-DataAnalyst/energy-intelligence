#!/usr/bin/env python3
"""
generate_tarot_assets.py
Weekly LONG-FORM tarot-reading video script: one Major Arcana card per
zodiac sign (deterministic weekly draw from tarot_deck.py — the card names
and meanings are GROUND TRUTH handed to Claude, never invented by it), a
~28-word spoken reading per sign (whole video <=3 min), plus
title/description/tags.

US audience. Safe framing baked in: readings are guidance/reflection
("this week favors...", "watch for..."), never certainty, never medical/
legal/financial advice. A disclaimer is appended in code.

Usage:
  python3 generate_tarot_assets.py 20260713
"""
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import tarot_deck

load_dotenv()

HERE = Path(__file__).parent

DISCLAIMER = ("Tarot readings are for entertainment and reflection only — "
              "not professional, medical, legal, or financial advice.")

SYSTEM_PROMPT = """You are a warm, insightful tarot reader hosting a weekly YouTube
tarot segment for a UNITED STATES audience. For each zodiac sign you are
given the card that was drawn and its upright meaning keywords — these are
GROUND TRUTH: interpret exactly that card, never substitute another.

Style: spoken, warm, encouraging, specific — like a gifted reader talking to
one person. Short sentences (the words become on-screen captions). Each
reading ties the card's meaning to the week ahead for that sign: one theme,
one practical nudge, one gentle watch-out. Even "hard" cards (Death, The
Tower, The Devil) are framed constructively — transformation, breakthrough,
reclaiming power — never doom, never fear.

Rules:
- 24-32 words per sign, flowing spoken prose. No lists. The WHOLE video
  must run under 3 minutes, so every reading is tight and punchy.
- Guidance language only: "this week favors", "you may find", "watch for".
  NEVER certainty, NEVER predictions of specific events, NEVER medical,
  legal, or financial advice.
- Vary the sentence rhythm between signs so the video doesn't feel templated.
Return ONLY valid raw JSON. No markdown, no code fences, no commentary."""


def _week_label(date_tag: str) -> str:
    d = datetime.strptime(date_tag, "%Y%m%d")
    end = d + timedelta(days=6)
    if d.month == end.month:
        return f"{d.strftime('%B %d')}–{end.strftime('%d, %Y')}"
    return f"{d.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"


def _validate(data: dict, spread: dict) -> None:
    for k in ("title", "description", "tags", "intro", "outro", "readings"):
        if not data.get(k):
            raise ValueError(f"missing '{k}'")
    readings = data["readings"]
    for sign in tarot_deck.SIGNS:
        r = readings.get(sign)
        if not r:
            raise ValueError(f"missing reading for {sign}")
        words = len(str(r).split())
        if words < 18:
            raise ValueError(f"{sign} reading too short ({words} words)")
        if words > 45:
            raise ValueError(f"{sign} reading too long ({words} words — the "
                             f"video must stay under 3 minutes)")
        # The reading must actually be about the drawn card.
        card_name = spread[sign]["name"]
        if card_name.lower().replace("the ", "") not in str(r).lower():
            raise ValueError(f"{sign} reading never mentions its card '{card_name}'")


def generate(date_tag: str = None) -> str:
    if not date_tag:
        date_tag = date.today().strftime("%Y%m%d")
    when = _week_label(date_tag)
    spread = tarot_deck.draw_weekly(date_tag)

    spread_block = "\n".join(
        f"- {sign.title()}: {c['name']} ({c['numeral']}) — upright keywords: {c['keywords']}"
        for sign, c in spread.items()
    )
    user_msg = f"""THIS WEEK'S DRAW ({when}) — one Major Arcana card per sign:
{spread_block}

Write the weekly tarot reading video script. Return this EXACT JSON shape:
{{
  "title": "SEO YouTube title for a weekly all-signs tarot reading, <=95 chars, include the week",
  "description": "2-3 sentence YouTube description",
  "tags": ["10-15 lowercase search tags"],
  "intro": "15-25 spoken words welcoming viewers and teasing this week's cards",
  "readings": {{
    "aries": "24-32 word spoken reading interpreting Aries' drawn card for the week",
    "...": "one entry for EVERY sign, keys exactly: {', '.join(tarot_deck.SIGNS)}"
  }},
  "outro": "15-25 spoken words: ask to subscribe + tease next week's draw"
}}"""

    client = anthropic.Anthropic(timeout=180)
    print(f"[INFO] Weekly tarot ({when}) → generating readings via Claude...")
    data, last_err = None, None
    for attempt in range(1, 4):
        try:
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
            _validate(candidate, spread)
            data = candidate
            break
        except Exception as e:
            last_err = e
            print(f"[WARN] Attempt {attempt}/3 failed: {e}", file=sys.stderr)
    if data is None:
        raise RuntimeError(f"Tarot generation failed after 3 attempts: {last_err}")

    data["content_type"] = "tarotweekly"
    data["date"] = when
    data["week"] = tarot_deck.week_key(date_tag)
    data["disclaimer"] = DISCLAIMER
    # serializable spread (drop the filename internals; renderer re-draws
    # deterministically anyway, but keeping it makes the JSON self-contained)
    data["spread"] = {s: {"name": c["name"], "numeral": c["numeral"],
                          "keywords": c["keywords"]}
                      for s, c in spread.items()}
    data["outro"] = f"{str(data['outro']).rstrip('. ')}. {DISCLAIMER}"
    data["description"] = f"{data['description']}\n\n{DISCLAIMER}"
    data.setdefault("hashtags", ["#tarot", "#tarotreading", "#astrology"])
    data.setdefault("pinned_comment",
                    "Which card did YOUR sign pull this week? Drop your sign below ⬇️\n"
                    f"New tarot reading every week — Subscribe 🔔\n\n{DISCLAIMER}")

    filename = f"tarotweekly_{date_tag}.json"
    (HERE / filename).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Assets → {filename}  (12 readings, week {data['week']})")
    return filename


def main():
    date_tag = sys.argv[1] if len(sys.argv) > 1 else None
    generate(date_tag)


if __name__ == "__main__":
    main()
