"""
Thumbnail generator — creates branded 1280×720 YouTube thumbnails using Pillow.
Supports sentiment-based color schemes and dynamic layout templates.
"""
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Optional

from config.settings import settings
from monitor.usage_ledger import record

logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir / "thumbnails"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

THUMB_W, THUMB_H = 1280, 720
FONTS_DIR = settings.assets_dir / "fonts"


@dataclass
class ThumbnailSpec:
    headline: str
    subtext: str
    ticker: Optional[str]
    emoji: str
    sentiment: str   # "bullish" | "bearish" | "neutral" | "warning"
    chart_path: Optional[Path]
    logo_path: Optional[Path]
    # The number the thumbnail is built around. At the size a thumbnail is
    # actually seen there is room for exactly one idea, and this is it.
    key_stat: str = ""


@dataclass
class ThumbnailFile:
    path: Path
    spec: ThumbnailSpec
    generated_at: str


# ── Color Palettes by Sentiment ────────────────────────────────────────────
# Bright enough to survive a feed. The previous palettes bottomed out at
# (5,5,15) — at 336px wide in a row of competing thumbnails, near-black
# reads as an empty slot rather than as a video.
SENTIMENT_PALETTES = {
    "bullish": {
        "bg_top": (0, 122, 51), "bg_bot": (4, 30, 16),
        "headline": (180, 255, 200), "accent": (0, 255, 128),
        "glow": (0, 200, 80), "number": (120, 255, 170),
    },
    "bearish": {
        "bg_top": (150, 20, 34), "bg_bot": (32, 4, 8),
        "headline": (255, 210, 215), "accent": (255, 70, 90),
        "glow": (200, 20, 40), "number": (255, 130, 140),
    },
    "neutral": {
        "bg_top": (22, 52, 130), "bg_bot": (6, 10, 30),
        "headline": (215, 230, 255), "accent": (120, 180, 255),
        "glow": (60, 120, 220), "number": (150, 200, 255),
    },
    "warning": {
        "bg_top": (150, 100, 0), "bg_bot": (30, 20, 2),
        "headline": (255, 240, 200), "accent": (255, 190, 0),
        "glow": (200, 160, 0), "number": (255, 210, 90),
    },
}


def _get_font(size: int, bold: bool = False):
    """Load font with fallback to default."""
    try:
        from PIL import ImageFont
        font_name = "Impact.ttf" if bold else "DejaVuSans-Bold.ttf"
        font_path = FONTS_DIR / font_name
        if font_path.exists():
            return ImageFont.truetype(str(font_path), size)
        # Try system fonts
        system_paths = [
            f"/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            f"/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            f"/System/Library/Fonts/Helvetica.ttc",
        ]
        for sp in system_paths:
            if Path(sp).exists():
                return ImageFont.truetype(sp, size)
    except Exception:
        pass
    try:
        from PIL import ImageFont
        return ImageFont.load_default()
    except Exception:
        return None


def _draw_gradient_bg(draw, width: int, height: int, palette: dict) -> None:
    """Draw vertical gradient background."""
    top = palette["bg_top"]
    bot = palette["bg_bot"]
    for y in range(height):
        ratio = y / height
        r = int(top[0] + (bot[0] - top[0]) * ratio)
        g = int(top[1] + (bot[1] - top[1]) * ratio)
        b = int(top[2] + (bot[2] - top[2]) * ratio)
        draw.line([(0, y), (width, y)], fill=(r, g, b))


def _draw_text_with_shadow(draw, text: str, xy: tuple, font, fill: tuple,
                           shadow_offset: int = 3, shadow_color: tuple = (0, 0, 0, 180)) -> None:
    x, y = xy
    draw.text((x + shadow_offset, y + shadow_offset), text, font=font, fill=shadow_color)
    draw.text((x, y), text, font=font, fill=fill)


def _draw_arrow(draw, box, colour, up: bool) -> None:
    """
    A direction arrow drawn as a polygon.

    Not an emoji: DejaVu carries no colour emoji glyph, so the old design
    rendered a tofu box in the most prominent position on the thumbnail. A
    polygon always draws, at any size, on any machine.
    """
    x0, y0, x1, y1 = box
    mid = (x0 + x1) / 2
    if up:
        draw.polygon([(mid, y0), (x1, y1), (x0, y1)], fill=colour)
    else:
        draw.polygon([(mid, y1), (x1, y0), (x0, y0)], fill=colour)


