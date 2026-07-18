"""
builders/logo_overlay.py — DriftWire326 channel branding
Round channel-logo badge overlaid on every long-form video and Short.

The source logo is fetched automatically from the channel's own YouTube
avatar (channels.list, 1 quota unit — cached forever after first fetch),
so no manual asset upload is needed. Dropping a custom PNG at
assets/branding/channel_logo.png overrides the fetched avatar.

Everything here is best-effort: any failure returns None and the video
simply builds without the badge.
"""
import logging
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

BRANDING_DIR = settings.root_dir / "assets" / "branding"
LOGO_PATH = BRANDING_DIR / "channel_logo.png"


def ensure_channel_logo() -> Optional[Path]:
    """
    Return the channel logo PNG, downloading the channel's YouTube avatar
    on first use. Returns None if unavailable (no auth yet, API error...).
    """
    if LOGO_PATH.exists() and LOGO_PATH.stat().st_size > 1024:
        return LOGO_PATH

    try:
        import requests
        from uploader.uploader import _get_authenticated_service

        service = _get_authenticated_service()
        response = service.channels().list(part="snippet", mine=True).execute()
        items = response.get("items", [])
        if not items:
            logger.warning("Channel logo fetch: no channel found for these credentials")
            return None

        thumbs = items[0]["snippet"].get("thumbnails", {})
        url = (
            thumbs.get("high", {}).get("url")
            or thumbs.get("medium", {}).get("url")
            or thumbs.get("default", {}).get("url")
        )
        if not url:
            logger.warning("Channel logo fetch: no avatar set on the channel")
            return None

        img = requests.get(url, timeout=30)
        img.raise_for_status()
        BRANDING_DIR.mkdir(parents=True, exist_ok=True)
        LOGO_PATH.write_bytes(img.content)
        logger.info("Channel avatar downloaded → %s (%d bytes)", LOGO_PATH, len(img.content))
        return LOGO_PATH
    except Exception as exc:
        logger.warning("Channel logo fetch failed (badge skipped): %s", exc)
        return None


def get_round_logo(size: int = 160) -> Optional[Path]:
    """
    Return a circular-cropped RGBA version of the channel logo at the given
    pixel size, cached per size. None if the logo can't be produced.
    """
    cached = BRANDING_DIR / f"channel_logo_round_{size}.png"
    if cached.exists() and cached.stat().st_size > 512:
        return cached

    source = ensure_channel_logo()
    if source is None:
        return None

    try:
        from PIL import Image, ImageDraw, ImageOps

        img = Image.open(source).convert("RGBA")
        img = ImageOps.fit(img, (size, size), method=Image.LANCZOS)

        # Circular alpha mask (anti-aliased: draw 4x then downscale)
        big = size * 4
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, big, big), fill=255)
        mask = mask.resize((size, size), Image.LANCZOS)
        img.putalpha(mask)

        # Subtle white ring for contrast on any background
        ring = ImageDraw.Draw(img)
        ring.ellipse((1, 1, size - 2, size - 2), outline=(255, 255, 255, 200), width=max(2, size // 60))

        BRANDING_DIR.mkdir(parents=True, exist_ok=True)
        img.save(cached)
        logger.debug("Round logo rendered → %s", cached.name)
        return cached
    except Exception as exc:
        logger.warning("Round logo render failed (badge skipped): %s", exc)
        return None
