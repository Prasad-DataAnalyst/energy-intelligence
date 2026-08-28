"""
core/shorts_renderer.py
One YouTube Short covering all 12 zodiac signs — under 2 minutes.

Structure:
  Intro  (3-4 s)  "All 12 Signs Daily Horoscope — {date}"
  × 12 signs      sign glyph + name + first 15 words (~7-8 s each)
  Outro  (4-5 s)  Subscribe CTA

Total: ~100-115 seconds  (YouTube Shorts supports up to 3 minutes)
Format: 1080×1920 vertical (9:16)
Cost: 1 upload  = ~1600 quota units  (vs 12 uploads for per-sign approach)

AI background downloaded ONCE from Pollinations.ai (free, no key needed)
and reused across all segments — each sign gets element-coloured accents.
"""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import re
import subprocess
import sys
import textwrap
import time
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

log = logging.getLogger(__name__)

W, H = 1080, 1920  # portrait Short dimensions

ZODIAC_GLYPHS = {
    "Aries": "♈", "Taurus": "♉", "Gemini": "♊", "Cancer": "♋",
    "Leo": "♌", "Virgo": "♍", "Libra": "♎", "Scorpio": "♏",
    "Sagittarius": "♐", "Capricorn": "♑", "Aquarius": "♒", "Pisces": "♓",
}

ELEMENT_COLORS = {
    "Fire":   {"top": (25, 5, 8),  "bot": (60, 10, 5),  "glow": (220, 55, 20)},
    "Earth":  {"top": (5, 20, 8),  "bot": (8, 45, 15),  "glow": (50, 180, 50)},
    "Air":    {"top": (5, 10, 35), "bot": (8, 20, 70),  "glow": (50, 150, 240)},
    "Water":  {"top": (5, 8, 45),  "bot": (8, 12, 90),  "glow": (30, 60, 240)},
    "cosmic": {"top": (8, 5, 45),  "bot": (18, 10, 70), "glow": (130, 50, 220)},
}

_AI_PROMPTS = [
    "stunning purple cosmic nebula stars galaxy glowing mystical spiritual portrait vertical, cinematic 4k, no text no words",
    "golden celestial light rays cosmos stars divine ethereal portrait vertical background, epic cinematic, no text no words",
    "deep blue cosmos moon phases planets night sky magical portrait vertical, cinematic 4k, no text no words",
    "violet mystical energy galaxy stars ethereal spiritual cosmic portrait vertical, 4k, no text no words",
    "cosmic dawn purple gold stars rising divine light ethereal portrait vertical, cinematic, no text no words",
    "deep space nebula galaxy glowing stars ethereal blue purple portrait vertical, 4k, no text no words",
    "emerald cosmic energy stars planets divine light mystical portrait vertical, 4k, no text no words",
    "silver moonlight cosmic stars night sky serene portrait vertical, cinematic, no text no words",
    "royal purple cosmic stars divine energy spiritual nebula portrait vertical, 4k, no text no words",
    "rose gold cosmic stars galaxy mystical ethereal spiritual portrait vertical, cinematic 4k, no text no words",
    "teal cosmic energy universe stars glowing mystical divine portrait vertical, 4k, no text no words",
    "indigo starfield cosmic rays spiritual energy portrait vertical, cinematic, no text no words",
]


