#!/usr/bin/env python3
"""
make_sports_video.py
Renders the daily long-form Sports Astrology Predictions video from a
sports_YYYYMMDD.json produced by generate_sports_astrology_assets.py.

Per match: match-info card -> sports-view card -> astrology-view card ->
prediction/confidence card. One disclaimer/outro card closes the video.
Vertical 1080x1920, narrated with word-synced burned captions, ambient
music, Ken Burns motion — reuses fonts/cards/audio/assembly/captions from
make_daily_video, and the natural-length-narration + motion-captions
assembly from make_topic_video (both generic, not topic-specific).

Usage:
  python3 make_sports_video.py sports_20260709.json
  python3 make_sports_video.py 20260709
"""
import json
import math
import os
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

import make_daily_video as mdv
import make_topic_video as mtv   # reuse _narrate / _pad_to / motion+captions assembly

W, H = mdv.WIDTH, mdv.HEIGHT
GOLD, WHITE, SILVER = mdv.GOLD, mdv.WHITE, mdv.SILVER
PAD = 64
CW = W - 2 * PAD

SPORT_COLOR = {
    # legacy keys (pre-US-only sports_data) kept for old JSONs
    "football": ((90, 220, 130), (4, 16, 8), (10, 38, 20)),
    "cricket":  ((90, 200, 255), (4, 12, 22), (10, 28, 48)),
    "rugby":    ((200, 150, 255), (12, 6, 22), (30, 14, 48)),
    # current US sport keys (see sports_data.SPORT_MAP)
    "nfl":        ((255, 150, 60), (18, 10, 4), (44, 24, 8)),
    "basketball": ((255, 140, 70), (20, 10, 4), (46, 24, 8)),
    "baseball":   ((110, 200, 255), (4, 12, 22), (12, 30, 48)),
    "hockey":     ((160, 220, 255), (6, 12, 20), (14, 28, 44)),
    "soccer":     ((90, 220, 130), (4, 16, 8), (10, 38, 20)),
}
DEFAULT_ACCENT = ((255, 215, 0), (16, 12, 2), (40, 30, 6))

SPORT_GLYPH = {
    # emoji are tofu in the bundled fonts — _icon() falls back to the shapes
    "football": ("⚽", "●"), "cricket": ("🏏", "◆"), "rugby": ("🏉", "⬢"),
    "nfl": ("🏈", "▲"), "basketball": ("🏀", "●"),
    "baseball": ("⚾", "◍"), "hockey": ("🏒", "◆"), "soccer": ("⚽", "●"),
}


def _accent(sport: str):
    return SPORT_COLOR.get((sport or "").lower(), DEFAULT_ACCENT)


