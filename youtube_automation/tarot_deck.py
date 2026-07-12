#!/usr/bin/env python3
"""
tarot_deck.py
The 22 Major Arcana for the weekly tarot-reading video: card metadata
(name, upright meaning keywords), PUBLIC-DOMAIN imagery, and a deterministic
weekly 12-card draw (one distinct card per zodiac sign per ISO week).

IMAGERY & LICENSE: the Rider–Waite–Smith deck (Pamela Colman Smith, first
published 1909) is public domain in the United States (pre-1929 publication).
Card scans are fetched once from Wikimedia Commons via the stable
Special:FilePath redirect (no volatile hash-path URLs) and cached in
.tarot_cards/. If a download fails (offline VM, renamed file), a clean
DRAWN fallback card (name + roman numeral in the house style) is generated
instead — the render never dies on a missing image.

NOTE: the exact Commons filenames below follow the well-known
"RWS_Tarot_NN_Name.jpg" convention but could not be live-verified from this
dev sandbox (network blocked). Run `python3 tarot_deck.py --prefetch` once
on the VM: it downloads all 22 and reports any misses so filenames can be
corrected before the first scheduled video.
"""
import hashlib
import random
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).parent
CACHE_DIR = HERE / ".tarot_cards"
# Drop your own card images here as e.g. "the-fool.jpg" to override downloads.
OVERRIDE_DIR = HERE / "tarot_override"

_COMMONS = "https://commons.wikimedia.org/wiki/Special:FilePath/"
_UA = {"User-Agent": "GetMindFuelNow/1.0 (public-domain tarot imagery fetch)"}
REQUEST_TIMEOUT = 20

SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]

# (display name, roman numeral, Commons filename candidates, upright keywords)
MAJORS = [
    ("The Fool", "0", ["RWS_Tarot_00_Fool.jpg"],
     "new beginnings, spontaneity, a leap of faith, fresh energy"),
    ("The Magician", "I", ["RWS_Tarot_01_Magician.jpg"],
     "manifestation, resourcefulness, skill, making it happen"),
    ("The High Priestess", "II", ["RWS_Tarot_02_High_Priestess.jpg"],
     "intuition, inner knowing, mystery, listening to yourself"),
    ("The Empress", "III", ["RWS_Tarot_03_Empress.jpg"],
     "abundance, nurturing, creativity, comfort and growth"),
    ("The Emperor", "IV", ["RWS_Tarot_04_Emperor.jpg"],
     "structure, authority, stability, taking charge"),
    ("The Hierophant", "V", ["RWS_Tarot_05_Hierophant.jpg"],
     "tradition, guidance, learning, trusted advice"),
    ("The Lovers", "VI", ["RWS_Tarot_06_Lovers.jpg", "TheLovers.jpg"],
     "love, alignment, meaningful choices, partnership"),
    ("The Chariot", "VII", ["RWS_Tarot_07_Chariot.jpg"],
     "willpower, momentum, victory through focus, drive"),
    ("Strength", "VIII", ["RWS_Tarot_08_Strength.jpg"],
     "quiet courage, patience, inner strength, gentle power"),
    ("The Hermit", "IX", ["RWS_Tarot_09_Hermit.jpg"],
     "reflection, wisdom, stepping back, finding answers within"),
    ("Wheel of Fortune", "X", ["RWS_Tarot_10_Wheel_of_Fortune.jpg"],
     "turning points, luck, cycles shifting, destiny in motion"),
    ("Justice", "XI", ["RWS_Tarot_11_Justice.jpg"],
     "fairness, truth, balance, accountability paying off"),
    ("The Hanged Man", "XII", ["RWS_Tarot_12_Hanged_Man.jpg"],
     "new perspective, pause, surrender, seeing it differently"),
    ("Death", "XIII", ["RWS_Tarot_13_Death.jpg"],
     "transformation, endings that free you, renewal, release"),
    ("Temperance", "XIV", ["RWS_Tarot_14_Temperance.jpg"],
     "balance, moderation, patience, blending opposites well"),
    ("The Devil", "XV", ["RWS_Tarot_15_Devil.jpg"],
     "naming what binds you, temptation, reclaiming your power"),
    ("The Tower", "XVI", ["RWS_Tarot_16_Tower.jpg"],
     "sudden change, breakthrough, clearing the false, liberation"),
    ("The Star", "XVII", ["RWS_Tarot_17_Star.jpg"],
     "hope, healing, renewal, quiet confidence in the future"),
    ("The Moon", "XVIII", ["RWS_Tarot_18_Moon.jpg"],
     "intuition, dreams, navigating uncertainty, trust your gut"),
    ("The Sun", "XIX", ["RWS_Tarot_19_Sun.jpg"],
     "joy, success, vitality, things going right"),
    ("Judgement", "XX", ["RWS_Tarot_20_Judgement.jpg"],
     "awakening, a call to rise, self-forgiveness, renewal"),
    ("The World", "XXI", ["RWS_Tarot_21_World.jpg"],
     "completion, achievement, wholeness, a cycle fulfilled"),
]