# ─────────────────────────────────────────────────────────────────────────────
# Font / drawing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _load_font(size: int, bold: bool = False):
    bold_paths = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
    ]
    reg_paths = [
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ]
    from PIL import ImageFont
    for fp in (bold_paths if bold else reg_paths):
        if os.path.exists(fp):
            try:
                return ImageFont.truetype(fp, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _draw_centered(draw, text: str, y: int, font, color: tuple) -> None:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), text, font=font, fill=color)
    except Exception:
        draw.text((W // 2 - 100, y), text, fill=color)


# ─────────────────────────────────────────────────────────────────────────────
# Background — downloaded once, shared across all frames
# ─────────────────────────────────────────────────────────────────────────────

def _make_gradient_bg(element: str = "cosmic", seed: int = 0):
    """Fallback 1080×1920 gradient + starfield."""
    from PIL import Image, ImageDraw
    import random as _rnd

    colors = ELEMENT_COLORS.get(element, ELEMENT_COLORS["cosmic"])
    top_c, bot_c = colors["top"], colors["bot"]
    glow_c = colors["glow"]

    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)
    for y in range(H):
        ratio = y / H
        r = int(top_c[0] + ratio * (bot_c[0] - top_c[0]))
        g = int(top_c[1] + ratio * (bot_c[1] - top_c[1]))
        b = int(top_c[2] + ratio * (bot_c[2] - top_c[2]))
        draw.rectangle([(0, y), (W, y + 1)], fill=(r, g, b))

    from PIL import Image as _I, ImageDraw as _ID
    for radius, alpha in [(480, 14), (290, 24), (170, 38)]:
        ov = _I.new("RGBA", (W, H), (0, 0, 0, 0))
        _ID.Draw(ov).ellipse(
            [(W // 2 - radius, H // 3 - radius), (W // 2 + radius, H // 3 + radius)],
            fill=(*glow_c, alpha),
        )
        img = _I.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

    draw = ImageDraw.Draw(img)
    rng = _rnd.Random(seed)
    for _ in range(280):
        sx, sy = rng.randint(0, W), rng.randint(0, H)
        sz = rng.randint(1, 3)
        br = rng.randint(80, 230)
        draw.ellipse([(sx, sy), (sx + sz, sy + sz)], fill=(br, br, br))

    return img


def _get_shared_bg(shorts_dir: Path, date: datetime.date):
    """
    Download ONE 1080×1920 AI background (or use gradient fallback).
    Returns a PIL Image ready to be .copy()'d for each frame.
    """
    from PIL import Image
    import urllib.request, urllib.parse

    prompt = _AI_PROMPTS[date.toordinal() % len(_AI_PROMPTS)]
    seed   = (date.toordinal() * 7) % 99999
    ai_path = str(shorts_dir / "shared_bg.jpg")

    try:
        encoded = urllib.parse.quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1080&height=1920&nologo=true&seed={seed}&model=flux"
        )
        log.info(f"Downloading shared AI background (seed={seed})…")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=180) as resp:
            with open(ai_path, "wb") as f:
                f.write(resp.read())
        if os.path.exists(ai_path) and os.path.getsize(ai_path) > 5_000:
            log.info(f"AI bg: {os.path.getsize(ai_path) // 1024}KB")
            return Image.open(ai_path).convert("RGB").resize((W, H), Image.LANCZOS)
    except Exception as e:
        log.warning(f"AI bg download failed ({e}), using gradient fallback")

    return _make_gradient_bg(seed=seed % 10000)


# ─────────────────────────────────────────────────────────────────────────────
# Frame renderers
# ─────────────────────────────────────────────────────────────────────────────

def _apply_dark_overlay(img, strength: int = 140):
    """Add uniform dark overlay for text contrast."""
    from PIL import Image, ImageDraw
    ov = Image.new("RGBA", (W, H), (4, 2, 18, strength))
    return Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")


def _render_intro_frame(out_path: str, date: datetime.date, bg) -> None:
    from PIL import Image, ImageDraw
    img  = _apply_dark_overlay(bg.copy(), 160)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0),    (W, 8)], fill=(140, 60, 255))
    draw.rectangle([(0, H - 8),(W, H)], fill=(140, 60, 255))

    _draw_centered(draw, "GetMindFuelNow",          60,  _load_font(46),         (160, 130, 220))
    _draw_centered(draw, "✨",                       240, _load_font(200),        (255, 215, 60))
    _draw_centered(draw, "DAILY HOROSCOPE",          520, _load_font(100, True),  (255, 215, 55))
    _draw_centered(draw, "ALL 12 ZODIAC SIGNS",      650, _load_font(68, True),   (215, 175, 255))
    _draw_centered(draw, date.strftime("%B %d, %Y"), 745, _load_font(52),         (190, 155, 255))
    _draw_centered(draw, "♈ ♉ ♊ ♋ ♌ ♍ ♎ ♏ ♐ ♑ ♒ ♓",
                   880, _load_font(54), (200, 170, 255))

    # CTA box
    from PIL import Image as _I, ImageDraw as _ID
    box_ov = _I.new("RGBA", (W, H), (0, 0, 0, 0))
    _ID.Draw(box_ov).rounded_rectangle(
        [(80, 1050), (W - 80, 1170)], radius=22,
        fill=(35, 5, 80, 200), outline=(160, 80, 255, 210), width=3,
    )
    img = _I.alpha_composite(img.convert("RGBA"), box_ov).convert("RGB")
    draw = ImageDraw.Draw(img)
    _draw_centered(draw, "🔔 Subscribe for Daily Readings", 1070, _load_font(52, True), (255, 215, 55))

    img.save(out_path, "PNG")


