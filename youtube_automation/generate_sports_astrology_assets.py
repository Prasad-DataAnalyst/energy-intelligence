#!/usr/bin/env python3
"""
generate_sports_astrology_assets.py
Daily Sports Astrology Prediction — combines real match facts (from
sports_data.py) with a REAL computed astrology chart (from astro_chart.py,
Swiss Ephemeris / Lahiri sidereal) for each match's start time + venue, and
has Claude write the sports analysis, astrology narrative, prediction, and
full YouTube content package (Tamil + English) for each match.

Claude is given the chart's real facts as GROUND TRUTH and instructed to
narrate them, not invent its own planetary positions — see SYSTEM_PROMPT.
The sports-analysis half is written as plausible/generic framing (form,
tendencies, historical patterns) rather than claimed real-time team news,
since no live injury/lineup feed is wired in.

Usage:
  python3 generate_sports_astrology_assets.py 20260709
  python3 generate_sports_astrology_assets.py 20260709 --period "July 2026"
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

import anthropic
from dotenv import load_dotenv

import astro_chart
import sports_data

load_dotenv()

HERE = Path(__file__).parent

DISCLAIMER = ("This prediction is for entertainment and astrology analysis "
              "only. It is not betting or financial advice.")

SYSTEM_PROMPT = """You are the writer and host of a popular sports astrology YouTube
channel. For each match you are given real match facts and a REAL COMPUTED
astrology chart (ascendant, moon sign/nakshatra, 7th house, day lord, hora
lord, and Mars/Saturn/Jupiter/Rahu/Ketu positions) for that match's exact
start time and venue.

CRITICAL: the chart facts you are given are real astronomical computations.
Narrate and interpret THOSE exact facts — never invent different planetary
positions, signs, or nakshatras of your own.

Tone: exciting, spiritual, and professional. Sound like a knowledgeable host
who takes both sports analysis and astrology seriously, speaking to one
viewer.

Rules:
- Sports View: recent form, key players, injuries/availability, head-to-head
  record, venue advantage, weather/pitch condition if relevant. Frame this
  generically/plausibly (team tendencies, historical patterns, typical
  conditions) — do NOT state specific injury news or lineup facts as if
  confirmed for this exact fixture, since you do not have live team-news
  access. It is fine to say things like "if their in-form attacker starts"
  rather than asserting who will start.
- Astrology View: interpret the given chart facts — moon strength, ascendant
  vs 7th house comparison, day lord and hora lord effect, and the
  Mars/Saturn/Jupiter/Rahu/Ketu influence — into a flowing, spoken-style
  paragraph. Use the given "momentum_favors" lean as your steer for who the
  chart favors, but express it as an astrological LEAN, not a certainty.
- NEVER guarantee a match result. NEVER promote or reference betting/gambling
  in any way. Use phrases like "slight edge," "strong chance," "close
  contest," and "astrology indicates" rather than definitive claims.
- confidence_pct must be modest and realistic: 55-78. Never near 100.
- Everything must be written for spoken delivery: warm, natural rhythm,
  short punchy sentences (this becomes word-synced captions).

Return ONLY valid raw JSON. No markdown, no code fences, no commentary."""


def _build_user_prompt(match: dict, chart: dict) -> str:
    dt = datetime.fromisoformat(match["datetime_utc"])
    planets_block = "\n".join(
        f"  - {p}: {d['sign']} ({d['dignity']})" for p, d in chart["planets"].items()
    )
    return f"""MATCH FACTS:
Sport: {match['sport']}
Match: {match['match_name'] or f"{match['team_a']} vs {match['team_b']}"}
Team A (home/1st house): {match['team_a']}
Team B (away/7th house): {match['team_b']}
Start (UTC): {dt.strftime('%B %d, %Y %H:%M UTC')}
Venue: {match['venue'] or 'TBC'}, {match['country'] or 'TBC'}
League/Competition: {match['league'] or 'N/A'}

REAL COMPUTED ASTROLOGY CHART (start-time, sidereal/Lahiri):
Ascendant: {chart['ascendant_sign']}
7th House: {chart['seventh_house_sign']}
Moon: {chart['moon_sign']} ({chart['moon_nakshatra']} nakshatra) — {chart['moon_strength']}
Day Lord: {chart['day_lord']}
Hora Lord: {chart['hora_lord']}
{planets_block}
Momentum lean (heuristic, yours to interpret — not a certainty): {chart['momentum_favors']}

