"""
core/horoscope_pipeline.py
GetMindFuelNow — Dedicated Horoscope Video Production Pipeline

Replaces the general trend-based pipeline with a purpose-built system
covering all 12 zodiac signs across 5 video types.

Stages:
  1. CONTENT    — generate all 12 sign readings via content_engine
  2. AUDIO      — voiceover via CinematicAudioEngine (espeak-ng / edge-tts)
  3. THUMBNAIL  — zodiac wheel image via PIL (no moviepy)
  4. VIDEO      — ffmpeg: dark purple/navy gradient + zodiac overlays
  5. MUX        — combine audio + video
  6. UPLOAD     — YouTube with chapters in description

Usage:
  python core/horoscope_pipeline.py --now
  python core/horoscope_pipeline.py --now --type weekly
  python core/horoscope_pipeline.py --now --no-upload
  python core/horoscope_pipeline.py --schedule
"""

from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

# ── Logging ───────────────────────────────────────────────────────────────────
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
today_str = datetime.date.today().isoformat()
_log_file = LOGS_DIR / f"horoscope_{today_str}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(_log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("horoscope_pipeline")

# ── Retry queue path ──────────────────────────────────────────────────────────
RETRY_QUEUE_PATH = HERE / "logs" / "upload_retry_queue.json"


# ---------------------------------------------------------------------------
# Divider helpers
# ---------------------------------------------------------------------------

def _div(label: str = "") -> None:
    w = 60
    if label:
        pad = (w - len(label) - 2) // 2
        log.info("═" * pad + f" {label} " + "═" * pad)
    else:
        log.info("═" * w)


def _elapsed(t0: float) -> str:
    s = time.time() - t0
    return f"{s:.1f}s" if s < 60 else f"{s / 60:.1f}min"


# ---------------------------------------------------------------------------
# Retry queue management
# ---------------------------------------------------------------------------

def _load_retry_queue() -> list[dict]:
    if RETRY_QUEUE_PATH.exists():
        try:
            return json.loads(RETRY_QUEUE_PATH.read_text())
        except Exception:
            pass
    return []


def _save_retry_queue(queue: list[dict]) -> None:
    RETRY_QUEUE_PATH.write_text(json.dumps(queue, indent=2, default=str))


def _add_to_retry_queue(video_path: str, metadata: dict) -> None:
    queue = _load_retry_queue()
    queue.append({
        "video_path": video_path,
        "metadata": metadata,
        "queued_at": datetime.datetime.utcnow().isoformat(),
        "attempts": 0,
    })
    _save_retry_queue(queue)
    log.info(f"Added to retry queue: {video_path}")


def process_retry_queue() -> int:
    """Attempt to upload any videos in the retry queue. Returns number processed."""
    queue = _load_retry_queue()
    if not queue:
        return 0

    remaining = []
    processed = 0
    for item in queue:
        video_path = item.get("video_path", "")
        if not os.path.exists(video_path):
            log.warning(f"Retry queue: video not found, dropping: {video_path}")
            continue

        item["attempts"] = item.get("attempts", 0) + 1
        try:
            from youtube_uploader import upload_video
            url = upload_video(video_path, item.get("metadata", {}))
            log.info(f"Retry upload succeeded: {url}")
            processed += 1
        except Exception as e:
            log.warning(f"Retry upload failed (attempt {item['attempts']}): {e}")
            if item["attempts"] < 5:
                remaining.append(item)
            else:
                log.error(f"Dropping after 5 failed attempts: {video_path}")

    _save_retry_queue(remaining)
    return processed


# ---------------------------------------------------------------------------
# Stage 1: Content generation
# ---------------------------------------------------------------------------

def _stage_content(
    video_type: str,
    date: datetime.date,
    event_name: str,
    event_type: str,
) -> dict:
    """Generate all 12 sign readings and full voiceover script."""
    _div("STAGE 1 — HOROSCOPE CONTENT")
    t0 = time.time()

    from modules.horoscope.content_engine import (
        generate_all_signs,
        get_video_title,
        get_video_description,
        get_tags,
        build_full_voiceover_script,
    )

    readings = generate_all_signs(video_type, date, event_name, event_type)
    title = get_video_title(video_type, date, event_name)
    description = get_video_description(video_type, date, readings, event_name)
    tags = get_tags(video_type)
    full_script = build_full_voiceover_script(readings, video_type, date, event_name)

    log.info(f"Generated {len(readings)} sign readings for type={video_type!r}")
    log.info(f"Title: {title}")
    log.info(f"Script length: {len(full_script.split())} words")
    log.info(f"Elapsed: {_elapsed(t0)}")

    return {
        "readings": readings,
        "title": title,
        "description": description,
        "tags": tags,
        "full_script": full_script,
        "ok": True,
    }


