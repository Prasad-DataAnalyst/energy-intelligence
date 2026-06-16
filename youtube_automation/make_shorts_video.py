#!/usr/bin/env python3
"""
make_shorts_video.py
Creates YouTube Shorts (9:16 vertical) from horoscope asset JSON files.
100% free pipeline: edge-tts + MoviePy + Pillow + numpy.

Usage:
  python3 make_shorts_video.py scorpio_short_20260616.json
  python3 make_shorts_video.py --all 20260616
"""
import argparse
import asyncio
import glob
import json
import os
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import AudioFileClip, VideoClip

# ── Video constants ────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
FPS = 24
VOICE = "en-US-AriaNeural"          # Free Microsoft Edge TTS — no API key
CHANNEL_TAG = "GetMindFuelNow"

# ── Per-sign deep-space gradient (top RGB, bottom RGB) ────────────────────────
SIGN_GRADIENTS = {
    "aries":       ((30,  0,  0), (110, 20, 20)),
    "taurus":      ((0,  22,  0), ( 20, 70, 20)),
    "gemini":      ((22, 18,  0), ( 90, 75,  0)),
    "cancer":      ((10, 10, 28), ( 45, 45, 90)),
    "leo":         ((28, 12,  0), (110, 60,  0)),
    "virgo":       ((0,  22,  8), ( 30, 85, 45)),
    "libra":       ((16,  0, 28), ( 72, 32,105)),
    "scorpio":     ((22,  0,  0), ( 55,  0,  8)),
    "sagittarius": ((0,   9, 28), (  0, 38, 95)),
    "capricorn":   ((9,  11,  9), ( 32, 48, 32)),
    "aquarius":    ((0,  20, 20), (  0, 38, 80)),
    "pisces":      ((12,  0, 24), ( 48, 20, 85)),
}

SIGN_ACCENT = {
    "aries":       (255,  80,  80),
    "taurus":      ( 80, 210,  80),
    "gemini":      (255, 225,   0),
    "cancer":      (180, 180, 255),
    "leo":         (255, 165,   0),
    "virgo":       (120, 210, 120),
    "libra":       (210, 130, 255),
    "scorpio":     (255,  40,  40),
    "sagittarius": ( 80, 145, 255),
    "capricorn":   (145, 185, 145),
    "aquarius":    (  0, 225, 225),
    "pisces":      (165, 125, 255),
}

SIGN_EMOJIS = {
    "aries": "♈", "taurus": "♉", "gemini": "♊", "cancer": "♋",
    "leo": "♌", "virgo": "♍", "libra": "♎", "scorpio": "♏",
    "sagittarius": "♐", "capricorn": "♑", "aquarius": "♒", "pisces": "♓",
}

# ── Font cache ─────────────────────────────────────────────────────────────────
_FONT_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
]
_FONT_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
]
_font_cache: dict = {}


def get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _font_cache:
        for path in (_FONT_BOLD if bold else _FONT_REG):
            if os.path.exists(path):
                _font_cache[key] = ImageFont.truetype(path, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


# ── Background generation ──────────────────────────────────────────────────────
def make_gradient(sign: str) -> np.ndarray:
    """Pre-render vertical gradient background as numpy array."""
    top, bot = SIGN_GRADIENTS.get(sign, ((10, 10, 25), (25, 25, 60)))
    bg = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    ys = np.linspace(0, 1, HEIGHT)[:, None]
    for c in range(3):
        bg[:, :, c] = (top[c] * (1 - ys) + bot[c] * ys)
    return np.clip(bg, 0, 255).astype(np.uint8)


# ── Star field ─────────────────────────────────────────────────────────────────
def make_star_field(n: int = 260, seed: int = 0) -> tuple:
    """Return (xs, ys, phases, sizes) as numpy arrays for fast twinkling."""
    rng = np.random.RandomState(seed)
    xs = rng.randint(0, WIDTH, n)
    ys = rng.randint(0, HEIGHT, n)
    phases = rng.uniform(0, 1, n)
    sizes = rng.uniform(0.6, 2.8, n).astype(int).clip(1, 3)
    return xs, ys, phases, sizes


def draw_stars(frame: np.ndarray, xs, ys, phases, sizes, t: float) -> np.ndarray:
    """Fast per-frame star twinkling via numpy brightness variation."""
    brightness = (140 + 115 * np.abs(np.sin(np.pi * (t * 0.35 + phases)))).astype(np.uint8)
    out = frame.copy()
    for i in range(len(xs)):
        x, y, b, r = int(xs[i]), int(ys[i]), brightness[i], int(sizes[i])
        out[max(0, y-r):y+r+1, max(0, x-r):x+r+1] = b
    return out


# ── Text overlay helpers ───────────────────────────────────────────────────────
def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_px: int) -> list:
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if dummy.textbbox((0, 0), test, font=font)[2] > max_px and cur:
            lines.append(cur)
            cur = w
        else:
            cur = test
    if cur:
        lines.append(cur)
    return lines


