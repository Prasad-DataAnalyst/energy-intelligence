"""
Generates a pencil-sketch animated video for the daily energy intelligence briefing.

Pipeline
--------
1. Render each "scene" as a sequence of PIL frames (sketch-on-paper look).
2. Apply a pencil-sketch post-process with OpenCV.
3. Assemble all frames into an MP4 via moviepy.

Scenes
------
  0 – Title card (logo + date, draws in)
  1 – Price ticker (4 markets animate up from zero)
  2 – Brent vs WTI 30-day chart (line draws itself)
  3 – Focus topic slide with bullet points
  4 – Outro / subscribe call-to-action
"""

import os
import math
import datetime
import textwrap
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import cv2
from moviepy.editor import ImageSequenceClip

import config

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

W, H = config.WIDTH, config.HEIGHT
FPS = config.FPS
BG = config.PAPER_BG
INK = config.INK_DARK
MID = config.INK_MID
LIGHT = config.INK_LIGHT
AMBER = config.ACCENT_AMBER
BLUE = config.ACCENT_BLUE
RED = config.ACCENT_RED
GREEN = config.ACCENT_GREEN


def _font(size: int, bold: bool = False):
    """Return a PIL font, falling back to default if TTF unavailable."""
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
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _blank() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    return img


def _pencil_sketch(frame_np: np.ndarray) -> np.ndarray:
    """Apply OpenCV pencil-sketch filter to a numpy RGB frame."""
    gray = cv2.cvtColor(frame_np, cv2.COLOR_RGB2GRAY)
    # Invert + blur to simulate pencil-on-paper
    inv = cv2.bitwise_not(gray)
    blur = cv2.GaussianBlur(inv, (21, 21), 0)
    sketch = cv2.divide(gray, cv2.bitwise_not(blur), scale=256.0)
    # Blend with warm cream background
    paper = np.full_like(sketch, 245)
    blended = cv2.addWeighted(sketch, 0.75, paper, 0.25, 0)
    return cv2.cvtColor(blended, cv2.COLOR_GRAY2RGB)


