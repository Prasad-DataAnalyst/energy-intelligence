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
CATEGORIES = ("sports", "crypto", "political", "celebrity", "love")

# ── Love compatibility (synastry by sign) ─────────────────────────────────────
# Real, classical astrology — NOT invented. The aspect between two signs is a
# function of how many signs apart they are, and element/modality are fixed
# traditional attributions. Computing these here (rather than letting Claude
# make them up) is the same principle as feeding the real chart to the other
# categories: the model INTERPRETS given facts, it never invents them.
_ELEMENT = {
    "Aries": "Fire", "Leo": "Fire", "Sagittarius": "Fire",
    "Taurus": "Earth", "Virgo": "Earth", "Capricorn": "Earth",
    "Gemini": "Air", "Libra": "Air", "Aquarius": "Air",
    "Cancer": "Water", "Scorpio": "Water", "Pisces": "Water",
}
_MODALITY = {
    "Aries": "Cardinal", "Cancer": "Cardinal", "Libra": "Cardinal", "Capricorn": "Cardinal",
    "Taurus": "Fixed", "Leo": "Fixed", "Scorpio": "Fixed", "Aquarius": "Fixed",
    "Gemini": "Mutable", "Virgo": "Mutable", "Sagittarius": "Mutable", "Pisces": "Mutable",
}
# distance in signs -> (aspect name, flavour, score). The score is COMPUTED,
# never model-chosen, so the same pairing always reports the same number — a
# viewer who checks twice (or compares with a friend) sees consistency, which
# is what makes the format feel credible rather than random.
# Every score sits at 60+ by design: classical astrology treats every pairing
# as workable, and a "your relationship is doomed, 14%" verdict about a real
# couple watching would be both bad astrology and genuinely unkind.
_ASPECT = {
    0: ("Conjunction", "same-sign mirror — instant recognition, shared blind spots", 78),
    1: ("Semi-sextile", "neighbouring signs — different tempos, learned patience", 66),
    2: ("Sextile", "easy friendship energy — light, encouraging, low-friction", 82),
    3: ("Square", "creative friction — real heat, real growth, needs work", 70),
    4: ("Trine", "same element — natural flow and deep mutual understanding", 88),
    5: ("Quincunx", "unlike each other — fascination that needs conscious adjustment", 64),
    6: ("Opposition", "zodiac opposites — magnetic pull, complementary halves", 74),
}


def _pair_facts(a: str, b: str) -> dict:
    """Classical synastry facts for a sign pair. Deterministic."""
    ia, ib = SIGNS.index(a), SIGNS.index(b)
    dist = abs(ia - ib)
    dist = min(dist, 12 - dist)          # zodiac is circular: 12 signs apart == 0
    name, flavour, score = _ASPECT[dist]
    return {
        "sign_a": a, "sign_b": b,
        "element_a": _ELEMENT[a], "element_b": _ELEMENT[b],
        "modality_a": _MODALITY[a], "modality_b": _MODALITY[b],
        "signs_apart": dist, "aspect": name, "aspect_flavour": flavour,
        "score": score,
        "same_element": _ELEMENT[a] == _ELEMENT[b],
        "same_modality": _MODALITY[a] == _MODALITY[b],
    }


def _all_pairs():
    """All 78 unordered pairings (66 mixed + 12 same-sign). Same-sign pairs are
    included because "Leo and Leo compatibility" is itself a searched query.
    One per day cycles the full set every 78 days without repeats.

    Computed lazily rather than at module scope: SIGNS is assigned further
    down this file (from astro_chart), so a module-level comprehension here
    raised NameError on import — which would have broken EVERY prediction
    category, not just love."""
    return [(SIGNS[i], SIGNS[j])
            for i in range(len(SIGNS)) for j in range(i, len(SIGNS))]


def _pair_of_the_day(date_tag: str):
    """Deterministic pairing for a date. toordinal() (not day-of-year) so the
    cycle runs continuously across a year boundary instead of jumping."""
    pairs = _all_pairs()
    return pairs[datetime.strptime(date_tag, "%Y%m%d").toordinal() % len(pairs)]

SIGNS = astro_chart.SIGNS

