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
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy import AudioFileClip, VideoClip

# ── Video constants ────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
FPS = 24
CHANNEL_TAG = "GetMindFuelNow"

# Soft, warm voice — sounds natural for horoscope content
VOICE       = "en-US-JennyNeural"
VOICE_RATE  = "-12%"    # slightly slower = more mystical
VOICE_PITCH = "-4Hz"    # slightly lower = warmer, less robotic

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
    "scorpio":     (255,  60,  60),
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


# ── Background gradient ────────────────────────────────────────────────────────
def make_gradient(sign: str) -> np.ndarray:
    top, bot = SIGN_GRADIENTS.get(sign, ((10, 10, 25), (25, 25, 60)))
    bg = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    ys = np.linspace(0, 1, HEIGHT)[:, None]
    for c in range(3):
        bg[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    return np.clip(bg, 0, 255).astype(np.uint8)


# ── Star field ─────────────────────────────────────────────────────────────────
def make_star_field(n: int = 260, seed: int = 0) -> tuple:
    rng = np.random.RandomState(seed)
    xs    = rng.randint(0, WIDTH, n)
    ys    = rng.randint(0, HEIGHT, n)
    phases = rng.uniform(0, 1, n)
    sizes  = rng.uniform(0.6, 2.8, n).astype(int).clip(1, 3)
    return xs, ys, phases, sizes


def draw_stars(frame: np.ndarray, xs, ys, phases, sizes, t: float) -> np.ndarray:
    brightness = (140 + 115 * np.abs(np.sin(np.pi * (t * 0.35 + phases)))).astype(np.uint8)
    out = frame.copy()
    for i in range(len(xs)):
        x, y, b, r = int(xs[i]), int(ys[i]), brightness[i], int(sizes[i])
        out[max(0, y-r):y+r+1, max(0, x-r):x+r+1] = b
    return out


# ── RGBA blend ────────────────────────────────────────────────────────────────
def blend_rgba(base: np.ndarray, overlay: np.ndarray, alpha_scale: float = 1.0) -> None:
    a = overlay[:, :, 3:4].astype(np.float32) / 255.0 * alpha_scale
    base[:] = np.clip(
        base.astype(np.float32) * (1 - a) + overlay[:, :, :3].astype(np.float32) * a,
        0, 255,
    ).astype(np.uint8)


# ── Text helpers ───────────────────────────────────────────────────────────────
def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_px: int) -> list:
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words, lines, cur = text.split(), [], ""
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


# ── Pre-render overlays ────────────────────────────────────────────────────────
def build_hook_overlay(hook_text: str, sign: str) -> np.ndarray:
    """Large centred hook text — shows first 3.5 seconds."""
    accent = SIGN_ACCENT.get(sign, (255, 255, 255))
    font = get_font(108, bold=True)
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    lines = wrap_text(hook_text, font, WIDTH - 100)
    line_h = int(font.size * 1.25)
    y = HEIGHT // 2 - (len(lines) * line_h) // 2 - 100
    for line in lines:
        bbox = dummy.textbbox((0, 0), line, font=font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 210))
        draw.text((x, y), line, font=font, fill=(*accent, 255))
        y += line_h
    return np.array(img)


def build_base_overlay(sign: str) -> np.ndarray:
    """Sign name header + channel footer — always visible."""
    accent = SIGN_ACCENT.get(sign, (255, 255, 255))
    emoji  = SIGN_EMOJIS.get(sign, "⭐")
    img    = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw   = ImageDraw.Draw(img)
    dummy  = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    # Header background strip
    draw.rectangle([0, 0, WIDTH, 230], fill=(0, 0, 0, 140))

    # Sign name + emoji
    font = get_font(90, bold=True)
    label = f"{emoji}  {sign.title()}  {emoji}"
    bbox = dummy.textbbox((0, 0), label, font=font)
    sx = (WIDTH - (bbox[2] - bbox[0])) // 2
    draw.text((sx + 3, 68 + 3), label, font=font, fill=(0, 0, 0, 180))
    draw.text((sx, 68), label, font=font, fill=(*accent, 255))

    # Footer
    tag_font = get_font(44, bold=False)
    bbox = dummy.textbbox((0, 0), CHANNEL_TAG, font=tag_font)
    tx = (WIDTH - (bbox[2] - bbox[0])) // 2
    draw.rectangle([0, HEIGHT - 110, WIDTH, HEIGHT], fill=(0, 0, 0, 130))
    draw.text((tx, HEIGHT - 90), CHANNEL_TAG, font=tag_font, fill=(200, 200, 200, 220))

    return np.array(img)


