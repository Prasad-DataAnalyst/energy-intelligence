"""
video_generator.py  —  Mind Fuel Daily
========================================
Full HD 1920×1080 @ 24fps, modern fast-paced YouTube style.

Visual design
-------------
• Dark gradient background with subtle animated texture
• Bold kinetic typography — text slams, zooms, slides
• Category-specific neon accent colour
• Live progress bar (fills across entire video)
• Stat/number highlight boxes (pops on key figures)
• Quick flash transitions between scenes
• @GetMindFuelNow watermark

Audio is added separately by daily_runner.py via ffmpeg.

Memory strategy: generator functions yield one frame at a time →
written to JPEG on disk → ffmpeg assembles MP4. Peak RAM ≈ 8 MB.
"""

import os, math, shutil, subprocess, tempfile, textwrap
from typing import Generator
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import cv2

import config

# ── Canvas (read from .env via config) ───────────────────────────────────────
W   = config.WIDTH    # default 1280
H   = config.HEIGHT   # default 720
FPS = config.FPS      # default 15
TOTAL_FRAMES = 0      # set dynamically in generate_video

# ── Colour palettes per category ─────────────────────────────────────────────
THEMES = {
    "Money":     {"bg1":(10,10,28),   "bg2":(20,20,50),  "acc":(255,215,0),   "hi":(255,180,0)},
    "Mindset":   {"bg1":(18,8,30),    "bg2":(35,15,55),  "acc":(180,80,255),  "hi":(220,130,255)},
    "Health":    {"bg1":(5,20,15),    "bg2":(8,38,25),   "acc":(0,255,136),   "hi":(80,255,160)},
    "Tech":      {"bg1":(5,15,28),    "bg2":(8,28,50),   "acc":(0,212,255),   "hi":(80,230,255)},
    "Career":    {"bg1":(28,10,5),    "bg2":(50,18,8),   "acc":(255,107,53),  "hi":(255,150,80)},
    "Lifestyle": {"bg1":(28,5,18),    "bg2":(50,8,35),   "acc":(255,60,172),  "hi":(255,120,200)},
    "Science":   {"bg1":(25,22,5),    "bg2":(45,40,8),   "acc":(255,230,50),  "hi":(255,245,100)},
}

WHITE  = (255, 255, 255)
GRAY   = (180, 180, 180)
BLACK  = (0, 0, 0)

# Scale factor — all sizes were designed for 1920×1080; scale down for smaller W
_SF = W / 1920

def _s(px: int) -> int:
    """Scale a pixel value proportionally to the canvas width."""
    return max(10, int(px * _SF))

# ── Font helpers ─────────────────────────────────────────────────────────────
_FC: dict = {}
def fnt(size: int, bold: bool = False) -> ImageFont.ImageFont:
    key = (size, bold)
    if key in _FC:
        return _FC[key]
    paths = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
              if bold else
              ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf"])
    for p in paths:
        if os.path.exists(p):
            try:
                f = ImageFont.truetype(p, size); _FC[key] = f; return f
            except Exception:
                pass
    f = ImageFont.load_default(); _FC[key] = f; return f

# ── Math helpers ─────────────────────────────────────────────────────────────
def ease(t):   t=max(0.,min(t,1.)); return t*t*(3-2*t)
def easein(t): t=max(0.,min(t,1.)); return t*t*t
def lc(a,b,t):
    t=max(0.,min(float(t),1.))
    return tuple(int(x+(y-x)*t) for x,y in zip(a,b))

# ── Background builder ────────────────────────────────────────────────────────
def _bg(theme: dict, frame_i: int, total: int) -> Image.Image:
    img  = Image.new("RGB", (W, H), theme["bg1"])
    draw = ImageDraw.Draw(img)
    # Vertical gradient
    for y in range(H):
        r = y / H
        c = lc(theme["bg1"], theme["bg2"], r)
        draw.line([(0,y),(W,y)], fill=c)
    # Subtle animated diagonal line texture
    offset = int((frame_i / max(total,1)) * 60) % 60
    for x in range(-H, W, 60):
        draw.line([(x+offset, 0),(x+offset+H, H)],
                  fill=(*[max(0,v+8) for v in theme["bg1"]],), width=1)
    return img

