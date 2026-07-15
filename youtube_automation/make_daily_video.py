#!/usr/bin/env python3
"""
make_daily_video.py
Creates a single "All 12 Signs" daily horoscope slideshow video.
Narrated cards with word-synced burned captions, ambient cosmic music.
4s intro + 12s per sign x 12 = 148s total (weekly/monthly/weeklyfull use
longer per-sign slots and an outro — see TYPE_CONFIG in generate_daily_assets.py).

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
# 4 + 12x14 = 172s (2m52s) — under the 3-minute Shorts limit, and 14s gives
# the narration room to read ALL five categories per sign at natural speed.
# Overridden per-run from the JSON's "sign_secs" (weekly=14, monthly=22).
SIGN_SECS      = 14
INTRO_SECS     = 4
OUTRO_SECS     = 10       # long-form only (deep/weeklyfull) — luckiest-sign reveal
CHANNEL_TAG    = "GetMindFuelNow"

# Content types that get the long-form outro (luckiest-sign reveal + subscribe).
_OUTRO_TYPES = ("deep", "weeklyfull")

# Set by process() from the JSON so the shared card renderer can label the
# timeframe (daily/weekly/monthly/deep) without threading params through every call.
CONTENT_TYPE   = "daily"
PERIOD_LABEL   = "TODAY"
VIDEO_FPS      = 12       # static-render fps; overwritten dynamically, see safe_static_fps()

# PROVEN CONSTRAINT (production e2-micro): 172s @ 12fps = 2064 frames renders
# reliably with ultrafast (June 25 onward). 486s @ 10fps = 4860 frames timed
# out TWICE at 40 min (2026-07-06 topic run). Every static-slideshow video
# (daily/weekly/monthly/deep/weeklyfull/topic) must therefore pick its fps
# from ACTUAL total duration, not a fixed number chosen without knowing the
# runtime — a fixed 10fps is safe at 172s and unsafe at 486s.
_SAFE_FRAME_BUDGET = 2000   # a hair under the proven 2064-frame ceiling


def safe_static_fps(total_secs: float, frame_budget: int = _SAFE_FRAME_BUDGET,
                    min_fps: int = 2, max_fps: int = 12) -> int:
    """Pick an encode fps so total frame count stays within a budget proven to
    render inside the timeout on the production VM. Content is 100% static
    per card — a card change is a hard cut regardless of frame rate — so a
    lower fps costs nothing visually, only less encode work."""
    import math
    if total_secs <= 0:
        return max_fps
    fps = math.floor(frame_budget / total_secs)
    return max(min_fps, min(max_fps, fps))


# CRITICAL: burned captions need their OWN, higher sampling rate — decoupled
# from the low background fps above. At e.g. 2-4fps (chosen purely to keep
# the expensive static-hold/zoompan render cheap), a video frame only exists
# every 0.25-0.5s; a quick 3-word caption cue often lasts LESS than that gap,
# so it can land between two sampled frames and never appear on screen at
# all — while the narration keeps playing. (Reported symptom: voice speaks
# many words, only ~3 ever show as text.) Fix: upsample to CAPTION_FPS via
# the cheap `fps=` filter (frame DUPLICATION, not re-rendering) right before
# burning subtitles, so every cue gets sampled. Duplicate frames compress to
# almost nothing in x264 (skip/P-frames), so this does not reintroduce the
# render-time cost the low background fps was chosen to avoid.
CAPTION_FPS = 15

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
# Bundled OFL fonts (assets/fonts/, licenses included) — same look everywhere:
#   Cinzel  (variable, Roman serif)  → titles & sign names
#   Poppins (geometric sans)         → labels & body text
_BUNDLED = Path(__file__).parent / "assets" / "fonts"

_dcache: dict = {}
_ucache: dict = {}


def _display_font(size: int, weight: int = 700) -> ImageFont.FreeTypeFont:
    """Title font: Cinzel (variable weight) → Marcellus → legacy chain."""
    key = (size, weight)
    if key not in _dcache:
        f = None
        p = _BUNDLED / "Cinzel-var.ttf"
        if p.exists():
            try:
                f = ImageFont.truetype(str(p), size)
                try:
                    f.set_variation_by_axes([weight])
                except Exception:
                    pass
            except Exception:
                f = None
        if f is None:
            p2 = _BUNDLED / "Marcellus-Regular.ttf"
            if p2.exists():
                try:
                    f = ImageFont.truetype(str(p2), size)
                except Exception:
                    f = None
        _dcache[key] = f or _font(size, bold=(weight >= 600))
    return _dcache[key]


def _ui_font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    """Body font: Poppins at the nearest bundled weight → legacy chain."""
    key = (size, weight)
    if key not in _ucache:
        name = ("Poppins-Bold.ttf" if weight >= 700 else
                "Poppins-SemiBold.ttf" if weight >= 600 else
                "Poppins-Medium.ttf" if weight >= 500 else
                "Poppins-Regular.ttf")
        p = _BUNDLED / name
        f = None
        if p.exists():
            try:
                f = ImageFont.truetype(str(p), size)
            except Exception:
                f = None
        _ucache[key] = f or _font(size, bold=(weight >= 600))
    return _ucache[key]


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


def _vgrad(w: int, h: int, top: tuple, bot: tuple) -> Image.Image:
    """Vertical gradient with fine dither. The cards are very dark, low-level
    gradients — the worst case for 8-bit banding; ±1 LSB noise before
    quantization removes the bands at the source for free."""
    a = np.zeros((h, w, 3), dtype=np.float32)
    ys = np.linspace(0, 1, h)[:, None]
    for c in range(3):
        a[:, :, c] = top[c] * (1 - ys) + bot[c] * ys
    a += np.random.default_rng(0).uniform(-1.0, 1.0, size=a.shape).astype(np.float32)
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _gradient_img(sign: str) -> Image.Image:
    top, bot = SIGN_GRADIENTS.get(sign, ((8, 4, 20), (16, 8, 40)))
    return _vgrad(WIDTH, HEIGHT, top, bot)


def _stars(draw: ImageDraw.Draw, seed: int = 0,
           w: int = None, h: int = None) -> None:
    """Layered starfield: dense faint dust + a few bright 4-point sparkles."""
    w, h = w or WIDTH, h or HEIGHT
    rng = random.Random(seed)
    for _ in range(300):
        x, y = rng.randint(0, w), rng.randint(0, h)
        b = rng.randint(70, 200)
        r = rng.choice([1, 1, 1, 2])
        col = (b, int(b * 0.84), 0) if rng.random() < 0.08 else (b, b, b)
        draw.ellipse([x - r, y - r, x + r, y + r], fill=col)
    for _ in range(14):     # bright sparkle stars
        x, y = rng.randint(30, w - 30), rng.randint(30, h - 30)
        s = rng.randint(7, 14)
        col = (255, 244, 214)
        draw.line([x - s, y, x + s, y], fill=col, width=2)
        draw.line([x, y - s, x, y + s], fill=col, width=2)
        draw.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(255, 255, 255))


def _cosmic_bg(w: int, h: int, top: tuple, bot: tuple,
               neon: tuple, seed: int = 0) -> Image.Image:
    """Cinematic backdrop: gradient + two blurred nebula glows in the sign's
    accent color + starfield + corner vignette. Returns RGBA."""
    from PIL import ImageFilter
    # Richer color: brighten the gradient floor so each sign's hue actually
    # reads on phone screens (the raw palette was near-black).
    bot = tuple(min(72, int(c * 1.6)) for c in bot)
    img = _vgrad(w, h, top, bot).convert("RGBA")

    # Nebula glows (drawn small + blurred = soft light)
    neb = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    nd  = ImageDraw.Draw(neb)
    rng = random.Random(seed + 7)
    cx1, cy1 = int(w * 0.82), int(h * rng.uniform(0.10, 0.22))
    cx2, cy2 = int(w * 0.12), int(h * rng.uniform(0.68, 0.82))
    nd.ellipse([cx1 - 340, cy1 - 300, cx1 + 340, cy1 + 300], fill=(*neon, 34))
    nd.ellipse([cx2 - 380, cy2 - 320, cx2 + 380, cy2 + 320], fill=(90, 60, 200, 26))
    neb = neb.filter(ImageFilter.GaussianBlur(160))
    img = Image.alpha_composite(img, neb)

    _stars(ImageDraw.Draw(img), seed=seed, w=w, h=h)

    # Vignette — darkened corners focus the eye on the content
    yy, xx = np.mgrid[0:h, 0:w]
    d2 = (((xx - w / 2) / (w / 2)) ** 2 + ((yy - h / 2) / (h / 2)) ** 2)
    alpha = np.clip((d2 - 0.55) * 110, 0, 120).astype(np.uint8)
    vig = Image.fromarray(np.dstack([np.zeros((h, w, 3), dtype=np.uint8), alpha]), "RGBA")
    img = Image.alpha_composite(img, vig)
    return img


# ── Intro card ─────────────────────────────────────────────────────────────────
def render_intro_card(date_str: str) -> Image.Image:
    img  = _cosmic_bg(WIDTH, HEIGHT, (6, 3, 22), (14, 6, 40),
                      (170, 120, 255), seed=42)
    draw = ImageDraw.Draw(img)

    draw.rectangle([0, 0, WIDTH, 8], fill=GOLD)
    draw.rectangle([0, HEIGHT - 8, WIDTH, HEIGHT], fill=GOLD)

    y = 250
    f_big = _display_font(122, weight=700)
    # Display label for the intro headline — keep it a clean single real word
    # even for compound internal type names like "weeklyfull" or "deep".
    intro_label = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY",
                  "weeklyfull": "WEEKLY", "deep": "DAILY"}.get(CONTENT_TYPE, CONTENT_TYPE.upper())
    for line in [intro_label, "HOROSCOPE"]:
        w = _tw(line, f_big)
        draw.text(((WIDTH - w) // 2 + 3, y + 3), line, font=f_big, fill=(0, 0, 0, 170))
        draw.text(((WIDTH - w) // 2,     y),     line, font=f_big, fill=GOLD)
        y += _th(f_big) + 14
    y += 26

    f_sub = _ui_font(62, 600)
    txt = "ALL 12 SIGNS"
    w = _tw(txt, f_sub)
    draw.text(((WIDTH - w) // 2, y), txt, font=f_sub, fill=WHITE)
    y += _th(f_sub) + 44

    draw.rectangle([PAD, y, WIDTH - PAD, y + 4], fill=(*GOLD, 200))
    y += 34

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
    y += 28

    draw.rectangle([PAD, y, WIDTH - PAD, y + 4], fill=(*GOLD, 200))
    y += 38

    f_date = _ui_font(52, 500)
    w = _tw(date_str, f_date)
    draw.text(((WIDTH - w) // 2, y), date_str, font=f_date, fill=SILVER)
    y += _th(f_date) + 26

    # The 5 things viewers came for
    f_cats = _ui_font(40, 500)
    cats   = "Love  •  Career  •  Money  •  Health  •  Lucky"
    w = _tw(cats, f_cats)
    draw.text(((WIDTH - w) // 2, y), cats, font=f_cats, fill=(220, 210, 255))
    y += _th(f_cats) + 56

    # ── Hook + CTA — the first 4s decide retention on Shorts ────────────────────
    f_hook = _ui_font(64, 700)
    hook   = "FIND YOUR SIGN"
    w = _tw(hook, f_hook)
    draw.text(((WIDTH - w) // 2 + 2, y + 2), hook, font=f_hook, fill=(0, 0, 0, 160))
    draw.text(((WIDTH - w) // 2,     y),     hook, font=f_hook, fill=WHITE)
    y += _th(f_hook) + 18

    f_arrow = _glyph_font(72)
    arrow   = "↓ ↓ ↓"
    w = _tw(arrow, f_arrow)
    draw.text(((WIDTH - w) // 2, y), arrow, font=f_arrow, fill=GOLD)

    # CTA pill above the channel tag
    f_cta = _ui_font(46, 700)
    cta   = "COMMENT YOUR SIGN"
    cw    = _tw(cta, f_cta)
    cx    = (WIDTH - cw) // 2
    cy    = HEIGHT - 188
    draw.rounded_rectangle([cx - 30, cy - 14, cx + cw + 30, cy + _th(f_cta) + 20],
                           radius=18, fill=GOLD)
    draw.text((cx, cy), cta, font=f_cta, fill=(10, 5, 30))

    f_ch = _ui_font(46, 500)
    w = _tw(CHANNEL_TAG, f_ch)
    draw.text(((WIDTH - w) // 2, HEIGHT - 96), CHANNEL_TAG, font=f_ch, fill=SILVER)

    return img.convert("RGB")


# ── Outro card (long-form only: deep / weeklyfull) ─────────────────────────────
# Pays off the intro's "stay to the end for the luckiest sign" promise — a
# real reveal card, not just a subscribe screen.
def render_outro_card(luckiest_sign: str) -> Image.Image:
    neon = SIGN_NEON.get((luckiest_sign or "").lower(), (170, 120, 255))
    img  = _cosmic_bg(WIDTH, HEIGHT, (6, 3, 22), (14, 6, 40), neon, seed=777)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, WIDTH, 8], fill=GOLD)
    draw.rectangle([0, HEIGHT - 8, WIDTH, HEIGHT], fill=GOLD)

    period = {"deep": "TODAY'S", "weeklyfull": "THIS WEEK'S"}.get(CONTENT_TYPE, "TODAY'S")
    y = 300
    f_lbl = _ui_font(52, 600)
    w = _tw(f"{period} LUCKIEST SIGN", f_lbl)
    draw.text(((WIDTH - w) // 2, y), f"{period} LUCKIEST SIGN", font=f_lbl, fill=(220, 210, 255))
    y += _th(f_lbl) + 50

    glyph = SIGN_EMOJIS.get((luckiest_sign or "").lower(), "★")
    gf = _glyph_font(260)
    gw = _tw(glyph, gf)
    draw.text(((WIDTH - gw) // 2, y), glyph, font=gf, fill=neon)
    y += _th(gf) + 40

    f_sign = _display_font(110, weight=700)
    name = (luckiest_sign or "Every Sign").upper()
    w = _tw(name, f_sign)
    draw.text(((WIDTH - w) // 2 + 3, y + 3), name, font=f_sign, fill=(0, 0, 0, 170))
    draw.text(((WIDTH - w) // 2,     y),     name, font=f_sign, fill=GOLD)
    y += _th(f_sign) + 70

    # Subscribe CTA
    f_cta = _ui_font(52, 700)
    cta = ("SUBSCRIBE FOR WEEKLY READINGS" if CONTENT_TYPE == "weeklyfull"
           else "SUBSCRIBE FOR DAILY READINGS")
    cw = _tw(cta, f_cta)
    cx = (WIDTH - cw) // 2
    draw.rounded_rectangle([cx - 34, y - 16, cx + cw + 34, y + _th(f_cta) + 24],
                           radius=20, fill=GOLD)
    draw.text((cx, y), cta, font=f_cta, fill=(10, 5, 30))

    f_ch = _ui_font(46, 500)
    w = _tw(CHANNEL_TAG, f_ch)
    draw.text(((WIDTH - w) // 2, HEIGHT - 96), CHANNEL_TAG, font=f_ch, fill=SILVER)
    return img.convert("RGB")


# ── Sign card ──────────────────────────────────────────────────────────────────
# The 5 things people actually check in a daily horoscope, each in its own
# glass panel: Love, Career, Money, Health, and a Lucky Guidance block
# (number • color • best time + advice).

_tofu_cache: dict = {}


def _glyph_pixels(sym: str, font) -> tuple:
    """(bbox_width, filled_pixel_count) for a rendered glyph — used to
    fingerprint a font's "missing glyph" (tofu) box, and to compare
    candidate symbols against it."""
    img = Image.new("L", (max(font.size * 2, 64),) * 2, 0)
    d = ImageDraw.Draw(img)
    bbox = d.textbbox((0, 0), sym, font=font)
    d.text((0, 0), sym, font=font, fill=255)
    filled = int((np.array(img) > 30).sum())
    return bbox[2] - bbox[0], filled


def _icon(sym: str, fallback: str, size: int) -> tuple:
    """Return (symbol, font) — validated so icons never render as tofu.

    A missing glyph still has nonzero width in most fonts (it draws a
    hollow placeholder box), so a width-only check — the previous
    implementation — passes tofu straight through. Instead, fingerprint
    this font's actual tofu box once per size (by rendering a Private Use
    Area codepoint, which is guaranteed unmapped) and reject any candidate
    symbol whose render matches that exact fingerprint."""
    gf = _glyph_font(size)
    if size not in _tofu_cache:
        _tofu_cache[size] = _glyph_pixels("", gf)
    try:
        sig = _glyph_pixels(sym, gf)
        if sig[0] > 2 and sig != _tofu_cache[size]:
            return sym, gf
    except Exception:
        pass
    return fallback, _ui_font(size, 600)


def render_sign_card(sign: str, fields: dict, idx: int) -> Image.Image:
    neon     = SIGN_NEON.get(sign, WHITE)
    top, bot = SIGN_GRADIENTS.get(sign, ((8, 4, 20), (16, 8, 40)))
    img      = _cosmic_bg(WIDTH, HEIGHT, top, bot, neon, seed=hash(sign) % 65536)

    glyph = SIGN_EMOJIS.get(sign, "")

    # Faint watermark glyph behind the panels
    wm_font = _glyph_font(430)
    try:
        wm_w = _tw(glyph, wm_font)
        ov   = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
        ImageDraw.Draw(ov).text(
            ((WIDTH - wm_w) // 2, HEIGHT // 2 - 260),
            glyph, font=wm_font, fill=(*neon, 16),
        )
        img = Image.alpha_composite(img, ov)
    except Exception:
        pass

    # ── Glass panels (translucent, rounded, neon accent bar) ──────────────────
    panels = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    pd     = ImageDraw.Draw(panels)

    P_X0, P_X1 = 44, WIDTH - 44
    y      = 256
    P_H    = 244          # four category panels
    P_GAP  = 22
    LUCK_H = 356          # lucky guidance panel

    cat_rects = []
    for _ in range(4):
        pd.rounded_rectangle([P_X0, y, P_X1, y + P_H], radius=26,
                             fill=(255, 255, 255, 16))
        pd.rounded_rectangle([P_X0, y, P_X0 + 10, y + P_H], radius=5,
                             fill=(*neon, 220))
        cat_rects.append(y)
        y += P_H + P_GAP

    luck_y = y
    pd.rounded_rectangle([P_X0, luck_y, P_X1, luck_y + LUCK_H], radius=26,
                         fill=(255, 215, 0, 22))
    pd.rounded_rectangle([P_X0, luck_y, P_X1, luck_y + 10], radius=5,
                         fill=(*GOLD, 230))

    img  = Image.alpha_composite(img, panels)
    draw = ImageDraw.Draw(img)

    # Top & bottom frame lines
    draw.rectangle([0, 0, WIDTH, 6], fill=GOLD)
    draw.rectangle([0, HEIGHT - 6, WIDTH, HEIGHT], fill=GOLD)

    # ── Header: neon glyph badge + Cinzel sign name ────────────────────────────
    hf     = _display_font(96, weight=700)
    name   = sign.upper()
    name_w = _tw(name, hf)
    bad_r  = 58
    gap    = 30
    total  = bad_r * 2 + gap + name_w
    x0     = (WIDTH - total) // 2
    bcx, bcy = x0 + bad_r, 118

    draw.ellipse([bcx - bad_r, bcy - bad_r, bcx + bad_r, bcy + bad_r],
                 fill=(0, 0, 0, 130), outline=(*neon, 255), width=5)
    bgf  = _glyph_font(66)
    gw_  = _tw(glyph, bgf)
    draw.text((bcx - gw_ // 2, bcy - _th(bgf) // 2 - 10), glyph, font=bgf, fill=neon)

    nx = x0 + bad_r * 2 + gap
    draw.text((nx + 3, 71), name, font=hf, fill=(0, 0, 0, 170))
    draw.text((nx,     68), name, font=hf, fill=GOLD)
    draw.rectangle([nx, 196, nx + name_w, 201], fill=(*neon, 220))

    # Period-label pill (TODAY / THIS WEEK / THIS MONTH), centered in the gap
    # between the name underline (~201) and the first panel (~256) so it always
    # shows regardless of the sign name's width.
    pf   = _ui_font(26, 700)
    pl_w = _tw(PERIOD_LABEL, pf)
    pill_w = pl_w + 40
    ppx = (WIDTH - pill_w) // 2
    ppy = 208
    draw.rounded_rectangle([ppx, ppy, ppx + pill_w, ppy + _th(pf) + 16],
                           radius=13, fill=(*neon, 60), outline=(*neon, 235), width=2)
    draw.text((ppx + 20, ppy + 6), PERIOD_LABEL, font=pf, fill=WHITE)

    # ── Category panels content ────────────────────────────────────────────────
    lf  = _ui_font(42, 600)      # label
    vf  = _ui_font(56, 400)      # value
    lh_ = _th(lf)
    vh_ = _th(vf)
    TXT_X  = P_X0 + 42
    TXT_W  = P_X1 - TXT_X - 30

    cats = [
        ("♥", "❤", "LOVE",   fields.get("love",   "—")),
        ("★", "*", "CAREER", fields.get("career", "—")),
        ("$", "$", "MONEY",  fields.get("money",  "—")),
        ("✚", "+", "HEALTH", fields.get("health", "—")),
    ]
    for (sym, fb, label, value), py in zip(cats, cat_rects):
        iy = py + 32
        isym, ifont = _icon(sym, fb, 40)
        draw.text((TXT_X, iy), isym, font=ifont, fill=neon)
        draw.text((TXT_X + 56, iy), label, font=lf, fill=GOLD)
        ty = py + 32 + lh_ + 20
        for ln in _wrap(str(value), vf, TXT_W)[:2]:
            draw.text((TXT_X, ty), ln, font=vf, fill=WHITE)
            ty += vh_ + 10

    # ── Lucky Guidance panel ───────────────────────────────────────────────────
    isym, ifont = _icon("☾", "★", 40)
    ly = luck_y + 30
    draw.text((TXT_X, ly), isym, font=ifont, fill=GOLD)
    draw.text((TXT_X + 56, ly), "LUCKY GUIDANCE", font=lf, fill=GOLD)
    ly += lh_ + 30

    klf = _ui_font(32, 500)      # mini label
    kvf = _ui_font(52, 600)      # mini value
    col_w = (P_X1 - P_X0) // 3
    minis = [
        ("NUMBER", str(fields.get("lucky_number", "?"))),
        ("COLOR",  str(fields.get("lucky_color",  "?"))),
        ("TIME",   str(fields.get("best_time",    "—"))),
    ]
    for i, (ml, mv) in enumerate(minis):
        cx = P_X0 + col_w * i + col_w // 2
        draw.text((cx - _tw(ml, klf) // 2, ly), ml, font=klf, fill=(*SILVER, 235))
        # shrink to fit the column if a color name is long
        f_fit, mv_w = kvf, _tw(mv, kvf)
        if mv_w > col_w - 24:
            f_fit = _ui_font(40, 600)
            mv_w  = _tw(mv, f_fit)
        draw.text((cx - mv_w // 2, ly + _th(klf) + 12), mv, font=f_fit, fill=neon)
    ly += _th(klf) + 12 + _th(kvf) + 26

    advice = str(fields.get("advice", fields.get("note", "—")))
    af = _ui_font(46, 500)
    for ln in _wrap(advice, af, TXT_W)[:2]:
        w_ = _tw(ln, af)
        draw.text(((WIDTH - w_) // 2, ly), ln, font=af, fill=WHITE)
        ly += _th(af) + 8

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
        elif i < idx:
            draw.ellipse([cx - dot_r + 2, dot_y - dot_r + 2,
                          cx + dot_r - 2, dot_y + dot_r - 2], fill=(*neon, 160))
        else:
            draw.ellipse([cx - dot_r, dot_y - dot_r,
                          cx + dot_r, dot_y + dot_r],
                         outline=(*SILVER, 130), width=2)

    # Footer
    ff  = _ui_font(40, 500)
    tag = f"{CHANNEL_TAG}  •  {idx + 1} of 12"
    fw  = _tw(tag, ff)
    draw.text(((WIDTH - fw) // 2, HEIGHT - 92), tag, font=ff, fill=(*SILVER, 235))

    return img.convert("RGB")


# ── Thumbnail (1280×720) ───────────────────────────────────────────────────────
def render_thumbnail(date_str: str, out_path: str) -> None:
    TW, TH = 1280, 720
    img  = _vgrad(TW, TH, (5, 2, 18), (12, 5, 35))
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
    f_big = _display_font(124, weight=700)
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
    f_sm = _ui_font(44, 500)
    dw   = _tw(date_str, f_sm)
    draw.text(((TW - dw) // 2, 336), date_str, font=f_sm, fill=SILVER)

    f_desc = _ui_font(42, 500)
    desc   = "Love  •  Career  •  Money  •  Health  •  Lucky Guidance"
    dw2    = _tw(desc, f_desc)
    draw.text(((TW - dw2) // 2, 402), desc, font=f_desc, fill=WHITE)

    # CTA button
    f_cta = _ui_font(52, 700)
    cta   = "DAILY UPDATES"
    cw    = _tw(cta, f_cta)
    cx    = (TW - cw) // 2
    draw.rounded_rectangle([cx - 26, 486, cx + cw + 26, 560], radius=16, fill=GOLD)
    draw.text((cx, 494), cta, font=f_cta, fill=(10, 5, 30))

    f_ch = _ui_font(38, 500)
    chw  = _tw(CHANNEL_TAG, f_ch)
    draw.text(((TW - chw) // 2, 592), CHANNEL_TAG, font=f_ch, fill=SILVER)

    img.save(out_path, "JPEG", quality=95)
    print(f"[INFO] Thumbnail → {out_path}")


# ── LANDSCAPE card renderers (weekly / weeklyfull / monthly) ──────────────────
# CHANNEL POLICY: only the DAILY horoscope ships vertical (a Short); every
# other horoscope video is a 1920x1080 regular video. These are separate
# functions rather than parameterized versions of the vertical renderers so
# the daily Short's proven layout code is untouched.
LS_W, LS_H = 1920, 1080


def render_intro_card_ls(date_str: str) -> Image.Image:
    img = _cosmic_bg(LS_W, LS_H, (6, 3, 22), (14, 6, 40), (170, 120, 255), seed=42)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, LS_W, 8], fill=GOLD)
    d.rectangle([0, LS_H - 8, LS_W, LS_H], fill=GOLD)

    intro_label = {"daily": "DAILY", "weekly": "WEEKLY", "monthly": "MONTHLY",
                   "weeklyfull": "WEEKLY", "deep": "DAILY"}.get(CONTENT_TYPE,
                                                                CONTENT_TYPE.upper())
    f_big = _display_font(116, weight=700)
    title = f"{intro_label} HOROSCOPE"
    w = _tw(title, f_big)
    d.text(((LS_W - w) // 2 + 3, 103), title, font=f_big, fill=(0, 0, 0, 170))
    d.text(((LS_W - w) // 2, 100), title, font=f_big, fill=GOLD)

    f_sub = _ui_font(56, 600)
    txt = "ALL 12 SIGNS"
    w = _tw(txt, f_sub)
    d.text(((LS_W - w) // 2, 260), txt, font=f_sub, fill=WHITE)

    d.rectangle([160, 356, LS_W - 160, 360], fill=(*GOLD, 200))

    # all 12 glyphs in a single row
    glyphs = [SIGN_EMOJIS[s] for s in SIGNS]
    f_g = _glyph_font(76)
    col_w = (LS_W - 320) // 12
    gx = 160 + col_w // 2
    for g in glyphs:
        gw = _tw(g, f_g)
        d.text((gx - gw // 2, 392), g, font=f_g, fill=GOLD)
        gx += col_w
    d.rectangle([160, 512, LS_W - 160, 516], fill=(*GOLD, 200))

    f_date = _ui_font(50, 500)
    w = _tw(date_str, f_date)
    d.text(((LS_W - w) // 2, 552), date_str, font=f_date, fill=SILVER)

    f_cats = _ui_font(40, 500)
    cats = "Love  •  Career  •  Money  •  Health  •  Lucky Guidance"
    w = _tw(cats, f_cats)
    d.text(((LS_W - w) // 2, 630), cats, font=f_cats, fill=(220, 210, 255))

    f_hook = _ui_font(58, 700)
    hook = "FIND YOUR SIGN"
    w = _tw(hook, f_hook)
    d.text(((LS_W - w) // 2 + 2, 724), hook, font=f_hook, fill=(0, 0, 0, 160))
    d.text(((LS_W - w) // 2, 722), hook, font=f_hook, fill=WHITE)

    f_cta = _ui_font(44, 700)
    cta = "COMMENT YOUR SIGN"
    cw = _tw(cta, f_cta)
    cx = (LS_W - cw) // 2
    d.rounded_rectangle([cx - 30, 846, cx + cw + 30, 846 + _th(f_cta) + 34],
                        radius=18, fill=GOLD)
    d.text((cx, 860), cta, font=f_cta, fill=(10, 5, 30))

    f_ch = _ui_font(44, 500)
    w = _tw(CHANNEL_TAG, f_ch)
    d.text(((LS_W - w) // 2, LS_H - 84), CHANNEL_TAG, font=f_ch, fill=SILVER)
    return img.convert("RGB")


def render_sign_card_ls(sign: str, fields: dict, idx: int) -> Image.Image:
    """Landscape sign card: header row (badge+name left, period pill right),
    2x2 grid of the category glass panels, full-width Lucky Guidance panel,
    progress dots + footer. Captions burn over the lower band."""
    neon = SIGN_NEON.get(sign, WHITE)
    top, bot = SIGN_GRADIENTS.get(sign, ((8, 4, 20), (16, 8, 40)))
    img = _cosmic_bg(LS_W, LS_H, top, bot, neon, seed=hash(sign) % 65536)
    glyph = SIGN_EMOJIS.get(sign, "")

    # faint watermark glyph, right of center so panels stay readable
    try:
        wm_font = _glyph_font(560)
        wm_w = _tw(glyph, wm_font)
        ov = Image.new("RGBA", (LS_W, LS_H), (0, 0, 0, 0))
        ImageDraw.Draw(ov).text((LS_W - wm_w - 120, LS_H // 2 - 280),
                                glyph, font=wm_font, fill=(*neon, 14))
        img = Image.alpha_composite(img, ov)
    except Exception:
        pass

    # glass panels: 2x2 categories + full-width lucky guidance
    panels = Image.new("RGBA", (LS_W, LS_H), (0, 0, 0, 0))
    pd = ImageDraw.Draw(panels)
    P_X0, COL_GAP = 70, 40
    col_w = (LS_W - 2 * P_X0 - COL_GAP) // 2
    P_H, ROW_GAP = 182, 24
    rows_y = [206, 206 + P_H + ROW_GAP]
    cat_boxes = []
    for r in range(2):
        for c in range(2):
            x0 = P_X0 + c * (col_w + COL_GAP)
            y0 = rows_y[r]
            pd.rounded_rectangle([x0, y0, x0 + col_w, y0 + P_H], radius=24,
                                 fill=(255, 255, 255, 16))
            pd.rounded_rectangle([x0, y0, x0 + 10, y0 + P_H], radius=5,
                                 fill=(*neon, 220))
            cat_boxes.append((x0, y0))
    luck_y, LUCK_H = rows_y[1] + P_H + 26, 210
    pd.rounded_rectangle([P_X0, luck_y, LS_W - P_X0, luck_y + LUCK_H], radius=24,
                         fill=(255, 215, 0, 22))
    pd.rounded_rectangle([P_X0, luck_y, LS_W - P_X0, luck_y + 10], radius=5,
                         fill=(*GOLD, 230))
    img = Image.alpha_composite(img, panels)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, LS_W, 6], fill=GOLD)
    draw.rectangle([0, LS_H - 6, LS_W, LS_H], fill=GOLD)

    # header: badge + name, left
    hf = _display_font(84, weight=700)
    name = sign.upper()
    bad_r = 52
    bcx, bcy = P_X0 + bad_r, 100
    draw.ellipse([bcx - bad_r, bcy - bad_r, bcx + bad_r, bcy + bad_r],
                 fill=(0, 0, 0, 130), outline=(*neon, 255), width=5)
    bgf = _glyph_font(58)
    gw_ = _tw(glyph, bgf)
    draw.text((bcx - gw_ // 2, bcy - _th(bgf) // 2 - 8), glyph, font=bgf, fill=neon)
    nx = P_X0 + bad_r * 2 + 28
    draw.text((nx + 3, 61), name, font=hf, fill=(0, 0, 0, 170))
    draw.text((nx, 58), name, font=hf, fill=GOLD)
    draw.rectangle([nx, 168, nx + _tw(name, hf), 173], fill=(*neon, 220))

    # period pill, right
    pf = _ui_font(30, 700)
    pl_w = _tw(PERIOD_LABEL, pf)
    ppx = LS_W - P_X0 - pl_w - 44
    draw.rounded_rectangle([ppx, 72, ppx + pl_w + 44, 72 + _th(pf) + 20],
                           radius=14, fill=(*neon, 60), outline=(*neon, 235), width=2)
    draw.text((ppx + 22, 80), PERIOD_LABEL, font=pf, fill=WHITE)

    # category panel contents
    lf = _ui_font(38, 600)
    vf2 = _ui_font(46, 400)
    cats = [
        ("♥", "❤", "LOVE",   fields.get("love",   "—")),
        ("★", "*", "CAREER", fields.get("career", "—")),
        ("$", "$", "MONEY",  fields.get("money",  "—")),
        ("✚", "+", "HEALTH", fields.get("health", "—")),
    ]
    for (sym, fb, label, value), (x0, y0) in zip(cats, cat_boxes):
        tx = x0 + 36
        iy = y0 + 22
        isym, ifont = _icon(sym, fb, 34)
        draw.text((tx, iy), isym, font=ifont, fill=neon)
        draw.text((tx + 48, iy), label, font=lf, fill=GOLD)
        ty = y0 + 22 + _th(lf) + 14
        for ln in _wrap(str(value), vf2, col_w - 70)[:2]:
            draw.text((tx, ty), ln, font=vf2, fill=WHITE)
            ty += _th(vf2) + 6

    # lucky guidance
    isym, ifont = _icon("☾", "★", 34)
    ly = luck_y + 20
    draw.text((P_X0 + 36, ly), isym, font=ifont, fill=GOLD)
    draw.text((P_X0 + 92, ly), "LUCKY GUIDANCE", font=lf, fill=GOLD)
    klf = _ui_font(28, 500)
    kvf = _ui_font(44, 600)
    minis = [("NUMBER", str(fields.get("lucky_number", "?"))),
             ("COLOR",  str(fields.get("lucky_color",  "?"))),
             ("TIME",   str(fields.get("best_time",    "—")))]
    mini_w = (LS_W - 2 * P_X0 - 700) // 3
    for i, (ml, mv) in enumerate(minis):
        cx = P_X0 + 700 + mini_w * i + mini_w // 2
        draw.text((cx - _tw(ml, klf) // 2, luck_y + 24), ml, font=klf,
                  fill=(*SILVER, 235))
        f_fit = kvf if _tw(mv, kvf) <= mini_w - 24 else _ui_font(34, 600)
        draw.text((cx - _tw(mv, f_fit) // 2, luck_y + 24 + _th(klf) + 10), mv,
                  font=f_fit, fill=neon)
    advice = str(fields.get("advice", fields.get("note", "—")))
    af = _ui_font(40, 500)
    ay = luck_y + 118
    for ln in _wrap(advice, af, LS_W - 2 * P_X0 - 90)[:2]:
        draw.text((P_X0 + 36, ay), ln, font=af, fill=WHITE)
        ay += _th(af) + 6

    # progress dots + footer
    dot_r, dot_sp = 8, 28
    total_w = 12 * dot_r * 2 + 11 * (dot_sp - dot_r * 2)
    dot_x0, dot_y = (LS_W - total_w) // 2, LS_H - 122
    for i in range(12):
        cx = dot_x0 + i * dot_sp + dot_r
        if i == idx:
            draw.ellipse([cx - dot_r - 3, dot_y - dot_r - 3,
                          cx + dot_r + 3, dot_y + dot_r + 3], fill=GOLD)
        elif i < idx:
            draw.ellipse([cx - dot_r + 2, dot_y - dot_r + 2,
                          cx + dot_r - 2, dot_y + dot_r - 2], fill=(*neon, 160))
        else:
            draw.ellipse([cx - dot_r, dot_y - dot_r, cx + dot_r, dot_y + dot_r],
                         outline=(*SILVER, 130), width=2)
    ff = _ui_font(36, 500)
    tag = f"{CHANNEL_TAG}  •  {idx + 1} of 12"
    fw = _tw(tag, ff)
    draw.text(((LS_W - fw) // 2, LS_H - 78), tag, font=ff, fill=(*SILVER, 235))
    return img.convert("RGB")


def render_outro_card_ls(luckiest_sign: str) -> Image.Image:
    neon = SIGN_NEON.get((luckiest_sign or "").lower(), (170, 120, 255))
    img = _cosmic_bg(LS_W, LS_H, (6, 3, 22), (14, 6, 40), neon, seed=777)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, LS_W, 8], fill=GOLD)
    draw.rectangle([0, LS_H - 8, LS_W, LS_H], fill=GOLD)

    period = {"deep": "TODAY'S", "weeklyfull": "THIS WEEK'S"}.get(CONTENT_TYPE, "TODAY'S")
    f_lbl = _ui_font(50, 600)
    lbl = f"{period} LUCKIEST SIGN"
    w = _tw(lbl, f_lbl)
    draw.text(((LS_W - w) // 2, 120), lbl, font=f_lbl, fill=(220, 210, 255))

    glyph = SIGN_EMOJIS.get((luckiest_sign or "").lower(), "★")
    gf = _glyph_font(300)
    gw = _tw(glyph, gf)
    draw.text(((LS_W - gw) // 2, 230), glyph, font=gf, fill=neon)

    f_sign = _display_font(104, weight=700)
    name = (luckiest_sign or "Every Sign").upper()
    w = _tw(name, f_sign)
    draw.text(((LS_W - w) // 2 + 3, 653), name, font=f_sign, fill=(0, 0, 0, 170))
    draw.text(((LS_W - w) // 2, 650), name, font=f_sign, fill=GOLD)

    f_cta = _ui_font(48, 700)
    cta = ("SUBSCRIBE FOR WEEKLY READINGS" if CONTENT_TYPE == "weeklyfull"
           else "SUBSCRIBE FOR DAILY READINGS")
    cw = _tw(cta, f_cta)
    cx = (LS_W - cw) // 2
    draw.rounded_rectangle([cx - 34, 824, cx + cw + 34, 824 + _th(f_cta) + 36],
                           radius=20, fill=GOLD)
    draw.text((cx, 840), cta, font=f_cta, fill=(10, 5, 30))

    f_ch = _ui_font(44, 500)
    w = _tw(CHANNEL_TAG, f_ch)
    draw.text(((LS_W - w) // 2, LS_H - 84), CHANNEL_TAG, font=f_ch, fill=SILVER)
    return img.convert("RGB")


# ── Audio ──────────────────────────────────────────────────────────────────────
def _generate_ambient(duration: float, out_path: str) -> bool:
    """Cosmic pad: mid-register detuned major triad (A3+C#4+A4, with a 0.5 Hz
    beating pair for shimmer) + band-passed pink-noise 'air', slow tremolo.

    Replaces the old 55-165 Hz sine stack: phone speakers (the dominant Shorts
    device) reproduce almost nothing below ~300 Hz, and 165 Hz sat right in the
    female-TTS fundamental range, masking the voice. normalize=0 keeps the
    explicit weights (default amix scales every input by 1/n).
    Output is WAV so no lossy generation is added before the final AAC encode."""
    fade_start = max(1, int(duration) - 5)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=220:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=277.18:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=440:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "sine=frequency=440.5:sample_rate=44100",
        "-f", "lavfi", "-t", str(duration + 2), "-i", "anoisesrc=color=pink:amplitude=0.15:sample_rate=44100",
        "-filter_complex",
        f"[4:a]highpass=f=300,lowpass=f=1200[nz];"
        f"[0:a][1:a][2:a][3:a][nz]amix=inputs=5:duration=shortest:normalize=0:"
        f"weights='0.20 0.14 0.08 0.08 0.35'[mix];"
        f"[mix]tremolo=f=0.1:d=0.5,afade=t=in:ss=0:d=5,afade=t=out:st={fade_start}:d=5[out]",
        "-map", "[out]", "-t", str(duration),
        "-ar", _AR, "-ac", _AC, "-c:a", "pcm_s16le",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            print(f"[WARN] Ambient: {r.stderr.decode()[-200:]}", file=sys.stderr)
        return r.returncode == 0
    except Exception as e:
        print(f"[WARN] Ambient exception: {e}", file=sys.stderr)
        return False


# ── Voice narration (edge-tts, free) ──────────────────────────────────────────
def _voice_script(sign: str, fields: dict) -> str:
    """Complete narration: ALL five categories per sign. Budget: fields are
    generated at max ~6 words each (see generate_daily_assets SYSTEM_PROMPT),
    so the full script is ~40 words ≈ 15s — fits the 14s slot with at most a
    small (+10-15%) rate adjustment, never chipmunk speed.

    For the long-form 'deep' type, read the rich ~85-word 'reading' paragraph
    instead (fills the 40s slot with a real in-depth reading)."""
    reading = fields.get("reading")
    if reading and str(reading).strip():
        return f"{sign.title()}. {str(reading).strip()}"
    love   = fields.get("love",   "")
    career = fields.get("career", "")
    money  = fields.get("money",  "")
    health = fields.get("health", "")
    num    = fields.get("lucky_number", "")
    color  = fields.get("lucky_color",  "")
    btime  = fields.get("best_time",    "")
    advice = fields.get("advice", fields.get("note", ""))

    parts = [f"{sign.title()}."]
    if love:   parts.append(f"Love: {love}.")
    if career: parts.append(f"Career: {career}.")
    if money:  parts.append(f"Money: {money}.")
    if health: parts.append(f"Health: {health}.")
    lucky_bits = ", ".join(str(x) for x in (num, color, btime) if x)
    if lucky_bits:
        parts.append(f"Lucky: {lucky_bits}.")
    if advice: parts.append(f"{advice}.")
    return " ".join(parts)


def _intro_script(date_str: str) -> str:
    """The first seconds decide the swipe — never open with dead air."""
    if CONTENT_TYPE == "deep":
        return (f"Welcome to your complete horoscope for {date_str}. "
                f"A full, in-depth reading for all twelve zodiac signs. "
                f"Use the chapters to jump to your sign — and stay to the end "
                f"for today's luckiest sign.")
    if CONTENT_TYPE == "weeklyfull":
        return (f"Welcome to your week ahead — the full, in-depth horoscope "
                f"for all twelve zodiac signs for {date_str}. "
                f"Use the chapters to jump to your sign — and stay to the end "
                f"for this week's luckiest sign.")
    tf = {"daily": "today", "weekly": "this week",
          "monthly": "this month"}.get(CONTENT_TYPE, "today")
    return f"Your {CONTENT_TYPE} horoscope for {tf}. Find your sign."


def _outro_script(luckiest: str) -> str:
    if CONTENT_TYPE == "weeklyfull":
        lucky = f"This week's luckiest sign is {luckiest}. " if luckiest else ""
        return (f"{lucky}Thank you for watching your full week-ahead horoscope. "
                f"Subscribe for an in-depth weekly reading every Monday, and "
                f"check the pinned comment for every sign's lucky guidance. "
                f"See you next week.")
    lucky = f"Today's luckiest sign is {luckiest}. " if luckiest else ""
    return (f"{lucky}Thank you for watching. Subscribe for your complete "
            f"horoscope every morning, and check the pinned comment for every "
            f"sign's lucky numbers. See you tomorrow.")


# Curated ADULT neural voices (no child voices — en-US-Ana and en-GB-Maisie are
# Microsoft's child voices and must not read horoscopes). ONE voice per day,
# rotated by date: a consistent narrator inside each video reads as production
# value; per-sign accent whiplash reads as randomness.
VOICES = [
    "en-US-AriaNeural",   "en-GB-SoniaNeural",  "en-US-JennyNeural",
    "en-IE-EmilyNeural",  "en-AU-NatashaNeural", "en-US-MichelleNeural",
    "en-GB-LibbyNeural",  "en-CA-ClaraNeural",  "en-US-AvaNeural",
    # en-US-SaraNeural WAS the 11th slot: Microsoft retired it from the free
    # edge-tts endpoint (confirmed dead 2026-07-14 — NoAudioReceived on every
    # call while Emily/Emma worked; it killed all three of that day's videos).
    # Microsoft retires voices without notice, so any pool member can die:
    # _tts_synth below survives that with a fallback cascade. Emma (proven
    # alive 2026-07-13) fills the slot — a duplicate pool entry is harmless.
    "en-US-EmmaNeural",   "en-US-EmmaNeural",   "en-AU-CarlyNeural",
]
DEFAULT_VOICE = "en-IE-EmilyNeural"


def _day_voice(date_tag: str) -> str:
    """Deterministic voice-of-the-day."""
    try:
        return VOICES[int(date_tag) % len(VOICES)]
    except Exception:
        return DEFAULT_VOICE


# ── TTS with voice fallback ────────────────────────────────────────────────────
# A retired/dead voice raises NoAudioReceived on EVERY call, so retrying the
# same voice is useless — the recovery is a DIFFERENT voice. The override is
# sticky for the process lifetime: once the day's voice proves dead, every
# later segment goes straight to the working voice (consistent narrator for
# the whole video, no doomed re-attempts on all 13 segments).
_TTS_FALLBACKS = [DEFAULT_VOICE, "en-US-AriaNeural", "en-US-JennyNeural",
                  "en-US-EmmaNeural"]
_VOICE_OVERRIDE = None


def _tts_synth(text: str, out_mp3: str, voice: str, rate: str = "+0%") -> tuple:
    """Synthesize text with automatic voice fallback.
    Returns (ok, word_cues, used_voice). Never raises."""
    global _VOICE_OVERRIDE
    first = _VOICE_OVERRIDE or voice
    candidates = [first] + [v for v in _TTS_FALLBACKS if v != first]
    for cand in candidates:
        try:
            words = asyncio.run(_tts_stream_with_words(text, out_mp3, cand, rate=rate))
            ok = Path(out_mp3).exists() and Path(out_mp3).stat().st_size > 512
        except Exception as e:
            print(f"[WARN] edge-tts stream ({cand}): {e}", file=sys.stderr)
            ok = False
        if ok:
            if cand != first:
                _VOICE_OVERRIDE = cand
                print(f"[WARN] Voice {first} is unavailable — switched to {cand} "
                      f"for the rest of this run", file=sys.stderr)
            return True, words, cand
    return False, [], first


async def _tts_stream_with_words(text: str, out_mp3: str, voice: str,
                                 rate: str = "+0%") -> list:
    """Stream TTS audio while capturing real per-word timing from edge-tts's
    WordBoundary events (offset/duration in 100ns ticks). This is what makes
    burned captions land exactly on the spoken word instead of a guessed/
    uniform timing. Shared by every video maker (daily/weekly/monthly/
    weeklyfull via _generate_sign_voice below, topic via make_topic_video's
    _narrate) so there is one source of truth for the edge-tts word-boundary
    API shape.

    CRITICAL: boundary="WordBoundary" must be explicit. edge_tts.Communicate's
    own default is "SentenceBoundary" (confirmed in edge_tts/communicate.py:
    the boundary choice maps directly to `wordBoundaryEnabled`/
    `sentenceBoundaryEnabled` flags sent to the TTS websocket) — with the
    default, the server is told wordBoundaryEnabled=false and NEVER emits a
    single "WordBoundary" chunk, silently making `words` empty every time.
    That's not a degraded/sparse-caption failure mode, it's a total one:
    has_captions becomes False and the whole caption feature no-ops in
    production while still working in any test that mocks this function
    directly (as this session's verification did, until this was traced
    back to the actual edge-tts call and checked)."""
    import edge_tts
    comm = edge_tts.Communicate(text, voice=voice, rate=rate, boundary="WordBoundary")
    words = []
    with open(out_mp3, "wb") as f:
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7          # 100ns ticks -> seconds
                dur = chunk["duration"] / 1e7
                words.append((start, start + dur, str(chunk["text"])))
    return words


# ── Captions: word-boundary timing -> short TikTok/Reels-style phrase cues ───
def _group_words_into_word_cues(word_cues: list, max_words: int = 3) -> list:
    """Merge consecutive (start,end,word) tuples into short caption phrases,
    KEEPING the per-word timing: returns [(start, end, [(ws,we,word),...])].
    A cue breaks early at sentence-ending punctuation so captions align with
    natural speech rhythm. The word timings inside each cue are what powers
    the karaoke highlight in _write_karaoke_ass."""
    if not word_cues:
        return []
    cues, cur = [], []
    for start, end, word in word_cues:
        cur.append((start, end, word))
        ends_sentence = word.rstrip().endswith((".", "!", "?", ","))
        if len(cur) >= max_words or ends_sentence:
            cues.append((cur[0][0], cur[-1][1], cur))
            cur = []
    if cur:
        cues.append((cur[0][0], cur[-1][1], cur))
    return cues


def _group_words_into_cues(word_cues: list, max_words: int = 3) -> list:
    """Text-only view of _group_words_into_word_cues (SRT fallback + tests)."""
    return [(s, e, " ".join(w for _, _, w in words))
            for s, e, words in _group_words_into_word_cues(word_cues, max_words)]


def _srt_timestamp(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    ms = int(round((t - int(t)) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _write_srt(cues: list, path: str) -> bool:
    if not cues:
        return False
    lines = []
    for i, (start, end, text) in enumerate(cues, 1):
        if end <= start:
            end = start + 0.3
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
    return True


# Karaoke caption styling. ASS colors are &HAABBGGRR (blue-green-red!).
# Spoken words flip from SecondaryColour to PrimaryColour as their \k
# duration elapses — Primary=gold, Secondary=white gives the TikTok-style
# "current word lights up gold" effect, driven by the REAL edge-tts word
# timings (not an animation guess). Sizes are in the frame's own pixels
# because the ASS header declares PlayResX/PlayResY explicitly (unlike the
# SRT path, where libass applies its internal scaling — see
# _subtitle_filter's calibration note).
KARAOKE_ENABLED = os.getenv("KARAOKE_CAPTIONS", "true").lower() == "true"
_ASS_GOLD  = "&H0000D7FF"   # RGB 255,215,0
_ASS_WHITE = "&H00FFFFFF"


def _ass_timestamp(t: float) -> str:
    t = max(0.0, t)
    h = int(t // 3600); m = int((t % 3600) // 60); s = int(t % 60)
    cs = int(round((t - int(t)) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _write_karaoke_ass(word_cues_grouped: list, path: str,
                       frame_w: int, frame_h: int) -> bool:
    """word_cues_grouped: [(start, end, [(ws,we,word),...]), ...] in global
    seconds. Writes an ASS file with per-word \\k karaoke timing."""
    if not word_cues_grouped:
        return False
    # Empirically calibrated per orientation (test-rendered + screenshotted,
    # same method as the SRT sizes): values are real pixels at the declared
    # PlayResX/PlayResY.
    if frame_w >= frame_h:                 # landscape 1920x1080
        fontsize, outline, margin_v = 60, 5, 150
    else:                                   # vertical 1080x1920
        fontsize, outline, margin_v = 64, 6, 330
    header = (
        "[Script Info]\n"
        "ScriptType: v4.00+\n"
        f"PlayResX: {frame_w}\n"
        f"PlayResY: {frame_h}\n"
        "WrapStyle: 2\n"
        "ScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Cap,Poppins,{fontsize},{_ASS_GOLD},{_ASS_WHITE},"
        f"&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,{outline},0,"
        f"2,60,60,{margin_v},1\n\n"
        "[Events]\n"
        # NOTE: Effect must be declared — the Dialogue lines carry an (empty)
        # Effect field; omitting it from Format shifts a field into the text
        # and renders a stray leading comma (seen in calibration).
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [header]
    for start, end, words in word_cues_grouped:
        if end <= start or not words:
            continue
        parts = []
        for i, (ws, we, word) in enumerate(words):
            # \k durations are consumed sequentially from the cue start, so
            # each word's slice runs to the NEXT word's start (folding the
            # inter-word gap in), keeping the highlight on the real clock.
            nxt = words[i + 1][0] if i + 1 < len(words) else end
            k_cs = max(1, int(round((nxt - ws) * 100)))
            text = str(word).replace("{", "").replace("}", "").replace("\n", " ")
            parts.append("{\\k%d}%s" % (k_cs, text))
        lines.append(f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},"
                     f"Cap,,0,0,0,,{' '.join(parts)}\n")
    Path(path).write_text("".join(lines), encoding="utf-8")
    return True


def _write_captions(word_cues_grouped: list, base_path: str,
                    frame_w: int = None, frame_h: int = None) -> tuple:
    """Single entry point for every video maker: writes the karaoke .ass
    (default) or the plain .srt fallback from grouped word cues, and returns
    (caption_path, has_captions). Any ASS failure falls back to SRT — the
    caption feature degrades, never dies."""
    frame_w = frame_w or WIDTH
    frame_h = frame_h or HEIGHT
    if not word_cues_grouped:
        return None, False
    if KARAOKE_ENABLED:
        try:
            ass_path = f"{base_path}.ass"
            if _write_karaoke_ass(word_cues_grouped, ass_path, frame_w, frame_h):
                return ass_path, True
        except Exception as e:
            print(f"[WARN] karaoke ASS write failed ({e}) — falling back to SRT",
                  file=sys.stderr)
    srt_path = f"{base_path}.srt"
    text_cues = [(s, e, " ".join(w for _, _, w in words))
                 for s, e, words in word_cues_grouped]
    return (srt_path, True) if _write_srt(text_cues, srt_path) else (None, False)


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


# Intermediates are WAV (pcm_s16le): the old chain re-encoded the voice through
# 4 lossy mp3/aac generations before upload. Now AAC is encoded exactly once.

def _generate_silence(duration: float, out_path: str) -> bool:
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-t", str(duration),
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={_AR}",
        "-ar", _AR, "-ac", _AC,
        "-c:a", "pcm_s16le",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def _generate_sign_voice(text: str, out_path: str, target_secs: float,
                         voice: str = DEFAULT_VOICE) -> tuple:
    """TTS (capturing real word-boundary timing) → speed up slightly if
    needed → pad/trim to exactly target_secs.

    Returns (ok, word_cues): word_cues are (start, end, word) LOCAL to this
    clip (0 = clip start), clamped to target_secs — a caption must never
    reference time past the card's own dwell. edge-tts reports WordBoundary
    offsets against whichever synthesis pass actually produced the audio, so
    a sped-up retry's cues are already in the right timebase with no extra
    scaling needed.

    Rate is capped at +20%: beyond that the narration sounds rushed. The spoken
    script is sized to fit at normal speed; a big overrun means the script is
    too long and should be shortened, not chipmunked."""
    raw = out_path.rsplit(".", 1)[0] + "_raw.mp3"
    ok, words, used_voice = _tts_synth(text, raw, voice)
    if not ok:
        Path(raw).unlink(missing_ok=True)
        return _generate_silence(target_secs, out_path), []

    dur = _audio_dur(raw)
    if dur > target_secs + 0.3:
        rate_pct = min(20, int((dur / target_secs - 1) * 100) + 3)
        # Speed-up re-synthesis: MUST use the voice that actually produced the
        # audio, go to a SEPARATE file, and never raise. The old code re-used
        # the original day voice unguarded and overwrote `raw` in place — with
        # the day voice dead (en-US-SaraNeural, 2026-07-14) and the fallback's
        # audio already in hand, this exact line crashed all of that day's
        # daily renders.
        fast = raw + ".fast.mp3"
        ok2, words2, _ = _tts_synth(text, fast, used_voice, rate=f"+{rate_pct}%")
        if ok2:
            words = words2
            os.replace(fast, raw)
        else:
            Path(fast).unlink(missing_ok=True)
            # keep the normal-rate audio — apad/atrim below clamps it to the
            # card dwell, which beats no video at all

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", raw,
        "-af", f"apad,atrim=end={target_secs}",
        "-ar", _AR, "-ac", _AC,
        "-c:a", "pcm_s16le",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=30)
        if r.returncode != 0:
            return False, []
        clamped = [(s, min(e, target_secs), w) for s, e, w in words if s < target_secs]
        return True, clamped
    except Exception:
        return False, []
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
        "-c:a", "pcm_s16le",
        out_path,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=300)
        if r.returncode != 0:
            print(f"[WARN] Voice concat: {r.stderr.decode()[-200:]}", file=sys.stderr)
        return r.returncode == 0
    except Exception as e:
        print(f"[WARN] Voice concat exception: {e}", file=sys.stderr)
        return False


def _mix_voice_ambient(voice_path: str, ambient_path: str, out_path: str) -> bool:
    """Mix voice over the ambient bed and master to YouTube's loudness target.

    Two tiers:
      1. amix normalize=0 + loudnorm to -14 LUFS (best; needs ffmpeg >= 4.3
         and enough CPU — loudnorm resamples internally and is heavy on a
         throttled vCPU, hence the generous timeout).
      2. Compatibility mix that works on ANY ffmpeg and is cheap: pre-scale
         the inputs 2x/0.7x so amix's built-in 1/n halving lands at exactly
         1.0 voice / 0.35 ambient. No loudnorm, but voice at full scale.
    Never returns a music-only result — the caller falls back to voice."""
    # loudnorm resamples to 192 kHz internally; on ffmpeg 4.4 an in-graph
    # `aresample=44100` after it triggers "Error reinitializing filters". Fix:
    # drop the resample FILTER and let the encoder resample via the `-ar 44100`
    # OUTPUT option instead. Lower tiers are progressively simpler fallbacks.
    #
    # Tier 1 (broadcast mix): the voice gets a mastering chain — highpass to
    # remove rumble, a gentle presence lift around 3.2 kHz, and light
    # compression for even, confident delivery — and the ambient bed is
    # SIDECHAIN-DUCKED by the voice: music automatically dips while the host
    # speaks and swells back in the gaps. This is the single audio trick that
    # separates "TTS over a loop" from a produced mix.
    # Tier 1 graph is a DIAMOND (asplit feeds both amix and sidechaincompress),
    # and those two filters have disjoint native sample formats (ffmpeg 4.4:
    # amix=fltp-only, sidechaincompress=packed-dbl-only). ffmpeg >= 5.1 has a
    # rewritten negotiator that auto-converts; 4.4 (Ubuntu 22.04 — the
    # production VM) fails the whole graph with "could not choose their
    # formats / consider inserting the (a)format filter" — seen live
    # 2026-07-13. So we insert those aformat pins OURSELVES at every branch
    # point: each multi-input filter now receives explicitly identical,
    # natively-supported formats, leaving the negotiator nothing to solve.
    _DBL  = "aformat=sample_fmts=dbl:sample_rates=44100:channel_layouts=stereo"
    _FLTP = "aformat=sample_fmts=fltp"
    tiers = [
        "[0:a]highpass=f=80,equalizer=f=3200:t=q:w=1:g=2.5,"
        "acompressor=threshold=-18dB:ratio=3:attack=8:release=120:makeup=4,"
        f"{_DBL},asplit=2[v][vsc];"
        f"[1:a]volume=0.55,{_DBL}[amb];"
        "[amb][vsc]sidechaincompress=threshold=0.02:ratio=8:attack=60:release=600[duck];"
        f"[v]{_FLTP}[vf];[duck]{_FLTP}[df];"
        "[vf][df]amix=inputs=2:duration=first:normalize=0,"
        "loudnorm=I=-14:TP=-1.5:LRA=11[out]",
        f"[0:a]{_FLTP}[va];[1:a]{_FLTP}[aa];"
        "[va][aa]amix=inputs=2:duration=first:normalize=0:weights='1 0.35',"
        "loudnorm=I=-14:TP=-1.5:LRA=11[out]",
        f"[0:a]volume=2.0,{_FLTP}[v];[1:a]volume=0.7,{_FLTP}[a];"
        "[v][a]amix=inputs=2:duration=first[out]",
    ]
    for i, flt in enumerate(tiers, 1):
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", voice_path, "-i", ambient_path,
            "-filter_complex", flt,
            "-map", "[out]",
            "-ar", _AR,                      # encoder resample (4.4-safe)
            "-c:a", "aac", "-b:a", "160k",
            out_path,
        ]
        try:
            r = _sp.run(cmd, capture_output=True, timeout=600)
            if r.returncode == 0:
                if i == 2:
                    print("      [INFO] Mix tier 2 (no voice EQ / music ducking)")
                elif i == 3:
                    print("      [INFO] Mix tier 3 (compatibility — no loudness master)")
                return True
            print(f"[WARN] Mix tier {i} failed: {r.stderr.decode()[-500:]}",
                  file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Mix tier {i} exception: {e}", file=sys.stderr)
    return False


# Motion is CPU-heavy (xfade re-encodes every frame). Off by default so the
# e2-micro cron stays within its render budget; enable once on a bigger VM.
MOTION_ENABLED = os.getenv("MOTION_ENABLED", "false").lower() == "true"
XFADE_SECS     = 0.5


def _audio_mux_args(audio_path: str) -> list:
    """Stream-copy when the track is already AAC (the mixed master); encode
    once from WAV otherwise. Avoids a pointless extra lossy generation."""
    if audio_path.endswith((".m4a", ".aac")):
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "160k"]


def _mux_audio(tmp_video: str, audio_path: str | None, out_path: str) -> bool:
    """Attach audio to a finished silent video (or move it if no audio)."""
    if audio_path and Path(audio_path).exists():
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", tmp_video, "-i", audio_path,
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", *_audio_mux_args(audio_path),
            "-shortest", "-movflags", "+faststart", out_path,
        ]
        return _sp.run(cmd, capture_output=True, timeout=120).returncode == 0
    import shutil
    shutil.move(tmp_video, out_path)
    return True


def assemble_video_motion(png_files: list, durations: list,
                          audio_path: str | None, out_path: str,
                          srt_path: str | None = None,
                          frame: tuple | None = None) -> bool:
    """Crossfade each card into the next for a smooth, 'alive' feel.
    Falls back to the static assembler on any failure."""
    fw, fh = frame or (WIDTH, HEIGHT)
    tmp_video = out_path.replace(".mp4", "_motion.mp4")
    n = len(png_files)
    T = XFADE_SECS
    inputs = []
    for png, dur in zip(png_files, durations):
        inputs += ["-loop", "1", "-t", str(dur + T), "-i", png]

    # Normalize every still, then chain xfades with cumulative offsets.
    parts = [
        f"[{i}:v]scale={fw}:{fh}:force_original_aspect_ratio=disable,"
        f"setsar=1,fps={FPS},settb=AVTB,format=yuv420p[v{i}]"
        for i in range(n)
    ]
    prev = "v0"
    offset = 0.0
    for i in range(1, n):
        # Accumulate full card durations (transition overlaps the tail) so the
        # total video length stays ≈ sum(durations) and stays in sync with the
        # 148s audio track. (Using dur-T here would shrink the video ~5s and
        # progressively desync the voice from the cards.)
        offset += durations[i - 1]
        label = f"x{i}"
        parts.append(
            f"[{prev}][v{i}]xfade=transition=fade:duration={T}:"
            f"offset={offset:.3f}[{label}]"
        )
        prev = label
    filt = ";".join(parts)
    out_label = f"[{prev}]"

    if srt_path and Path(srt_path).exists():
        # FPS (24) is already well above CAPTION_FPS, so no upsample stage is
        # needed today — this guard only matters if FPS is ever lowered.
        pre = f"[{prev}]"
        if FPS < CAPTION_FPS:
            filt += f";[{prev}]fps={CAPTION_FPS}[vcap]"
            pre = "[vcap]"
        filt += f";{pre}{_subtitle_filter(srt_path)}[vout]"
        out_label = "[vout]"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", filt, "-map", out_label,
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "veryfast",
        "-crf", "20", "-threads", "0", "-movflags", "+faststart", tmp_video,
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
# Cinematic grade: a touch more saturation/contrast and a soft vignette.
# Applied at the LOW base fps (before the caption-fps upsample) so the extra
# per-frame cost stays tiny, and before the subtitle burn so text stays crisp.
# NO film grain: animated noise is nearly incompressible — measured 192 MB on
# a 74s prediction render (vs ~50 MB without), 4x the file for a subtle
# effect, blowing QC's 150 MB cap and upload bandwidth.
CINEMATIC_GRADE = os.getenv("CINEMATIC_GRADE", "true").lower() == "true"


def grade_filter() -> str:
    return "eq=saturation=1.06:contrast=1.04,vignette=PI/5"


def kenburns_expr(idx: int, frames: int) -> str:
    """A zoompan clause whose motion DIRECTION varies by card index —
    zoom-in, zoom-out, pan-right, pan-left — so consecutive cards feel
    edited rather than screensaver-uniform. Expects the input pre-scaled
    ~1.2x so the crop never leaves the canvas. Caller appends :s=WxH:fps=N
    and the trim/setpts hard-cap."""
    variant = idx % 4
    if variant == 0:      # slow zoom in, centered
        return (f"zoompan=z='1+0.10*on/{frames}':d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")
    if variant == 1:      # slow zoom out, centered
        return (f"zoompan=z='1.10-0.10*on/{frames}':d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'")
    if variant == 2:      # gentle pan left -> right at fixed zoom
        return (f"zoompan=z='1.08':d={frames}:"
                f"x='(iw-iw/zoom)*on/{frames}':y='ih/2-(ih/zoom/2)'")
    return (f"zoompan=z='1.08':d={frames}:"      # gentle pan right -> left
            f"x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'")


def _subtitle_filter(srt_path: str) -> str:
    """ffmpeg subtitles filter clause using the bundled fonts (libass), with
    the path escaped for the filter's own ':'/',' delimiters.

    A .ass file (the karaoke captions) carries its own complete style section
    with explicit PlayResX/PlayResY, so it gets NO force_style — its sizes
    are real pixels and already orientation-aware.

    For .srt, Fontsize/MarginV below are EMPIRICALLY calibrated, not literal
    pixels: this ffmpeg/libass build scales SRT-derived subtitles by a fixed
    internal factor regardless of the `original_size` option, which measurably
    had ZERO effect here (identical output bytes with/without it — verified,
    not assumed). Do not "fix" these numbers to look like sane pixel values;
    they were tuned by rendering single frames and visually checking
    placement, and land correctly THERE."""
    esc = str(srt_path).replace("\\", "\\\\").replace(":", "\\:")
    fontsdir = str(Path(__file__).parent / "assets" / "fonts").replace(":", "\\:")
    if str(srt_path).endswith(".ass"):
        return f"subtitles={esc}:fontsdir={fontsdir}"
    style = ("FontName=Poppins,Fontsize=24,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,"
             "Bold=1,Alignment=2,MarginV=55")
    return f"subtitles={esc}:fontsdir={fontsdir}:force_style='{style}'"


def assemble_video(png_files: list, durations: list,
                   audio_path: str | None, out_path: str,
                   srt_path: str | None = None,
                   frame: tuple | None = None) -> bool:
    """frame=(w,h) selects output size — default vertical WIDTHxHEIGHT (the
    daily Short); landscape types pass (LS_W, LS_H)."""
    fw, fh = frame or (WIDTH, HEIGHT)
    tmp_video = out_path.replace(".mp4", "_noaudio.mp4")
    concat_txt = out_path.replace(".mp4", "_concat.txt")

    # Write concat file (ffmpeg requires last entry repeated without duration)
    with open(concat_txt, "w") as f:
        for img_path, dur in zip(png_files, durations):
            f.write(f"file '{img_path}'\nduration {dur}\n")
        f.write(f"file '{png_files[-1]}'\n")

    # Static path encoder.
    # HARD-WON PRODUCTION CONSTRAINT (do not "improve" without testing on the
    # actual VM): the e2-micro timed out at 30-40 min with veryfast@24fps
    # (2026-07-04 05:30) AND with superfast+stillimage@12fps (2026-07-04
    # 13:29). The ONLY encoder proven to finish there is ultrafast (June 25,
    # twice, ~10-15 min at 24fps). ultrafast + 12fps + crf22 keeps that proven
    # base with half the frames; visible quality is protected by the gradient
    # dither in _vgrad (banding was the real artifact, and it's fixed at the
    # source). Set ENCODER_PRESET in .env only on a bigger machine.
    static_fps = min(FPS, VIDEO_FPS)
    preset = os.getenv("ENCODER_PRESET", "ultrafast")
    vf = f"fps={static_fps},scale={fw}:{fh}:force_original_aspect_ratio=disable"
    if CINEMATIC_GRADE:
        vf += "," + grade_filter()     # at low fps, before captions — cheap + crisp text
    if srt_path and Path(srt_path).exists():
        # Upsample to CAPTION_FPS (cheap: duplicated frames, not re-rendered)
        # BEFORE burning subtitles, so short caption cues can't fall in the
        # gap between two sparsely-sampled low-fps frames. See CAPTION_FPS.
        if static_fps < CAPTION_FPS:
            vf += f",fps={CAPTION_FPS}"
        vf += "," + _subtitle_filter(srt_path)
    cmd1 = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", concat_txt,
        "-vf", vf,
        "-pix_fmt", "yuv420p",
        "-c:v", "libx264", "-preset", preset, "-crf", "22",
        "-threads", "0",
        "-movflags", "+faststart",
        tmp_video,
    ]

    try:
        r1 = _sp.run(cmd1, capture_output=True, timeout=2400)
        if r1.returncode != 0:
            print(f"[ERROR] Video assembly: {r1.stderr.decode()[-300:]}", file=sys.stderr)
            return False

        if audio_path and Path(audio_path).exists():
            cmd2 = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-i", tmp_video, "-i", audio_path,
                "-map", "0:v", "-map", "1:a",
                "-c:v", "copy", *_audio_mux_args(audio_path),
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

    global CONTENT_TYPE, PERIOD_LABEL, VIDEO_FPS
    data     = json.loads(path.read_text(encoding="utf-8"))
    date_str = data.get("date", "")

    # Type-aware metadata (falls back to daily for old JSONs).
    CONTENT_TYPE = data.get("content_type", "daily")
    PERIOD_LABEL = data.get("period_label", "TODAY")
    sign_secs    = int(data.get("sign_secs", SIGN_SECS))

    # CHANNEL POLICY: only the daily horoscope is vertical (a Short). Every
    # other type this module renders (weekly/weeklyfull/monthly/deep) is a
    # landscape 1920x1080 regular video — landscape uploads are never
    # auto-classed as Shorts regardless of length.
    is_landscape = CONTENT_TYPE != "daily"
    frame = (LS_W, LS_H) if is_landscape else None

    # Extract YYYYMMDD from filename <type>_horoscope_YYYYMMDD.json
    date_tag = path.stem.split("_")[-1]

    out_dir = Path("outputs") / date_tag / f"{CONTENT_TYPE.capitalize()}All"
    out_dir.mkdir(parents=True, exist_ok=True)

    base        = f"{CONTENT_TYPE}_horoscope_{date_tag}"
    video_path  = str(out_dir / f"{base}.mp4")
    thumb_path  = str(out_dir / f"{base}_thumbnail.jpg")
    has_outro   = CONTENT_TYPE in _OUTRO_TYPES
    total_dur   = INTRO_SECS + len(SIGNS) * sign_secs + (OUTRO_SECS if has_outro else 0)

    # Duration-aware fps — a fixed fps is only safe at the duration it was
    # tuned for; deep/weeklyfull run ~490s and would time out at the daily
    # short's 12fps (see safe_static_fps() docstring for the proof).
    VIDEO_FPS = safe_static_fps(total_dur)

    print(f"\n{'='*58}")
    print(f"  {CONTENT_TYPE.upper()} HOROSCOPE — ALL 12 SIGNS")
    print(f"  Date: {date_str}  |  {total_dur}s  ({total_dur // 60}m {total_dur % 60}s)  |  {VIDEO_FPS}fps")
    print(f"  Output: {out_dir}/")
    print(f"{'='*58}\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        png_files: list = []
        durations: list = []

        # 1. Render cards
        print(f"[1/4] Rendering cards ({'landscape' if is_landscape else 'vertical'})...")
        intro_png = str(tmp / "00_intro.png")
        intro_card = render_intro_card_ls(date_str) if is_landscape else render_intro_card(date_str)
        intro_card.save(intro_png, "PNG")
        png_files.append(intro_png)
        durations.append(INTRO_SECS)
        print(f"      [intro]  {INTRO_SECS}s")

        signs_data = data.get("signs", {})
        for idx, sign in enumerate(SIGNS):
            fields   = signs_data.get(sign, {})
            card_png = str(tmp / f"{idx + 1:02d}_{sign}.png")
            sign_card = (render_sign_card_ls(sign, fields, idx) if is_landscape
                        else render_sign_card(sign, fields, idx))
            sign_card.save(card_png, "PNG")
            png_files.append(card_png)
            durations.append(sign_secs)
            print(f"      [{sign.title():<14}]  {sign_secs}s")

        luckiest_sign = str(data.get("luckiest_sign", "")).strip()
        if has_outro:
            outro_png = str(tmp / "99_outro.png")
            outro_card = (render_outro_card_ls(luckiest_sign) if is_landscape
                         else render_outro_card(luckiest_sign))
            outro_card.save(outro_png, "PNG")
            png_files.append(outro_png)
            durations.append(OUTRO_SECS)
            print(f"      [outro]  {OUTRO_SECS}s  (luckiest: {luckiest_sign or '—'})")

        # 2. Voice narration (edge-tts, free) — one narrator per day
        day_voice = _day_voice(date_tag)
        print(f"\n[2/5] Generating voice narration (edge-tts, voice: {day_voice})...")
        voice_clips: list = []
        caption_cues: list = []   # (start, end, [(ws,we,word),...]) in GLOBAL seconds
        t_cursor = 0.0             # card durations are FIXED, so this tracks in lockstep

        def _cue_group(words, offset):
            """Group a SINGLE segment's own words into short caption phrases
            BEFORE applying the global time offset. Grouping must never see
            words from two different segments at once — otherwise a cue can
            straddle a hard cut (e.g. a sign's last word merged with the next
            sign's first words into one caption group), so the caption still
            shows fragments of the PREVIOUS card's speech after the video has
            already visually cut to the next one. Word-level timings are kept
            so _write_captions can emit karaoke highlighting."""
            return [(offset + s, offset + e,
                     [(offset + ws, offset + we, w) for ws, we, w in wlist])
                    for s, e, wlist in _group_words_into_word_cues(words, max_words=3)]

        # Narrated intro — never open a Short with 4s of dead air.
        intro_voice = str(tmp / "voice_00_intro.wav")
        ok, words = _generate_sign_voice(_intro_script(date_str), intro_voice,
                                         INTRO_SECS, voice=day_voice)
        if ok:
            voice_clips.append(intro_voice)
            caption_cues += _cue_group(words, t_cursor)
            print(f"      [intro]  {INTRO_SECS}s narrated hook")
        else:
            voice_clips.append(None)
        t_cursor += INTRO_SECS

        for idx, sign in enumerate(SIGNS):
            fields = signs_data.get(sign, {})
            script = _voice_script(sign, fields)
            vpath  = str(tmp / f"voice_{idx + 1:02d}_{sign}.wav")
            ok, words = _generate_sign_voice(script, vpath, sign_secs, voice=day_voice)
            if ok:
                voice_clips.append(vpath)
                caption_cues += _cue_group(words, t_cursor)
                print(f"      [{sign.title():<14}]  {sign_secs}s")
            else:
                sil2 = str(tmp / f"sil_{idx + 1:02d}.wav")
                _generate_silence(sign_secs, sil2)
                voice_clips.append(sil2)
                print(f"      [{sign.title():<14}]  (TTS failed, silence)")
            t_cursor += sign_secs

        if has_outro:
            outro_voice = str(tmp / "voice_99_outro.wav")
            ok, words = _generate_sign_voice(_outro_script(luckiest_sign), outro_voice,
                                             OUTRO_SECS, voice=day_voice)
            if ok:
                voice_clips.append(outro_voice)
                caption_cues += _cue_group(words, t_cursor)
                print(f"      [outro]  {OUTRO_SECS}s narrated reveal")
            else:
                sil3 = str(tmp / "sil_outro.wav")
                _generate_silence(OUTRO_SECS, sil3)
                voice_clips.append(sil3)
                print(f"      [outro]  (TTS failed, silence)")
            t_cursor += OUTRO_SECS

        # Write the caption file spanning the whole assembled timeline (global
        # offsets already baked into each segment's cues above): karaoke .ass
        # by default (spoken word lights up gold), .srt fallback. Empty if
        # word timing wasn't captured anywhere (all TTS failed) — captions
        # are then simply skipped, never a hard failure.
        srt_path, has_captions = _write_captions(caption_cues, str(tmp / "captions"))
        print(f"      Captions: {len(caption_cues)} cues"
              f" ({'karaoke' if str(srt_path).endswith('.ass') else 'plain'})"
              if has_captions
              else "      [WARN] No word-timing captured — captions skipped")

        # Zero cues across ALL segments means every TTS attempt (day voice +
        # every fallback) failed — the narration IS the product, so abort NOW
        # rather than spend 20+ min assembling a silent video that QC would
        # reject anyway (exit 1 → run_daily retries once, then emails FAIL).
        if not caption_cues:
            print("[ERROR] TTS produced no narration for ANY segment "
                  "(all voices failed) — aborting before assembly.",
                  file=sys.stderr)
            sys.exit(1)

        voice_concat_path = str(tmp / "voice_all.wav")
        valid_clips = [c for c in voice_clips if c and Path(c).exists()]
        voice_ok = (len(valid_clips) == len(voice_clips) and
                    _concat_audio(valid_clips, voice_concat_path))
        if voice_ok:
            print("      Voice concat OK")
        else:
            print("      [WARN] Voice concat failed — falling back to ambient-only")

        # 3. Ambient music
        print(f"\n[3/5] Generating ambient music ({total_dur}s)...")
        ambient_path = str(tmp / "ambient.wav")
        ambient_ok = _generate_ambient(total_dur, ambient_path)
        if ambient_ok:
            print("      OK")
        else:
            print("      [WARN] Ambient failed")

        # Mix voice + ambient (single AAC encode, loudness-mastered to -14 LUFS).
        # Fallback priority: mixed > VOICE-only > ambient-only. The narration
        # is the product — a music-only video must never ship while a voice
        # track exists.
        if voice_ok and ambient_ok:
            mixed_path = str(tmp / "audio_final.m4a")
            if _mix_voice_ambient(voice_concat_path, ambient_path, mixed_path):
                audio_path: str | None = mixed_path
                print("      Voice + ambient mixed")
            else:
                audio_path = voice_concat_path
                print("      [WARN] Mix failed — using voice-only (no music)")
        elif voice_ok:
            audio_path = voice_concat_path
            print("      Voice-only (no ambient)")
        elif ambient_ok:
            audio_path = ambient_path
            print("      [WARN] No voice — ambient only")
        else:
            audio_path = None
            print("      [WARN] No audio — video will be silent")

        # 4. Assemble
        out_w, out_h = frame or (WIDTH, HEIGHT)
        mode = "motion (crossfade)" if MOTION_ENABLED else "static"
        # Motion renders at the full FPS constant; static uses the
        # duration-aware VIDEO_FPS — print whichever will actually be used
        # (the log used to claim 24fps for an 11fps static encode).
        shown_fps = FPS if MOTION_ENABLED else VIDEO_FPS
        print(f"\n[4/5] Assembling {out_w}x{out_h} @ {shown_fps}fps  [{mode}]...")
        cap_srt = srt_path if has_captions else None
        ok = False
        if MOTION_ENABLED:
            ok = assemble_video_motion(png_files, durations, audio_path, video_path,
                                       srt_path=cap_srt, frame=frame)
        if not ok:
            ok = assemble_video(png_files, durations, audio_path, video_path,
                                srt_path=cap_srt, frame=frame)
        if not ok:
            print("[ERROR] Assembly failed", file=sys.stderr)
            sys.exit(1)
        size_mb = os.path.getsize(video_path) / 1_048_576
        print(f"      OK — {size_mb:.1f} MB")

    # 5. Thumbnail (outside tmpdir, using persistent paths)
    print(f"\n[5/5] Thumbnail...")
    render_thumbnail(date_str, thumb_path)

    # Per-sign chapters: derived from the same constants as the video, so they
    # can never drift. In regular search/watch surfaces they render a chapter
    # bar, and the 12 "<sign>" lines are 12 long-tail keyword matches.
    def _ts(secs: int) -> str:
        return f"{secs // 60}:{secs % 60:02d}"

    chapter_lines = ["0:00 Intro"] + [
        f"{_ts(INTRO_SECS + i * sign_secs)} {s.title()}" for i, s in enumerate(SIGNS)
    ]
    if has_outro:
        chapter_lines.append(f"{_ts(INTRO_SECS + len(SIGNS) * sign_secs)} Luckiest Sign")
    chapters_block = "\n".join(chapter_lines)

    description = data.get("description", "")
    if "0:00" not in description:
        description = f"{description}\n\n⏱ Find your sign:\n{chapters_block}"

    # Save metadata for uploader
    cadence_label = {"daily": "daily", "weekly": "weekly", "monthly": "monthly",
                     "deep": "daily", "weeklyfull": "weekly"}.get(CONTENT_TYPE, CONTENT_TYPE)
    meta = {
        "title":       data.get("title", f"{cadence_label.title()} Horoscope — {date_str} — All 12 Zodiac Signs"),
        "description": description,
        "tags":        data.get("tags", []),
        "hashtags":    data.get("hashtags", []),
        "date":        date_tag,
        "content_type": CONTENT_TYPE,
        "pinned_comment": (
            f"Which sign are you? Drop it below! ⬇️\n\n"
            f"⏱ Jump to your sign:\n{chapters_block}\n\n"
            f"Like + Subscribe for {cadence_label} cosmic guidance "
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
