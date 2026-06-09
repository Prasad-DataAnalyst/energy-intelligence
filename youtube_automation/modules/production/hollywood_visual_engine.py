"""
modules/production/hollywood_visual_engine.py
Hollywood-grade 1080p video generation using PIL + OpenCV + MoviePy.
Replaces: video_generator.py
"""

from __future__ import annotations

import math
import os
import textwrap
import urllib.request
from enum import Enum
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ── Canvas constants ──────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1920, 1080
FPS = 24
FONT_DIR = Path(__file__).parent.parent.parent / "assets" / "fonts"


# ── Color Grade Enum ──────────────────────────────────────────────────────────
class ColorGrade(Enum):
    TECH_DARK = "tech_dark"
    DOCUMENTARY = "documentary"
    THRILLER = "thriller"
    EDUCATION = "education"
    NEWS = "news"


# ── Palette definitions ───────────────────────────────────────────────────────
_PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "tech_dark": {
        "bg":        (10,  10,  26),
        "accent":    (0,   212, 255),
        "text":      (255, 255, 255),
        "highlight": (255, 107, 0),
        "shadow":    (0,   80,  120),
    },
    "documentary": {
        "bg":        (26,  26,  20),
        "accent":    (232, 213, 163),
        "text":      (245, 245, 240),
        "highlight": (200, 160, 80),
        "shadow":    (60,  50,  20),
    },
    "thriller": {
        "bg":        (0,   0,   0),
        "accent":    (255, 34,  34),
        "text":      (255, 255, 255),
        "highlight": (200, 0,   0),
        "shadow":    (80,  0,   0),
    },
    "education": {
        "bg":        (15,  27,  45),
        "accent":    (76,  175, 80),
        "text":      (255, 255, 255),
        "highlight": (129, 212, 134),
        "shadow":    (20,  60,  25),
    },
    "news": {
        "bg":        (10,  10,  20),
        "accent":    (200, 30,  30),
        "text":      (255, 255, 255),
        "highlight": (255, 200, 0),
        "shadow":    (80,  10,  10),
    },
}

_STYLE_TO_GRADE: Dict[str, ColorGrade] = {
    "tech_dark":   ColorGrade.TECH_DARK,
    "documentary": ColorGrade.DOCUMENTARY,
    "thriller":    ColorGrade.THRILLER,
    "education":   ColorGrade.EDUCATION,
    "news":        ColorGrade.NEWS,
}