def _add_paper_texture(frame_np: np.ndarray) -> np.ndarray:
    """Overlay a subtle grain to simulate paper texture."""
    noise = np.random.normal(0, 4, frame_np.shape).astype(np.int16)
    return np.clip(frame_np.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def _draw_grid(draw: ImageDraw.ImageDraw, alpha: int = 30):
    """Light pencil grid lines."""
    col = (*LIGHT, alpha) if len(LIGHT) == 3 else LIGHT
    step = 80
    for x in range(0, W, step):
        draw.line([(x, 0), (x, H)], fill=(*LIGHT, 20), width=1)
    for y in range(0, H, step):
        draw.line([(0, y), (W, y)], fill=(*LIGHT, 20), width=1)


# ---------------------------------------------------------------------------
# Scene 0 – Title card
# ---------------------------------------------------------------------------

def scene_title(content: dict, n_frames: int) -> list[np.ndarray]:
    frames = []
    title = "Energy Market Daily"
    date_str = content["date"].strftime("%d %B %Y")
    topic = content["topic"]

    f_title = _font(90, bold=True)
    f_sub = _font(48)
    f_topic = _font(36)

    for i in range(n_frames):
        t = i / n_frames                    # 0 → 1
        img = _blank()
        draw = ImageDraw.Draw(img)

        # Decorative horizontal rules
        y_top = 180
        rule_w = int(W * 0.7 * min(t * 3, 1.0))
        draw.line([(W // 2 - rule_w // 2, y_top),
                   (W // 2 + rule_w // 2, y_top)], fill=AMBER, width=4)

        # Title — reveal left-to-right
        visible_chars = int(len(title) * min(t * 2, 1.0))
        draw.text((W // 2, 260), title[:visible_chars],
                  font=f_title, fill=INK, anchor="mm")

        # Date fades in
        if t > 0.3:
            alpha_date = min((t - 0.3) / 0.3, 1.0)
            date_col = tuple(int(c * alpha_date + 245 * (1 - alpha_date))
                             for c in BLUE)
            draw.text((W // 2, 370), date_str,
                      font=f_sub, fill=date_col, anchor="mm")

        # Topic slugs in
        if t > 0.55:
            alpha_topic = min((t - 0.55) / 0.3, 1.0)
            topic_col = tuple(int(c * alpha_topic + 245 * (1 - alpha_topic))
                              for c in MID)
            draw.text((W // 2, 450), f"Focus: {topic}",
                      font=f_topic, fill=topic_col, anchor="mm")

        # Bottom rule
        if t > 0.7:
            rule_w2 = int(W * 0.7 * min((t - 0.7) / 0.3, 1.0))
            draw.line([(W // 2 - rule_w2 // 2, 520),
                       (W // 2 + rule_w2 // 2, 520)], fill=AMBER, width=4)

        # Sketch + texture
        arr = np.array(img)
        arr = _pencil_sketch(arr)
        arr = _add_paper_texture(arr)
        frames.append(arr)

    return frames


# ---------------------------------------------------------------------------
# Scene 1 – Price ticker
# ---------------------------------------------------------------------------

def scene_prices(content: dict, n_frames: int) -> list[np.ndarray]:
    prices = content["prices"]
    items = [
        ("Brent Crude", prices["brent"], prices["brent_chg"], "$/bbl"),
        ("WTI Crude",   prices["wti"],   prices["wti_chg"],   "$/bbl"),
        ("Henry Hub",   prices["henry_hub"], prices["henry_hub_chg"], "$/MMBtu"),
        ("LNG JKM",     prices["lng_jkm"],   prices["lng_chg"],       "$/MMBtu"),
    ]

    f_label = _font(44, bold=True)
    f_value = _font(72, bold=True)
    f_unit = _font(32)
    f_chg = _font(36)
    f_head = _font(54, bold=True)

    frames = []
    for i in range(n_frames):
        t = i / n_frames
        img = _blank()
        draw = ImageDraw.Draw(img)

        draw.text((W // 2, 80), "Market Prices",
                  font=f_head, fill=INK, anchor="mm")
        draw.line([(200, 130), (W - 200, 130)], fill=AMBER, width=3)

        for idx, (label, val, chg, unit) in enumerate(items):
            # staggered entry
            item_t = max(0, min((t - idx * 0.18) / 0.35, 1.0))
            col_x = 260 + (idx % 2) * (W // 2)
            row_y = 280 + (idx // 2) * 300

            if item_t <= 0:
                continue

            # Box
            bx1, by1 = col_x - 180, row_y - 80
            bx2, by2 = col_x + 220, row_y + 140
            box_alpha = int(item_t * 255)
            draw.rectangle([bx1, by1, bx2, by2],
                           outline=(*MID,), width=2)

            # Label
            draw.text((col_x, row_y - 40), label,
                      font=f_label, fill=INK, anchor="mm")

            # Animated value (counts up)
            display_val = val * item_t
            draw.text((col_x, row_y + 40),
                      f"${display_val:.2f}",
                      font=f_value, fill=INK, anchor="mm")

            draw.text((col_x, row_y + 100), unit,
                      font=f_unit, fill=MID, anchor="mm")

            # Change indicator
            if item_t > 0.7:
                chg_col = GREEN if chg >= 0 else RED
                sign = "▲" if chg >= 0 else "▼"
                draw.text((col_x + 190, row_y + 30),
                          f"{sign} {abs(chg):.2f}",
                          font=f_chg, fill=chg_col, anchor="mm")

        arr = np.array(img)
        arr = _pencil_sketch(arr)
        arr = _add_paper_texture(arr)
        frames.append(arr)

    return frames


# ---------------------------------------------------------------------------
# Scene 2 – Animated 30-day chart
# ---------------------------------------------------------------------------

def scene_chart(content: dict, n_frames: int) -> list[np.ndarray]:
    brent = content["brent_series"]
    wti = content["wti_series"]
    n_points = len(brent)

    f_head = _font(54, bold=True)
    f_axis = _font(28)
    f_legend = _font(34)

    # Chart area
    cx1, cy1 = 160, 160
    cx2, cy2 = W - 160, H - 160
    cw, ch = cx2 - cx1, cy2 - cy1

    all_vals = brent + wti
    y_min = min(all_vals) - 3
    y_max = max(all_vals) + 3

    def to_px(xi, yi):
        px = cx1 + (xi / (n_points - 1)) * cw
        py = cy2 - ((yi - y_min) / (y_max - y_min)) * ch
        return int(px), int(py)

    frames = []
    for i in range(n_frames):
        t = i / n_frames
        visible = max(2, int(t * n_points))

        img = _blank()
        draw = ImageDraw.Draw(img)

        # Title
        draw.text((W // 2, 80), "Brent vs WTI – 30-Day Trend",
                  font=f_head, fill=INK, anchor="mm")
        draw.line([(cx1, cy1), (cx2, cy1)], fill=LIGHT, width=1)

        # Axes
        draw.line([(cx1, cy1), (cx1, cy2)], fill=MID, width=2)
        draw.line([(cx1, cy2), (cx2, cy2)], fill=MID, width=2)

        # Y-axis ticks
        for tick in np.linspace(y_min, y_max, 6):
            _, py = to_px(0, tick)
            draw.line([(cx1 - 8, py), (cx1, py)], fill=MID, width=1)
            draw.text((cx1 - 12, py), f"{tick:.0f}",
                      font=f_axis, fill=MID, anchor="rm")

        # Brent line (draws in)
        brent_pts = [to_px(j, brent[j]) for j in range(visible)]
        if len(brent_pts) > 1:
            draw.line(brent_pts, fill=AMBER, width=4)
            # Dot at tip
            tx, ty = brent_pts[-1]
            draw.ellipse([tx - 6, ty - 6, tx + 6, ty + 6], fill=AMBER)

        # WTI line (draws in with slight delay)
        wti_visible = max(2, int((t - 0.1) * n_points)) if t > 0.1 else 2
        wti_pts = [to_px(j, wti[j]) for j in range(min(wti_visible, n_points))]
        if len(wti_pts) > 1:
            draw.line(wti_pts, fill=BLUE, width=4)
            tx, ty = wti_pts[-1]
            draw.ellipse([tx - 6, ty - 6, tx + 6, ty + 6], fill=BLUE)

        # Legend
        if t > 0.5:
            draw.rectangle([cx2 - 260, cy1 + 10, cx2 - 10, cy1 + 110],
                           outline=LIGHT, width=1)
            draw.line([(cx2 - 250, cy1 + 40), (cx2 - 180, cy1 + 40)],
                      fill=AMBER, width=4)
            draw.text((cx2 - 170, cy1 + 40), "Brent",
                      font=f_legend, fill=AMBER, anchor="lm")
            draw.line([(cx2 - 250, cy1 + 80), (cx2 - 180, cy1 + 80)],
                      fill=BLUE, width=4)
            draw.text((cx2 - 170, cy1 + 80), "WTI",
                      font=f_legend, fill=BLUE, anchor="lm")

        arr = np.array(img)
        arr = _pencil_sketch(arr)
        arr = _add_paper_texture(arr)
        frames.append(arr)

    return frames


# ---------------------------------------------------------------------------
# Scene 3 – Focus topic bullets
# ---------------------------------------------------------------------------

TOPIC_BULLETS = {
    "Gulf Crude Export Outlook": [
        "Saudi Aramco cut OSP by $0.30/bbl for Asia",
        "UAE Murban crude trading above $88/bbl",
        "Kuwait targets 2.65 mbpd by Q3 2026",
        "Red Sea diversions adding 4–6 days transit",
    ],
    "Ras Laffan LNG Restart Update": [
        "Trains 1–8 operational; T9 commissioning",
        "Daily output: 22 Mtpa of 77 Mtpa capacity",
        "Asia spot JKM premium at $1.2/MMBtu",
        "Shipping congestion easing at Strait of Hormuz",
    ],
    "OPEC+ Production Discipline": [
        "Compliance at 96% for May 2026",
        "Iraq committed to compensatory cuts",
        "Russia exports down 180 kbd vs target",
        "Next review: July 2026 Vienna summit",
    ],
    "Brent Crude Weekly Forecast": [
        "Technicals: support at $78, resistance $88",
        "IEA raises 2026 demand forecast by 0.3 mbpd",
        "US SPR refill adds 50 kbd incremental demand",
        "Hedge-fund net-long positioning: +12%",
    ],
    "Offshore Asset Integrity Trends": [
        "Corrosion incidents up 8% YoY in North Sea",
        "ROV inspection cycle now 18-month standard",
        "Digital twin adoption: 34% of assets",
        "Subsea pipeline wall-loss threshold tightened",
    ],
    "HSE Incident Rate Benchmarking": [
        "TRIR industry average: 0.41 per 200k hrs",
        "Process safety events declined 14% in 2025",
        "Safety leadership training linked to -22% LTI",
        "Behaviour-based safety rollout across GCC",
    ],
    "Red Sea Shipping Disruption Impact": [
        "Cape of Good Hope re-routing: +$1.2M per voyage",
        "LNG tanker rerouting delays: avg 6.3 days",
        "European gas prices up 8% on supply anxiety",
        "Suez transit volumes down 42% vs Jan 2024",
    ],
    "Middle East Refining Capacity": [
        "Jizan refinery running at 87% utilisation",
        "Al Zour (Kuwait) starts second-phase units",
        "Ruwais expansion adds 200 kbd by 2027",
        "Arab Light crack spread: $14.3/bbl",
    ],
    "Methane Emission Compliance (OGMP 2.0)": [
        "OGMP Level 5 adoption: 18 new signatories",
        "EU methane regulation effective Jan 2027",
        "Continuous monitoring mandated on 80% wells",
        "Flaring intensity target: <0.3 m3/boe",
    ],
    "Asia LNG Demand Surge Analysis": [
        "Japan spot imports up 12% ahead of summer",
        "South Korea: 3 new FSRUs contracted",
        "India: Petronet capacity at 103% utilisation",
        "China long-term LNG contracts at record 18 mt",
    ],
}


def scene_topic(content: dict, n_frames: int) -> list[np.ndarray]:
    topic = content["topic"]
    bullets = TOPIC_BULLETS.get(topic, ["Analysis pending for this topic."])

    f_head = _font(60, bold=True)
    f_sub = _font(40)
    f_bullet = _font(38)

    frames = []
    for i in range(n_frames):
        t = i / n_frames
        img = _blank()
        draw = ImageDraw.Draw(img)

        # Header
        draw.text((W // 2, 100), "Today's Focus", font=f_head,
                  fill=INK, anchor="mm")
        draw.text((W // 2, 170), topic, font=f_sub,
                  fill=AMBER, anchor="mm")
        draw.line([(200, 210), (W - 200, 210)], fill=AMBER, width=3)

        # Bullets appear one by one
        for b_idx, bullet in enumerate(bullets):
            b_t = max(0, min((t - b_idx * 0.18) / 0.25, 1.0))
            if b_t <= 0:
                continue
            by = 310 + b_idx * 110
            # Dot
            bx = 230
            draw.ellipse([bx - 14, by - 14, bx + 14, by + 14],
                         fill=(*AMBER,) if b_t > 0.5 else (*LIGHT,))
            # Text slides in from right
            text_x = int(280 + (1 - b_t) * 200)
            col = tuple(int(c * b_t + 245 * (1 - b_t)) for c in INK)
            draw.text((text_x, by), bullet, font=f_bullet,
                      fill=col, anchor="lm")

        arr = np.array(img)
        arr = _pencil_sketch(arr)
        arr = _add_paper_texture(arr)
        frames.append(arr)

    return frames


# ---------------------------------------------------------------------------
# Scene 4 – Outro
# ---------------------------------------------------------------------------

def scene_outro(content: dict, n_frames: int) -> list[np.ndarray]:
    f_big = _font(80, bold=True)
    f_med = _font(46)
    f_sm = _font(34)

    frames = []
    for i in range(n_frames):
        t = i / n_frames
        img = _blank()
        draw = ImageDraw.Draw(img)

        if t > 0.1:
            alpha = min((t - 0.1) / 0.3, 1.0)
            col = tuple(int(c * alpha + 245 * (1 - alpha)) for c in INK)
            draw.text((W // 2, H // 2 - 160), "Thanks for watching!",
                      font=f_big, fill=col, anchor="mm")

        if t > 0.35:
            alpha = min((t - 0.35) / 0.3, 1.0)
            col = tuple(int(c * alpha + 245 * (1 - alpha)) for c in BLUE)
            draw.text((W // 2, H // 2 - 40),
                      "Subscribe for daily energy intelligence",
                      font=f_med, fill=col, anchor="mm")

        if t > 0.6:
            alpha = min((t - 0.6) / 0.3, 1.0)
            col = tuple(int(c * alpha + 245 * (1 - alpha)) for c in MID)
            draw.text((W // 2, H // 2 + 80),
                      "Prasad Selvaraj | Energy Intelligence",
                      font=f_sm, fill=col, anchor="mm")
            draw.text((W // 2, H // 2 + 140),
                      "prasad2t@gmail.com",
                      font=f_sm, fill=col, anchor="mm")

        # Pulsing ring (subscribe CTA)
        if t > 0.75:
            pulse = abs(math.sin((t - 0.75) * math.pi * 4))
            r = int(60 + pulse * 20)
            cx, cy = W // 2, H // 2 + 300
            col_ring = tuple(int(c * pulse) for c in RED)
            draw.ellipse([cx - r, cy - r, cx + r, cy + r],
                         outline=RED, width=4)
            draw.text((cx, cy), "SUB", font=_font(36, bold=True),
                      fill=RED, anchor="mm")

        arr = np.array(img)
        arr = _pencil_sketch(arr)
        arr = _add_paper_texture(arr)
        frames.append(arr)

    return frames


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def generate_video(content: dict, output_path: str) -> str:
    """Render all scenes and write MP4 to output_path. Returns output_path."""
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    total_seconds = config.DURATION
    # Allocate seconds per scene
    scene_durations = [8, 40, 60, 52, 20]   # sums to 180 s
    assert sum(scene_durations) == total_seconds

    scene_fns = [scene_title, scene_prices, scene_chart, scene_topic, scene_outro]

    print("Rendering scenes…")
    all_frames: list[np.ndarray] = []
    for fn, dur in zip(scene_fns, scene_durations):
        n = dur * FPS
        print(f"  {fn.__name__}: {n} frames ({dur}s)")
        all_frames.extend(fn(content, n))

    print(f"Total frames: {len(all_frames)} — assembling video…")
    clip = ImageSequenceClip(all_frames, fps=FPS)
    clip.write_videofile(output_path, codec="libx264",
                         audio=False, logger=None,
                         ffmpeg_params=["-crf", "23", "-preset", "fast"])
    print(f"Video written → {output_path}")
    return output_path
