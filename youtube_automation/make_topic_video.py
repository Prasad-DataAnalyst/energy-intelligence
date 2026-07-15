#!/usr/bin/env python3
"""
make_topic_video.py
Renders the astrology TOPIC video (title → sections → outro) from a
topic_YYYYMMDD.json produced by generate_topic_assets.py.

LANDSCAPE 1920x1080 regular video (channel policy: only the daily horoscope
is vertical/Shorts — every other type, including this one, is landscape so
it's never auto-classed as a Short). Narrated, ambient music, chapters;
capped under 3 minutes by generate_topic_assets.py's word budget. Reuses
the audio + assembly + fonts from make_daily_video.

Usage:
  python3 make_topic_video.py topic_20260706.json
  python3 make_topic_video.py 20260706
"""
import json
import math
import os
import subprocess as _sp
import sys
import tempfile
import asyncio
from pathlib import Path

from PIL import Image, ImageDraw
from dotenv import load_dotenv

import make_daily_video as mdv   # reuse fonts, cosmic bg, audio, assembly

load_dotenv()

W, H = mdv.LS_W, mdv.LS_H   # landscape — see module docstring
GOLD, WHITE, SILVER = mdv.GOLD, mdv.WHITE, mdv.SILVER
PAD = 90
CW  = W - 2 * PAD

# Accent color + background tone per category.
CAT_COLOR = {
    "lunar":        ((180, 200, 255), (4, 8, 24), (12, 18, 48)),
    "planets":      ((255, 170,  90), (18, 10, 4), (44, 22, 8)),
    "signs":        ((255, 215,   0), (16, 12, 2), (40, 30, 6)),
    "compatibility":((255, 120, 170), (20, 6, 14), (46, 12, 30)),
    "love":         ((255, 110, 140), (22, 6, 12), (48, 12, 26)),
    "money":        (( 90, 220, 130), (4, 16, 8), (10, 38, 20)),
    "business":     (( 90, 200, 255), (4, 12, 22), (10, 28, 48)),
    "career":       (( 90, 200, 255), (4, 12, 22), (10, 28, 48)),
    "health":       ((120, 230, 160), (4, 16, 10), (10, 36, 22)),
    "houses":       ((200, 160, 255), (12, 6, 22), (30, 14, 48)),
    "aspects":      ((160, 200, 255), (8, 10, 22), (18, 24, 46)),
    "concepts":     ((180, 220, 255), (8, 10, 20), (18, 24, 44)),
    "deep":         ((200, 150, 255), (12, 6, 22), (30, 14, 48)),
    "engagement":   ((255, 210,  80), (16, 12, 2), (40, 30, 6)),
    "political":    ((210, 210, 220), (10, 10, 16), (26, 26, 36)),
    "stockmarket":  ((100, 230, 150), (4, 18, 10), (10, 40, 22)),
    "crypto":       ((120, 220, 255), (4, 14, 20), (10, 32, 42)),
    "celebrity":    ((255, 190, 220), (20, 8, 16), (46, 14, 32)),
    "numerology":   ((255, 200, 100), (16, 10, 2), (40, 26, 6)),
    "tarot":        ((190, 140, 255), (14, 6, 24), (34, 16, 52)),
    "manifestation":((150, 235, 210), (4, 16, 14), (10, 38, 32)),
}
DEFAULT_ACCENT = ((200, 170, 255), (8, 6, 20), (18, 14, 44))

# One evocative symbol per category, used as a large faint watermark motif so
# every section frame has real visual content beyond text (each validated at
# render time via mdv._icon — never shows as a tofu box).
CAT_GLYPH = {
    "lunar": "☽", "planets": "✦", "signs": "✵", "compatibility": "♡",
    "love": "♥", "money": "$", "business": "▲", "career": "▲",
    "health": "✚", "houses": "⌂", "aspects": "✧", "concepts": "◈",
    "deep": "☄", "engagement": "★",
    "political": "⚖", "stockmarket": "%", "crypto": "◆", "celebrity": "♛",
    "numerology": "7", "tarot": "♠", "manifestation": "∞",
}


def _accent(cat: str):
    return CAT_COLOR.get((cat or "").lower(), DEFAULT_ACCENT)


