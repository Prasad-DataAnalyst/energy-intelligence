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
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from moviepy import AudioFileClip, VideoClip

# ── Video constants ────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
FPS = 24
CHANNEL_TAG = "GetMindFuelNow"

# Voice priority list — ethereal Irish first, then British, then US fallbacks
VOICE_PREFERENCES = [
    ("en-IE-EmilyNeural",    "-10%", "-3Hz"),
    ("en-GB-SoniaNeural",    "-10%", "-3Hz"),
    ("en-US-MichelleNeural", "-10%", "-3Hz"),
    ("en-US-JennyNeural",    "-12%", "-4Hz"),  # last resort
]
_active_voice: list = []   # populated on first TTS call

# ── Gold accent (used for all signs — premium, mystical look) ─────────────────
GOLD   = (255, 215,   0)   # #FFD700
WHITE  = (255, 255, 255)
SILVER = (200, 200, 220)

# ── Per-sign: charcoal-black → deep sign colour (dark, premium) ───────────────
SIGN_GRADIENTS = {
    "aries":       ((8,  2,  2),  (28,  6,  6)),
    "taurus":      ((2,  8,  2),  ( 6, 28,  6)),
    "gemini":      ((8,  7,  1),  (24, 22,  3)),
    "cancer":      ((3,  3, 10),  ( 8,  8, 32)),
    "leo":         ((10, 5,  0),  (30, 14,  0)),
    "virgo":       ((2,  8,  3),  ( 6, 28, 10)),
    "libra":       ((6,  2, 10),  (18,  5, 32)),
    "scorpio":     ((8,  0,  1),  (24,  0,  4)),
    "sagittarius": ((0,  3, 10),  ( 0,  9, 32)),
    "capricorn":   ((3,  4,  3),  ( 9, 12,  9)),
    "aquarius":    ((0,  6,  8),  ( 0, 16, 28)),
    "pisces":      ((4,  0,  9),  (12,  0, 28)),
}

# Neon per-sign secondary (used sparingly for variety)
SIGN_NEON = {
    "aries":       (255,  60,  60),
    "taurus":      ( 60, 220,  60),
    "gemini":      (255, 230,  30),
    "cancer":      (160, 160, 255),
    "leo":         (255, 160,   0),
    "virgo":       (100, 220, 100),
    "libra":       (200, 100, 255),
    "scorpio":     (255,  30,  50),
    "sagittarius": ( 60, 130, 255),
    "capricorn":   (130, 180, 130),
    "aquarius":    (  0, 210, 230),
    "pisces":      (150, 100, 255),
}

SIGN_EMOJIS = {
    "aries": "♈", "taurus": "♉", "gemini": "♊", "cancer": "♋",
    "leo": "♌", "virgo": "♍", "libra": "♎", "scorpio": "♏",
    "sagittarius": "♐", "capricorn": "♑", "aquarius": "♒", "pisces": "♓",
}

# ── Font management ────────────────────────────────────────────────────────────
_FONT_DIR  = Path.home() / ".local" / "share" / "fonts"
_CINZEL_B  = _FONT_DIR / "Cinzel-Bold.ttf"
_CINZEL_R  = _FONT_DIR / "Cinzel-Regular.ttf"

_FALLBACK_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
_FALLBACK_REG = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]

_font_cache: dict = {}


def _ensure_cinzel() -> None:
    """Download Cinzel (elegant serif) from Google Fonts if not present."""
    if _CINZEL_B.exists() and _CINZEL_R.exists():
        return
    _FONT_DIR.mkdir(parents=True, exist_ok=True)
    base = "https://github.com/google/fonts/raw/main/ofl/cinzel/static/"
    for fname, dest in [("Cinzel-Bold.ttf", _CINZEL_B),
                        ("Cinzel-Regular.ttf", _CINZEL_R)]:
        if not dest.exists():
            print(f"[FONT] Downloading {fname}...")
            try:
                urllib.request.urlretrieve(base + fname, dest)
            except Exception as e:
                print(f"[FONT] Download failed ({e}) — using fallback font")


