#!/usr/bin/env python3
"""
generate_prediction_assets.py
Short (~90s) LANDSCAPE "astrology prediction" video script, one per category:
sports / crypto / political / celebrity. Punchy and specific, with a real
computed astrology chart (astro_chart.py) as ground truth, plus per-section
image search keywords for stock photos (stock_images.py) so the video shows
real imagery, not an empty starfield.

SAFETY (this is automated, unattended, public daily content — the framing
is deliberate, not incidental):
  sports    — astrology entertainment pick for a real match. Fine + disclaimer.
  crypto    — astrological MOOD for the crypto market (energetic / cautious /
              volatile). NEVER "buy X" or "price will go up today, act on it".
              Not financial advice.
  political — general NATIONAL MOOD / collective themes by astrology only.
              NEVER a candidate, party, or election-outcome claim.
  celebrity — a zodiac sign's celebrity archetype using PUBLIC birth-sign
              facts. NEVER private life, relationships, health, or predictions
              about a real person.
Disclaimers are appended in CODE (not left to the LLM) so they are always
present regardless of what the script otherwise says.

Usage:
  python3 generate_prediction_assets.py sports   20260710
  python3 generate_prediction_assets.py crypto   20260710
  python3 generate_prediction_assets.py political 20260710
  python3 generate_prediction_assets.py celebrity 20260710
"""
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import astro_chart

load_dotenv()

HERE = Path(__file__).parent
CATEGORIES = ("sports", "crypto", "political", "celebrity")

SIGNS = astro_chart.SIGNS

# A tiny, uncontroversial seed of very widely-known public figures per sign,
# used ONLY to ground the celebrity video in real public birth-sign facts so
# Claude doesn't invent them. Personality/archetype commentary only — never
# private life. Extend freely.
SIGN_CELEBS = {
    "Aries": "Lady Gaga, Robert Downey Jr., Mariah Carey",
    "Taurus": "Adele, Dwayne Johnson, David Beckham",
    "Gemini": "Angelina Jolie, Kanye West, Naomi Campbell",
    "Cancer": "Tom Hanks, Selena Gomez, Lionel Messi",
    "Leo": "Barack Obama, Jennifer Lopez, Chris Hemsworth",
    "Virgo": "Beyonce, Keanu Reeves, Zendaya",
    "Libra": "Kim Kardashian, Will Smith, Serena Williams",
    "Scorpio": "Leonardo DiCaprio, Katy Perry, Drake",
    "Sagittarius": "Taylor Swift, Brad Pitt, Nicki Minaj",
    "Capricorn": "Michelle Obama, Denzel Washington, Zayn Malik",
    "Aquarius": "Cristiano Ronaldo, Oprah Winfrey, Harry Styles",
    "Pisces": "Rihanna, Albert Einstein, Justin Bieber",
}

DISCLAIMERS = {
    "sports":    "For entertainment and astrology fun only — not betting advice.",
    "crypto":    "For entertainment and astrology fun only — not financial or trading advice.",
    "political": "A general astrological mood reading for entertainment only — not a political prediction or endorsement.",
    "celebrity": "Entertainment astrology using publicly known birth signs only.",
}

_COMMON_RULES = """
Write for SPOKEN delivery in a punchy, exciting, confident voice — this
becomes word-synced captions, so use short sentences. Total across hook +
all beats + verdict + outro must be about 170-200 words (the finished video
must stay UNDER 90 seconds). Be specific and crispy, never vague filler.
Return ONLY valid raw JSON — no markdown, no code fences, no commentary."""