def build_caption_overlay(text: str) -> np.ndarray:
    """Timed caption line shown at bottom-centre during voiceover."""
    font = get_font(60, bold=True)
    img  = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    lines  = wrap_text(text, font, WIDTH - 100)
    line_h = 76
    total_h = len(lines) * line_h + 30
    cap_top = HEIGHT - 180 - total_h

    # Dark pill background
    draw.rectangle([40, cap_top - 16, WIDTH - 40, cap_top + total_h + 4],
                   fill=(0, 0, 0, 170))

    y = cap_top
    for line in lines:
        bbox = dummy.textbbox((0, 0), line, font=font)
        x = (WIDTH - (bbox[2] - bbox[0])) // 2
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=(255, 255, 255, 255))
        y += line_h

    return np.array(img)


# ── Caption timing ─────────────────────────────────────────────────────────────
def make_caption_chunks(script: str, audio_duration: float,
                        words_per_chunk: int = 9) -> list:
    """Split script into (start_t, end_t, text) chunks proportional to word count."""
    words = script.split()
    total = len(words)
    chunks = []
    i = 0
    while i < total:
        chunks.append(" ".join(words[i:i + words_per_chunk]))
        i += words_per_chunk

    wps = total / audio_duration          # words per second
    result, t = [], 0.0
    for chunk in chunks:
        dur = len(chunk.split()) / wps
        result.append((t, t + dur, chunk))
        t += dur
    return result


# ── TTS ────────────────────────────────────────────────────────────────────────
async def _tts(script: str, path: str) -> None:
    import edge_tts
    comm = edge_tts.Communicate(script, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH)
    await comm.save(path)


def generate_audio(script: str, out_path: str) -> None:
    asyncio.run(_tts(script, out_path))


