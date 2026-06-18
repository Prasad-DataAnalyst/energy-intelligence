#!/usr/bin/env python3
"""
run_daily.py
Master daily runner: generate assets → render video → quality check → upload, all 12 signs.

Usage:
  python3 run_daily.py --date 20260616 --period "June 2026" --format short
  python3 run_daily.py --date 20260617 --period "June 2026" --signs scorpio,leo
  python3 run_daily.py --date 20260616 --period "June 2026" --skip-assets --upload
"""
import argparse
import json
import os
import smtplib
import subprocess
import sys
import time
from datetime import datetime
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

PYTHON = sys.executable

SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]


def run_captured(cmd: list, timeout: int = 120) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return False, str(e)


def run_live(cmd: list, timeout: int = 1800) -> bool:
    try:
        r = subprocess.run(cmd, timeout=timeout)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT after {timeout}s]")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def send_summary_email(date: str, results: dict, elapsed: int, upload: bool) -> None:
    """Send daily summary email via Gmail. Silent if not configured."""
    app_pw = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")
    sender = os.environ.get("GMAIL_ADDRESS", "prasad2t@gmail.com")
    if not app_pw:
        return

    passed = sum(
        1 for r in results.values()
        if all(str(r.get(k, "")).startswith("✅") for k in ["assets", "video", "quality"] if r.get(k))
    )
    total  = len(results)
    status = "ALL OK" if passed == total else f"ISSUES: {passed}/{total} passed"

    lines = [
        f"GetMindFuelNow Daily Pipeline — {date}",
        f"Result  : {status}",
        f"Duration: {elapsed // 60}m {elapsed % 60}s",
        f"Time    : {datetime.utcnow():%Y-%m-%d %H:%M UTC}",
        "",
        "Sign-by-sign results:",
        "-" * 55,
    ]
    for sign, r in results.items():
        a  = r.get("assets",  "—")
        v  = r.get("video",   "—")
        q  = r.get("quality", "—")
        u  = r.get("upload",  "")
        ok = all(str(x).startswith("✅") for x in [a, v, q] if x)
        lines.append(
            f"{'OK' if ok else 'FAIL'}  {sign.title():<14}"
            f"  assets:{a}  video:{v}  qc:{q}"
            + (f"  yt:{u}" if u else "")
        )

    if upload:
        lines += ["", "Upload log: ~/energy-intelligence/youtube_automation/logs/uploads.json"]

    msg          = MIMEText("\n".join(lines))
    msg["Subject"] = f"GetMindFuelNow {date} — {status}"
    msg["From"]    = sender
    msg["To"]      = sender

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(sender, app_pw)
            s.sendmail(sender, sender, msg.as_string())
        print(f"  [INFO] Summary email sent to {sender}")
    except Exception as e:
        print(f"  [INFO] Email skipped: {e}")


