#!/usr/bin/env python3
"""
generate_daily_assets.py
Generates all 12 sign horoscope data in ONE Claude API call, for any timeframe.
Output: <type>_horoscope_YYYYMMDD.json  (daily_/weekly_/monthly_)

The card LAYOUT is identical across types — only the words, the on-card period
label, the per-sign duration and the SEO change. So a weekly/monthly video
reuses the whole renderer; this file just swaps the prompt and metadata.

Usage:
  python3 generate_daily_assets.py "July 2026"                 # daily, today
  python3 generate_daily_assets.py "July 2026" 20260705        # daily, dated
  python3 generate_daily_assets.py "July 2026" 20260705 weekly
  python3 generate_daily_assets.py "July 2026" 20260701 monthly
"""
import json
import re
import sys
from datetime import date, datetime, timedelta

import anthropic
from dotenv import load_dotenv

load_dotenv()

SIGNS = [
    "aries","taurus","gemini","cancer","leo","virgo",
    "libra","scorpio","sagittarius","capricorn","aquarius","pisces",
]

# Per-type configuration. period_label shows on each card; sign_secs sets the
# slot length (and thus whether the video is a Short (<180s) or a regular video).
TYPE_CONFIG = {
    "daily": {
        "noun":         "Daily",
        "timeframe":    "today",
        "period_label": "TODAY",
        "sign_secs":    14,
        "max_words":    6,
    },
    "weekly": {
        "noun":         "Weekly",
        "timeframe":    "this week",
        "period_label": "THIS WEEK",
        "sign_secs":    14,
        "max_words":    6,
    },
    "monthly": {
        "noun":         "Monthly",
        "timeframe":    "this month",
        "period_label": "THIS MONTH",
        "sign_secs":    22,      # ~4m30s total → a regular video, more detail
        "max_words":    10,
    },
    # LONG-FORM daily for MONETIZATION: ~8.5 min (mid-roll-ad eligible), all 12
    # signs in depth. Card stays punchy (max_words 6); the extra "reading"
    # paragraph is what the narrator reads for ~35s/sign.
    "deep": {
        "noun":         "Full Daily",
        "timeframe":    "today",
        "period_label": "IN DEPTH",
        "sign_secs":    40,
        "max_words":    6,
        "deep":         True,    # adds a "reading" paragraph + luckiest_sign
    },
}

_REQUIRED_FIELDS = ("love", "career", "money", "health",
                    "lucky_number", "lucky_color", "best_time", "advice")

# Default lucky-value seeds so Claude has a template to overwrite (kept varied).
_SEED = {
    "aries": (7, "Red", "4 PM"),        "taurus": (4, "Forest Green", "10 AM"),
    "gemini": (11, "Yellow", "2 PM"),   "cancer": (2, "Silver", "9 PM"),
    "leo": (1, "Gold", "Noon"),         "virgo": (6, "Navy Blue", "8 AM"),
    "libra": (9, "Rose Pink", "5 PM"),  "scorpio": (8, "Deep Red", "11 PM"),
    "sagittarius": (3, "Purple", "3 PM"), "capricorn": (10, "Dark Brown", "7 AM"),
    "aquarius": (5, "Electric Blue", "6 PM"), "pisces": (12, "Sea Green", "Sunset"),
}