SYSTEM_PROMPTS = {
    "sports": (
        "You are the host of a fun sports-astrology YouTube channel. You are "
        "given ONE real match and a REAL computed astrology chart for its "
        "start time. Give an entertaining astrological PICK for the match — "
        "which side the stars slightly favor, and why (moon, ascendant vs 7th "
        "house, day/hora lord). Interpret the GIVEN chart facts; never invent "
        "different ones. Use 'slight edge', 'strong chance', 'close contest', "
        "'astrology favors'. NEVER guarantee a result. NEVER mention or promote "
        "betting/gambling." + _COMMON_RULES
    ),
    "crypto": (
        "You are the host of a fun markets-astrology YouTube channel. Using the "
        "GIVEN real astrology chart for today, describe the astrological MOOD "
        "for the crypto market today — e.g. energetic, cautious, volatile, "
        "expansive — and which planetary influences color it. This is symbolic "
        "entertainment about the day's ENERGY, NOT a trading signal. NEVER tell "
        "viewers to buy or sell, NEVER name a coin to buy, NEVER say a specific "
        "price will go up or down as actionable advice. Use 'astrology hints at', "
        "'the mood leans'. This is NOT financial advice." + _COMMON_RULES
    ),
    "political": (
        "You are the host of a fun mundane-astrology channel. Using the GIVEN "
        "real astrology chart for today, describe the general NATIONAL MOOD and "
        "collective themes the day's sky suggests — communication, tension, "
        "optimism, patience — as light symbolic entertainment. ABSOLUTE RULES: "
        "NEVER name or reference any politician, party, candidate, or election. "
        "NEVER predict any election outcome or vote. NEVER take a political "
        "side. Keep it to general collective 'energy/mood' only, the kind of "
        "thing that could apply to any country on any day." + _COMMON_RULES
    ),
    "celebrity": (
        "You are the host of a fun celebrity-astrology channel. You are given "
        "ONE zodiac sign and a few very famous people who publicly share it. "
        "Describe that sign's 'celebrity archetype' — the shared traits, "
        "on-screen/stage energy, and career strengths astrologers associate "
        "with the sign — referencing the given names as fun examples. ABSOLUTE "
        "RULES: use ONLY public career facts and the birth SIGN. NEVER discuss "
        "or speculate about anyone's private life, relationships, health, "
        "family, or future. NEVER predict anything about a real person. Keep it "
        "celebratory and light." + _COMMON_RULES
    ),
}

_JSON_SHAPE = """Return this EXACT JSON shape:
{
  "title_en": "punchy YouTube title in English, <=80 chars",
  "title_ta": "the same title in Tamil",
  "description": "2-3 sentence YouTube description in English",
  "description_ta": "2 sentence description in Tamil",
  "tags": ["8-12 lowercase english search tags"],
  "hook": "one punchy spoken opening sentence",
  "subject_label": "a short 2-4 word on-screen label for what this is about",
  "beats": [
    {"heading": "2-4 word on-screen heading",
     "narration": "35-50 words spoken, punchy",
     "image_query": "2-4 word stock-photo search phrase for this beat",
     "image_fallback": "1-2 word broader stock-photo search phrase"}
  ],
  "verdict": {
    "headline": "the punchy one-line takeaway/prediction (<=8 words)",
    "confidence_pct": 62,
    "detail": "one short spoken sentence explaining the takeaway"
  },
  "outro": "closing spoken line: recap + ask to subscribe",
  "hashtags": ["#astrology", "#..."]
}
Use EXACTLY 3 beats."""


def _now_utc(date_tag: str) -> datetime:
    """Noon UTC on the given day — a stable, sensible 'today' chart moment for
    crypto/political/celebrity (which aren't tied to a specific event time)."""
    d = datetime.strptime(date_tag, "%Y%m%d")
    return d.replace(hour=12, minute=0, tzinfo=timezone.utc).replace(tzinfo=None)


def _chart_summary(chart: dict) -> str:
    planets = "; ".join(f"{p}: {d['sign']} ({d['dignity']})"
                        for p, d in chart["planets"].items())
    return (f"Ascendant {chart['ascendant_sign']}, 7th house "
            f"{chart['seventh_house_sign']}, Moon {chart['moon_sign']} "
            f"({chart['moon_nakshatra']}, {chart['moon_strength']}), "
            f"day lord {chart['day_lord']}, hora lord {chart['hora_lord']}. "
            f"{planets}. Heuristic lean: {chart['momentum_favors']}.")


def _build_user_msg(category: str, date_tag: str) -> tuple:
    """Return (user_msg, default_image_query) for the category. default image
    query is a safe broad fallback for the intro/verdict cards."""
    when = datetime.strptime(date_tag, "%Y%m%d").strftime("%B %d, %Y")

    if category == "sports":
        import sports_data
        matches = sports_data.fetch_today_matches(date_tag)
        if not matches:
            raise RuntimeError("no matches today")
        m = matches[0]
        dt = datetime.fromisoformat(m["datetime_utc"])
        chart = astro_chart.compute_chart(dt, m.get("venue", ""), m.get("country", ""))
        msg = (f"MATCH ({when}): {m['team_a']} vs {m['team_b']} — {m['sport']}, "
               f"{dt.strftime('%H:%M UTC')} at {m['venue']}, {m['country']}.\n"
               f"REAL START-TIME CHART: {_chart_summary(chart)}\n"
               f"Team A = {m['team_a']} (home/1st house), Team B = {m['team_b']} "
               f"(away/7th house).\n\n{_JSON_SHAPE}")
        return msg, f"{m['sport']} stadium", {"match": m, "chart": chart}

    chart = astro_chart.compute_chart(_now_utc(date_tag))
    if category == "crypto":
        msg = (f"TODAY ({when}) real sky: {_chart_summary(chart)}\n\n"
               f"Give the crypto-market MOOD for today.\n\n{_JSON_SHAPE}")
        return msg, "cryptocurrency bitcoin", {"chart": chart}

    if category == "political":
        msg = (f"TODAY ({when}) real sky: {_chart_summary(chart)}\n\n"
               f"Give the general national MOOD / collective themes for today. "
               f"No politicians, parties, or elections.\n\n{_JSON_SHAPE}")
        return msg, "city skyline crowd", {"chart": chart}

    if category == "celebrity":
        sign = SIGNS[datetime.strptime(date_tag, "%Y%m%d").timetuple().tm_yday % 12]
        celebs = SIGN_CELEBS[sign]
        msg = (f"ZODIAC SIGN: {sign}. Famous people who publicly share this "
               f"sign: {celebs}.\n\nDescribe {sign}'s celebrity archetype using "
               f"these public examples.\n\n{_JSON_SHAPE}")
        return msg, "red carpet celebrity", {"sign": sign, "celebs": celebs}

    raise ValueError(f"unknown category: {category}")


