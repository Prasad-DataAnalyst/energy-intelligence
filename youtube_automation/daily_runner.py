"""
daily_runner.py — Mind Fuel Daily
===================================
Orchestrates the full pipeline:
  1. Build daily content (topic, bullets, takeaway)
  2. Generate AI voiceover  (edge-tts  → en-US-AriaNeural)
  3. Generate background music  (procedural lo-fi beat)
  4. Render pencil-sketch video frames  (1920×1080 @ 24fps)
  5. Mix audio + mux into final MP4
  6. Upload to YouTube

Usage
-----
  python daily_runner.py --now          # run immediately
  python daily_runner.py --schedule     # run daily at UPLOAD_TIME
  python daily_runner.py --now --no-upload   # generate only
"""

import argparse
import datetime
import logging
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import schedule

import config
from content_generator import build_daily_content
from video_generator    import generate_video
from audio_generator    import build_script, generate_voiceover, \
                               generate_background_music, mix_audio
from youtube_uploader   import upload_video

# ── Logging ───────────────────────────────────────────────────────────────────
Path(config.LOGS_DIR).mkdir(parents=True, exist_ok=True)
log_file = Path(config.LOGS_DIR) / f"runner_{datetime.date.today()}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(log_file), logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


# ── Pipeline ──────────────────────────────────────────────────────────────────

def run_pipeline(upload: bool = True):
    log.info("=" * 65)
    log.info("Mind Fuel Daily — video pipeline starting")

    # ── 1. Content ────────────────────────────────────────────────────────────
    log.info("Step 1/5 — building daily content…")
    content  = build_daily_content()
    date_tag = content["date"].strftime("%Y-%m-%d")
    log.info(f"  Topic    : {content['topic']}")
    log.info(f"  Category : {content['category']}")

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    voice_wav = str(out_dir / f"voice_{date_tag}.wav")
    music_wav = str(out_dir / f"music_{date_tag}.wav")
    mixed_wav = str(out_dir / f"audio_{date_tag}.wav")
    silent_mp4 = str(out_dir / f"silent_{date_tag}.mp4")
    final_mp4  = str(out_dir / f"mind_fuel_{date_tag}.mp4")

    # ── 2. Voiceover ──────────────────────────────────────────────────────────
    log.info("Step 2/5 — generating AI voiceover (espeak-ng female)…")
    script = build_script(content)
    log.info(f"  Script   : {len(script.split())} words")
    generate_voiceover(script, voice_wav)

    # ── 3. Background music ───────────────────────────────────────────────────
    log.info("Step 3/5 — generating background music…")
    # We'll estimate total duration; will trim/pad at mix step
    est_seconds = 6 + 4 + len(content["bullets"]) * 13 + 10 + 8
    generate_background_music(est_seconds + 2, music_wav)

    # ── 4. Video frames ───────────────────────────────────────────────────────
    log.info("Step 4/5 — rendering 1080p video frames…")
    result      = generate_video(content, silent_mp4)
    total_s     = result[1] if isinstance(result, tuple) else est_seconds
    size_mb     = os.path.getsize(silent_mp4) / 1_048_576
    log.info(f"  Silent   : {silent_mp4} ({size_mb:.1f} MB, {total_s}s)")

    # ── 5. Mix audio + mux ────────────────────────────────────────────────────
    log.info("Step 5/5 — mixing audio and muxing into final MP4…")
    mix_audio(voice_wav, music_wav, mixed_wav, total_s)

    cmd = [
        "ffmpeg", "-y",
        "-i", silent_mp4,
        "-i", mixed_wav,
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-shortest",
        final_mp4,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"ffmpeg mux error: {r.stderr[:400]}")
        final_mp4 = silent_mp4   # fallback: use silent video
    else:
        # Clean up temp files
        for f in [voice_wav, music_wav, mixed_wav, silent_mp4]:
            try: os.remove(f)
            except: pass

    size_mb = os.path.getsize(final_mp4) / 1_048_576
    log.info(f"  Final    : {final_mp4} ({size_mb:.1f} MB)")

    # ── Upload ────────────────────────────────────────────────────────────────
    if upload:
        log.info("Uploading to YouTube…")
        try:
            vid_id = upload_video(final_mp4, content)
            log.info(f"  Live at  : https://youtu.be/{vid_id}")
        except FileNotFoundError as e:
            log.error(f"Upload skipped — {e}")
        except RuntimeError as e:
            log.error(str(e))
    else:
        log.info("Upload skipped (--no-upload)")

    log.info("Pipeline complete.")
    log.info("=" * 65)


# ── Scheduler ─────────────────────────────────────────────────────────────────

def run_scheduled(upload: bool = True):
    log.info(f"Scheduler started — daily upload at {config.UPLOAD_TIME} local time")
    schedule.every().day.at(config.UPLOAD_TIME).do(
        lambda: _safe_run(upload)
    )
    while True:
        schedule.run_pending()
        time.sleep(30)


def _safe_run(upload):
    try:
        run_pipeline(upload=upload)
    except Exception:
        log.error("Pipeline error:\n" + traceback.format_exc())


# ── Entry ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--now",       action="store_true")
    parser.add_argument("--schedule",  action="store_true")
    parser.add_argument("--no-upload", action="store_true")
    args = parser.parse_args()

    do_upload = not args.no_upload
    if args.now:
        run_pipeline(upload=do_upload)
    elif args.schedule:
        run_scheduled(upload=do_upload)
    else:
        parser.print_help()
