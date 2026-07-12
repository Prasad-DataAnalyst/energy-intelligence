#!/usr/bin/env python3
"""
build_calendar.py — generate a TREND-OPTIMIZED 365-day astrology topic plan.

Astrology audiences reliably watch/engage most with (highest → lower):
  1. Compatibility  — "are X and Y compatible", soulmates, best/worst matches
  2. Sign deep-dives — personality, dark side, secrets, in love
  3. Love & relationships — how each sign loves, exes, jealousy, ghosting
  4. Fun rankings / engagement — most powerful/attractive/loyal/dangerous ...
  5. Lunar & timely — full/new moons, eclipses, retrogrades (search spikes)
  6. Planets, 7. Money & career, 8. Core concepts, 9. Deep/advanced, 10. Wellness

We weight the year toward those, generate each bucket with Claude, de-duplicate,
then INTERLEAVE so consecutive days never share a category (variety = retention).
Writes content_calendar_365.json (the daily topic pipeline prefers it).

Usage:
  python3 build_calendar.py            # full 365 (several Claude calls, ~2-4 min)
  python3 build_calendar.py --preview  # small sample to inspect quality
"""
import json
import re
import sys
from pathlib import Path

import anthropic
from dotenv import load_dotenv

load_dotenv()
HERE = Path(__file__).parent
OUT  = HERE / "content_calendar_365.json"

# (category, target_count, guidance) — counts sum to 365, weighted by demand.
BUCKETS = [
    ("compatibility", 37, "Sign-pair compatibility and matches: 'Are Aries and Leo compatible?', best/worst matches for each sign, soulmate pairs, twin flames, karmic matches, element compatibility. Vary the pairs widely."),
    ("signs",         33, "Single-sign deep dives — personality, hidden traits, dark side, strengths, 'the truth about X', 'X in love', secrets of each sign. Cover all 12 signs across multiple angles."),
    ("love",          29, "Love & relationships by sign: how each sign loves, texts, breaks up, handles jealousy, exes, ghosting, commitment, red flags, ideal date. Emotional and relatable."),
    ("engagement",    24, "Fun, shareable RANKINGS and lists: most powerful, most attractive, most loyal, most dangerous, smartest, most creative, hardest to read, best leaders, luckiest, most misunderstood signs — each with astrological reasoning."),
    ("lunar",         25, "Lunar and timely events: full moon and new moon meaning & rituals, lunar/solar eclipses, blood/blue/super moons, moon in each sign, void-of-course, and how to work with each."),
    ("planets",       28, "The planets: meaning of Venus, Mars, Mercury, Jupiter, Saturn, Uranus, Neptune, Pluto; retrogrades (esp. Mercury); your Venus/Mars/moon sign; planet-in-sign highlights."),
    ("money",         20, "Money, wealth and career by sign, for a US audience: richest signs, best careers/jobs, money luck, side hustles, spending styles, 2nd/8th house wealth, business strengths."),
    ("stockmarket",   17, "Astrology applied to the US STOCK MARKET (Wall Street, the S&P 500, the Dow), as an EDUCATIONAL/HISTORICAL lens: Mercury retrograde and market volatility, Jupiter transits and expansion cycles, Saturn transits and corrections, moon phases and trader psychology, the astrology behind famous US crashes and rallies (1929, dot-com, 2008), eclipse-season market patterns. Frame every topic around GENERAL astrological patterns and historical case studies. NEVER a specific real-time stock pick, ticker, or piece of financial advice — every angle must read as entertainment/education, not guidance to trade on."),
    ("crypto",        14, "Astrology applied to cryptocurrency, as an EDUCATIONAL/HISTORICAL lens: Uranus (disruption/innovation) and crypto's rise, Bitcoin's January 2009 'genesis' chart, Rahu/Ketu and speculative-asset mania, Mercury retrograde and crypto volatility, Neptune and bubble/hype cycles, the astrology behind historical crypto booms and crashes. Frame every topic as general astrological patterns and historical analysis. NEVER a specific real-time coin pick or financial advice — entertainment/education only."),
    ("political",     15, "Mundane (world-events) astrology applied to POLITICAL HISTORY, U.S.-focused: Saturn returns of a nation, Pluto transits and historical power shifts, the astrology of the USA's founding chart (July 4, 1776), eclipse seasons around historical upheaval, past presidencies/inaugurations as historical case studies. Keep every topic EVERGREEN, HISTORICAL, and NON-PARTISAN: NEVER predict outcomes for a current or upcoming election, and NEVER make claims about specific living politicians — frame everything as historical mundane-astrology analysis and general cyclical patterns, entertainment/education only."),
    ("celebrity",     20, "Celebrity astrology using ONLY PUBLICLY KNOWN birth dates, biased toward US-household names: which sign famous actors/musicians/athletes share, zodiac patterns among award winners or chart-topping artists, celebrity couple sun-sign compatibility (public birth dates only), 'which sign is the most [trait]' celebrity-list content. NEVER speculate about a real person's private life, relationships, health, death, or future — light entertainment sign-personality commentary only, never real news or predictions about a specific living individual."),
    ("numerology",    20, "Numerology and ANGEL NUMBERS (huge US search volume): what 1111, 222, 333, 444, 555 and other repeating numbers mean, life path numbers explained and calculated, expression/destiny numbers, personal year numbers, how numerology pairs with each zodiac sign, birthday number meanings. Searchable, specific titles ('444 Meaning: ...')."),
    ("tarot",         16, "Tarot-meets-astrology (tarot trends bigger than astrology on US social platforms): the tarot card that rules each zodiac sign, major arcana explained through their zodiac/planet correspondences, moon-phase tarot spreads, 'the card each sign needs to hear right now' style readings framed as general guidance for each sign — always entertainment, never fortune-telling certainty."),
    ("manifestation", 16, "Manifestation and spiritual practice (huge US niche): moon-phase manifestation timing, scripting and visualization by zodiac element, each sign's manifestation superpower and block, gratitude and abundance practices tied to astrology, Venus/Jupiter transits as 'manifestation windows'. Practical, uplifting, never guarantees of outcomes."),
    ("concepts",      19, "Foundational concepts: rising/ascendant, sun-moon-rising 'big three', the four elements, the three modalities, the 12 houses, aspects (trine/square/opposition), synastry basics."),
    ("deep",          15, "Advanced/mystical: lunar nodes & destiny, Chiron the wounded healer, Black Moon Lilith, Saturn return, the 12th house, retrograde planets in birth chart, part of fortune, asteroids."),
    ("wellness",      17, "Wellness, self-care and misc: each sign's health & stress style, self-care rituals, spirituality, crystals by sign, zodiac myths & history."),
]

