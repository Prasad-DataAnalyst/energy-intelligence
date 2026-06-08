"""
Pencil-sketch animated video generator — 18-40 US audience.

Memory strategy: scene functions are generators that yield one frame at a
time. generate_video writes each frame as a JPEG to a temp dir, then calls
ffmpeg to assemble the MP4. Peak RAM is ~3 MB (one frame) regardless of
video length.

Resolution: 1280×720 (YouTube HD, 4× less RAM than 1080p)
FPS: 15 (smooth enough for sketch animation)

Scenes
------
  0  Hook card     ( 8 s) — big statement draws in word-by-word
  1  Badge         ( 4 s) — category icon expands
  2  Bullets       (50 s) — 5 key points slide in sequentially
  3  Takeaway      (18 s) — single action with decorative brackets
  4  CTA outro     (12 s) — LIKE / SHARE / SUB pulsing buttons
                  --------
                  92 s total
"""

import os
import math
import shutil
import subprocess
import tempfile
import textwrap
from typing import Generator

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import cv2

import config

# ---------------------------------------------------------------------------
# Dimensions & palette
# ---------------------------------------------------------------------------

W, H = 1280, 720
FPS = 15

PAPER   = (248, 245, 238)
INK     = (22,  18,  12)
INK_MID = (75,  65,  55)
RED     = (210,  60,  45)
BLUE    = ( 40,  90, 170)
GREEN   = ( 45, 140,  75)
AMBER   = (200, 135,  30)
PURPLE  = (120,  50, 170)

CATEGORY_COLORS = {
    "Money":     AMBER,
    "Mindset":   PURPLE,
    "Health":    GREEN,
    "Tech":      BLUE,
    "Career":    RED,
    "Lifestyle": (0, 160, 160),
    "Science":   (170, 80, 0),
}

CATEGORY_ICONS = {
    "Money": "$", "Mindset": "~", "Health": "+",
    "Tech": "#",  "Career": "^", "Lifestyle": "@", "Science": "*",
}

_FONT_CACHE: dict = {}

def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
    )
    for path in candidates:
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

def _blank() -> Image.Image:
    return Image.new("RGB", (W, H), PAPER)

def _post(img: Image.Image) -> np.ndarray:
    arr = np.array(img)
    # Pencil-sketch effect
    gray  = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY)
    inv   = cv2.bitwise_not(gray)
    blur  = cv2.GaussianBlur(inv, (21, 21), 0)
    sk    = cv2.divide(gray, cv2.bitwise_not(blur), scale=256.0)
    paper = np.full_like(sk, 245)
    merged = cv2.addWeighted(sk, 0.72, paper, 0.28, 0)
    rgb   = cv2.cvtColor(merged, cv2.COLOR_GRAY2RGB)
    # Paper grain
    noise = np.random.normal(0, 3, rgb.shape).astype(np.int16)
    return np.clip(rgb.astype(np.int16) + noise, 0, 255).astype(np.uint8)

def ease(t: float) -> float:
    t = max(0.0, min(t, 1.0))
    return t * t * (3 - 2 * t)

def lc(c1, c2, t: float):
    t = max(0.0, min(t, 1.0))
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

# ---------------------------------------------------------------------------
# Scene generators  (yield one np.ndarray per frame)
# ---------------------------------------------------------------------------

