"""
Phase 6 — Thumbnail Generator
================================
Produces 3 A/B-testable thumbnail variants using Pillow:
  v1: High-contrast dark + accent colour + bold title
  v2: Warm gradient + smaller subtitle text
  v3: Split layout — bold stat/number left, text right

Each thumbnail is 1280×720 (YouTube 16:9 recommended).
"""
import logging
import math
import os
import textwrap
from typing import List, Tuple

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance

from utils.models import TrendResult, Script

log = logging.getLogger("viral_pipeline.thumbnails")

TH_W, TH_H = 1280, 720

VARIANTS = [
    {
        "name": "contrast",
        "bg":     [10, 10, 40],
        "bg2":    [25, 5, 60],
        "acc":    [255, 215, 0],
        "text":   [255, 255, 255],
        "layout": "full",
    },
    {
        "name": "warm",
        "bg":     [40, 15, 5],
        "bg2":    [70, 30, 8],
        "acc":    [255, 107, 53],
        "text":   [255, 240, 220],
        "layout": "full",
    },
    {
        "name": "epic",
        "bg":     [5, 20, 35],
        "bg2":    [8, 35, 65],
        "acc":    [0, 212, 255],
        "text":   [200, 240, 255],
        "layout": "split",
    },
]


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
              if bold else
              ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _ease(t: float) -> float:
    t = max(0.0, min(t, 1.0))
    return t * t * (3 - 2 * t)


def _gradient_bg(palette: dict) -> Image.Image:
    img  = Image.new("RGB", (TH_W, TH_H))
    draw = ImageDraw.Draw(img)
    bg, bg2 = palette["bg"], palette["bg2"]

    # Diagonal gradient
    for y in range(TH_H):
        for x in range(TH_W):
            r = (x / TH_W * 0.5 + y / TH_H * 0.5)
            c = tuple(int(bg[i] + (bg2[i] - bg[i]) * r) for i in range(3))
            draw.point((x, y), fill=c)
    return img


def _fast_gradient_bg(palette: dict) -> Image.Image:
    """Faster gradient using numpy."""
    bg  = np.array(palette["bg"],  dtype=np.float32)
    bg2 = np.array(palette["bg2"], dtype=np.float32)
    ys  = np.linspace(0, 1, TH_H).reshape(TH_H, 1)
    xs  = np.linspace(0, 1, TH_W).reshape(1, TH_W)
    blend = (ys * 0.5 + xs * 0.5)
    arr   = (bg + (bg2 - bg) * blend[..., np.newaxis]).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _glow_circle(draw: ImageDraw.ImageDraw, cx: int, cy: int,
                 radius: int, colour: Tuple[int, int, int]) -> None:
    for step in range(30, 0, -3):
        alpha = int(15 * (step / 30))
        r     = int(radius * step / 30)
        draw.ellipse(
            [cx - r, cy - r, cx + r, cy + r],
            fill=(*colour, alpha),
        )


def _add_grain(img: Image.Image, strength: int = 8) -> Image.Image:
    arr    = np.array(img).astype(np.int16)
    noise  = np.random.normal(0, strength, arr.shape).astype(np.int16)
    result = np.clip(arr + noise, 0, 255).astype(np.uint8)
    return Image.fromarray(result)


