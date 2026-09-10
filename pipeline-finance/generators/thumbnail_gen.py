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
    # Which show this is: "MARKET CLOSE", "PRE-MARKET", "SUNDAY DEEP DIVE".
    # Same slot, same weight, every video — it is what lets a returning
    # viewer recognise the channel before reading a word of the headline.
    series: str = ""


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


def _photo_background(palette: dict):
    """
    A photograph behind the type, with a scrim over the column that carries
    it.

    A gradient field is the safe choice and it is why every thumbnail on the
    channel looks like every other one. A picture varies by itself, every
    day, without any per-video design work — but only if the left column
    stays dark enough to read white type against, hence the scrim rather
    than a flat dim.
    """
    from PIL import Image, ImageEnhance
    from builders.broll_fetcher import cached_photos

    photos = cached_photos(8)
    if not photos:
        return None
    # Rotate by day so consecutive videos do not share a picture.
    photo = photos[datetime.now().timetuple().tm_yday % len(photos)]
    try:
        shot = Image.open(photo).convert("RGB")
    except Exception as exc:
        logger.warning("Thumbnail photo unusable (%s) — keeping gradient", exc)
        return None

    scale = max(THUMB_W / shot.width, THUMB_H / shot.height)
    shot = shot.resize((max(int(shot.width * scale), THUMB_W),
                        max(int(shot.height * scale), THUMB_H)), Image.LANCZOS)
    left = (shot.width - THUMB_W) // 2
    shot = shot.crop((left, 0, left + THUMB_W, THUMB_H))
    shot = ImageEnhance.Brightness(shot).enhance(0.55)

    # Horizontal scrim: opaque brand colour on the left, clear on the right
    # so the picture is still visibly a picture.
    scrim = Image.new("L", (THUMB_W, 1))
    for x in range(THUMB_W):
        edge = x / THUMB_W
        scrim.putpixel((x, 0), int(235 * max(0.0, 1.0 - (edge / 0.72) ** 1.6)))
    scrim = scrim.resize((THUMB_W, THUMB_H))
    field = Image.new("RGB", (THUMB_W, THUMB_H), palette["bg_bot"])
    return Image.composite(field, shot, scrim)


def _draw_series_badge(draw, series: str, palette: dict) -> int:
    """
    The show's name, in the same corner at the same weight on every video.

    This is the one element that must not vary. The channels that build an
    audience on thumbnails alone — "IBD Explains", "FAANG Stock Show" — do
    it by stamping the series, not by redesigning each upload. Returns the
    y coordinate the rest of the layout should start below.
    """
    top = int(THUMB_H * 0.055)
    if not series:
        return top

    text = series.upper()[:22]
    font = _get_font(34, bold=True)
    if font is None:
        return top
    left = int(THUMB_W * 0.055)
    box = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = 18, 12
    draw.rectangle(
        [(left - pad_x, top - pad_y),
         (left + box[2] - box[0] + pad_x, top + box[3] + pad_y)],
        fill=palette["accent"],
    )
    draw.text((left, top - box[1]), text, font=font, fill=(8, 8, 12))
    return top + box[3] + pad_y


def _hero_number(spec: "ThumbnailSpec") -> str:
    """
    The one figure the thumbnail is built around.

    spec.key_stat is authoritative. The fallback matters because for most of
    this channel's life nothing set it: the copy model's subtext came
    through instead and was truncated to twelve characters, so "S&P falls
    0.55%" rendered as "S&P falls 0." — a broken number in the largest type
    on the image. Pull the figure out of the sentence rather than cutting it.
    """
    explicit = (spec.key_stat or "").strip()
    if explicit:
        return explicit[:12]
    match = re.search(r"[+-−]?\$?\d[\d,]*(?:\.\d+)?%?", spec.subtext or "")
    return match.group()[:12] if match else ""


