#!/usr/bin/env python3
"""
make_topic_video.py
Renders a long-form astrology TOPIC video (title → sections → outro) from a
topic_YYYYMMDD.json produced by generate_topic_assets.py.

Vertical 1080x1920, narrated, ambient music, chapters. Duration is driven by
the narration length of each section (~7-9 min → monetizable / mid-roll).
Reuses the audio + assembly + fonts from make_daily_video.

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

W, H = mdv.WIDTH, mdv.HEIGHT
GOLD, WHITE, SILVER = mdv.GOLD, mdv.WHITE, mdv.SILVER
PAD = 64
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
}
DEFAULT_ACCENT = ((200, 170, 255), (8, 6, 20), (18, 14, 44))


def _accent(cat: str):
    return CAT_COLOR.get((cat or "").lower(), DEFAULT_ACCENT)


# ── Narration → natural-length audio clip, card dwell = its duration ──────────
def _narrate(text: str, out_wav: str, voice: str, tmp: Path) -> float:
    """TTS at natural pace, normalize to WAV, return duration (s). Card dwells
    for this long so audio + visuals stay in sync and the video auto-sizes to
    the script."""
    raw = str(tmp / (Path(out_wav).stem + "_raw.mp3"))
    ok = asyncio.run(mdv._tts_async(text, raw, voice=voice))
    if not ok:
        mdv._generate_silence(3.0, out_wav)
        return 3.0
    r = _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", raw,
                 "-ar", mdv._AR, "-ac", mdv._AC, "-c:a", "pcm_s16le", out_wav],
                capture_output=True, timeout=60)
    Path(raw).unlink(missing_ok=True)
    if r.returncode != 0:
        mdv._generate_silence(3.0, out_wav)
        return 3.0
    return max(1.0, mdv._audio_dur(out_wav))


def _pad_to(in_wav: str, out_wav: str, dur: float) -> None:
    _sp.run(["ffmpeg", "-y", "-loglevel", "error", "-i", in_wav,
             "-af", f"apad,atrim=end={dur}", "-ar", mdv._AR, "-ac", mdv._AC,
             "-c:a", "pcm_s16le", out_wav], capture_output=True, timeout=60)


# ── Cards ─────────────────────────────────────────────────────────────────────
def render_title_card(data: dict) -> Image.Image:
    accent, top, bot = _accent(data.get("category"))
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=11)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    # Category chip
    cf = mdv._ui_font(34, 700)
    chip = (data.get("category", "astrology")).upper()
    cw = mdv._tw(chip, cf)
    cx = (W - cw - 44) // 2
    d.rounded_rectangle([cx, 300, cx + cw + 44, 300 + mdv._th(cf) + 22], radius=16,
                        fill=(*accent, 60), outline=(*accent, 235), width=2)
    d.text((cx + 22, 308), chip, font=cf, fill=WHITE)

    # Title (Cinzel, wrapped, centered)
    tf = mdv._display_font(84, weight=700)
    lines = mdv._wrap(data.get("topic_title", data.get("title", "Astrology")), tf, CW)[:4]
    y = 430
    for ln in lines:
        w = mdv._tw(ln, tf)
        d.text(((W - w) // 2 + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 170))
        d.text(((W - w) // 2,     y),     ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 16

    d.rectangle([PAD, y + 20, W - PAD, y + 25], fill=(*accent, 220))

    # Hook line
    hf = mdv._ui_font(46, 500)
    for ln in mdv._wrap(data.get("hook", ""), hf, CW)[:3]:
        w = mdv._tw(ln, hf)
        d.text(((W - w) // 2, y + 60), ln, font=hf, fill=(225, 220, 245))
        y += mdv._th(hf) + 8

    # Date + channel
    df = mdv._ui_font(44, 500)
    dw = mdv._tw(data.get("date", ""), df)
    d.text(((W - dw) // 2, H - 260), data.get("date", ""), font=df, fill=SILVER)
    chf = mdv._ui_font(46, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 120), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


def render_section_card(sec: dict, idx: int, total: int, cat: str) -> Image.Image:
    accent, top, bot = _accent(cat)
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=100 + idx)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=GOLD); d.rectangle([0, H - 6, W, H], fill=GOLD)

    # Section number
    nf = mdv._ui_font(38, 700)
    tag = f"{idx} / {total}"
    d.text((PAD, 70), tag, font=nf, fill=(*accent, 235))

    # Heading (Cinzel wrapped)
    hf = mdv._display_font(74, weight=700)
    y = 140
    for ln in mdv._wrap(sec.get("heading", ""), hf, CW)[:3]:
        d.text((PAD + 2, y + 2), ln, font=hf, fill=(0, 0, 0, 170))
        d.text((PAD,     y),     ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 10
    d.rectangle([PAD, y + 16, PAD + 360, y + 22], fill=(*accent, 230))
    y += 70

    # Bullet panel (glass). Pre-wrap each bullet so the panel is sized to the
    # ACTUAL line count (bullets that wrap to 2 lines no longer overlap).
    bullets = [b for b in sec.get("screen", []) if str(b).strip()][:5]
    bf = mdv._ui_font(54, 500)
    lh = mdv._th(bf) + 14
    gap = 26
    wrapped = [mdv._wrap(str(b), bf, CW - 96)[:2] for b in bullets]
    total_lines = sum(len(w) for w in wrapped)
    panel_h = 48 + total_lines * lh + max(0, len(wrapped) - 1) * gap
    panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(panel).rounded_rectangle(
        [PAD - 8, y, W - PAD + 8, y + panel_h], radius=28, fill=(255, 255, 255, 16))
    img = Image.alpha_composite(img.convert("RGBA"), panel)
    d = ImageDraw.Draw(img)
    by = y + 34
    for lines_ in wrapped:
        d.ellipse([PAD + 14, by + lh // 2 - 9, PAD + 32, by + lh // 2 + 9],
                  fill=(*accent, 255))
        for ln in lines_:
            d.text((PAD + 58, by), ln, font=bf, fill=WHITE)
            by += lh
        by += gap

    # Footer
    ff = mdv._ui_font(38, 500)
    foot = f"{mdv.CHANNEL_TAG}  •  Astrology Explained"
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 88), foot, font=ff, fill=(*SILVER, 235))
    return img.convert("RGB")


def render_outro_card(data: dict) -> Image.Image:
    accent, top, bot = _accent(data.get("category"))
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=999)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    tf = mdv._display_font(96, weight=700)
    for i, ln in enumerate(["THANKS FOR", "WATCHING"]):
        w = mdv._tw(ln, tf)
        d.text(((W - w) // 2, 420 + i * (mdv._th(tf) + 12)), ln, font=tf, fill=GOLD)

    sf = mdv._ui_font(52, 500)
    for i, ln in enumerate(["A new astrology topic", "every single day."]):
        w = mdv._tw(ln, sf)
        d.text(((W - w) // 2, 720 + i * (mdv._th(sf) + 10)), ln, font=sf, fill=(225, 220, 245))

    # Subscribe pill
    cf = mdv._ui_font(58, 700)
    cta = "SUBSCRIBE  🔔"
    cw = mdv._tw(cta, cf)
    cx = (W - cw - 80) // 2
    d.rounded_rectangle([cx, 1020, cx + cw + 80, 1020 + mdv._th(cf) + 44],
                        radius=22, fill=(220, 40, 40))
    d.text((cx + 40, 1042), cta, font=cf, fill=WHITE)

    chf = mdv._ui_font(48, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 130), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


# ── Thumbnail (1280x720) ──────────────────────────────────────────────────────
def render_thumbnail(data: dict, out_path: str) -> None:
    accent, top, bot = _accent(data.get("category"))
    TW, TH = 1280, 720
    img = mdv._cosmic_bg(TW, TH, top, bot, accent, seed=7)
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

        # 1) Narrate everything (natural length), render each card to its dwell
        print("[1/4] Rendering cards + narration...")

        def add(card_img, text, label, is_section=False, sec_idx=0):
            nonlocal t_cursor
            nat = str(tmp / f"nat_{len(pngs):02d}.wav")
            dur = _narrate(text, nat, voice, tmp)
            card_dur = math.ceil(dur) + 0.4
            clip = str(tmp / f"clip_{len(pngs):02d}.wav")
            _pad_to(nat, clip, card_dur)
            png = str(tmp / f"card_{len(pngs):02d}.png")
            card_img.save(png, "PNG")
            pngs.append(png); durs.append(card_dur); clips.append(clip)
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

        # 3) Assemble
        print(f"\n[3/4] Assembling {W}x{H} @ {mdv.VIDEO_FPS}fps...")
        if not mdv.assemble_video(pngs, durs, audio, video_path):
            print("[ERROR] Assembly failed", file=sys.stderr); sys.exit(1)
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
