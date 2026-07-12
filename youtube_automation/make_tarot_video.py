#!/usr/bin/env python3
"""
make_tarot_video.py
Renders the weekly tarot-reading video from a tarotweekly_YYYYMMDD.json
(generate_tarot_assets.py). Vertical 1080x1920 long-form (~7-8 min,
monetizable): intro card → 12 sign cards → outro.

Each sign card shows the REAL public-domain Rider–Waite–Smith card image
(tarot_deck.card_image — drawn fallback if the scan is unavailable) framed
in gold at the center, with the sign header above, the card's name below,
and the spoken reading as word-synced burned captions in the lower third.
Narration length drives each card's dwell (same natural-length pattern as
the topic video). Ken Burns motion tier with static fallback.

Usage:
  python3 make_tarot_video.py tarotweekly_20260713.json
  python3 make_tarot_video.py 20260713
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

import make_daily_video as mdv
import make_topic_video as mtv   # _narrate / _pad_to / motion+captions assembly
import tarot_deck

W, H = mdv.WIDTH, mdv.HEIGHT
GOLD, WHITE, SILVER = mdv.GOLD, mdv.WHITE, mdv.SILVER
PAD = 64

ACCENT = (190, 140, 255)          # tarot purple (matches the topic category)
BG_TOP, BG_BOT = (14, 6, 24), (34, 16, 52)


def render_intro_card(data: dict) -> Image.Image:
    img = mdv._cosmic_bg(W, H, BG_TOP, BG_BOT, ACCENT, seed=5)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    cf = mdv._ui_font(36, 700)
    chip = "WEEKLY TAROT"
    cw = mdv._tw(chip, cf)
    cx = (W - cw - 48) // 2
    d.rounded_rectangle([cx, 280, cx + cw + 48, 280 + mdv._th(cf) + 24], radius=16,
                        fill=(*ACCENT, 60), outline=(*ACCENT, 235), width=2)
    d.text((cx + 24, 290), chip, font=cf, fill=WHITE)

    tf = mdv._display_font(96, weight=700)
    for i, ln in enumerate(["YOUR SIGN'S", "CARD THIS WEEK"]):
        w_ = mdv._tw(ln, tf)
        d.text(((W - w_) // 2 + 3, 430 + i * (mdv._th(tf) + 14) + 3), ln,
               font=tf, fill=(0, 0, 0, 170))
        d.text(((W - w_) // 2, 430 + i * (mdv._th(tf) + 14)), ln, font=tf, fill=GOLD)

    # a fan of three face-down card backs as the visual motif
    card_w, card_h = 260, 440
    for i, (dx, rot) in enumerate([(-240, -12), (0, 0), (240, 12)]):
        card = Image.new("RGBA", (card_w, card_h), (26, 12, 44, 255))
        cd = ImageDraw.Draw(card)
        cd.rectangle([6, 6, card_w - 6, card_h - 6], outline=(212, 175, 55), width=5)
        star, sf_ = mdv._icon("✵", "*", 120)
        sw = mdv._tw(star, sf_)
        cd.text(((card_w - sw) // 2, card_h // 2 - 80), star, font=sf_,
                fill=(*ACCENT, 220))
        card = card.rotate(rot, expand=True, resample=Image.BICUBIC)
        img.paste(card, (W // 2 - card.width // 2 + dx, 800), card)

    sf = mdv._ui_font(46, 500)
    sub = data.get("date", "")
    sw_ = mdv._tw(sub, sf)
    d.text(((W - sw_) // 2, 1420), sub, font=sf, fill=(225, 220, 245))
    chf = mdv._ui_font(46, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 120), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


def render_sign_card(sign: str, card: dict, idx: int) -> Image.Image:
    """Sign header + the real card image (gold-framed, centered) + card name,
    with a caption scrim at the bottom for the burned reading."""
    img = mdv._cosmic_bg(W, H, BG_TOP, BG_BOT, ACCENT, seed=500 + idx)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=GOLD); d.rectangle([0, H - 6, W, H], fill=GOLD)

    # progress pill
    nf = mdv._ui_font(34, 700)
    tag = f"{idx} / 12"
    tw_ = mdv._tw(tag, nf)
    px0 = W - PAD - tw_ - 36
    d.rounded_rectangle([px0, 64, px0 + tw_ + 36, 64 + mdv._th(nf) + 20],
                        radius=14, fill=(*ACCENT, 50), outline=(*ACCENT, 200), width=2)
    d.text((px0 + 18, 74), tag, font=nf, fill=WHITE)

    # sign header (zodiac glyph badge + name), same style family as the
    # horoscope cards
    glyph = {"aries": "♈", "taurus": "♉", "gemini": "♊", "cancer": "♋",
             "leo": "♌", "virgo": "♍", "libra": "♎", "scorpio": "♏",
             "sagittarius": "♐", "capricorn": "♑", "aquarius": "♒",
             "pisces": "♓"}.get(sign, "✦")
    gsym, gfont = mdv._icon(glyph, "*", 64)
    hf = mdv._display_font(88, weight=700)
    name = sign.upper()
    name_w = mdv._tw(name, hf)
    total_w = 80 + 24 + name_w
    x0 = (W - total_w) // 2
    d.ellipse([x0, 78, x0 + 80, 158], fill=(0, 0, 0, 130), outline=(*ACCENT, 255), width=4)
    gw = mdv._tw(gsym, gfont)
    d.text((x0 + 40 - gw // 2, 118 - mdv._th(gfont) // 2 - 6), gsym, font=gfont, fill=ACCENT)
    d.text((x0 + 104 + 2, 82), name, font=hf, fill=(0, 0, 0, 170))
    d.text((x0 + 104, 80), name, font=hf, fill=GOLD)

    # the card itself — real RWS scan (or drawn fallback), gold-framed.
    # RWS cards are ~600x1050; fit into a 640x1060 box centered.
    art = tarot_deck.card_image(card)
    box_w, box_h = 640, 1060
    aw, ah = art.size
    scale = min(box_w / aw, box_h / ah)
    art = art.resize((int(aw * scale), int(ah * scale)), Image.LANCZOS)
    ax = (W - art.width) // 2
    ay = 250
    d.rectangle([ax - 12, ay - 12, ax + art.width + 12, ay + art.height + 12],
                fill=(10, 6, 18), outline=GOLD, width=6)
    img.paste(art, (ax, ay))
    d = ImageDraw.Draw(img)

    # card name + numeral beneath the art
    cnf = mdv._display_font(64, weight=700)
    label = f"{card['numeral']} · {card['name']}"
    lw = mdv._tw(label, cnf)
    ly = ay + art.height + 34
    d.text(((W - lw) // 2 + 2, ly + 2), label, font=cnf, fill=(0, 0, 0, 170))
    d.text(((W - lw) // 2, ly), label, font=cnf, fill=GOLD)

    # caption scrim (lower third)
    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    top = H - 420
    steps = 48
    for i in range(steps):
        a = int(150 * (i / steps))
        yy = top + int(i * (420 / steps))
        sd.rectangle([0, yy, W, yy + (420 // steps) + 1], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img.convert("RGBA"), scrim)
    d = ImageDraw.Draw(img)

    ff = mdv._ui_font(36, 500)
    foot = f"{mdv.CHANNEL_TAG}  •  Weekly Tarot"
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 78), foot, font=ff, fill=(*SILVER, 200))
    return img.convert("RGB")


def render_outro_card(data: dict) -> Image.Image:
    img = mdv._cosmic_bg(W, H, BG_TOP, BG_BOT, ACCENT, seed=999)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)
    tf = mdv._display_font(96, weight=700)
    for i, ln in enumerate(["SAME TIME", "NEXT WEEK"]):
        w_ = mdv._tw(ln, tf)
        d.text(((W - w_) // 2, 420 + i * (mdv._th(tf) + 12)), ln, font=tf, fill=GOLD)
    df = mdv._ui_font(40, 500)
    dy = 720
    for ln in mdv._wrap(data.get("disclaimer", ""), df, W - 2 * PAD)[:3]:
        w_ = mdv._tw(ln, df)
        d.text(((W - w_) // 2, dy), ln, font=df, fill=(225, 220, 245))
        dy += mdv._th(df) + 8
    cf = mdv._ui_font(58, 700)
    cta = "SUBSCRIBE"
    cw = mdv._tw(cta, cf)
    cx = (W - cw - 80) // 2
    d.rounded_rectangle([cx, dy + 60, cx + cw + 80, dy + 60 + mdv._th(cf) + 44],
                        radius=22, fill=(220, 40, 40))
    d.text((cx + 40, dy + 82), cta, font=cf, fill=WHITE)
    chf = mdv._ui_font(46, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 130), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


def render_thumbnail(data: dict, out_path: str) -> None:
    TW, TH = 1280, 720
    img = mdv._cosmic_bg(TW, TH, BG_TOP, BG_BOT, ACCENT, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TW, 6], fill=GOLD); d.rectangle([0, TH - 6, TW, TH], fill=GOLD)
    # three mini card-backs on the right
    for i, dx in enumerate((0, 90, 180)):
        x0 = TW - 380 + dx
        d.rounded_rectangle([x0, 160 + i * 10, x0 + 170, 560 - i * 10], radius=12,
                            fill=(26, 12, 44), outline=GOLD, width=4)
    tf = mdv._display_font(92, weight=700)
    y = 150
    for ln in mdv._wrap("YOUR SIGN'S TAROT CARD THIS WEEK", tf, TW - 480)[:4]:
        d.text((60 + 3, y + 3), ln, font=tf, fill=(0, 0, 0, 190))
        d.text((60, y), ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 10
    chf = mdv._ui_font(40, 600)
    d.text((60, TH - 70), mdv.CHANNEL_TAG, font=chf, fill=SILVER)
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
    spread = tarot_deck.draw_weekly(date_tag)   # deterministic — matches assets

    mdv.CONTENT_TYPE = "tarotweekly"
    out_dir = Path("outputs") / date_tag / "TarotAll"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"tarotweekly_{date_tag}"
    video_path = str(out_dir / f"{base}.mp4")
    thumb_path = str(out_dir / f"{base}_thumbnail.jpg")

    voice = mdv._day_voice(date_tag)
    print(f"\n{'='*58}\n  WEEKLY TAROT — {data.get('week','')} ({data.get('date','')})\n"
          f"  12 signs | voice {voice}\n{'='*58}\n")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pngs, durs, clips = [], [], []
        chapters = ["0:00 Intro"]
        caption_cues = []
        t_cursor = 0.0

        print("[1/4] Rendering cards + narration...")

        def add(card_img, text, label, is_chapter=False):
            nonlocal t_cursor
            nat = str(tmp / f"nat_{len(pngs):02d}.wav")
            dur, word_cues = mtv._narrate(text, nat, voice, tmp)
            card_dur = math.ceil(dur) + 0.4
            clip = str(tmp / f"clip_{len(pngs):02d}.wav")
            mtv._pad_to(nat, clip, card_dur)
            png = str(tmp / f"card_{len(pngs):02d}.png")
            card_img.save(png, "PNG")
            pngs.append(png); durs.append(card_dur); clips.append(clip)
            for c0, c1, ct in mdv._group_words_into_cues(word_cues, max_words=3):
                caption_cues.append((t_cursor + c0, t_cursor + c1, ct))
            if is_chapter:
                chapters.append(f"{_ts(t_cursor)} {label}")
            t_cursor += card_dur
            print(f"      [{label[:34]:<34}] {card_dur:.1f}s")

        add(render_intro_card(data), data.get("intro", "Welcome to this week's tarot."),
            "Intro")
        readings = data.get("readings", {})
        for i, sign in enumerate(tarot_deck.SIGNS, 1):
            card = spread[sign]
            add(render_sign_card(sign, card, i), readings.get(sign, ""),
                f"{sign.title()} — {card['name']}", is_chapter=True)
        add(render_outro_card(data), data.get("outro", "See you next week."), "Outro")

        total = sum(durs)
        mdv.VIDEO_FPS = mdv.safe_static_fps(total)
        print(f"      Total: {total:.0f}s ({int(total//60)}m {int(total%60)}s)  |  {mdv.VIDEO_FPS}fps")

        srt_path = str(tmp / "captions.srt")
        has_captions = mdv._write_srt(caption_cues, srt_path)
        print(f"      Captions: {len(caption_cues)} cues" if has_captions
              else "      [WARN] No word timing — captions skipped")

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

        motion_on = os.getenv("TOPIC_MOTION_ENABLED", "true").lower() == "true"
        ok = False
        if motion_on:
            m_fps = mtv.motion_fps_for(total)
            print(f"\n[3/4] Assembling {W}x{H} — Ken Burns motion @ {m_fps}fps...")
            ok = mtv.assemble_video_motion_captions(
                pngs, durs, audio, srt_path if has_captions else None, video_path, m_fps)
            if not ok:
                print("      Motion tier failed — falling back to static + captions")
        if not ok:
            print(f"\n[3/4] Assembling {W}x{H} @ {mdv.VIDEO_FPS}fps (static)...")
            ok = mdv.assemble_video(pngs, durs, audio, video_path,
                                    srt_path=srt_path if has_captions else None)
        if not ok:
            ok = mdv.assemble_video(pngs, durs, audio, video_path)
        if not ok:
            print("[ERROR] Assembly failed (all tiers)", file=sys.stderr); sys.exit(1)
        size_mb = os.path.getsize(video_path) / 1_048_576
        print(f"      OK — {size_mb:.1f} MB")

    print("\n[4/4] Thumbnail + metadata...")
    render_thumbnail(data, thumb_path)
    chapters_block = "\n".join(chapters)
    desc = data.get("description", "")
    if "0:00" not in desc:
        desc = f"{desc}\n\n⏱ Find your sign:\n{chapters_block}"
    meta = {
        "title":       data.get("title", f"Weekly Tarot Reading — {data.get('date','')}")[:100],
        "description": desc,
        "tags":        data.get("tags", []),
        "hashtags":    data.get("hashtags", []),
        "date":        date_tag,
        "content_type": "tarotweekly",
        "pinned_comment": (data.get("pinned_comment", "") +
                           f"\n\n⏱ Jump to your sign:\n{chapters_block}"),
    }
    (out_dir / f"{base}_assets.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Done → {video_path}")
    return video_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 make_tarot_video.py tarotweekly_YYYYMMDD.json | YYYYMMDD")
        sys.exit(1)
    arg = sys.argv[1]
    json_path = arg if arg.endswith(".json") else f"tarotweekly_{arg}.json"
    process(json_path)


if __name__ == "__main__":
    main()
