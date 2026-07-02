#!/usr/bin/env python3
"""
make_daily_video.py
Creates a single "All 12 Signs" daily horoscope slideshow video.
Text-only cards, no voice-over, ambient cosmic music.
4s intro + 12s per sign x 12 = 148s total.

Usage:
  python3 make_daily_video.py daily_horoscope_20260621.json
  python3 make_daily_video.py 20260621
"""
import asyncio
import json
import os
import subprocess as _sp
import sys
import tempfile
import random
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

load_dotenv()

WIDTH, HEIGHT  = 1080, 1920
FPS            = 24
SIGN_SECS      = 12
INTRO_SECS     = 4
CHANNEL_TAG    = "GetMindFuelNow"

SIGNS = [
    "aries","taurus","gemini","cancer","leo","virgo",
    "libra","scorpio","sagittarius","capricorn","aquarius","pisces",
]

SIGN_EMOJIS = {
    "aries":"♈","taurus":"♉","gemini":"♊","cancer":"♋",
    "leo":"♌","virgo":"♍","libra":"♎","scorpio":"♏",
    "sagittarius":"♐","capricorn":"♑","aquarius":"♒","pisces":"♓",
}

GOLD   = (255, 215,   0)
WHITE  = (255, 255, 255)
SILVER = (200, 200, 220)

SIGN_GRADIENTS = {
    "aries":       ((18,  4,  4), (38,  8,  8)),
    "taurus":      (( 4, 16,  4), ( 8, 36,  8)),
    "gemini":      ((18, 16,  2), (36, 32,  4)),
    "cancer":      (( 4,  6, 20), ( 8, 12, 42)),
    "leo":         ((20,  8,  0), (44, 18,  0)),
    "virgo":       (( 4, 16,  6), ( 8, 36, 12)),
    "libra":       ((10,  4, 20), (22,  8, 44)),
    "scorpio":     ((20,  2,  4), (44,  4,  8)),
    "sagittarius": (( 2,  8, 20), ( 4, 16, 44)),
    "capricorn":   (( 6,  8,  6), (14, 18, 14)),
    "aquarius":    (( 0, 10, 20), ( 0, 22, 44)),
    "pisces":      (( 8,  2, 18), (18,  4, 40)),
}

SIGN_NEON = {
    "aries":       (255,  80,  80),
    "taurus":      ( 80, 220,  80),
    "gemini":      (255, 235,  50),
    "cancer":      (160, 160, 255),
    "leo":         (255, 175,   0),
    "virgo":       (100, 220, 100),
    "libra":       (220, 120, 255),
    "scorpio":     (255,  50,  70),
    "sagittarius": ( 80, 160, 255),
    "capricorn":   (155, 205, 155),
    "aquarius":    (  0, 220, 255),
    "pisces":      (180, 120, 255),
}

# ── Font management ────────────────────────────────────────────────────────────
_FONT_DIR = Path.home() / ".local" / "share" / "fonts"
_CINZEL_B = _FONT_DIR / "Cinzel-Bold.ttf"
_CINZEL_R = _FONT_DIR / "Cinzel-Regular.ttf"

_FALLBACK_BOLD = [
    "/usr/share/fonts/truetype/cinzel/Cinzel-Bold.otf",
    "/usr/share/fonts/opentype/cinzel/Cinzel-Bold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
]
_FALLBACK_REG = [
    "/usr/share/fonts/truetype/cinzel/Cinzel-Regular.otf",
    "/usr/share/fonts/opentype/cinzel/Cinzel-Regular.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/ubuntu/Ubuntu-R.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]

# Fonts that actually contain the U+2648–U+2653 zodiac glyphs. Cinzel does NOT —
# so glyphs must never be drawn with the display font or they render as tofu boxes.
_GLYPH_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSansSymbols2-Regular.ttf",
    "/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf",
    "/usr/share/fonts/truetype/symbola/Symbola.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
]

_fcache: dict = {}
_gcache: dict = {}


