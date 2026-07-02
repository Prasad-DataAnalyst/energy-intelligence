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
from datetime import datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

PYTHON = sys.executable


def _utcnow() -> datetime:
    """Timezone-naive UTC now (replaces deprecated datetime.utcnow())."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]

# Each sign publishes at a different hour — spreads 12 videos across the day
# Times in UTC (EDT = UTC-4, so 10 UTC = 6 AM EST = prime morning horoscope time)
SIGN_PUBLISH_HOUR_UTC = {
    "aries":       10,   # 6 AM EST
    "taurus":      11,   # 7 AM EST
    "gemini":      12,   # 8 AM EST
    "cancer":      13,   # 9 AM EST
    "leo":         14,   # 10 AM EST
    "virgo":       15,   # 11 AM EST
    "libra":       16,   # 12 PM EST
    "scorpio":     17,   # 1 PM EST
    "sagittarius": 18,   # 2 PM EST
    "capricorn":   19,   # 3 PM EST
    "aquarius":    20,   # 4 PM EST
    "pisces":      21,   # 5 PM EST
}


def run_captured(cmd: list, timeout: int = 120) -> tuple:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return False, str(e)


def _daemon(action: str) -> None:
    """Stop/start the getmindfuelnow daemon to free CPU/RAM during rendering.
    Tries both /usr/bin and /bin systemctl paths (merged-/usr Ubuntu resolves to
    /usr/bin, but the sudoers rule may whitelist either). Best-effort; warns on
    failure but never blocks the pipeline."""
    r = subprocess.run(["sudo", "systemctl", action, "getmindfuelnow"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  [WARN] daemon {action} failed (rc={r.returncode}): "
              f"{(r.stderr or '').strip()[:120]}")


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

    check_keys = ["assets", "video", "quality"] + (["upload"] if upload else [])
    passed = sum(
        1 for r in results.values()
        if all(str(r.get(k, "")).startswith("✅") for k in check_keys if r.get(k))
    )
    total  = len(results)
    status = "ALL OK" if passed == total else f"ISSUES: {passed}/{total} passed"

    lines = [
        f"GetMindFuelNow Daily Pipeline — {date}",
        f"Result  : {status}",
        f"Duration: {elapsed // 60}m {elapsed % 60}s",
        f"Time    : {_utcnow():%Y-%m-%d %H:%M UTC}",
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


def run_all_signs_pipeline(args) -> int:
    """
    New pipeline: one combined video covering all 12 signs.
    generate_daily_assets.py → make_daily_video.py → upload (optional)
    Returns exit code (0 = success).
    """
    t_start = time.time()

    print(f"\n{'='*60}")
    print(f"  GetMindFuelNow — ALL 12 SIGNS VIDEO — {args.date}")
    print(f"  Period: {args.period}  |  Started: {datetime.now():%H:%M:%S}")
    print(f"{'='*60}\n")

    _daemon("stop")
    print("  [INFO] Daemon paused for rendering\n")

    assets_ok = qc_ok = False
    upload_result = ""
    try:
        # ── 1. Generate all-signs assets JSON ──────────────────────────────────
        json_file = f"daily_horoscope_{args.date}.json"
        if args.skip_assets and Path(json_file).exists():
            print(f"[1/4] Assets   — reusing {json_file}")
            assets_ok = True
        else:
            print(f"[1/4] Assets   — generating all 12 signs via Claude...")
            ok, out = run_captured(
                [PYTHON, "generate_daily_assets.py", args.period, args.date],
                timeout=300,
            )
            assets_ok = ok
            if ok:
                print(f"      OK → {json_file}")
            else:
                print(f"      FAILED: {out[-200:]}", file=sys.stderr)

        if not assets_ok:
            return 1

        # ── 2. Render slideshow video ──────────────────────────────────────────
        print(f"\n[2/4] Video    — rendering slideshow...")
        ok = run_live([PYTHON, "make_daily_video.py", json_file], timeout=1800)
        if not ok:
            print("      FAILED", file=sys.stderr)
            return 1
        print("      OK")

        # ── 3. Quality check ───────────────────────────────────────────────────
        video_path = f"outputs/{args.date}/DailyAll/daily_horoscope_{args.date}.mp4"
        print(f"\n[3/4] QC       — checking {video_path}...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=60)
        qc_ok = ok
        print(f"      {'PASS' if ok else 'FAIL'}" + (f": {out}" if not ok else ""))

        # ── 4. Upload ──────────────────────────────────────────────────────────
        if args.upload and qc_ok:
            print(f"\n[4/4] Upload   — uploading to YouTube...")
            try:
                sys.path.insert(0, str(Path(__file__).parent))
                from youtube_uploader import (upload_video, upload_thumbnail,
                                              post_comment, pin_comment,
                                              process_retry_queue, find_uploaded,
                                              quota_spent_today, DAILY_QUOTA_LIMIT)
                import json as _json

                # Idempotency: if this date was already uploaded, don't post a
                # duplicate (and don't waste another ~1600 quota units). Repeated
                # manual runs or a double cron are now safe. Use --force to override.
                existing = find_uploaded(args.date)
                if existing and not args.force:
                    print(f"      SKIP: {args.date} already uploaded → "
                          f"https://youtu.be/{existing} (use --force to re-upload)")
                    upload_result = f"✅ youtu.be/{existing} (existing)"
                    raise StopIteration  # jump to finally without treating as error

                # Retry any videos queued from a previous quota-limited day.
                try:
                    retried = process_retry_queue()
                    if retried:
                        print(f"      [INFO] Uploaded {retried} queued video(s) from prior days")
                except Exception as _qe:
                    print(f"      [INFO] Retry queue skipped: {_qe}")

                print(f"      Quota used today: {quota_spent_today()}/{DAILY_QUOTA_LIMIT} units")

                assets_json = f"outputs/{args.date}/DailyAll/daily_horoscope_{args.date}_assets.json"
                assets  = _json.loads(Path(assets_json).read_text(encoding="utf-8"))
                content = {
                    "title":          assets.get("title", ""),
                    "description":    assets.get("description", ""),
                    "tags":           assets.get("tags", []),
                    "date":           args.date,
                    "privacy_status": "public",
                }
                # Publish at 10 AM UTC (6 AM EST). If that moment is already past
                # (late/manual run), schedule ASAP — YouTube rejects past publishAt.
                pub_dt = datetime.strptime(args.date, "%Y%m%d").replace(hour=10, minute=0, second=0)
                if pub_dt <= _utcnow():
                    pub_dt = _utcnow() + timedelta(minutes=15)
                publish_at = pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                vid_id     = upload_video(video_path, content, publish_at=publish_at)
                thumb_path = f"outputs/{args.date}/DailyAll/daily_horoscope_{args.date}_thumbnail.jpg"
                upload_thumbnail(vid_id, thumb_path)
                cid = post_comment(vid_id, assets.get("pinned_comment", ""))
                pin_comment(vid_id, cid)
                print(f"      OK: https://youtu.be/{vid_id} — publishes {publish_at}")
                upload_result = f"✅ youtu.be/{vid_id}"
            except StopIteration:
                pass  # already-uploaded skip — upload_result already set
            except Exception as _e:
                print(f"      FAILED: {_e}")
                upload_result = f"❌ {str(_e)[:80]}"
    finally:
        _daemon("start")
        print("  [INFO] Daemon restarted\n")

    elapsed   = int(time.time() - t_start)
    upload_ok = (not args.upload) or upload_result.startswith("✅")
    all_ok    = assets_ok and qc_ok and upload_ok

    print(f"\n{'='*60}")
    print(f"  SUMMARY — {args.date}  ({elapsed // 60}m {elapsed % 60}s)")
    print(f"  {'✅ ALL OK' if all_ok else '❌ ISSUES'}")
    if upload_result:
        print(f"  Upload: {upload_result}")
    print(f"  Output: outputs/{args.date}/DailyAll/")
    print(f"{'='*60}\n")

    send_summary_email(
        args.date,
        {"all_signs": {
            "assets": "✅" if assets_ok else "❌",
            "video":  "✅" if qc_ok else "❌",
            "quality":"✅" if qc_ok else "❌",
            "upload": upload_result,
        }},
        elapsed, args.upload,
    )
    return 0 if all_ok else 1


def main():
    parser = argparse.ArgumentParser(description="Daily GetMindFuelNow Shorts pipeline")
    parser.add_argument("--date",        required=True, metavar="YYYYMMDD")
    parser.add_argument("--period",      required=True, help="e.g. 'June 2026'")
    parser.add_argument("--mode",        default="all", choices=["all", "short"],
                        help="'all' = one combined 12-sign video (default), "
                             "'short' = 12 separate per-sign videos")
    parser.add_argument("--format",      default="short", choices=["short", "long"])
    parser.add_argument("--signs",       metavar="SIGN,...",
                        help="Comma-separated subset (default: all 12) — short mode only")
    parser.add_argument("--skip-assets", action="store_true",
                        help="Reuse existing JSON files instead of regenerating")
    parser.add_argument("--upload",      action="store_true",
                        help="Auto-upload completed videos to YouTube")
    parser.add_argument("--force",       action="store_true",
                        help="Re-upload even if a video for this date already exists")
    parser.add_argument("--tiktok",      action="store_true",
                        help="Also cross-post to TikTok")
    args = parser.parse_args()

    # ── NEW: all-signs combined video (default) ────────────────────────────────
    if args.mode == "all":
        sys.exit(run_all_signs_pipeline(args))

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
    _daemon("stop")
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
                # Schedule each sign to publish at its own hour
                pub_hour = SIGN_PUBLISH_HOUR_UTC.get(sign, 12)
                pub_date = datetime.strptime(args.date, "%Y%m%d")
                publish_at = pub_date.replace(
                    hour=pub_hour, minute=0, second=0
                ).strftime("%Y-%m-%dT%H:%M:%SZ")

                vid_id = upload_video(video_path, content, publish_at=publish_at)
                upload_thumbnail(vid_id, video_path.replace(".mp4", "_thumbnail.jpg"))
                cid = post_comment(vid_id, assets.get("pinned_comment", ""))
                pin_comment(vid_id, cid)
                r["upload"] = f"✅ youtu.be/{vid_id} @ {pub_hour}:00 UTC"
                print(f"         OK: https://youtu.be/{vid_id} — publishes {publish_at}")
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
    _daemon("start")
    print("  [INFO] Daemon restarted\n")

    # Email summary
    send_summary_email(args.date, results, elapsed, args.upload)

    sys.exit(0 if passed == len(signs) else 1)


if __name__ == "__main__":
    main()
