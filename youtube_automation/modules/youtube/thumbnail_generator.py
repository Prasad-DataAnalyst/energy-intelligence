"""
thumbnail_generator.py
Generate a 1280×720 thumbnail via SDXL (Replicate) → PIL fallback, then upload.
"""

import io
import logging
import os
import textwrap
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import config
import youtube_uploader as uploader

log = logging.getLogger(__name__)

# Thumbnail specs
_W, _H = 1280, 720
_FONT_SIZE = 72


def _generate_sdxl(prompt: str, out_path: str) -> bool:
    """Try Replicate SDXL to generate a 1280×720 image. Returns True on success."""
    try:
        import replicate
        api_key = os.environ.get("REPLICATE_API_TOKEN")
        if not api_key:
            return False
        client = replicate.Client(api_token=api_key)
        output = client.run(
            "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b",
            input={
                "prompt":          prompt,
                "width":           _W,
                "height":          _H,
                "num_outputs":     1,
                "guidance_scale":  7.5,
                "num_inference_steps": 30,
            },
        )
        url = output[0] if isinstance(output, list) else str(output)
        import urllib.request
        urllib.request.urlretrieve(url, out_path)
        log.info("SDXL thumbnail generated: %s", out_path)
        return True
    except Exception as exc:
        log.warning("SDXL thumbnail failed: %s", exc)
        return False


def _generate_pil(title: str, out_path: str, variant: int = 0) -> str:
    """PIL fallback: gradient background + bold title text."""
    from PIL import Image, ImageDraw, ImageFont

    palettes = [
        ((15, 23, 42),  (56, 189, 248)),   # dark blue → cyan
        ((15, 10, 40),  (168, 85, 247)),    # dark purple → violet
        ((10, 35, 20),  (52, 211, 153)),    # dark green → emerald
        ((40, 10, 10),  (251, 113, 133)),   # dark red → rose
    ]
    bg_col, accent = palettes[variant % len(palettes)]

    img = Image.new("RGB", (_W, _H))
    draw = ImageDraw.Draw(img)

    # Gradient
    for y in range(_H):
        t = y / _H
        r = int(bg_col[0] + (accent[0] - bg_col[0]) * t * 0.35)
        g = int(bg_col[1] + (accent[1] - bg_col[1]) * t * 0.35)
        b = int(bg_col[2] + (accent[2] - bg_col[2]) * t * 0.35)
        draw.line([(0, y), (_W, y)], fill=(r, g, b))

    # Accent bar
    draw.rectangle([(0, _H - 8), (_W, _H)], fill=accent)

    # Title text (wrapped)
    try:
        font_path = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if not Path(font_path).exists():
            font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        font = ImageFont.truetype(font_path, _FONT_SIZE)
        small_font = ImageFont.truetype(font_path, 32)
    except Exception:
        font = ImageFont.load_default()
        small_font = font

    lines = textwrap.wrap(title.upper(), width=24)
    total_h = len(lines) * (_FONT_SIZE + 12)
    y0 = (_H - total_h) // 2 - 20

    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        tw = bbox[2] - bbox[0]
        x0 = (_W - tw) // 2
        # Shadow
        draw.text((x0 + 3, y0 + 3), line, font=font, fill=(0, 0, 0, 180))
        draw.text((x0, y0), line, font=font, fill=(255, 255, 255))
        y0 += _FONT_SIZE + 12

    # Channel name
    channel = "GetMindFuelNow"
    cb = draw.textbbox((0, 0), channel, font=small_font)
    draw.text((_W - cb[2] - 24, _H - 52), channel, font=small_font, fill=accent)

    img.save(out_path, "JPEG", quality=95)
    log.info("PIL thumbnail generated: %s", out_path)
    return out_path


def generate_thumbnail(
    title: str,
    out_path: str,
    sdxl_prompt: Optional[str] = None,
    variant: int = 0,
) -> str:
    """
    Generate a thumbnail image. Returns out_path on success.
    Priority: SDXL → PIL fallback.
    """
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)

    if sdxl_prompt:
        if _generate_sdxl(sdxl_prompt, out_path):
            return out_path

    return _generate_pil(title, out_path, variant)


def upload_thumbnail_to_video(video_id: str, image_path: str) -> bool:
    """Upload image_path as the thumbnail for video_id. Returns True on success."""
    try:
        uploader.upload_thumbnail(video_id, image_path)
        log.info("Thumbnail uploaded to video %s from %s", video_id, image_path)
        return True
    except Exception as exc:
        log.error("Thumbnail upload failed for %s: %s", video_id, exc)
        return False


def generate_and_upload(
    video_id: str,
    title: str,
    seo_package: Optional[dict] = None,
    work_dir: Optional[str] = None,
    num_variants: int = 3,
) -> bool:
    """
    Generate up to num_variants thumbnail candidates, pick the best-named one
    (first successful), upload it, and return True on success.
    """
    seo_package = seo_package or {}
    work_dir = work_dir or str(Path(config.OUTPUT_DIR) / "thumbnails")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    visual_style = seo_package.get("visual_style", "")
    keywords     = seo_package.get("keywords",     [])
    keyword_str  = ", ".join(keywords[:5]) if keywords else title

    uploaded = False
    for v in range(num_variants):
        out_path = str(Path(work_dir) / f"thumb_v{v}.jpg")
        sdxl_prompt = (
            f"Cinematic YouTube thumbnail, {visual_style}, "
            f"bold text overlay '{title[:40]}', "
            f"dramatic lighting, ultra HD, professional photography, "
            f"{keyword_str}, vibrant colors, 16:9 ratio"
        ) if visual_style else None

        try:
            generate_thumbnail(title, out_path, sdxl_prompt, variant=v)
            if upload_thumbnail_to_video(video_id, out_path):
                uploaded = True
                break           # first successful upload wins
        except Exception as exc:
            log.warning("Thumbnail variant %d failed: %s", v, exc)
        time.sleep(1)

    return uploaded
