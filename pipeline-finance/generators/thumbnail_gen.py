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
from config.prompts import THUMBNAIL_TEXT_PROMPT

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


@dataclass
class ThumbnailFile:
    path: Path
    spec: ThumbnailSpec
    generated_at: str


# ── Color Palettes by Sentiment ────────────────────────────────────────────
SENTIMENT_PALETTES = {
    "bullish": {
        "bg_top": (0, 80, 20),
        "bg_bot": (5, 5, 15),
        "headline": (0, 255, 120),
        "accent": (255, 215, 0),
        "glow": (0, 200, 80),
    },
    "bearish": {
        "bg_top": (80, 0, 10),
        "bg_bot": (5, 5, 15),
        "headline": (255, 30, 50),
        "accent": (255, 140, 0),
        "glow": (200, 20, 40),
    },
    "neutral": {
        "bg_top": (10, 20, 60),
        "bg_bot": (5, 5, 15),
        "headline": (100, 160, 255),
        "accent": (255, 215, 0),
        "glow": (60, 120, 220),
    },
    "warning": {
        "bg_top": (80, 50, 0),
        "bg_bot": (5, 5, 15),
        "headline": (255, 200, 0),
        "accent": (255, 80, 0),
        "glow": (200, 160, 0),
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


def generate_thumbnail(
    spec: ThumbnailSpec,
    chart_path: Optional[Path] = None,
) -> ThumbnailFile:
    """Generate a single branded thumbnail PNG."""
    try:
        from PIL import Image, ImageDraw, ImageFilter, ImageFont
    except ImportError:
        logger.error("Pillow not installed — cannot generate thumbnails")
        raise

    palette = SENTIMENT_PALETTES.get(spec.sentiment, SENTIMENT_PALETTES["neutral"])
    img = Image.new("RGB", (THUMB_W, THUMB_H), (0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")

    # Gradient background
    _draw_gradient_bg(draw, THUMB_W, THUMB_H, palette)

    # Subtle grid overlay for depth
    for x in range(0, THUMB_W, 80):
        draw.line([(x, 0), (x, THUMB_H)], fill=(255, 255, 255, 8), width=1)
    for y in range(0, THUMB_H, 80):
        draw.line([(0, y), (THUMB_W, y)], fill=(255, 255, 255, 8), width=1)

    # Chart embed (right side, 40% width)
    if chart_path and chart_path.exists():
        try:
            chart_img = Image.open(chart_path).convert("RGBA")
            chart_w = int(THUMB_W * 0.45)
            chart_h = int(THUMB_H * 0.7)
            chart_img = chart_img.resize((chart_w, chart_h), Image.LANCZOS)
            # Apply slight transparency
            chart_img.putalpha(180)
            img.paste(chart_img, (THUMB_W - chart_w - 20, (THUMB_H - chart_h) // 2), chart_img)
        except Exception as exc:
            logger.warning("Could not embed chart in thumbnail: %s", exc)

    # Accent bar on left
    draw.rectangle([(0, 0), (8, THUMB_H)], fill=palette["accent"])

    # Emoji (large, left-center)
    emoji_font = _get_font(140, bold=True)
    if emoji_font:
        try:
            draw.text((40, 80), spec.emoji, font=emoji_font, fill=(255, 255, 255))
        except Exception:
            draw.text((40, 80), "📈", font=_get_font(100), fill=(255, 255, 255))

    # Headline (main text — bold, large)
    headline_font = _get_font(88, bold=True)
    if headline_font:
        headline_text = spec.headline.upper()
        # Word wrap at ~14 chars per line
        words = headline_text.split()
        lines, current = [], ""
        for word in words:
            if len(current + " " + word) > 14 and current:
                lines.append(current)
                current = word
            else:
                current = (current + " " + word).strip()
        if current:
            lines.append(current)

        y_start = 230 if len(lines) <= 2 else 180
        for i, line in enumerate(lines[:3]):
            _draw_text_with_shadow(
                draw, line, (40, y_start + i * 95),
                headline_font, palette["headline"], shadow_offset=4,
            )

    # Subtext
    sub_font = _get_font(42)
    if sub_font and spec.subtext:
        _draw_text_with_shadow(
            draw, spec.subtext.upper(), (40, THUMB_H - 130),
            sub_font, (255, 255, 255), shadow_offset=2,
        )

    # Ticker badge (bottom-right area)
    if spec.ticker:
        ticker_font = _get_font(52, bold=True)
        badge_x, badge_y = 40, THUMB_H - 80
        badge_w, badge_h = 180, 60
        draw.rounded_rectangle(
            [(badge_x, badge_y), (badge_x + badge_w, badge_y + badge_h)],
            radius=10, fill=palette["accent"],
        )
        if ticker_font:
            draw.text(
                (badge_x + 15, badge_y + 8), f"${spec.ticker}",
                font=ticker_font, fill=(0, 0, 0),
            )

    # Channel watermark (bottom right)
    wm_font = _get_font(26)
    if wm_font:
        draw.text((THUMB_W - 200, THUMB_H - 40), "@DriftWire326",
                  font=wm_font, fill=(255, 255, 255, 150))

    # Glow effect on text area
    glow_layer = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow_layer)
    glow_color = (*palette["glow"], 40)
    glow_draw.ellipse([(0, 150), (600, 650)], fill=glow_color)
    glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(radius=60))
    img = Image.alpha_composite(img.convert("RGBA"), glow_layer).convert("RGB")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"thumbnail_{spec.sentiment}_{timestamp}.jpg"
    path = OUTPUT_DIR / filename
    img.save(path, "JPEG", quality=95, optimize=True)

    logger.info("Thumbnail generated → %s", path)
    return ThumbnailFile(path=path, spec=spec, generated_at=datetime.now().isoformat())


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
    prompt = THUMBNAIL_TEXT_PROMPT.substitute(
        title=video_title, key_stat=key_stat, sentiment=sentiment,
    )
    try:
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=256,
            temperature=0.4,
            messages=[{"role": "user", "content": prompt}],
        )
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
