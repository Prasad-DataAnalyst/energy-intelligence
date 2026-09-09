"""
builders/shorts_cards.py — vertical cards for Shorts.

The previous cards were centred paragraphs on a flat colour field, held for
60/len(cards) seconds each. That is a slide deck rotated ninety degrees, and
it looks nothing like what performs in a vertical feed.

Modelled on how financial television builds its own Shorts: real imagery
behind, a caption band over it, and a number given the whole frame when
there is one. The imagery is the part that has to come from stock photos
rather than a camera crew, but the treatment — dim the picture, band the
text, make the figure enormous — is what actually carries the look.

Rendered with PIL and fed to the concat demuxer, the same path the
long-form beat system uses, rather than through drawtext or MoviePy text
clips. Full control over layout, and no filtergraph to outgrow.
"""
import logging
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

BG = (10, 10, 15)
TEXT = (255, 255, 255)
MUTED = (168, 176, 194)
GREEN = (0, 214, 118)
RED = (255, 82, 96)
AMBER = (255, 196, 0)

# How far to knock the photo back. Anything brighter and the caption stops
# being readable at a glance, which is the whole job of the caption.
PHOTO_DIM = 0.42

# The readable frame. Below SAFE_BOTTOM the Shorts player draws the title,
# channel row and action buttons over the video, so anything placed there is
# invisible in the app however good it looks in a still.
SAFE_TOP = 0.22
SAFE_BOTTOM = 0.72


def _font(px: int, bold: bool = True):
    from PIL import ImageFont
    name = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    try:
        return ImageFont.truetype(name, px)
    except Exception:
        return ImageFont.load_default()


def _size() -> tuple:
    return settings.shorts_width, settings.shorts_height


def _background(photo: Optional[Path], width: int, height: int, tint):
    """Photo cropped to fill and dimmed, or a graded colour field."""
    from PIL import Image, ImageEnhance, ImageDraw
    if photo is not None:
        try:
            img = Image.open(photo).convert("RGB")
            scale = max(width / img.width, height / img.height)
            img = img.resize((max(int(img.width * scale), width),
                              max(int(img.height * scale), height)), Image.LANCZOS)
            left = (img.width - width) // 2
            top = (img.height - height) // 3      # bias up: faces and skylines
            img = img.crop((left, top, left + width, top + height))
            return ImageEnhance.Brightness(img).enhance(PHOTO_DIM)
        except Exception as exc:
            logger.warning("Short background photo unusable (%s) — using colour", exc)

    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)
    for y in range(height):
        blend = y / height
        draw.line([(0, y), (width, y)],
                  fill=tuple(int(tint[i] * (1 - blend) + BG[i] * blend)
                             for i in range(3)))
    return img