def render_full_layout(img: Image.Image, title: str,
                       subtitle: str, palette: dict) -> Image.Image:
    """Full-width title overlay layout."""
    draw = ImageDraw.Draw(img, "RGBA")
    acc  = tuple(palette["acc"])
    txt  = tuple(palette["text"])

    # Bottom gradient bar for text legibility
    bar_h = 280
    for y in range(TH_H - bar_h, TH_H):
        alpha = int(200 * (y - (TH_H - bar_h)) / bar_h)
        draw.line([(0, y), (TH_W, y)], fill=(0, 0, 0, alpha))

    # Glow behind title position
    _glow_circle(draw, TH_W // 2, TH_H - 160, 280, acc)

    # Accent top stripe
    draw.rectangle([0, 0, TH_W, 10], fill=acc)

    # Title — wrapped at 32 chars
    wrapped = textwrap.fill(title.upper(), width=32)
    lines   = wrapped.split("\n")
    f_title = _font(min(80, 1200 // max(len(l) for l in lines)), bold=True)
    y0 = TH_H - 240 - len(lines) * 80

    for i, line in enumerate(lines):
        # Shadow
        draw.text((TH_W // 2 + 3, y0 + i * 82 + 3), line,
                  font=f_title, fill=(0, 0, 0, 200), anchor="mm")
        draw.text((TH_W // 2, y0 + i * 82), line,
                  font=f_title, fill=(*txt, 255), anchor="mm")

    # Accent underline
    tw = draw.textlength(lines[-1], font=f_title)
    uy = y0 + (len(lines) - 1) * 82 + 45
    draw.rectangle([TH_W//2 - tw//2, uy,
                    TH_W//2 + tw//2, uy + 5], fill=acc)

    # Subtitle
    if subtitle:
        f_sub = _font(36)
        draw.text((TH_W // 2, TH_H - 60), subtitle,
                  font=f_sub, fill=(*acc, 220), anchor="mm")

    # Channel tag
    f_sm = _font(28)
    draw.text((TH_W - 20, 20), "@GetMindFuelNow",
              font=f_sm, fill=(160, 160, 160), anchor="rt")

    return img


def render_split_layout(img: Image.Image, title: str,
                        stat: str, palette: dict) -> Image.Image:
    """Split layout: big number/stat left, title text right."""
    draw = ImageDraw.Draw(img, "RGBA")
    acc  = tuple(palette["acc"])
    txt  = tuple(palette["text"])

    # Vertical divider
    div_x = TH_W // 2 - 20
    draw.rectangle([div_x, 60, div_x + 4, TH_H - 60], fill=acc)

    # Left: big impactful number / stat
    big_stat = stat or title.split()[0].upper()[:8]
    f_big    = _font(min(180, 1600 // max(len(big_stat), 1)), bold=True)
    _glow_circle(draw, TH_W // 4, TH_H // 2, 200, acc)
    draw.text((TH_W // 4 + 4, TH_H // 2 + 4), big_stat,
              font=f_big, fill=(0, 0, 0, 200), anchor="mm")
    draw.text((TH_W // 4, TH_H // 2), big_stat,
              font=f_big, fill=acc, anchor="mm")

    # Right: title text
    right_x = TH_W * 3 // 4
    wrapped  = textwrap.fill(title, width=22).split("\n")
    f_title  = _font(54, bold=True)
    y0       = TH_H // 2 - len(wrapped) * 60 // 2
    for i, line in enumerate(wrapped):
        draw.text((right_x + 2, y0 + i * 64 + 2), line,
                  font=f_title, fill=(0, 0, 0, 180), anchor="mm")
        draw.text((right_x, y0 + i * 64), line,
                  font=f_title, fill=txt, anchor="mm")

    # Accent top / bottom bars
    draw.rectangle([0, 0, TH_W, 8], fill=acc)
    draw.rectangle([0, TH_H - 8, TH_W, TH_H], fill=acc)

    return img


def generate_thumbnail(title: str, subtitle: str, stat: str,
                        palette: dict, output_path: str) -> str:
    layout = palette.get("layout", "full")
    img    = _fast_gradient_bg(palette)

    if layout == "split":
        img = render_split_layout(img, title, stat, palette)
    else:
        img = render_full_layout(img, title, subtitle, palette)

    img = _add_grain(img, strength=5)
    img.save(output_path, quality=95, optimize=True)
    log.info(f"  Thumbnail: {output_path} ({os.path.getsize(output_path)//1024} KB)")
    return output_path


def run_phase6(trend: TrendResult, script: Script,
               output_dir: str) -> List[str]:
    """Generate 3 thumbnail variants. Returns list of PNG paths."""
    log.info("Phase 6: Generating thumbnails…")
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    # Extract a stat from talking points (first number found)
    import re
    stat = ""
    for point in trend.talking_points:
        m = re.search(r"\$?[\d,]+[%kKmMbBx]?", point)
        if m:
            stat = m.group()
            break

    subtitle = trend.target_audience[:50] if trend.target_audience else ""
    title    = script.title or trend.title

    for i, variant in enumerate(VARIANTS, 1):
        out_path = os.path.join(output_dir, f"thumbnail_v{i}.png")
        generate_thumbnail(title, subtitle, stat, variant, out_path)
        paths.append(out_path)

    return paths