def get_font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _font_cache:
        candidates = ([str(_CINZEL_B)] + _FALLBACK_BOLD if bold
                      else [str(_CINZEL_R)] + _FALLBACK_REG)
        for p in candidates:
            if Path(p).exists():
                _font_cache[key] = ImageFont.truetype(p, size)
                break
        else:
            _font_cache[key] = ImageFont.load_default()
    return _font_cache[key]


# ── Background ─────────────────────────────────────────────────────────────────
def make_gradient(sign: str) -> np.ndarray:
    top, bot = SIGN_GRADIENTS.get(sign, ((4, 4, 12), (10, 10, 28)))
    bg = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    ys = np.linspace(0, 1, HEIGHT)[:, None]
    for c in range(3):
        bg[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    return np.clip(bg, 0, 255).astype(np.uint8)


# ── Star field ─────────────────────────────────────────────────────────────────
def make_star_field(n: int = 320, seed: int = 0) -> tuple:
    rng = np.random.RandomState(seed)
    xs     = rng.randint(0, WIDTH, n)
    ys     = rng.randint(0, HEIGHT, n)
    phases = rng.uniform(0, 1, n)
    sizes  = rng.choice([1, 1, 1, 2, 2, 3], size=n)
    gold_mask = rng.rand(n) < 0.08
    return xs, ys, phases, sizes, gold_mask


def draw_stars(frame: np.ndarray, stars: tuple, t: float) -> np.ndarray:
    xs, ys, phases, sizes, gold_mask = stars
    brightness = (130 + 125 * np.abs(np.sin(np.pi * (t * 0.3 + phases)))).astype(np.uint8)
    out = frame.copy()
    for i in range(len(xs)):
        x, y, b, r = int(xs[i]), int(ys[i]), int(brightness[i]), int(sizes[i])
        if gold_mask[i]:
            col = (min(255, b), min(255, int(b * 0.84)), 0)
        else:
            col = (b, b, b)
        out[max(0, y-r):y+r+1, max(0, x-r):x+r+1] = col
    return out


# ── Floating particles ─────────────────────────────────────────────────────────
def make_particles(n: int = 60, seed: int = 42) -> tuple:
    """Pre-compute particle positions for slow upward drift."""
    rng      = np.random.RandomState(seed)
    px       = rng.randint(0, WIDTH, n).astype(float)
    py_start = rng.randint(0, HEIGHT, n).astype(float)
    speeds   = rng.uniform(8, 22, n)          # pixels per second
    sizes    = rng.choice([1, 1, 2, 2, 3], size=n)
    alphas   = rng.uniform(30, 100, n)        # 0-255 brightness contribution
    return px, py_start, speeds, sizes, alphas


def draw_particles(frame: np.ndarray, particles: tuple, t: float) -> np.ndarray:
    px, py_start, speeds, sizes, alphas = particles
    out = frame.copy()
    for i in range(len(px)):
        x  = int(px[i])
        y  = int((py_start[i] - speeds[i] * t) % HEIGHT)
        r  = int(sizes[i])
        a  = alphas[i] / 255.0
        y1, y2 = max(0, y - r), min(HEIGHT, y + r + 1)
        x1, x2 = max(0, x - r), min(WIDTH,  x + r + 1)
        if y1 >= y2 or x1 >= x2:
            continue
        region = out[y1:y2, x1:x2].astype(np.float32)
        out[y1:y2, x1:x2] = np.clip(
            region * (1 - a) + 255 * a, 0, 255
        ).astype(np.uint8)
    return out


# ── Vignette ───────────────────────────────────────────────────────────────────
def make_vignette() -> np.ndarray:
    """Dark elliptical vignette — darkens screen edges for cinematic look."""
    xs = np.linspace(-1, 1, WIDTH)
    ys = np.linspace(-1, 1, HEIGHT)
    xx, yy = np.meshgrid(xs, ys)
    dist = np.sqrt(xx**2 + yy**2) / np.sqrt(2)   # 0 centre, 1 corner
    alpha = np.clip((dist - 0.35) / 0.65, 0, 1) ** 1.8 * 200
    vig = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    vig[:, :, 3] = alpha.astype(np.uint8)          # pure black with alpha
    return vig


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


def text_width(text: str, font: ImageFont.FreeTypeFont) -> int:
    dummy = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bbox = dummy.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0]