def render_text_to_rgba(
    text: str, font: ImageFont.FreeTypeFont, fill: tuple,
    canvas_w: int, canvas_h: int, cx: int, cy: int,
    shadow: bool = True,
) -> np.ndarray:
    """Render centered text onto a transparent RGBA canvas."""
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    lines = wrap_text(text, font, canvas_w - 80)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    line_h = int(font.size * 1.2)
    total_h = len(lines) * line_h
    y = cy - total_h // 2
    for line in lines:
        bbox = dummy.textbbox((0, 0), line, font=font)
        x = cx - (bbox[2] - bbox[0]) // 2
        if shadow:
            draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=fill)
        y += line_h
    return np.array(img)


def blend_rgba(base: np.ndarray, overlay_rgba: np.ndarray, alpha_scale: float = 1.0) -> None:
    """In-place alpha blend RGBA overlay onto RGB base."""
    a = overlay_rgba[:, :, 3:4].astype(np.float32) / 255.0 * alpha_scale
    base[:] = np.clip(
        base.astype(np.float32) * (1 - a) + overlay_rgba[:, :, :3].astype(np.float32) * a,
        0, 255
    ).astype(np.uint8)


# ── Pre-render static overlays ─────────────────────────────────────────────────
def build_overlays(sign: str, hook_text: str) -> tuple:
    """Return (hook_overlay_rgba, base_overlay_rgba) as numpy arrays."""
    accent = SIGN_ACCENT.get(sign, (255, 255, 255))
    emoji = SIGN_EMOJIS.get(sign, "⭐")

    # Hook text overlay (shown first 4 seconds)
    hook_font = get_font(105, bold=True)
    hook_rgba = render_text_to_rgba(
        hook_text, hook_font, (*accent, 255),
        WIDTH, HEIGHT, WIDTH // 2, HEIGHT // 2,
    )

    # Base overlay: sign name at top + channel tag at bottom
    base_img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(base_img)

    sign_font = get_font(82, bold=True)
    sign_label = f"{emoji}  {sign.title()}  {emoji}"
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = dummy.textbbox((0, 0), sign_label, font=sign_font)
    sx = (WIDTH - (bbox[2] - bbox[0])) // 2
    draw.text((sx + 3, 103), sign_label, font=sign_font, fill=(0, 0, 0, 180))
    draw.text((sx, 100), sign_label, font=sign_font, fill=(*accent, 255))

    tag_font = get_font(46, bold=False)
    tag_bbox = dummy.textbbox((0, 0), CHANNEL_TAG, font=tag_font)
    tx = (WIDTH - (tag_bbox[2] - tag_bbox[0])) // 2
    draw.text((tx, HEIGHT - 90), CHANNEL_TAG, font=tag_font, fill=(190, 190, 190, 220))

    base_rgba = np.array(base_img)
    return hook_rgba, base_rgba


# ── TTS ────────────────────────────────────────────────────────────────────────
async def _tts(script: str, voice: str, path: str) -> None:
    import edge_tts
    await edge_tts.Communicate(script, voice).save(path)


def generate_audio(script: str, out_path: str) -> None:
    asyncio.run(_tts(script, VOICE, out_path))