def scene_hook(content: dict, n: int) -> Generator:
    hook     = content["hook"]
    category = content["category"]
    accent   = CATEGORY_COLORS.get(category, AMBER)
    date_str = content["date"].strftime("%A, %B %d")
    wrapped  = textwrap.wrap(hook, width=38)

    f_hook = _font(54, bold=True)
    f_cat  = _font(30, bold=True)
    f_date = _font(26)

    for i in range(n):
        t = i / n
        img  = _blank()
        draw = ImageDraw.Draw(img)

        # Top accent bar
        draw.rectangle([0, 0, int(W * ease(t * 1.8)), 10], fill=accent)

        if t > 0.08:
            draw.text((W//2, 52), category.upper(),
                      font=f_cat, fill=lc(PAPER, accent, ease((t-0.08)/0.25)), anchor="mm")
        if t > 0.18:
            draw.text((W//2, 86), date_str,
                      font=f_date, fill=lc(PAPER, INK_MID, ease((t-0.18)/0.25)), anchor="mm")

        y0 = H//2 - len(wrapped) * 62 // 2
        for li, line in enumerate(wrapped):
            lt = ease(max(0, (t - 0.28 - li*0.14) / 0.28))
            if lt <= 0:
                continue
            words = line.split()
            partial = " ".join(words[:max(1, int(len(words)*lt))])
            draw.text((W//2, y0 + li*62), partial,
                      font=f_hook, fill=lc(PAPER, INK, lt), anchor="mm")

        if t > 0.88:
            rw = int((W-300) * ease((t-0.88)/0.12))
            draw.line([(150, H-60), (150+rw, H-60)], fill=accent, width=4)

        yield _post(img)


def scene_badge(content: dict, n: int) -> Generator:
    category = content["category"]
    accent   = CATEGORY_COLORS.get(category, AMBER)
    icon     = CATEGORY_ICONS.get(category, "?")

    f_big = _font(96, bold=True)
    f_cat = _font(44, bold=True)
    f_sub = _font(28)

    for i in range(n):
        t  = i / n
        et = ease(min(t * 2.2, 1.0))
        img  = _blank()
        draw = ImageDraw.Draw(img)

        r = int(180 * et)
        cx, cy = W//2, H//2 - 50
        if r > 0:
            draw.ellipse([cx-r, cy-r, cx+r, cy+r],
                         outline=accent, width=4,
                         fill=lc(PAPER, accent, et * 0.15))
        if et > 0.4:
            draw.text((cx, cy), icon, font=f_big,
                      fill=lc(PAPER, accent, ease((et-0.4)/0.6)), anchor="mm")
        if t > 0.55:
            draw.text((W//2, H//2+160), category,
                      font=f_cat, fill=lc(PAPER, INK, ease((t-0.55)/0.3)), anchor="mm")
        if t > 0.72:
            draw.text((W//2, H//2+210), "Today's Drop",
                      font=f_sub, fill=lc(PAPER, INK_MID, ease((t-0.72)/0.28)), anchor="mm")

        yield _post(img)


def scene_bullets(content: dict, n: int) -> Generator:
    title   = content["topic"]
    bullets = content["bullets"]
    accent  = CATEGORY_COLORS.get(content["category"], AMBER)

    f_title  = _font(40, bold=True)
    f_num    = _font(38, bold=True)
    f_bullet = _font(30)

    wtitle = textwrap.wrap(title, width=50)
    per_b  = 1.0 / max(len(bullets), 1)

    for i in range(n):
        t = i / n
        img  = _blank()
        draw = ImageDraw.Draw(img)

        for li, line in enumerate(wtitle):
            draw.text((W//2, 48 + li*46), line,
                      font=f_title, fill=lc(PAPER, INK, ease(min(t*3, 1.0))), anchor="mm")

        ul_w = int((W-300) * ease(min(t*4, 1.0)))
        draw.line([(150, 50 + len(wtitle)*46),
                   (150 + ul_w, 50 + len(wtitle)*46)], fill=accent, width=3)

        y0 = 50 + len(wtitle)*46 + 30
        for b_idx, bullet in enumerate(bullets):
            b_t = ease(max(0, (t - b_idx*per_b*0.88) / (per_b*0.65)))
            if b_t <= 0:
                continue
            ry  = y0 + b_idx * 92
            ncx, ncy = 90, ry + 28
            r   = int(28 * b_t)
            draw.ellipse([ncx-r, ncy-r, ncx+r, ncy+r],
                         fill=lc(PAPER, accent, b_t), outline=accent, width=2)
            if b_t > 0.5:
                draw.text((ncx, ncy), str(b_idx+1),
                          font=f_num, fill=PAPER, anchor="mm")
            tx = int(132 + (1-b_t)*130)
            for wl, wline in enumerate(textwrap.wrap(bullet, width=62)[:2]):
                draw.text((tx, ry + wl*34), wline,
                          font=f_bullet, fill=lc(PAPER, INK, b_t), anchor="lm")

        yield _post(img)


def scene_takeaway(content: dict, n: int) -> Generator:
    takeaway = content["takeaway"]
    accent   = CATEGORY_COLORS.get(content["category"], AMBER)

    f_label = _font(34, bold=True)
    f_text  = _font(40, bold=True)
    f_sub   = _font(26)

    wrapped = textwrap.wrap(takeaway, width=42)

    for i in range(n):
        t  = i / n
        img  = _blank()
        draw = ImageDraw.Draw(img)

        if t > 0.04:
            draw.text((W//2, 100), "KEY TAKEAWAY",
                      font=f_label, fill=lc(PAPER, accent, ease((t-0.04)/0.22)), anchor="mm")

        if t > 0.2:
            bh = int(160 * ease((t-0.2)/0.28))
            lx, rx = 130, W-130
            for pts in [
                [(lx, H//2-bh),(lx, H//2+bh)],
                [(lx, H//2-bh),(lx+40, H//2-bh)],
                [(lx, H//2+bh),(lx+40, H//2+bh)],
                [(rx, H//2-bh),(rx, H//2+bh)],
                [(rx-40, H//2-bh),(rx, H//2-bh)],
                [(rx-40, H//2+bh),(rx, H//2+bh)],
            ]:
                draw.line(pts, fill=accent, width=5)

        y0 = H//2 - len(wrapped)*54//2
        for li, line in enumerate(wrapped):
            lt = ease(max(0, (t - 0.32 - li*0.1) / 0.28))
            if lt <= 0:
                continue
            draw.text((W//2, y0 + li*54), line,
                      font=f_text, fill=lc(PAPER, INK, lt), anchor="mm")

        if t > 0.84:
            draw.text((W//2, H-70), "Save this. You'll thank yourself later.",
                      font=f_sub, fill=lc(PAPER, INK_MID, ease((t-0.84)/0.16)), anchor="mm")

        yield _post(img)


def scene_outro(content: dict, n: int) -> Generator:
    accent = CATEGORY_COLORS.get(content["category"], AMBER)

    f_big = _font(64, bold=True)
    f_med = _font(38)
    f_sm  = _font(28)
    f_cta = _font(34, bold=True)

    ctas = [
        ("LIKE",  RED,   W//2 - 260, H//2 + 100),
        ("SHARE", BLUE,  W//2,       H//2 + 100),
        ("SUB",   GREEN, W//2 + 260, H//2 + 100),
    ]

    for i in range(n):
        t = i / n
        img  = _blank()
        draw = ImageDraw.Draw(img)

        draw.rectangle([0, 0, W, 10], fill=accent)

        if t > 0.06:
            draw.text((W//2, 150), "Thanks for watching!",
                      font=f_big, fill=lc(PAPER, INK, ease((t-0.06)/0.28)), anchor="mm")
        if t > 0.28:
            draw.text((W//2, H//2-50), "New video every single day.",
                      font=f_med, fill=lc(PAPER, INK_MID, ease((t-0.28)/0.28)), anchor="mm")
            draw.text((W//2, H//2),    "Subscribe so you never miss one.",
                      font=f_sm, fill=lc(PAPER, INK_MID, ease((t-0.28)/0.28)), anchor="mm")

        for c_idx, (label, col, cx, cy) in enumerate(ctas):
            ct = ease(max(0, (t - 0.48 - c_idx*0.08) / 0.28))
            if ct <= 0:
                continue
            pulse = abs(math.sin(t*math.pi*3 + c_idx)) * 0.22 + 0.78
            r = int(64 * ct * pulse)
            draw.ellipse([cx-r, cy-r, cx+r, cy+r],
                         fill=lc(PAPER, col, ct*0.18), outline=col, width=3)
            if ct > 0.5:
                draw.text((cx, cy), label, font=f_cta,
                          fill=lc(PAPER, col, ct), anchor="mm")

        if t > 0.84:
            draw.text((W//2, H-50),
                      "Daily Drop with Prasad  |  New video every day at 3 PM EST",
                      font=_font(24), fill=lc(PAPER, INK_MID, ease((t-0.84)/0.16)), anchor="mm")

        yield _post(img)


# ---------------------------------------------------------------------------
# Main entry — streams frames to disk, calls ffmpeg
# ---------------------------------------------------------------------------

SCENE_FNS  = [scene_hook, scene_badge, scene_bullets, scene_takeaway, scene_outro]
SCENE_SECS = [8,           4,           50,            18,             12]   # = 92 s


def generate_video(content: dict, output_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="dd_frames_")

    try:
        frame_idx = 0
        for fn, dur in zip(SCENE_FNS, SCENE_SECS):
            n = dur * FPS
            print(f"  {fn.__name__}  {dur}s  ({n} frames)")
            for arr in fn(content, n):
                path = os.path.join(tmp, f"{frame_idx:06d}.jpg")
                Image.fromarray(arr).save(path, quality=88)
                frame_idx += 1

        total_s = sum(SCENE_SECS)
        print(f"Frames on disk: {frame_idx} ({total_s}s) — running ffmpeg…")

        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", os.path.join(tmp, "%06d.jpg"),
            "-c:v", "libx264",
            "-crf", "22",
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            output_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg error:\n{result.stderr}")

        print(f"Video saved → {output_path}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return output_path