def _render_sign_frame(reading: dict, out_path: str,
                       date: datetime.date, bg,
                       sign_index: int = 0, total_signs: int = 12) -> None:
    from PIL import Image, ImageDraw

    sign      = reading.get("sign", "")
    symbol    = reading.get("symbol", "★")
    element   = reading.get("element", "cosmic")
    dates_str = reading.get("dates", "")
    lucky_color   = reading.get("lucky_color", "")
    lucky_day     = reading.get("lucky_day", "")
    script_text   = reading.get("script_text", "")

    colors = ELEMENT_COLORS.get(element, ELEMENT_COLORS["cosmic"])
    glow_c = colors["glow"]
    accent = tuple(min(255, c + 90) for c in glow_c)

    # Tint background with element glow
    from PIL import Image as _I, ImageDraw as _ID
    tint_ov = _I.new("RGBA", (W, H), (0, 0, 0, 0))
    _ID.Draw(tint_ov).rectangle([(0, 0), (W, H)], fill=(*glow_c, 25))
    img = _I.alpha_composite(bg.copy().convert("RGBA"), tint_ov)
    img = _apply_dark_overlay(img, 130)
    draw = ImageDraw.Draw(img)

    # Element-coloured accent stripes
    draw.rectangle([(0, 0),    (W, 10)], fill=(*glow_c,))
    draw.rectangle([(0, H - 10),(W, H)], fill=(*glow_c,))

    # Header
    _draw_centered(draw, "GetMindFuelNow",          22,  _load_font(40),         (160, 130, 220))
    _draw_centered(draw, date.strftime("%B %d, %Y"),75,  _load_font(36, True),   (215, 175, 255))

    # Giant zodiac glyph
    _draw_centered(draw, symbol,      200, _load_font(290, True), accent)

    # Sign name + metadata
    _draw_centered(draw, sign.upper(), 560, _load_font(118, True), (255, 255, 255))
    _draw_centered(draw, dates_str,    690, _load_font(40),         (*accent,))

    # Reading preview (first 20 words → ~3 lines)
    words   = script_text.split()
    preview = " ".join(words[:20]) + ("..." if len(words) > 20 else "")
    wrapped = textwrap.wrap(preview, width=26)[:3]

    f_read = _load_font(52)
    box_y1 = 780
    line_h = 66
    box_y2 = box_y1 + len(wrapped) * line_h + 28
    bx1, bx2 = 55, W - 55

    box_ov = _I.new("RGBA", (W, H), (0, 0, 0, 0))
    _ID.Draw(box_ov).rounded_rectangle(
        [(bx1, box_y1), (bx2, box_y2)], radius=22,
        fill=(10, 5, 40, 165), outline=(*glow_c, 195), width=3,
    )
    img = _I.alpha_composite(img.convert("RGBA"), box_ov).convert("RGB")
    draw = ImageDraw.Draw(img)

    for li, line in enumerate(wrapped):
        try:
            bbox = draw.textbbox((0, 0), line, font=f_read)
            lx = (W - (bbox[2] - bbox[0])) // 2
        except Exception:
            lx = bx1 + 40
        draw.text((lx, box_y1 + 18 + li * line_h), line,
                  font=f_read, fill=(230, 220, 255))

    # Lucky line
    lucky_line = f"✦ {lucky_color}  •  {lucky_day} ✦"
    _draw_centered(draw, lucky_line, box_y2 + 36, _load_font(40), (*accent,))

    # ── Progress bar + sign counter (e.g. "3 / 12") ─────────────────────────
    # Shows viewers where they are in the Short — drives watch time
    bar_y  = H - 130
    bar_h  = 8
    bar_x1, bar_x2 = 60, W - 60
    bar_w  = bar_x2 - bar_x1
    filled = int(bar_w * (sign_index + 1) / total_signs)

    # Background bar (dim)
    draw.rounded_rectangle([(bar_x1, bar_y), (bar_x2, bar_y + bar_h)],
                            radius=4, fill=(60, 40, 100, 180))
    # Filled portion (element accent colour)
    if filled > 0:
        draw.rounded_rectangle([(bar_x1, bar_y), (bar_x1 + filled, bar_y + bar_h)],
                                radius=4, fill=(*accent,))

    # Sign counter
    counter_text = f"{sign_index + 1} / {total_signs}"
    _draw_centered(draw, counter_text, bar_y + 16, _load_font(36, True), (*accent,))

    # Bottom channel label
    _draw_centered(draw, "GetMindFuelNow  •  🔔 Subscribe",
                   H - 65, _load_font(36, True), (255, 215, 55))

    img.save(out_path, "PNG")