def _validate(data: dict) -> None:
    for k in ("title_en", "hook", "beats", "verdict", "outro", "subject_label"):
        if not data.get(k):
            raise ValueError(f"missing '{k}'")
    beats = data["beats"]
    if not isinstance(beats, list) or len(beats) < 3:
        raise ValueError(f"need 3 beats, got {len(beats) if isinstance(beats, list) else 'none'}")
    for i, b in enumerate(beats[:3]):
        if not b.get("heading") or not b.get("narration"):
            raise ValueError(f"beat {i}: missing heading/narration")
        if not b.get("image_query"):
            raise ValueError(f"beat {i}: missing image_query")
    v = data["verdict"]
    if not v.get("headline") or "confidence_pct" not in v:
        raise ValueError("verdict missing headline/confidence_pct")
    c = v["confidence_pct"]
    if not isinstance(c, (int, float)) or not (50 <= c <= 80):
        raise ValueError(f"confidence_pct out of sane range: {c}")
    # total word budget guard — must stay short enough for <90s
    words = len(str(data["hook"]).split()) + len(str(data["outro"]).split())
    words += sum(len(str(b["narration"]).split()) for b in beats[:3])
    if words > 240:
        raise ValueError(f"script too long ({words} words, must be <=240 for <90s)")


def generate(category: str, date_tag: str) -> str:
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of {CATEGORIES}, got '{category}'")
    when = datetime.strptime(date_tag, "%Y%m%d").strftime("%B %d, %Y")
    user_msg, default_img, context = _build_user_msg(category, date_tag)

    client = anthropic.Anthropic(timeout=120)
    print(f"[INFO] {category} prediction ({when}) → generating 90s script via Claude...")
    data, last_err = None, None
    for attempt in range(1, 4):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                system=SYSTEM_PROMPTS[category],
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
        raise RuntimeError(f"{category} prediction generation failed after 3 attempts: {last_err}")

    # Assemble final asset with hardcoded disclaimer (never left to the LLM).
    disclaimer = DISCLAIMERS[category]
    data["content_type"] = "prediction"
    data["category"]     = category
    data["date"]         = when
    data["disclaimer"]   = disclaimer
    data["default_image"] = default_img
    data.setdefault("title_ta", data["title_en"])
    data.setdefault("description", data.get("title_en", ""))
    data.setdefault("tags", ["astrology", category, "prediction"])
    data.setdefault("hashtags", ["#astrology", f"#{category}", "#prediction"])
    data["title"] = f"{data['title_en']} | {when}"[:100]
    data["outro"] = f"{str(data['outro']).rstrip('. ')}. {disclaimer}"
    data["description"] = f"{data['description']}\n\n{disclaimer}"
    data["pinned_comment"] = (
        f"What do the stars say for you today? Comment below ⬇️\n"
        f"New astrology prediction every day — Subscribe 🔔\n\n{disclaimer}"
    )
    # carry any computed context (chart/match) for reference/debugging
    data["_context"] = {k: v for k, v in context.items() if k != "match"}
    if "match" in context:
        data["_match"] = context["match"]

    filename = f"prediction_{category}_{date_tag}.json"
    (HERE / filename).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] Assets → {filename}  ({len(data['beats'])} beats, "
          f"verdict '{data['verdict']['headline']}')")
    return filename


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in CATEGORIES:
        print(f"Usage: python3 generate_prediction_assets.py "
              f"{{{'|'.join(CATEGORIES)}}} [YYYYMMDD]")
        sys.exit(1)
    category = sys.argv[1]
    date_tag = sys.argv[2] if len(sys.argv) > 2 else datetime.now().strftime("%Y%m%d")
    generate(category, date_tag)


if __name__ == "__main__":
    main()
