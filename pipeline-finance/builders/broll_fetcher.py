"""
builders/broll_fetcher.py — DriftWire326 visual design v2
Topical stock photography from the Pexels API (free tier: 200 req/hour),
stylized into branded full-bleed slides with a dark overlay + caption.

Requires PEXELS_API_KEY in .env — without it, everything returns empty
and the video simply builds without photo slides (never fatal).

Pexels license: free for commercial use, no attribution required — but we
credit photographers in a description line as good practice.
"""
import logging
import re
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

BROLL_DIR = settings.output_dir / "broll"
_SEARCH_URL = "https://api.pexels.com/v1/search"

# Rotating generic finance queries — mixed with content-specific ones
_GENERIC_QUERIES = [
    "stock market trading screen",
    "wall street new york",
    "stock exchange trading floor",
    "financial district skyline",
    "candlestick chart monitor",
    "federal reserve building",
]


def _api_key() -> str:
    return getattr(settings, "pexels_api_key", "") or ""


def fetch_photo(query: str) -> Optional[tuple[Path, str]]:
    """
    Fetch the best landscape photo for a query.
    Returns (path, photographer_credit) or None. Cached per query per day.
    """
    key = _api_key()
    if not key:
        return None

    slug = re.sub(r"[^a-z0-9]+", "_", query.lower())[:40]
    cached = BROLL_DIR / f"{date.today().isoformat()}_{slug}.jpg"
    credit_file = cached.with_suffix(".credit")
    if cached.exists() and cached.stat().st_size > 10_000:
        credit = credit_file.read_text() if credit_file.exists() else "Pexels"
        return cached, credit

    try:
        resp = requests.get(
            _SEARCH_URL,
            headers={"Authorization": key},
            params={
                "query": query,
                "orientation": "landscape",
                "size": "large",
                "per_page": 3,
            },
            timeout=20,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        if not photos:
            logger.info("Pexels: no results for '%s'", query)
            return None

        photo = photos[0]
        url = photo["src"].get("large2x") or photo["src"].get("large")
        img = requests.get(url, timeout=30)
        img.raise_for_status()

        BROLL_DIR.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(img.content)
        credit = photo.get("photographer", "Pexels")
        credit_file.write_text(credit)
        logger.info("Pexels photo fetched: '%s' by %s", query, credit)
        return cached, credit
    except Exception as exc:
        logger.warning("Pexels fetch failed for '%s': %s", query, exc)
        return None


def stylize_broll(photo_path: Path, caption: str = "") -> Optional[Path]:
    """
    Turn a raw photo into a branded slide: cover-crop to video resolution,
    dark gradient overlay for text legibility, caption bottom-left, brand bar.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageFilter

        w, h = settings.video_width, settings.video_height
        img = Image.open(photo_path).convert("RGB")
        img = ImageOps.fit(img, (w, h), method=Image.LANCZOS)

        # Dark gradient: heavier at bottom where the caption sits
        overlay = Image.new("L", (1, h))
        for y in range(h):
            overlay.putpixel((0, y), int(90 + 110 * (y / h)))
        overlay = overlay.resize((w, h))
        black = Image.new("RGB", (w, h), (8, 8, 14))
        img = Image.composite(black, img, overlay.point(lambda v: 255 - v))

        draw = ImageDraw.Draw(img)
        draw.rectangle((0, 0, w, 8), fill=(90, 40, 200))

        def _font(px, bold=True):
            name = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
                    if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
            try:
                return ImageFont.truetype(name, px)
            except Exception:
                return ImageFont.load_default()

        if caption:
            draw.text((int(w * 0.06), h - int(h * 0.16)), caption.upper(),
                      font=_font(int(h * 0.048)), fill=(240, 242, 245), anchor="lm")
        draw.text((w - int(w * 0.04), h - 30), "@DriftWire326",
                  font=_font(int(h * 0.026), bold=False),
                  fill=(200, 205, 215), anchor="rm")

        out = photo_path.with_name(photo_path.stem + "_slide.png")
        img.save(out)
        return out
    except Exception as exc:
        logger.warning("B-roll stylize failed: %s", exc)
        return None


def get_broll_slides(context_terms: list[str], count: int = 3) -> list[Path]:
    """
    Return up to `count` stylized photo slides. Queries mix content-specific
    terms (e.g. the day's top-story company) with rotating generic finance
    imagery. Returns [] when no key or no network — never raises.

    context_terms: e.g. ["META stock", "technology sector"] with the first
    entries treated as captions-worthy specifics.
    """
    if not _api_key():
        logger.info("PEXELS_API_KEY not set — skipping photo B-roll")
        return []

    day_offset = date.today().toordinal()
    queries: list[tuple[str, str]] = []          # (query, caption)
    for term in context_terms[:2]:
        queries.append((term, term))
    while len(queries) < count + 1:              # +1 spare in case one fails
        generic = _GENERIC_QUERIES[(day_offset + len(queries)) % len(_GENERIC_QUERIES)]
        queries.append((generic, ""))

    slides: list[Path] = []
    for query, caption in queries:
        if len(slides) >= count:
            break
        fetched = fetch_photo(query)
        if not fetched:
            continue
        slide = stylize_broll(fetched[0], caption)
        if slide:
            slides.append(slide)
    logger.info("B-roll slides ready: %d", len(slides))
    return slides
