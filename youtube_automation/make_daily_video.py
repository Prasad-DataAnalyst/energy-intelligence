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

def _icon(sym: str, fallback: str, size: int) -> tuple:
    """Return (symbol, font) — validated so icons never render as tofu."""
    gf = _glyph_font(size)
    try:
        if _tw(sym, gf) > 2:
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
    "en-US-EmmaNeural",   "en-US-SaraNeural",   "en-AU-CarlyNeural",
]
DEFAULT_VOICE = "en-IE-EmilyNeural"


def _day_voice(date_tag: str) -> str:
    """Deterministic voice-of-the-day."""
    try:
        return VOICES[int(date_tag) % len(VOICES)]
    except Exception:
        return DEFAULT_VOICE


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
                         voice: str = DEFAULT_VOICE) -> bool:
    """TTS → speed up slightly if needed → pad/trim to exactly target_secs.

    Rate is capped at +20%: beyond that the narration sounds rushed. The spoken
    script is sized to fit at normal speed; a big overrun means the script is
    too long and should be shortened, not chipmunked."""
    raw = out_path.rsplit(".", 1)[0] + "_raw.mp3"
    ok = asyncio.run(_tts_async(text, raw, voice=voice))
    if not ok:
        Path(raw).unlink(missing_ok=True)
        return _generate_silence(target_secs, out_path)

    dur = _audio_dur(raw)
    if dur > target_secs + 0.3:
        rate_pct = min(20, int((dur / target_secs - 1) * 100) + 3)
        asyncio.run(_tts_async(text, raw, rate=f"+{rate_pct}%", voice=voice))  # overwrite raw

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
    # OUTPUT option instead. Tier 2 is the no-loudnorm compatibility fallback.
    tiers = [
        "[0:a][1:a]amix=inputs=2:duration=first:normalize=0:weights='1 0.35',"
        "loudnorm=I=-14:TP=-1.5:LRA=11[out]",
        "[0:a]volume=2.0[v];[1:a]volume=0.7[a];"
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
                if i > 1:
                    print(f"      [INFO] Mix used compatibility tier {i} (no loudness master)")
                return True
            print(f"[WARN] Mix tier {i} failed: {r.stderr.decode()[-200:]}",
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

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", filt, "-map", f"[{prev}]",
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
def _subtitle_filter(srt_path: str) -> str:
    """ffmpeg subtitles filter clause using the bundled fonts (libass), with
    the srt path escaped for the filter's own ':'/',' delimiters.

    Fontsize/MarginV below are EMPIRICALLY calibrated, not literal pixels:
    this ffmpeg/libass build scales SRT-derived subtitles by a fixed internal
    factor (~6.2x, consistent with an assumed ~1080x... no — measured via a
    default-vs-forced-size probe on a 1080x1920 frame) regardless of the
    `original_size` option, which measurably had ZERO effect here (identical
    output bytes with/without it — verified, not assumed). Do not "fix" these
    numbers to look like sane pixel values; they were tuned by rendering
    single frames and visually checking placement, and land correctly THERE."""
    esc = str(srt_path).replace("\\", "\\\\").replace(":", "\\:")
    fontsdir = str(Path(__file__).parent / "assets" / "fonts").replace(":", "\\:")
    style = ("FontName=Poppins,Fontsize=24,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=0,"
             "Bold=1,Alignment=2,MarginV=55")
    return f"subtitles={esc}:fontsdir={fontsdir}:force_style='{style}'"


def assemble_video(png_files: list, durations: list,
                   audio_path: str | None, out_path: str,
                   srt_path: str | None = None) -> bool:
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
    vf = f"fps={static_fps},scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=disable"
    if srt_path and Path(srt_path).exists():
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
            durations.append(sign_secs)
            print(f"      [{sign.title():<14}]  {sign_secs}s")

        luckiest_sign = str(data.get("luckiest_sign", "")).strip()
        if has_outro:
            outro_png = str(tmp / "99_outro.png")
            render_outro_card(luckiest_sign).save(outro_png, "PNG")
            png_files.append(outro_png)
            durations.append(OUTRO_SECS)
            print(f"      [outro]  {OUTRO_SECS}s  (luckiest: {luckiest_sign or '—'})")

        # 2. Voice narration (edge-tts, free) — one narrator per day
        day_voice = _day_voice(date_tag)
        print(f"\n[2/5] Generating voice narration (edge-tts, voice: {day_voice})...")
        voice_clips: list = []

        # Narrated intro — never open a Short with 4s of dead air.
        intro_voice = str(tmp / "voice_00_intro.wav")
        if _generate_sign_voice(_intro_script(date_str), intro_voice,
                                INTRO_SECS, voice=day_voice):
            voice_clips.append(intro_voice)
            print(f"      [intro]  {INTRO_SECS}s narrated hook")
        else:
            voice_clips.append(None)

        for idx, sign in enumerate(SIGNS):
            fields = signs_data.get(sign, {})
            script = _voice_script(sign, fields)
            vpath  = str(tmp / f"voice_{idx + 1:02d}_{sign}.wav")
            if _generate_sign_voice(script, vpath, sign_secs, voice=day_voice):
                voice_clips.append(vpath)
                print(f"      [{sign.title():<14}]  {sign_secs}s")
            else:
                sil2 = str(tmp / f"sil_{idx + 1:02d}.wav")
                _generate_silence(sign_secs, sil2)
                voice_clips.append(sil2)
                print(f"      [{sign.title():<14}]  (TTS failed, silence)")

        if has_outro:
            outro_voice = str(tmp / "voice_99_outro.wav")
            if _generate_sign_voice(_outro_script(luckiest_sign), outro_voice,
                                    OUTRO_SECS, voice=day_voice):
                voice_clips.append(outro_voice)
                print(f"      [outro]  {OUTRO_SECS}s narrated reveal")
            else:
                sil3 = str(tmp / "sil_outro.wav")
                _generate_silence(OUTRO_SECS, sil3)
                voice_clips.append(sil3)
                print(f"      [outro]  (TTS failed, silence)")

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