# ── Narration → natural-length audio clip, card dwell = its duration ──────────
# _tts_stream_with_words / _group_words_into_cues / _srt_timestamp / _write_srt
# now live in make_daily_video.py (mdv) as shared utilities — daily/weekly/
# monthly/weeklyfull need the same word-boundary capture + SRT writing this
# module pioneered, so they were promoted rather than duplicated.

def _narrate(text: str, out_wav: str, voice: str, tmp: Path) -> tuple:
    """TTS at natural pace, normalize to WAV. Returns (duration_secs,
    word_cues) where word_cues is a list of (start, end, word) in LOCAL time
    (0 = this card's audio start). Card dwells exactly as long as the audio,
    so video and narration always stay in sync."""
    raw = str(tmp / (Path(out_wav).stem + "_raw.mp3"))
    # _tts_synth = shared voice-fallback synth (a retired voice fails on EVERY
    # call, so recovery is a different voice, remembered for the whole run —
    # see make_daily_video). Never raises.
    ok, words, _used = mdv._tts_synth(text, raw, voice)
    if not ok:
        mdv._generate_silence(3.0, out_wav)
        return 3.0, []
    # areverse/silenceremove/areverse trims TRAILING dead air (edge-tts pads
    # the clip end), keeping 0.15s as a natural breath — tightens pacing on
    # every card without touching the attack of the first word.
    r = _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                 "-af", ("areverse,silenceremove=start_periods=1:"
                         "start_threshold=-45dB:start_silence=0.15,areverse"),
                 "-ar", mdv._AR, "-ac", mdv._AC, "-c:a", "pcm_s16le", out_wav],
                capture_output=True, timeout=60)
    Path(raw).unlink(missing_ok=True)
    if r.returncode != 0:
        mdv._generate_silence(3.0, out_wav)
        return 3.0, []
    return max(1.0, mdv._audio_dur(out_wav)), words


def _pad_to(in_wav: str, out_wav: str, dur: float) -> None:
    _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", in_wav,
             "-af", f"apad,atrim=end={dur}", "-ar", mdv._AR, "-ac", mdv._AC,
             "-c:a", "pcm_s16le", out_wav], capture_output=True, timeout=60)