# ── Font cache ────────────────────────────────────────────────────────────────
_FONT_CACHE: Dict[Tuple[int, bool], ImageFont.FreeTypeFont] = {}


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    key = (size, bold)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]

    candidates = []
    if bold:
        candidates += [
            str(FONT_DIR / "Inter-ExtraBold.ttf"),
            str(FONT_DIR / "Montserrat-Bold.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
    else:
        candidates += [
            str(FONT_DIR / "Inter-Regular.ttf"),
            str(FONT_DIR / "Montserrat-Regular.ttf"),
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]

    for path in candidates:
        if os.path.exists(path):
            try:
                font = ImageFont.truetype(path, size)
                _FONT_CACHE[key] = font
                return font
            except Exception:
                pass

    # absolute last resort
    font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ── Math helpers ──────────────────────────────────────────────────────────────
def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _lerp_color(
    a: Tuple[int, int, int], b: Tuple[int, int, int], t: float
) -> Tuple[int, int, int]:
    t = _clamp(t)
    return tuple(int(ca + (cb - ca) * t) for ca, cb in zip(a, b))  # type: ignore[return-value]


def _np_blank() -> np.ndarray:
    return np.zeros((HEIGHT, WIDTH, 3), dtype=np.float32)


def _to_uint8(frame: np.ndarray) -> np.ndarray:
    return np.clip(frame, 0, 255).astype(np.uint8)


def _pil_to_np(img: Image.Image) -> np.ndarray:
    return np.array(img.convert("RGB"), dtype=np.float32)


def _np_to_pil(arr: np.ndarray) -> Image.Image:
    return Image.fromarray(_to_uint8(arr))


def _alpha_blend(base: np.ndarray, overlay: np.ndarray, alpha: float) -> np.ndarray:
    """Blend overlay onto base with global alpha in [0,1]."""
    alpha = _clamp(alpha)
    return base * (1.0 - alpha) + overlay * alpha


# ── Scene time helpers ────────────────────────────────────────────────────────
def _local_t(t: float, scene_start: float, scene_dur: float) -> float:
    """Return normalized [0,1] time within a scene, clamped."""
    return _clamp((t - scene_start) / max(scene_dur, 1e-6))


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENGINE CLASS
# ─────────────────────────────────────────────────────────────────────────────
class HollywoodVisualEngine:
    """
    Hollywood-grade 1080p video engine.
    Generates cinematic YouTube videos using PIL + OpenCV + MoviePy.
    """

    def __init__(self, style: str = "tech_dark") -> None:
        self.style = style.lower()
        if self.style not in _PALETTES:
            self.style = "tech_dark"

        self.grade = _STYLE_TO_GRADE.get(self.style, ColorGrade.TECH_DARK)
        self.palette = _PALETTES[self.style]

        # Ensure font directory exists and try to download premium fonts
        FONT_DIR.mkdir(parents=True, exist_ok=True)
        self._download_font()

    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC ENTRY POINT
    # ─────────────────────────────────────────────────────────────────────────
    def generate_video(self, content: dict, out_path: str) -> Tuple[str, float]:
        """
        Generate a complete cinematic video from content dict.

        content keys: topic, hook, bullets (list[str]), takeaway, category, tags
        Returns: (out_path, total_duration_seconds)
        """
        out_path = str(out_path)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        bullets = content.get("bullets", [""] * 5)
        while len(bullets) < 5:
            bullets.append("")

        # Scene schedule: (scene_start, scene_end, renderer)
        scenes: List[Tuple[float, float, Callable[[float, float], np.ndarray]]] = []

        t = 0.0
        # Scene 1: Hook Slam — 0-6s
        s1_dur = 6.0
        scenes.append((t, t + s1_dur, lambda _t, _s, c=content: self._render_hook_scene(_t, _s, c)))
        t += s1_dur

        # Scene 2: Context Build — 6-12s
        s2_dur = 6.0
        scenes.append((t, t + s2_dur, lambda _t, _s, c=content: self._render_context_scene(_t, _s, c)))
        t += s2_dur

        # Scenes 3-7: Data Points (bullets 1-5) — 4s each
        bullet_dur = 4.0
        for bi in range(5):
            b_text = bullets[bi]
            scenes.append(
                (
                    t,
                    t + bullet_dur,
                    lambda _t, _s, num=bi + 1, txt=b_text: self._render_bullet_scene(
                        _t, _s, num, txt
                    ),
                )
            )
            t += bullet_dur

        # Scene 8: Takeaway Slam — 32-38s
        s8_dur = 6.0
        scenes.append(
            (t, t + s8_dur, lambda _t, _s, c=content: self._render_takeaway_scene(_t, _s, c.get("takeaway", "")))
        )
        t += s8_dur

        # Scene 9: CTA — 38-46s
        s9_dur = 8.0
        next_topic = content.get("next_topic", content.get("topic", ""))
        scenes.append(
            (t, t + s9_dur, lambda _t, _s, nt=next_topic: self._render_cta_scene(_t, _s, nt))
        )
        t += s9_dur

        total_duration = t

        audio_path = content.get("audio_path", "")
        return self._assemble_video(scenes, audio_path, out_path, total_duration), total_duration

    # ─────────────────────────────────────────────────────────────────────────
    # CORE FRAME MAKER
    # ─────────────────────────────────────────────────────────────────────────
    def _make_frame(self, t: float, scene: dict) -> np.ndarray:
        """Return RGB numpy array (HEIGHT, WIDTH, 3) at time t for the given scene."""
        renderer = scene.get("renderer")
        scene_start = scene.get("start", 0.0)
        if renderer is None:
            return _to_uint8(_np_blank())
        frame = renderer(t, scene_start)
        return _to_uint8(frame)

    # ─────────────────────────────────────────────────────────────────────────
    # BACKGROUND SYSTEMS
    # ─────────────────────────────────────────────────────────────────────────
    def _render_bg_gradient(self, variant: int = 0) -> np.ndarray:
        """
        Cinematic gradient backgrounds.
          variant 0 — top-to-bottom dark blue to near-black
          variant 1 — radial glow from center (subtle)
          variant 2 — corner vignette dark
        """
        bg = self.palette["bg"]
        frame = _np_blank()

        if variant == 0:
            # Top-to-bottom gradient: bg color at top, deeper at bottom
            top = np.array(bg, dtype=np.float32)
            bot = np.array([max(0, c - 12) for c in bg], dtype=np.float32)
            for y in range(HEIGHT):
                t = y / HEIGHT
                frame[y, :] = top * (1 - t) + bot * t

        elif variant == 1:
            # Radial glow from center
            cx, cy = WIDTH / 2.0, HEIGHT / 2.0
            base = np.array(bg, dtype=np.float32)
            glow = np.array([min(255, c + 30) for c in bg], dtype=np.float32)
            ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
            dist = np.sqrt(((xs - cx) / (WIDTH * 0.6)) ** 2 + ((ys - cy) / (HEIGHT * 0.6)) ** 2)
            t_map = np.clip(1.0 - dist, 0.0, 1.0) ** 1.5
            frame = base[np.newaxis, np.newaxis, :] + (glow - base)[np.newaxis, np.newaxis, :] * t_map[:, :, np.newaxis]

        elif variant == 2:
            # Corner vignette
            base = np.array(bg, dtype=np.float32)
            cx, cy = WIDTH / 2.0, HEIGHT / 2.0
            ys, xs = np.mgrid[0:HEIGHT, 0:WIDTH]
            dist = np.sqrt(((xs - cx) / (WIDTH * 0.5)) ** 2 + ((ys - cy) / (HEIGHT * 0.5)) ** 2)
            vignette = np.clip(1.0 - dist * 0.5, 0.3, 1.0)
            frame = base[np.newaxis, np.newaxis, :] * vignette[:, :, np.newaxis]
        else:
            frame[:] = np.array(bg, dtype=np.float32)

        return frame

    def _render_bg_particles(self, t: float, n_particles: int = 80) -> np.ndarray:
        """
        Animated particle field. Particles drift upward slowly.
        Deterministic based on t (sin/cos motion). Creates subtle depth.
        """
        frame = _np_blank()
        acc = np.array(self.palette["accent"], dtype=np.float32)

        for i in range(n_particles):
            seed = i * 0.137
            # Base position
            bx = (math.sin(seed * 7.3) * 0.5 + 0.5) * WIDTH
            by = (math.cos(seed * 5.1) * 0.5 + 0.5) * HEIGHT

            # Drift upward + slight horizontal sway
            drift_y = (t * 18.0 * (0.5 + (i % 7) * 0.1)) % HEIGHT
            sway_x = math.sin(t * 0.8 + seed * 3.1) * 12.0

            px = int((bx + sway_x) % WIDTH)
            py = int((by - drift_y) % HEIGHT)

            # Size 1-3 pixels
            size = 1 + (i % 3)
            brightness = 0.15 + 0.25 * (math.sin(seed * 11.3 + t * 1.2) * 0.5 + 0.5)

            # Apply pixel
            x0 = max(0, px - size)
            x1 = min(WIDTH, px + size + 1)
            y0 = max(0, py - size)
            y1 = min(HEIGHT, py + size + 1)
            frame[y0:y1, x0:x1] += acc * brightness

        return np.clip(frame, 0, 255)

    def _render_grid_lines(self, t: float, opacity: float = 0.05) -> np.ndarray:
        """
        Subtle animated grid (tech feel).
        Horizontal + vertical lines at 120px spacing.
        Very low opacity (0.03-0.07). Slowly scroll/fade in.
        """
        frame = _np_blank()
        accent = np.array(self.palette["accent"], dtype=np.float32)

        # Effective opacity pulses gently
        eff_opacity = opacity * (0.8 + 0.2 * math.sin(t * 0.5))
        scroll = (t * 6.0) % 120  # slow scroll

        spacing = 120

        # Horizontal lines
        y = int(scroll) % spacing
        while y < HEIGHT:
            frame[y, :] = accent * eff_opacity
            y += spacing

        # Vertical lines
        x = int(scroll * 0.7) % spacing
        while x < WIDTH:
            frame[:, x] = accent * eff_opacity
            x += spacing

        return frame

    # ─────────────────────────────────────────────────────────────────────────
    # TEXT RENDERING SYSTEMS
    # ─────────────────────────────────────────────────────────────────────────
    def _slam_text(
        self,
        frame: np.ndarray,
        text: str,
        t: float,
        scene_start: float,
        duration: float,
        font_size: int = 90,
        y_pct: float = 0.45,
    ) -> np.ndarray:
        """
        SLAM animation:
          0-0.15s  : scale 130%→100% with slight overshoot to 102%
          0.15s-...: stable
          last 0.3s: fade out
        """
        local = t - scene_start
        if local < 0 or local > duration:
            return frame

        # Scale calculation
        slam_dur = 0.15
        if local < slam_dur:
            prog = local / slam_dur
            # Ease: overshoot spring. 130% → 102% via cubic then slight bounce
            ease_prog = self._ease_in_out_cubic(prog)
            scale = 1.30 - 0.28 * ease_prog + 0.02 * math.sin(ease_prog * math.pi)
        else:
            scale = 1.0

        # Alpha: fade out in last 0.3s
        fade_dur = 0.3
        if local > duration - fade_dur:
            alpha = _clamp((duration - local) / fade_dur)
        else:
            alpha = 1.0

        if alpha <= 0.01:
            return frame

        # Wrap text
        max_chars = int(WIDTH * 0.70 / (font_size * 0.55))
        wrapped = textwrap.wrap(text, width=max(10, max_chars))

        effective_size = max(12, int(font_size * scale))
        font = _load_font(effective_size, bold=True)
        lh = int(effective_size * 1.25)

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        total_h = len(wrapped) * lh
        y_base = int(HEIGHT * y_pct) - total_h // 2

        for li, line in enumerate(wrapped):
            bbox = draw.textbbox((0, 0), line, font=font)
            tw_px = bbox[2] - bbox[0]
            cx = WIDTH // 2
            cy = y_base + li * lh

            # Drop shadow: electric blue, offset 4,4
            shadow_col = self.palette.get("shadow", (0, 80, 120))
            shadow_rgba = (*shadow_col, int(200 * alpha))
            white_rgba = (255, 255, 255, int(255 * alpha))

            draw.text((cx + 4, cy + 4), line, font=font, fill=shadow_rgba, anchor="mm")
            draw.text((cx, cy), line, font=font, fill=white_rgba, anchor="mm")

        return _pil_to_np(img)

    def _typewriter_text(
        self,
        frame: np.ndarray,
        text: str,
        t: float,
        scene_start: float,
        chars_per_sec: float = 40.0,
        font_size: int = 48,
        y_pos: int = 600,
    ) -> np.ndarray:
        """
        Typewriter effect. Characters appear one by one at chars_per_sec rate.
        Cursor blinks at end (2Hz).
        """
        local = t - scene_start
        if local < 0:
            return frame

        n_chars = int(local * chars_per_sec)
        visible = text[:min(n_chars, len(text))]

        # Cursor blink (2Hz) — show if not all chars revealed yet or blink at end
        show_cursor = True
        if n_chars >= len(text):
            show_cursor = (math.sin(t * 2 * math.pi * 2) > 0)

        display = visible + ("|" if show_cursor else "")

        font = _load_font(font_size, bold=False)
        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]

        # Wrap text
        max_chars = int(WIDTH * 0.80 / (font_size * 0.55))
        wrapped = textwrap.wrap(display, width=max(10, max_chars))

        lh = int(font_size * 1.3)
        for li, line in enumerate(wrapped):
            y = y_pos + li * lh
            draw.text((WIDTH // 2, y), line, font=font, fill=(*text_col, 230), anchor="mm")

        return _pil_to_np(img)

    def _number_card(
        self,
        frame: np.ndarray,
        number: int,
        fact: str,
        t: float,
        scene_start: float,
    ) -> np.ndarray:
        """
        Cinematic number card.
        Left side: large number with count-up animation, electric blue glow.
        Right side: fact text.
        Horizontal separator animates left-to-right.
        """
        local = t - scene_start
        if local < 0:
            return frame

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]

        # Count-up: 0 → number over 0.5s
        count_dur = 0.5
        if local < count_dur:
            displayed_num = int(number * self._ease_in_out_cubic(local / count_dur))
        else:
            displayed_num = number

        # Pulsing glow after count-up
        glow_alpha = 0
        if local > count_dur:
            glow_alpha = int(60 + 40 * math.sin((local - count_dur) * math.pi * 1.5))

        # Left panel: 30% of width
        left_cx = int(WIDTH * 0.18)
        num_font = _load_font(220, bold=True)
        num_str = str(displayed_num)

        # Glow effect: draw slightly blurred version in accent color
        if glow_alpha > 0:
            for offset in [(0, 6), (6, 0), (-6, 0), (0, -6), (4, 4), (-4, -4)]:
                draw.text(
                    (left_cx + offset[0], HEIGHT // 2 + offset[1]),
                    num_str,
                    font=num_font,
                    fill=(*accent, glow_alpha),
                    anchor="mm",
                )

        draw.text((left_cx, HEIGHT // 2), num_str, font=num_font, fill=(*accent, 255), anchor="mm")

        # Separator line: animates left-to-right over 0.4s
        sep_x_start = int(WIDTH * 0.32)
        sep_y = HEIGHT // 2
        sep_max_len = int(WIDTH * 0.04)
        sep_prog = _clamp(local / 0.4)
        sep_end = sep_x_start + int(sep_max_len * sep_prog)
        if sep_end > sep_x_start:
            draw.rectangle([sep_x_start, sep_y - 3, sep_end, sep_y + 3], fill=accent)

        # Right panel: fact text
        if local > 0.2:
            fact_font = _load_font(52, bold=False)
            wrapped = textwrap.wrap(fact, width=38)
            lh = 65
            y0 = HEIGHT // 2 - (len(wrapped) * lh) // 2
            fact_alpha = _clamp((local - 0.2) / 0.3)
            for li, line in enumerate(wrapped):
                col = (*text_col, int(230 * fact_alpha))
                draw.text((int(WIDTH * 0.37), y0 + li * lh), line, font=fact_font, fill=col, anchor="lm")

        return _pil_to_np(img)

    def _lower_third(
        self,
        frame: np.ndarray,
        text: str,
        t: float,
        scene_start: float,
        source: str = "",
    ) -> np.ndarray:
        """
        Professional lower third.
        Slides in from LEFT at scene_start+0.3s (0.25s ease animation).
        Stays 3s then slides back left (0.2s).
        """
        trigger = scene_start + 0.3
        slide_in_dur = 0.25
        hold_dur = 3.0
        slide_out_dur = 0.2

        if t < trigger:
            return frame

        local = t - trigger
        total_dur = slide_in_dur + hold_dur + slide_out_dur

        if local > total_dur:
            return frame

        if local < slide_in_dur:
            slide_prog = self._ease_in_out_cubic(local / slide_in_dur)
            offset_x = int((1.0 - slide_prog) * (-WIDTH * 0.45))
        elif local < slide_in_dur + hold_dur:
            offset_x = 0
        else:
            slide_prog = self._ease_in_out_cubic((local - slide_in_dur - hold_dur) / slide_out_dur)
            offset_x = int(slide_prog * (-WIDTH * 0.45))

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]
        gray = (160, 160, 160)

        bar_x = int(WIDTH * 0.04) + offset_x
        bar_y = int(HEIGHT * 0.80)
        bar_w = int(WIDTH * 0.40)
        bar_h = 6

        # Accent line above text
        draw.rectangle([bar_x, bar_y, bar_x + bar_w, bar_y + bar_h], fill=accent)

        # Main text
        main_font = _load_font(40, bold=True)
        draw.text((bar_x, bar_y + bar_h + 10), text, font=main_font, fill=(*text_col, 230), anchor="la")

        # Source credit below
        if source:
            src_font = _load_font(30, bold=False)
            draw.text(
                (bar_x, bar_y + bar_h + 60),
                source,
                font=src_font,
                fill=(*gray, 180),
                anchor="la",
            )

        return _pil_to_np(img)

    # ─────────────────────────────────────────────────────────────────────────
    # MOTION EFFECTS
    # ─────────────────────────────────────────────────────────────────────────
    def _apply_zoom(
        self,
        frame: np.ndarray,
        scale: float,
        cx: float = 0.5,
        cy: float = 0.5,
    ) -> np.ndarray:
        """Zoom frame around center point using cv2.resize + crop."""
        if abs(scale - 1.0) < 1e-4:
            return frame

        scale = max(0.1, scale)
        uint8 = _to_uint8(frame)

        new_w = int(WIDTH * scale)
        new_h = int(HEIGHT * scale)

        resized = cv2.resize(uint8, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        # Crop back to original size, centered on (cx, cy)
        center_x = int(cx * new_w)
        center_y = int(cy * new_h)

        x0 = max(0, center_x - WIDTH // 2)
        y0 = max(0, center_y - HEIGHT // 2)
        x1 = x0 + WIDTH
        y1 = y0 + HEIGHT

        if x1 > new_w:
            x1 = new_w
            x0 = max(0, x1 - WIDTH)
        if y1 > new_h:
            y1 = new_h
            y0 = max(0, y1 - HEIGHT)

        cropped = resized[y0:y1, x0:x1]

        # Pad if needed (edge case)
        if cropped.shape[0] < HEIGHT or cropped.shape[1] < WIDTH:
            pad_frame = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            ch = min(cropped.shape[0], HEIGHT)
            cw = min(cropped.shape[1], WIDTH)
            pad_frame[:ch, :cw] = cropped[:ch, :cw]
            cropped = pad_frame

        return cropped.astype(np.float32)

    def _ease_in_out_cubic(self, t: float) -> float:
        """Cubic ease in-out: t in [0,1] → [0,1]."""
        t = _clamp(t)
        if t < 0.5:
            return 4.0 * t * t * t
        else:
            p = 2.0 * t - 2.0
            return 1.0 + 0.5 * p * p * p

    def _apply_ken_burns(
        self,
        frame: np.ndarray,
        t: float,
        duration: float,
        direction: str = "push_in",
    ) -> np.ndarray:
        """
        Ken Burns effect:
          push_in  : scale 1.0 → 1.08 (ease in-out cubic)
          pull_out : scale 1.08 → 1.0
          pan_right: translate X 0 → +3% width
          pan_left : translate X 0 → -3% width
        """
        prog = self._ease_in_out_cubic(_clamp(t / max(duration, 1e-6)))

        if direction == "push_in":
            scale = 1.0 + 0.08 * prog
            return self._apply_zoom(frame, scale)

        elif direction == "pull_out":
            scale = 1.08 - 0.08 * prog
            return self._apply_zoom(frame, scale)

        elif direction in ("pan_right", "pan_left"):
            sign = 1.0 if direction == "pan_right" else -1.0
            shift = int(sign * WIDTH * 0.03 * prog)
            uint8 = _to_uint8(frame)
            M = np.float32([[1, 0, shift], [0, 1, 0]])
            shifted = cv2.warpAffine(uint8, M, (WIDTH, HEIGHT), borderMode=cv2.BORDER_REFLECT)
            return shifted.astype(np.float32)

        return frame

    def _zoom_punch(
        self,
        frame: np.ndarray,
        t: float,
        trigger_t: float,
        intensity: float = 0.05,
    ) -> np.ndarray:
        """
        Beat hit effect: instant zoom to (1+intensity)x in 3 frames, ease back in 8 frames.
        """
        elapsed = t - trigger_t
        if elapsed < 0:
            return frame

        in_frames = 3.0 / FPS
        out_frames = 8.0 / FPS
        total = in_frames + out_frames

        if elapsed > total:
            return frame

        if elapsed <= in_frames:
            prog = elapsed / in_frames
            scale = 1.0 + intensity * prog
        else:
            prog = (elapsed - in_frames) / out_frames
            scale = 1.0 + intensity * (1.0 - self._ease_in_out_cubic(prog))

        return self._apply_zoom(frame, scale)

    # ─────────────────────────────────────────────────────────────────────────
    # DATA VISUALIZATION
    # ─────────────────────────────────────────────────────────────────────────
    def _render_stat_card(
        self,
        frame: np.ndarray,
        stat_value: str,
        label: str,
        t: float,
        scene_start: float,
    ) -> np.ndarray:
        """
        Cinematic stat reveal:
        Background rectangle with gradient, large count-up number, typewriter label.
        """
        local = t - scene_start
        if local < 0:
            return frame

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]
        bg = self.palette["bg"]

        # Card background
        card_x0 = WIDTH // 2 - 340
        card_y0 = HEIGHT // 2 - 160
        card_x1 = WIDTH // 2 + 340
        card_y1 = HEIGHT // 2 + 160

        # Gradient fill: manual approach with horizontal strips
        for y in range(card_y0, card_y1):
            t_strip = (y - card_y0) / max(1, card_y1 - card_y0)
            strip_col = tuple(
                int(bg[c] + (min(255, bg[c] + 35) - bg[c]) * (1 - t_strip))
                for c in range(3)
            )
            draw.rectangle([card_x0, y, card_x1, y], fill=strip_col)

        # Card border
        border_alpha = _clamp(local / 0.3)
        draw.rectangle([card_x0, card_y0, card_x1, card_y1], outline=(*accent, int(200 * border_alpha)), width=2)

        # Stat value — parse numeric portion for count-up
        try:
            num_val = int("".join(c for c in stat_value if c.isdigit()))
            count_dur = 0.6
            if local < count_dur:
                displayed = str(int(num_val * self._ease_in_out_cubic(local / count_dur)))
            else:
                displayed = str(num_val)
            # Re-attach units
            suffix = stat_value.lstrip("0123456789")
            display_str = displayed + suffix
        except (ValueError, AttributeError):
            display_str = stat_value

        stat_font = _load_font(120, bold=True)
        draw.text((WIDTH // 2, HEIGHT // 2 - 40), display_str, font=stat_font, fill=(*accent, 255), anchor="mm")

        # Accent line under number
        draw.rectangle([WIDTH // 2 - 200, HEIGHT // 2 + 50, WIDTH // 2 + 200, HEIGHT // 2 + 56], fill=accent)

        # Label: typewriter
        if local > 0.4:
            label_progress = _clamp((local - 0.4) / 0.5)
            n_chars = int(len(label) * label_progress)
            label_font = _load_font(38, bold=False)
            draw.text(
                (WIDTH // 2, HEIGHT // 2 + 80),
                label[:n_chars],
                font=label_font,
                fill=(*text_col, 200),
                anchor="mm",
            )

        return _pil_to_np(img)

    # ─────────────────────────────────────────────────────────────────────────
    # FULL SCENE RENDERERS
    # ─────────────────────────────────────────────────────────────────────────
    def _render_hook_scene(self, t: float, scene_start: float, content: dict) -> np.ndarray:
        """
        Scene 1 (0-6s): HOOK SLAM
        Background: TECH_DARK gradient + slow particle field
        Full-screen hook text (SLAM animation)
        Bottom: 'Mind Fuel Daily' wordmark fades in after 2s
        Ken Burns push-in on whole frame
        """
        scene_dur = 6.0
        local = t - scene_start

        # Background
        frame = self._render_bg_gradient(variant=0)

        # Particle field
        particles = self._render_bg_particles(t, n_particles=80)
        frame = _alpha_blend(frame, particles, 0.6)

        # Grid lines
        grid = self._render_grid_lines(t, opacity=0.04)
        frame = frame + grid

        # Hook text SLAM
        hook_text = content.get("hook", content.get("topic", ""))
        frame = self._slam_text(
            frame,
            hook_text,
            t,
            scene_start,
            scene_dur,
            font_size=90,
            y_pct=0.45,
        )

        # "Mind Fuel Daily" wordmark fades in after 2s
        if local > 2.0:
            wordmark_alpha = _clamp((local - 2.0) / 1.0)
            img = _np_to_pil(frame)
            draw = ImageDraw.Draw(img)
            wm_font = _load_font(36, bold=True)
            gray = (120, 120, 120)
            draw.text(
                (WIDTH // 2, HEIGHT - 60),
                "MIND FUEL DAILY",
                font=wm_font,
                fill=(*gray, int(180 * wordmark_alpha)),
                anchor="mm",
            )
            frame = _pil_to_np(img)

        # Ken Burns push-in: 0.5% over 6s (very subtle)
        frame = self._apply_ken_burns(frame, local, scene_dur, direction="push_in")

        return frame

    def _render_context_scene(self, t: float, scene_start: float, content: dict) -> np.ndarray:
        """
        Scene 2 (6-12s): CONTEXT BUILD — topic introduction
        """
        scene_dur = 6.0
        local = t - scene_start

        frame = self._render_bg_gradient(variant=1)
        grid = self._render_grid_lines(t, opacity=0.03)
        frame = frame + grid

        topic = content.get("topic", "")
        category = content.get("category", "")

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]

        # Category label top-left
        if local > 0.1:
            cat_alpha = _clamp((local - 0.1) / 0.4)
            cat_font = _load_font(34, bold=True)
            cat_bg_w, cat_bg_h = 280, 52
            draw.rectangle([32, 32, 32 + cat_bg_w, 32 + cat_bg_h], fill=accent)
            draw.text(
                (32 + cat_bg_w // 2, 32 + cat_bg_h // 2),
                category.upper(),
                font=cat_font,
                fill=(0, 0, 0),
                anchor="mm",
            )

        # Topic text
        if local > 0.3:
            topic_alpha = _clamp((local - 0.3) / 0.5)
            topic_font = _load_font(72, bold=True)
            wrapped = textwrap.wrap(topic, width=36)
            lh = 85
            y0 = HEIGHT // 2 - (len(wrapped) * lh) // 2
            for li, line in enumerate(wrapped):
                slide = int((1.0 - self._ease_in_out_cubic(_clamp((local - 0.3 - li * 0.1) / 0.5))) * 80)
                alpha_this = _clamp((local - 0.3 - li * 0.1) / 0.5)
                col = tuple(int(c * alpha_this) for c in text_col)
                draw.text((WIDTH // 2, y0 + li * lh + slide), line, font=topic_font, fill=col, anchor="mm")

        # Underline accent bar
        if local > 0.8:
            bar_prog = _clamp((local - 0.8) / 0.5)
            bar_w = int((WIDTH - 400) * bar_prog)
            draw.rectangle([200, HEIGHT // 2 + 120, 200 + bar_w, HEIGHT // 2 + 128], fill=accent)

        # Subtitle
        if local > 1.2:
            sub_alpha = _clamp((local - 1.2) / 0.4)
            sub_font = _load_font(42, bold=False)
            draw.text(
                (WIDTH // 2, HEIGHT // 2 + 160),
                "5 things you need to know today",
                font=sub_font,
                fill=(160, 160, 160, int(200 * sub_alpha)),
                anchor="mm",
            )

        frame = _pil_to_np(img)
        frame = self._apply_ken_burns(frame, local, scene_dur, direction="pan_right")
        return frame

    def _render_bullet_scene(
        self,
        t: float,
        scene_start: float,
        bullet_num: int,
        bullet_text: str,
    ) -> np.ndarray:
        """
        Scene 3-7 (12-32s): DATA POINT — number card + bullet text
        Left: large number card, Right: bullet text typewriter
        Zoom punch at 0.2s into scene.
        """
        scene_dur = 4.0
        local = t - scene_start

        frame = self._render_bg_gradient(variant=2)
        grid = self._render_grid_lines(t, opacity=0.05)
        frame = frame + grid

        particles = self._render_bg_particles(t, n_particles=40)
        frame = _alpha_blend(frame, particles, 0.4)

        # Number card on left
        frame = self._number_card(frame, bullet_num, bullet_text, t, scene_start)

        # Zoom punch at 0.2s
        frame = self._zoom_punch(frame, t, scene_start + 0.2, intensity=0.04)

        return frame

    def _render_takeaway_scene(
        self, t: float, scene_start: float, takeaway: str
    ) -> np.ndarray:
        """
        Scene 8 (32-38s): TAKEAWAY SLAM
        Full screen with dramatic radial glow.
        "KEY TAKEAWAY" label fades first, then main text SLAMS.
        """
        scene_dur = 6.0
        local = t - scene_start

        frame = self._render_bg_gradient(variant=1)
        particles = self._render_bg_particles(t, n_particles=60)
        frame = _alpha_blend(frame, particles, 0.5)

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]

        # "KEY TAKEAWAY" label fades in first (small, uppercase)
        if local > 0.1:
            label_alpha = _clamp((local - 0.1) / 0.4)
            label_font = _load_font(44, bold=True)
            label_y = int(HEIGHT * 0.30)
            label_w = 400
            draw.rectangle(
                [WIDTH // 2 - label_w // 2, label_y - 26, WIDTH // 2 + label_w // 2, label_y + 26],
                fill=accent,
            )
            draw.text(
                (WIDTH // 2, label_y),
                "KEY  TAKEAWAY",
                font=label_font,
                fill=(0, 0, 0, int(255 * label_alpha)),
                anchor="mm",
            )

        frame = _pil_to_np(img)

        # Main takeaway text SLAM animation (starts at 0.5s offset, duration = scene_dur - 0.5)
        if local > 0.4:
            frame = self._slam_text(
                frame,
                takeaway,
                t,
                scene_start + 0.4,
                scene_dur - 0.4,
                font_size=80,
                y_pct=0.52,
            )

        # "Save this. Act on it today." fades in near end
        if local > scene_dur - 1.5:
            end_alpha = _clamp((local - (scene_dur - 1.5)) / 0.5)
            img2 = _np_to_pil(frame)
            draw2 = ImageDraw.Draw(img2)
            sm_font = _load_font(40, bold=False)
            draw2.text(
                (WIDTH // 2, HEIGHT - 100),
                "Save this. Act on it today.",
                font=sm_font,
                fill=(160, 160, 160, int(200 * end_alpha)),
                anchor="mm",
            )
            frame = _pil_to_np(img2)

        # Ken Burns pull-out for drama
        frame = self._apply_ken_burns(frame, local, scene_dur, direction="pull_out")
        return frame

    def _render_cta_scene(
        self, t: float, scene_start: float, next_topic: str = ""
    ) -> np.ndarray:
        """
        Scene 9 (38-46s): CTA
        SUBSCRIBE text, pulsing ring animation, bell icon, channel name.
        """
        scene_dur = 8.0
        local = t - scene_start

        frame = self._render_bg_gradient(variant=0)
        particles = self._render_bg_particles(t, n_particles=60)
        frame = _alpha_blend(frame, particles, 0.4)

        img = _np_to_pil(frame)
        draw = ImageDraw.Draw(img)

        accent = self.palette["accent"]
        text_col = self.palette["text"]

        # Top accent bar
        draw.rectangle([0, 0, WIDTH, 10], fill=accent)

        # "Thanks for watching!"
        if local > 0.1:
            ta_alpha = _clamp((local - 0.1) / 0.4)
            big_font = _load_font(90, bold=True)
            col = tuple(int(c * ta_alpha) for c in text_col)
            draw.text((WIDTH // 2, 200), "Thanks for watching!", font=big_font, fill=col, anchor="mm")

        # Subtext
        if local > 0.5:
            sub_alpha = _clamp((local - 0.5) / 0.4)
            sub_font = _load_font(48, bold=False)
            draw.text(
                (WIDTH // 2, HEIGHT // 2 - 80),
                "New mind fuel drops every day at 3 PM.",
                font=sub_font,
                fill=(160, 160, 160, int(200 * sub_alpha)),
                anchor="mm",
            )

        # SUBSCRIBE button with pulsing ring
        sub_cx, sub_cy = WIDTH // 2, HEIGHT // 2 + 80
        if local > 0.7:
            sub_alpha = _clamp((local - 0.7) / 0.4)
            pulse = 1.0 + 0.07 * math.sin(t * math.pi * 3.0)
            sub_r = int(90 * pulse)

            # Outer pulsing ring
            ring_col = (*accent, int(80 * sub_alpha))
            ring_r = int(sub_r * 1.35)
            draw.ellipse(
                [sub_cx - ring_r, sub_cy - ring_r, sub_cx + ring_r, sub_cy + ring_r],
                outline=ring_col,
                width=3,
            )
            # Second ring
            ring_r2 = int(sub_r * 1.65)
            ring_col2 = (*accent, int(30 * sub_alpha))
            draw.ellipse(
                [sub_cx - ring_r2, sub_cy - ring_r2, sub_cx + ring_r2, sub_cy + ring_r2],
                outline=ring_col2,
                width=2,
            )

            # Subscribe circle fill
            draw.ellipse(
                [sub_cx - sub_r, sub_cy - sub_r, sub_cx + sub_r, sub_cy + sub_r],
                fill=(*accent, int(200 * sub_alpha)),
            )

            # Bell icon (simple PIL shape)
            bx, by = sub_cx, sub_cy - 8
            bell_col = (0, 0, 0, int(220 * sub_alpha))
            # Bell body arc
            bell_w, bell_h = 38, 32
            draw.ellipse([bx - bell_w // 2, by - bell_h, bx + bell_w // 2, by + 4], fill=bell_col)
            # Bell handle
            draw.rectangle([bx - 5, by - bell_h - 6, bx + 5, by - bell_h + 4], fill=bell_col)
            # Bell bottom clapper base
            draw.ellipse([bx - 8, by + 2, bx + 8, by + 14], fill=bell_col)

            # SUBSCRIBE text inside circle
            if sub_alpha > 0.4:
                btn_font = _load_font(28, bold=True)
                draw.text((sub_cx, sub_cy + 52), "SUBSCRIBE", font=btn_font, fill=(0, 0, 0, 220), anchor="mm")

        # Channel name
        if local > 1.2:
            ch_alpha = _clamp((local - 1.2) / 0.4)
            ch_font = _load_font(38, bold=False)
            draw.text(
                (WIDTH // 2, HEIGHT // 2 + 210),
                "@GetMindFuelNow  •  Mind Fuel Daily",
                font=ch_font,
                fill=(120, 120, 120, int(180 * ch_alpha)),
                anchor="mm",
            )

        # Next video teaser
        if next_topic and local > 1.8:
            nv_alpha = _clamp((local - 1.8) / 0.5)
            nv_font = _load_font(36, bold=False)
            draw.text(
                (WIDTH // 2, HEIGHT - 70),
                f"Next: {next_topic}",
                font=nv_font,
                fill=(*accent, int(180 * nv_alpha)),
                anchor="mm",
            )

        return _pil_to_np(img)

    # ─────────────────────────────────────────────────────────────────────────
    # FINAL ASSEMBLY
    # ─────────────────────────────────────────────────────────────────────────
    def _assemble_video(
        self,
        scenes: List[Tuple[float, float, Callable]],
        audio_path: str,
        out_path: str,
        total_duration: float,
    ) -> str:
        """
        Assemble all scenes into a final video using MoviePy.
        """
        from moviepy import VideoClip

        def make_frame(t: float) -> np.ndarray:
            for scene_start, scene_end, renderer in scenes:
                if scene_start <= t < scene_end:
                    try:
                        frame = renderer(t, scene_start)
                        return _to_uint8(frame)
                    except Exception as exc:
                        print(f"[HollywoodVisualEngine] Frame error at t={t:.2f}: {exc}")
                        return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
            return np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)

        print(f"[HollywoodVisualEngine] Rendering {total_duration:.1f}s @ {FPS}fps → {out_path}")

        clip = VideoClip(frame_function=make_frame, duration=total_duration)
        clip = clip.with_fps(FPS)

        # Attach audio if provided
        if audio_path and os.path.exists(audio_path):
            try:
                from moviepy import AudioFileClip
                audio = AudioFileClip(audio_path)
                # Trim audio to video length
                if audio.duration > total_duration:
                    audio = audio.with_end(total_duration)
                clip = clip.with_audio(audio)
            except Exception as exc:
                print(f"[HollywoodVisualEngine] Audio attach failed: {exc}")

        clip.write_videofile(
            out_path,
            fps=FPS,
            codec="libx264",
            bitrate="8000k",
            audio_codec="aac",
            ffmpeg_params=["-crf", "18", "-pix_fmt", "yuv420p"],
            logger="bar",
        )

        return out_path

    # ─────────────────────────────────────────────────────────────────────────
    # FONT DOWNLOADER
    # ─────────────────────────────────────────────────────────────────────────
    def _download_font(self) -> bool:
        """
        Try to download Inter-ExtraBold.ttf from a public CDN.
        Returns True on success, False on failure (falls back to DejaVuSans-Bold).
        """
        target = FONT_DIR / "Inter-ExtraBold.ttf"
        if target.exists():
            return True

        urls = [
            "https://github.com/rsms/inter/raw/master/docs/font-files/Inter-ExtraBold.ttf",
            "https://fonts.gstatic.com/s/inter/v13/UcCO3FwrK3iLTeHuS_fvQtMwCp50KnMw2boKoduKmMEVuLyfAZ9hiA.woff2",
        ]

        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = resp.read()
                if len(data) > 10_000:
                    target.write_bytes(data)
                    print(f"[HollywoodVisualEngine] Downloaded font → {target}")
                    return True
            except Exception:
                pass

        print("[HollywoodVisualEngine] Font download failed — using DejaVuSans-Bold fallback")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# CONVENIENCE FUNCTION (drop-in replacement for video_generator.generate_video)
# ─────────────────────────────────────────────────────────────────────────────
def generate_video(content: dict, output_path: str, style: str = "tech_dark") -> Tuple[str, float]:
    """
    Drop-in replacement for video_generator.generate_video().

    Args:
        content: dict with keys topic, hook, bullets, takeaway, category, tags
        output_path: where to write the .mp4
        style: one of tech_dark, documentary, thriller, education, news

    Returns:
        (output_path, total_duration_seconds)
    """
    engine = HollywoodVisualEngine(style=style)
    return engine.generate_video(content, output_path)