def _slug(name: str) -> str:
    return name.lower().replace(" ", "-")


def week_key(date_tag: str) -> str:
    """ISO year-week key, e.g. '2026-W28' — the deterministic draw seed."""
    d = datetime.strptime(date_tag, "%Y%m%d")
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def draw_weekly(date_tag: str) -> dict:
    """{sign: card_dict} — 12 DISTINCT majors per ISO week, deterministic
    (same week → same spread on every run/machine; next week differs).
    Uses md5, not hash(), because Python salts hash() per process."""
    seed = int(hashlib.md5(week_key(date_tag).encode()).hexdigest(), 16)
    rng = random.Random(seed)
    deck = list(range(len(MAJORS)))
    rng.shuffle(deck)
    spread = {}
    for sign, idx in zip(SIGNS, deck):
        name, numeral, files, keywords = MAJORS[idx]
        spread[sign] = {"name": name, "numeral": numeral,
                        "keywords": keywords, "files": files}
    return spread


def _download_card(card: dict):
    """Local path of the card's public-domain scan (cached), or None.

    Wikimedia rate-limits rapid bursts (observed live: a straight 22-request
    loop got 8/22, an immediate rerun with the same filenames got 5 more —
    same names succeeding on retry means throttling, not bad filenames). So:
    retry each file on 429/5xx with a backoff, and pace politely."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    slug = _slug(card["name"])
    if OVERRIDE_DIR.exists():
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            p = OVERRIDE_DIR / f"{slug}{ext}"
            if p.exists():
                return p
    dest = CACHE_DIR / f"{slug}.jpg"
    if dest.exists() and dest.stat().st_size > 5000:
        return dest
    for fname in card["files"]:
        for attempt in range(3):
            try:
                r = requests.get(_COMMONS + fname, headers=_UA,
                                 timeout=REQUEST_TIMEOUT, allow_redirects=True)
                if r.status_code == 200 and len(r.content) > 5000:
                    dest.write_bytes(r.content)
                    return dest
                if r.status_code in (429, 500, 502, 503):
                    time.sleep(4 * (attempt + 1))   # throttled — back off, retry
                    continue
                break   # 404 etc — this filename is wrong, try the next one
            except Exception as e:
                print(f"[WARN] tarot image fetch failed ({fname}): {e}", file=sys.stderr)
                time.sleep(2)
    return None


def _fallback_card_image(card: dict, w: int = 700, h: int = 1200):
    """A clean drawn stand-in in the channel's cosmic style — used only when
    the public-domain scan can't be fetched. Never raises."""
    import make_daily_video as mdv
    from PIL import Image, ImageDraw
    img = mdv._cosmic_bg(w, h, (14, 6, 24), (34, 16, 52), (190, 140, 255), seed=42)
    d = ImageDraw.Draw(img)
    d.rectangle([10, 10, w - 10, h - 10], outline=(212, 175, 55), width=6)
    nf = mdv._display_font(120, weight=700)
    nw = mdv._tw(card["numeral"], nf)
    d.text(((w - nw) // 2, 160), card["numeral"], font=nf, fill=(212, 175, 55))
    tf = mdv._display_font(64, weight=700)
    y = h // 2 - 60
    for ln in mdv._wrap(card["name"], tf, w - 120)[:3]:
        tw_ = mdv._tw(ln, tf)
        d.text(((w - tw_) // 2, y), ln, font=tf, fill=(235, 230, 250))
        y += mdv._th(tf) + 10
    return img.convert("RGB")


def card_image(card: dict):
    """PIL Image for a card: real RWS scan if available, drawn fallback if
    not. Always returns an image."""
    from PIL import Image
    path = _download_card(card)
    if path:
        try:
            return Image.open(path).convert("RGB")
        except Exception as e:
            print(f"[WARN] tarot image unreadable ({path}): {e}", file=sys.stderr)
    return _fallback_card_image(card)


def main():
    if "--prefetch" in sys.argv:
        misses = []
        for name, numeral, files, _kw in MAJORS:
            card = {"name": name, "numeral": numeral, "files": files}
            already_cached = (CACHE_DIR / f"{_slug(name)}.jpg").exists()
            p = _download_card(card)
            print(f"  {'OK ' if p else 'MISS'}  {name}" + (f"  → {p}" if p else ""))
            if not p:
                misses.append(name)
            if not already_cached:
                time.sleep(1.5)   # pace fresh downloads — Wikimedia throttles bursts
        print(f"\n{22 - len(misses)}/22 downloaded" +
              (f" — MISSING: {', '.join(misses)} (drawn fallback will be used)"
               if misses else " — all real card scans available."))
        return
    date_tag = sys.argv[1] if len(sys.argv) > 1 else datetime.now().strftime("%Y%m%d")
    spread = draw_weekly(date_tag)
    print(f"Week {week_key(date_tag)} spread:")
    for sign, card in spread.items():
        print(f"  {sign.title():<12} {card['numeral']:>5}  {card['name']:<20} ({card['keywords']})")


if __name__ == "__main__":
    main()
