"""
core/shorts_renderer.py
Generate and upload 12 per-sign YouTube Shorts from a daily horoscope run.

Each Short:
  - 1080×1920 vertical format (9:16)
  - 45–58 seconds  (YouTube Shorts ≤ 60 s)
  - One zodiac sign's reading (first ~90 words + CTA)
  - AI-generated vertical background via Pollinations.ai (free, no key)
  - Title: ♈ Aries Daily Horoscope — June 15, 2026 #Shorts

Quota note:
  Each YouTube upload costs ~1600 quota units.  The free daily quota is
  10,000 units.  With the main video (~1600) that leaves room for
  MAX_SHORTS_PER_DAY (default 4) before the limit.  To upload all 12,
  request a quota increase at:
  https://support.google.com/youtube/contact/yt_api_form
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


def _draw_centered(draw, text: str, y: int, font, color: tuple, W: int) -> None:
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw = bbox[2] - bbox[0]
        draw.text(((W - tw) // 2, y), text, font=font, fill=color)
    except Exception:
        draw.text((W // 2 - 100, y), text, fill=color)


# ─────────────────────────────────────────────────────────────────────────────
# Background generation
# ─────────────────────────────────────────────────────────────────────────────

def _make_vertical_gradient(W: int, H: int, element: str, seed: int = 0):
    """1080×1920 cosmic gradient + starfield fallback background."""
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
        ovd = _ID.Draw(ov)
        cx, cy = W // 2, H // 3
        ovd.ellipse([(cx - radius, cy - radius), (cx + radius, cy + radius)],
                    fill=(*glow_c, alpha))
        img = _I.alpha_composite(img.convert("RGBA"), ov).convert("RGB")

    draw = ImageDraw.Draw(img)
    rng = _rnd.Random(seed)
    for _ in range(280):
        sx, sy = rng.randint(0, W), rng.randint(0, H)
        sz = rng.randint(1, 3)
        br = rng.randint(80, 230)
        draw.ellipse([(sx, sy), (sx + sz, sy + sz)], fill=(br, br, br))

    return img


def _download_ai_vertical_bg(prompt: str, out_path: str, seed: int) -> bool:
    """Download 1080×1920 portrait AI background from Pollinations.ai (free)."""
    import urllib.request, urllib.parse
    try:
        encoded = urllib.parse.quote(prompt)
        url = (
            f"https://image.pollinations.ai/prompt/{encoded}"
            f"?width=1080&height=1920&nologo=true&seed={seed}&model=flux"
        )
        log.info(f"AI portrait bg (seed={seed}): {prompt[:55]}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=90) as resp:
            with open(out_path, "wb") as f:
                f.write(resp.read())
        ok = os.path.exists(out_path) and os.path.getsize(out_path) > 5_000
        if ok:
            log.info(f"AI bg: {os.path.getsize(out_path) // 1024}KB")
        return ok
    except Exception as e:
        log.warning(f"AI portrait bg failed: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Frame renderer
# ─────────────────────────────────────────────────────────────────────────────

def _render_short_frame(reading: dict, out_path: str,
                        date: datetime.date, seed: int) -> None:
    """
    Render a stunning 1080×1920 portrait frame for one sign's Short.

    Layout (top → bottom):
      28   — channel name
      85   — date
      220  — large zodiac glyph  (260px font)
      530  — sign name  (110px bold)
      658  — "DAILY HOROSCOPE • #Shorts"
      718  — sign date range
      790+ — reading preview box  (first 80 words, max 7 lines)
      below box — lucky info bar
      H-210 — Subscribe CTA
    """
    from PIL import Image, ImageDraw
    W, H = 1080, 1920

    sign          = reading.get("sign", "")
    symbol        = reading.get("symbol", "★")
    element       = reading.get("element", "cosmic")
    dates_str     = reading.get("dates", "")
    lucky_numbers = reading.get("lucky_numbers", [])
    lucky_color   = reading.get("lucky_color", "")
    lucky_day     = reading.get("lucky_day", "")
    script_text   = reading.get("script_text", "")

    colors = ELEMENT_COLORS.get(element, ELEMENT_COLORS["cosmic"])
    glow_c = colors["glow"]
    accent = tuple(min(255, c + 80) for c in glow_c)

    # Background: AI portrait or cosmic gradient fallback
    prompt  = _AI_PROMPTS[date.toordinal() % len(_AI_PROMPTS)]
    ai_path = out_path.replace(".png", "_ai.jpg")
    ai_ok   = _download_ai_vertical_bg(prompt, ai_path, seed)

    if ai_ok:
        try:
            img = Image.open(ai_path).convert("RGB").resize((W, H), Image.LANCZOS)
        except Exception:
            ai_ok = False

    if not ai_ok:
        img = _make_vertical_gradient(W, H, element, seed=seed % 10000)

    # Dark overlays for text readability
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov = ImageDraw.Draw(overlay)
    for y in range(230):                         # top fade
        alpha = int(215 * (1 - y / 230))
        ov.rectangle([(0, y), (W, y + 1)], fill=(4, 2, 18, alpha))
    for y in range(360, 1480):                   # content area semi-dark
        ov.rectangle([(0, y), (W, y + 1)], fill=(4, 2, 18, 140))
    for y in range(1480, H):                     # bottom fade
        alpha = int(210 * ((y - 1480) / (H - 1480)))
        ov.rectangle([(0, y), (W, y + 1)], fill=(4, 2, 18, alpha))
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Accent stripes
    draw.rectangle([(0, 0), (W, 7)], fill=(140, 60, 255))
    draw.rectangle([(0, H - 7), (W, H)], fill=(140, 60, 255))

    # ── Header ───────────────────────────────────────────────────────────────
    f_sm   = _load_font(44)
    f_date = _load_font(40, bold=True)
    _draw_centered(draw, "GetMindFuelNow", 28,  f_sm,   (160, 130, 220), W)
    _draw_centered(draw, date.strftime("%B %d, %Y"), 85, f_date, (215, 175, 255), W)

    # ── Zodiac glyph + sign name ─────────────────────────────────────────────
    f_glyph = _load_font(260, bold=True)
    f_sign  = _load_font(110, bold=True)
    f_sub   = _load_font(44)
    f_dates = _load_font(38)

    _draw_centered(draw, symbol, 220, f_glyph, accent,          W)
    _draw_centered(draw, sign.upper(), 530, f_sign, (255, 255, 255), W)
    _draw_centered(draw, "DAILY HOROSCOPE  •  #Shorts", 658, f_sub, (215, 175, 255), W)
    _draw_centered(draw, dates_str, 718, f_dates, (*accent,),   W)

    # ── Reading preview box ───────────────────────────────────────────────────
    words   = script_text.split()
    preview = " ".join(words[:80]) + ("..." if len(words) > 80 else "")
    wrapped = textwrap.wrap(preview, width=32)[:7]  # max 7 lines

    f_reading = _load_font(44)
    f_quote   = _load_font(65)

    box_y1 = 790
    line_h = 58
    box_y2 = box_y1 + len(wrapped) * line_h + 30
    bx1, bx2 = 50, W - 50

    box_ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bov = ImageDraw.Draw(box_ov)
    bov.rounded_rectangle(
        [(bx1, box_y1), (bx2, box_y2)],
        radius=22, fill=(10, 5, 40, 160), outline=(*glow_c, 190), width=3,
    )
    img = Image.alpha_composite(img.convert("RGBA"), box_ov).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.text((bx1 + 28, box_y1 + 5),   "“", font=f_quote, fill=(*accent,))
    draw.text((bx2 - 72, box_y2 - 62),  "”", font=f_quote, fill=(*accent,))

    for li, line in enumerate(wrapped):
        try:
            bbox = draw.textbbox((0, 0), line, font=f_reading)
            lx = (W - (bbox[2] - bbox[0])) // 2
        except Exception:
            lx = bx1 + 40
        draw.text((lx, box_y1 + 52 + li * line_h), line,
                  font=f_reading, fill=(230, 220, 255))

    # ── Lucky info ────────────────────────────────────────────────────────────
    lucky_nums = ", ".join(str(n) for n in lucky_numbers[:3]) if lucky_numbers else "—"
    lucky_line = f"✦ Lucky: {lucky_nums}  •  {lucky_color}  •  {lucky_day} ✦"
    _draw_centered(draw, lucky_line, box_y2 + 38, _load_font(38), (*accent,), W)

    # ── Subscribe CTA ─────────────────────────────────────────────────────────
    cta_y = H - 215
    _draw_centered(draw, "🔔 Subscribe for Daily Readings",
                   cta_y,      _load_font(52, bold=True), (255, 215, 55), W)
    _draw_centered(draw, "Drop your zodiac sign in the comments!",
                   cta_y + 70, _load_font(40),            (190, 155, 255), W)

    img.save(out_path, "PNG")
    log.info(f"Short frame: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Audio
# ─────────────────────────────────────────────────────────────────────────────

async def _async_tts(text: str, out_path: str) -> None:
    import edge_tts
    comm = edge_tts.Communicate(text, voice="en-US-AriaNeural", rate="-10%")
    await comm.save(out_path)


def _tts_for_sign(script_text: str, out_path: str, sign: str) -> tuple[str, int]:
    """
    Generate TTS for one sign's Short (~45–55 s).
    First 90 words + brief CTA.  Returns (audio_path, duration_s).
    """
    words = script_text.split()
    preview = " ".join(words[:90])
    full_text = (
        f"Here is your {sign} daily horoscope. {preview} "
        "Subscribe to GetMindFuelNow for your daily cosmic guidance."
    )
    try:
        asyncio.run(_async_tts(full_text, out_path))
        if os.path.exists(out_path) and os.path.getsize(out_path) > 1000:
            return out_path, _get_audio_duration(out_path)
    except Exception as e:
        log.warning(f"edge-tts Short failed ({sign}): {e}")

    # Fallback: 50-second silence so video still generates
    silent = out_path.replace(".mp3", "_silent.wav")
    _generate_silent_wav(silent, 50)
    return silent, 50


def _get_audio_duration(path: str) -> int:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=30,
        )
        return max(10, int(float(result.stdout.strip())))
    except Exception:
        return 50


def _generate_silent_wav(path: str, duration_s: int = 50) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t", str(duration_s), "-acodec", "pcm_s16le", path,
    ], capture_output=True, timeout=30)


# ─────────────────────────────────────────────────────────────────────────────
# Video builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_short_video(frame_path: str, audio_path: str,
                       out_path: str, duration: int) -> bool:
    """Combine portrait frame + audio into a vertical mp4 Short (≤59 s)."""
    fade = 0.4
    fade_out_start = max(0.5, duration - fade)
    max_dur = min(duration + 1, 59)          # Shorts hard cap

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", frame_path,
        "-i", audio_path,
        "-c:v", "libx264",
        "-c:a", "aac", "-b:a", "128k",
        "-t", str(max_dur),
        "-pix_fmt", "yuv420p",
        "-vf", (
            f"scale=1080:1920:force_original_aspect_ratio=decrease,"
            f"pad=1080:1920:(ow-iw)/2:(oh-ih)/2,"
            f"fade=t=in:st=0:d={fade},"
            f"fade=t=out:st={fade_out_start}:d={fade}"
        ),
        "-r", "30",                          # Shorts work best at 30 fps
        "-preset", "ultrafast",
        "-crf", "26",
        "-shortest",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode == 0 and os.path.exists(out_path):
        log.info(f"Short mp4: {out_path} ({os.path.getsize(out_path) // 1024}KB)")
        return True
    log.warning(f"Short build failed: {result.stderr.decode()[-300:]}")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Uploader
# ─────────────────────────────────────────────────────────────────────────────

def _upload_short(video_path: str, reading: dict,
                  date: datetime.date, parent_url: str = "") -> str:
    """Upload one Short to YouTube. Returns video URL or ''."""
    sign   = reading.get("sign", "")
    symbol = reading.get("symbol", ZODIAC_GLYPHS.get(sign, "★"))
    glyph  = ZODIAC_GLYPHS.get(sign, symbol)

    title = f"{glyph} {sign} Daily Horoscope — {date.strftime('%B %d, %Y')} #Shorts"[:100]

    desc_parts = [
        f"Your complete {sign} horoscope for {date.strftime('%B %d, %Y')}.",
        "",
        "🌙 GetMindFuelNow — Daily cosmic guidance for all 12 zodiac signs.",
    ]
    if parent_url:
        desc_parts += ["", f"Full reading for ALL 12 signs: {parent_url}"]
    desc_parts += [
        "",
        "✨ Subscribe: https://www.youtube.com/@GetMindFuelNow",
        "🔔 Hit the bell so you never miss a reading!",
        "",
        f"#{sign} #horoscope #dailyhoroscope #astrology #zodiac #Shorts #GetMindFuelNow",
    ]

    raw_tags = [sign, "horoscope", "daily horoscope", "astrology",
                "zodiac", "Shorts", "GetMindFuelNow", "cosmic"]
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
        log.info(f"Short uploaded: {sign} → {url}")
        return str(url)
    except Exception as e:
        log.warning(f"Short upload failed ({sign}): {e}")
        return ""


# ─────────────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────────────

def generate_all_shorts(
    readings: list[dict],
    date: datetime.date,
    out_dir: Path,
    upload: bool = True,
    parent_url: str = "",
    max_uploads: int = 4,
) -> list[dict]:
    """
    Generate (and optionally upload) one Short per zodiac sign.

    Args:
        readings:    list of reading dicts from content_engine (all 12 signs)
        date:        video date
        out_dir:     base output dir (e.g. output/horoscope_2026-06-15_daily/)
        upload:      whether to push to YouTube
        parent_url:  YouTube URL of the main full-length video (linked in description)
        max_uploads: how many Shorts to actually upload per run (quota guard)

    Returns:
        list of {sign, video_path, url, ok, elapsed} dicts
    """
    shorts_dir = out_dir / "shorts"
    shorts_dir.mkdir(parents=True, exist_ok=True)

    results       = []
    uploads_done  = 0

    log.info(f"Shorts: generating {len(readings)} signs (upload={upload}, max={max_uploads})")

    for reading in readings:
        sign = reading.get("sign", "unknown")
        t0   = time.time()
        log.info(f"Short [{sign}] starting…")

        frame_path = str(shorts_dir / f"frame_{sign.lower()}.png")
        audio_path = str(shorts_dir / f"audio_{sign.lower()}.mp3")
        video_path = str(shorts_dir / f"short_{sign.lower()}_{date.isoformat()}.mp4")
        seed       = (date.toordinal() * 13 + abs(hash(sign))) % 99999

        # 1. Vertical frame
        try:
            _render_short_frame(reading, frame_path, date, seed)
        except Exception as e:
            log.warning(f"Short frame failed ({sign}): {e}")
            results.append({"sign": sign, "ok": False, "error": str(e)})
            continue

        # 2. TTS audio (sign-specific excerpt)
        audio_path, duration = _tts_for_sign(
            reading.get("script_text", ""), audio_path, sign
        )

        # 3. Combine into vertical mp4
        if not _build_short_video(frame_path, audio_path, video_path, duration):
            results.append({"sign": sign, "ok": False, "error": "video build failed"})
            continue

        # 4. Upload (quota-gated)
        url = ""
        if upload:
            if uploads_done < max_uploads:
                url = _upload_short(video_path, reading, date, parent_url)
                if url:
                    uploads_done += 1
                    time.sleep(5)          # brief pause between uploads
            else:
                log.info(f"Short [{sign}]: quota cap reached ({max_uploads}), not uploaded")

        elapsed = f"{time.time() - t0:.1f}s"
        results.append({
            "sign":       sign,
            "video_path": video_path,
            "url":        url,
            "ok":         True,
            "elapsed":    elapsed,
        })
        log.info(
            f"Short [{sign}] done in {elapsed}"
            + (f" → {url}" if url else " (local only)")
        )

    uploaded = sum(1 for r in results if r.get("url"))
    generated = sum(1 for r in results if r.get("ok"))
    log.info(f"Shorts complete: {generated}/{len(readings)} generated, {uploaded} uploaded")
    return results