# ── Thumbnail ──────────────────────────────────────────────────────────────────
def make_thumbnail(sign: str, thumb_text: str, out_path: str) -> None:
    TW, TH = 1280, 720
    top, bot = SIGN_GRADIENTS.get(sign, ((10, 10, 25), (25, 25, 60)))
    accent = SIGN_ACCENT.get(sign, (255, 255, 255))
    emoji  = SIGN_EMOJIS.get(sign, "⭐")

    bg = np.zeros((TH, TW, 3), dtype=np.float32)
    ys = np.linspace(0, 1, TH)[:, None]
    for c in range(3):
        bg[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    img  = Image.fromarray(bg.astype(np.uint8))
    draw = ImageDraw.Draw(img)

    rng = np.random.RandomState(hash(sign) % 2**31)
    for _ in range(150):
        x, y = rng.randint(0, TW), rng.randint(0, TH)
        b = rng.randint(140, 255)
        draw.ellipse([x-2, y-2, x+2, y+2], fill=(b, b, b))

    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    # Left: large emoji
    em_font = get_font(260, bold=True)
    draw.text((30, TH // 2 - 175), emoji, font=em_font, fill=accent)

    # Top-left: sign name
    name_font = get_font(105, bold=True)
    draw.text((30, 20), sign.upper(), font=name_font, fill=(255, 255, 255))

    # Right: thumbnail graphic text
    msg_font = get_font(85, bold=True)
    right_x  = TW // 2 + 60
    max_w    = TW - right_x - 40
    lines    = wrap_text(thumb_text.upper(), msg_font, max_w)
    y = TH // 2 - len(lines) * 58
    for line in lines:
        bbox = dummy.textbbox((0, 0), line, font=msg_font)
        draw.text((right_x + 3, y + 3), line, font=msg_font, fill=(0, 0, 0))
        draw.text((right_x, y), line, font=msg_font, fill=accent)
        y += 115

    # Footer
    tag_font = get_font(40, bold=False)
    draw.text((30, TH - 56), CHANNEL_TAG, font=tag_font, fill=(200, 200, 200))

    img.save(out_path, "JPEG", quality=95)
    print(f"[INFO] Thumbnail → {out_path}")


# ── Main pipeline ──────────────────────────────────────────────────────────────
def process(json_path: str) -> None:
    path = Path(json_path)
    if not path.exists():
        print(f"[ERROR] Not found: {json_path}", file=sys.stderr)
        return

    data       = json.loads(path.read_text(encoding="utf-8"))
    hook_text  = data["hook_on_screen_text"]
    thumb_text = data["thumbnail_graphic_text"]
    script     = data["script"]

    parts    = path.stem.split("_")
    sign     = parts[0].lower()
    date_tag = parts[-1]
    base     = f"{sign}_short_{date_tag}"

    audio_path = f"{base}.mp3"
    video_path = f"{base}.mp4"
    thumb_path = f"{base}_thumbnail.jpg"

    print(f"\n{'='*55}")
    print(f"  {sign.upper()} {SIGN_EMOJIS.get(sign, '')}  →  {video_path}")
    print(f"{'='*55}")

    # ── 1. TTS ────────────────────────────────────────────────────────────────
    print(f"[1/3] Generating voiceover  voice={VOICE}  rate={VOICE_RATE}  pitch={VOICE_PITCH}")
    generate_audio(script, audio_path)
    audio_clip = AudioFileClip(audio_path)
    duration   = audio_clip.duration
    print(f"      {duration:.1f}s  |  {len(script.split())} words")

    # ── 2. Video ──────────────────────────────────────────────────────────────
    print(f"[2/3] Rendering {WIDTH}x{HEIGHT} @ {FPS}fps  (~3-6 min)...")

    bg             = make_gradient(sign)
    sx, sy, sp, sz = make_star_field(260, seed=hash(sign) % 2**31)
    hook_rgba      = build_hook_overlay(hook_text, sign)
    base_rgba      = build_base_overlay(sign)
    caption_chunks = make_caption_chunks(script, duration)

    # Pre-render all caption overlays (avoids per-frame PIL work)
    cap_cache = {text: build_caption_overlay(text) for _, _, text in caption_chunks}

    hook_end  = 3.5
    fade_dur  = 0.4

    def make_frame(t: float) -> np.ndarray:
        frame = draw_stars(bg, sx, sy, sp, sz, t)
        blend_rgba(frame, base_rgba, 1.0)

        # Hook text — centre screen, fade in/out first 3.5 s
        if t < hook_end:
            a = min(1.0, t / fade_dur) * min(1.0, (hook_end - t) / fade_dur)
            blend_rgba(frame, hook_rgba, a)

        # Caption — timed to voiceover, starts after 0.5 s
        if t >= 0.5:
            for start, end, text in caption_chunks:
                if start <= t < end:
                    blend_rgba(frame, cap_cache[text], 1.0)
                    break

        return frame

    video_clip = VideoClip(make_frame, duration=duration).with_fps(FPS)
    video_clip = video_clip.with_audio(audio_clip)
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

    # ── 3. Thumbnail ──────────────────────────────────────────────────────────
    print(f"[3/3] Generating thumbnail...")
    make_thumbnail(sign, thumb_text, thumb_path)

    size_mb = os.path.getsize(video_path) / 1_048_576
    print(f"\n[OK] {sign.title()} done  →  {video_path} ({size_mb:.1f} MB)  +  {thumb_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create YouTube Shorts from horoscope asset JSON files"
    )
    parser.add_argument("json_file", nargs="?", help="Single JSON asset file")
    parser.add_argument("--all", metavar="YYYYMMDD",
                        help="Process all 12 signs for a given date")
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