def main():
    parser = argparse.ArgumentParser(description="Daily GetMindFuelNow Shorts pipeline")
    parser.add_argument("--date",        required=True, metavar="YYYYMMDD")
    parser.add_argument("--period",      required=True, help="e.g. 'June 2026'")
    parser.add_argument("--format",      default="short", choices=["short", "long"])
    parser.add_argument("--signs",       metavar="SIGN,...",
                        help="Comma-separated subset (default: all 12)")
    parser.add_argument("--skip-assets", action="store_true",
                        help="Reuse existing JSON files instead of regenerating")
    parser.add_argument("--upload",      action="store_true",
                        help="Auto-upload completed videos to YouTube")
    parser.add_argument("--tiktok",      action="store_true",
                        help="Also cross-post to TikTok")
    args = parser.parse_args()

    signs = ([s.strip().lower() for s in args.signs.split(",")]
             if args.signs else SIGNS)

    print(f"\n{'='*60}")
    print(f"  GetMindFuelNow Daily Pipeline — {args.date}")
    print(f"  Period: {args.period}  |  Format: {args.format}")
    print(f"  Signs: {len(signs)}  |  Started: {datetime.now():%H:%M:%S}")
    print(f"{'='*60}\n")

    # ── Process any videos queued from previous quota failures ────────────────
    if args.upload:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from youtube_uploader import process_retry_queue
            retried = process_retry_queue()
            if retried:
                print(f"  [INFO] Uploaded {retried} queued video(s) from previous days\n")
        except Exception as _e:
            print(f"  [INFO] Retry queue skipped: {_e}\n")

    results = {}
    t_start = time.time()

    # Pause daemon so rendering gets full CPU + RAM
    subprocess.run(["sudo", "systemctl", "stop", "getmindfuelnow"], capture_output=True)
    print("  [INFO] Daemon paused for rendering — will restart after all signs\n")

    for idx, sign in enumerate(signs, 1):
        print(f"\n[{idx:02d}/{len(signs):02d}] ── {sign.upper()} ──────────────────────────")
        r = {}

        # ── 1. Assets ─────────────────────────────────────────────────────────
        json_file = f"{sign}_short_{args.date}.json"
        if args.skip_assets and Path(json_file).exists():
            r["assets"] = "✅ existing"
            print(f"  [1/4] Assets   — reusing {json_file}")
        else:
            print(f"  [1/4] Assets   — generating...")
            ok, out = run_captured(
                [PYTHON, "generate_horoscope_assets.py",
                 sign.title(), args.period, "--format", args.format],
                timeout=120,
            )
            r["assets"] = "✅" if ok else f"❌ {out[-120:]}"
            print(f"         {'OK' if ok else 'FAILED'}" +
                  (f": {out[-120:]}" if not ok else ""))
            if not ok:
                results[sign] = r
                continue

        # ── 2. Video ──────────────────────────────────────────────────────────
        print(f"  [2/4] Video    — rendering...")
        ok = run_live([PYTHON, "make_shorts_video.py", json_file], timeout=1800)
        r["video"] = "✅" if ok else "❌ render failed"
        if not ok:
            results[sign] = r
            continue

        # ── 3. Quality check ──────────────────────────────────────────────────
        video_path = f"outputs/{args.date}/{sign.title()}/{sign}_short_{args.date}.mp4"
        print(f"  [3/4] QC       — checking...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=60)
        r["quality"] = "✅" if ok else f"❌ {out[-120:]}"
        print(f"         {'PASS' if ok else 'FAIL'}" + (f": {out}" if not ok else ""))

        # ── 4. Upload ─────────────────────────────────────────────────────────
        if args.upload and r.get("quality", "").startswith("✅"):
            print(f"  [4/4] Upload   — uploading to YouTube...")
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from youtube_uploader import (upload_video, upload_thumbnail,
                                              post_comment, pin_comment)
                assets_json = (f"outputs/{args.date}/{sign.title()}/"
                               f"{sign}_{args.date}_assets.json")
                assets  = json.loads(Path(assets_json).read_text(encoding="utf-8"))
                content = {
                    "title":          assets.get("title", ""),
                    "description":    assets.get("description", ""),
                    "tags":           assets.get("tags", []),
                    "date":           args.date,
                    "privacy_status": "public",
                }
                vid_id = upload_video(video_path, content)
                upload_thumbnail(vid_id, video_path.replace(".mp4", "_thumbnail.jpg"))
                cid = post_comment(vid_id, assets.get("pinned_comment", ""))
                pin_comment(vid_id, cid)
                r["upload"] = f"✅ youtu.be/{vid_id}"
                print(f"         OK: https://youtu.be/{vid_id}")
            except Exception as _e:
                r["upload"] = f"❌ {str(_e)[:80]}"
                print(f"         FAILED: {_e}")

        # ── 5. TikTok ─────────────────────────────────────────────────────────
        if args.tiktok and r.get("quality", "").startswith("✅"):
            print(f"  [5/5] TikTok   — cross-posting...")
            try:
                from tiktok_uploader import upload_to_tiktok
                assets_json = (f"outputs/{args.date}/{sign.title()}/"
                               f"{sign}_{args.date}_assets.json")
                assets = json.loads(Path(assets_json).read_text(encoding="utf-8"))
                pub_id = upload_to_tiktok(
                    video_path, assets.get("title", ""), assets.get("hashtags", [])
                )
                r["tiktok"] = f"✅ {pub_id[:12]}"
                print(f"         OK: {pub_id}")
            except Exception as _e:
                r["tiktok"] = f"❌ {str(_e)[:80]}"
                print(f"         FAILED: {_e}")

        results[sign] = r

    # ── Final summary ──────────────────────────────────────────────────────────
    elapsed = int(time.time() - t_start)
    print(f"\n{'='*60}")
    print(f"  SUMMARY — {args.date}  ({elapsed // 60}m {elapsed % 60}s)")
    print(f"{'='*60}")

    passed = 0
    for sign, r in results.items():
        a  = r.get("assets",  "—")
        v  = r.get("video",   "—")
        q  = r.get("quality", "—")
        u  = r.get("upload",  "")
        tt = r.get("tiktok",  "")
        all_ok = all(str(x).startswith("✅") for x in [a, v, q] if x)
        icon   = "✅" if all_ok else "❌"
        print(f"  {icon}  {sign.title():<14}  assets:{a}  video:{v}  qc:{q}"
              + (f"  yt:{u}"  if u  else "")
              + (f"  tt:{tt}" if tt else ""))
        if all_ok:
            passed += 1

    print(f"\n  {passed}/{len(signs)} signs complete")
    print(f"  Output: outputs/{args.date}/")
    print(f"{'='*60}\n")

    # Restart daemon
    subprocess.run(["sudo", "systemctl", "start", "getmindfuelnow"], capture_output=True)
    print("  [INFO] Daemon restarted\n")

    # Email summary
    send_summary_email(args.date, results, elapsed, args.upload)

    sys.exit(0 if passed == len(signs) else 1)


if __name__ == "__main__":
    main()