def _glyph_font(size: int) -> ImageFont.FreeTypeFont:
    """Font guaranteed to render a zodiac glyph (♈). Validates the actual glyph,
    not just 'X', so a glyph-less display font is never chosen for symbols."""
    if size not in _gcache:
        for p in _GLYPH_FONTS:
            if not Path(p).exists():
                continue
            try:
                f = ImageFont.truetype(p, size)
                d = ImageDraw.Draw(Image.new("RGB", (max(size * 2, 40),) * 2))
                if d.textbbox((0, 0), "♈", font=f)[2] > 2:   # ♈ Aries
                    _gcache[size] = f
                    break
            except Exception:
                continue
        else:
            _gcache[size] = _font(size, bold=True)   # last resort
    return _gcache[size]


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key not in _fcache:
        for p in ([str(_CINZEL_B)] + _FALLBACK_BOLD if bold
                  else [str(_CINZEL_R)] + _FALLBACK_REG):
            if not Path(p).exists():
                continue
            try:
                f = ImageFont.truetype(p, size)
                d = ImageDraw.Draw(Image.new("RGB", (max(size * 2, 40), max(size * 2, 40))))
                if d.textbbox((0, 0), "X", font=f)[2] > 2:
                    _fcache[key] = f
                    break
            except Exception:
                continue
        else:
            try:
                _fcache[key] = ImageFont.load_default(size=size)
            except TypeError:
                _fcache[key] = ImageFont.load_default()
    return _fcache[key]


def _tw(text: str, f) -> int:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    b = d.textbbox((0, 0), str(text), font=f)
    return b[2] - b[0]


def _th(f) -> int:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    b = d.textbbox((0, 0), "Ag", font=f)
    return b[3] - b[1]


def _wrap(text: str, f, max_px: int) -> list:
    d = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    words, lines, cur = str(text).split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textbbox((0, 0), t, font=f)[2] > max_px and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines or [""]


PAD = 64
CW  = WIDTH - 2 * PAD   # 952 px


def _gradient_img(sign: str) -> Image.Image:
    top, bot = SIGN_GRADIENTS.get(sign, ((8, 4, 20), (16, 8, 40)))
    a = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    ys = np.linspace(0, 1, HEIGHT)[:, None]
    for c in range(3):
        a[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _stars(draw: ImageDraw.Draw, seed: int = 0) -> None:
    rng = random.Random(seed)
    for _ in range(260):
        x, y = rng.randint(0, WIDTH), rng.randint(0, HEIGHT)
        b = rng.randint(90, 210)
        r = rng.choice([1, 1, 1, 2])
        col = (b, int(b * 0.84), 0) if rng.random() < 0.1 else (b, b, b)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)