Write the full prediction package. Return this EXACT JSON shape:
{{
  "sports_view": "90-130 words, flowing spoken paragraph covering recent form, key players, injuries/availability framed generically, head-to-head, venue advantage, weather/pitch if relevant",
  "astrology_view": "90-130 words, flowing spoken paragraph interpreting the given chart facts — moon strength, ascendant vs 7th house, day/hora lord effect, Mars/Saturn/Jupiter/Rahu/Ketu influence, momentum",
  "prediction": {{
    "favoured_team": "team name with a slight/strong edge, or 'Too close to call'",
    "match_type": "close contest" or "one-sided",
    "turning_point": "short phrase describing a possible momentum shift",
    "confidence_pct": 65
  }},
  "youtube": {{
    "title_en": "exciting YouTube title in English, <=90 chars",
    "title_ta": "same title translated/localized into Tamil",
    "description_ta": "2-3 sentence YouTube description written in Tamil",
    "tags_en": ["8-10 lowercase English search tags"],
    "tags_ta": ["5-8 Tamil search tags"],
    "thumbnail_texts": ["3 short punchy thumbnail text ideas, <=6 words each"],
    "script_30s": "a 30-second vertical Shorts script for JUST this match, punchy hook + prediction",
    "script_60s": "a 60-second script for JUST this match with a bit more sports+astrology detail"
  }}
}}"""


def _validate(match_result: dict) -> None:
    for k in ("sports_view", "astrology_view", "prediction", "youtube"):
        if not match_result.get(k):
            raise ValueError(f"missing '{k}'")
    if len(str(match_result["sports_view"]).split()) < 50:
        raise ValueError("sports_view too short")
    if len(str(match_result["astrology_view"]).split()) < 50:
        raise ValueError("astrology_view too short")
    pred = match_result["prediction"]
    for k in ("favoured_team", "match_type", "turning_point", "confidence_pct"):
        if k not in pred:
            raise ValueError(f"prediction missing '{k}'")
    conf = pred["confidence_pct"]
    if not isinstance(conf, (int, float)) or not (50 <= conf <= 85):
        raise ValueError(f"confidence_pct out of sane range: {conf}")
    yt = match_result["youtube"]
    for k in ("title_en", "title_ta", "description_ta", "tags_en", "tags_ta",
             "thumbnail_texts", "script_30s", "script_60s"):
        if not yt.get(k):
            raise ValueError(f"youtube missing '{k}'")


def _generate_for_match(client: anthropic.Anthropic, match: dict) -> dict:
    dt = datetime.fromisoformat(match["datetime_utc"])
    chart = astro_chart.compute_chart(dt, match.get("venue", ""), match.get("country", ""))
    user_msg = _build_user_prompt(match, chart)

    last_err = None
    for attempt in range(1, 4):
        try:
            resp = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = resp.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            candidate = json.loads(raw)
            _validate(candidate)
            candidate["match"] = match
            candidate["chart"] = chart
            return candidate
        except Exception as e:
            last_err = e
            print(f"[WARN] Match '{match.get('match_name')}' attempt {attempt}/3 failed: {e}",
                  file=sys.stderr)
    raise RuntimeError(f"Sports prediction generation failed for "
                       f"'{match.get('match_name')}' after 3 attempts: {last_err}")


def _format_human_readable(when: str, results: list) -> str:
    lines = [f"Today's Sports Astrology Prediction – {when}", ""]
    for i, r in enumerate(results, 1):
        m, p = r["match"], r["prediction"]
        dt = datetime.fromisoformat(m["datetime_utc"])
        lines += [
            f"Match {i}:",
            f"Sport: {m['sport'].title()}",
            f"Teams: {m['team_a']} vs {m['team_b']}",
            f"Time: {dt.strftime('%B %d, %Y %H:%M UTC')}",
            f"Venue: {m['venue']}, {m['country']}",
            f"Sports View: {r['sports_view']}",
            f"Astrology View: {r['astrology_view']}",
            f"Prediction: {p['favoured_team']} — {p['match_type']}. "
            f"Possible turning point: {p['turning_point']}.",
            f"Confidence: {p['confidence_pct']}%",
            "",
        ]
    lines.append(DISCLAIMER)
    return "\n".join(lines)


def generate(date_tag: str = None, period: str = None) -> str:
    if not date_tag:
        date_tag = date.today().strftime("%Y%m%d")
    when = datetime.strptime(date_tag, "%Y%m%d").strftime("%B %d, %Y")

    matches = sports_data.fetch_today_matches(date_tag)
    if not matches:
        raise RuntimeError(
            f"No matches available for {date_tag} — supply "
            f"sports_matches_{date_tag}.json (see sports_data.py) or check "
            f"the SPORTSDB_API_KEY / network."
        )

    client = anthropic.Anthropic(timeout=120)
    results = []
    for match in matches:
        print(f"[INFO] {match['sport']}: {match['match_name']} → generating prediction...")
        results.append(_generate_for_match(client, match))

    human_readable = _format_human_readable(when, results)

    all_tags = sorted({t for r in results for t in r["youtube"]["tags_en"]})[:15]
    data = {
        "content_type": "sports",
        "date": when,
        "period_label": "TODAY",
        "title": f"Sports Astrology Predictions — {when} | {', '.join(m['match']['sport'].title() for m in results[:3])}",
        "description": (f"Daily sports astrology predictions for {when}. "
                        f"{DISCLAIMER}"),
        "tags": all_tags,
        "hashtags": ["#sportsastrology", "#astrology", "#predictions"],
        "disclaimer": DISCLAIMER,
        "human_readable": human_readable,
        "matches": results,
        "pinned_comment": (f"Which match are you watching today? Drop it below! ⬇️\n"
                           f"New sports astrology prediction every day — Subscribe 🔔\n\n"
                           f"{DISCLAIMER}"),
    }

    filename = f"sports_{date_tag}.json"
    (HERE / filename).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Assets → {filename}  ({len(results)} matches)")
    print(f"\n{human_readable}")
    return filename


def main():
    date_tag = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else None
    period = None
    if "--period" in sys.argv:
        period = sys.argv[sys.argv.index("--period") + 1]
    generate(date_tag, period)


if __name__ == "__main__":
    main()