# A tiny, uncontroversial seed of public figures per sign — US-household
# names (the channel is US-only), used ONLY to ground the celebrity video in
# real public birth-sign facts so Claude doesn't invent them. Personality/
# archetype commentary only — never private life. Deliberately NO politicians
# or political figures (keeps the celebrity category cleanly separated from
# the non-partisan rule on the political one). Extend freely.
SIGN_CELEBS = {
    "Aries": "Lady Gaga, Robert Downey Jr., Mariah Carey",
    "Taurus": "Adele, Dwayne Johnson, George Clooney",
    "Gemini": "Angelina Jolie, Kanye West, Kendrick Lamar",
    "Cancer": "Tom Hanks, Selena Gomez, Ariana Grande",
    "Leo": "Jennifer Lopez, Jennifer Lawrence, Chris Hemsworth",
    "Virgo": "Beyonce, Keanu Reeves, Zendaya",
    "Libra": "Kim Kardashian, Will Smith, Serena Williams",
    "Scorpio": "Leonardo DiCaprio, Katy Perry, Drake",
    "Sagittarius": "Taylor Swift, Brad Pitt, Nicki Minaj",
    "Capricorn": "Denzel Washington, LeBron James, Dolly Parton",
    "Aquarius": "Michael Jordan, Oprah Winfrey, Harry Styles",
    "Pisces": "Rihanna, Albert Einstein, Justin Bieber",
}

DISCLAIMERS = {
    "sports":    "For entertainment and astrology fun only — not betting advice.",
    "crypto":    "For entertainment and astrology fun only — not financial or trading advice.",
    "political": "A general astrological mood reading for entertainment only — not a political prediction or endorsement.",
    "celebrity": "Entertainment astrology using publicly known birth signs only.",
    "love": ("For entertainment and astrology fun only — sun-sign compatibility "
             "is not relationship advice, and no chart can define a real "
             "relationship."),
}

_COMMON_RULES = """
Your audience is in the UNITED STATES — use American framing, references,
and phrasing throughout.
The "hook" must open a curiosity gap — tease the verdict without revealing
it ("the chart picked a side tonight — and it's not who you think"), never
a generic welcome. Speak directly to the viewer in second person.
Write for SPOKEN delivery in a punchy, exciting, confident voice — this
becomes word-synced captions, so use short sentences. STRICT LENGTH BUDGET
(the finished video must stay under 90 seconds of speech): hook <= 15 words,
each beat narration 30-40 words, verdict detail <= 15 words, outro <= 20
words — total across hook + all three beats + verdict + outro must be
130-160 words. Be specific and crispy, never vague filler.
Return ONLY valid raw JSON — no markdown, no code fences, no commentary."""