# ── Intro card ─────────────────────────────────────────────────────────────────
def render_intro_card(date_str: str) -> Image.Image:
    top, bot = (5, 2, 18), (12, 5, 35)
    a = np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)
    ys = np.linspace(0, 1, HEIGHT)[:, None]
    for c in range(3):
        a[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    img  = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8)).convert("RGBA")
    draw = ImageDraw.Draw(img)

    _stars(draw, seed=42)

    draw.rectangle([0, 0, WIDTH, 8], fill=GOLD)
    draw.rectangle([0, HEIGHT - 8, WIDTH, HEIGHT], fill=GOLD)

    y = 300
    f_big = _font(116, bold=True)
    for line in ["DAILY", "HOROSCOPE"]:
        w = _tw(line, f_big)
        draw.text(((WIDTH - w) // 2 + 3, y + 3), line, font=f_big, fill=(0, 0, 0, 170))
        draw.text(((WIDTH - w) // 2,     y),     line, font=f_big, fill=GOLD)
        y += _th(f_big) + 10
    y += 28

    f_sub = _font(70, bold=True)
    txt = "ALL 12 SIGNS"
    w = _tw(txt, f_sub)
    draw.text(((WIDTH - w) // 2, y), txt, font=f_sub, fill=WHITE)
    y += _th(f_sub) + 48

    draw.rectangle([PAD, y, WIDTH - PAD, y + 4], fill=(*GOLD, 200))
    y += 36

    # All 12 zodiac glyphs — 2 rows of 6
    glyphs = [SIGN_EMOJIS[s] for s in SIGNS]
    f_g    = _glyph_font(78)
    col_w  = CW // 6
    for row in range(2):
        gx = PAD + col_w // 2
        for col in range(6):
            g  = glyphs[row * 6 + col]
            gw = _tw(g, f_g)
            draw.text((gx - gw // 2, y), g, font=f_g, fill=GOLD)
            gx += col_w
        y += _th(f_g) + 14
    y += 30

    draw.rectangle([PAD, y, WIDTH - PAD, y + 4], fill=(*GOLD, 200))
    y += 40

    f_date = _font(54, bold=False)
    w = _tw(date_str, f_date)
    draw.text(((WIDTH - w) // 2, y), date_str, font=f_date, fill=SILVER)
    y += _th(f_date) + 60

    # ── Hook + CTA — the first 4s decide retention on Shorts ────────────────────
    f_hook = _font(64, bold=True)
    hook   = "FIND YOUR SIGN"
    w = _tw(hook, f_hook)
    draw.text(((WIDTH - w) // 2 + 2, y + 2), hook, font=f_hook, fill=(0, 0, 0, 160))
    draw.text(((WIDTH - w) // 2,     y),     hook, font=f_hook, fill=WHITE)
    y += _th(f_hook) + 16

    f_arrow = _glyph_font(72)
    arrow   = "↓ ↓ ↓"
    w = _tw(arrow, f_arrow)
    draw.text(((WIDTH - w) // 2, y), arrow, font=f_arrow, fill=GOLD)

    # CTA pill above the channel tag
    f_cta = _font(46, bold=True)
    cta   = "COMMENT YOUR SIGN"
    cw    = _tw(cta, f_cta)
    cx    = (WIDTH - cw) // 2
    cy    = HEIGHT - 188
    draw.rounded_rectangle([cx - 30, cy - 14, cx + cw + 30, cy + _th(f_cta) + 20],
                           radius=18, fill=GOLD)
    draw.text((cx, cy), cta, font=f_cta, fill=(10, 5, 30))

    f_ch = _font(50, bold=False)
    w = _tw(CHANNEL_TAG, f_ch)
    draw.text(((WIDTH - w) // 2, HEIGHT - 96), CHANNEL_TAG, font=f_ch, fill=SILVER)

    return img.convert("RGB")


# ── Sign card ──────────────────────────────────────────────────────────────────
def render_sign_card(sign: str, fields: dict, idx: int) -> Image.Image:
    img  = _gradient_img(sign).convert("RGBA")
    neon = SIGN_NEON.get(sign, WHITE)

    # Faint zodiac glyph watermark
    glyph   = SIGN_EMOJIS.get(sign, "")
    wm_font = _glyph_font(360)
    try:
        wm_w = _tw(glyph, wm_font)
        ov   = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(ov).text(
            ((WIDTH - wm_w) // 2, HEIGHT // 2 - 140),
            glyph, font=wm_font, fill=(*neon, 18),
        )
        img = Image.alpha_composite(img, ov)
    except Exception:
        pass

    draw = ImageDraw.Draw(img)
    _stars(draw, seed=hash(sign) % 65536)

    # Gold bars
    draw.rectangle([0, 0, WIDTH, 6], fill=GOLD)
    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=GOLD)

    # Header strip
    draw.rectangle([0, 0, WIDTH, 224], fill=(0, 0, 0, 150))
    draw.rectangle([0, 224, WIDTH, 230], fill=GOLD)
    # Draw name in the display font, flanking glyphs in a glyph-capable font,
    # so the zodiac symbols never render as tofu when Cinzel is installed.
    hf     = _font(84, bold=True)
    gf     = _glyph_font(84)
    name   = sign.upper()
    gap    = 28
    name_w = _tw(name, hf)
    gly_w  = _tw(glyph, gf)
    total  = gly_w + gap + name_w + gap + gly_w
    x0     = (WIDTH - total) // 2
    parts  = [(glyph, gf, gly_w), (name, hf, name_w), (glyph, gf, gly_w)]
    for dx, dy, col in [(3, 57, (0, 0, 0, 155)), (0, 54, GOLD)]:
        gx = x0 + dx
        for txt, fnt, w in parts:
            draw.text((gx, dy), txt, font=fnt, fill=col)
            gx += w + gap

    # ── Content ────────────────────────────────────────────────────────────────
    y   = 248
    lf  = _font(50, bold=True)    # category label font
    vf  = _font(64, bold=False)   # value font
    lh_ = _th(lf)
    vh_ = _th(vf)

    def field(label: str, value: str, val_col=WHITE):
        nonlocal y
        draw.text((PAD, y), label, font=lf, fill=GOLD)
        y += lh_ + 8
        for ln in _wrap(value, vf, CW - 20):
            draw.text((PAD + 16, y), ln, font=vf, fill=val_col)
            y += vh_ + 4
        y += 20

    field("LOVE",   fields.get("love",   "—"))
    draw.rectangle([PAD, y, WIDTH - PAD, y + 2], fill=(*GOLD, 80))
    y += 14

    field("CAREER", fields.get("career", "—"))
    draw.rectangle([PAD, y, WIDTH - PAD, y + 2], fill=(*GOLD, 80))
    y += 14

    field("MONEY",  fields.get("money",  "—"))

    # Heavy divider
    draw.rectangle([PAD, y + 4, WIDTH - PAD, y + 10], fill=(*GOLD, 200))
    y += 30

    # Lucky row — two columns
    klf  = _font(46, bold=True)
    kvf  = _font(60, bold=False)
    klh  = _th(klf)
    kvh  = _th(kvf)
    mid  = WIDTH // 2 + 20

    draw.text((PAD,   y), "LUCKY NUMBER", font=klf, fill=GOLD)
    draw.text((mid,   y), "LUCKY COLOR",  font=klf, fill=GOLD)
    y += klh + 8
    draw.text((PAD + 16, y), str(fields.get("lucky_number", "?")), font=kvf, fill=neon)
    draw.text((mid  + 16, y), str(fields.get("lucky_color",  "?")), font=kvf, fill=neon)
    y += kvh + 28

    # Heavy divider
    draw.rectangle([PAD, y, WIDTH - PAD, y + 6], fill=(*GOLD, 200))
    y += 22

    field("TODAY'S MESSAGE", fields.get("note", "—"))

    # ── Progress dots ──────────────────────────────────────────────────────────
    dot_r  = 9
    dot_sp = 30
    total_w = 12 * dot_r * 2 + 11 * (dot_sp - dot_r * 2)
    dot_x0  = (WIDTH - total_w) // 2
    dot_y   = HEIGHT - 148
    for i in range(12):
        cx = dot_x0 + i * dot_sp + dot_r
        if i == idx:
            draw.ellipse([cx - dot_r - 3, dot_y - dot_r - 3,
                          cx + dot_r + 3, dot_y + dot_r + 3], fill=GOLD)
        else:
            draw.ellipse([cx - dot_r, dot_y - dot_r,
                          cx + dot_r, dot_y + dot_r],
                         outline=(*SILVER, 130), width=2)

    # Footer
    draw.rectangle([0, HEIGHT - 110, WIDTH, HEIGHT - 6], fill=(0, 0, 0, 140))
    ff  = _font(44, bold=False)
    tag = f"{CHANNEL_TAG}  •  {idx + 1} of 12"
    fw  = _tw(tag, ff)
    draw.text(((WIDTH - fw) // 2, HEIGHT - 90), tag, font=ff, fill=(*SILVER, 230))

    return img.convert("RGB")


# ── Thumbnail (1280×720) ───────────────────────────────────────────────────────
def render_thumbnail(date_str: str, out_path: str) -> None:
    TW, TH = 1280, 720
    top, bot = (5, 2, 18), (12, 5, 35)
    a = np.zeros((TH, TW, 3), dtype=np.float32)
    ys = np.linspace(0, 1, TH)[:, None]
    for c in range(3):
        a[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    img  = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(img)

    rng = random.Random(7)
    for _ in range(200):
        x, y = rng.randint(0, TW), rng.randint(0, TH)
        b    = rng.randint(90, 210)
        r    = rng.choice([1, 1, 2])
        col  = (b, int(b * 0.84), 0) if rng.random() < 0.1 else (b, b, b)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)

    draw.rectangle([0, 0, TW, 6], fill=GOLD)
    draw.rectangle([0, TH - 6, TW, TH], fill=GOLD)

    # Title
    f_big = _font(128, bold=True)
    title = "DAILY HOROSCOPES"
    tw_   = _tw(title, f_big)
    tx    = (TW - tw_) // 2
    draw.text((tx + 3, 44), title, font=f_big, fill=(0, 0, 0, 160))
    draw.text((tx,     41), title, font=f_big, fill=GOLD)

    draw.rectangle([40, 196, TW - 40, 202], fill=(*GOLD, 200))

    # Zodiac glyphs in one row
    glyphs = [SIGN_EMOJIS[s] for s in SIGNS]
    f_g    = _glyph_font(58)
    total_gw = sum(_tw(g, f_g) for g in glyphs) + 11 * 18
    gx = (TW - total_gw) // 2
    gy = 220
    for g in glyphs:
        draw.text((gx, gy), g, font=f_g, fill=GOLD)
        gx += _tw(g, f_g) + 18

    draw.rectangle([40, 316, TW - 40, 320], fill=(*GOLD, 200))

    # Date + subtitle
    f_sm = _font(46, bold=False)
    dw   = _tw(date_str, f_sm)
    draw.text(((TW - dw) // 2, 336), date_str, font=f_sm, fill=SILVER)

    f_desc = _font(44, bold=False)
    desc   = "Love  •  Career  •  Money  •  Lucky Number & Color"
    dw2    = _tw(desc, f_desc)
    draw.text(((TW - dw2) // 2, 402), desc, font=f_desc, fill=WHITE)

    # CTA button
    f_cta = _font(54, bold=True)
    cta   = "DAILY UPDATES"
    cw    = _tw(cta, f_cta)
    cx    = (TW - cw) // 2
    draw.rectangle([cx - 22, 488, cx + cw + 22, 556], fill=GOLD)
    draw.text((cx, 492), cta, font=f_cta, fill=(0, 0, 0))

    f_ch = _font(40, bold=False)
    chw  = _tw(CHANNEL_TAG, f_ch)
    draw.text(((TW - chw) // 2, 590), CHANNEL_TAG, font=f_ch, fill=SILVER)

    img.save(out_path, "JPEG", quality=95)
    print(f"[INFO] Thumbnail → {out_path}")


# ── Audio ──────────────────────────────────────────────────────────────────────
def _generate_ambient(duration: float, out_path: str) -> bool:
    fade_start = max(1, int(duration) - 5)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=55:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=82:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=110:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=165:sample_rate=44100",
        "-filter_complex",
        f"[0:a]volume=0.35[a0];[1:a]volume=0.22[a1];[2:a]volume=0.18[a2];[3:a]volume=0.12[a3];"
        f"[a0][a1][a2][a3]amix=inputs=4:duration=shortest[mix];"
        f"[mix]afade=t=in:ss=0:d=5,afade=t=out:st={fade_start}:d=5[out]",
        "-map", "[out]", "-t", str(duration), out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=90)
        return r.returncode == 0
    except Exception:
        return False


# ── Voice narration (edge-tts, free) ──────────────────────────────────────────
def _voice_script(sign: str, fields: dict) -> str:
    love   = fields.get("love",   "")
    career = fields.get("career", "")
    money  = fields.get("money",  "")
    num    = fields.get("lucky_number", "")
    color  = fields.get("lucky_color",  "")
    note   = fields.get("note",   "")
    return (
        f"{sign.title()}. "
        f"Love: {love}. "
        f"Career: {career}. "
        f"Money: {money}. "
        f"Lucky number {num}, lucky color {color}. "
        f"{note}."
    )


# A rotation of pleasant, free English neural voices so all 12 signs don't sound
# identical. Assigned deterministically by sign index.
VOICES = [
    "en-US-AriaNeural",     "en-GB-SoniaNeural",   "en-US-JennyNeural",
    "en-IE-EmilyNeural",    "en-AU-NatashaNeural", "en-US-MichelleNeural",
    "en-GB-LibbyNeural",    "en-CA-ClaraNeural",   "en-US-AnaNeural",
    "en-GB-MaisieNeural",   "en-US-SaraNeural",    "en-AU-CarlyNeural",
]
DEFAULT_VOICE = "en-IE-EmilyNeural"


async def _tts_async(text: str, out_path: str, rate: str = "+0%",
                     voice: str = DEFAULT_VOICE) -> bool:
    try:
        import edge_tts
        comm = edge_tts.Communicate(text, voice=voice, rate=rate)
        await comm.save(out_path)
        return Path(out_path).exists() and Path(out_path).stat().st_size > 512
    except Exception as e:
        print(f"[WARN] edge-tts ({voice}): {e}", file=sys.stderr)
        # Retry once with the default voice in case a rotation voice is unavailable.
        if voice != DEFAULT_VOICE:
            try:
                import edge_tts
                await edge_tts.Communicate(text, voice=DEFAULT_VOICE, rate=rate).save(out_path)
                return Path(out_path).exists() and Path(out_path).stat().st_size > 512
            except Exception:
                pass
        return False


def _audio_dur(path: str) -> float:
    """Duration in seconds via ffprobe, or 0.0 on failure."""
    try:
        r = _sp.run(
            ["ffprobe", "-v", "quiet", "-of", "csv=p=0",
             "-show_entries", "format=duration", path],
            capture_output=True, text=True, timeout=20,
        )
        return float(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else 0.0
    except Exception:
        return 0.0


# All audio normalized to this layout so concat never fails on a mismatch.
_AR = "44100"
_AC = "2"


def _generate_silence(duration: float, out_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", str(duration),
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={_AR}",
        "-ar", _AR, "-ac", _AC,
        "-acodec", "libmp3lame", "-b:a", "64k",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _generate_sign_voice(text: str, out_path: str, target_secs: float,
                         voice: str = DEFAULT_VOICE) -> bool:
    """TTS → speed up to fit target_secs (avoids mid-sentence cut) → pad/trim exact."""
    raw = out_path.replace(".mp3", "_raw.mp3")
    ok = asyncio.run(_tts_async(text, raw, voice=voice))
    if not ok:
        Path(raw).unlink(missing_ok=True)
        return _generate_silence(target_secs, out_path)

    # If the narration overruns the slide, re-synthesize faster (cap +60%) so the
    # whole line is spoken within the window instead of being chopped off.
    dur = _audio_dur(raw)
    if dur > target_secs + 0.3:
        rate_pct = min(60, int((dur / target_secs - 1) * 100) + 3)
        asyncio.run(_tts_async(text, raw, rate=f"+{rate_pct}%", voice=voice))  # overwrite raw

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", raw,
        "-af", f"apad,atrim=end={target_secs}",
        "-ar", _AR, "-ac", _AC,
        "-acodec", "libmp3lame", "-b:a", "128k",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False
    finally:
        Path(raw).unlink(missing_ok=True)


def _concat_audio(clips: list, out_path: str) -> bool:
    inputs = []
    for c in clips:
        inputs += ["-i", c]
    n = len(clips)
    # Normalize every input's sample rate + channel layout before concat so a
    # mono TTS clip and a stereo silence clip can't break the filter.
    norm = "".join(
        f"[{i}:a]aformat=sample_rates={_AR}:channel_layouts=stereo[a{i}];"
        for i in range(n)
    )
    flt = norm + "".join(f"[a{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1[out]"
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", flt,
        "-map", "[out]",
        "-acodec", "libmp3lame", "-b:a", "128k",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


def _mix_voice_ambient(voice_path: str, ambient_path: str, out_path: str) -> bool:
    """Mix voice (85%) with ambient (15%)."""
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", voice_path, "-i", ambient_path,
        "-filter_complex",
        "[0:a]volume=0.85[v];[1:a]volume=0.15[a];[v][a]amix=inputs=2:duration=first[out]",
        "-map", "[out]",
        "-acodec", "aac", "-b:a", "128k",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0
    except Exception:
        return False


# Motion is CPU-heavy (xfade re-encodes every frame). Off by default so the
# e2-micro cron stays within its render budget; enable once on a bigger VM.
MOTION_ENABLED = os.getenv("MOTION_ENABLED", "false").lower() == "true"
XFADE_SECS     = 0.5


def _mux_audio(tmp_video: str, audio_path: str | None, out_path: str) -> bool:
    """Attach audio to a finished silent video (or move it if no audio)."""
    if audio_path and Path(audio_path).exists():
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", tmp_video, "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
            "-shortest", "-movflags", "+faststart", out_path,
        ]
        return _sp.run(cmd, capture_output=True, timeout=120).returncode == 0
    import shutil
    shutil.move(tmp_video, out_path)
    return True


def assemble_video_motion(png_files: list, durations: list,
                          audio_path: str | None, out_path: str) -> bool:
    """Crossfade each card into the next for a smooth, 'alive' feel.
    Falls back to the static assembler on any failure."""
    tmp_video = out_path.replace(".mp4", "_motion.mp4")
    n = len(png_files)
    T = XFADE_SECS
    inputs = []
    for png, dur in zip(png_files, durations):
        inputs += ["-loop", "1", "-t", str(dur + T), "-i", png]

    # Normalize every still, then chain xfades with cumulative offsets.
    parts = [
        f"[{i}:v]scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={FPS},settb=AVTB,format=yuv420p[v{i}]"
        for i in range(n)
    ]
    prev = "v0"
    offset = 0.0
    for i in range(1, n):
        offset += durations[i - 1] - T
        label = f"x{i}"
        parts.append(
            f"[{prev}][v{i}]xfade=transition=fade:duration={T}:"
            f"offset={offset:.3f}[{label}]"
        )
        prev = label
    filt = ";".join(parts)

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", filt, "-map", f"[{prev}]",
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "23", "-threads", "0", "-movflags", "+faststart", tmp_video,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=1800)
        if r.returncode != 0:
            print(f"[WARN] Motion assembly failed, using static: "
                  f"{r.stderr.decode()[-200:]}", file=sys.stderr)
            return False
        return _mux_audio(tmp_video, audio_path, out_path)
    except Exception as e:
        print(f"[WARN] Motion assembly exception, using static: {e}", file=sys.stderr)
        return False
    finally:
        Path(tmp_video).unlink(missing_ok=True)


# ── Video assembly ─────────────────────────────────────────────────────────────
def assemble_video(png_files: list, durations: list,
                   audio_path: str | None, out_path: str) -> bool:
    tmp_video = out_path.replace(".mp4", "_noaudio.mp4")
    concat_txt = out_path.replace(".mp4", "_concat.txt")

    # Write concat file (ffmpeg requires last entry repeated without duration)
    with open(concat_txt, "w") as f:
        for img_path, dur in zip(png_files, durations):
            f.write(f"file '{img_path}'\nduration {dur}\n")
        f.write(f"file '{png_files[-1]}'\n")

    cmd1 = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-vf", f"fps={FPS},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=disable",
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
        "-threads", "0",
        "-movflags", "+faststart",
        tmp_video,
    ]

    try:
        r1 = _sp.run(cmd1, capture_output=True, timeout=1800)
        if r1.returncode != 0:
            print(f"[ERROR] Video assembly: {r1.stderr.decode()[-300:]}", file=sys.stderr)
            return False

        if audio_path and Path(audio_path).exists():
            cmd2 = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", tmp_video, "-i", audio_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                "-shortest", "-movflags", "+faststart",
                out_path,
            ]
            r2 = _sp.run(cmd2, capture_output=True, timeout=120)
            ok = r2.returncode == 0
        else:
            import shutil
            shutil.move(tmp_video, out_path)
            ok = True

        return ok
    except Exception as e:
        print(f"[ERROR] assemble_video: {e}", file=sys.stderr)
        return False
    finally:
        for p in [tmp_video, concat_txt]:
            Path(p).unlink(missing_ok=True)


# ── Main pipeline ──────────────────────────────────────────────────────────────
def process(json_path: str) -> str:
    path = Path(json_path)
    if not path.exists():
        print(f"[ERROR] File not found: {json_path}", file=sys.stderr)
        sys.exit(1)

    data     = json.loads(path.read_text(encoding="utf-8"))
    date_str = data.get("date", "")
    # Extract YYYYMMDD from filename daily_horoscope_YYYYMMDD.json
    date_tag = path.stem.split("_")[-1]

    out_dir = Path("outputs") / date_tag / "DailyAll"
    out_dir.mkdir(parents=True, exist_ok=True)

    base        = f"daily_horoscope_{date_tag}"
    video_path  = str(out_dir / f"{base}.mp4")
    thumb_path  = str(out_dir / f"{base}_thumbnail.jpg")
    total_dur   = INTRO_SECS + len(SIGNS) * SIGN_SECS   # 148 seconds

    print(f"\n{'='*58}")
    print(f"  DAILY HOROSCOPE — ALL 12 SIGNS")
    print(f"  Date: {date_str}  |  {total_dur}s  ({total_dur // 60}m {total_dur % 60}s)")
    print(f"  Output: {out_dir}/")
    print(f"{'='*58}\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        png_files: list = []
        durations: list = []

        # 1. Render cards
        print("[1/4] Rendering cards...")
        intro_png = str(tmp / "00_intro.png")
        render_intro_card(date_str).save(intro_png, "PNG")
        png_files.append(intro_png)
        durations.append(INTRO_SECS)
        print(f"      [intro]  {INTRO_SECS}s")

        signs_data = data.get("signs", {})
        for idx, sign in enumerate(SIGNS):
            fields   = signs_data.get(sign, {})
            card_png = str(tmp / f"{idx + 1:02d}_{sign}.png")
            render_sign_card(sign, fields, idx).save(card_png, "PNG")
            png_files.append(card_png)
            durations.append(SIGN_SECS)
            print(f"      [{sign.title():<14}]  {SIGN_SECS}s")

        # 2. Voice narration (edge-tts, free)
        print(f"\n[2/5] Generating voice narration (edge-tts)...")
        voice_clips: list = []

        sil_path = str(tmp / "voice_00_intro.mp3")
        if _generate_silence(INTRO_SECS, sil_path):
            voice_clips.append(sil_path)
            print(f"      [intro]  {INTRO_SECS}s silence")
        else:
            voice_clips.append(None)

        for idx, sign in enumerate(SIGNS):
            fields = signs_data.get(sign, {})
            script = _voice_script(sign, fields)
            voice  = VOICES[idx % len(VOICES)]
            vpath  = str(tmp / f"voice_{idx + 1:02d}_{sign}.mp3")
            if _generate_sign_voice(script, vpath, SIGN_SECS, voice=voice):
                voice_clips.append(vpath)
                print(f"      [{sign.title():<14}]  {SIGN_SECS}s  ({voice.split('-',2)[-1]})")
            else:
                sil2 = str(tmp / f"sil_{idx + 1:02d}.mp3")
                _generate_silence(SIGN_SECS, sil2)
                voice_clips.append(sil2)
                print(f"      [{sign.title():<14}]  (TTS failed, silence)")

        voice_concat_path = str(tmp / "voice_all.mp3")
        valid_clips = [c for c in voice_clips if c and Path(c).exists()]
        voice_ok = (len(valid_clips) == len(voice_clips) and
                    _concat_audio(valid_clips, voice_concat_path))
        if voice_ok:
            print("      Voice concat OK")
        else:
            print("      [WARN] Voice concat failed — falling back to ambient-only")

        # 3. Ambient music
        print(f"\n[3/5] Generating ambient music ({total_dur}s)...")
        ambient_path = str(tmp / "ambient.mp3")
        ambient_ok = _generate_ambient(total_dur, ambient_path)
        if ambient_ok:
            print("      OK")
        else:
            print("      [WARN] Ambient failed")

        # Mix voice + ambient
        if voice_ok and ambient_ok:
            mixed_path = str(tmp / "audio_final.aac")
            if _mix_voice_ambient(voice_concat_path, ambient_path, mixed_path):
                audio_path: str | None = mixed_path
                print("      Voice + ambient mixed")
            else:
                audio_path = ambient_path
                print("      [WARN] Mix failed — ambient only")
        elif ambient_ok:
            audio_path = ambient_path
        elif voice_ok:
            audio_path = voice_concat_path
        else:
            audio_path = None
            print("      [WARN] No audio — video will be silent")

        # 4. Assemble
        mode = "motion (crossfade)" if MOTION_ENABLED else "static"
        print(f"\n[4/5] Assembling {WIDTH}x{HEIGHT} @ {FPS}fps  [{mode}]...")
        ok = False
        if MOTION_ENABLED:
            ok = assemble_video_motion(png_files, durations, audio_path, video_path)
        if not ok:
            ok = assemble_video(png_files, durations, audio_path, video_path)
        if not ok:
            print("[ERROR] Assembly failed", file=sys.stderr)
            sys.exit(1)
        size_mb = os.path.getsize(video_path) / 1_048_576
        print(f"      OK — {size_mb:.1f} MB")

    # 5. Thumbnail (outside tmpdir, using persistent paths)
    print(f"\n[5/5] Thumbnail...")
    render_thumbnail(date_str, thumb_path)

    # Save metadata for uploader
    meta = {
        "title":       data.get("title", f"Daily Horoscope All 12 Signs — {date_str}"),
        "description": data.get("description", ""),
        "tags":        data.get("tags", []),
        "hashtags":    data.get("hashtags", []),
        "date":        date_tag,
        "pinned_comment": (
            f"Which sign are you? Drop it below!\n"
            f"Like + Subscribe for daily cosmic guidance every morning "
            f"#horoscope #astrology #zodiac"
        ),
    }
    meta_path = str(out_dir / f"{base}_assets.json")
    Path(meta_path).write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\n[OK] Done!")
    print(f"     Video:     {video_path}  ({size_mb:.1f} MB)")
    print(f"     Thumbnail: {thumb_path}")
    print(f"     Duration:  {total_dur // 60}m {total_dur % 60}s")
    return video_path


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python3 make_daily_video.py daily_horoscope_YYYYMMDD.json")
        print("       python3 make_daily_video.py YYYYMMDD")
        sys.exit(1)

    arg = sys.argv[1]
    json_path = arg if arg.endswith(".json") else f"daily_horoscope_{arg}.json"
    process(json_path)


if __name__ == "__main__":
    main()