def _grain(arr: np.ndarray) -> np.ndarray:
    n = np.random.normal(0, 2.5, arr.shape).astype(np.int16)
    return np.clip(arr.astype(np.int16)+n, 0, 255).astype(np.uint8)

# ── Progress bar (shared across all scenes) ──────────────────────────────────
def _progress(draw: ImageDraw.ImageDraw, frame_i: int, total: int, acc):
    pct   = frame_i / max(total, 1)
    bar_w = int(W * pct)
    draw.rectangle([0, H-8, W, H], fill=(*[max(0,v-20) for v in acc],))
    draw.rectangle([0, H-8, bar_w, H], fill=acc)

# ── Watermark ────────────────────────────────────────────────────────────────
def _watermark(draw: ImageDraw.ImageDraw):
    draw.text((W-_s(20), _s(20)), "@GetMindFuelNow",
              font=fnt(_s(28)), fill=(120,120,120), anchor="rt")

# ── Number highlight box ──────────────────────────────────────────────────────
def _has_number(text: str) -> bool:
    import re
    return bool(re.search(r'\$[\d,]+|[\d]+[%kKmMbB]|[\d]{2,}', text))

def _highlight_numbers(draw, text, x, y, font, base_col, hi_col, anchor="lm"):
    """Draw text with numbers/stats highlighted in accent colour."""
    import re
    parts = re.split(r'(\$[\d,\.]+[kKmMbB%]?|[\d]+[%kKmMbBx+\-\.]+|[\d]{2,})', text)
    # Measure total width
    full_w = draw.textlength(text, font=font)
    if anchor == "mm":
        cx = x - full_w / 2
    elif anchor == "rm":
        cx = x - full_w
    else:
        cx = x
    for part in parts:
        col = hi_col if re.search(r'\d', part) else base_col
        if re.search(r'\d', part):
            pw = draw.textlength(part, font=font)
            draw.rectangle([cx-4, y-font.size//2-4, cx+pw+4, y+font.size//2+4],
                           fill=(*[min(255,v+40) for v in hi_col], 40) if len(hi_col)==3
                           else (80,60,0,80))
        draw.text((cx, y), part, font=font, fill=col, anchor="lm")
        cx += draw.textlength(part, font=font)


# ─────────────────────────────────────────────────────────────────────────────
# SCENE GENERATORS
# Each yields np.ndarray frames (RGB uint8)
# ─────────────────────────────────────────────────────────────────────────────

def scene_hook(content, n, total, fi_start) -> Generator:
    theme = THEMES.get(content["category"], THEMES["Money"])
    acc   = theme["acc"]
    hook  = content["hook"]
    words = hook.split()

    for i in range(n):
        t   = i / n
        fi  = fi_start + i
        img  = _bg(theme, fi, total)
        draw = ImageDraw.Draw(img)

        # Flash at start
        if i < 4:
            overlay = Image.new("RGB", (W,H), acc)
            img = Image.blend(img, overlay, alpha=max(0, 0.6 - i*0.18))
            draw = ImageDraw.Draw(img)

        # Hook text — word by word slam
        visible = max(1, int(len(words) * ease(t * 1.6)))
        line    = " ".join(words[:visible])
        wrapped = textwrap.wrap(line, width=34)
        lh      = _s(100)
        y0      = H//2 - len(wrapped)*lh//2

        for li, wline in enumerate(wrapped):
            scale = 1 + max(0, 0.3 - t*0.6)
            fs    = int(_s(88) * scale)
            f     = fnt(fs, bold=True)
            draw.text((W//2+2, y0+li*lh+2), wline, font=f, fill=(0,0,0), anchor="mm")
            _highlight_numbers(draw, wline, W//2, y0+li*lh, f, WHITE, theme["hi"], "mm")

        # Category tag top-left
        tw, th = _s(280), _s(55)
        draw.rectangle([_s(30), _s(30), _s(30)+tw, _s(30)+th], fill=acc)
        draw.text((_s(30)+tw//2, _s(30)+th//2), content["category"].upper(),
                  font=fnt(_s(34),True), fill=BLACK, anchor="mm")

        _progress(draw, fi, total, acc)
        _watermark(draw)
        yield _grain(np.array(img))


def scene_title(content, n, total, fi_start) -> Generator:
    theme  = THEMES.get(content["category"], THEMES["Money"])
    acc    = theme["acc"]
    title  = content["topic"]
    wtitle = textwrap.wrap(title, width=40)

    f_title = fnt(_s(72), bold=True)
    f_sub   = fnt(_s(40))

    for i in range(n):
        t   = i / n
        fi  = fi_start + i
        img  = _bg(theme, fi, total)
        draw = ImageDraw.Draw(img)

        slide = int((1 - ease(min(t*2.5,1))) * _s(120))
        lh    = _s(80)
        y0    = H//2 - len(wtitle)*lh//2

        for li, line in enumerate(wtitle):
            alpha = ease(max(0,(t - li*0.12)/0.3))
            col   = lc(theme["bg2"], WHITE, alpha)
            draw.text((W//2, y0 + li*lh + slide), line,
                      font=f_title, fill=col, anchor="mm")

        if t > 0.4:
            uw = int((W-_s(400)) * ease((t-0.4)/0.4))
            draw.rectangle([_s(200), y0+len(wtitle)*lh+_s(20),
                            _s(200)+uw, y0+len(wtitle)*lh+_s(26)], fill=acc)

        if t > 0.55:
            a2 = ease((t-0.55)/0.3)
            draw.text((W//2, y0+len(wtitle)*lh+_s(70)),
                      "5 things you need to know today",
                      font=f_sub, fill=lc(theme["bg2"], GRAY, a2), anchor="mm")

        _progress(draw, fi, total, acc)
        _watermark(draw)
        tw, th = _s(280), _s(55)
        draw.rectangle([_s(30), _s(30), _s(30)+tw, _s(30)+th], fill=acc)
        draw.text((_s(30)+tw//2, _s(30)+th//2), content["category"].upper(),
                  font=fnt(_s(34),True), fill=BLACK, anchor="mm")
        yield _grain(np.array(img))


def scene_bullet(content, bullet_idx, n, total, fi_start) -> Generator:
    theme  = THEMES.get(content["category"], THEMES["Money"])
    acc    = theme["acc"]
    bullet = content["bullets"][bullet_idx]
    num    = str(bullet_idx + 1)
    wb     = textwrap.wrap(bullet, width=52)

    for i in range(n):
        t   = i / n
        fi  = fi_start + i
        img  = _bg(theme, fi, total)
        draw = ImageDraw.Draw(img)

        if i < 3:
            flash = Image.new("RGB",(W,H), acc)
            img   = Image.blend(img, flash, alpha=max(0,0.4 - i*0.15))
            draw  = ImageDraw.Draw(img)

        num_scale  = 1 + max(0, 0.8 - t*1.6)
        num_size   = int(_s(160) * num_scale)
        num_alpha  = ease(min(t*3, 1.0))
        num_col    = lc(theme["bg2"], acc, num_alpha)
        draw.text((_s(220), H//2 - _s(40)), num,
                  font=fnt(num_size, True), fill=num_col, anchor="mm")

        if t > 0.15:
            txt_t   = ease((t-0.15)/0.45)
            visible = max(1, int(sum(len(l) for l in wb) * txt_t))
            chars   = 0
            lh      = _s(65)
            y0      = H//2 - len(wb)*lh//2

            for li, line in enumerate(wb):
                if chars >= visible:
                    break
                show   = line[:max(0, visible - chars)]
                chars += len(line)
                col    = lc(theme["bg2"], WHITE, ease(min(t*3,1.0)))
                slide  = int((1-txt_t)*_s(200)*(1/(li+1)))
                _highlight_numbers(draw, show,
                                   _s(360) + slide, y0 + li*lh,
                                   fnt(_s(54)), col, theme["hi"])

        if t > 0.68 and t < 0.82 and _has_number(bullet):
            pulse = 1 + 0.06 * math.sin((t-0.68)/0.14 * math.pi)
            draw.text((W//2+_s(50), H-_s(120)),
                      "↑ KEY STAT",
                      font=fnt(int(_s(32)*pulse), True), fill=acc, anchor="mm")

        _progress(draw, fi, total, acc)
        _watermark(draw)
        tw, th = _s(280), _s(55)
        draw.rectangle([_s(30), _s(30), _s(30)+tw, _s(30)+th], fill=acc)
        draw.text((_s(30)+tw//2, _s(30)+th//2), content["category"].upper(),
                  font=fnt(_s(34),True), fill=BLACK, anchor="mm")
        yield _grain(np.array(img))


def scene_takeaway(content, n, total, fi_start) -> Generator:
    theme   = THEMES.get(content["category"], THEMES["Money"])
    acc     = theme["acc"]
    takeaway = content["takeaway"]
    wt       = textwrap.wrap(takeaway, width=44)

    f_label = fnt(_s(52), bold=True)
    f_text  = fnt(_s(60), bold=True)

    for i in range(n):
        t   = i / n
        fi  = fi_start + i
        img  = _bg(theme, fi, total)
        draw = ImageDraw.Draw(img)

        if i < 4:
            flash = Image.new("RGB",(W,H), acc)
            img   = Image.blend(img, flash, alpha=max(0,0.5-i*0.15))
            draw  = ImageDraw.Draw(img)

        if t > 0.05:
            bw = _s(420)
            by = _s(120)
            draw.rectangle([W//2-bw//2, by, W//2+bw//2, by+_s(65)], fill=acc)
            draw.text((W//2, by+_s(32)), "KEY TAKEAWAY",
                      font=f_label, fill=BLACK, anchor="mm")

        lh = _s(74)
        y0 = H//2 - len(wt)*lh//2
        for li, line in enumerate(wt):
            lt = ease(max(0,(t - 0.28 - li*0.1)/0.28))
            if lt <= 0:
                continue
            col   = lc(theme["bg2"], WHITE, lt)
            slide = int((1-lt)*_s(80))
            _highlight_numbers(draw, line, W//2+slide, y0+li*lh,
                               f_text, col, theme["hi"], "mm")

        if t > 0.78:
            a2 = ease((t-0.78)/0.22)
            draw.text((W//2, H-_s(120)), "Save this. Act on it today.",
                      font=fnt(_s(40)), fill=lc(theme["bg2"], GRAY, a2), anchor="mm")

        _progress(draw, fi, total, acc)
        _watermark(draw)
        yield _grain(np.array(img))


def scene_cta(content, n, total, fi_start) -> Generator:
    theme = THEMES.get(content["category"], THEMES["Money"])
    acc   = theme["acc"]

    f_big  = fnt(_s(90), bold=True)
    f_med  = fnt(_s(54))
    f_sm   = fnt(_s(38))
    f_btn  = fnt(_s(46), bold=True)

    btns = [
        ("LIKE",      (220,50,50),   W//2-_s(480), H//2+_s(80)),
        ("SUBSCRIBE", acc,           W//2,          H//2+_s(80)),
        ("SHARE",     (50,130,220),  W//2+_s(480), H//2+_s(80)),
    ]

    for i in range(n):
        t   = i / n
        fi  = fi_start + i
        img  = _bg(theme, fi, total)
        draw = ImageDraw.Draw(img)

        draw.rectangle([0,0,W,_s(12)], fill=acc)

        if t > 0.06:
            a = ease((t-0.06)/0.25)
            draw.text((W//2, _s(200)), "Thanks for watching!",
                      font=f_big, fill=lc(theme["bg2"],WHITE,a), anchor="mm")

        if t > 0.28:
            a = ease((t-0.28)/0.25)
            draw.text((W//2, H//2-_s(80)),
                      "New mind fuel drops every day at 3 PM.",
                      font=f_med, fill=lc(theme["bg2"],GRAY,a), anchor="mm")

        for ci, (label, col, cx, cy) in enumerate(btns):
            ct = ease(max(0,(t-0.42-ci*0.08)/0.28))
            if ct <= 0: continue
            pulse = 1 + 0.08*abs(math.sin(t*math.pi*4+ci))
            r = int(_s(78)*ct*pulse)
            draw.ellipse([cx-r,cy-r,cx+r,cy+r],
                         fill=(*[int(v*0.2) for v in col],),
                         outline=col, width=max(2,_s(4)))
            if ct > 0.55:
                draw.text((cx,cy), label, font=f_btn,
                          fill=lc(theme["bg2"],col,ct), anchor="mm")

        if t > 0.80:
            a = ease((t-0.80)/0.20)
            draw.text((W//2, H-_s(80)),
                      "Mind Fuel Daily  |  @GetMindFuelNow",
                      font=f_sm, fill=lc(theme["bg2"],GRAY,a), anchor="mm")

        _progress(draw, fi, total, acc)
        yield _grain(np.array(img))


# ── Scene schedule ────────────────────────────────────────────────────────────
# [function, duration_seconds]
SCENES = [
    (scene_hook,     6),
    (scene_title,    4),
    # bullets injected dynamically
    (scene_takeaway, 10),
    (scene_cta,      8),
]
BULLET_DURATION = 13   # seconds per bullet


def _build_schedule(content):
    schedule = [
        (scene_hook,     6),
        (scene_title,    4),
    ]
    for idx in range(len(content["bullets"])):
        schedule.append((lambda c, bi=idx: (lambda n, total, fi: scene_bullet(c, bi, n, total, fi)),
                         BULLET_DURATION))
    schedule += [
        (scene_takeaway, 10),
        (scene_cta,       8),
    ]
    return schedule


# ── Main entry ────────────────────────────────────────────────────────────────

def generate_video(content: dict, output_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="mfd_frames_")

    # Build schedule and compute total frames
    schedule = []
    schedule.append((lambda c: (lambda n, total, fi: scene_hook(c, n, total, fi)),  6))
    schedule.append((lambda c: (lambda n, total, fi: scene_title(c, n, total, fi)), 4))
    for bidx in range(len(content["bullets"])):
        schedule.append(
            (lambda c, bi=bidx: (lambda n, total, fi: scene_bullet(c, bi, n, total, fi)),
             BULLET_DURATION)
        )
    schedule.append((lambda c: (lambda n, total, fi: scene_takeaway(c, n, total, fi)), 10))
    schedule.append((lambda c: (lambda n, total, fi: scene_cta(c, n, total, fi)),     8))

    total_s      = sum(d for _, d in schedule)
    total_frames = total_s * FPS

    try:
        frame_idx = 0
        for make_fn, dur in schedule:
            fn = make_fn(content)
            n  = dur * FPS
            print(f"  {dur}s  ({n} frames)")
            for arr in fn(n, total_frames, frame_idx):
                path = os.path.join(tmp, f"{frame_idx:06d}.jpg")
                Image.fromarray(arr).save(path, quality=95)   # quality=95 for HD
                frame_idx += 1

        print(f"  Total: {frame_idx} frames ({total_s}s) — encoding…")
        cmd = [
            "ffmpeg", "-y",
            "-framerate", str(FPS),
            "-i", os.path.join(tmp, "%06d.jpg"),
            "-c:v", "libx264",
            "-crf", "18",           # high quality (lower = better)
            "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-vf", f"scale={W}:{H}",
            output_path,
        ]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg error:\n{r.stderr}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"  Video  : {output_path}")
    return output_path, total_s