def _render_outro_frame(out_path: str, bg) -> None:
    from PIL import Image, ImageDraw
    img  = _apply_dark_overlay(bg.copy(), 165)
    draw = ImageDraw.Draw(img)

    draw.rectangle([(0, 0),    (W, 8)], fill=(140, 60, 255))
    draw.rectangle([(0, H - 8),(W, H)], fill=(140, 60, 255))

    _draw_centered(draw, "✨",                              220, _load_font(160),        (255, 215, 60))
    _draw_centered(draw, "GetMindFuelNow",                  470, _load_font(90, True),   (255, 215, 55))
    _draw_centered(draw, "SUBSCRIBE",                       590, _load_font(110, True),  (220, 180, 255))
    _draw_centered(draw, "for Daily Horoscopes",            720, _load_font(58),         (190, 155, 255))
    _draw_centered(draw, "New reading every day  •  All 12 signs",
                   820, _load_font(44),         (160, 130, 210))
    _draw_centered(draw, "Drop your sign in the comments!",
                   900, _load_font(46),         (200, 170, 240))
    _draw_centered(draw, "♈ ♉ ♊ ♋ ♌ ♍ ♎ ♏ ♐ ♑ ♒ ♓",
                   1000, _load_font(60),        (200, 170, 255))

    img.save(out_path, "PNG")


# ─────────────────────────────────────────────────────────────────────────────
# Audio
# ─────────────────────────────────────────────────────────────────────────────

async def _async_tts(text: str, out_path: str) -> None:
    import edge_tts
    # Shorts: warm British voice at -8% — fast enough to fit 45s, deep enough
    # to feel intentional.  Same voice family as main video for brand consistency.
    comm = edge_tts.Communicate(
        text,
        voice="en-GB-SoniaNeural",
        rate="-8%",
        pitch="-5Hz",
    )
    await comm.save(out_path)


def _tts(text: str, out_path: str) -> int:
    """Generate TTS, return duration in seconds. Falls back to silence."""
    try:
        asyncio.run(_async_tts(text, out_path))
        if os.path.exists(out_path) and os.path.getsize(out_path) > 500:
            return _audio_duration(out_path)
    except Exception as e:
        log.warning(f"TTS failed: {e}")
    # Fallback: estimate ~2 words/sec
    words = len(text.split())
    dur   = max(3, words // 2)
    _silent_wav(out_path.replace(".mp3", "_sil.wav"), dur)
    return dur


def _audio_duration(path: str) -> int:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return max(1, int(float(r.stdout.strip())))
    except Exception:
        return 5


def _silent_wav(path: str, duration_s: int) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration_s), "-acodec", "pcm_s16le", path,
    ], capture_output=True, timeout=30)


# ─────────────────────────────────────────────────────────────────────────────
# Video segment builder + concatenator
# ─────────────────────────────────────────────────────────────────────────────