def _draw_title_block(draw, headline: str, left: int, top: int,
                      max_width: int, palette: dict) -> None:
    """A large two- or three-line title, for videos with no figure."""
    words = headline.upper().split()
    if not words:
        return
    lines, line = [], ""
    probe = _get_font(72, bold=True)
    for word in words:
        trial = f"{line} {word}".strip()
        if probe and draw.textbbox((0, 0), trial, font=probe)[2] > max_width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    if line:
        lines.append(line)
    lines = lines[:3]

    y = top
    for text in lines:
        font = _fit_font(draw, text, max_width, 96, 44)
        if font is None:
            return
        box = draw.textbbox((0, 0), text, font=font)
        draw.text((left + 5, y + 5 - box[1]), text, font=font, fill=(0, 0, 0, 160))
        draw.text((left, y - box[1]), text, font=font, fill=palette["headline"])
        y += box[3] - box[1] + int(THUMB_H * 0.035)


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
    img = _photo_background(palette)
    if img is None:
        img = Image.new("RGB", (THUMB_W, THUMB_H), palette["bg_bot"])
        _draw_gradient_bg(ImageDraw.Draw(img, "RGBA"), THUMB_W, THUMB_H, palette)
    draw = ImageDraw.Draw(img, "RGBA")

    # Direction comes from the number itself where there is one. Deriving it
    # from sentiment alone drew a down arrow over "+0.26%", because anything
    # not classified bullish fell to the else branch.
    stat_raw = _hero_number(spec)
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

    # The series badge is the fixed point of the whole layout; everything
    # else starts below whatever height it took.
    cursor = _draw_series_badge(draw, spec.series, palette) + int(THUMB_H * 0.04)

    # Label: the ticker, and only the ticker. It used to fall back to the
    # headline, which is also the kicker at the bottom of the frame — so
    # every thumbnail without a ticker printed the same three words twice
    # and spent two of its three elements saying one thing.
    label = (spec.ticker or "").upper()[:22]
    label_font = _get_font(52, bold=True)
    if label and label_font:
        draw.text((left, cursor), label, font=label_font,
                  fill=palette["headline"])
        cursor += int(THUMB_H * 0.11)

    stat = stat_raw
    if stat:
        # The number, as large as it will go.
        stat_font = _fit_font(draw, stat, text_width, int(THUMB_H * 0.42), 90)
        if stat_font:
            box = draw.textbbox((0, 0), stat, font=stat_font)
            # Heavy shadow: these sit on a photograph, not a flat field.
            draw.text((left + 6, cursor + 6 - box[1]), stat, font=stat_font,
                      fill=(0, 0, 0, 160))
            draw.text((left, cursor - box[1]), stat, font=stat_font,
                      fill=palette["number"])

        # Two or three words of context, no more — and never the number
        # again. "-0.55%" above "S&P 500 FALLS 0.55%" spends the whole
        # thumbnail saying one thing twice.
        words = [w for w in spec.headline.split()
                 if not any(ch.isdigit() for ch in w)]
        kicker = " ".join(words[:4]).upper()
        kicker_font = _fit_font(draw, kicker, text_width, 76, 40)
        if kicker_font:
            draw.text((left, int(THUMB_H * 0.76)), kicker,
                      font=kicker_font, fill=(255, 255, 255))
    else:
        # The educational videos have no figure to build around, and the
        # layout used to leave the middle of the frame empty and drop the
        # title to the bottom edge. With nothing to compete with, the title
        # IS the hero — set large across the frame, which is how the
        # channels that run title-led thumbnails do it.
        _draw_title_block(draw, spec.headline, left, cursor, text_width, palette)

    # Accent rule anchoring the left column.
    draw.rectangle([(0, 0), (14, THUMB_H)], fill=palette["accent"])

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    slug = re.sub(r"[^a-z0-9]+", "_", (spec.series or spec.sentiment).lower()).strip("_")
    # Second resolution is not enough: two thumbnails made in the same second
    # wrote to the same name, and the second one silently replaced the first.
    path = OUTPUT_DIR / f"thumbnail_{slug}_{timestamp}.jpg"
    suffix = 2
    while path.exists():
        path = OUTPUT_DIR / f"thumbnail_{slug}_{timestamp}_{suffix}.jpg"
        suffix += 1
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


# The channel's shows. These strings are the brand: they go in the same
# corner at the same weight on every upload, and changing one breaks the
# recognition it exists to build, so they are named here rather than passed
# as ad-hoc literals from each scheduler.
SERIES_PREMARKET = "PRE-MARKET"
SERIES_MARKET_CLOSE = "MARKET CLOSE"
SERIES_SUNDAY = "SUNDAY DEEP DIVE"

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
        series: str = "",
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
            key_stat=key_stat,
            series=series or SERIES_MARKET_CLOSE,
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
            series=SERIES_SUNDAY,
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


def generate_thumbnail_safe(
    video_title: str,
    key_stat: str,
    sentiment: str,
    chart_path: Optional[Path] = None,
    series: str = "",
) -> Optional[ThumbnailFile]:
    """
    A thumbnail, or None — but never an exception.

    The pipeline called generate_thumbnail_from_claude unguarded, so any
    failure in copy generation or image rendering took the whole video with
    it. That trade is backwards: a thumbnail is decoration and YouTube picks
    a frame when one is missing, while a lost video is a lost publishing
    slot that cannot be recovered.

    Degrades in two steps — Claude copy plus artwork, then artwork alone
    from the title and the figure the caller already has.
    """
    try:
        return generate_thumbnail_from_claude(
            video_title=video_title, key_stat=key_stat, sentiment=sentiment,
            chart_path=chart_path, series=series,
        )
    except Exception as exc:
        logger.error("Thumbnail generation failed (%s) — retrying without Claude", exc)

    try:
        words = [w for w in video_title.split() if not any(c.isdigit() for c in w)]
        return generate_thumbnail(ThumbnailSpec(
            headline=" ".join(words[:4]) or video_title[:30],
            subtext=key_stat, ticker=None, emoji="",
            sentiment=sentiment, chart_path=chart_path, logo_path=None,
            key_stat=key_stat, series=series,
        ))
    except Exception as exc:
        logger.error("Fallback thumbnail also failed (%s) — publishing without one "
                     "so the video is not lost", exc)
        return None


def generate_thumbnail_from_claude(
    video_title: str,
    key_stat: str,
    sentiment: str,
    chart_path: Optional[Path] = None,
    series: str = "",
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
        # The caller's figure, not the copy model's sentence. Leaving this
        # empty is what put "S&P falls 0." in the largest type on the image.
        key_stat=key_stat,
        series=series,
    )
    return generate_thumbnail(spec, chart_path=chart_path)