# ── Overlays ───────────────────────────────────────────────────────────────────
def build_aura_overlay(sign: str) -> np.ndarray:
    """Soft sign-coloured glow behind the header strip — warmth + identity."""
    neon = SIGN_NEON.get(sign, WHITE)
    img  = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    # Draw a wide ellipse on a strip, then blur heavily
    glow = Image.new("RGBA", (WIDTH, 340), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-120, -80, WIDTH + 120, 300], fill=(*neon, 70))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=60))
    img.paste(glow, (0, 0), glow)
    return np.array(img)


def build_hook_overlay(hook_text: str) -> np.ndarray:
    """
    Dramatic full-centre hook: large GOLD Cinzel text + dark vignette strip.
    Shown during first 3.5 seconds with fade in/out.
    """
    img  = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    font  = get_font(118, bold=True)
    lines = wrap_text(hook_text, font, WIDTH - 80)
    lh    = int(font.size * 1.3)
    total = len(lines) * lh
    cy    = HEIGHT // 2 - 80
    y     = cy - total // 2

    pad = 40
    draw.rectangle([0, y - pad, WIDTH, y + total + pad], fill=(0, 0, 0, 175))

    for line in lines:
        x = (WIDTH - text_width(line, font)) // 2
        draw.text((x + 4, y + 4), line, font=font, fill=(0, 0, 0, 220))
        draw.text((x, y), line, font=font, fill=(*GOLD, 255))
        y += lh

    return np.array(img)


def build_base_overlay(sign: str) -> np.ndarray:
    """Sign name header (gold Cinzel) + channel tag footer — always visible."""
    neon  = SIGN_NEON.get(sign, WHITE)
    emoji = SIGN_EMOJIS.get(sign, "⭐")
    img   = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)

    draw.rectangle([0, 0, WIDTH, 240], fill=(0, 0, 0, 160))
    draw.rectangle([0, 0, WIDTH, 6], fill=(*GOLD, 255))

    font  = get_font(88, bold=True)
    label = f"{emoji}  {sign.title()}  {emoji}"
    x     = (WIDTH - text_width(label, font)) // 2
    draw.text((x + 3, 72 + 3), label, font=font, fill=(0, 0, 0, 200))
    draw.text((x, 72), label, font=font, fill=(*GOLD, 255))

    draw.rectangle([0, HEIGHT - 120, WIDTH, HEIGHT], fill=(0, 0, 0, 150))
    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=(*GOLD, 200))

    tag_font = get_font(46, bold=False)
    tw = text_width(CHANNEL_TAG, tag_font)
    draw.text(((WIDTH - tw) // 2, HEIGHT - 95), CHANNEL_TAG,
              font=tag_font, fill=(*SILVER, 230))

    return np.array(img)


def build_caption_overlay(text: str, sign: str) -> np.ndarray:
    """
    Caption strip: white Cinzel text, dark pill background.
    Positioned in lower-middle to not clash with footer.
    """
    font  = get_font(58, bold=False)
    img   = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw  = ImageDraw.Draw(img)

    lines  = wrap_text(text, font, WIDTH - 120)
    lh     = 74
    total  = len(lines) * lh + 20
    cap_y  = HEIGHT - 200 - total

    draw.rectangle([50, cap_y - 18, WIDTH - 50, cap_y + total + 10],
                   fill=(0, 0, 0, 175))

    y = cap_y
    for line in lines:
        x = (WIDTH - text_width(line, font)) // 2
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0, 200))
        draw.text((x, y), line, font=font, fill=(*WHITE, 255))
        y += lh

    return np.array(img)


# ── Caption timing ─────────────────────────────────────────────────────────────
def make_caption_chunks(script: str, duration: float, words_per_chunk: int = 4) -> list:
    words = script.split()
    total = len(words)
    raw   = [" ".join(words[i:i + words_per_chunk])
             for i in range(0, total, words_per_chunk)]
    wps   = total / duration
    result, t = [], 0.0
    for chunk in raw:
        dur = len(chunk.split()) / wps
        result.append((t, t + dur, chunk))
        t += dur
    return result


