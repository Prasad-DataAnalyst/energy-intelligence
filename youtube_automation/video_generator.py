"""
Pencil-sketch animated video generator — 18-40 US audience.

Pipeline:  scenes → PIL frames → OpenCV pencil-sketch → MoviePy MP4

Scenes
------
  0  Hook card       (10 s) — big question/statement draws in
  1  Category badge  ( 5 s) — day's category with icon
  2  Main bullets    (90 s) — 5 bullet points appear one by one with icon
  3  Takeaway slide  (30 s) — key insight with animated underline
  4  CTA outro       (25 s) — subscribe, like, comment call-to-action
"""

import os
import math
import textwrap
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2
from moviepy.editor import ImageSequenceClip

import config

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

W, H = config.WIDTH, config.HEIGHT
FPS = config.FPS

# Warm cream paper palette
PAPER    = (248, 245, 238)
INK      = (22,  18,  12)
INK_MID  = (75,  65,  55)
INK_LITE = (160, 150, 135)
RED      = (210,  60,  45)
BLUE     = ( 40,  90, 170)
GREEN    = ( 45, 140,  75)
AMBER    = (200, 135,  30)
PURPLE   = (120,  50, 170)

CATEGORY_COLORS = {
    "Money":     AMBER,
    "Mindset":   PURPLE,
    "Health":    GREEN,
    "Tech":      BLUE,
    "Career":    RED,
    "Lifestyle": (0, 160, 160),   # teal
    "Science":   (170, 80, 0),    # burnt orange
}

CATEGORY_ICONS = {
    "Money":     "$",
    "Mindset":   "~",
    "Health":    "+",
    "Tech":      "#",
    "Career":    "^",
    "Lifestyle": "@",
    "Science":   "*",
}

# Bullet check mark — plain ASCII for broad font compatibility
BULLET_MARK = ">"


# ---------------------------------------------------------------------------
# Font helpers
# ---------------------------------------------------------------------------

_FONT_CACHE: dict = {}

def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates_bold = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    candidates_reg = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in (candidates_bold if bold else candidates_reg):
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size)
                _FONT_CACHE[key] = f
                return f
            except Exception:
                pass
    f = ImageFont.load_default()
    _FONT_CACHE[key] = f
    return f


# ---------------------------------------------------------------------------
# Frame utilities
# ---------------------------------------------------------------------------

def _blank() -> Image.Image:
    return Image.new("RGB", (W, H), PAPER)


def _pencil_sketch(arr: np.ndarray) -> np.ndarray:
    """OpenCV pencil-sketch filter — keeps the warm paper tone."""
    gray = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    inv  = cv2.bitwise_not(gray)
    blur = cv2.GaussianBlur(inv, (25, 25), 0)
    sketch = cv2.divide(gray, cv2.bitwise_not(blur), scale=256.0)
    # Blend sketch with warm paper
    paper_layer = np.full_like(sketch, 245)
    merged = cv2.addWeighted(sketch, 0.72, paper_layer, 0.28, 0)
    return cv2.cvtColor(merged, cv2.COLOR_GRAY2RGB)