SYSTEM_PROMPTS = {
    "sports": (
        "You are the host of WORLD SPORTS ASTROLOGY — a fun global "
        "sports-astrology YouTube channel covering the biggest fixture on "
        "earth each day: Premier League and Champions League football, IPL "
        "and international cricket, the NBA, Formula 1, Grand Slam tennis, "
        "rugby and the NFL. You are given ONE real match and a REAL computed "
        "astrology chart for its start time. Give an entertaining "
        "astrological PICK for the match — which side the stars slightly "
        "favor, and why (moon, ascendant vs 7th house, day/hora lord). "
        "Interpret the GIVEN chart facts; never invent different ones. Use "
        "'slight edge', 'strong chance', 'close contest', 'astrology favors'. "
        "NEVER guarantee a result. NEVER mention or promote betting/gambling. "
        "AUDIENCE: worldwide — this OVERRIDES the US framing below. Write for "
        "a global fan (say 'football' not 'soccer' for the world game, keep "
        "references international, never assume the viewer is American). Name "
        "the two teams and the competition early so fans searching for this "
        "fixture recognise it instantly." + _COMMON_RULES
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
    "love": (
        "You are the host of WORLD LOVE ASTROLOGY — a warm, fun zodiac "
        "compatibility channel. You are given TWO zodiac signs and their REAL "
        "classical synastry facts: the aspect between them (from how many "
        "signs apart they sit), their elements, and their modalities. "
        "Interpret ONLY those given facts — never invent a different aspect, "
        "element or modality. Structure: beat 1 = what naturally CONNECTS "
        "them, beat 2 = where they CLASH or need work, beat 3 = the LONG "
        "GAME (what makes it last). "
        "ABSOLUTE RULES: every pairing must get a fair, balanced reading — "
        "name real strengths AND real growth areas for all of them. NEVER "
        "tell viewers to start, leave, avoid or fix a relationship. NEVER "
        "call any pairing doomed, toxic, hopeless or a mistake, and never "
        "imply someone should break up. This is playful entertainment about "
        "two SIGNS, never advice about a real person's relationship. "
        "AUDIENCE: worldwide — this OVERRIDES the US framing below. Keep "
        "references international; never assume the viewer is American. Name "
        "BOTH signs in the first sentence and in the title, because viewers "
        "search for their exact pairing." + _COMMON_RULES
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
     "narration": "30-40 words spoken, punchy",
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
        # Team names carry the US city ("Los Angeles Rams") — pass them into
        # the venue lookup so the chart uses the right coast's local sky.
        venue_hint = f"{m['team_a']} {m['team_b']} {m.get('venue', '')}"
        chart = astro_chart.compute_chart(dt, venue_hint, m.get("country", ""))
        msg = (f"MATCH ({when}): {m['team_a']} vs {m['team_b']} — {m['sport']}, "
               f"{dt.strftime('%H:%M UTC')} at {m['venue']}, {m['country']}.\n"
               f"REAL START-TIME CHART: {_chart_summary(chart)}\n"
               f"Team A = {m['team_a']} (home/1st house), Team B = {m['team_b']} "
               f"(away/7th house).\n\n{_JSON_SHAPE}")
        return msg, f"{m['sport']} stadium", {"match": m, "chart": chart}

    if category == "love":
        # BEFORE the transit-chart computation on purpose: sun-sign synastry is
        # sign-to-sign, so love needs no ephemeris call at all. Sitting below it
        # meant paying for a full chart it discards — and crashing with it if
        # swisseph ever failed, for data it never used.
        a, b = _pair_of_the_day(date_tag)
        f = _pair_facts(a, b)
        msg = (
            f"PAIRING: {a} + {b}\n"
            f"REAL SYNASTRY FACTS (interpret these, do not invent others):\n"
            f"- They sit {f['signs_apart']} sign(s) apart -> classical aspect: "
            f"{f['aspect']} ({f['aspect_flavour']}).\n"
            f"- {a}: {f['element_a']} element, {f['modality_a']} modality.\n"
            f"- {b}: {f['element_b']} element, {f['modality_b']} modality.\n"
            f"- Same element: {f['same_element']}. Same modality: {f['same_modality']}.\n"
            f"- Compatibility score to present: {f['score']}%.\n\n"
            f"Give the {a} + {b} love-compatibility reading. Use the score "
            f"{f['score']} as verdict.confidence_pct EXACTLY — do not choose a "
            f"different number.\n\n{_JSON_SHAPE}")
        return msg, "romantic couple sunset", {"pair": f}

    chart = astro_chart.compute_chart(_now_utc(date_tag))
    if category == "crypto":
        msg = (f"TODAY ({when}) real sky: {_chart_summary(chart)}\n\n"
               f"Give the crypto-market MOOD for today.\n\n{_JSON_SHAPE}")
        return msg, "cryptocurrency bitcoin", {"chart": chart}

    if category == "political":
        msg = (f"TODAY ({when}) real sky: {_chart_summary(chart)}\n\n"
               f"Give the general AMERICAN national MOOD / collective themes "
               f"for today across the United States. "
               f"No politicians, parties, or elections.\n\n{_JSON_SHAPE}")
        return msg, "american flag city", {"chart": chart}

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
    # Total SPOKEN word budget — must stay short enough for <90s of speech.
    # Everything narrated counts: hook, 3 beats, verdict headline+detail
    # (spoken on the verdict card as "headline. detail"), and the outro.
    # Budget math (validated against a real overlong production run that hit
    # 103s): TTS ≈ 2.4 words/sec, plus ~5s of per-card padding across the 6
    # cards, plus the ~12-word disclaimer appended to the outro AFTER this
    # validation. 175 words + disclaimer ≈ 187 spoken ≈ 78s + 5s padding ≈
    # 83s — comfortably under the 90s target / 100s QC hard cap. The earlier
    # 240-word cap ALSO omitted the verdict + disclaimer, which is exactly
    # how a "valid" script rendered to 103s and failed QC in production.
    words = len(str(data["hook"]).split()) + len(str(data["outro"]).split())
    words += sum(len(str(b["narration"]).split()) for b in beats[:3])
    words += len(str(v.get("headline", "")).split()) + len(str(v.get("detail", "")).split())
    if words > 175:
        raise ValueError(f"script too long ({words} spoken words incl. verdict, "
                         f"must be <=175 to render under 90s)")


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
            # Feed the previous attempt's validation error back so a retry is
            # corrective, not a coin-flip on the identical prompt (a too-long
            # script tends to come back too long again otherwise).
            msg = user_msg if last_err is None else (
                f"{user_msg}\n\nYour previous attempt was rejected: {last_err}. "
                f"Fix exactly that and return the corrected JSON.")
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=3000,
                system=SYSTEM_PROMPTS[category],
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
    if category == "sports":
        # WORLD SPORTS ASTROLOGY branding + globally-searched tags. The match
        # itself (teams/competition) is already in title_en from the prompt,
        # so these ride alongside it rather than replacing it.
        data.setdefault("tags", [
            "world sports astrology", "sports astrology", "astrology prediction",
            "football astrology", "cricket astrology", "match prediction astrology",
            "vedic astrology sports", "astrology today", "sports prediction",
        ])
        data.setdefault("hashtags", ["#WorldSportsAstrology", "#SportsAstrology",
                                     "#astrology", "#football", "#cricket"])
    elif category == "love":
        pf = context["pair"]
        a, b = pf["sign_a"], pf["sign_b"]
        # The score is COMPUTED from the real aspect, so overwrite whatever the
        # model returned: the same pairing must always report the same number
        # (viewers do re-check, and compare with friends). Also guarantees the
        # "never crushingly low" floor baked into _ASPECT.
        data.setdefault("verdict", {})["confidence_pct"] = pf["score"]
        # Long-tail search is the whole point of this format — people type
        # their exact pairing, in both word orders.
        data.setdefault("tags", [
            f"{a.lower()} and {b.lower()} compatibility",
            f"{b.lower()} and {a.lower()} compatibility",
            f"{a.lower()} {b.lower()} love", "zodiac compatibility",
            "love astrology", "star sign compatibility", "astrology love match",
            "zodiac love match", "synastry astrology",
        ])
        data.setdefault("hashtags", [f"#{a.lower()}", f"#{b.lower()}",
                                     "#zodiaccompatibility", "#loveastrology",
                                     "#astrology"])
        data.setdefault("subject_label", f"{a} + {b}")
    else:
        data.setdefault("tags", ["astrology", category, "prediction"])
        data.setdefault("hashtags", ["#astrology", f"#{category}", "#prediction"])
    if category == "love":
        # Pairing FIRST — it is the search term. Brand suffix trails it.
        pf = context["pair"]
        data["title"] = (f"{pf['sign_a']} + {pf['sign_b']} Love Compatibility "
                         f"| {pf['score']}% Match")[:100]
    elif category == "sports":
        # Brand suffix so the series is recognisable in search/suggested; the
        # fixture stays FIRST because that's what fans actually search for.
        data["title"] = f"{data['title_en']} | World Sports Astrology"[:100]
    else:
        data["title"] = f"{data['title_en']} | {when}"[:100]
    data["outro"] = f"{str(data['outro']).rstrip('. ')}. {disclaimer}"
    data["description"] = f"{data['description']}\n\n{disclaimer}"
    if category == "love":
        pf = context["pair"]
        data["pinned_comment"] = (
            f"Are you a {pf['sign_a']} or a {pf['sign_b']}? Tag your other "
            f"half and tell us if this one landed ⬇️\n"
            f"A new zodiac pairing every day — Subscribe 🔔\n\n{disclaimer}"
        )
    else:
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