def _system_prompt(cfg: dict) -> str:
    tf, mw = cfg["timeframe"], cfg["max_words"]
    scope = {
        "today":      "the day ahead",
        "this week":  "the 7 days ahead (mention the strongest and the most cautious day)",
        "this month": "the month ahead (mention which week is strongest)",
    }[tf]
    deep_block = ""
    if cfg.get("deep"):
        deep_block = """
- reading:      A warm, flowing spoken paragraph of 70-90 words that a narrator
                reads aloud — cover love, career, money, health and lucky guidance
                in depth, like a real astrologer speaking directly to the viewer.
                Full sentences (this is NOT shown on screen, only spoken).
"""
    return f"""You are a professional astrologer creating {cfg['noun'].lower()} horoscope content for all 12 zodiac signs.

Cover the 5 things people actually check in a horoscope:
Love & Relationships, Career & Business, Money & Finance, Health & Energy, and Lucky Guidance.

Each prediction is for {scope}. The short fields below appear ON SCREEN and must be
MAX {mw} words — punchy and specific, never vague.

Fields per sign:
- love:         romance, relationships, connections {tf} (MAX {mw} words)
- career:       job, business, workplace energy {tf} (MAX {mw} words)
- money:        income, spending, investments {tf} (MAX {mw} words)
- health:       physical energy, stress, mood {tf} (MAX {mw} words)
- lucky_number: one integer 1-99
- lucky_color:  one color name (1-2 words)
- best_time:    the luckiest time/day, e.g. "4 PM", "Tuesday", "First Week" (1-3 words)
- advice:       one simple actionable tip or remedy for {tf} (MAX {mw} words){deep_block}
Style rules:
- Direct. Specific. No generic fluff.
- Vary tone across signs — not all positive, some carry warnings.
- lucky_number, lucky_color and best_time must be single values (no lists).
{"- ALSO return a top-level string field 'luckiest_sign' = the ONE sign with the best day (capitalized, e.g. 'Leo')." if cfg.get('deep') else ""}

Return ONLY valid raw JSON. No markdown. No explanation. No code fences."""


def _title(cfg: dict, when: str) -> str:
    n = cfg["noun"]
    if cfg is TYPE_CONFIG["daily"]:
        return f"{n} Horoscope Today, {when} — All 12 Zodiac Signs (Love, Career, Money)"
    if cfg is TYPE_CONFIG["weekly"]:
        return f"{n} Horoscope {when} — All 12 Zodiac Signs This Week (Love, Career, Money)"
    if cfg.get("deep"):
        return f"Full Horoscope Today {when} — All 12 Zodiac Signs In Depth (Love, Career, Money, Health)"
    return f"{n} Horoscope {when} — All 12 Zodiac Signs (Love, Career, Money, Health)"


def _validate(data: dict, deep: bool = False) -> None:
    """Raise ValueError if any sign or required field is missing/empty."""
    signs = data.get("signs")
    if not isinstance(signs, dict):
        raise ValueError("'signs' object missing")
    missing_signs = [s for s in SIGNS if s not in signs]
    if missing_signs:
        raise ValueError(f"missing signs: {missing_signs}")
    required = _REQUIRED_FIELDS + (("reading",) if deep else ())
    for s in SIGNS:
        f = signs[s]
        if not isinstance(f, dict):
            raise ValueError(f"{s}: not an object")
        for k in required:
            v = f.get(k)
            if v is None or (isinstance(v, str) and not v.strip()):
                raise ValueError(f"{s}: empty field '{k}'")
            if isinstance(v, (list, dict)):
                raise ValueError(f"{s}: field '{k}' must be a single value, got {type(v).__name__}")
        if deep and len(str(f.get("reading", "")).split()) < 40:
            raise ValueError(f"{s}: 'reading' too short for a deep-dive narration")


def _when_label(ctype: str, date_tag: str) -> str:
    """Human 'when' string for titles: a date, a week range, or a month."""
    d = datetime.strptime(date_tag, "%Y%m%d")
    if ctype == "daily":
        return d.strftime("%B %d, %Y")
    if ctype == "weekly":
        end = d + timedelta(days=6)
        if d.month == end.month:
            return f"{d.strftime('%B %d')}–{end.strftime('%d, %Y')}"
        return f"{d.strftime('%b %d')} – {end.strftime('%b %d, %Y')}"
    return d.strftime("%B %Y")   # monthly