def _paper_grain(arr: np.ndarray) -> np.ndarray:
    noise = np.random.normal(0, 3.5, arr.shape).astype(np.int16)
    return np.clip(arr.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _post(img: Image.Image) -> np.ndarray:
    arr = np.array(img)
    arr = _pencil_sketch(arr)
    arr = _paper_grain(arr)
    return arr


def ease_in_out(t: float) -> float:
    """Smooth cubic easing."""
    return t * t * (3 - 2 * t)


def lerp_color(c1, c2, t: float):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


# ---------------------------------------------------------------------------
# Scene 0 — Hook card
# ---------------------------------------------------------------------------

def scene_hook(content: dict, n_frames: int) -> list[np.ndarray]:
    hook      = content["hook"]
    category  = content["category"]
    accent    = CATEGORY_COLORS.get(category, AMBER)
    date_str  = content["date"].strftime("%A, %B %d")

    f_hook    = _font(66, bold=True)
    f_date    = _font(34)
    f_cat     = _font(38, bold=True)

    # Wrap hook text
    wrapped = textwrap.wrap(hook, width=42)

    frames = []
    for i in range(n_frames):
        t  = i / n_frames
        et = ease_in_out(min(t * 1.6, 1.0))

        img  = _blank()
        draw = ImageDraw.Draw(img)

        # Top accent bar sweeps in
        bar_w = int(W * et)
        draw.rectangle([0, 0, bar_w, 14], fill=accent)

        # Category label
        if t > 0.1:
            cat_alpha = ease_in_out(min((t - 0.1) / 0.25, 1.0))
            cat_col   = lerp_color(PAPER, accent, cat_alpha)
            draw.text((W // 2, 70), category.upper(),
                      font=f_cat, fill=cat_col, anchor="mm")

        # Date
        if t > 0.2:
            da = ease_in_out(min((t - 0.2) / 0.3, 1.0))
            dc = lerp_color(PAPER, INK_MID, da)
            draw.text((W // 2, 120), date_str,
                      font=f_date, fill=dc, anchor="mm")

        # Hook lines appear word-by-word
        y_start = H // 2 - (len(wrapped) * 80) // 2
        for li, line in enumerate(wrapped):
            line_t = max(0.0, min((t - 0.25 - li * 0.15) / 0.3, 1.0))
            if line_t <= 0:
                continue
            words  = line.split()
            visible = max(1, int(len(words) * ease_in_out(line_t)))
            partial = " ".join(words[:visible])
            lc = lerp_color(PAPER, INK, ease_in_out(line_t))
            draw.text((W // 2, y_start + li * 82), partial,
                      font=f_hook, fill=lc, anchor="mm")

        # Bottom rule
        if t > 0.85:
            ra = ease_in_out((t - 0.85) / 0.15)
            rw = int((W - 400) * ra)
            draw.line([(200, H - 80), (200 + rw, H - 80)],
                      fill=accent, width=5)

        frames.append(_post(img))
    return frames


# ---------------------------------------------------------------------------
# Scene 1 — Category badge
# ---------------------------------------------------------------------------

def scene_badge(content: dict, n_frames: int) -> list[np.ndarray]:
    category = content["category"]
    accent   = CATEGORY_COLORS.get(category, AMBER)
    icon     = CATEGORY_ICONS.get(category, "?")

    f_big  = _font(120, bold=True)
    f_cat  = _font(58, bold=True)
    f_sub  = _font(36)

    frames = []
    for i in range(n_frames):
        t  = i / n_frames
        et = ease_in_out(min(t * 2.0, 1.0))

        img  = _blank()
        draw = ImageDraw.Draw(img)

        # Expanding circle
        r = int(220 * et)
        cx, cy = W // 2, H // 2 - 60
        if r > 0:
            ring_col = lerp_color(PAPER, accent, et * 0.18)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         outline=accent, width=5, fill=ring_col)

        # Icon
        if et > 0.4:
            ia = ease_in_out((et - 0.4) / 0.6)
            ic = lerp_color(PAPER, accent, ia)
            draw.text((cx, cy), icon, font=f_big, fill=ic, anchor="mm")

        # Category name
        if t > 0.55:
            na = ease_in_out((t - 0.55) / 0.3)
            nc = lerp_color(PAPER, INK, na)
            draw.text((W // 2, H // 2 + 200), category,
                      font=f_cat, fill=nc, anchor="mm")

        if t > 0.72:
            sa = ease_in_out((t - 0.72) / 0.28)
            sc = lerp_color(PAPER, INK_MID, sa)
            draw.text((W // 2, H // 2 + 280), "Today's Drop",
                      font=f_sub, fill=sc, anchor="mm")

        frames.append(_post(img))
    return frames


# ---------------------------------------------------------------------------
# Scene 2 — Five bullets
# ---------------------------------------------------------------------------

def scene_bullets(content: dict, n_frames: int) -> list[np.ndarray]:
    title   = content["topic"]
    bullets = content["bullets"]
    accent  = CATEGORY_COLORS.get(content["category"], AMBER)

    f_title  = _font(52, bold=True)
    f_num    = _font(56, bold=True)
    f_bullet = _font(40)

    wrapped_title = textwrap.wrap(title, width=50)

    # Each bullet gets an equal time slice
    per_bullet = 1.0 / max(len(bullets), 1)

    frames = []
    for i in range(n_frames):
        t = i / n_frames

        img  = _blank()
        draw = ImageDraw.Draw(img)

        # Title header
        for li, line in enumerate(wrapped_title):
            ta = ease_in_out(min(t * 3, 1.0))
            tc = lerp_color(PAPER, INK, ta)
            draw.text((W // 2, 70 + li * 58), line,
                      font=f_title, fill=tc, anchor="mm")

        # Accent underline below title
        ul_w = int((W - 400) * ease_in_out(min(t * 4, 1.0)))
        draw.line([(200, 75 + len(wrapped_title) * 58),
                   (200 + ul_w, 75 + len(wrapped_title) * 58)],
                  fill=accent, width=4)

        y_top = 75 + len(wrapped_title) * 58 + 40

        # Bullets appear sequentially
        for b_idx, bullet in enumerate(bullets):
            b_start = b_idx * per_bullet * 0.9
            b_t = max(0.0, min((t - b_start) / (per_bullet * 0.7), 1.0))
            if b_t <= 0:
                continue

            et_b  = ease_in_out(b_t)
            row_y = y_top + b_idx * 115

            # Number circle
            cx = 130
            cy = row_y + 38
            r  = int(38 * et_b)
            nc = lerp_color(PAPER, accent, et_b)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         fill=nc, outline=accent, width=2)
            if et_b > 0.5:
                draw.text((cx, cy), str(b_idx + 1),
                          font=f_num, fill=PAPER, anchor="mm")

            # Bullet text slides in from left
            txt_x = int(210 + (1 - et_b) * 180)
            tc    = lerp_color(PAPER, INK, et_b)
            # Wrap long bullets
            wrapped_b = textwrap.wrap(bullet, width=58)
            for wl, wline in enumerate(wrapped_b[:2]):
                draw.text((txt_x, row_y + wl * 44), wline,
                          font=f_bullet, fill=tc, anchor="lm")

        frames.append(_post(img))
    return frames


# ---------------------------------------------------------------------------
# Scene 3 — Takeaway
# ---------------------------------------------------------------------------

def scene_takeaway(content: dict, n_frames: int) -> list[np.ndarray]:
    takeaway = content["takeaway"]
    accent   = CATEGORY_COLORS.get(content["category"], AMBER)

    f_label  = _font(44, bold=True)
    f_text   = _font(52, bold=True)
    f_sub    = _font(36)

    wrapped = textwrap.wrap(takeaway, width=44)

    frames = []
    for i in range(n_frames):
        t  = i / n_frames
        et = ease_in_out(min(t * 1.8, 1.0))

        img  = _blank()
        draw = ImageDraw.Draw(img)

        # "KEY TAKEAWAY" label
        if t > 0.05:
            la = ease_in_out(min((t - 0.05) / 0.25, 1.0))
            lc = lerp_color(PAPER, accent, la)
            draw.text((W // 2, 140), "KEY TAKEAWAY",
                      font=f_label, fill=lc, anchor="mm")

        # Decorative brackets draw in
        if t > 0.2:
            ba = ease_in_out(min((t - 0.2) / 0.3, 1.0))
            bh = int(200 * ba)
            lx, rx = 180, W - 180
            # Left bracket
            draw.line([(lx, H//2 - bh), (lx, H//2 + bh)], fill=accent, width=6)
            draw.line([(lx, H//2 - bh), (lx + 50, H//2 - bh)], fill=accent, width=6)
            draw.line([(lx, H//2 + bh), (lx + 50, H//2 + bh)], fill=accent, width=6)
            # Right bracket
            draw.line([(rx, H//2 - bh), (rx, H//2 + bh)], fill=accent, width=6)
            draw.line([(rx - 50, H//2 - bh), (rx, H//2 - bh)], fill=accent, width=6)
            draw.line([(rx - 50, H//2 + bh), (rx, H//2 + bh)], fill=accent, width=6)

        # Takeaway text
        y_start = H // 2 - (len(wrapped) * 68) // 2
        for li, line in enumerate(wrapped):
            lt = max(0.0, min((t - 0.35 - li * 0.12) / 0.3, 1.0))
            if lt <= 0:
                continue
            lc = lerp_color(PAPER, INK, ease_in_out(lt))
            draw.text((W // 2, y_start + li * 68), line,
                      font=f_text, fill=lc, anchor="mm")

        # Sub-prompt
        if t > 0.82:
            sa = ease_in_out((t - 0.82) / 0.18)
            sc = lerp_color(PAPER, INK_MID, sa)
            draw.text((W // 2, H - 110), "Save this. You'll thank yourself later.",
                      font=f_sub, fill=sc, anchor="mm")

        frames.append(_post(img))
    return frames


# ---------------------------------------------------------------------------
# Scene 4 — CTA outro
# ---------------------------------------------------------------------------

def scene_outro(content: dict, n_frames: int) -> list[np.ndarray]:
    accent = CATEGORY_COLORS.get(content["category"], AMBER)

    f_big  = _font(82, bold=True)
    f_med  = _font(50)
    f_sm   = _font(38)
    f_cta  = _font(44, bold=True)

    ctas = [
        ("LIKE", RED,   W // 2 - 340, H // 2 + 130),
        ("SHARE", BLUE, W // 2,       H // 2 + 130),
        ("SUB",   GREEN, W // 2 + 340, H // 2 + 130),
    ]

    frames = []
    for i in range(n_frames):
        t  = i / n_frames
        et = ease_in_out(min(t * 2.5, 1.0))

        img  = _blank()
        draw = ImageDraw.Draw(img)

        # Top bar
        draw.rectangle([0, 0, W, 14], fill=accent)

        # "Thanks for watching!"
        if t > 0.08:
            ta = ease_in_out(min((t - 0.08) / 0.3, 1.0))
            tc = lerp_color(PAPER, INK, ta)
            draw.text((W // 2, 200), "Thanks for watching!",
                      font=f_big, fill=tc, anchor="mm")

        if t > 0.3:
            sa = ease_in_out(min((t - 0.3) / 0.3, 1.0))
            sc = lerp_color(PAPER, INK_MID, sa)
            draw.text((W // 2, H // 2 - 60),
                      "New video every single day.",
                      font=f_med, fill=sc, anchor="mm")
            draw.text((W // 2, H // 2),
                      "Subscribe so you never miss one.",
                      font=f_sm, fill=sc, anchor="mm")

        # Pulsing CTA buttons
        for c_idx, (label, col, cx, cy) in enumerate(ctas):
            ct = max(0.0, min((t - 0.52 - c_idx * 0.1) / 0.3, 1.0))
            if ct <= 0:
                continue
            pulse = abs(math.sin(t * math.pi * 3 + c_idx)) * 0.25 + 0.75
            r = int(80 * ct * pulse)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         fill=lerp_color(PAPER, col, ct * 0.2),
                         outline=col, width=4)
            if ct > 0.5:
                draw.text((cx, cy), label, font=f_cta,
                          fill=lerp_color(PAPER, col, ct), anchor="mm")

        # Channel name
        if t > 0.82:
            na = ease_in_out((t - 0.82) / 0.18)
            nc = lerp_color(PAPER, INK_MID, na)
            draw.text((W // 2, H - 80),
                      "Daily Drop with Prasad  |  New video every day at 3 PM EST",
                      font=_font(32), fill=nc, anchor="mm")

        frames.append(_post(img))
    return frames


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

# Scene durations must sum to config.DURATION (default 160 s; scene split below)
SCENE_DURATIONS = [10, 5, 90, 30, 25]   # seconds — total = 160

def generate_video(content: dict, output_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)

    scene_fns = [scene_hook, scene_badge, scene_bullets, scene_takeaway, scene_outro]

    print("Rendering pencil-sketch scenes…")
    all_frames: list[np.ndarray] = []
    for fn, dur in zip(scene_fns, SCENE_DURATIONS):
        n = dur * FPS
        print(f"  {fn.__name__}  →  {n} frames  ({dur}s)")
        all_frames.extend(fn(content, n))

    total = sum(SCENE_DURATIONS)
    print(f"Total: {len(all_frames)} frames ({total}s) — writing MP4…")

    clip = ImageSequenceClip(all_frames, fps=FPS)
    clip.write_videofile(
        output_path,
        codec="libx264",
        audio=False,
        logger=None,
        ffmpeg_params=["-crf", "22", "-preset", "fast"],
    )
    print(f"Video saved → {output_path}")
    return output_path
