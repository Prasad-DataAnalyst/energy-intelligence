#!/usr/bin/env python3
"""
make_prediction_video.py
Renders a SHORT (~90s) LANDSCAPE (1920x1080) astrology-prediction video from a
prediction_{category}_{date}.json produced by generate_prediction_assets.py.

Fixes the "empty video" problem: each card is backed by a real stock photo
(stock_images.py) cover-fit + darkened, with a strong heading, a category
chip, a data element (confidence meter / subject label), and word-synced
burned captions. Gentle Ken Burns zoom for a real-video feel, static fallback.

Structure (all ~90s total): intro → 3 beats → verdict → outro.
Reuses fonts / audio / caption grouping / narration from make_daily_video +
make_topic_video; landscape layout and assembly are defined here.

Usage:
  python3 make_prediction_video.py prediction_sports_20260710.json
  python3 make_prediction_video.py sports 20260710
"""
import json
import math
import os
import subprocess as _sp
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

import make_daily_video as mdv
import make_topic_video as mtv      # _narrate / _pad_to
import stock_images

PW, PH = 1920, 1080                 # landscape
PAD = 90
GOLD, WHITE, SILVER = mdv.GOLD, mdv.WHITE, mdv.SILVER

# accent + gradient tone per category (fallback background when no photo)
CAT = {
    "sports":    ((90, 220, 130), (4, 16, 8), (10, 38, 20)),
    "crypto":    ((120, 220, 255), (4, 14, 20), (10, 32, 42)),
    "political": ((210, 210, 220), (10, 10, 16), (26, 26, 36)),
    "celebrity": ((255, 190, 220), (20, 8, 16), (46, 14, 32)),
}
DEFAULT = ((255, 215, 0), (16, 12, 2), (40, 30, 6))

# MODERN-SMOOTH MOTION. These videos are SHORT (~75-90s), so a high frame
# rate is affordable here in a way it never is for the 5-8 minute horoscopes:
# 90s x 24fps = 2160 frames, still under the daily Short's proven 2610.
# 12fps Ken Burns is the single biggest "dated slideshow" tell — a slow zoom
# sampled 12 times a second visibly stutters, while 24fps reads as film.
# (Budget/timeout raised together: the timeout must cover a CPU-credit
# throttled encode of the larger frame count — see make_daily_video's
# caption_fps_for note on the 2026-07-19 burst-credit incident.)
# TIMEOUT INVARIANT: _MOTION_TIMEOUT + _STATIC_TIMEOUT must stay under
# run_daily's per-attempt prediction timeout (2700s), with margin for the
# non-encode work (TTS, audio mix, thumbnail). assemble_landscape tries the
# motion tier and THEN the static tier, so their budgets ADD; if the sum
# exceeds the outer budget, the outer process-group kill lands first and the
# static fallback never runs — turning a graceful degradation into a lost
# video. 1500 + 900 = 2400 < 2700 leaves 300s of headroom.
# The static tier gets the smaller budget because it is genuinely cheaper:
# ~900 base-fps frames of plain concat (no zoompan), ~9 min even on a
# credit-throttled vCPU.
_MOTION_FRAME_BUDGET = 2300
_MOTION_MAX_FPS = 24
_MOTION_TIMEOUT = 1500
_STATIC_TIMEOUT = 900


def _accent(cat):
    return CAT.get((cat or "").lower(), DEFAULT)


