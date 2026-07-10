#!/usr/bin/env python3
"""
stock_images.py
Fetch topical stock photos (free tiers) for the 90-second landscape
"prediction" videos, so a card shows real imagery instead of an empty
starfield. Two providers, tried in order; either can be missing:

  1. Pexels   (PEXELS_API_KEY)   — https://api.pexels.com/v1/search
  2. Pixabay  (PIXABAY_API_KEY)  — https://pixabay.com/api/

Design mirrors sports_data.py: a manual override folder is ALWAYS checked
first, and every network path degrades to [] (never raises) so a missing
key / rate limit / offline VM just falls back to the cosmic-gradient card
instead of failing the day's render.

NOTE ON VERIFICATION: the exact Pexels/Pixabay JSON field names below come
from their published API docs, not a live call from this dev sandbox
(outbound to those hosts is blocked here). Every field is read defensively
(.get(), never assumed). The FIRST live production run should be eyeballed:
  python3 stock_images.py "football stadium" 3
"""
import hashlib
import os
import sys
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

HERE = Path(__file__).parent
# Drop your own images here as <slug>.jpg to force-use them (slug = the
# search query lowercased, spaces->'-'), e.g. stock_override/france-morocco.jpg
OVERRIDE_DIR = HERE / "stock_override"
CACHE_DIR = HERE / ".stock_cache"       # downloaded images, reused across runs

PEXELS_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_KEY = os.getenv("PIXABAY_API_KEY", "").strip()

REQUEST_TIMEOUT = 15
_UA = {"User-Agent": "GetMindFuelNow/1.0 (astrology content bot)"}


def _slug(query: str) -> str:
    return "".join(c if c.isalnum() or c == " " else "" for c in query).strip().lower().replace(" ", "-")[:60]


def _override_for(query: str):
    if not OVERRIDE_DIR.exists():
        return None
    slug = _slug(query)
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        p = OVERRIDE_DIR / f"{slug}{ext}"
        if p.exists():
            return p
    return None


def _download(url: str, query: str, idx: int):
    """Download one image URL to the cache, return the local path or None."""
    if not url:
        return None
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(f"{query}|{idx}|{url}".encode()).hexdigest()[:16]
    dest = CACHE_DIR / f"{_slug(query)}-{key}.jpg"
    if dest.exists() and dest.stat().st_size > 1024:
        return dest
    try:
        r = requests.get(url, headers=_UA, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        if len(r.content) < 1024:
            return None
        dest.write_bytes(r.content)
        return dest
    except Exception as e:
        print(f"[WARN] stock image download failed ({url[:60]}...): {e}", file=sys.stderr)
        return None


def _pexels(query: str, count: int) -> list:
    if not PEXELS_KEY:
        return []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": PEXELS_KEY, **_UA},
            params={"query": query, "per_page": max(count, 5),
                    "orientation": "landscape"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        photos = r.json().get("photos", []) or []
    except Exception as e:
        print(f"[WARN] Pexels search failed for '{query}': {e}", file=sys.stderr)
        return []
    urls = []
    for p in photos:
        src = p.get("src", {}) or {}
        # Prefer a large landscape rendition; fall back through what's present.
        url = src.get("landscape") or src.get("large2x") or src.get("large") or src.get("original")
        if url:
            urls.append(url)
    return urls


def _pixabay(query: str, count: int) -> list:
    if not PIXABAY_KEY:
        return []
    try:
        r = requests.get(
            "https://pixabay.com/api/",
            headers=_UA,
            params={"key": PIXABAY_KEY, "q": query, "image_type": "photo",
                    "orientation": "horizontal", "per_page": max(count, 3),
                    "safesearch": "true"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        hits = r.json().get("hits", []) or []
    except Exception as e:
        print(f"[WARN] Pixabay search failed for '{query}': {e}", file=sys.stderr)
        return []
    urls = []
    for h in hits:
        url = h.get("largeImageURL") or h.get("webformatURL")
        if url:
            urls.append(url)
    return urls


def fetch_images(query: str, count: int = 1) -> list:
    """Return up to `count` LOCAL image paths for a search query, best-effort.
    Order: manual override -> Pexels -> Pixabay -> []. Never raises."""
    override = _override_for(query)
    if override:
        return [override] * count if count > 1 else [override]

    urls = _pexels(query, count)
    if not urls:
        urls = _pixabay(query, count)
    if not urls:
        return []

    out = []
    for i, url in enumerate(urls):
        p = _download(url, query, i)
        if p:
            out.append(p)
        if len(out) >= count:
            break
    return out


def fetch_first(query: str, fallback_query: str = None):
    """Single best image path for a query, or None. Tries a fallback query
    (e.g. a broader term) before giving up — useful when a specific phrase
    like 'France vs Morocco' returns nothing but 'football stadium' does."""
    got = fetch_images(query, 1)
    if got:
        return got[0]
    if fallback_query:
        got = fetch_images(fallback_query, 1)
        if got:
            return got[0]
    return None


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 stock_images.py 'search query' [count]")
        sys.exit(1)
    query = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    print(f"Providers: Pexels={'yes' if PEXELS_KEY else 'NO KEY'}, "
          f"Pixabay={'yes' if PIXABAY_KEY else 'NO KEY'}")
    paths = fetch_images(query, count)
    if not paths:
        print(f"[INFO] No images for '{query}'. (Drop one at "
              f"{OVERRIDE_DIR}/{_slug(query)}.jpg to force-use your own.)")
    for p in paths:
        print(f"  {p}  ({Path(p).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