def _build_segment(frame_path: str, audio_path: str,
                   out_path: str, duration: int) -> bool:
    """One frame + one audio clip → one mp4 segment."""
    fade = 0.3
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", frame_path,
        "-i", audio_path,
        "-c:v", "libx264", "-c:a", "aac", "-b:a", "128k",
        "-t", str(duration + 0.3),
        "-pix_fmt", "yuv420p",
        "-vf", (
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2,"
            f"fade=t=in:st=0:d={fade}"
        ),
        "-r", "30",
        "-preset", "ultrafast",
        "-crf", "23",        # sharper than 26; same speed with ultrafast
        "-shortest",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=120)
    ok = r.returncode == 0 and os.path.exists(out_path)
    if not ok:
        log.warning(f"Segment build failed: {r.stderr.decode()[-200:]}")
    return ok


def _concat_segments(segment_paths: list[str], out_path: str) -> bool:
    """Concatenate mp4 segments using ffmpeg concat demuxer."""
    txt = out_path.replace(".mp4", "_concat.txt")
    with open(txt, "w") as f:
        for seg in segment_paths:
            f.write(f"file '{seg}'\n")
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", txt, "-c", "copy", out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=300)
    ok = r.returncode == 0 and os.path.exists(out_path)
    if ok:
        size_mb = os.path.getsize(out_path) / 1_048_576
        log.info(f"Combined Short: {out_path} ({size_mb:.1f} MB)")
    else:
        log.warning(f"Concat failed: {r.stderr.decode()[-300:]}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# Uploader
# ─────────────────────────────────────────────────────────────────────────────

def _upload_combined_short(video_path: str, date: datetime.date,
                           parent_url: str = "") -> str:
    """Upload the combined Short. Returns YouTube URL or ''."""
    title = (
        f"Daily Horoscope ALL 12 Signs — {date.strftime('%B %d, %Y')} #Shorts"
    )[:100]

    desc_parts = [
        f"All 12 zodiac sign readings for {date.strftime('%B %d, %Y')} in under 2 minutes!",
        "",
        "♈ Aries  ♉ Taurus  ♊ Gemini  ♋ Cancer",
        "♌ Leo  ♍ Virgo  ♎ Libra  ♏ Scorpio",
        "♐ Sagittarius  ♑ Capricorn  ♒ Aquarius  ♓ Pisces",
        "",
        "🌙 GetMindFuelNow — Full daily readings for every sign, every day.",
    ]
    if parent_url:
        desc_parts += ["", f"Full extended reading: {parent_url}"]
    desc_parts += [
        "",
        "✨ Subscribe: https://www.youtube.com/@GetMindFuelNow",
        "🔔 Hit the bell so you never miss a daily reading!",
        "",
        "#Shorts #horoscope #dailyhoroscope #astrology #zodiac #allsigns #GetMindFuelNow",
    ]

    raw_tags = ["horoscope", "daily horoscope", "all 12 signs", "astrology",
                "zodiac", "Shorts", "GetMindFuelNow", "zodiac signs", "cosmic"]
    tags, total = [], 0
    for tag in raw_tags:
        tag = re.sub(r"[^\w\s\-]", "", tag, flags=re.UNICODE).strip()
        if not tag or len(tags) >= 10:
            break
        addition = len(tag) + (1 if tags else 0)
        if total + addition > 400:
            break
        tags.append(tag)
        total += addition

    metadata = {
        "title":          title,
        "description":    "\n".join(desc_parts)[:5000],
        "tags":           tags,
        "category_id":   "22",
        "privacy_status": "public",
        "made_for_kids":  False,
        "thumbnail":      "",
    }

    try:
        from youtube_uploader import upload_video
        url = upload_video(video_path, metadata)
        log.info(f"Combined Short uploaded → {url}")
        return str(url)
    except Exception as e:
        log.warning(f"Combined Short upload failed: {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_combined_short(
    readings: list[dict],
    date: datetime.date,
    out_dir: Path,
    upload: bool = True,
    parent_url: str = "",
) -> dict:
    """
    Generate ONE YouTube Short covering all 12 zodiac signs (≤ 2 minutes).

    Steps:
      1. Download AI portrait background once  (shared across all frames)
      2. Render intro frame + 12 sign frames + outro frame
      3. Generate TTS audio for intro + each sign + outro
      4. Build 14 video segments then concatenate into one mp4
      5. Upload as a single Short

    Returns: {ok, video_path, url, duration_s, elapsed}
    """
    t_start  = time.time()
    shorts_dir = out_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)

    log.info("Combined Short: starting…")

    # ── 1. Shared AI background ───────────────────────────────────────────────
    bg = _get_shared_bg(shorts_dir, date)

    segments: list[str] = []

    # ── 2. Intro ──────────────────────────────────────────────────────────────
    intro_frame = str(shorts_dir / "comb_intro.png")
    intro_audio = str(shorts_dir / "comb_intro.mp3")
    intro_video = str(shorts_dir / "comb_intro.mp4")

    try:
        _render_intro_frame(intro_frame, date, bg)
    except Exception as e:
        log.warning(f"Intro frame error: {e}")

    intro_text = f"All twelve signs. {date.strftime('%B %d')}. Here we go."
    intro_dur = _tts(intro_text, intro_audio)

    if os.path.exists(intro_frame) and _build_segment(intro_frame, intro_audio, intro_video, intro_dur):
        segments.append(intro_video)
    log.info(f"Intro: {intro_dur}s")

    # ── 3. One segment per sign ───────────────────────────────────────────────
    for i, reading in enumerate(readings):
        sign    = reading.get("sign", f"sign{i}")
        words   = reading.get("script_text", "").split()
        # 5 words per sign → ~3s at normal TTS speed → 12×3 = 36s for signs alone
        # Total Short target: intro(~3s) + 12×3s + outro(~3s) ≈ 42s ✓
        preview = " ".join(words[:5])

        frame_path = str(shorts_dir / f"comb_frame_{sign.lower()}.png")
        audio_path = str(shorts_dir / f"comb_audio_{sign.lower()}.mp3")
        video_path = str(shorts_dir / f"comb_seg_{sign.lower()}.mp4")

        # Frame — pass index so progress bar shows "3 / 12"
        try:
            _render_sign_frame(reading, frame_path, date, bg,
                               sign_index=i, total_signs=len(readings))
        except Exception as e:
            log.warning(f"Sign frame error ({sign}): {e}")
            continue

        # Audio: "{Sign}. {first 5 words}" — short, punchy, Shorts-native
        tts_text = f"{sign}. {preview}."
        seg_dur  = _tts(tts_text, audio_path)

        if _build_segment(frame_path, audio_path, video_path, seg_dur):
            segments.append(video_path)
            log.info(f"  [{i+1:02d}/12] {sign} — {seg_dur}s")
        else:
            log.warning(f"  [{i+1:02d}/12] {sign} — segment failed")

    # ── 4. Outro ──────────────────────────────────────────────────────────────
    outro_frame = str(shorts_dir / "comb_outro.png")
    outro_audio = str(shorts_dir / "comb_outro.mp3")
    outro_video = str(shorts_dir / "comb_outro.mp4")

    try:
        _render_outro_frame(outro_frame, bg)
    except Exception as e:
        log.warning(f"Outro frame error: {e}")

    outro_text = "Subscribe. GetMindFuelNow. Daily readings."
    outro_dur = _tts(outro_text, outro_audio)

    if os.path.exists(outro_frame) and _build_segment(outro_frame, outro_audio, outro_video, outro_dur):
        segments.append(outro_video)
    log.info(f"Outro: {outro_dur}s")

    # ── 5. Concatenate ────────────────────────────────────────────────────────
    if len(segments) < 3:
        log.error(f"Too few segments ({len(segments)}) to build Combined Short — need at least intro + 1 sign + outro")
        return {"ok": False, "error": f"only {len(segments)} segment(s) built"}

    final_path = str(shorts_dir / f"combined_short_{date.isoformat()}.mp4")
    ok = _concat_segments(segments, final_path)

    if not ok:
        return {"ok": False, "error": "concat failed"}

    total_dur = _audio_duration(final_path)
    elapsed   = f"{time.time() - t_start:.1f}s"
    log.info(f"Combined Short ready: {total_dur}s total, built in {elapsed}")

    # ── 6. Upload ─────────────────────────────────────────────────────────────
    url = ""
    if upload:
        url = _upload_combined_short(final_path, date, parent_url)

    return {
        "ok":         True,
        "video_path": final_path,
        "url":        url,
        "duration_s": total_dur,
        "segments":   len(segments),
        "elapsed":    elapsed,
    }