# ---------------------------------------------------------------------------
# Stage 2: Audio generation
# ---------------------------------------------------------------------------

def _stage_audio(full_script: str, audio_dir: Path, date_tag: str, voice_tone: str) -> str:
    """Generate voiceover WAV. Returns path to mixed audio file."""
    _div("STAGE 2 — VOICEOVER AUDIO")
    t0 = time.time()

    voice_path = str(audio_dir / f"horoscope_voice_{date_tag}.wav")
    music_path = str(audio_dir / f"horoscope_music_{date_tag}.wav")
    mixed_path = str(audio_dir / f"horoscope_mixed_{date_tag}.wav")

    # Estimate total duration for music (avg ~150 words/min)
    word_count = len(full_script.split())
    estimated_seconds = max(60, int(word_count / 2.5))  # 2.5 words/sec

    try:
        from modules.production.cinematic_audio_engine import CinematicAudioEngine
        engine = CinematicAudioEngine()

        # Voiceover
        voice_path = engine.generate_voiceover_sync(full_script, voice_path, tone=voice_tone)
        log.info(f"Voice generated: {voice_path}")

        # Background music
        music_path = engine.generate_background_music(
            duration_s=estimated_seconds + 10,
            out_path=music_path,
            energy="calm",
        )
        log.info(f"Music generated: {music_path}")

        # Mix
        mixed_path = engine.mix_audio(voice_path, music_path, mixed_path, estimated_seconds)
        log.info(f"Mixed: {mixed_path}")

    except Exception as e:
        log.warning(f"CinematicAudioEngine failed ({e}) — trying espeak-ng fallback")
        # espeak-ng fallback
        try:
            result = subprocess.run(
                ["espeak-ng", "-v", "en-us+f3", "-s", "155", "-w", voice_path, full_script[:5000]],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr[:300])
            log.info(f"espeak-ng voice: {voice_path}")
            # No music without engine — use voice only
            mixed_path = voice_path
        except Exception as e2:
            log.warning(f"espeak-ng also failed ({e2}) — generating silent audio placeholder")
            # Generate 1-second silent WAV so pipeline can continue
            _generate_silent_wav(mixed_path, duration_s=estimated_seconds)

    log.info(f"Audio stage elapsed: {_elapsed(t0)}")
    return mixed_path