def _wrap(draw, text: str, font, max_width: int) -> list:
    words, lines, line = text.split(), [], ""
    for word in words:
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def render_card(
    text: str,
    dest: Path,
    kind: str = "context",
    stat: str = "",
    photo: Optional[Path] = None,
    progress: float = 0.0,
) -> Path:
    """
    One vertical card.

    kind drives the treatment: "stat" gives the number the frame, everything
    else bands the text over the picture.
    """
    from PIL import Image, ImageDraw

    width, height = _size()
    up = stat.strip().startswith("+")
    accent = GREEN if up else RED if stat.strip().startswith("-") else AMBER
    tint = {"hook": (28, 16, 60), "stat": (6, 26, 16) if up else (34, 8, 12),
            "cta": (52, 0, 16)}.get(kind, (14, 20, 40))

    img = _background(photo, width, height, tint).convert("RGBA")
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    margin = int(width * 0.075)
    inner = width - margin * 2

    # Progress bar. Vertical feeds reward telling people how long this is.
    bar_h = max(int(height * 0.006), 6)
    draw.rectangle((0, 0, width, bar_h), fill=(255, 255, 255, 40))
    draw.rectangle((0, 0, int(width * max(0.0, min(progress, 1.0))), bar_h),
                   fill=accent + (255,))

    if kind == "stat" and stat:
        # Label above, number below. A one-word caption band under a huge
        # figure reads as an orphan; as a kicker above it, it is a label.
        label = text.strip().upper()[:24]
        label_font = _font(int(height * 0.030))
        draw.text((width // 2, int(height * 0.255)), label,
                  font=label_font, fill=MUTED + (255,), anchor="ma")

        size = int(height * 0.15)
        while size > int(height * 0.06):
            font = _font(size)
            if draw.textlength(stat, font=font) <= inner:
                break
            size = int(size * 0.9)
        font = _font(size)
        box = draw.textbbox((0, 0), stat, font=font)
        stat_y = int(height * 0.31)
        draw.text((width // 2 + 5, stat_y + 5), stat, font=font,
                  fill=(0, 0, 0, 170), anchor="ma")
        draw.text((width // 2, stat_y), stat, font=font, fill=accent, anchor="ma")

        arrow = int(width * 0.055)
        ax, ay = width // 2, stat_y - int(height * 0.055)
        if up:
            draw.polygon([(ax, ay - arrow), (ax + arrow, ay), (ax - arrow, ay)],
                         fill=accent + (255,))
        elif stat.strip().startswith("-"):
            draw.polygon([(ax, ay), (ax + arrow, ay - arrow), (ax - arrow, ay - arrow)],
                         fill=accent + (255,))
        lines = []          # the label above is the caption
        caption_top = 0
    else:
        # Caption band — a dark strip behind the words so they read over any
        # picture, which is the trick that makes footage-backed Shorts
        # legible.
        #
        # It is centred within the safe zone rather than pinned to a fixed
        # top. YouTube's Shorts chrome — title, channel, action buttons —
        # covers roughly the bottom fifth and the right edge, so SAFE_BOTTOM
        # is where the readable frame actually ends, and a short caption
        # placed at a fixed top leaves the whole lower half dead.
        body_font = _font(int(height * 0.044))
        lines = _wrap(draw, text.strip(), body_font, inner)[:5]
        line_h = int(height * 0.060)
        pad = int(height * 0.028)
        band_h = line_h * len(lines) + int(height * 0.05)
        safe_top, safe_bottom = int(height * SAFE_TOP), int(height * SAFE_BOTTOM)
        caption_top = max(safe_top, safe_top + (safe_bottom - safe_top - band_h) // 2)

        draw.rounded_rectangle((margin - 20, caption_top - pad,
                                width - margin + 20, caption_top + band_h),
                               radius=24, fill=(8, 8, 12, 210))
        draw.rectangle((margin - 20, caption_top - pad,
                        margin - 10, caption_top + band_h), fill=accent + (255,))
        y = caption_top
        for line in lines:
            draw.text((margin, y), line, font=body_font, fill=TEXT)
            y += line_h

    # Handle sits above the chrome, not under it.
    draw.text((width // 2, int(height * 0.775)), "@DriftWire326",
              font=_font(int(height * 0.026), bold=False),
              fill=MUTED + (210,), anchor="ma")

    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(img, layer).convert("RGB").save(dest)
    return dest


def build_card_sequence(cards: list, tmp_dir: Path, total_seconds: float,
                        photos: Optional[list] = None) -> list:
    """
    Render a Short as a sequence of cards.

    Returns [(image_path, seconds), ...] for the concat demuxer, or [] to
    tell the caller to fall back to whatever it did before. Each card is a
    dict: {"text": str, "kind": str, "stat": str}.

    Photos rotate rather than repeat, because the same picture on every card
    is the flat-colour problem again wearing a photograph.
    """
    if not cards or total_seconds <= 0:
        return []
    photos = [p for p in (photos or []) if p and Path(p).exists()]
    per_card = total_seconds / len(cards)
    sequence = []
    for index, card in enumerate(cards):
        try:
            photo = photos[index % len(photos)] if photos else None
            path = render_card(
                text=str(card.get("text", "")),
                dest=Path(tmp_dir) / f"short_{index:02d}.png",
                kind=str(card.get("kind", "context")),
                stat=str(card.get("stat", "")),
                photo=photo,
                progress=(index + 1) / len(cards),
            )
            sequence.append((path, per_card))
        except Exception as exc:
            logger.warning("Short card %d failed (non-fatal): %s", index, exc)
    if not sequence:
        return []
    logger.info("Short: %d cards over %.0fs (%.1fs each, %d photo backgrounds)",
                len(sequence), total_seconds, per_card, len(photos))
    return sequence