# ── Cards (1920x1080 landscape) ────────────────────────────────────────────────
def render_title_card(data: dict) -> Image.Image:
    accent, top, bot = _accent(data.get("category"))
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=11)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    # Category chip
    cf = mdv._ui_font(32, 700)
    chip = (data.get("category", "astrology")).upper()
    cw = mdv._tw(chip, cf)
    cx = (W - cw - 44) // 2
    d.rounded_rectangle([cx, 90, cx + cw + 44, 90 + mdv._th(cf) + 22], radius=16,
                        fill=(*accent, 60), outline=(*accent, 235), width=2)
    d.text((cx + 22, 98), chip, font=cf, fill=WHITE)

    # Title (Cinzel, wrapped, centered) — the short landscape frame only has
    # room for ~3 lines before crowding the hook/footer, vs 4 in the old
    # vertical layout.
    tf = mdv._display_font(76, weight=700)
    lines = mdv._wrap(data.get("topic_title", data.get("title", "Astrology")), tf, CW)[:3]
    y = 230
    for ln in lines:
        w = mdv._tw(ln, tf)
        d.text(((W - w) // 2 + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 170))
        d.text(((W - w) // 2,     y),     ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 14

    d.rectangle([PAD, y + 16, W - PAD, y + 20], fill=(*accent, 220))

    # Hook line
    hf = mdv._ui_font(42, 500)
    for ln in mdv._wrap(data.get("hook", ""), hf, CW)[:2]:
        w = mdv._tw(ln, hf)
        d.text(((W - w) // 2, y + 50), ln, font=hf, fill=(225, 220, 245))
        y += mdv._th(hf) + 8

    # Date + channel
    df = mdv._ui_font(40, 500)
    dw = mdv._tw(data.get("date", ""), df)
    d.text(((W - dw) // 2, H - 150), data.get("date", ""), font=df, fill=SILVER)
    chf = mdv._ui_font(42, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 84), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


# Real photo backgrounds on section cards (the "images" half of the quality
# push). Off gracefully: no key / no result / disabled -> the cosmic+glyph
# look stays. Fallback query per category when Claude didn't supply one.
TOPIC_PHOTOS_ENABLED = os.getenv("TOPIC_PHOTOS_ENABLED", "true").lower() == "true"
_CAT_PHOTO_QUERY = {
    "lunar": "full moon night sky", "planets": "planets space",
    "signs": "zodiac constellation", "compatibility": "couple silhouette sunset",
    "love": "couple holding hands", "money": "money abundance",
    "business": "city skyline night", "career": "city skyline night",
    "stockmarket": "wall street", "crypto": "cryptocurrency bitcoin",
    "political": "american flag", "celebrity": "red carpet lights",
    "numerology": "glowing numbers abstract", "tarot": "tarot cards candle",
    "manifestation": "sunrise meditation", "concepts": "starry night sky",
    "deep": "galaxy nebula", "engagement": "night sky stars",
    "wellness": "calm meditation nature", "health": "calm meditation nature",
    "houses": "night sky stars", "aspects": "night sky stars",
}


def _section_photo(sec: dict, cat: str):
    """Best-effort darkened vertical photo for a section card, or None."""
    if not TOPIC_PHOTOS_ENABLED:
        return None
    try:
        import stock_images
        query = sec.get("image_query") or _CAT_PHOTO_QUERY.get((cat or "").lower(), "")
        fallback = sec.get("image_fallback") or _CAT_PHOTO_QUERY.get(
            (cat or "").lower(), "night sky")
        path = stock_images.fetch_first(query or fallback, fallback)
        if not path:
            return None
        photo = Image.open(path).convert("RGB")
        iw, ih = photo.size
        scale = max(W / iw, H / ih)
        photo = photo.resize((int(iw * scale + 0.5), int(ih * scale + 0.5)),
                             Image.LANCZOS)
        left, top_ = (photo.width - W) // 2, (photo.height - H) // 2
        photo = photo.crop((left, top_, left + W, top_ + H))
        # dim for legibility (headings + captions sit on top)
        return Image.blend(photo, Image.new("RGB", (W, H), (0, 0, 0)), 0.42)
    except Exception as e:
        print(f"[WARN] section photo lookup failed ({e})", file=sys.stderr)
        return None


def render_section_card(sec: dict, idx: int, total: int, cat: str) -> Image.Image:
    """Cinematic section background — NOT a slide. No bullet list: the actual
    words are delivered as burned, word-synced captions layered on top of this
    at assembly time (see _group_words_into_cues / assemble). This PNG only
    provides the backdrop: real darkened stock photo when available (cosmic
    gradient + category motif otherwise), heading, progress, and a caption
    safe-zone scrim so the captions stay legible over any art underneath."""
    accent, top, bot = _accent(cat)
    photo = _section_photo(sec, cat)
    img = photo or mdv._cosmic_bg(W, H, top, bot, accent, seed=100 + idx)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=GOLD); d.rectangle([0, H - 6, W, H], fill=GOLD)

    # Large faint category glyph — the visual anchor of a NON-photo frame
    # (on a real photo it just muddies the image, so it's skipped there).
    # Sized/positioned for the short 1080px-tall landscape frame (was tuned
    # for a 1920-tall vertical canvas — that size/offset would mostly hide
    # behind the caption scrim here).
    if photo is None:
        glyph, gfont = mdv._icon(CAT_GLYPH.get((cat or "").lower(), "✦"), "*", 460)
        gw = mdv._tw(glyph, gfont)
        ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ov).text(((W - gw) // 2, H // 2 - 260), glyph, font=gfont,
                                fill=(*accent, 30))
        img = Image.alpha_composite(img.convert("RGBA"), ov)
        d = ImageDraw.Draw(img)

    # Progress pill, top-right — subtle, not a slide-deck page number.
    nf = mdv._ui_font(34, 700)
    tag = f"{idx} / {total}"
    tw_ = mdv._tw(tag, nf)
    px0 = W - PAD - tw_ - 36
    d.rounded_rectangle([px0, 64, px0 + tw_ + 36, 64 + mdv._th(nf) + 20],
                        radius=14, fill=(*accent, 50), outline=(*accent, 200), width=2)
    d.text((px0 + 18, 74), tag, font=nf, fill=WHITE)

    # Heading (Cinzel, wrapped, top-left) — stays on screen the whole section.
    hf = mdv._display_font(68, weight=700)
    y = 90
    for ln in mdv._wrap(sec.get("heading", ""), hf, CW - 220)[:2]:
        d.text((PAD + 2, y + 2), ln, font=hf, fill=(0, 0, 0, 170))
        d.text((PAD,     y),     ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 10
    d.rectangle([PAD, y + 12, PAD + 300, y + 17], fill=(*accent, 220))

    # Caption safe-zone scrim: a soft dark gradient over the lower band so
    # burned captions read cleanly against any background art. Drawn now (in
    # PIL) rather than left to the subtitle renderer, so it composites with
    # the nebula/stars underneath instead of sitting flatly on top. Height
    # matched to make_prediction_video's landscape scrim (proven legible).
    SCRIM_H = 340
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    scrim_top = H - SCRIM_H
    steps = 48
    for i in range(steps):
        a = int(160 * (i / steps))
        yy = scrim_top + int(i * (SCRIM_H / steps))
        sd.rectangle([0, yy, W, yy + (SCRIM_H // steps) + 1], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img.convert("RGBA"), scrim)
    d = ImageDraw.Draw(img)

    # Footer
    ff = mdv._ui_font(34, 500)
    foot = f"{mdv.CHANNEL_TAG}  •  Astrology Explained"
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 56), foot, font=ff, fill=(*SILVER, 200))
    return img.convert("RGB")


# ── Motion assembly: Ken Burns zoom per card + burned captions ────────────────
# Higher per-frame cost than a static hold (zoompan does real crop/scale work
# every frame, vs. a straight copy), so this is attempted with a SMALLER frame
# budget than the proven-safe static ceiling, and a bounded timeout. On any
# failure (timeout, ffmpeg error, missing filter) the caller falls back to the
# plain static+captions path, which is the one already proven reliable.
_MOTION_FRAME_BUDGET = 1200
_MOTION_TIMEOUT_SECS = 1500   # 25 min cap — leaves room to still fall back and finish


def motion_fps_for(total_secs: float) -> int:
    return mdv.safe_static_fps(total_secs, frame_budget=_MOTION_FRAME_BUDGET,
                               min_fps=2, max_fps=10)


def assemble_video_motion_captions(png_files: list, durations: list,
                                   audio_path, srt_path, out_path: str,
                                   fps: int) -> bool:
    tmp_video = out_path.replace(".mp4", "_motion.mp4")
    n = len(png_files)
    # CRITICAL: do NOT put "-t dur" on these inputs. zoompan treats each
    # incoming frame as the start of a new d-frame zoom cycle — an input that
    # already delivers dur-seconds-worth of frames (at the image2 demuxer's
    # default ~25fps) makes zoompan run ~25x more cycles than intended
    # (measured: a 10s two-card test produced a 1250s file — exactly the
    # 125x = 25fps*5x multiplication this caused). "-loop 1 -i png" alone
    # feeds a single repeating frame; zoompan's own d=/fps= fully control
    # the output length, and an explicit trim below hard-caps it regardless.
    inputs = []
    for png in png_files:
        inputs += ["-loop", "1", "-i", png]

    parts = []
    for i, dur in enumerate(durations):
        frames = max(1, int(round(dur * fps)))
        # Upscale 1.2x first so the zoom/pan crop never exceeds the source
        # canvas; trim+setpts hard-caps each segment to exactly `frames`
        # frames and resets timestamps to 0, which is what makes concat's
        # duration math correct regardless of zoompan's internal accounting.
        # Motion DIRECTION alternates per card (zoom in / zoom out / pan
        # right / pan left) — identical motion on every card reads as a
        # screensaver; alternating reads as editing.
        parts.append(
            f"[{i}:v]scale=w=iw*1.2:h=ih*1.2,"
            f"{mdv.kenburns_expr(i, frames)}:"
            f"s={W}x{H}:fps={fps},"
            f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(n))
    filt = ";".join(parts) + f";{concat_in}concat=n={n}:v=1:a=0[vcat]"
    out_label = "[vcat]"
    if mdv.CINEMATIC_GRADE:
        # grade at the low motion fps (cheap), before captions (crisp text)
        filt += f";{out_label}{mdv.grade_filter()}[vgrade]"
        out_label = "[vgrade]"
    if srt_path and Path(srt_path).exists():
        # Same fix as the static path: the motion fps (often 2-4fps to keep
        # zoompan's per-frame cost bounded) is far too sparse to reliably
        # catch short caption cues. Upsample via cheap frame duplication
        # BEFORE burning subtitles — the expensive zoompan work above is
        # unaffected, still done at the low `fps`.
        if fps < mdv.CAPTION_FPS:
            filt += f";{out_label}fps={mdv.CAPTION_FPS}[vcatup]"
            out_label = "[vcatup]"
        filt += f";{out_label}{mdv._subtitle_filter(srt_path)}[vout]"
        out_label = "[vout]"

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        *inputs, "-filter_complex", filt, "-map", out_label,
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "23", "-threads", "0", "-movflags", "+faststart", tmp_video,
    ]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=_MOTION_TIMEOUT_SECS)
        if r.returncode != 0:
            print(f"[WARN] Motion assembly failed: {r.stderr.decode()[-300:]}",
                  file=sys.stderr)
            return False
        return mdv._mux_audio(tmp_video, audio_path, out_path)
    except Exception as e:
        print(f"[WARN] Motion assembly exception: {e}", file=sys.stderr)
        return False
    finally:
        Path(tmp_video).unlink(missing_ok=True)


def render_outro_card(data: dict) -> Image.Image:
    accent, top, bot = _accent(data.get("category"))
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=999)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    tf = mdv._display_font(84, weight=700)
    for i, ln in enumerate(["THANKS FOR", "WATCHING"]):
        w = mdv._tw(ln, tf)
        d.text(((W - w) // 2, 130 + i * (mdv._th(tf) + 10)), ln, font=tf, fill=GOLD)

    sf = mdv._ui_font(46, 500)
    for i, ln in enumerate(["A new astrology topic", "every single day."]):
        w = mdv._tw(ln, sf)
        d.text(((W - w) // 2, 420 + i * (mdv._th(sf) + 8)), ln, font=sf, fill=(225, 220, 245))

    # Subscribe pill
    cf = mdv._ui_font(54, 700)
    cta = "SUBSCRIBE"
    cw = mdv._tw(cta, cf)
    cx = (W - cw - 80) // 2
    d.rounded_rectangle([cx, 620, cx + cw + 80, 620 + mdv._th(cf) + 40],
                        radius=22, fill=(220, 40, 40))
    d.text((cx + 40, 640), cta, font=cf, fill=WHITE)

    chf = mdv._ui_font(44, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 84), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


# ── Thumbnail (1280x720) ──────────────────────────────────────────────────────
def _thumb_photo_bg(data: dict, TW: int, TH: int):
    """Best-effort real-photo thumbnail background (thumbnails drive clicks
    more than anything else on the watch page). Searches the topic title's
    key words, falls back to the category, and returns None on any failure
    so the cosmic gradient remains the safety net."""
    try:
        import stock_images
        from PIL import Image as _Im
        title = data.get("topic_title", "")
        # strip filler words so the query is the topic's substance
        stop = {"the", "a", "an", "of", "for", "your", "every", "what", "why",
                "how", "and", "in", "on", "to", "it", "means", "explained",
                "today", "tonight", "sign", "signs", "zodiac"}
        words = [w.strip(":,.!?'\"").lower() for w in title.split()]
        query = " ".join([w for w in words if w and w not in stop][:3])
        path = stock_images.fetch_first(query or data.get("category", "astrology"),
                                        data.get("category", "astrology"))
        if not path:
            return None
        photo = _Im.open(path).convert("RGB")
        iw, ih = photo.size
        scale = max(TW / iw, TH / ih)
        photo = photo.resize((int(iw * scale + 0.5), int(ih * scale + 0.5)), _Im.LANCZOS)
        left, top_ = (photo.width - TW) // 2, (photo.height - TH) // 2
        photo = photo.crop((left, top_, left + TW, top_ + TH))
        return _Im.blend(photo, _Im.new("RGB", (TW, TH), (0, 0, 0)), 0.45)
    except Exception as e:
        print(f"[WARN] thumbnail photo lookup failed ({e}) — cosmic bg", file=sys.stderr)
        return None


def render_thumbnail(data: dict, out_path: str) -> None:
    accent, top, bot = _accent(data.get("category"))
    TW, TH = 1280, 720
    img = _thumb_photo_bg(data, TW, TH) or mdv._cosmic_bg(TW, TH, top, bot, accent, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TW, 6], fill=GOLD); d.rectangle([0, TH - 6, TW, TH], fill=GOLD)
    tf = mdv._display_font(96, weight=700)
    lines = mdv._wrap(data.get("topic_title", data.get("title", "Astrology")), tf, TW - 100)[:4]
    y = (TH - len(lines) * (mdv._th(tf) + 12)) // 2 - 20
    for ln in lines:
        w = mdv._tw(ln, tf)
        d.text(((TW - w) // 2 + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 170))
        d.text(((TW - w) // 2,     y),     ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 12
    chf = mdv._ui_font(42, 600)
    cw = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((TW - cw) // 2, TH - 70), mdv.CHANNEL_TAG, font=chf, fill=SILVER)
    img.convert("RGB").save(out_path, "JPEG", quality=92)
    print(f"[INFO] Thumbnail → {out_path}")


def _ts(secs: float) -> str:
    s = int(secs)
    return f"{s // 60}:{s % 60:02d}"


def process(json_path: str) -> str:
    path = Path(json_path)
    if not path.exists():
        print(f"[ERROR] File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    date_tag = path.stem.split("_")[-1]
    cat = data.get("category", "astrology")
    sections = data.get("sections", [])

    mdv.CONTENT_TYPE = "topic"
    # VIDEO_FPS is set AFTER the narration loop below, once the actual total
    # duration is known — narration length varies day to day, and a fixed fps
    # chosen before that (the old 10fps default) is only safe up to a certain
    # duration (see mdv.safe_static_fps() docstring: 486s @ 10fps timed out
    # twice at 40 min on the production VM).

    out_dir = Path("outputs") / date_tag / "TopicAll"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"topic_{date_tag}"
    video_path = str(out_dir / f"{base}.mp4")
    thumb_path = str(out_dir / f"{base}_thumbnail.jpg")

    voice = mdv._day_voice(date_tag)
    print(f"\n{'='*58}\n  TOPIC VIDEO — {data.get('topic_title','')[:40]}\n"
          f"  {len(sections)} sections | voice {voice}\n{'='*58}\n")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pngs, durs, clips = [], [], []
        chapters = ["0:00 Intro"]
        t_cursor = 0.0
        caption_cues = []   # (start, end, text) in GLOBAL video-timeline seconds

        # 1) Narrate everything (natural length), render each card to its dwell
        print("[1/4] Rendering cards + narration...")

        def add(card_img, text, label, is_section=False, sec_idx=0):
            nonlocal t_cursor
            nat = str(tmp / f"nat_{len(pngs):02d}.wav")
            dur, word_cues = _narrate(text, nat, voice, tmp)
            card_dur = math.ceil(dur) + 0.4
            clip = str(tmp / f"clip_{len(pngs):02d}.wav")
            _pad_to(nat, clip, card_dur)
            png = str(tmp / f"card_{len(pngs):02d}.png")
            card_img.save(png, "PNG")
            pngs.append(png); durs.append(card_dur); clips.append(clip)
            # Group THIS card's own words into caption phrases BEFORE applying
            # the global offset — grouping must never span two cards, or a cue
            # could straddle the hard cut between them (showing the previous
            # card's last words on the next card). Word timings kept for the
            # karaoke highlight. See make_daily_video.
            for c0, c1, wlist in mdv._group_words_into_word_cues(word_cues, max_words=3):
                caption_cues.append((t_cursor + c0, t_cursor + c1,
                                     [(t_cursor + ws, t_cursor + we, w)
                                      for ws, we, w in wlist]))
            if is_section:
                chapters.append(f"{_ts(t_cursor)} {label}")
            t_cursor += card_dur
            print(f"      [{label[:34]:<34}] {card_dur:.1f}s")

        add(render_title_card(data), data.get("hook", data.get("topic_title", "")), "Intro")
        for i, sec in enumerate(sections, 1):
            add(render_section_card(sec, i, len(sections), cat),
                sec.get("narration", ""), sec.get("heading", f"Part {i}")[:40],
                is_section=True, sec_idx=i)
        add(render_outro_card(data), data.get("outro", "Subscribe for more."), "Outro")

        total = sum(durs)
        mdv.VIDEO_FPS = mdv.safe_static_fps(total)
        print(f"      Total: {total:.0f}s ({int(total//60)}m {int(total%60)}s)  |  {mdv.VIDEO_FPS}fps")

        # Write the caption file spanning the whole assembled timeline (each
        # card's cues already carry their global offset): karaoke .ass by
        # default, .srt fallback. Empty if word timing wasn't available
        # anywhere (TTS fallback silence) — captions are then simply skipped,
        # never a hard failure.
        srt_path, has_captions = mdv._write_captions(caption_cues, str(tmp / "captions"))
        print(f"      Captions: {len(caption_cues)} cues" if has_captions
              else "      [WARN] No word-timing captured — captions skipped")

        # Zero cues means EVERY narration failed (all TTS voices dead) and each
        # card fell back to its 3s-silence dwell — the result is a ~20s mute
        # stub. 2026-07-14 rendered exactly that; QC caught it, but the render
        # was wasted. Abort loudly instead.
        if not caption_cues:
            print("[ERROR] TTS produced no narration for ANY card (all voices "
                  "failed) — aborting instead of rendering a silent stub.",
                  file=sys.stderr)
            sys.exit(1)

        # 2) Concatenate voice, generate ambient, mix
        print("\n[2/4] Building audio track...")
        voice_all = str(tmp / "voice_all.wav")
        voice_ok = mdv._concat_audio(clips, voice_all)
        amb = str(tmp / "amb.wav")
        amb_ok = mdv._generate_ambient(total, amb)
        if voice_ok and amb_ok:
            mixed = str(tmp / "audio.m4a")
            audio = mixed if mdv._mix_voice_ambient(voice_all, amb, mixed) else voice_all
        else:
            audio = voice_all if voice_ok else (amb if amb_ok else None)
        print("      OK")

        # 3) Assemble — tiered: Ken Burns motion+captions -> static+captions ->
        # plain static. Each tier falls back on ANY failure (timeout, ffmpeg
        # error), so a slow VM degrades gracefully instead of failing the day.
        motion_on = os.getenv("TOPIC_MOTION_ENABLED", "true").lower() == "true"
        ok = False
        if motion_on:
            m_fps = motion_fps_for(total)
            print(f"\n[3/4] Assembling {W}x{H} — trying Ken Burns motion @ {m_fps}fps "
                  f"(cap {_MOTION_TIMEOUT_SECS}s)...")
            ok = assemble_video_motion_captions(
                pngs, durs, audio, srt_path if has_captions else None, video_path, m_fps)
            if not ok:
                print("      Motion tier failed — falling back to static + captions")
        if not ok:
            print(f"\n[3/4] Assembling {W}x{H} @ {mdv.VIDEO_FPS}fps (static)...")
            ok = mdv.assemble_video(pngs, durs, audio, video_path,
                                    srt_path=srt_path if has_captions else None,
                                    frame=(W, H))
        if not ok:
            print("      Captioned static tier failed — falling back to plain static")
            ok = mdv.assemble_video(pngs, durs, audio, video_path, frame=(W, H))
        if not ok:
            print("[ERROR] Assembly failed (all tiers)", file=sys.stderr); sys.exit(1)
        size_mb = os.path.getsize(video_path) / 1_048_576
        print(f"      OK — {size_mb:.1f} MB")

    # 4) Thumbnail + metadata
    print("\n[4/4] Thumbnail + metadata...")
    render_thumbnail(data, thumb_path)
    chapters_block = "\n".join(chapters)
    desc = data.get("description", "")
    if "0:00" not in desc:
        desc = f"{desc}\n\n⏱ Chapters:\n{chapters_block}"
    meta = {
        "title":       data.get("title", data.get("topic_title", "Astrology"))[:100],
        "description": desc,
        "tags":        data.get("tags", []),
        "hashtags":    data.get("hashtags", []),
        "date":        date_tag,
        "content_type": "topic",
        "pinned_comment": (data.get("pinned_comment", "") + f"\n\n⏱ Chapters:\n{chapters_block}"),
    }
    (out_dir / f"{base}_assets.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Done → {video_path}")
    return video_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 make_topic_video.py topic_YYYYMMDD.json | YYYYMMDD")
        sys.exit(1)
    arg = sys.argv[1]
    process(arg if arg.endswith(".json") else f"topic_{arg}.json")


if __name__ == "__main__":
    main()