def _generate_silent_wav(path: str, duration_s: int = 60, sample_rate: int = 22050) -> None:
    """Write a silent WAV file using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=mono:sample_rate={sample_rate}",
        "-t", str(duration_s),
        "-acodec", "pcm_s16le",
        path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)


# ---------------------------------------------------------------------------
# Stage 3: Thumbnail generation (PIL only)
# ---------------------------------------------------------------------------

def _stage_thumbnail(readings: list[dict], out_dir: Path, video_type: str,
                     date: datetime.date) -> str:
    """Generate a zodiac wheel thumbnail using PIL. Returns path."""
    _div("STAGE 3 — THUMBNAIL")
    t0 = time.time()
    thumb_path = str(out_dir / f"thumbnail_{date.isoformat()}.png")

    try:
        from PIL import Image, ImageDraw, ImageFont
        _make_thumbnail(readings, thumb_path, video_type, date)
        log.info(f"Thumbnail: {thumb_path} ({_elapsed(t0)})")
    except ImportError:
        log.warning("PIL not available — creating placeholder thumbnail via ffmpeg")
        _ffmpeg_thumbnail(thumb_path, video_type, date)
    except Exception as e:
        log.warning(f"Thumbnail generation failed ({e}) — using ffmpeg fallback")
        _ffmpeg_thumbnail(thumb_path, video_type, date)

    return thumb_path


def _make_thumbnail(readings: list[dict], out_path: str, video_type: str,
                    date: datetime.date) -> None:
    """Build a 1280×720 zodiac wheel thumbnail with PIL."""
    from PIL import Image, ImageDraw, ImageFont

    W, H = 1280, 720
    img = Image.new("RGB", (W, H), (10, 5, 30))  # deep navy
    draw = ImageDraw.Draw(img)

    # Dark purple gradient overlay (manual rows)
    for y in range(H):
        ratio = y / H
        r = int(10 + ratio * 20)
        g = int(5 + ratio * 0)
        b = int(30 + ratio * 50)
        draw.rectangle([(0, y), (W, y + 1)], fill=(r, g, b))

    # Nebula glow effect (semi-transparent circles)
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov_draw = ImageDraw.Draw(overlay)
    glow_centers = [(W // 2, H // 2, 350, (80, 0, 180, 40)),
                    (200, 150, 200, (0, 100, 180, 30)),
                    (1080, 580, 180, (180, 50, 0, 25))]
    for cx, cy, r, color in glow_centers:
        ov_draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=color)
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Zodiac wheel (circle of 12 symbols)
    cx, cy = W // 2, H // 2
    wheel_r = min(W, H) // 2 - 40
    symbols = [r["symbol"] for r in readings]
    sign_names = [r["sign"] for r in readings]
    element_colors = {
        "Fire": (255, 80, 30),
        "Earth": (60, 180, 60),
        "Air": (80, 180, 255),
        "Water": (60, 80, 220),
    }

    # Try to load a Unicode-capable font
    font_symbol = None
    font_sign = None
    font_title = None
    for font_path in [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]:
        if os.path.exists(font_path):
            try:
                from PIL import ImageFont as _IF
                font_symbol = _IF.truetype(font_path, 28)
                font_sign = _IF.truetype(font_path, 14)
                font_title = _IF.truetype(font_path, 42)
                break
            except Exception:
                pass
    if font_symbol is None:
        from PIL import ImageFont as _IF
        font_symbol = _IF.load_default()
        font_sign = font_symbol
        font_title = font_symbol

    # Draw wheel segments
    for i, (sym, name, reading) in enumerate(zip(symbols, sign_names, readings)):
        angle_deg = -90 + i * 30  # start from top
        angle_rad = math.radians(angle_deg)
        sym_r = wheel_r - 30
        sx = int(cx + sym_r * math.cos(angle_rad))
        sy = int(cy + sym_r * math.sin(angle_rad))

        elem_color = element_colors.get(reading.get("element", "Fire"), (255, 255, 255))

        # Draw spoke line
        line_x = int(cx + (wheel_r - 10) * math.cos(angle_rad))
        line_y = int(cy + (wheel_r - 10) * math.sin(angle_rad))
        draw.line([(cx, cy), (line_x, line_y)], fill=(*elem_color, 80), width=1)

        # Draw symbol dot
        dot_x = int(cx + (sym_r - 25) * math.cos(angle_rad))
        dot_y = int(cy + (sym_r - 25) * math.sin(angle_rad))
        draw.ellipse([(dot_x - 22, dot_y - 22), (dot_x + 22, dot_y + 22)],
                     fill=(20, 10, 60), outline=elem_color, width=2)

        # Draw symbol text
        try:
            draw.text((sx - 10, sy - 14), sym, font=font_symbol, fill=elem_color)
        except Exception:
            draw.text((sx - 8, sy - 12), sym, fill=elem_color)

        # Draw sign name (small, outside wheel)
        name_r = wheel_r + 10
        nx = int(cx + name_r * math.cos(angle_rad))
        ny = int(cy + name_r * math.sin(angle_rad))
        try:
            draw.text((nx - 20, ny - 7), name, font=font_sign, fill=(200, 180, 255))
        except Exception:
            draw.text((nx - 15, ny - 6), name, fill=(200, 180, 255))

    # Center circle
    draw.ellipse([(cx - 55, cy - 55), (cx + 55, cy + 55)],
                 fill=(30, 10, 80), outline=(160, 100, 255), width=3)
    draw.text((cx - 28, cy - 20), "✨", font=font_symbol, fill=(255, 220, 80))

    # Title text
    type_labels = {
        "daily": f"DAILY {date.strftime('%b %d').upper()}",
        "weekly": f"WEEKLY {date.strftime('%b %d').upper()}",
        "monthly": f"MONTHLY {date.strftime('%B %Y').upper()}",
        "yearly": f"YEARLY {date.year}",
        "special_event": "SPECIAL EVENT",
    }
    label = type_labels.get(video_type, "HOROSCOPE")
    try:
        draw.text((40, 30), "HOROSCOPE", font=font_title, fill=(255, 220, 80))
        draw.text((40, 80), label, font=font_title, fill=(180, 100, 255))
        draw.text((40, 130), "ALL 12 SIGNS", font=font_sign, fill=(160, 160, 255))
        draw.text((40, H - 50), "GetMindFuelNow", font=font_sign, fill=(120, 120, 200))
    except Exception:
        pass  # font errors are non-fatal

    img.save(out_path, "PNG")


def _ffmpeg_thumbnail(out_path: str, video_type: str, date: datetime.date) -> None:
    """Minimal fallback thumbnail using ffmpeg lavfi."""
    label = f"Horoscope {date.strftime('%B %d %Y')}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c=0x0a0520:s=1280x720:r=1",
        "-frames:v", "1",
        "-vf", f"drawtext=text='{label}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
        out_path,
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=30)
    except Exception as e:
        log.warning(f"ffmpeg thumbnail fallback failed: {e}")


# ---------------------------------------------------------------------------
# Stage 4: Video generation (ffmpeg + PIL, no moviepy)
# ---------------------------------------------------------------------------

def _stage_video(
    readings: list[dict],
    audio_path: str,
    out_dir: Path,
    date_tag: str,
    video_type: str,
    date: datetime.date,
) -> str:
    """
    Generate the full horoscope video using ffmpeg.

    Approach:
      1. Create one background PNG per zodiac sign (dark purple/navy gradient + symbol)
      2. Concatenate them into a video with the audio track
      3. Return path to final MP4

    No moviepy is used anywhere.
    """
    _div("STAGE 4 — VIDEO GENERATION")
    t0 = time.time()

    from modules.horoscope.video_types import (
        get_duration_per_sign,
        get_intro_duration,
        get_outro_duration,
    )

    intro_dur = get_intro_duration(video_type)
    sign_dur = get_duration_per_sign(video_type)
    outro_dur = get_outro_duration(video_type)

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    segment_files: list[str] = []
    concat_list_path = str(out_dir / "concat_list.txt")
    final_silent_path = str(out_dir / f"horoscope_silent_{date_tag}.mp4")

    # ── Build intro frame ────────────────────────────────────────────────────
    intro_frame = str(frames_dir / "intro.png")
    _make_sign_frame(
        frame_path=intro_frame,
        sign_name="HOROSCOPE",
        symbol="✨",
        element="cosmic",
        date=date,
        video_type=video_type,
        is_intro=True,
    )
    intro_video = str(out_dir / "seg_intro.mp4")
    _image_to_video(intro_frame, intro_video, duration=intro_dur)
    segment_files.append(intro_video)

    # ── Build one video segment per sign ────────────────────────────────────
    for i, reading in enumerate(readings):
        sign_name = reading["sign"]
        symbol = reading["symbol"]
        element = reading.get("element", "Fire")

        frame_path = str(frames_dir / f"sign_{i:02d}_{sign_name.lower()}.png")
        _make_sign_frame(
            frame_path=frame_path,
            sign_name=sign_name,
            symbol=symbol,
            element=element,
            date=date,
            video_type=video_type,
            reading=reading,
        )

        seg_path = str(out_dir / f"seg_{i:02d}_{sign_name.lower()}.mp4")
        _image_to_video(frame_path, seg_path, duration=sign_dur)
        segment_files.append(seg_path)
        log.info(f"  Segment {i + 1:02d}/12: {sign_name} ({sign_dur}s)")

    # ── Build outro frame ────────────────────────────────────────────────────
    outro_frame = str(frames_dir / "outro.png")
    _make_sign_frame(
        frame_path=outro_frame,
        sign_name="SUBSCRIBE",
        symbol="🔔",
        element="cosmic",
        date=date,
        video_type=video_type,
        is_outro=True,
    )
    outro_video = str(out_dir / "seg_outro.mp4")
    _image_to_video(outro_frame, outro_video, duration=outro_dur)
    segment_files.append(outro_video)

    # ── Concatenate all segments ─────────────────────────────────────────────
    with open(concat_list_path, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    concat_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_list_path,
        "-c", "copy",
        final_silent_path,
    ]
    result = subprocess.run(concat_cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        err = result.stderr.decode()[-500:]
        raise RuntimeError(f"ffmpeg concat failed:\n{err}")

    log.info(f"Silent video: {final_silent_path} ({_elapsed(t0)})")
    return final_silent_path


def _make_sign_frame(
    frame_path: str,
    sign_name: str,
    symbol: str,
    element: str,
    date: datetime.date,
    video_type: str,
    reading: dict | None = None,
    is_intro: bool = False,
    is_outro: bool = False,
) -> None:
    """
    Generate a 1920×1080 PNG frame for a zodiac sign segment.
    Uses PIL when available, falls back to a solid-color ffmpeg frame.
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        _pil_sign_frame(frame_path, sign_name, symbol, element, date,
                        video_type, reading, is_intro, is_outro)
    except ImportError:
        _ffmpeg_sign_frame(frame_path, sign_name, symbol)
    except Exception as e:
        log.debug(f"PIL frame error for {sign_name}: {e}")
        _ffmpeg_sign_frame(frame_path, sign_name, symbol)


