"""
Daily runner — orchestrates content → video → upload pipeline.

Usage
-----
  # Run once immediately (great for testing):
  python daily_runner.py --now

  # Start the scheduler (blocks, runs every day at UPLOAD_TIME):
  python daily_runner.py --schedule

  # Generate video only, skip upload:
  python daily_runner.py --now --no-upload
"""

import argparse
import datetime
import logging
import os
import sys
import time
import traceback
from pathlib import Path

import schedule

import config
from content_generator import build_daily_content
from video_generator import generate_video
from youtube_uploader import upload_video

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

Path(config.LOGS_DIR).mkdir(parents=True, exist_ok=True)
log_file = Path(config.LOGS_DIR) / f"runner_{datetime.date.today()}.log"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def run_pipeline(upload: bool = True):
    log.info("=" * 60)
    log.info("Daily energy intelligence video pipeline starting")

    # 1. Fetch content / build script
    log.info("Step 1/3 — building daily content…")
    content = build_daily_content()
    log.info(f"  Topic : {content['topic']}")
    log.info(f"  Title : {content['title']}")

    # 2. Render video
    log.info("Step 2/3 — rendering pencil-sketch video…")
    date_tag = content["date"].strftime("%Y-%m-%d")
    output_path = str(Path(config.OUTPUT_DIR) / f"energy_daily_{date_tag}.mp4")
    Path(config.OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

    generate_video(content, output_path)
    size_mb = os.path.getsize(output_path) / 1_048_576
    log.info(f"  Video : {output_path} ({size_mb:.1f} MB)")

    # 3. Upload
    if upload:
        log.info("Step 3/3 — uploading to YouTube…")
        try:
            video_id = upload_video(output_path, content)
            log.info(f"  URL   : https://youtu.be/{video_id}")
        except FileNotFoundError as e:
            log.error(f"Upload skipped — {e}")
    else:
        log.info("Step 3/3 — upload skipped (--no-upload flag set)")

    log.info("Pipeline complete.")
    log.info("=" * 60)


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def run_scheduled(upload: bool = True):
    upload_time = config.UPLOAD_TIME
    log.info(f"Scheduler active — will run daily at {upload_time} local time.")

    def _job():
        try:
            run_pipeline(upload=upload)
        except Exception:
            log.error("Pipeline error:\n" + traceback.format_exc())

    schedule.every().day.at(upload_time).do(_job)

    while True:
        schedule.run_pending()
        time.sleep(30)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Energy Intelligence YouTube automation")
    parser.add_argument("--now", action="store_true",
                        help="Run pipeline immediately instead of scheduling")
    parser.add_argument("--schedule", action="store_true",
                        help="Start the daily scheduler (blocking)")
    parser.add_argument("--no-upload", action="store_true",
                        help="Skip the YouTube upload step")
    args = parser.parse_args()

    do_upload = not args.no_upload

    if args.now:
        run_pipeline(upload=do_upload)
    elif args.schedule:
        run_scheduled(upload=do_upload)
    else:
        parser.print_help()