# ── image helpers ─────────────────────────────────────────────────────────────
def _cover_fit(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize + center-crop so `img` fills exactly w×h (CSS background: cover)."""
    img = img.convert("RGB")
    iw, ih = img.size
    scale = max(w / iw, h / ih)
    nw, nh = int(iw * scale + 0.5), int(ih * scale + 0.5)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - w) // 2, (nh - h) // 2
    return img.crop((left, top, left + w, top + h))


def _photo_bg(query, fallback_query, accent, top, bot, seed,
              path=None) -> Image.Image:
    """A darkened full-bleed stock photo for the card background, or the cosmic
    gradient if no image is available (missing key / offline / no result).
    Never raises — the video always renders. Pass `path` to reuse an already-
    fetched photo (multi-image beats fetch once, render twice)."""
    if path is None:
        try:
            path = stock_images.fetch_first(query, fallback_query)
        except Exception as e:
            print(f"[WARN] stock image lookup failed: {e}", file=sys.stderr)
    if not path:
        return mdv._cosmic_bg(PW, PH, top, bot, accent, seed=seed)
    try:
        photo = _cover_fit(Image.open(path), PW, PH)
    except Exception as e:
        print(f"[WARN] stock image open failed ({path}): {e}", file=sys.stderr)
        return mdv._cosmic_bg(PW, PH, top, bot, accent, seed=seed)
    # Darken for text legibility: a global dim + a stronger bottom-third scrim
    # (captions live there). Done in PIL so it composites with the photo, not
    # flatly stamped by the subtitle renderer.
    dim = Image.new("RGB", (PW, PH), (0, 0, 0))
    photo = Image.blend(photo, dim, 0.38)
    scrim = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    band_top = PH - 340
    steps = 48
    for i in range(steps):
        a = int(165 * (i / steps))
        y = band_top + int(i * (340 / steps))
        sd.rectangle([0, y, PW, y + (340 // steps) + 1], fill=(0, 0, 0, a))
    return Image.alpha_composite(photo.convert("RGBA"), scrim).convert("RGB")


def _chip(d, text, x, y, accent, font=None):
    f = font or mdv._ui_font(38, 700)
    tw = mdv._tw(text, f)
    d.rounded_rectangle([x, y, x + tw + 52, y + mdv._th(f) + 24], radius=16,
                        fill=(*accent, 60), outline=(*accent, 230), width=2)
    d.text((x + 26, y + 12), text, font=f, fill=WHITE)
    return x + tw + 52


def _framelines(d, accent):
    d.rectangle([0, 0, PW, 7], fill=GOLD)
    d.rectangle([0, PH - 7, PW, PH], fill=GOLD)


def _footer(d, extra=""):
    ff = mdv._ui_font(34, 500)
    foot = f"{mdv.CHANNEL_TAG}{('  •  ' + extra) if extra else ''}"
    d.text((PAD, PH - 58), foot, font=ff, fill=(*SILVER, 210))


# ── card renderers (1920x1080) ────────────────────────────────────────────────
def render_intro(data) -> Image.Image:
    cat = data.get("category", "")
    accent, top, bot = _accent(cat)
    img = _photo_bg(data.get("default_image", cat), cat, accent, top, bot, seed=11)
    d = ImageDraw.Draw(img)
    _framelines(d, accent)
    _chip(d, cat.upper() + " ASTROLOGY", PAD, 90, accent)

    tf = mdv._display_font(120, weight=700)
    lines = mdv._wrap(data.get("subject_label", "Today's Prediction"), tf, PW - 2 * PAD)[:2]
    y = 300
    for ln in lines:
        d.text((PAD + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 180))
        d.text((PAD, y), ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 8

    hf = mdv._ui_font(50, 500)
    for ln in mdv._wrap(data.get("hook", ""), hf, PW - 2 * PAD)[:2]:
        d.text((PAD, y + 24), ln, font=hf, fill=(230, 226, 245))
        y += mdv._th(hf) + 8
    _footer(d, "Astrology Prediction")
    return img.convert("RGB")


def render_beat(beat, idx, total, cat, photo_path=None,
                transparent=False) -> Image.Image:
    """The beat card. transparent=True returns CHROME ONLY on an RGBA
    canvas (framelines, chip, heading, caption scrim, footer — no photo):
    the Tier-3 video-background path overlays this onto a moving stock clip
    in ffmpeg, so the chrome must not carry its own background."""
    accent, top, bot = _accent(cat)
    if transparent:
        img = Image.new("RGBA", (PW, PH), (0, 0, 0, 0))
        # caption scrim baked into the chrome (the video bg is only eq-dimmed)
        sd = ImageDraw.Draw(img)
        band_top = PH - 340
        steps = 48
        for i in range(steps):
            a = int(165 * (i / steps))
            y_ = band_top + int(i * (340 / steps))
            sd.rectangle([0, y_, PW, y_ + (340 // steps) + 1], fill=(0, 0, 0, a))
    else:
        img = _photo_bg(beat.get("image_query", cat), beat.get("image_fallback", cat),
                        accent, top, bot, seed=40 + idx, path=photo_path)
    d = ImageDraw.Draw(img)
    _framelines(d, accent)

    # progress chip, top-right
    nf = mdv._ui_font(34, 700)
    tag = f"{idx} / {total}"
    tw = mdv._tw(tag, nf)
    d.rounded_rectangle([PW - PAD - tw - 44, 90, PW - PAD, 90 + mdv._th(nf) + 22],
                        radius=14, fill=(*accent, 70), outline=(*accent, 230), width=2)
    d.text((PW - PAD - tw - 22, 101), tag, font=nf, fill=WHITE)

    # heading, top-left (stays up the whole beat). 84 -> 108: at 1920 wide,
    # 84px reads small on a phone (where most of this is watched) and is the
    # other half of the "dated slideshow" look. Modern short-form runs its
    # on-screen heading 5-6% of frame height; the drop shadow is deepened to
    # match, so big type stays legible over a busy photo background.
    hf = mdv._display_font(108, weight=700)
    y = 88
    for ln in mdv._wrap(beat.get("heading", ""), hf, PW - 2 * PAD - 220)[:2]:
        d.text((PAD + 5, y + 5), ln, font=hf, fill=(0, 0, 0, 200))
        d.text((PAD, y), ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 4
    # Clearance is derived from the FONT SIZE, not _th(): _th measures the
    # rendered string's bbox (79px for a 108px font, and smaller still for
    # text with no descenders), so a fixed +12 put this bar on the glyph
    # baseline — it read as a strikethrough across the heading rather than
    # an underline beneath it. 0.22em of clearance clears descenders too.
    d.rectangle([PAD, y + 24, PAD + 360, y + 33], fill=(*accent, 235))
    _footer(d, "Astrology Prediction")
    return img if transparent else img.convert("RGB")


def render_verdict(data) -> Image.Image:
    cat = data.get("category", "")
    accent, top, bot = _accent(cat)
    img = _photo_bg(data.get("default_image", cat), cat, accent, top, bot, seed=77)
    d = ImageDraw.Draw(img)
    _framelines(d, accent)

    v = data.get("verdict", {})
    # The payoff card — the frame most likely to be screenshotted/shared, so
    # it carries the largest type in the video.
    lbl = mdv._ui_font(46, 700)
    d.text((PAD, 112), "THE VERDICT", font=lbl, fill=(*accent, 255))

    hf = mdv._display_font(126, weight=700)
    y = 186
    for ln in mdv._wrap(str(v.get("headline", "")), hf, PW - 2 * PAD)[:2]:
        d.text((PAD + 5, y + 5), ln, font=hf, fill=(0, 0, 0, 200))
        d.text((PAD, y), ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 6

    # confidence meter. Clearance sized for the 126px headline's descenders
    # (_th under-measures — same trap as the beat card's underline): at +30
    # the meter bar cut through the 'g' of a headline like "Slight Edge".
    conf = int(v.get("confidence_pct", 60))
    y += 64
    mw, mh = PW - 2 * PAD - 360, 54
    d.rounded_rectangle([PAD, y, PAD + mw, y + mh], radius=mh // 2,
                        outline=(*accent, 255), width=3)
    fill_w = int(mw * max(0, min(100, conf)) / 100)
    if fill_w > 10:
        d.rounded_rectangle([PAD, y, PAD + fill_w, y + mh], radius=mh // 2, fill=(*accent, 200))
    cf = mdv._ui_font(46, 700)
    d.text((PAD + mw + 30, y + 2), f"{conf}%", font=cf, fill=WHITE)
    sub = mdv._ui_font(34, 500)
    d.text((PAD, y + mh + 16), "astrology confidence", font=sub, fill=(*SILVER, 220))
    _footer(d, "Entertainment only")
    return img.convert("RGB")


def render_outro(data) -> Image.Image:
    cat = data.get("category", "")
    accent, top, bot = _accent(cat)
    img = mdv._cosmic_bg(PW, PH, top, bot, accent, seed=99)
    d = ImageDraw.Draw(img)
    _framelines(d, accent)

    tf = mdv._display_font(104, weight=700)
    t = "THANKS FOR WATCHING"
    for i, ln in enumerate(mdv._wrap(t, tf, PW - 2 * PAD)[:2]):
        w = mdv._tw(ln, tf)
        d.text(((PW - w) // 2, 220 + i * (mdv._th(tf) + 8)), ln, font=tf, fill=GOLD)

    # disclaimer
    df = mdv._ui_font(40, 500)
    dy = 500
    for ln in mdv._wrap(data.get("disclaimer", ""), df, PW - 2 * PAD)[:2]:
        w = mdv._tw(ln, df)
        d.text(((PW - w) // 2, dy), ln, font=df, fill=(225, 220, 245))
        dy += mdv._th(df) + 8

    cf = mdv._ui_font(58, 700)
    cta = "SUBSCRIBE"
    cw = mdv._tw(cta, cf)
    cx = (PW - cw - 80) // 2
    d.rounded_rectangle([cx, dy + 40, cx + cw + 80, dy + 40 + mdv._th(cf) + 44],
                        radius=22, fill=(220, 40, 40))
    d.text((cx + 40, dy + 62), cta, font=cf, fill=WHITE)
    chf = mdv._ui_font(46, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((PW - cw2) // 2, PH - 96), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


def render_thumbnail(data, out_path: str) -> None:
    cat = data.get("category", "")
    accent, top, bot = _accent(cat)
    TW, TH = 1280, 720
    base = _photo_bg(data.get("default_image", cat), cat, accent, top, bot, seed=7)
    img = base.resize((TW, TH), Image.LANCZOS)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TW, 6], fill=GOLD); d.rectangle([0, TH - 6, TW, TH], fill=GOLD)
    tf = mdv._display_font(104, weight=700)
    lines = mdv._wrap(str(data.get("verdict", {}).get("headline", data.get("subject_label", "Astrology"))), tf, TW - 90)[:3]
    y = (TH - len(lines) * (mdv._th(tf) + 10)) // 2 - 30
    for ln in lines:
        w = mdv._tw(ln, tf)
        d.text(((TW - w) // 2 + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 190))
        d.text(((TW - w) // 2, y), ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 10
    chip_f = mdv._ui_font(40, 700)
    _chip(d, cat.upper(), 40, 40, accent, font=chip_f)
    img.convert("RGB").save(out_path, "JPEG", quality=92)
    print(f"[INFO] Thumbnail → {out_path}")


# ── landscape assembly ────────────────────────────────────────────────────────
def _subtitle_filter_landscape(srt_path: str) -> str:
    """Caption burn for 1920x1080. Fontsize/MarginV are EMPIRICALLY calibrated
    for this frame (libass scales SRT subs by a fixed internal factor, see the
    note in make_daily_video._subtitle_filter) — tuned by test-render, do not
    'correct' to look like literal pixels."""
    esc = str(srt_path).replace("\\", "\\\\").replace(":", "\\:")
    fontsdir = str(Path(__file__).parent / "assets" / "fonts").replace(":", "\\:")
    # 22 -> 27 with a heavier outline: burned captions are the most-read text
    # in the video and were undersized for phone viewing. Verified by test
    # render (this is the empirical scale the note above refers to — changing
    # it without a test render is what that warning is about, not a ban).
    style = ("FontName=Poppins,Fontsize=27,PrimaryColour=&H00FFFFFF,"
             "OutlineColour=&H00000000,BorderStyle=1,Outline=4,Shadow=0,"
             "Bold=1,Alignment=2,MarginV=54")
    return f"subtitles={esc}:fontsdir={fontsdir}:force_style='{style}'"


def assemble_landscape(pngs, durs, audio_path, srt_path, out_path, fps, motion=True) -> bool:
    """Ken Burns zoom per card (landscape) → concat → optional caption-fps
    upsample → burn subtitles → mux. Falls back to a plain static concat on
    any failure. Mirrors the proven make_topic_video motion assembly (no '-t'
    on inputs; trim=end_frame hard-caps each segment) but at 1920x1080."""
    tmp_video = out_path.replace(".mp4", "_v.mp4")
    n = len(pngs)
    if motion:
        inputs = []
        for p in pngs:
            inputs += ["-loop", "1", "-i", p]
        parts = []
        for i, dur in enumerate(durs):
            frames = max(1, int(round(dur * fps)))
            # direction alternates per card (zoom in/out, pan L/R) — see
            # mdv.kenburns_expr; identical motion on every card reads as a
            # screensaver, alternating reads as editing.
            parts.append(
                f"[{i}:v]scale=w=iw*1.2:h=ih*1.2,"
                f"{mdv.kenburns_expr(i, frames)}:"
                f"s={PW}x{PH}:fps={fps},"
                f"trim=end_frame={frames},setpts=PTS-STARTPTS[v{i}]"
            )
        concat_in = "".join(f"[v{i}]" for i in range(n))
        filt = ";".join(parts) + f";{concat_in}concat=n={n}:v=1:a=0[vcat]"
        out_label = "[vcat]"
        if mdv.CINEMATIC_GRADE:
            filt += f";{out_label}{mdv.grade_filter()}[vgrade]"
            out_label = "[vgrade]"
        if srt_path and Path(srt_path).exists():
            if fps < mdv.CAPTION_FPS:
                filt += f";{out_label}fps={mdv.CAPTION_FPS}[vup]"
                out_label = "[vup]"
            filt += f";{out_label}{_subtitle_filter_landscape(srt_path)}[vout]"
            out_label = "[vout]"
        cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs,
               "-filter_complex", filt, "-map", out_label,
               "-pix_fmt", "yuv420p", "-c:v", "libx264", "-preset", "ultrafast",
               "-crf", "23", "-threads", "0", "-movflags", "+faststart", tmp_video]
        try:
            r = _sp.run(cmd, capture_output=True, timeout=_MOTION_TIMEOUT)
            if r.returncode == 0:
                return mdv._mux_audio(tmp_video, audio_path, out_path)
            print(f"[WARN] Landscape motion failed: {r.stderr.decode()[-300:]}", file=sys.stderr)
        except Exception as e:
            print(f"[WARN] Landscape motion exception: {e}", file=sys.stderr)
        finally:
            Path(tmp_video).unlink(missing_ok=True)

    # static fallback
    concat_txt = out_path.replace(".mp4", "_concat.txt")
    with open(concat_txt, "w") as f:
        for p, dur in zip(pngs, durs):
            f.write(f"file '{p}'\nduration {dur}\n")
        f.write(f"file '{pngs[-1]}'\n")
    vf = f"fps={fps},scale={PW}:{PH}:force_original_aspect_ratio=disable"
    if mdv.CINEMATIC_GRADE:
        vf += "," + mdv.grade_filter()
    if srt_path and Path(srt_path).exists():
        if fps < mdv.CAPTION_FPS:
            vf += f",fps={mdv.CAPTION_FPS}"
        vf += "," + _subtitle_filter_landscape(srt_path)
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
           "-i", concat_txt, "-vf", vf, "-pix_fmt", "yuv420p",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
           "-threads", "0", "-movflags", "+faststart", tmp_video]
    try:
        r = _sp.run(cmd, capture_output=True, timeout=_STATIC_TIMEOUT)
        if r.returncode != 0:
            print(f"[ERROR] Landscape static failed: {r.stderr.decode()[-300:]}", file=sys.stderr)
            return False
        return mdv._mux_audio(tmp_video, audio_path, out_path)
    except Exception as e:
        print(f"[ERROR] Landscape static exception: {e}", file=sys.stderr)
        return False
    finally:
        Path(tmp_video).unlink(missing_ok=True)
        Path(concat_txt).unlink(missing_ok=True)


def assemble_landscape_segments(segments, audio_path, srt_path, out_path, fps) -> bool:
    """Tier-3 experimental path (PREDICTION_VIDEO_BG=true): pre-render each
    card to its own small mp4 segment — beats with a fetched stock VIDEO clip
    get the moving clip (looped, cover-cropped, dimmed) with the transparent
    chrome overlaid; every other card gets its usual Ken Burns still — then
    concat the segments, grade, burn captions, mux audio. Any failure returns
    False so the caller falls back to the proven still-image path.

    segments: [{"type": "video", "clip": path, "chrome": png, "dur": s} |
               {"type": "png", "path": png, "dur": s}]"""
    seg_files = []
    try:
        for i, seg in enumerate(segments):
            seg_mp4 = out_path.replace(".mp4", f"_seg{i:02d}.mp4")
            frames = max(1, int(round(seg["dur"] * fps)))
            if seg["type"] == "video":
                cmd = ["ffmpeg", "-y", "-loglevel", "error",
                       "-stream_loop", "-1", "-i", str(seg["clip"]),
                       "-loop", "1", "-i", str(seg["chrome"]),
                       "-filter_complex",
                       (f"[0:v]scale={PW}:{PH}:force_original_aspect_ratio=increase,"
                        f"crop={PW}:{PH},fps={fps},eq=brightness=-0.14:saturation=1.03[bg];"
                        f"[bg][1:v]overlay=0:0[v]"),
                       "-map", "[v]", "-frames:v", str(frames), "-r", str(fps),
                       "-an", "-c:v", "libx264", "-preset", "ultrafast",
                       "-crf", "23", "-pix_fmt", "yuv420p", seg_mp4]
            else:
                cmd = ["ffmpeg", "-y", "-loglevel", "error",
                       "-loop", "1", "-i", str(seg["path"]),
                       "-vf",
                       (f"scale=w=iw*1.2:h=ih*1.2,"
                        f"{mdv.kenburns_expr(i, frames)}:s={PW}x{PH}:fps={fps},"
                        f"trim=end_frame={frames},setpts=PTS-STARTPTS"),
                       "-frames:v", str(frames),
                       "-an", "-c:v", "libx264", "-preset", "ultrafast",
                       "-crf", "23", "-pix_fmt", "yuv420p", seg_mp4]
            r = _sp.run(cmd, capture_output=True, timeout=600)
            if r.returncode != 0:
                print(f"[WARN] segment {i} pre-render failed: "
                      f"{r.stderr.decode()[-200:]}", file=sys.stderr)
                return False
            seg_files.append(seg_mp4)

        concat_txt = out_path.replace(".mp4", "_segs.txt")
        with open(concat_txt, "w") as f:
            for p in seg_files:
                # ABSOLUTE paths: the concat demuxer resolves relative entries
                # against the list file's own directory, so a relative
                # "outputs/..." entry inside "outputs/.../x_segs.txt" doubles
                # the prefix and fails to open (hit in testing).
                f.write(f"file '{Path(p).resolve()}'\n")
        vf = f"fps={fps}"
        if mdv.CINEMATIC_GRADE:
            vf += "," + mdv.grade_filter()
        if srt_path and Path(srt_path).exists():
            if fps < mdv.CAPTION_FPS:
                vf += f",fps={mdv.CAPTION_FPS}"
            vf += "," + _subtitle_filter_landscape(srt_path)
        tmp_video = out_path.replace(".mp4", "_v.mp4")
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
               "-i", concat_txt, "-vf", vf, "-pix_fmt", "yuv420p",
               "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23",
               "-threads", "0", "-movflags", "+faststart", tmp_video]
        r = _sp.run(cmd, capture_output=True, timeout=_STATIC_TIMEOUT)
        if r.returncode != 0:
            print(f"[WARN] segment concat failed: {r.stderr.decode()[-200:]}",
                  file=sys.stderr)
            return False
        ok = mdv._mux_audio(tmp_video, audio_path, out_path)
        Path(tmp_video).unlink(missing_ok=True)
        return ok
    except Exception as e:
        print(f"[WARN] segment assembly exception: {e}", file=sys.stderr)
        return False
    finally:
        for p in seg_files:
            Path(p).unlink(missing_ok=True)
        Path(out_path.replace(".mp4", "_segs.txt")).unlink(missing_ok=True)


def _ts(secs: float) -> str:
    s = int(secs)
    return f"{s // 60}:{s % 60:02d}"


MAX_SECS = 92   # hard-ish target; we warn if narration pushes past this


def process(json_path: str) -> str:
    path = Path(json_path)
    if not path.exists():
        print(f"[ERROR] File not found: {json_path}", file=sys.stderr)
        sys.exit(1)
    data = json.loads(path.read_text(encoding="utf-8"))
    category = data.get("category", "prediction")
    # date tag = trailing YYYYMMDD of prediction_<cat>_<date>.json
    date_tag = path.stem.split("_")[-1]
    beats = data.get("beats", [])[:3]

    mdv.CONTENT_TYPE = "prediction"
    out_dir = Path("outputs") / date_tag / f"Prediction_{category}"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"prediction_{category}_{date_tag}"
    video_path = str(out_dir / f"{base}.mp4")
    thumb_path = str(out_dir / f"{base}_thumbnail.jpg")

    voice = mdv._day_voice(date_tag)
    print(f"\n{'='*58}\n  PREDICTION ({category}) — {data.get('subject_label','')[:38]}\n"
          f"  {len(beats)} beats | landscape {PW}x{PH} | voice {voice}\n{'='*58}\n")

    video_bg_on = os.getenv("PREDICTION_VIDEO_BG", "false").lower() == "true"

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pngs, durs, clips = [], [], []
        segments = []      # Tier-3 spec list, parallel in time to pngs/durs
        caption_cues = []
        t_cursor = 0.0

        print("[1/4] Rendering cards + narration...")

        def add(card_imgs, text, label, video_clip=None, chrome_img=None):
            """card_imgs: one PIL image or a LIST of visual variants. One
            narration spans them all; the dwell splits evenly, so a 2-image
            beat cuts to its second photo mid-narration — reads as an edit.
            Audio stays a single clip (concat alignment is by total time).
            video_clip + chrome_img (Tier-3): this card's segment plays the
            stock VIDEO clip with the transparent chrome overlaid; the still
            card_imgs remain the fallback for the proven image path."""
            nonlocal t_cursor
            if not isinstance(card_imgs, (list, tuple)):
                card_imgs = [card_imgs]
            nat = str(tmp / f"nat_{len(pngs):02d}.wav")
            dur, word_cues = mtv._narrate(text, nat, voice, tmp)
            card_dur = math.ceil(dur) + 0.35
            clip = str(tmp / f"clip_{len(pngs):02d}.wav")
            mtv._pad_to(nat, clip, card_dur)
            clips.append(clip)
            share = card_dur / len(card_imgs)
            first_png = None
            for img_ in card_imgs:
                png = str(tmp / f"card_{len(pngs):02d}.png")
                img_.save(png, "PNG")
                first_png = first_png or png
                pngs.append(png); durs.append(share)
            if video_clip and chrome_img is not None:
                chrome_png = str(tmp / f"chrome_{len(pngs):02d}.png")
                chrome_img.save(chrome_png, "PNG")
                segments.append({"type": "video", "clip": str(video_clip),
                                 "chrome": chrome_png, "dur": card_dur})
            else:
                # still segments mirror pngs/durs one-to-one
                start = len(pngs) - len(card_imgs)
                for k in range(len(card_imgs)):
                    segments.append({"type": "png", "path": pngs[start + k],
                                     "dur": share})
            # group THIS segment's words before offsetting (no cross-card bleed)
            for c0, c1, wlist in mdv._group_words_into_word_cues(word_cues, max_words=3):
                caption_cues.append((t_cursor + c0, t_cursor + c1,
                                     [(t_cursor + ws, t_cursor + we, w)
                                      for ws, we, w in wlist]))
            t_cursor += card_dur
            print(f"      [{label[:34]:<34}] {card_dur:.1f}s"
                  + (" (video bg)" if video_clip else
                     (f" ({len(card_imgs)} shots)" if len(card_imgs) > 1 else "")))

        add(render_intro(data), data.get("hook", data.get("subject_label", "")), "Intro")
        for i, b in enumerate(beats, 1):
            # Tier 3 (flag-gated): a real moving stock clip behind the beat.
            vclip = None
            if video_bg_on:
                try:
                    vclip = stock_images.fetch_video(b.get("image_query", category),
                                                     b.get("image_fallback", category))
                except Exception as e:
                    print(f"[WARN] video bg fetch failed: {e}", file=sys.stderr)
            # Two distinct photos per beat when available — the mid-beat cut
            # is what makes it feel edited rather than a slideshow. (Also the
            # fallback visuals when the video path is on but fails later.)
            try:
                shots = stock_images.fetch_images(b.get("image_query", category), 2)
                if len(shots) < 2:
                    shots += stock_images.fetch_images(
                        b.get("image_fallback", category), 2 - len(shots))
            except Exception:
                shots = []
            if len(shots) >= 2:
                imgs = [render_beat(b, i, len(beats), category, photo_path=p)
                        for p in shots[:2]]
            else:
                imgs = render_beat(b, i, len(beats), category)
            chrome = (render_beat(b, i, len(beats), category, transparent=True)
                      if vclip else None)
            add(imgs, b.get("narration", ""), b.get("heading", f"Beat {i}"),
                video_clip=vclip, chrome_img=chrome)
        v = data.get("verdict", {})
        add(render_verdict(data),
            f"{v.get('headline','')}. {v.get('detail','')}", "Verdict")
        add(render_outro(data), data.get("outro", "Subscribe for more."), "Outro")

        # Compliance disclaimer end-card (channel policy — 2s, silent, on
        # EVERY published video; shared renderer in make_daily_video).
        disc_png = str(tmp / "card_disclaimer.png")
        mdv.render_disclaimer_card(PW, PH).save(disc_png, "PNG")
        disc_wav = str(tmp / "clip_disclaimer.wav")
        mdv._generate_silence(mdv.DISCLAIMER_SECS, disc_wav)
        pngs.append(disc_png); durs.append(mdv.DISCLAIMER_SECS); clips.append(disc_wav)
        t_cursor += mdv.DISCLAIMER_SECS
        print(f"      [{'Disclaimer':<34}] {mdv.DISCLAIMER_SECS:.1f}s")

        total = sum(durs)
        if total > MAX_SECS:
            print(f"      [WARN] Total {total:.0f}s exceeds {MAX_SECS}s target — "
                  f"script came back long; consider trimming beats.")
        mdv.VIDEO_FPS = mdv.safe_static_fps(total)
        print(f"      Total: {total:.0f}s  |  static-fps {mdv.VIDEO_FPS}")
        print(f"      Imagery: {stock_images.usage_summary()}")

        srt_path, has_captions = mdv._write_captions(caption_cues, str(tmp / "captions"),
                                                     frame_w=PW, frame_h=PH)
        print(f"      Captions: {len(caption_cues)} cues" if has_captions
              else "      [WARN] No word timing — captions skipped")

        # Zero cues = every TTS voice failed and all beats fell back to 3s of
        # silence (a ~20s mute stub, seen 2026-07-14). Abort loudly instead of
        # wasting the render on something QC must reject.
        if not caption_cues:
            print("[ERROR] TTS produced no narration for ANY beat (all voices "
                  "failed) — aborting instead of rendering a silent stub.",
                  file=sys.stderr)
            sys.exit(1)

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

        motion_on = os.getenv("PREDICTION_MOTION_ENABLED", "true").lower() == "true"
        m_fps = mdv.safe_static_fps(total, frame_budget=_MOTION_FRAME_BUDGET,
                                    min_fps=3, max_fps=_MOTION_MAX_FPS)
        ok = False
        has_video_segs = any(s["type"] == "video" for s in segments)
        if video_bg_on and has_video_segs:
            print(f"\n[3/4] Assembling {PW}x{PH} @ {m_fps}fps (VIDEO backgrounds)...")
            ok = assemble_landscape_segments(segments, audio,
                                             srt_path if has_captions else None,
                                             video_path, m_fps)
            if not ok:
                print("      Video-bg tier failed — falling back to stills")
        if not ok:
            print(f"\n[3/4] Assembling {PW}x{PH} @ {m_fps if motion_on else mdv.VIDEO_FPS}fps"
                  f" ({'motion' if motion_on else 'static'})...")
            ok = assemble_landscape(pngs, durs, audio,
                                    srt_path if has_captions else None,
                                    video_path, m_fps if motion_on else mdv.VIDEO_FPS,
                                    motion=motion_on)
        if not ok:
            print("[ERROR] Assembly failed", file=sys.stderr); sys.exit(1)
        size_mb = os.path.getsize(video_path) / 1_048_576
        print(f"      OK — {size_mb:.1f} MB")

    print("\n[4/4] Thumbnail + metadata...")
    render_thumbnail(data, thumb_path)
    # The category disclaimer is already in the description (hardcoded by
    # generate_prediction_assets); the channel-wide compliance footer adds
    # the entertainment-only wording + copyright line on top.
    desc = data.get("description", "")
    if "general informational purposes" not in desc:
        desc = f"{desc}\n\n{mdv.disclaimer_block()}"
    meta = {
        "title":       data.get("title", data.get("title_en", "Astrology Prediction"))[:100],
        "description": desc,
        "tags":        data.get("tags", []),
        "hashtags":    data.get("hashtags", []),
        "date":        date_tag,
        "content_type": "prediction",
        "category":    category,
        "pinned_comment": data.get("pinned_comment", ""),
    }
    (out_dir / f"{base}_assets.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Done → {video_path}")
    return video_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 make_prediction_video.py prediction_<cat>_<YYYYMMDD>.json")
        print("   or: python3 make_prediction_video.py <category> <YYYYMMDD>")
        sys.exit(1)
    if sys.argv[1].endswith(".json"):
        process(sys.argv[1])
    else:
        cat, date_tag = sys.argv[1], sys.argv[2]
        process(f"prediction_{cat}_{date_tag}.json")


if __name__ == "__main__":
    main()