def render_day_intro_card(when: str, num_matches: int) -> Image.Image:
    accent, top, bot = DEFAULT_ACCENT
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=1)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    chip_f = mdv._ui_font(40, 700)
    chip = "SPORTS ASTROLOGY"
    cw = mdv._tw(chip, chip_f)
    cx = (W - cw - 60) // 2
    d.rounded_rectangle([cx, 260, cx + cw + 60, 260 + mdv._th(chip_f) + 28],
                        radius=16, fill=(*accent, 60), outline=(*accent, 220), width=2)
    d.text((cx + 30, 274), chip, font=chip_f, fill=GOLD)

    tf = mdv._display_font(84, weight=700)
    for i, ln in enumerate(["TODAY'S", "PREDICTIONS"]):
        w = mdv._tw(ln, tf)
        d.text(((W - w) // 2, 420 + i * (mdv._th(tf) + 14)), ln, font=tf, fill=GOLD)

    sf = mdv._ui_font(46, 500)
    sub = f"{when}  •  {num_matches} match{'es' if num_matches != 1 else ''}"
    sw = mdv._tw(sub, sf)
    d.text(((W - sw) // 2, 700), sub, font=sf, fill=(225, 220, 245))

    ff = mdv._ui_font(34, 500)
    foot = mdv.CHANNEL_TAG
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 78), foot, font=ff, fill=(*SILVER, 200))
    return img.convert("RGB")


def render_match_info_card(match: dict, idx: int, total: int) -> Image.Image:
    accent, top, bot = _accent(match["sport"])
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=200 + idx)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=GOLD); d.rectangle([0, H - 6, W, H], fill=GOLD)

    glyph, gfont = mdv._icon(*SPORT_GLYPH.get((match["sport"] or "").lower(), ("✦", "*")), 340)
    gw = mdv._tw(glyph, gfont)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(ov).text(((W - gw) // 2, 340), glyph, font=gfont, fill=(*accent, 40))
    img = Image.alpha_composite(img.convert("RGBA"), ov)
    d = ImageDraw.Draw(img)

    nf = mdv._ui_font(34, 700)
    tag = f"Match {idx} / {total}"
    tw_ = mdv._tw(tag, nf)
    px0 = W - PAD - tw_ - 36
    d.rounded_rectangle([px0, 64, px0 + tw_ + 36, 64 + mdv._th(nf) + 20],
                        radius=14, fill=(*accent, 50), outline=(*accent, 200), width=2)
    d.text((px0 + 18, 74), tag, font=nf, fill=WHITE)

    sport_f = mdv._ui_font(38, 600)
    sport_lbl = match["sport"].upper()
    d.text((PAD, 150), sport_lbl, font=sport_f, fill=(*accent, 255))

    hf = mdv._display_font(64, weight=700)
    vs_line = f"{match['team_a']}  vs  {match['team_b']}"
    y = 210
    for ln in mdv._wrap(vs_line, hf, CW - 40)[:3]:
        d.text((PAD + 2, y + 2), ln, font=hf, fill=(0, 0, 0, 170))
        d.text((PAD,     y),     ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 10

    info_f = mdv._ui_font(42, 500)
    for ln in (match.get("_time_label", ""), f"{match['venue']}, {match['country']}"):
        if not ln.strip():
            continue
        d.text((PAD, y + 20), ln, font=info_f, fill=(225, 220, 245))
        y += mdv._th(info_f) + 16

    ff = mdv._ui_font(36, 500)
    foot = f"{mdv.CHANNEL_TAG}  •  Sports Astrology"
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 78), foot, font=ff, fill=(*SILVER, 200))
    return img.convert("RGB")


def render_view_card(heading: str, sport: str, idx: int, total: int) -> Image.Image:
    """Shared visual for the Sports View / Astrology View cards — same
    caption-safe-zone-scrim pattern as make_topic_video's section card (the
    text itself is delivered as burned word-synced captions, not baked in
    here)."""
    accent, top, bot = _accent(sport)
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=300 + idx)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 6], fill=GOLD); d.rectangle([0, H - 6, W, H], fill=GOLD)

    glyph, gfont = mdv._icon(*SPORT_GLYPH.get((sport or "").lower(), ("✦", "*")), 560)
    gw = mdv._tw(glyph, gfont)
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(ov).text(((W - gw) // 2, H // 2 - 320), glyph, font=gfont, fill=(*accent, 26))
    img = Image.alpha_composite(img.convert("RGBA"), ov)
    d = ImageDraw.Draw(img)

    nf = mdv._ui_font(34, 700)
    tag = f"Match {idx} / {total}"
    tw_ = mdv._tw(tag, nf)
    px0 = W - PAD - tw_ - 36
    d.rounded_rectangle([px0, 64, px0 + tw_ + 36, 64 + mdv._th(nf) + 20],
                        radius=14, fill=(*accent, 50), outline=(*accent, 200), width=2)
    d.text((px0 + 18, 74), tag, font=nf, fill=WHITE)

    hf = mdv._display_font(68, weight=700)
    y = 150
    for ln in mdv._wrap(heading, hf, CW - 200)[:2]:
        d.text((PAD + 2, y + 2), ln, font=hf, fill=(0, 0, 0, 170))
        d.text((PAD,     y),     ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 12
    d.rectangle([PAD, y + 14, PAD + 300, y + 20], fill=(*accent, 220))

    scrim = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(scrim)
    scrim_top = H - 620
    steps = 60
    for i in range(steps):
        a = int(150 * (i / steps))
        yy = scrim_top + int(i * (620 / steps))
        sd.rectangle([0, yy, W, yy + (620 // steps) + 1], fill=(0, 0, 0, a))
    img = Image.alpha_composite(img, scrim)
    d = ImageDraw.Draw(img)

    ff = mdv._ui_font(36, 500)
    foot = f"{mdv.CHANNEL_TAG}  •  Sports Astrology"
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 78), foot, font=ff, fill=(*SILVER, 200))
    return img.convert("RGB")


def render_prediction_card(match: dict, prediction: dict, idx: int, total: int) -> Image.Image:
    accent, top, bot = _accent(match["sport"])
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=400 + idx)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    nf = mdv._ui_font(34, 700)
    tag = f"Match {idx} / {total}  •  PREDICTION"
    tw_ = mdv._tw(tag, nf)
    d.text(((W - tw_) // 2, 120), tag, font=nf, fill=(*accent, 255))

    hf = mdv._display_font(64, weight=700)
    fav = str(prediction.get("favoured_team", "")).strip() or "Too close to call"
    y = 260
    for ln in mdv._wrap(fav, hf, CW - 80)[:2]:
        w = mdv._tw(ln, hf)
        d.text(((W - w) // 2 + 2, y + 2), ln, font=hf, fill=(0, 0, 0, 170))
        d.text(((W - w) // 2,     y),     ln, font=hf, fill=GOLD)
        y += mdv._th(hf) + 10

    mt_f = mdv._ui_font(46, 600)
    mt = str(prediction.get("match_type", "")).upper()
    mtw = mdv._tw(mt, mt_f)
    d.text(((W - mtw) // 2, y + 30), mt, font=mt_f, fill=(225, 220, 245))
    y += 30 + mdv._th(mt_f) + 40

    # Confidence meter — a simple filled pill, not a claim of precision.
    conf = int(prediction.get("confidence_pct", 60))
    meter_w, meter_h = CW - 120, 56
    mx0 = (W - meter_w) // 2
    d.rounded_rectangle([mx0, y, mx0 + meter_w, y + meter_h], radius=meter_h // 2,
                        outline=(*accent, 255), width=3)
    fill_w = int(meter_w * max(0, min(100, conf)) / 100)
    if fill_w > 8:
        d.rounded_rectangle([mx0, y, mx0 + fill_w, y + meter_h], radius=meter_h // 2,
                            fill=(*accent, 180))
    cf = mdv._ui_font(38, 700)
    conf_lbl = f"{conf}% CONFIDENCE"
    clw = mdv._tw(conf_lbl, cf)
    d.text(((W - clw) // 2, y + meter_h + 20), conf_lbl, font=cf, fill=WHITE)

    ff = mdv._ui_font(36, 500)
    foot = f"{mdv.CHANNEL_TAG}  •  Sports Astrology"
    fw = mdv._tw(foot, ff)
    d.text(((W - fw) // 2, H - 78), foot, font=ff, fill=(*SILVER, 200))
    return img.convert("RGB")


def render_disclaimer_card(when: str) -> Image.Image:
    accent, top, bot = DEFAULT_ACCENT
    img = mdv._cosmic_bg(W, H, top, bot, accent, seed=999)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 8], fill=GOLD); d.rectangle([0, H - 8, W, H], fill=GOLD)

    tf = mdv._display_font(58, weight=700)
    hf = mdv._wrap("ENTERTAINMENT & ASTROLOGY ANALYSIS ONLY", tf, CW - 80)
    y = 500
    for ln in hf[:3]:
        w = mdv._tw(ln, tf)
        d.text(((W - w) // 2, y), ln, font=tf, fill=GOLD)
        y += mdv._th(tf) + 10

    sf = mdv._ui_font(42, 500)
    for ln in mdv._wrap("Not betting or financial advice.", sf, CW - 100)[:2]:
        w = mdv._tw(ln, sf)
        d.text(((W - w) // 2, y + 30), ln, font=sf, fill=(225, 220, 245))
        y += mdv._th(sf) + 14

    cf = mdv._ui_font(50, 700)
    cta = "SUBSCRIBE"
    cw = mdv._tw(cta, cf)
    cx = (W - cw - 80) // 2
    d.rounded_rectangle([cx, H - 340, cx + cw + 80, H - 340 + mdv._th(cf) + 44],
                        radius=22, fill=(220, 40, 40))
    d.text((cx + 40, H - 318), cta, font=cf, fill=WHITE)

    chf = mdv._ui_font(44, 600)
    cw2 = mdv._tw(mdv.CHANNEL_TAG, chf)
    d.text(((W - cw2) // 2, H - 130), mdv.CHANNEL_TAG, font=chf, fill=GOLD)
    return img.convert("RGB")


def render_thumbnail(data: dict, out_path: str) -> None:
    accent, top, bot = DEFAULT_ACCENT
    TW, TH = 1280, 720
    img = mdv._cosmic_bg(TW, TH, top, bot, accent, seed=7)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, TW, 6], fill=GOLD); d.rectangle([0, TH - 6, TW, TH], fill=GOLD)
    tf = mdv._display_font(88, weight=700)
    title = "SPORTS ASTROLOGY"
    lines = mdv._wrap(title, tf, TW - 100)[:3]
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
    matches = data.get("matches", [])
    when = data.get("date", date_tag)
    disclaimer = data.get("disclaimer",
        "This prediction is for entertainment and astrology analysis only. "
        "It is not betting or financial advice.")

    mdv.CONTENT_TYPE = "sports"

    out_dir = Path("outputs") / date_tag / "SportsAll"
    out_dir.mkdir(parents=True, exist_ok=True)
    base = f"sports_{date_tag}"
    video_path = str(out_dir / f"{base}.mp4")
    thumb_path = str(out_dir / f"{base}_thumbnail.jpg")

    voice = mdv._day_voice(date_tag)
    print(f"\n{'='*58}\n  SPORTS ASTROLOGY — {when}\n"
          f"  {len(matches)} matches | voice {voice}\n{'='*58}\n")

    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        pngs, durs, clips = [], [], []
        chapters = ["0:00 Intro"]
        t_cursor = 0.0
        caption_cues = []   # (start, end, text) in GLOBAL video-timeline seconds

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
            # Group THIS card's own words into caption phrases BEFORE applying
            # the global offset — grouping must never span two cards, or a cue
            # could straddle the hard cut between them. See make_daily_video.
            for c_start, c_end, c_text in mdv._group_words_into_cues(word_cues, max_words=3):
                caption_cues.append((t_cursor + c_start, t_cursor + c_end, c_text))
            if is_chapter:
                chapters.append(f"{_ts(t_cursor)} {label}")
            t_cursor += card_dur
            print(f"      [{label[:34]:<34}] {card_dur:.1f}s")

        intro_hook = (f"Today's sports astrology predictions for {when}. "
                     f"{len(matches)} match{'es' if len(matches) != 1 else ''} on the "
                     f"radar — let's dive in.")
        add(render_day_intro_card(when, len(matches)), intro_hook, "Intro")

        for i, m in enumerate(matches, 1):
            match = m["match"]
            from datetime import datetime as _dt
            dt = _dt.fromisoformat(match["datetime_utc"])
            match["_time_label"] = dt.strftime("%B %d, %H:%M UTC")
            label = f"{match['team_a']} vs {match['team_b']}"

            add(render_match_info_card(match, i, len(matches)),
                f"Match {i}: {match['sport']}. {match['team_a']} versus {match['team_b']}, "
                f"{dt.strftime('%B %d, %H:%M UTC')}, at {match['venue']}, {match['country']}.",
                f"Match {i}: {label}", is_chapter=True)

            add(render_view_card("Sports View", match["sport"], i, len(matches)),
                m["sports_view"], f"  Sports View")

            add(render_view_card("Astrology View", match["sport"], i, len(matches)),
                m["astrology_view"], f"  Astrology View")

            pred = m["prediction"]
            pred_text = (f"So here's the call: {pred['favoured_team']} — {pred['match_type']}. "
                        f"Watch for {pred['turning_point']}. "
                        f"Confidence: {pred['confidence_pct']} percent.")
            add(render_prediction_card(match, pred, i, len(matches)),
                pred_text, f"  Prediction")

        add(render_disclaimer_card(when),
            f"{disclaimer} Subscribe for a new sports astrology prediction every day.",
            "Disclaimer", is_chapter=True)

        total = sum(durs)
        mdv.VIDEO_FPS = mdv.safe_static_fps(total)
        print(f"      Total: {total:.0f}s ({int(total//60)}m {int(total%60)}s)  |  {mdv.VIDEO_FPS}fps")

        srt_path = str(tmp / "captions.srt")
        has_captions = mdv._write_srt(caption_cues, srt_path)
        print(f"      Captions: {len(caption_cues)} cues" if has_captions
              else "      [WARN] No word-timing captured — captions skipped")

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
            print(f"\n[3/4] Assembling {W}x{H} — trying Ken Burns motion @ {m_fps}fps...")
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
        desc = f"{desc}\n\n⏱ Chapters:\n{chapters_block}"
    meta = {
        "title":       data.get("title", f"Sports Astrology — {when}")[:100],
        "description": desc,
        "tags":        data.get("tags", []),
        "hashtags":    data.get("hashtags", []),
        "date":        date_tag,
        "content_type": "sports",
        "pinned_comment": data.get("pinned_comment", ""),
    }
    (out_dir / f"{base}_assets.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n[OK] Done → {video_path}")
    return video_path


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 make_sports_video.py sports_YYYYMMDD.json | YYYYMMDD")
        sys.exit(1)
    arg = sys.argv[1]
    json_path = arg if arg.endswith(".json") else f"sports_{arg}.json"
    process(json_path)


if __name__ == "__main__":
    main()