def _pil_sign_frame(
    frame_path: str,
    sign_name: str,
    symbol: str,
    element: str,
    date: datetime.date,
    video_type: str,
    reading: dict | None,
    is_intro: bool,
    is_outro: bool,
) -> None:
    """Create a stylized 1920×1080 frame using PIL."""
    from PIL import Image, ImageDraw

    W, H = 1920, 1080

    # Background gradient colors by element
    BG_COLORS = {
        "Fire":   ((20, 5, 30), (50, 10, 10)),
        "Earth":  ((5, 20, 10), (10, 40, 20)),
        "Air":    ((5, 10, 30), (10, 20, 60)),
        "Water":  ((5, 10, 40), (10, 10, 80)),
        "cosmic": ((5, 5, 40), (20, 10, 60)),
    }
    top_c, bot_c = BG_COLORS.get(element, BG_COLORS["cosmic"])

    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # Draw vertical gradient
    for y in range(H):
        ratio = y / H
        r = int(top_c[0] + ratio * (bot_c[0] - top_c[0]))
        g = int(top_c[1] + ratio * (bot_c[1] - top_c[1]))
        b = int(top_c[2] + ratio * (bot_c[2] - top_c[2]))
        draw.rectangle([(0, y), (W, y + 1)], fill=(r, g, b))

    # Glow circles
    ELEM_GLOW = {
        "Fire":   (200, 40, 20),
        "Earth":  (40, 160, 40),
        "Air":    (40, 120, 220),
        "Water":  (20, 40, 220),
        "cosmic": (120, 40, 200),
    }
    glow = ELEM_GLOW.get(element, (120, 40, 200))

    for radius, alpha in [(400, 20), (250, 35), (150, 50)]:
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ov_draw = ImageDraw.Draw(overlay)
        cx, cy = W // 2, H // 2
        ov_draw.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)],
                         fill=(*glow, alpha))
        img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

    # Star field (pseudo-random but consistent)
    import random as _rnd
    star_rng = _rnd.Random(hash(sign_name + str(date)))
    for _ in range(120):
        sx = star_rng.randint(0, W)
        sy = star_rng.randint(0, H)
        sz = star_rng.randint(1, 3)
        brightness = star_rng.randint(100, 255)
        draw.ellipse([(sx, sy), (sx + sz, sy + sz)], fill=(brightness, brightness, brightness))

    # Load font
    font_large = font_medium = font_small = None
    for fp in [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]:
        if os.path.exists(fp):
            try:
                from PIL import ImageFont as _IF
                font_large = _IF.truetype(fp, 120)
                font_medium = _IF.truetype(fp, 56)
                font_small = _IF.truetype(fp, 32)
                break
            except Exception:
                pass
    if font_large is None:
        from PIL import ImageFont as _IF
        font_large = font_medium = font_small = _IF.load_default()

    elem_accent = tuple(c + 80 for c in glow)  # lighter version

    if is_intro:
        # Intro frame
        lines = [
            ("GetMindFuelNow", font_small, (160, 130, 255), H // 2 - 220),
            ("✨ HOROSCOPE ✨", font_large, (255, 220, 80), H // 2 - 100),
            (f"All 12 Signs", font_medium, (200, 160, 255), H // 2 + 60),
            (date.strftime("%B %d, %Y"), font_medium, (160, 160, 255), H // 2 + 140),
        ]
    elif is_outro:
        lines = [
            ("✨ GetMindFuelNow ✨", font_medium, (255, 220, 80), H // 2 - 150),
            ("Thank You for Watching", font_medium, (200, 180, 255), H // 2 - 50),
            ("SUBSCRIBE for Daily Horoscopes", font_medium, (255, 200, 80), H // 2 + 60),
            ("🔔 Hit the Bell!", font_small, (180, 140, 255), H // 2 + 140),
        ]
    else:
        # Sign frame
        lines = [
            (symbol, font_large, elem_accent, H // 2 - 200),
            (sign_name.upper(), font_large, (255, 255, 255), H // 2 - 50),
        ]
        if reading:
            planet_text = f"Ruling Planet: {reading.get('ruling_planet', '')}"
            element_text = f"{reading.get('element', '')} Sign · {reading.get('modality', '')} Energy"
            lines.append((planet_text, font_small, elem_accent, H // 2 + 120))
            lines.append((element_text, font_small, (160, 160, 220), H // 2 + 170))
        lines.append(("GetMindFuelNow", font_small, (100, 100, 180), H - 60))

    # Render text centered
    for text, font, color, y_pos in lines:
        try:
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            x_pos = (W - tw) // 2
            draw.text((x_pos, y_pos), text, font=font, fill=color)
        except Exception:
            try:
                draw.text((W // 2 - 100, y_pos), text, fill=color)
            except Exception:
                pass

    # Bottom accent line
    accent = glow
    draw.rectangle([(0, H - 8), (W, H)], fill=(*accent, 255))
    draw.rectangle([(0, 0), (W, 8)], fill=(*accent, 255))

    img.save(frame_path, "PNG")


def _ffmpeg_sign_frame(frame_path: str, sign_name: str, symbol: str) -> None:
    """Minimal ffmpeg-only fallback frame."""
    color = "0x0a0520"
    label = f"{symbol} {sign_name}"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi",
        "-i", f"color=c={color}:s=1920x1080:r=1",
        "-frames:v", "1",
        "-vf", (
            f"drawtext=text='{label}':fontcolor=white:fontsize=96:"
            f"x=(w-text_w)/2:y=(h-text_h)/2,"
            f"drawtext=text='GetMindFuelNow':fontcolor=0xaaaaff:fontsize=40:"
            f"x=(w-text_w)/2:y=h-80"
        ),
        frame_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)


def _image_to_video(image_path: str, out_path: str, duration: int) -> None:
    """Convert a still image to an MP4 video of specified duration using ffmpeg."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-c:v", "libx264",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-vf", "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-r", "24",
        "-preset", "ultrafast",
        "-crf", "28",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=120)
    if result.returncode != 0:
        err = result.stderr.decode()[-300:]
        raise RuntimeError(f"image_to_video failed for {image_path}:\n{err}")


# ---------------------------------------------------------------------------
# Stage 5: Mux audio + video
# ---------------------------------------------------------------------------

def _stage_mux(silent_path: str, audio_path: str, final_path: str) -> str:
    """Mux video and audio using ffmpeg. Returns final video path."""
    _div("STAGE 5 — MUX")
    t0 = time.time()

    cmd = [
        "ffmpeg", "-y",
        "-i", silent_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        final_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode != 0:
        err = result.stderr.decode()[-500:]
        log.warning(f"Mux with audio failed: {err}")
        # Fallback: copy silent video (no audio track)
        shutil.copy2(silent_path, final_path)

    size_mb = os.path.getsize(final_path) / 1_048_576 if os.path.exists(final_path) else 0
    log.info(f"Final video: {final_path} ({size_mb:.1f} MB, {_elapsed(t0)})")
    return final_path


# ---------------------------------------------------------------------------
# Stage 6: YouTube upload
# ---------------------------------------------------------------------------

def _stage_upload(
    final_path: str,
    thumbnail_path: str,
    title: str,
    description: str,
    tags: list[str],
    video_type: str,
    upload: bool,
) -> dict:
    """Upload video to YouTube. Returns upload stage dict."""
    _div("STAGE 6 — YOUTUBE UPLOAD")
    t0 = time.time()

    metadata = {
        "title": title[:100],  # YouTube title limit
        "description": description,
        "tags": tags[:500],    # YouTube tags limit (500 chars)
        "category_id": "22",
        "privacy_status": "public",
        "made_for_kids": False,
        "thumbnail": thumbnail_path,
    }

    if not upload:
        log.info("Upload skipped (--no-upload)")
        return {"ok": True, "skipped": True, "metadata": metadata}

    try:
        from youtube_uploader import upload_video
        video_url = upload_video(final_path, metadata)
        log.info(f"Uploaded: {video_url} ({_elapsed(t0)})")
        return {"ok": True, "url": video_url, "elapsed": _elapsed(t0)}
    except Exception as e:
        log.error(f"Upload failed: {e}\n{traceback.format_exc()}")
        _add_to_retry_queue(final_path, metadata)
        return {"ok": False, "error": str(e), "queued_for_retry": True}


# ---------------------------------------------------------------------------
# Main pipeline function
# ---------------------------------------------------------------------------

def run_horoscope_pipeline(
    video_type: str = "auto",
    date: datetime.date | None = None,
    upload: bool = True,
    event_name: str = "",
    event_type: str = "",
) -> dict:
    """
    Execute the full horoscope video production pipeline.

    Args:
        video_type: 'auto' (decide based on today), 'daily', 'weekly',
                    'monthly', 'yearly', 'special_event'
        date:       Override the production date (defaults to today)
        upload:     If False, generate video but do not upload
        event_name: For special_event type (e.g. "Full Moon in Scorpio")
        event_type: For special_event type (e.g. "full_moon")

    Returns:
        Production report dict.
    """
    pipeline_start = time.time()
    if date is None:
        date = datetime.date.today()
    date_tag = date.isoformat()

    # Resolve 'auto' → actual type(s)
    if video_type == "auto":
        from modules.horoscope.video_types import get_todays_video_types, get_event_for_date
        types_today = get_todays_video_types(date)
        # Run highest-priority type first; daily always runs last
        primary_type = types_today[0]
        log.info(f"Auto-detected video types for {date_tag}: {types_today}")

        # For special_event, populate event details from the calendar
        if primary_type == "special_event" and not event_name:
            ev = get_event_for_date(date)
            if ev:
                event_name = ev["name"]
                event_type = ev["type"]
    else:
        primary_type = video_type

    from modules.horoscope.video_types import VIDEO_TYPES
    vt_config = VIDEO_TYPES.get(primary_type, VIDEO_TYPES["daily"])
    voice_tone = vt_config.get("voice_tone", "warm")

    # ── Output directories ────────────────────────────────────────────────────
    out_base = HERE / "output" / f"horoscope_{date_tag}_{primary_type}"
    out_base.mkdir(parents=True, exist_ok=True)
    audio_dir = out_base / "audio"
    thumb_dir = out_base / "thumbnails"
    audio_dir.mkdir(exist_ok=True)
    thumb_dir.mkdir(exist_ok=True)

    report: dict[str, Any] = {
        "date": date_tag,
        "video_type": primary_type,
        "event_name": event_name,
        "stages": {},
        "output_files": {},
        "errors": [],
        "success": False,
    }

    # ── Stage 1: Content ──────────────────────────────────────────────────────
    try:
        content = _stage_content(primary_type, date, event_name, event_type)
        readings = content["readings"]
        title = content["title"]
        description = content["description"]
        tags = content["tags"]
        full_script = content["full_script"]
        report["stages"]["content"] = {"ok": True, "title": title}
        report["output_files"]["script"] = str(out_base / "voiceover_script.txt")
        (out_base / "voiceover_script.txt").write_text(full_script)
    except Exception as e:
        log.error(f"Content stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"content: {e}")
        report["stages"]["content"] = {"ok": False, "error": str(e)}
        report["success"] = False
        return report

    # ── Stage 2: Audio ────────────────────────────────────────────────────────
    try:
        mixed_audio = _stage_audio(full_script, audio_dir, date_tag, voice_tone)
        report["stages"]["audio"] = {"ok": True, "path": mixed_audio}
        report["output_files"]["audio"] = mixed_audio
    except Exception as e:
        log.error(f"Audio stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"audio: {e}")
        report["stages"]["audio"] = {"ok": False, "error": str(e)}
        mixed_audio = ""

    # ── Stage 3: Thumbnail ────────────────────────────────────────────────────
    try:
        thumb_path = _stage_thumbnail(readings, thumb_dir, primary_type, date)
        report["stages"]["thumbnail"] = {"ok": True, "path": thumb_path}
        report["output_files"]["thumbnail"] = thumb_path
    except Exception as e:
        log.warning(f"Thumbnail stage failed: {e}")
        report["errors"].append(f"thumbnail: {e}")
        report["stages"]["thumbnail"] = {"ok": False, "error": str(e)}
        thumb_path = ""

    # ── Stage 4: Video ────────────────────────────────────────────────────────
    try:
        silent_path = _stage_video(readings, mixed_audio, out_base, date_tag, primary_type, date)
        report["stages"]["video"] = {"ok": True, "path": silent_path}
        report["output_files"]["silent_video"] = silent_path
    except Exception as e:
        log.error(f"Video stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"video: {e}")
        report["stages"]["video"] = {"ok": False, "error": str(e)}
        silent_path = ""

    # ── Stage 5: Mux ─────────────────────────────────────────────────────────
    final_path = str(out_base / f"horoscope_{primary_type}_{date_tag}.mp4")
    try:
        if silent_path and mixed_audio and os.path.exists(silent_path) and os.path.exists(mixed_audio):
            final_path = _stage_mux(silent_path, mixed_audio, final_path)
        elif silent_path and os.path.exists(silent_path):
            log.warning("No audio — using silent video as final")
            shutil.copy2(silent_path, final_path)
        else:
            raise RuntimeError("No video to mux")
        report["stages"]["mux"] = {"ok": True, "path": final_path}
        report["output_files"]["final_video"] = final_path
    except Exception as e:
        log.error(f"Mux stage failed: {e}")
        report["errors"].append(f"mux: {e}")
        report["stages"]["mux"] = {"ok": False, "error": str(e)}

    # ── Stage 6: Upload ───────────────────────────────────────────────────────
    upload_result = _stage_upload(final_path, thumb_path, title, description, tags, primary_type, upload)
    report["stages"]["upload"] = upload_result
    if upload_result.get("url"):
        report["output_files"]["youtube_url"] = upload_result["url"]

    # ── Final report ──────────────────────────────────────────────────────────
    report["success"] = len(report["errors"]) == 0
    report["total_elapsed"] = _elapsed(pipeline_start)

    report_path = out_base / "production_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _div("COMPLETE")
    status = "SUCCESS" if report["success"] else f"COMPLETED WITH {len(report['errors'])} ERRORS"
    log.info(f"Pipeline: {status} in {report['total_elapsed']}")
    for stage, data in report["stages"].items():
        ok = "OK" if data.get("ok") else "FAIL"
        log.info(f"  [{ok}] {stage}")
    if report["errors"]:
        for e in report["errors"]:
            log.warning(f"  ERROR: {e}")
    if report["output_files"].get("youtube_url"):
        log.info(f"  YouTube: {report['output_files']['youtube_url']}")

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GetMindFuelNow Horoscope Pipeline")
    parser.add_argument("--now",        action="store_true",   help="Run immediately")
    parser.add_argument("--schedule",   action="store_true",   help="Run on schedule (daemon mode)")
    parser.add_argument("--no-upload",  action="store_true",   help="Generate video only, do not upload")
    parser.add_argument("--type",       type=str, default="auto",
                        choices=["auto", "daily", "weekly", "monthly", "yearly", "special_event"],
                        help="Video type to produce")
    parser.add_argument("--date",       type=str, default="",  help="Override date (YYYY-MM-DD)")
    parser.add_argument("--event-name", type=str, default="",  help="Event name for special_event type")
    parser.add_argument("--event-type", type=str, default="",  help="Event type (full_moon, retrograde, eclipse, ...)")
    args = parser.parse_args()

    target_date = None
    if args.date:
        target_date = datetime.date.fromisoformat(args.date)

    do_upload = not args.no_upload

    if args.now:
        run_horoscope_pipeline(
            video_type=args.type,
            date=target_date,
            upload=do_upload,
            event_name=args.event_name,
            event_type=args.event_type,
        )
    elif args.schedule:
        # Hand off to daemon
        daemon_path = Path(__file__).parent / "horoscope_daemon.py"
        os.execv(sys.executable, [sys.executable, str(daemon_path), "--schedule"])
    else:
        parser.print_help()