SYSTEM = """You are an astrology content strategist for a daily YouTube channel
whose audience is in the UNITED STATES — favor topics, framing, and examples
that resonate with and are searched by Americans (tropical zodiac).
You know what astrology audiences actually search for and click. You output ONLY
a valid raw JSON array — no markdown, no commentary."""


def _ask(client, category, guidance, n, avoid):
    avoid_txt = ("\n\nDo NOT repeat or closely duplicate these existing titles:\n- "
                 + "\n- ".join(avoid[-150:])) if avoid else ""
    msg = f"""Generate {n} DISTINCT, highly clickable astrology video topics in the "{category}" theme.

Theme guidance: {guidance}

Every title must be searchable and compelling (the kind people actually click).
Return a JSON array of EXACTLY {n} objects:
[{{"title": "video title", "angle": "one sentence on what it covers", "category": "{category}"}}]{avoid_txt}"""
    resp = client.messages.create(model="claude-sonnet-4-6", max_tokens=8000,
                                  system=SYSTEM, messages=[{"role": "user", "content": msg}])
    raw = resp.content[0].text.strip()
    raw = re.sub(r"^```[a-z]*\n?", "", raw); raw = re.sub(r"\n?```$", "", raw)
    return json.loads(raw)


def _interleave(by_cat: dict) -> list:
    """Round-robin across categories so consecutive days differ. Longest
    buckets get spread evenly through the year."""
    queues = {c: list(v) for c, v in by_cat.items() if v}
    order = sorted(queues, key=lambda c: -len(queues[c]))
    out = []
    while any(queues.values()):
        for c in order:
            if queues[c]:
                out.append(queues[c].pop(0))
    return out


def main():
    preview = "--preview" in sys.argv
    client = anthropic.Anthropic(timeout=180)
    buckets = [(c, 6, g) for c, _, g in BUCKETS] if preview else \
              [(c, n, g) for c, n, g in BUCKETS]

    by_cat, seen = {}, set()
    for category, target, guidance in buckets:
        got = []
        while len(got) < target:
            need = min(30, target - len(got))
            print(f"[INFO] {category}: {len(got)}/{target} ...")
            try:
                batch = _ask(client, category, guidance, need,
                             [t["title"] for t in sum(by_cat.values(), []) + got])
            except Exception as e:
                print(f"[WARN] {category} batch failed ({e}); retrying once", file=sys.stderr)
                try:
                    batch = _ask(client, category, guidance, need, [t["title"] for t in got])
                except Exception:
                    break
            for t in batch:
                key = str(t.get("title", "")).strip().lower()
                if key and key not in seen and t.get("angle"):
                    seen.add(key)
                    got.append({"category": category, "title": t["title"].strip(),
                                "angle": t["angle"].strip()})
                if len(got) >= target:
                    break
        by_cat[category] = got

    topics = _interleave(by_cat)
    for i, t in enumerate(topics, 1):
        t["day"] = i
    data = {"_comment": f"{len(topics)} trend-weighted astrology topics, indexed by "
                        f"day-of-year, interleaved so consecutive days differ. "
                        f"The daily topic pipeline prefers this file.",
            "topics": topics}
    OUT.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    dist = {c: len(v) for c, v in by_cat.items()}
    print(f"[OK] Wrote {len(topics)} topics → {OUT.name}\n     distribution: {dist}")


if __name__ == "__main__":
    main()