# ── Thumbnail ──────────────────────────────────────────────────────────────────
def make_thumbnail(sign: str, thumb_text: str, out_path: str) -> None:
    TW, TH = 1280, 720
    top, bot = SIGN_GRADIENTS.get(sign, ((10, 10, 25), (25, 25, 60)))
    accent = SIGN_ACCENT.get(sign, (255, 255, 255))
    emoji = SIGN_EMOJIS.get(sign, "⭐")

    # Gradient background
    bg = np.zeros((TH, TW, 3), dtype=np.float32)
    ys = np.linspace(0, 1, TH)[:, None]
    for c in range(3):
        bg[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    img = Image.fromarray(bg.astype(np.uint8))
    draw = ImageDraw.Draw(img)

    # Stars
    rng = np.random.RandomState(hash(sign) % 2**31)
    for _ in range(140):
        x, y = rng.randint(0, TW), rng.randint(0, TH)
        b = rng.randint(140, 255)
        draw.ellipse([x-2, y-2, x+2, y+2], fill=(b, b, b))

    # Left side: large emoji
    em_font = get_font(260, bold=True)
    draw.text((40, TH // 2 - 160), emoji, font=em_font, fill=accent)

    # Top-left: sign name
    name_font = get_font(100, bold=True)
    draw.text((40, 30), sign.upper(), font=name_font, fill=(255, 255, 255))

    # Right side: thumbnail graphic text
    msg_font = get_font(88, bold=True)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    right_x = TW // 2 + 50
    max_w = TW - right_x - 40
    lines = wrap_text(thumb_text.upper(), msg_font, max_w)
    y = TH // 2 - len(lines) * 55
    for line in lines:
        bbox = dummy.textbbox((0, 0), line, font=msg_font)
        lw = bbox[2] - bbox[0]
        draw.text((right_x + 3, y + 3), line, font=msg_font, fill=(0, 0, 0))
        draw.text((right_x, y), line, font=msg_font, fill=accent)
        y += 110

    # Bottom: channel tag
    tag_font = get_font(40, bold=False)
    draw.text((40, TH - 60), CHANNEL_TAG, font=tag_font, fill=(200, 200, 200))

    img.save(out_path, "JPEG", quality=95)
    print(f"[INFO] Thumbnail → {out_path}")


# ── Main pipeline ──────────────────────────────────────────────────────────────
def process(json_path: str) -> None:
    path = Path(json_path)
    if not path.exists():
        print(f"[ERROR] Not found: {json_path}", file=sys.stderr)
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    hook_text  = data["hook_on_screen_text"]
    thumb_text = data["thumbnail_graphic_text"]
    script     = data["script"]

    stem    = path.stem                    # e.g. scorpio_short_20260616
    parts   = stem.split("_")
    sign    = parts[0].lower()
    date_tag = parts[-1]
    base    = f"{sign}_short_{date_tag}"

    audio_path = f"{base}.mp3"
    video_path = f"{base}.mp4"
    thumb_path = f"{base}_thumbnail.jpg"

    print(f"\n{'='*55}")
    print(f"  {sign.upper()} {SIGN_EMOJIS.get(sign, '')}  →  {video_path}")
    print(f"{'='*55}")

    # 1. TTS
    print(f"[1/3] Generating voiceover via edge-tts (free)...")
    generate_audio(script, audio_path)
    audio_clip = AudioFileClip(audio_path)
    duration   = audio_clip.duration
    print(f"      Duration: {duration:.1f}s  ({len(script.split())} words)")

    # 2. Video frames
    print(f"[2/3] Rendering video ({WIDTH}x{HEIGHT} @ {FPS}fps)...")
    bg           = make_gradient(sign)
    star_seed    = hash(sign) % 2**31
    sx, sy, sp, sz = make_star_field(n=260, seed=star_seed)
    hook_rgba, base_rgba = build_overlays(sign, hook_text)
    hook_end     = 4.0
    fade_dur     = 0.4

    def make_frame(t: float) -> np.ndarray:
        frame = draw_stars(bg, sx, sy, sp, sz, t)
        blend_rgba(frame, base_rgba, 1.0)
        if t < hook_end:
            alpha = min(1.0, t / fade_dur) * min(1.0, (hook_end - t) / fade_dur)
            blend_rgba(frame, hook_rgba, alpha)
        return frame

    video_clip = VideoClip(make_frame, duration=duration).set_fps(FPS)
    video_clip = video_clip.set_audio(audio_clip)

    video_clip.write_videofile(
        video_path,
        fps=FPS,
        codec="libx264",
        audio_codec="aac",
        preset="fast",
        ffmpeg_params=["-crf", "22"],
        logger=None,
    )
    audio_clip.close()
    video_clip.close()

    # 3. Thumbnail
    print(f"[3/3] Generating thumbnail...")
    make_thumbnail(sign, thumb_text, thumb_path)

    size_mb = os.path.getsize(video_path) / 1_048_576
    print(f"\n[OK] {sign.title()} complete")
    print(f"     Video:     {video_path}  ({size_mb:.1f} MB)")
    print(f"     Thumbnail: {thumb_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create YouTube Shorts videos from horoscope asset JSON files"
    )
    parser.add_argument("json_file", nargs="?", help="Single JSON asset file")
    parser.add_argument(
        "--all", metavar="YYYYMMDD",
        help="Process all 12 signs for a date (e.g. --all 20260616)",
    )
    args = parser.parse_args()

    if args.all:
        files = sorted(glob.glob(f"*_short_{args.all}.json"))
        if not files:
            print(f"[ERROR] No *_short_{args.all}.json files found", file=sys.stderr)
            sys.exit(1)
        print(f"[INFO] Processing {len(files)} sign(s) for {args.all}")
        for f in files:
            process(f)
        print(f"\n[DONE] All {len(files)} videos generated.")
    elif args.json_file:
        process(args.json_file)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