def generate(period: str, date_tag: str = None, ctype: str = "daily") -> str:
    ctype = ctype if ctype in TYPE_CONFIG else "daily"
    cfg   = TYPE_CONFIG[ctype]
    client = anthropic.Anthropic(timeout=90)

    if not date_tag:
        date_tag = date.today().strftime("%Y%m%d")
    when = _when_label(ctype, date_tag)

    is_deep = bool(cfg.get("deep"))
    # Build the per-sign JSON skeleton with seeded lucky values.
    sign_lines = []
    for s in SIGNS:
        n, c, t = _SEED[s]
        reading = ', "reading": "..."' if is_deep else ""
        sign_lines.append(
            f'    "{s}": {{"love": "...", "career": "...", "money": "...", '
            f'"health": "...", "lucky_number": {n}, "lucky_color": "{c}", '
            f'"best_time": "{t}", "advice": "..."{reading}}}'
        )
    signs_block = ",\n".join(sign_lines)

    title = _title(cfg, when)
    tf    = cfg["timeframe"]
    desc  = (f"Complete {cfg['noun'].lower()} horoscope for all 12 zodiac signs — {when}. "
             f"Love, career, money, health, lucky number, lucky color and best time. "
             f"Subscribe for {cfg['noun'].lower()} cosmic guidance. "
             f"#horoscope #astrology #zodiac")
    tags = [f"{cfg['noun'].lower()} horoscope", "all 12 signs horoscope",
            f"horoscope {tf}", "astrology", "zodiac reading",
            f"love horoscope {tf}", "health horoscope", f"money horoscope {tf}",
            f"horoscope {when}"]

    luckiest_line = '  "luckiest_sign": "...",\n' if is_deep else ""
    user_msg = f"""Generate the {cfg['noun'].lower()} horoscope for all 12 signs.
When: {when}
Period: {period}

Return this EXACT JSON structure (fill in all 12 signs, every "..." replaced):
{{
{luckiest_line}  "signs": {{
{signs_block}
  }}
}}"""

    print(f"[INFO] Generating all 12 signs ({ctype}) via Claude...")
    data = None
    last_err = None
    for attempt in range(1, 4):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=8000 if is_deep else 6000,
                system=_system_prompt(cfg),
                messages=[{"role": "user", "content": user_msg}],
            )
            raw = response.content[0].text.strip()
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
            candidate = json.loads(raw)
            _validate(candidate, deep=is_deep)
            data = candidate
            break
        except Exception as e:
            last_err = e
            print(f"[WARN] Attempt {attempt}/3 failed: {e}", file=sys.stderr)

    if data is None:
        raise RuntimeError(f"Claude asset generation failed after 3 attempts: {last_err}")

    # Attach the metadata the renderer + uploader read.
    data["content_type"] = ctype
    data["period_label"] = cfg["period_label"]
    data["sign_secs"]    = cfg["sign_secs"]
    data["date"]         = when
    data["title"]        = title
    data["description"]  = desc
    data["tags"]         = tags
    data["hashtags"]     = ["#horoscope", "#astrology", "#zodiac",
                            f"#{cfg['noun'].lower().replace(' ','')}horoscope", "#allsigns"]
    if is_deep:
        # Long-form: render at 10 fps (static cards look identical) to keep the
        # ~8.5-min encode within the e2-micro's budget; keep luckiest_sign for
        # the outro reveal.
        data["video_fps"] = 10
        data.setdefault("luckiest_sign", "")
        data["description"] = (
            f"Your COMPLETE daily horoscope for all 12 zodiac signs — {when}, in depth. "
            f"A full reading of love, career, money, health and lucky guidance for every "
            f"sign. Use the chapters to jump to your sign, and stay to the end for today's "
            f"luckiest sign. New full horoscope every morning — subscribe for daily cosmic "
            f"guidance. #horoscope #astrology #zodiac #dailyhoroscope")

    filename = f"{ctype}_horoscope_{date_tag}.json"
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"[OK] Assets → {filename}")
    return filename


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 generate_daily_assets.py 'July 2026' [YYYYMMDD] [daily|weekly|monthly]")
        sys.exit(1)
    period   = sys.argv[1]
    date_tag = sys.argv[2] if len(sys.argv) > 2 else None
    ctype    = sys.argv[3] if len(sys.argv) > 3 else "daily"
    generate(period, date_tag, ctype)


if __name__ == "__main__":
    main()
