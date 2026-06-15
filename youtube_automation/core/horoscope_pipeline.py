"""
core/horoscope_pipeline.py
GetMindFuelNow — Horoscope Video Production Pipeline

Stages:
  1. CONTENT    — generate all 12 sign readings via content_engine
  2. AUDIO      — edge-tts female voice + soft ambient background music
  3. THUMBNAIL  — zodiac wheel via PIL
  4. VIDEO      — beautiful animated frames with reading text (ffmpeg)
  5. MUX        — combine audio + video
  6. UPLOAD     — YouTube with chapters

Usage:
  python core/horoscope_pipeline.py --now
  python core/horoscope_pipeline.py --now --type weekly
  python core/horoscope_pipeline.py --now --no-upload
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import logging
import math
import os
import shutil
import subprocess
import sys
import textwrap
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

RETRY_QUEUE_PATH = HERE / "logs" / "upload_retry_queue.json"


def _div(label: str = "") -> None:
    w = 60
    if label:
        pad = max(0, (w - len(label) - 2) // 2)
        log.info("═" * pad + f" {label} " + "═" * pad)
    else:
        log.info("═" * w)


def _elapsed(t0: float) -> str:
    s = time.time() - t0
    return f"{s:.1f}s" if s < 60 else f"{s / 60:.1f}min"


# ---------------------------------------------------------------------------
# Retry queue
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
    queue = _load_retry_queue()
    if not queue:
        return 0
    remaining = []
    processed = 0
    for item in queue:
        video_path = item.get("video_path", "")
        if not os.path.exists(video_path):
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
    _save_retry_queue(remaining)
    return processed


# ---------------------------------------------------------------------------
# Stage 1: Content
# ---------------------------------------------------------------------------

def _stage_content(video_type: str, date: datetime.date,
                   event_name: str, event_type: str) -> dict:
    _div("STAGE 1 — HOROSCOPE CONTENT")
    t0 = time.time()

    from modules.horoscope.content_engine import (
        generate_all_signs, get_video_title, get_video_description,
        get_tags, build_full_voiceover_script,
    )

    readings = generate_all_signs(video_type, date, event_name, event_type)
    title = get_video_title(video_type, date, event_name)
    description = get_video_description(video_type, date, readings, event_name)
    tags = get_tags(video_type)
    full_script = build_full_voiceover_script(readings, video_type, date, event_name)

    log.info(f"Generated {len(readings)} sign readings for type={video_type!r}")
    log.info(f"Title: {title}")
    log.info(f"Script: {len(full_script.split())} words ({_elapsed(t0)})")

    return {
        "readings": readings,
        "title": title,
        "description": description,
        "tags": tags,
        "full_script": full_script,
        "ok": True,
    }


# ---------------------------------------------------------------------------
# Stage 2: Audio — edge-tts female voice + ambient background music
# ---------------------------------------------------------------------------

async def _async_edge_tts(text: str, out_path: str, voice: str, rate: str) -> None:
    """Generate speech using edge-tts (Microsoft Neural TTS, free)."""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(out_path)


def _edge_tts_voiceover(text: str, out_path: str) -> bool:
    """
    Generate voiceover with edge-tts using a calm female voice at slow pace.
    Returns True on success.

    Voice: en-US-AriaNeural — clear, warm, professional female voice
    Rate: -15% — noticeably slower than normal (clear for all listeners)
    """
    try:
        voice = "en-US-AriaNeural"
        rate = "-15%"
        log.info(f"edge-tts: voice={voice}, rate={rate}, chars={len(text)}")
        asyncio.run(_async_edge_tts(text, out_path, voice, rate))
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            log.info(f"edge-tts voice: {out_path} ({os.path.getsize(out_path)//1024}KB)")
            return True
        log.warning("edge-tts produced empty output")
        return False
    except ImportError:
        log.warning("edge-tts not installed — run: pip install edge-tts")
        return False
    except Exception as e:
        log.warning(f"edge-tts failed: {e}")
        return False


def _espeak_voiceover(text: str, out_path: str) -> bool:
    """Fallback: espeak-ng with best female-sounding voice settings."""
    try:
        result = subprocess.run(
            [
                "espeak-ng",
                "-v", "en-us+f3",   # female voice variant 3
                "-s", "140",         # words per minute (140 = slow, clear)
                "-p", "55",          # pitch 55/100 (lower = more pleasant)
                "-a", "180",         # amplitude (volume)
                "-w", out_path,
                text[:8000],
            ],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0 and os.path.exists(out_path):
            log.info(f"espeak-ng voice: {out_path}")
            return True
        log.warning(f"espeak-ng failed: {result.stderr[:200]}")
        return False
    except Exception as e:
        log.warning(f"espeak-ng exception: {e}")
        return False


def _generate_ambient_music(duration_s: int, out_path: str) -> bool:
    """
    Generate soft ambient background music using ffmpeg sine wave synthesis.

    Uses a three-note chord in solfeggio frequencies (432, 540, 648 Hz)
    with long reverb tails to create a gentle mystical atmosphere.
    Volume is kept low so it sits under the voice without competing.
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            # Three sine waves — a gentle A-major-ish chord
            "-f", "lavfi", "-i", f"sine=f=432:d={duration_s + 5}",
            "-f", "lavfi", "-i", f"sine=f=540:d={duration_s + 5}",
            "-f", "lavfi", "-i", f"sine=f=648:d={duration_s + 5}",
            "-filter_complex",
            (
                # Volume-balance each tone
                "[0]volume=0.12,aecho=0.8:0.9:800:0.5[a0];"
                "[1]volume=0.07,aecho=0.7:0.85:1200:0.4[a1];"
                "[2]volume=0.05,aecho=0.6:0.8:1600:0.3[a2];"
                # Mix, lowpass to make it warm and soft
                "[a0][a1][a2]amix=inputs=3:duration=first,"
                "lowpass=f=700,highpass=f=80,"
                # Fade in 2s, fade out 3s
                f"afade=t=in:d=2,afade=t=out:st={duration_s - 3}:d=3[out]"
            ),
            "-map", "[out]",
            "-ar", "44100", "-ac", "2",
            "-t", str(duration_s),
            "-acodec", "pcm_s16le",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=120)
        if result.returncode == 0 and os.path.exists(out_path):
            log.info(f"Ambient music generated: {out_path}")
            return True
        log.warning(f"Music gen failed: {result.stderr.decode()[-300:]}")
        return False
    except Exception as e:
        log.warning(f"Music gen exception: {e}")
        return False