def _fit_font(draw, text: str, max_width: int, start_px: int, floor_px: int,
              bold: bool = True):
    """Largest font that keeps text inside max_width."""
    size = start_px
    while size > floor_px:
        font = _get_font(size, bold=bold)
        if font is None:
            return None
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size = int(size * 0.92)
    return _get_font(floor_px, bold=bold)


def generate_thumbnail(
    spec: ThumbnailSpec,
    chart_path: Optional[Path] = None,
) -> ThumbnailFile:
    """
    Generate a branded thumbnail built around one number.

    Designed for the size a thumbnail is actually seen at — roughly 336px
    wide in a feed, where the previous layout became an unreadable smudge:
    a wrapped headline in body-sized type, a subtext line, a ticker chip and
    a watermark all competing, over a near-black background, with a tofu box
    where the emoji should have been.

    There is room for one idea. The number is that idea.
    """
    try:
        from PIL import Image, ImageDraw, ImageFilter
    except ImportError:
        logger.error("Pillow not installed — cannot generate thumbnails")
        raise

    palette = SENTIMENT_PALETTES.get(spec.sentiment, SENTIMENT_PALETTES["neutral"])
    img = Image.new("RGB", (THUMB_W, THUMB_H), palette["bg_bot"])
    draw = ImageDraw.Draw(img, "RGBA")
    _draw_gradient_bg(draw, THUMB_W, THUMB_H, palette)

    # Direction comes from the number itself where there is one. Deriving it
    # from sentiment alone drew a down arrow over "+0.26%", because anything
    # not classified bullish fell to the else branch.
    stat_raw = (spec.key_stat or spec.subtext or "").strip()
    if stat_raw.startswith("-") or stat_raw.startswith("−"):
        up = False
    elif stat_raw.startswith("+"):
        up = True
    else:
        up = spec.sentiment == "bullish"

    # A large, soft arrow behind everything — direction readable before any
    # text resolves, which at feed size is most of the impression.
    arrow_w = int(THUMB_W * 0.42)
    arrow_box = (THUMB_W - arrow_w - 40, int(THUMB_H * 0.12),
                 THUMB_W - 40, int(THUMB_H * 0.88))
    glow = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    _draw_arrow(ImageDraw.Draw(glow), arrow_box, palette["accent"] + (70,), up)
    img = Image.alpha_composite(img.convert("RGBA"),
                                glow.filter(ImageFilter.GaussianBlur(6))).convert("RGB")
    draw = ImageDraw.Draw(img, "RGBA")

    left = int(THUMB_W * 0.055)
    text_width = int(THUMB_W * 0.60)

    # Label: what the number refers to.
    label = (spec.ticker or spec.headline).upper()[:22]
    label_font = _get_font(52, bold=True)
    if label_font:
        draw.text((left, int(THUMB_H * 0.13)), label,
                  font=label_font, fill=palette["headline"])

    # The number, as large as it will go.
    stat = (spec.key_stat or spec.subtext or "").strip()[:12]
    if stat:
        stat_font = _fit_font(draw, stat, text_width, int(THUMB_H * 0.42), 90)
        if stat_font:
            box = draw.textbbox((0, 0), stat, font=stat_font)
            y = int(THUMB_H * 0.30)
            # Heavy shadow: these sit on a coloured field, not a flat one.
            draw.text((left + 6, y + 6 - box[1]), stat, font=stat_font,
                      fill=(0, 0, 0, 160))
            draw.text((left, y - box[1]), stat, font=stat_font,
                      fill=palette["number"])

    # Two or three words of context, no more — and never the number again.
    # "-0.55%" above "S&P 500 FALLS 0.55%" spends the whole thumbnail
    # saying one thing twice.
    words = [w for w in spec.headline.split()
             if not any(ch.isdigit() for ch in w)]
    kicker = " ".join(words[:4]).upper()
    kicker_font = _fit_font(draw, kicker, text_width, 76, 40)
    if kicker_font:
        draw.text((left, int(THUMB_H * 0.76)), kicker,
                  font=kicker_font, fill=(255, 255, 255))

    # Accent rule anchoring the left column.
    draw.rectangle([(0, 0), (14, THUMB_H)], fill=palette["accent"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = OUTPUT_DIR / f"thumbnail_{spec.sentiment}_{timestamp}.jpg"
    img.save(path, "JPEG", quality=95, optimize=True)
    logger.info("Thumbnail: %s — %s (%s)", path.name, stat or "no stat",
                spec.sentiment)
    return ThumbnailFile(path=path, spec=spec,
                         generated_at=datetime.now().isoformat())



_THUMBNAIL_COPY_PROMPT = """\
Generate thumbnail copy for a DriftWire326 YouTube video.

TITLE: {title}
KEY STAT: {key_stat}
SENTIMENT: {sentiment}

Rules — three visual elements ONLY (headline, the big stat, ticker):
- headline: max 3 words ALL CAPS, factual — match intensity to magnitude.
  Moves under 3% must NOT say CRASH / WRECKED / MELTDOWN / CARNAGE —
  use SELL-OFF / SLIDE / TECH DROPS instead. YouTube penalizes
  thumbnails that overpromise vs. the actual content.
- subtext: max 4 words, must include the key stat number itself.
- No paragraph text, no more than one number besides the key stat.
- emoji relevant to market sentiment.

Return ONLY valid JSON:
{{"headline": "<3-word ALL CAPS>", "subtext": "<max 4 words>", "ticker": "<symbol or null>", "emoji": "<single emoji>"}}"""

_TIER_SENTIMENT = {
    "tier1": "warning",    # breakout — urgent orange/yellow
    "tier2": "neutral",    # notable — blue
    "tier3": "neutral",    # routine — blue
}

_SUNDAY_SENTIMENT = {
    "investment_banking": "bullish",
    "insurance_protection": "neutral",
    "savings_wealth": "bullish",
    "rotating_bonus": "warning",
}


MAX_THUMB_BYTES = 2 * 1024 * 1024  # 2 MB YouTube limit


class ThumbnailGenerator:
    """Class-based thumbnail generation with tier + theme routing."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_weekday_thumbnail(
        self,
        title: str,
        key_stat: str,
        ticker: Optional[str] = None,
        tier: str = "tier2",
        chart_path: Optional[Path] = None,
        sentiment: Optional[str] = None,
    ) -> ThumbnailFile:
        """
        Generate a weekday thumbnail.
        Tier-aware: tier1 uses warning palette, tier2/3 use neutral/bullish by stat.
        """
        auto_sentiment = sentiment or _TIER_SENTIMENT.get(tier, "neutral")
        if key_stat and not sentiment:
            has_minus = "-" in key_stat or "fell" in title.lower() or "crash" in title.lower()
            auto_sentiment = "bearish" if has_minus else "bullish"

        spec = ThumbnailSpec(
            headline=self._make_headline(title),
            subtext=key_stat[:30] if key_stat else "Market Update",
            ticker=ticker,
            emoji="📉" if "bearish" in auto_sentiment else "📈",
            sentiment=auto_sentiment,
            chart_path=chart_path,
            logo_path=None,
        )
        thumb = generate_thumbnail(spec, chart_path)
        self.validate_file_size(thumb.path)
        return thumb

    def generate_sunday_thumbnail(
        self,
        title: str,
        theme: str = "investment_banking",
        chart_path: Optional[Path] = None,
    ) -> ThumbnailFile:
        """Generate a Sunday educational thumbnail with theme-based palette."""
        sent = _SUNDAY_SENTIMENT.get(theme, "bullish")
        spec = ThumbnailSpec(
            headline=self._make_headline(title),
            subtext="SUNDAY DEEP DIVE",
            ticker=None,
            emoji="🎓",
            sentiment=sent,
            chart_path=chart_path,
            logo_path=None,
        )
        thumb = generate_thumbnail(spec, chart_path)
        self.validate_file_size(thumb.path)
        return thumb

    def apply_brand_template(self, image_path: Path, sentiment: str = "neutral") -> Path:
        """
        Apply brand frame/overlay to an existing image.
        Returns the modified path (overwrites in-place).
        """
        try:
            from PIL import Image, ImageDraw
            img = Image.open(image_path).convert("RGB")
            draw = ImageDraw.Draw(img, "RGBA")
            palette = SENTIMENT_PALETTES.get(sentiment, SENTIMENT_PALETTES["neutral"])
            # Left accent bar
            draw.rectangle([(0, 0), (8, img.height)], fill=palette["accent"])
            # Bottom brand strip
            draw.rectangle(
                [(0, img.height - 40), (img.width, img.height)],
                fill=(0, 0, 0, 160),
            )
            self.add_channel_watermark(draw, img.width, img.height)
            img.save(image_path, "JPEG", quality=92)
        except Exception as exc:
            logger.error("apply_brand_template failed: %s", exc)
        return image_path

    def add_channel_watermark(
        self,
        draw,
        width: int,
        height: int,
        alpha: float = 0.15,
    ) -> None:
        """Draw @DriftWire326 watermark at 15% opacity (bottom-right)."""
        font = _get_font(26)
        if font:
            opacity = int(255 * alpha)
            draw.text(
                (width - 200, height - 36),
                "@DriftWire326",
                font=font,
                fill=(255, 255, 255, opacity),
            )

    def add_text_with_shadow(
        self,
        draw,
        text: str,
        xy: tuple,
        font,
        fill: tuple,
        shadow_offset: int = 3,
    ) -> None:
        """Draw text with black drop shadow."""
        _draw_text_with_shadow(draw, text, xy, font, fill, shadow_offset)

    def validate_file_size(self, path: Path) -> bool:
        """
        Check thumbnail is under 2 MB. Re-saves at lower quality if over.
        Returns True if within limit.
        """
        if not path.exists():
            return False
        size = path.stat().st_size
        if size <= MAX_THUMB_BYTES:
            return True
        # Re-compress
        try:
            from PIL import Image
            img = Image.open(path)
            for quality in (85, 70, 55):
                img.save(path, "JPEG", quality=quality, optimize=True)
                if path.stat().st_size <= MAX_THUMB_BYTES:
                    logger.info("Thumbnail re-compressed to %dKB (q=%d)", path.stat().st_size // 1024, quality)
                    return True
            logger.error("Thumbnail still over 2MB after re-compression: %s", path)
        except Exception as exc:
            logger.error("validate_file_size failed: %s", exc)
        return False

    def _make_headline(self, title: str) -> str:
        """Shorten title to ≤3 meaningful words for the thumbnail headline."""
        words = title.split()
        stop = {"the", "a", "an", "is", "are", "was", "were", "of", "in", "and", "or", "to"}
        meaningful = [w for w in words if w.lower() not in stop]
        return " ".join(meaningful[:3]).upper() or title[:20].upper()


def generate_thumbnail_from_claude(
    video_title: str,
    key_stat: str,
    sentiment: str,
    chart_path: Optional[Path] = None,
) -> ThumbnailFile:
    """Ask Claude for thumbnail copy, then generate the image."""
    import anthropic
    import json

    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = _THUMBNAIL_COPY_PROMPT.format(
        title=video_title, key_stat=key_stat, sentiment=sentiment,
    )
    try:
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=256,
            temperature=0.4,
            messages=[{"role": "user", "content": prompt}],
        )
        record(resp, "thumbnail_gen")
        raw = resp.content[0].text
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(json_match.group()) if json_match else {}
    except Exception as exc:
        logger.error("Claude thumbnail copy generation failed: %s", exc)
        data = {}

    spec = ThumbnailSpec(
        headline=data.get("headline", video_title[:30]),
        subtext=data.get("subtext", key_stat[:20]),
        ticker=data.get("ticker"),
        emoji=data.get("emoji", "📈" if sentiment == "bullish" else "📉"),
        sentiment=sentiment,
        chart_path=chart_path,
        logo_path=None,
    )
    return generate_thumbnail(spec, chart_path=chart_path)