# ── TTS ────────────────────────────────────────────────────────────────────────
async def _tts(script: str, path: str) -> None:
    import edge_tts
    global _active_voice
    if not _active_voice:
        try:
            available = {v["Name"] for v in await edge_tts.list_voices()}
        except Exception:
            available = set()
        for name, rate, pitch in VOICE_PREFERENCES:
            if not available or name in available:
                _active_voice = [name, rate, pitch]
                print(f"[TTS] Voice selected: {name}")
                break
        if not _active_voice:
            _active_voice = list(VOICE_PREFERENCES[-1])
            print(f"[TTS] Fallback voice: {_active_voice[0]}")
    name, rate, pitch = _active_voice
    await edge_tts.Communicate(script, name, rate=rate, pitch=pitch).save(path)


def generate_audio(script: str, out_path: str) -> None:
    asyncio.run(_tts(script, out_path))


# ── SRT subtitle writer ────────────────────────────────────────────────────────
def write_srt(chunks: list, out_path: str) -> None:
    def fmt_time(s: float) -> str:
        h   = int(s // 3600)
        m   = int((s % 3600) // 60)
        sec = int(s % 60)
        ms  = int(round((s - int(s)) * 1000))
        return f"{h:02d}:{m:02d}:{sec:02d},{ms:03d}"

    lines = []
    for i, (start, end, text) in enumerate(chunks, 1):
        lines.append(str(i))
        lines.append(f"{fmt_time(start)} --> {fmt_time(end)}")
        lines.append(text)
        lines.append("")
    Path(out_path).write_text("\n".join(lines), encoding="utf-8")
    print(f"[INFO] SRT → {out_path}")


# ── Output folder management ───────────────────────────────────────────────────
def make_output_folder(sign: str, date_tag: str) -> Path:
    folder = Path("outputs") / date_tag / sign.title()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def write_metadata_files(folder: Path, data: dict, sign: str, date_tag: str) -> None:
    for key in ["title", "description", "hashtags", "tags", "pinned_comment", "script"]:
        if key in data:
            (folder / f"{key}.txt").write_text(str(data[key]), encoding="utf-8")
    (folder / f"{sign}_{date_tag}_assets.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[INFO] Metadata → {folder}/")


# ── Thumbnail ──────────────────────────────────────────────────────────────────
def make_thumbnail(sign: str, thumb_text: str, out_path: str) -> None:
    TW, TH = 1280, 720
    top, bot = SIGN_GRADIENTS.get(sign, ((4, 4, 12), (10, 10, 28)))
    neon  = SIGN_NEON.get(sign, WHITE)
    emoji = SIGN_EMOJIS.get(sign, "⭐")

    bg = np.zeros((TH, TW, 3), dtype=np.float32)
    ys = np.linspace(0, 1, TH)[:, None]
    for c in range(3):
        bg[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    img  = Image.fromarray(bg.astype(np.uint8))
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, TW, 8], fill=(*GOLD, 255))
    draw.rectangle([0, TH - 8, TW, TH], fill=(*GOLD, 255))

    rng = np.random.RandomState(hash(sign) % 2**31)
    for _ in range(180):
        x, y = rng.randint(0, TW), rng.randint(0, TH)
        b = rng.randint(120, 220)
        gold = rng.rand() < 0.1
        col = (b, int(b * 0.84), 0) if gold else (b, b, b)
        draw.ellipse([x-2, y-2, x+2, y+2], fill=col)

    em_font = get_font(280, bold=True)
    draw.text((20, TH // 2 - 185), emoji, font=em_font, fill=GOLD)

    name_font = get_font(110, bold=True)
    draw.text((22, 18), sign.upper(), font=name_font, fill=GOLD)

    msg_font = get_font(105, bold=True)
    right_x  = TW // 2 + 40
    max_w    = TW - right_x - 30
    lines    = wrap_text(thumb_text.upper(), msg_font, max_w)
    y = TH // 2 - len(lines) * 65
    for line in lines:
        lw = text_width(line, msg_font)
        draw.text((right_x + 4, y + 4), line, font=msg_font, fill=(0, 0, 0))
        draw.text((right_x, y), line, font=msg_font, fill=neon)
        y += 128

    tag_font = get_font(40, bold=False)
    draw.text((22, TH - 55), CHANNEL_TAG, font=tag_font, fill=SILVER)

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

    # Output folder: outputs/YYYYMMDD/SignName/
    out_dir    = make_output_folder(sign, date_tag)
    base       = f"{sign}_short_{date_tag}"
    audio_path = str(out_dir / f"{base}.mp3")
    video_path = str(out_dir / f"{base}.mp4")
    thumb_path = str(out_dir / f"{base}_thumbnail.jpg")
    srt_path   = str(out_dir / f"{base}.srt")

    print(f"\n{'='*55}")
    print(f"  {sign.upper()} {SIGN_EMOJIS.get(sign, '')}  →  {out_dir}/")
    print(f"{'='*55}")

    # ── 1. TTS ────────────────────────────────────────────────────────────────
    print(f"[1/4] Voiceover...")
    generate_audio(script, audio_path)
    audio_clip = AudioFileClip(audio_path)
    duration   = audio_clip.duration
    print(f"      {duration:.1f}s  |  {len(script.split())} words")

    # ── 2. Pre-render all overlays ────────────────────────────────────────────
    print(f"[2/4] Rendering {WIDTH}x{HEIGHT} @ {FPS}fps...")

    bg        = make_gradient(sign)
    stars     = make_star_field(320, seed=hash(sign) % 2**31)
    particles = make_particles(60, seed=hash(sign + "p") % 2**31)
    vignette  = make_vignette()
    aura_ov   = build_aura_overlay(sign)
    hook_ov   = build_hook_overlay(hook_text)
    base_ov   = build_base_overlay(sign)
    chunks    = make_caption_chunks(script, duration)
    cap_cache = {txt: build_caption_overlay(txt, sign) for _, _, txt in chunks}

    hook_end = 3.5
    fade_dur = 0.4
    fade_in  = 0.8   # global video fade-in duration

    def make_frame(t: float) -> np.ndarray:
        frame = draw_stars(bg, stars, t)
        frame = draw_particles(frame, particles, t)
        blend_rgba(frame, aura_ov, 1.0)
        blend_rgba(frame, vignette, 1.0)
        blend_rgba(frame, base_ov, 1.0)
        if t < hook_end:
            a = min(1.0, t / fade_dur) * min(1.0, (hook_end - t) / fade_dur)
            blend_rgba(frame, hook_ov, a)
        if t >= 0.5:
            for start, end, txt in chunks:
                if start <= t < end:
                    blend_rgba(frame, cap_cache[txt], 1.0)
                    break
        # Global 0.8s fade-in from black
        if t < fade_in:
            frame = (frame.astype(np.float32) * (t / fade_in)).astype(np.uint8)
        return frame

    clip = VideoClip(make_frame, duration=duration).with_fps(FPS)
    clip = clip.with_audio(audio_clip)
    clip.write_videofile(
        video_path, fps=FPS, codec="libx264", audio_codec="aac",
        preset="fast", ffmpeg_params=["-crf", "22"], logger=None,
    )
    audio_clip.close()
    clip.close()

    # ── 3. SRT subtitles ──────────────────────────────────────────────────────
    print(f"[3/4] Writing SRT...")
    write_srt(chunks, srt_path)

    # ── 4. Thumbnail + metadata ───────────────────────────────────────────────
    print(f"[4/4] Thumbnail + metadata...")
    make_thumbnail(sign, thumb_text, thumb_path)
    write_metadata_files(out_dir, data, sign, date_tag)

    size_mb = os.path.getsize(video_path) / 1_048_576
    print(f"\n[OK] {sign.title()} → {out_dir}/")
    print(f"     Video:     {video_path}  ({size_mb:.1f} MB)")
    print(f"     Thumbnail: {thumb_path}")
    print(f"     SRT:       {srt_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create YouTube Shorts from horoscope asset JSON files"
    )
    parser.add_argument("json_file", nargs="?", help="Single JSON asset file")
    parser.add_argument("--all", metavar="YYYYMMDD",
                        help="Process all 12 signs for a given date")
    args = parser.parse_args()

    _ensure_cinzel()

    if args.all:
        files = sorted(glob.glob(f"*_short_{args.all}.json"))
        if not files:
            print(f"[ERROR] No *_short_{args.all}.json files found", file=sys.stderr)
            sys.exit(1)
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