def _mix_voice_and_music(voice_path: str, music_path: str,
                          out_path: str, voice_duration_s: int) -> str:
    """
    Mix voice (loud) + music (quiet background). Returns out_path.
    Music is ducked to -18 dB relative to voice.
    """
    try:
        cmd = [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-i", music_path,
            "-filter_complex",
            (
                # Voice at full volume, music at -18 dB
                "[0]volume=1.0[v];"
                "[1]volume=0.12[m];"
                f"[v][m]amix=inputs=2:duration=first,"
                f"alimiter=limit=0.95:level=true,"
                f"afade=t=in:d=0.5,"
                f"afade=t=out:st={max(0, voice_duration_s - 2)}:d=2[out]"
            ),
            "-map", "[out]",
            "-ar", "44100", "-ac", "2",
            "-acodec", "pcm_s16le",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode == 0 and os.path.exists(out_path):
            return out_path
        log.warning("Mix failed — using voice only")
        return voice_path
    except Exception as e:
        log.warning(f"Mix exception: {e}")
        return voice_path


def _get_audio_duration(path: str) -> int:
    """Get duration of audio file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return max(60, int(float(result.stdout.strip())))
    except Exception:
        return 60


def _stage_audio(full_script: str, audio_dir: Path, date_tag: str,
                 word_count: int) -> tuple[str, int]:
    """
    Generate voice + background music. Returns (mixed_audio_path, duration_seconds).

    Priority:
      1. edge-tts (Microsoft Neural TTS — best quality, free)
      2. espeak-ng (robot fallback)
      3. Silent audio (last resort so video still generates)
    """
    _div("STAGE 2 — AUDIO")
    t0 = time.time()

    voice_mp3 = str(audio_dir / f"voice_{date_tag}.mp3")
    voice_wav = str(audio_dir / f"voice_{date_tag}.wav")
    music_wav = str(audio_dir / f"music_{date_tag}.wav")
    mixed_wav = str(audio_dir / f"mixed_{date_tag}.wav")

    # ── Generate voice ────────────────────────────────────────────────────────
    voice_ok = _edge_tts_voiceover(full_script, voice_mp3)

    if voice_ok:
        # Convert mp3 → wav for ffmpeg pipeline compatibility
        conv = subprocess.run(
            ["ffmpeg", "-y", "-i", voice_mp3, "-ar", "44100", "-ac", "2",
             "-acodec", "pcm_s16le", voice_wav],
            capture_output=True, timeout=60,
        )
        if conv.returncode != 0 or not os.path.exists(voice_wav):
            # Keep mp3 if conversion failed
            voice_wav = voice_mp3

    if not voice_ok or not os.path.exists(voice_wav):
        log.warning("edge-tts failed — trying espeak-ng fallback")
        voice_ok = _espeak_voiceover(full_script, voice_wav)

    if not voice_ok or not os.path.exists(voice_wav):
        log.warning("All TTS failed — using silence")
        estimated_s = max(60, word_count * 60 // 140)  # 140 wpm
        _generate_silent_wav(voice_wav, duration_s=estimated_s)

    # Get actual voice duration
    voice_duration = _get_audio_duration(voice_wav)
    log.info(f"Voice duration: {voice_duration}s")

    # ── Generate ambient music ────────────────────────────────────────────────
    music_ok = _generate_ambient_music(voice_duration + 5, music_wav)

    # ── Mix voice + music ─────────────────────────────────────────────────────
    if music_ok and os.path.exists(music_wav):
        final_audio = _mix_voice_and_music(voice_wav, music_wav, mixed_wav, voice_duration)
    else:
        log.info("Skipping music mix — voice only")
        final_audio = voice_wav

    log.info(f"Audio stage: {_elapsed(t0)}, output: {final_audio}")
    return final_audio, voice_duration


def _generate_silent_wav(path: str, duration_s: int = 60, sample_rate: int = 44100) -> None:
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"anullsrc=channel_layout=stereo:sample_rate={sample_rate}",
        "-t", str(duration_s), "-acodec", "pcm_s16le", path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)


# ---------------------------------------------------------------------------
# Stage 3: Thumbnail
# ---------------------------------------------------------------------------

def _stage_thumbnail(readings: list[dict], out_dir: Path, video_type: str,
                     date: datetime.date, event_name: str = "",
                     event_type: str = "") -> str:
    _div("STAGE 3 — THUMBNAIL")
    t0 = time.time()
    thumb_path = str(out_dir / f"thumbnail_{date.isoformat()}.png")

    try:
        from PIL import Image, ImageDraw, ImageFont
        _make_thumbnail(readings, thumb_path, video_type, date, event_name, event_type)
        log.info(f"Thumbnail: {thumb_path} ({_elapsed(t0)})")
    except Exception as e:
        log.warning(f"PIL thumbnail failed ({e}) — using ffmpeg fallback")
        _ffmpeg_thumbnail(thumb_path, video_type, date)

    return thumb_path


def _load_font(size: int, bold: bool = False) -> Any:
    """Load the best available font. Always returns a font object."""
    bold_fonts = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    regular_fonts = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    from PIL import ImageFont
    for fp in (bold_fonts if bold else regular_fonts):
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_text_centered(draw, text: str, y: int, font, color: tuple, W: int) -> None:
    """Draw text centered horizontally at y position."""
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        x = max(0, (W - tw) // 2)
        draw.text((x, y), text, font=font, fill=color)
    except Exception:
        draw.text((W // 2 - 100, y), text, fill=color)


# ---------------------------------------------------------------------------
# AI thumbnail background prompts — rotated by date for visual variety
# ---------------------------------------------------------------------------

_AI_BG_PROMPTS = [
    "stunning purple cosmic nebula stars galaxy glowing mystical spiritual, cinematic 4k, no text no words",
    "golden celestial light rays cosmos stars divine ethereal background, epic cinematic, no text no words",
    "deep blue cosmos moon phases planets night sky magical, cinematic 4k, no text no words",
    "violet mystical energy galaxy stars ethereal spiritual cosmic, 4k photorealistic, no text no words",
    "cosmic dawn purple gold stars rising divine light ethereal, cinematic, no text no words",
    "deep space nebula galaxy glowing stars ethereal blue purple, 4k cinematic, no text no words",
    "emerald cosmic energy stars planets divine light mystical, 4k photorealistic, no text no words",
    "silver moonlight cosmic stars night sky serene peaceful, cinematic, no text no words",
    "royal purple cosmic stars divine energy spiritual nebula, 4k, no text no words",
    "rose gold cosmic stars galaxy mystical ethereal spiritual, cinematic 4k, no text no words",
    "teal cosmic energy universe stars glowing mystical divine, 4k photorealistic, no text no words",
    "indigo starfield cosmic rays spiritual energy universe, cinematic, no text no words",
    "amber cosmic light stars galaxy divine ethereal warm, 4k, no text no words",
    "crimson cosmic energy stars night mystical dark universe, cinematic, no text no words",
]

_EVENT_BG_PROMPTS = {
    "eclipse":    "dramatic solar eclipse golden corona cosmic sky, cinematic 4k, no text no words",
    "full_moon":  "full moon glowing silver night sky stars mystical purple, cinematic, no text no words",
    "new_moon":   "dark new moon cosmic sky stars glowing mystical, cinematic 4k, no text no words",
    "retrograde": "swirling cosmic energy purple stars planetary retrograde, 4k, no text no words",
    "solstice":   "summer solstice golden light rays cosmic stars spiritual, cinematic, no text no words",
    "equinox":    "equinox balanced cosmic light stars purple golden ethereal, cinematic, no text no words",
}


def _download_ai_background(prompt: str, out_path: str, seed: int) -> bool:
    """Download AI-generated background from Pollinations.ai (free, no API key needed)."""
    import urllib.request
    import urllib.parse
    try:
        encoded = urllib.parse.quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1280&height=720&nologo=true&seed={seed}&model=flux"
        )
        log.info(f"Generating AI thumbnail (seed={seed}, prompt[:60]={prompt[:60]}...)")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            with open(out_path, "wb") as f:
                f.write(resp.read())
        ok = os.path.exists(out_path) and os.path.getsize(out_path) > 5_000
        if ok:
            log.info(f"AI background: {os.path.getsize(out_path)//1024}KB")
        return ok
    except Exception as e:
        log.warning(f"AI background download failed: {e}")
        return False


def _make_thumbnail(readings: list[dict], out_path: str, video_type: str,
                    date: datetime.date, event_name: str = "",
                    event_type: str = "") -> None:
    """
    Generate a stunning AI-powered 1280×720 thumbnail.

    Background: Pollinations.ai (FLUX model, free, no key) — different image every day.
    Overlay: professional text layout with dark gradient bands for readability.
    Falls back to high-quality PIL background if AI download fails.
    """
    from PIL import Image, ImageDraw

    W, H = 1280, 720
    ai_bg_path = out_path.replace(".png", "_ai_bg.jpg")

    # Pick prompt: event-specific or date-rotating theme
    if video_type == "special_event" and event_type in _EVENT_BG_PROMPTS:
        base_prompt = _EVENT_BG_PROMPTS[event_type]
    else:
        base_prompt = _AI_BG_PROMPTS[date.toordinal() % len(_AI_BG_PROMPTS)]

    seed = (date.toordinal() * 7 + abs(hash(video_type))) % 99999

    # Attempt AI background download
    ai_ok = _download_ai_background(base_prompt, ai_bg_path, seed)

    if ai_ok:
        try:
            img = Image.open(ai_bg_path).convert("RGB").resize((W, H), Image.LANCZOS)
        except Exception:
            ai_ok = False

    if not ai_ok:
        img = _make_cosmic_background(W, H, "cosmic", seed=seed % 10000)

    # ── Dark gradient bands top + bottom for text contrast ──────────────────
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov = ImageDraw.Draw(overlay)
    for y in range(155):                          # top fade
        alpha = int(230 * (1 - y / 155))
        ov.rectangle([(0, y), (W, y + 1)], fill=(4, 2, 18, alpha))
    for y in range(H - 195, H):                  # bottom fade
        alpha = int(230 * ((y - (H - 195)) / 195))
        ov.rectangle([(0, y), (W, y + 1)], fill=(4, 2, 18, alpha))
    for y in range(150, H - 190):                # center vignette (subtle)
        alpha = 55
        ov.rectangle([(0, y), (W, y + 1)], fill=(4, 2, 18, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # ── Top accent stripe ────────────────────────────────────────────────────
    draw.rectangle([(0, 0), (W, 5)], fill=(140, 60, 255))

    # ── Type + date (top band) ───────────────────────────────────────────────
    type_labels = {
        "daily":         "DAILY HOROSCOPE",
        "weekly":        "WEEKLY HOROSCOPE",
        "monthly":       "MONTHLY HOROSCOPE",
        "yearly":        "YEARLY HOROSCOPE",
        "special_event": (event_name.upper() if event_name else "SPECIAL HOROSCOPE"),
    }
    date_labels = {
        "daily":         date.strftime("%B %d, %Y"),
        "weekly":        f"Week of {date.strftime('%B %d')}",
        "monthly":       date.strftime("%B %Y"),
        "yearly":        str(date.year),
        "special_event": date.strftime("%B %d, %Y"),
    }

    f_type = _load_font(74, bold=True)
    f_date = _load_font(38, bold=True)
    f_med  = _load_font(30)
    f_sm   = _load_font(22)
    f_star = _load_font(90)

    _draw_text_centered(draw, type_labels.get(video_type, "HOROSCOPE"), 12, f_type, (255, 215, 55), W)
    _draw_text_centered(draw, date_labels.get(video_type, ""), 97, f_date, (215, 175, 255), W)

    # ── Central star/moon (visible through center vignette) ─────────────────
    _draw_text_centered(draw, "✨", H // 2 - 50, f_star, (255, 225, 60), W)

    # ── Bottom banner with "ALL 12 ZODIAC SIGNS" ────────────────────────────
    banner_y = H - 190
    box_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bov = ImageDraw.Draw(box_overlay)
    bov.rounded_rectangle(
        [(50, banner_y), (W - 50, banner_y + 125)],
        radius=14,
        fill=(35, 5, 80, 210),
        outline=(160, 80, 255, 230),
        width=3,
    )
    img = Image.alpha_composite(img.convert("RGBA"), box_overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    _draw_text_centered(draw, "ALL 12 ZODIAC SIGNS", banner_y + 8,  f_date, (255, 215, 55), W)
    _draw_text_centered(draw, "♈ ♉ ♊ ♋ ♌ ♍ ♎ ♏ ♐ ♑ ♒ ♓",
                        banner_y + 52, _load_font(26),           (190, 155, 255), W)
    _draw_text_centered(draw, "GetMindFuelNow  •  Subscribe for Daily Readings",
                        banner_y + 90, f_sm,                     (160, 130, 220), W)

    # ── Bottom accent stripe ─────────────────────────────────────────────────
    draw.rectangle([(0, H - 5), (W, H)], fill=(140, 60, 255))

    img.save(out_path, "PNG")


def _ffmpeg_thumbnail(out_path: str, video_type: str, date: datetime.date) -> None:
    label = f"Horoscope {date.strftime('%B %d %Y')}"
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "color=c=0x0a0520:s=1280x720:r=1",
        "-frames:v", "1",
        "-vf", f"drawtext=text='{label}':fontcolor=white:fontsize=48:x=(w-text_w)/2:y=(h-text_h)/2",
        out_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)


# ---------------------------------------------------------------------------
# Stage 4: Video — animated frames with reading text on screen
# ---------------------------------------------------------------------------

def _stage_video(
    readings: list[dict],
    audio_path: str,
    out_dir: Path,
    date_tag: str,
    video_type: str,
    date: datetime.date,
    audio_duration: int,
) -> str:
    """
    Generate the full video using ffmpeg.

    Each sign gets a beautifully rendered 1920×1080 frame showing:
      - Zodiac symbol (large)
      - Sign name
      - Date range + element
      - The actual reading text (wrapped, readable)
      - Lucky numbers / color / day

    Frames are animated with fade-in/fade-out transitions.
    """
    _div("STAGE 4 — VIDEO GENERATION")
    t0 = time.time()

    from modules.horoscope.video_types import (
        get_duration_per_sign, get_intro_duration, get_outro_duration,
    )

    intro_dur = get_intro_duration(video_type)
    outro_dur = get_outro_duration(video_type)
    # Calculate sign duration from actual audio so video and audio stay in sync.
    # Fixed sign_dur ignores how long TTS actually runs, causing audio/video mismatch.
    if audio_duration > 0 and len(readings) > 0:
        available = audio_duration - intro_dur - outro_dur
        sign_dur = max(20, available // len(readings))
    else:
        sign_dur = get_duration_per_sign(video_type)

    frames_dir = out_dir / "frames"
    frames_dir.mkdir(exist_ok=True)

    segment_files: list[str] = []
    concat_list_path = str(out_dir / "concat_list.txt")
    final_silent_path = str(out_dir / f"horoscope_silent_{date_tag}.mp4")

    # ── Intro ─────────────────────────────────────────────────────────────────
    intro_frame = str(frames_dir / "intro.png")
    _render_intro_frame(intro_frame, video_type, date)
    intro_seg = str(out_dir / "seg_intro.mp4")
    _frame_to_animated_video(intro_frame, intro_seg, duration=intro_dur)
    segment_files.append(intro_seg)
    log.info(f"Intro segment: {intro_dur}s")

    # ── One segment per sign ──────────────────────────────────────────────────
    for i, reading in enumerate(readings):
        frame_path = str(frames_dir / f"sign_{i:02d}_{reading['sign'].lower()}.png")
        _render_sign_frame(frame_path, reading, date, video_type)

        seg_path = str(out_dir / f"seg_{i:02d}_{reading['sign'].lower()}.mp4")
        _frame_to_animated_video(frame_path, seg_path, duration=sign_dur)
        segment_files.append(seg_path)
        log.info(f"  [{i+1:02d}/12] {reading['sign']} — {sign_dur}s")

    # ── Outro ─────────────────────────────────────────────────────────────────
    outro_frame = str(frames_dir / "outro.png")
    _render_outro_frame(outro_frame, date)
    outro_seg = str(out_dir / "seg_outro.mp4")
    _frame_to_animated_video(outro_frame, outro_seg, duration=outro_dur)
    segment_files.append(outro_seg)
    log.info(f"Outro segment: {outro_dur}s")

    # ── Concatenate ───────────────────────────────────────────────────────────
    with open(concat_list_path, "w") as f:
        for seg in segment_files:
            f.write(f"file '{seg}'\n")

    result = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", concat_list_path, "-c", "copy", final_silent_path],
        capture_output=True, timeout=600,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed:\n{result.stderr.decode()[-500:]}")

    log.info(f"Silent video: {final_silent_path} ({_elapsed(t0)})")
    return final_silent_path


def _render_intro_frame(frame_path: str, video_type: str, date: datetime.date) -> None:
    """Render a 1280×720 intro title card."""
    try:
        from PIL import Image, ImageDraw
        W, H = 1280, 720
        img = _make_cosmic_background(W, H, "cosmic", seed=0)
        draw = ImageDraw.Draw(img)

        type_labels = {
            "daily": "DAILY HOROSCOPE",
            "weekly": "WEEKLY HOROSCOPE",
            "monthly": "MONTHLY HOROSCOPE",
            "yearly": "YEARLY HOROSCOPE",
            "special_event": "SPECIAL EVENT HOROSCOPE",
        }
        subtitle_labels = {
            "daily": date.strftime("%B %d, %Y"),
            "weekly": f"Week of {date.strftime('%B %d, %Y')}",
            "monthly": date.strftime("%B %Y"),
            "yearly": str(date.year),
            "special_event": date.strftime("%B %d, %Y"),
        }

        f_huge = _load_font(86, bold=True)
        f_big = _load_font(52, bold=True)
        f_med = _load_font(34)
        f_sm = _load_font(24)

        draw.rectangle([(0, 0), (W, 7)], fill=(140, 70, 240))

        _draw_text_centered(draw, "✨", H // 2 - 175, _load_font(66), (255, 215, 80), W)
        _draw_text_centered(draw, type_labels.get(video_type, "HOROSCOPE"),
                            H // 2 - 100, f_huge, (255, 215, 80), W)
        _draw_text_centered(draw, subtitle_labels.get(video_type, ""),
                            H // 2 + 13, f_big, (200, 150, 255), W)
        _draw_text_centered(draw, "All 12 Zodiac Signs — Complete Reading",
                            H // 2 + 87, f_med, (160, 140, 220), W)
        _draw_text_centered(draw, "GetMindFuelNow", H - 47, f_sm, (120, 100, 200), W)

        draw.rectangle([(0, H - 7), (W, H)], fill=(140, 70, 240))
        img.save(frame_path, "PNG")
    except Exception as e:
        log.debug(f"Intro frame PIL error: {e}")
        _ffmpeg_solid_frame(frame_path, "HOROSCOPE — All 12 Signs")


def _render_outro_frame(frame_path: str, date: datetime.date) -> None:
    """Render a 1280×720 outro / subscribe card."""
    try:
        from PIL import Image, ImageDraw
        W, H = 1280, 720
        img = _make_cosmic_background(W, H, "cosmic", seed=99)
        draw = ImageDraw.Draw(img)

        f_big = _load_font(66, bold=True)
        f_med = _load_font(41, bold=True)
        f_reg = _load_font(30)
        f_sm = _load_font(22)

        draw.rectangle([(0, 0), (W, 7)], fill=(140, 70, 240))

        _draw_text_centered(draw, "✨ GetMindFuelNow ✨",
                            H // 2 - 147, f_big, (255, 215, 80), W)
        _draw_text_centered(draw, "Thank You for Watching",
                            H // 2 - 60, f_med, (220, 180, 255), W)
        _draw_text_centered(draw, "SUBSCRIBE for Daily Horoscopes",
                            H // 2 + 33, f_reg, (255, 200, 80), W)
        _draw_text_centered(draw, "Drop your zodiac sign in the comments!",
                            H // 2 + 93, f_sm, (170, 150, 230), W)
        _draw_text_centered(draw, "New reading every day  •  Like  •  Subscribe  •  Share",
                            H - 60, f_sm, (120, 100, 200), W)

        draw.rectangle([(0, H - 7), (W, H)], fill=(140, 70, 240))
        img.save(frame_path, "PNG")
    except Exception as e:
        log.debug(f"Outro frame PIL error: {e}")
        _ffmpeg_solid_frame(frame_path, "SUBSCRIBE — GetMindFuelNow")


ELEMENT_COLORS = {
    "Fire":   {"top": (25, 5, 8),   "bot": (60, 10, 5),   "glow": (220, 55, 20)},
    "Earth":  {"top": (5, 20, 8),   "bot": (8, 45, 15),   "glow": (50, 180, 50)},
    "Air":    {"top": (5, 10, 35),  "bot": (8, 20, 70),   "glow": (50, 150, 240)},
    "Water":  {"top": (5, 8, 45),   "bot": (8, 12, 90),   "glow": (30, 60, 240)},
    "cosmic": {"top": (8, 5, 45),   "bot": (18, 10, 70),  "glow": (130, 50, 220)},
}


def _make_cosmic_background(W: int, H: int, element: str, seed: int = 0):
    """Create a gradient background with stars and glow effects."""
    from PIL import Image, ImageDraw
    import random as _rnd

    colors = ELEMENT_COLORS.get(element, ELEMENT_COLORS["cosmic"])
    top_c, bot_c = colors["top"], colors["bot"]
    glow_c = colors["glow"]

    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # Gradient
    for y in range(H):
        ratio = y / H
        r = int(top_c[0] + ratio * (bot_c[0] - top_c[0]))
        g = int(top_c[1] + ratio * (bot_c[1] - top_c[1]))
        b = int(top_c[2] + ratio * (bot_c[2] - top_c[2]))
        draw.rectangle([(0, y), (W, y + 1)], fill=(r, g, b))

    # Glow orbs
    from PIL import Image as _Img, ImageDraw as _ID
    for radius, alpha in [(320, 18), (200, 30), (120, 45)]:
        ov = _Img.new("RGBA", (W, H), (0, 0, 0, 0))
        ovd = _ID.Draw(ov)
        cx, cy = W // 2, H // 2
        ovd.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)],
                    fill=(*glow_c, alpha))
        img = _Img.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

    draw = ImageDraw.Draw(img)

    # Star field
    rng = _rnd.Random(seed)
    for _ in range(180):
        sx, sy = rng.randint(0, W), rng.randint(0, H)
        sz = rng.randint(1, 3)
        br = rng.randint(80, 230)
        draw.ellipse([(sx, sy), (sx + sz, sy + sz)], fill=(br, br, br))

    return img


def _render_sign_frame(
    frame_path: str,
    reading: dict,
    date: datetime.date,
    video_type: str,
) -> None:
    """
    Render a beautiful 1920×1080 sign frame.

    Layout:
      - Header: GetMindFuelNow + date
      - Large zodiac symbol (top-center)
      - Sign name (bold, large)
      - Date range + element/modality
      - Reading text box (main content — 4-6 lines of wrapped text)
      - Lucky info bar (numbers, color, day)
      - Footer accent line
    """
    try:
        from PIL import Image, ImageDraw
        W, H = 1280, 720

        sign = reading.get("sign", "")
        symbol = reading.get("symbol", "★")
        element = reading.get("element", "cosmic")
        modality = reading.get("modality", "")
        ruling_planet = reading.get("ruling_planet", "")
        dates_str = reading.get("dates", "")
        lucky_numbers = reading.get("lucky_numbers", [])
        lucky_color = reading.get("lucky_color", "")
        lucky_day = reading.get("lucky_day", "")
        script_text = reading.get("script_text", "")

        colors = ELEMENT_COLORS.get(element, ELEMENT_COLORS["cosmic"])
        glow_c = colors["glow"]
        # Lighter accent from glow
        accent = tuple(min(255, c + 80) for c in glow_c)

        img = _make_cosmic_background(W, H, element, seed=hash(sign + str(date)) % 10000)
        draw = ImageDraw.Draw(img)

        # ── Decorative accent lines ────────────────────────────────────────
        draw.rectangle([(0, 0), (W, 6)], fill=(*glow_c,))
        draw.rectangle([(0, H - 6), (W, H)], fill=(*glow_c,))

        # ── Header bar ──────────────────────────────────────────────────────
        f_header = _load_font(22)
        draw.text((35, 13), "GetMindFuelNow", font=f_header, fill=(130, 100, 220))
        date_str = date.strftime("%B %d, %Y")
        try:
            bbox = draw.textbbox((0, 0), date_str, font=f_header)
            draw.text((W - bbox[2] - 35, 13), date_str, font=f_header, fill=(130, 100, 220))
        except Exception:
            draw.text((W - 200, 13), date_str, fill=(130, 100, 220))

        # ── Central symbol ──────────────────────────────────────────────────
        f_symbol = _load_font(106, bold=True)
        f_name = _load_font(72, bold=True)
        f_sub = _load_font(29)

        _draw_text_centered(draw, symbol, 53, f_symbol, accent, W)
        _draw_text_centered(draw, sign.upper(), 177, f_name, (255, 255, 255), W)

        # Dates + element line
        elem_line = f"{dates_str}  •  {element} Sign  •  {modality}"
        if ruling_planet:
            elem_line += f"  •  {ruling_planet}"
        _draw_text_centered(draw, elem_line, 262, f_sub, (*accent,), W)

        # ── Reading text box ─────────────────────────────────────────────────
        # Extract best preview text from script (first 65 words for daily,
        # proportionally more for weekly/monthly)
        preview_words = {
            "daily": 55, "weekly": 75, "monthly": 90,
            "yearly": 100, "special_event": 60,
        }.get(video_type, 55)
        words = script_text.split()
        preview_text = " ".join(words[:preview_words])
        if len(words) > preview_words:
            preview_text += "..."

        wrapped_lines = textwrap.wrap(preview_text, width=54)[:6]  # max 6 lines

        f_reading = _load_font(27)
        f_quote = _load_font(40)

        # Semi-transparent box behind text
        box_y1 = 315
        box_y2 = box_y1 + len(wrapped_lines) * 35 + 22
        box_x1, box_x2 = 130, W - 130

        box_overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        box_draw = ImageDraw.Draw(box_overlay)
        box_draw.rounded_rectangle(
            [(box_x1, box_y1), (box_x2, box_y2)],
            radius=14,
            fill=(10, 5, 40, 145),
            outline=(*glow_c, 160),
            width=2,
        )
        img = Image.alpha_composite(img.convert("RGBA"), box_overlay).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Quote marks + reading lines (resized for 720p)
        f_quote = _load_font(40)
        ly_offset = 35
        draw.text((box_x1 + 20, box_y1 + 5), "“", font=f_quote, fill=(*accent,))

        # Render wrapped lines
        for li, line in enumerate(wrapped_lines):
            ly = box_y1 + 33 + li * ly_offset
            try:
                bbox = draw.textbbox((0, 0), line, font=f_reading)
                tw = bbox[2] - bbox[0]
                lx = (W - tw) // 2
                draw.text((lx, ly), line, font=f_reading, fill=(230, 220, 255))
            except Exception:
                draw.text((box_x1 + 40, ly), line, fill=(230, 220, 255))

        # Closing quote mark
        draw.text((box_x2 - 60, box_y2 - 55), "”", font=f_quote, fill=(*accent,))

        # ── Lucky info bar ───────────────────────────────────────────────────
        luck_y = H - 55
        f_lucky = _load_font(24)
        lucky_nums = ", ".join(str(n) for n in lucky_numbers[:3]) if lucky_numbers else "—"
        lucky_line = f"✦  Lucky: {lucky_nums}  |  {lucky_color}  |  {lucky_day}  ✦"
        _draw_text_centered(draw, lucky_line, luck_y, f_lucky, (*accent,), W)

        img.save(frame_path, "PNG")

    except Exception as e:
        log.debug(f"PIL sign frame error for {reading.get('sign', '?')}: {e}")
        _ffmpeg_solid_frame(
            frame_path,
            f"{reading.get('symbol', '★')} {reading.get('sign', '').upper()}",
        )


def _ffmpeg_solid_frame(frame_path: str, label: str) -> None:
    """Minimal ffmpeg fallback frame."""
    label_safe = label.replace("'", "").replace(":", "")
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "color=c=0x080520:s=1280x720:r=1",
        "-frames:v", "1",
        "-vf", (
            f"drawtext=text='{label_safe}':fontcolor=white:fontsize=64:"
            f"x=(w-text_w)/2:y=(h-text_h)/2,"
            f"drawtext=text='GetMindFuelNow':fontcolor=0xaaaaff:fontsize=28:"
            f"x=(w-text_w)/2:y=h-55"
        ),
        frame_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)


def _frame_to_animated_video(image_path: str, out_path: str, duration: int) -> None:
    """
    Convert a still PNG to a video with smooth fade-in and fade-out.
    The fade transitions (0.5s each) make the slideshow look much more
    professional than a hard cut between segments.
    """
    fade_dur = 0.5
    fade_out_start = max(0.5, duration - fade_dur)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-c:v", "libx264",
        "-t", str(duration),
        "-pix_fmt", "yuv420p",
        "-vf", (
            f"scale=1280:720:force_original_aspect_ratio=decrease,"
            f"pad=1280:720:(ow-iw)/2:(oh-ih)/2,"
            f"fade=t=in:st=0:d={fade_dur},"
            f"fade=t=out:st={fade_out_start}:d={fade_dur}"
        ),
        "-r", "15",
        "-preset", "ultrafast",
        "-crf", "28",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=600)
    if result.returncode != 0:
        err = result.stderr.decode()[-300:]
        raise RuntimeError(f"frame_to_animated_video failed for {image_path}:\n{err}")


# ---------------------------------------------------------------------------
# Stage 5: Mux
# ---------------------------------------------------------------------------

def _stage_mux(silent_path: str, audio_path: str, final_path: str) -> str:
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
    result = subprocess.run(cmd, capture_output=True, timeout=1200)
    if result.returncode != 0:
        log.warning(f"Mux failed: {result.stderr.decode()[-500:]}")
        shutil.copy2(silent_path, final_path)

    size_mb = os.path.getsize(final_path) / 1_048_576 if os.path.exists(final_path) else 0
    log.info(f"Final video: {final_path} ({size_mb:.1f} MB, {_elapsed(t0)})")
    return final_path


# ---------------------------------------------------------------------------
# Stage 6: Upload
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
    _div("STAGE 6 — YOUTUBE UPLOAD")
    t0 = time.time()

    metadata = {
        "title": title[:100],
        "description": description,
        "tags": tags[:500],
        "category_id": "22",
        "privacy_status": "public",
        "made_for_kids": False,
        "thumbnail": thumbnail_path,
    }

    if not upload:
        log.info("Upload skipped (--no-upload)")
        return {"ok": True, "skipped": True, "metadata": metadata}

    try:
        from youtube_uploader import upload_video, upload_thumbnail, update_channel_branding
        video_url = upload_video(final_path, metadata)
        log.info(f"Uploaded: {video_url} ({_elapsed(t0)})")

        # Upload thumbnail separately if present
        if thumbnail_path and os.path.exists(thumbnail_path):
            video_id = video_url.split("/")[-1] if "/" in str(video_url) else str(video_url)
            upload_thumbnail(video_id, thumbnail_path)

        # Keep channel bio + keywords fresh (runs at most once per week)
        try:
            update_channel_branding()
        except Exception as be:
            log.debug(f"Channel branding update skipped: {be}")

        return {"ok": True, "url": video_url, "elapsed": _elapsed(t0)}
    except Exception as e:
        log.error(f"Upload failed: {e}\n{traceback.format_exc()}")
        _add_to_retry_queue(final_path, metadata)
        return {"ok": False, "error": str(e), "queued_for_retry": True}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_horoscope_pipeline(
    video_type: str = "auto",
    date: datetime.date | None = None,
    upload: bool = True,
    event_name: str = "",
    event_type: str = "",
) -> dict:
    pipeline_start = time.time()
    if date is None:
        date = datetime.date.today()
    date_tag = date.isoformat()

    if video_type == "auto":
        from modules.horoscope.video_types import get_todays_video_types, get_event_for_date
        types_today = get_todays_video_types(date)
        primary_type = types_today[0]
        log.info(f"Auto-detected video types for {date_tag}: {types_today}")
        if primary_type == "special_event" and not event_name:
            ev = get_event_for_date(date)
            if ev:
                event_name = ev["name"]
                event_type = ev["type"]
    else:
        primary_type = video_type

    from modules.horoscope.video_types import VIDEO_TYPES
    vt_config = VIDEO_TYPES.get(primary_type, VIDEO_TYPES["daily"])

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

    # Stage 1: Content
    try:
        content = _stage_content(primary_type, date, event_name, event_type)
        readings = content["readings"]
        title = content["title"]
        description = content["description"]
        tags = content["tags"]
        full_script = content["full_script"]
        (out_base / "voiceover_script.txt").write_text(full_script)
        report["stages"]["content"] = {"ok": True, "title": title}
    except Exception as e:
        log.error(f"Content stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"content: {e}")
        report["stages"]["content"] = {"ok": False, "error": str(e)}
        return report

    # Stage 2: Audio
    word_count = len(full_script.split())
    try:
        mixed_audio, audio_duration = _stage_audio(
            full_script, audio_dir, date_tag, word_count
        )
        report["stages"]["audio"] = {"ok": True, "path": mixed_audio,
                                      "duration_s": audio_duration}
    except Exception as e:
        log.error(f"Audio stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"audio: {e}")
        report["stages"]["audio"] = {"ok": False, "error": str(e)}
        mixed_audio = ""
        audio_duration = max(60, word_count * 60 // 140)

    # Stage 3: Thumbnail
    try:
        thumb_path = _stage_thumbnail(readings, thumb_dir, primary_type, date,
                                      event_name, event_type)
        report["stages"]["thumbnail"] = {"ok": True, "path": thumb_path}
    except Exception as e:
        log.warning(f"Thumbnail failed: {e}")
        thumb_path = ""
        report["stages"]["thumbnail"] = {"ok": False, "error": str(e)}

    # Stage 4: Video
    try:
        silent_path = _stage_video(
            readings, mixed_audio, out_base, date_tag,
            primary_type, date, audio_duration,
        )
        report["stages"]["video"] = {"ok": True, "path": silent_path}
    except Exception as e:
        log.error(f"Video stage failed: {e}\n{traceback.format_exc()}")
        report["errors"].append(f"video: {e}")
        report["stages"]["video"] = {"ok": False, "error": str(e)}
        silent_path = ""

    # Stage 5: Mux
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
    except Exception as e:
        log.error(f"Mux failed: {e}")
        report["errors"].append(f"mux: {e}")
        report["stages"]["mux"] = {"ok": False, "error": str(e)}

    # Stage 6: Upload
    upload_result = _stage_upload(
        final_path, thumb_path, title, description, tags, primary_type, upload
    )
    report["stages"]["upload"] = upload_result
    parent_url = ""
    if upload_result.get("url"):
        report["output_files"]["youtube_url"] = upload_result["url"]
        parent_url = upload_result["url"]

    # Stage 7: Combined Short — one vertical video, all 12 signs, ≤ 2 minutes
    # Only runs for daily videos; enabled via SHORTS_ENABLED in .env
    try:
        import config as _cfg
        if _cfg.SHORTS_ENABLED and primary_type == "daily":
            _div("STAGE 7 — COMBINED SHORT (ALL 12 SIGNS)")
            from core.shorts_renderer import generate_combined_short
            short_result = generate_combined_short(
                readings=readings,
                date=date,
                out_dir=out_base,
                upload=upload,
                parent_url=parent_url,
            )
            report["stages"]["shorts"] = short_result
            if short_result.get("url"):
                report["output_files"]["shorts_url"] = short_result["url"]
        else:
            log.info("Shorts skipped (disabled or non-daily type)")
    except Exception as e:
        log.warning(f"Shorts stage failed (non-fatal): {e}")
        report["stages"]["shorts"] = {"ok": False, "error": str(e)}

    report["success"] = len(report["errors"]) == 0
    report["total_elapsed"] = _elapsed(pipeline_start)

    (out_base / "production_report.json").write_text(
        json.dumps(report, indent=2, default=str)
    )

    _div("COMPLETE")
    status = "SUCCESS" if report["success"] else f"DONE WITH {len(report['errors'])} ERRORS"
    log.info(f"Pipeline: {status} in {report['total_elapsed']}")
    for stage, data in report["stages"].items():
        ok = "OK" if data.get("ok") else "FAIL"
        log.info(f"  [{ok}] {stage}")
    for e in report.get("errors", []):
        log.warning(f"  ERROR: {e}")
    if report["output_files"].get("youtube_url"):
        log.info(f"  YouTube: {report['output_files']['youtube_url']}")

    return report


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GetMindFuelNow Horoscope Pipeline")
    parser.add_argument("--now",        action="store_true",  help="Run immediately")
    parser.add_argument("--no-upload",  action="store_true",  help="Generate only, no upload")
    parser.add_argument("--type",       type=str, default="auto",
                        choices=["auto", "daily", "weekly", "monthly", "yearly", "special_event"])
    parser.add_argument("--date",       type=str, default="",  help="Override date YYYY-MM-DD")
    parser.add_argument("--event-name", type=str, default="")
    parser.add_argument("--event-type", type=str, default="")
    args = parser.parse_args()

    target_date = datetime.date.fromisoformat(args.date) if args.date else None

    if args.now:
        run_horoscope_pipeline(
            video_type=args.type,
            date=target_date,
            upload=not args.no_upload,
            event_name=args.event_name,
            event_type=args.event_type,
        )
    else:
        parser.print_help()
