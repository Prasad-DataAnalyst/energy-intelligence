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
import fcntl
import json
import os
import signal
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
_LOCK_PATH = Path(__file__).parent / ".run_daily.lock"


def _acquire_pipeline_lock(max_wait_secs: int | None = None):
    """Cross-invocation lock with a bounded WAIT. The VM is 1-core: cron
    schedules daily/topic/prediction/weekly/... with gaps sized for TYPICAL
    render times, but a slow day (retries, a big render) can still be going
    when the next job fires. Two renders fighting for the same core is worse
    than serializing — both slow down and risk the timeout already hit in
    production once.

    Previously a busy lock SKIPPED the run entirely (exit 0) — which silently
    dropped a whole day's video whenever the 5:30 daily overran 6:00 (topic)
    or 6:30 (prediction). Now we WAIT for the current render to finish (poll
    every 30s, up to LOCK_WAIT_SECS — default 90 min, comfortably above the
    45-min render timeout + retry) and only give up past the deadline. The
    later publish times (7-11 AM ET) leave hours of slack for a queued job.
    Held for the process lifetime; the OS releases it when the process exits."""
    if max_wait_secs is None:
        max_wait_secs = int(os.environ.get("LOCK_WAIT_SECS", "5400"))
    fd = open(_LOCK_PATH, "w")
    deadline = time.time() + max_wait_secs
    announced = False
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if announced:
                print("  [INFO] Lock acquired — the earlier render finished; proceeding.")
            return fd
        except BlockingIOError:
            if time.time() >= deadline:
                fd.close()
                return None
            if not announced:
                print(f"  [INFO] Another pipeline is rendering on this 1-core VM — "
                      f"waiting up to {max_wait_secs // 60} min for it to finish "
                      f"(instead of skipping today's video)...")
                announced = True
            time.sleep(30)


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


def _kill_process_group(p: "subprocess.Popen") -> None:
    """SIGKILL the child's entire process group, then reap it. subprocess's
    own timeout kills ONLY the direct child (the make_*_video.py python) —
    its ffmpeg grandchild survived as an orphan, kept rendering for HOURS,
    ate the single core (guaranteeing the retry also timed out), and pushed
    the box deep into swap. Seen live in production on 2026-07-13: a 9:00
    weeklyfull ffmpeg still in D-state at 15:21. start_new_session=True in
    the runners below puts the whole tree in one group so this kills all of it."""
    try:
        os.killpg(os.getpgid(p.pid), signal.SIGKILL)
    except Exception:
        try:
            p.kill()
        except Exception:
            pass
    try:
        p.wait(timeout=30)
    except Exception:
        pass


def run_captured(cmd: list, timeout: int = 120) -> tuple:
    p = None
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, start_new_session=True)
        out, err = p.communicate(timeout=timeout)
        return p.returncode == 0, (err or out).strip()
    except subprocess.TimeoutExpired:
        _kill_process_group(p)
        return False, f"TIMEOUT after {timeout}s (whole process group killed)"
    except KeyboardInterrupt:
        # start_new_session detaches the child from the terminal's process
        # group, so Ctrl-C alone would NOT reach it — kill the group before
        # propagating, or a manual abort would orphan the very ffmpeg the
        # session isolation was added to contain.
        if p is not None:
            _kill_process_group(p)
        raise
    except Exception as e:
        if p is not None:
            _kill_process_group(p)
        return False, str(e)


def _daemon(action: str) -> None:
    """The standalone `getmindfuelnow` daemon (core/horoscope_daemon.py) has been
    RETIRED — it was a second, independent uploader producing the old-format
    7-minute videos and per-sign shorts. This cron pipeline is now the single
    source of truth.

    So: NEVER (re)start it — a stray `start` would revive the rogue uploader.
    `stop` remains a harmless best-effort kill in case an old instance lingers,
    which also frees CPU/RAM for the render."""
    if action != "stop":
        return
    subprocess.run(["sudo", "systemctl", "stop", "getmindfuelnow"],
                   capture_output=True, text=True)


# Staggered publish hours (US Eastern, DST-aware) — one per content type.
# Publishing everything at the same instant floods subscribers' feeds and
# makes our own videos compete against each other in the algorithm's
# crucial first hour. Instead each type gets its own slot across the US
# morning: the daily Short lands first (prime "check my horoscope" time),
# then the rest follow hourly. Override per type with
# PUBLISH_ET_HOUR_<TYPE> (e.g. PUBLISH_ET_HOUR_TOPIC=12) or globally with
# PUBLISH_ET_HOUR / PUBLISH_HOUR_UTC in .env.
_PUBLISH_ET_HOURS = {
    "daily":       7,    # 7 AM ET — morning horoscope check
    "topic":       8,
    "prediction":  9,
    "sports":      9,    # legacy long-form (not on cron)
    "weekly":     10,    # Sundays
    "weeklyfull": 10,    # Mondays
    "tarotweekly":10,    # Saturdays
    "monthly":    11,    # 1st of the month
}


def _publish_utc_dt(date_tag: str, ctype: str = "daily") -> datetime:
    """UTC datetime at which the video should go public, targeting a real
    US-morning hour so it's live when the US audience wakes up.

    Default: the per-type hour from _PUBLISH_ET_HOURS (US Eastern, DST-AWARE —
    stays fixed ET year-round instead of drifting between EDT and EST).
    Overrides: PUBLISH_ET_HOUR_<TYPE> > PUBLISH_ET_HOUR > per-type default;
    a fixed PUBLISH_HOUR_UTC in .env trumps everything (advanced use)."""
    d = datetime.strptime(date_tag, "%Y%m%d")

    if os.environ.get("PUBLISH_HOUR_UTC"):
        return d.replace(hour=int(os.environ["PUBLISH_HOUR_UTC"]), minute=0, second=0)

    et_hour = int(
        os.environ.get(f"PUBLISH_ET_HOUR_{ctype.upper()}")
        or os.environ.get("PUBLISH_ET_HOUR")
        or _PUBLISH_ET_HOURS.get(ctype, 7))
    try:
        from zoneinfo import ZoneInfo
        et = datetime(d.year, d.month, d.day, et_hour, 0, tzinfo=ZoneInfo("America/New_York"))
        return et.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    except Exception:
        # Manual US DST fallback: EDT (UTC-4) from 2nd Sun Mar to 1st Sun Nov,
        # else EST (UTC-5). tzdata-free, deterministic.
        mar1 = datetime(d.year, 3, 1)
        mar_2nd_sun = mar1 + timedelta(days=((6 - mar1.weekday()) % 7) + 7)
        nov1 = datetime(d.year, 11, 1)
        nov_1st_sun = nov1 + timedelta(days=(6 - nov1.weekday()) % 7)
        offset = 4 if (mar_2nd_sun <= d < nov_1st_sun) else 5
        return d.replace(hour=et_hour, minute=0, second=0) + timedelta(hours=offset)


def run_live(cmd: list, timeout: int = 1800) -> bool:
    p = None
    try:
        p = subprocess.Popen(cmd, start_new_session=True)
        return p.wait(timeout=timeout) == 0
    except subprocess.TimeoutExpired:
        # Kill the WHOLE process group (renderer python + its ffmpeg children)
        # — see _kill_process_group. Killing only the python used to orphan a
        # still-rendering ffmpeg that then starved the retry of the one core.
        _kill_process_group(p)
        print(f"  [TIMEOUT after {timeout}s — whole render process group killed]")
        return False
    except KeyboardInterrupt:
        # Ctrl-C doesn't reach the detached session — kill it before exiting
        # (same orphan risk as a timeout, just triggered manually).
        if p is not None:
            _kill_process_group(p)
        raise
    except Exception as e:
        if p is not None:
            _kill_process_group(p)
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
        f"Result   : {status}",
        # "Duration" alone read as the VIDEO's length — it is the pipeline's
        # wall-clock run time (assets + render + QC + upload), so say so.
        f"Run time : {elapsed // 60}m {elapsed % 60}s (pipeline total, not video length)",
        f"Finished : {_utcnow():%Y-%m-%d %H:%M UTC}",
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


def _upload_flow(video_path: str, assets_json: str, thumb_path: str,
                 run_key: str, args) -> str:
    """Shared upload → thumbnail → playlist → queue-comment → verify flow.
    Returns an upload_result string ('✅ ...' or '❌ ...'). Used by every
    pipeline type (daily/weekly/monthly/topic)."""
    try:
        sys.path.insert(0, str(Path(__file__).parent))
        from youtube_uploader import (upload_video, upload_thumbnail,
                                      queue_comment, process_pending_comments,
                                      process_retry_queue, find_uploaded,
                                      verify_upload, add_to_playlist,
                                      quota_spent_today, DAILY_QUOTA_LIMIT)
        import json as _json

        # Housekeeping — runs even if today is a duplicate-skip.
        try:
            retried = process_retry_queue()
            if retried:
                print(f"      [INFO] Uploaded {retried} queued video(s) from prior days")
            posted = process_pending_comments()
            if posted:
                print(f"      [INFO] Posted {posted} pending comment(s)")
        except Exception as _qe:
            print(f"      [INFO] Housekeeping skipped: {_qe}")

        # Idempotency: never post a duplicate for the same (type,date).
        existing = find_uploaded(run_key)
        if existing and not args.force:
            print(f"      SKIP: {run_key} already uploaded → "
                  f"https://youtu.be/{existing} (use --force to re-upload)")
            return f"✅ youtu.be/{existing} (existing)"

        print(f"      Quota used today: {quota_spent_today()}/{DAILY_QUOTA_LIMIT} units")
        assets = _json.loads(Path(assets_json).read_text(encoding="utf-8"))
        content = {
            "title":          assets.get("title", ""),
            "description":    assets.get("description", ""),
            "tags":           assets.get("tags", []),
            "date":           run_key,
            "privacy_status": "public",
        }
        # Publish at this type's staggered US-morning slot (DST-aware, see
        # _PUBLISH_ET_HOURS); if that moment already passed, schedule ASAP.
        pub_dt = _publish_utc_dt(args.date, getattr(args, "type", "daily") or "daily")
        if pub_dt <= _utcnow():
            pub_dt = _utcnow() + timedelta(minutes=15)
        publish_at = pub_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        vid_id = upload_video(video_path, content, publish_at=publish_at)
        upload_thumbnail(vid_id, thumb_path)
        add_to_playlist(vid_id)   # no-op unless YOUTUBE_PLAYLIST_ID set

        comment_after = (pub_dt + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
        queue_comment(vid_id, assets.get("pinned_comment", ""), comment_after)
        print(f"      Comment queued for {comment_after}")

        v_ok, v_detail = verify_upload(vid_id, expect_publish_at=publish_at)
        print(f"      Verify: {'OK' if v_ok else 'PROBLEM'} — {v_detail}")
        print(f"      OK: https://youtu.be/{vid_id} — publishes {publish_at}")
        return (f"✅ youtu.be/{vid_id}" if v_ok
                else f"❌ uploaded but {v_detail[:60]} (youtu.be/{vid_id})")
    except Exception as _e:
        print(f"      FAILED: {_e}")
        return f"❌ {str(_e)[:80]}"


def run_topic_pipeline(args) -> int:
    """Daily LONG-FORM astrology TOPIC video (for monetization).
    generate_topic_assets.py → make_topic_video.py → upload."""
    t_start = time.time()
    base       = f"topic_{args.date}"
    json_file  = f"{base}.json"
    out_base   = f"outputs/{args.date}/TopicAll/{base}"
    video_path, assets_json, thumb_path = (f"{out_base}.mp4",
                                           f"{out_base}_assets.json",
                                           f"{out_base}_thumbnail.jpg")
    run_key = f"topic_{args.date}"

    print(f"\n{'='*60}\n  GetMindFuelNow — TOPIC (long-form) — {args.date}\n"
          f"  Started: {datetime.now():%H:%M:%S}\n{'='*60}\n")

    if not args.skip_doctor:
        try:
            import doctor
            healthy, lines = doctor.preflight(deep=False)
            print("[0/4] Preflight:"); print("\n".join(lines))
            if not healthy:
                doctor._email_report(False, lines); return 1
        except Exception as _de:
            print(f"  [WARN] Preflight skipped: {_de}")

    _daemon("stop")
    assets_ok = qc_ok = False
    upload_result = ""
    try:
        # 1. Topic script
        if args.skip_assets and Path(json_file).exists():
            print(f"[1/4] Assets   — reusing {json_file}"); assets_ok = True
        else:
            print(f"[1/4] Assets   — writing topic script via Claude...")
            ok, out = run_captured([PYTHON, "generate_topic_assets.py", args.period, args.date],
                                   timeout=300)
            assets_ok = ok
            print(f"      {'OK' if ok else 'FAILED: ' + out[-200:]}")
        if not assets_ok:
            send_summary_email(args.date, {"topic": {"assets": "❌ script failed",
                "video": "—", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 2. Render (long-form → generous timeout for the e2-micro)
        print(f"\n[2/4] Video    — rendering long-form topic video...")
        ok = run_live([PYTHON, "make_topic_video.py", json_file], timeout=3600)
        if not ok:
            print("      FAILED — retry in 60s...", file=sys.stderr); time.sleep(60)
            ok = run_live([PYTHON, "make_topic_video.py", json_file], timeout=3600)
        if not ok:
            print("      FAILED (after retry)", file=sys.stderr)
            send_summary_email(args.date, {"topic": {"assets": "✅",
                "video": "❌ render failed", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 3. QC
        print(f"\n[3/4] QC       — checking {video_path}...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=300)
        qc_ok = ok
        print(f"      {'PASS' if ok else 'FAIL: ' + out}")

        # 4. Upload
        if args.upload and qc_ok:
            print(f"\n[4/4] Upload   — uploading to YouTube...")
            upload_result = _upload_flow(video_path, assets_json, thumb_path, run_key, args)
    finally:
        _daemon("start")

    elapsed   = int(time.time() - t_start)
    upload_ok = (not args.upload) or upload_result.startswith("✅")
    all_ok    = assets_ok and qc_ok and upload_ok
    print(f"\n{'='*60}\n  SUMMARY — TOPIC {args.date}  ({elapsed//60}m {elapsed%60}s)\n"
          f"  {'✅ ALL OK' if all_ok else '❌ ISSUES'}"
          + (f"  |  {upload_result}" if upload_result else "") + f"\n{'='*60}\n")
    if all_ok:
        try:
            import heartbeat; heartbeat.record_success()
        except Exception:
            pass
    send_summary_email(args.date, {"topic": {
        "assets": "✅" if assets_ok else "❌", "video": "✅" if qc_ok else "❌",
        "quality": "✅" if qc_ok else "❌", "upload": upload_result}},
        elapsed, args.upload)
    return 0 if all_ok else 1


def run_sports_pipeline(args) -> int:
    """Daily LONG-FORM Sports Astrology Predictions video (for monetization).
    sports_data.py (match fetch) → generate_sports_astrology_assets.py
    (real chart + Claude script) → make_sports_video.py → upload.

    Unlike every other content type here, this one has a real external
    dependency (today's matches) that can legitimately be empty — no major
    fixtures that day is not a pipeline failure, so that case exits 0 and
    just skips the video rather than alerting like a broken run would."""
    t_start = time.time()
    base       = f"sports_{args.date}"
    json_file  = f"{base}.json"
    out_base   = f"outputs/{args.date}/SportsAll/{base}"
    video_path, assets_json, thumb_path = (f"{out_base}.mp4",
                                           f"{out_base}_assets.json",
                                           f"{out_base}_thumbnail.jpg")
    run_key = f"sports_{args.date}"

    print(f"\n{'='*60}\n  GetMindFuelNow — SPORTS ASTROLOGY (long-form) — {args.date}\n"
          f"  Started: {datetime.now():%H:%M:%S}\n{'='*60}\n")

    if not args.skip_doctor:
        try:
            import doctor
            healthy, lines = doctor.preflight(deep=False)
            print("[0/4] Preflight:"); print("\n".join(lines))
            if not healthy:
                doctor._email_report(False, lines); return 1
        except Exception as _de:
            print(f"  [WARN] Preflight skipped: {_de}")

    _daemon("stop")
    assets_ok = qc_ok = False
    upload_result = ""
    try:
        # 1. Match data + real astrology chart + Claude script
        if args.skip_assets and Path(json_file).exists():
            print(f"[1/4] Assets   — reusing {json_file}"); assets_ok = True
        else:
            print(f"[1/4] Assets   — fetching matches + writing predictions via Claude...")
            ok, out = run_captured([PYTHON, "generate_sports_astrology_assets.py", args.date],
                                   timeout=300)
            assets_ok = ok
            print(f"      {'OK' if ok else 'FAILED: ' + out[-300:]}")
        if not assets_ok:
            # A "no matches today" run is a legitimate skip, not a pipeline
            # failure — generate_sports_astrology_assets.py's own error text
            # says so explicitly when that's the cause; anything else is a
            # real failure and should alert like one.
            if "no matches" in out.lower():
                print("      [INFO] No matches today — skipping sports video (not an error).")
                return 0
            send_summary_email(args.date, {"sports": {"assets": "❌ script failed",
                "video": "—", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 2. Render (long-form → generous timeout for the e2-micro)
        print(f"\n[2/4] Video    — rendering long-form sports astrology video...")
        ok = run_live([PYTHON, "make_sports_video.py", json_file], timeout=3600)
        if not ok:
            print("      FAILED — retry in 60s...", file=sys.stderr); time.sleep(60)
            ok = run_live([PYTHON, "make_sports_video.py", json_file], timeout=3600)
        if not ok:
            print("      FAILED (after retry)", file=sys.stderr)
            send_summary_email(args.date, {"sports": {"assets": "✅",
                "video": "❌ render failed", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 3. QC
        print(f"\n[3/4] QC       — checking {video_path}...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=300)
        qc_ok = ok
        print(f"      {'PASS' if ok else 'FAIL: ' + out}")

        # 4. Upload
        if args.upload and qc_ok:
            print(f"\n[4/4] Upload   — uploading to YouTube...")
            upload_result = _upload_flow(video_path, assets_json, thumb_path, run_key, args)
    finally:
        _daemon("start")

    elapsed   = int(time.time() - t_start)
    upload_ok = (not args.upload) or upload_result.startswith("✅")
    all_ok    = assets_ok and qc_ok and upload_ok
    print(f"\n{'='*60}\n  SUMMARY — SPORTS {args.date}  ({elapsed//60}m {elapsed%60}s)\n"
          f"  {'✅ ALL OK' if all_ok else '❌ ISSUES'}"
          + (f"  |  {upload_result}" if upload_result else "") + f"\n{'='*60}\n")
    if all_ok:
        try:
            import heartbeat; heartbeat.record_success()
        except Exception:
            pass
    send_summary_email(args.date, {"sports": {
        "assets": "✅" if assets_ok else "❌", "video": "✅" if qc_ok else "❌",
        "quality": "✅" if qc_ok else "❌", "upload": upload_result}},
        elapsed, args.upload)
    return 0 if all_ok else 1


def run_prediction_pipeline(args) -> int:
    """Daily SHORT (~90s) LANDSCAPE astrology PREDICTION video for one category
    (sports / crypto / political / celebrity). generate_prediction_assets.py
    (real chart + safe Claude script) → make_prediction_video.py (stock images
    + captions) → upload.

    Like sports, this can legitimately produce nothing (e.g. sports with no
    matches today) — that exits 0 as a clean skip, not a failure alert."""
    t_start = time.time()
    cat        = args.category
    base       = f"prediction_{cat}_{args.date}"
    json_file  = f"{base}.json"
    out_base   = f"outputs/{args.date}/Prediction_{cat}/{base}"
    video_path, assets_json, thumb_path = (f"{out_base}.mp4",
                                           f"{out_base}_assets.json",
                                           f"{out_base}_thumbnail.jpg")
    run_key = f"prediction_{cat}_{args.date}"

    print(f"\n{'='*60}\n  GetMindFuelNow — PREDICTION [{cat}] (90s landscape) — {args.date}\n"
          f"  Started: {datetime.now():%H:%M:%S}\n{'='*60}\n")

    if not args.skip_doctor:
        try:
            import doctor
            healthy, lines = doctor.preflight(deep=False)
            print("[0/4] Preflight:"); print("\n".join(lines))
            if not healthy:
                doctor._email_report(False, lines); return 1
        except Exception as _de:
            print(f"  [WARN] Preflight skipped: {_de}")

    _daemon("stop")
    assets_ok = qc_ok = False
    upload_result = ""
    out = ""
    try:
        # 1. Real chart + safe Claude script (+ match fetch for sports)
        if args.skip_assets and Path(json_file).exists():
            print(f"[1/4] Assets   — reusing {json_file}"); assets_ok = True
        else:
            print(f"[1/4] Assets   — writing {cat} prediction via Claude...")
            ok, out = run_captured([PYTHON, "generate_prediction_assets.py", cat, args.date],
                                   timeout=300)
            assets_ok = ok
            print(f"      {'OK' if ok else 'FAILED: ' + out[-300:]}")
        if not assets_ok:
            if "no matches" in out.lower():
                print(f"      [INFO] No matches today — skipping {cat} prediction (not an error).")
                return 0
            send_summary_email(args.date, {f"pred-{cat}": {"assets": "❌ script failed",
                "video": "—", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 2. Render (short landscape → modest timeout)
        print(f"\n[2/4] Video    — rendering 90s landscape {cat} prediction...")
        ok = run_live([PYTHON, "make_prediction_video.py", json_file], timeout=1800)
        if not ok:
            print("      FAILED — retry in 60s...", file=sys.stderr); time.sleep(60)
            ok = run_live([PYTHON, "make_prediction_video.py", json_file], timeout=1800)
        if not ok:
            print("      FAILED (after retry)", file=sys.stderr)
            send_summary_email(args.date, {f"pred-{cat}": {"assets": "✅",
                "video": "❌ render failed", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 3. QC
        print(f"\n[3/4] QC       — checking {video_path}...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=300)
        qc_ok = ok
        print(f"      {'PASS' if ok else 'FAIL: ' + out}")

        # 4. Upload
        if args.upload and qc_ok:
            print(f"\n[4/4] Upload   — uploading to YouTube...")
            upload_result = _upload_flow(video_path, assets_json, thumb_path, run_key, args)
    finally:
        _daemon("start")

    elapsed   = int(time.time() - t_start)
    upload_ok = (not args.upload) or upload_result.startswith("✅")
    all_ok    = assets_ok and qc_ok and upload_ok
    print(f"\n{'='*60}\n  SUMMARY — PREDICTION [{cat}] {args.date}  ({elapsed//60}m {elapsed%60}s)\n"
          f"  {'✅ ALL OK' if all_ok else '❌ ISSUES'}"
          + (f"  |  {upload_result}" if upload_result else "") + f"\n{'='*60}\n")
    if all_ok:
        try:
            import heartbeat; heartbeat.record_success()
        except Exception:
            pass
    send_summary_email(args.date, {f"pred-{cat}": {
        "assets": "✅" if assets_ok else "❌", "video": "✅" if qc_ok else "❌",
        "quality": "✅" if qc_ok else "❌", "upload": upload_result}},
        elapsed, args.upload)
    return 0 if all_ok else 1


def run_tarot_pipeline(args) -> int:
    """Weekly LONG-FORM tarot reading (one Major Arcana card per sign, real
    public-domain RWS card imagery). generate_tarot_assets.py →
    make_tarot_video.py → upload. Same shape as the topic pipeline."""
    t_start = time.time()
    base       = f"tarotweekly_{args.date}"
    json_file  = f"{base}.json"
    out_base   = f"outputs/{args.date}/TarotAll/{base}"
    video_path, assets_json, thumb_path = (f"{out_base}.mp4",
                                           f"{out_base}_assets.json",
                                           f"{out_base}_thumbnail.jpg")
    run_key = f"tarotweekly_{args.date}"

    print(f"\n{'='*60}\n  GetMindFuelNow — WEEKLY TAROT (long-form) — {args.date}\n"
          f"  Started: {datetime.now():%H:%M:%S}\n{'='*60}\n")

    if not args.skip_doctor:
        try:
            import doctor
            healthy, lines = doctor.preflight(deep=False)
            print("[0/4] Preflight:"); print("\n".join(lines))
            if not healthy:
                doctor._email_report(False, lines); return 1
        except Exception as _de:
            print(f"  [WARN] Preflight skipped: {_de}")

    _daemon("stop")
    assets_ok = qc_ok = False
    upload_result = ""
    try:
        # 1. Weekly card draw + Claude readings
        if args.skip_assets and Path(json_file).exists():
            print(f"[1/4] Assets   — reusing {json_file}"); assets_ok = True
        else:
            print(f"[1/4] Assets   — drawing cards + writing readings via Claude...")
            ok, out = run_captured([PYTHON, "generate_tarot_assets.py", args.date],
                                   timeout=300)
            assets_ok = ok
            print(f"      {'OK' if ok else 'FAILED: ' + out[-300:]}")
        if not assets_ok:
            send_summary_email(args.date, {"tarot": {"assets": "❌ script failed",
                "video": "—", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 2. Render (long-form → generous timeout for the e2-micro)
        print(f"\n[2/4] Video    — rendering weekly tarot video...")
        ok = run_live([PYTHON, "make_tarot_video.py", json_file], timeout=3600)
        if not ok:
            print("      FAILED — retry in 60s...", file=sys.stderr); time.sleep(60)
            ok = run_live([PYTHON, "make_tarot_video.py", json_file], timeout=3600)
        if not ok:
            print("      FAILED (after retry)", file=sys.stderr)
            send_summary_email(args.date, {"tarot": {"assets": "✅",
                "video": "❌ render failed", "quality": "—", "upload": ""}},
                int(time.time() - t_start), args.upload)
            return 1

        # 3. QC
        print(f"\n[3/4] QC       — checking {video_path}...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=300)
        qc_ok = ok
        print(f"      {'PASS' if ok else 'FAIL: ' + out}")

        # 4. Upload
        if args.upload and qc_ok:
            print(f"\n[4/4] Upload   — uploading to YouTube...")
            upload_result = _upload_flow(video_path, assets_json, thumb_path, run_key, args)
    finally:
        _daemon("start")

    elapsed   = int(time.time() - t_start)
    upload_ok = (not args.upload) or upload_result.startswith("✅")
    all_ok    = assets_ok and qc_ok and upload_ok
    print(f"\n{'='*60}\n  SUMMARY — TAROT {args.date}  ({elapsed//60}m {elapsed%60}s)\n"
          f"  {'✅ ALL OK' if all_ok else '❌ ISSUES'}"
          + (f"  |  {upload_result}" if upload_result else "") + f"\n{'='*60}\n")
    if all_ok:
        try:
            import heartbeat; heartbeat.record_success()
        except Exception:
            pass
    send_summary_email(args.date, {"tarot": {
        "assets": "✅" if assets_ok else "❌", "video": "✅" if qc_ok else "❌",
        "quality": "✅" if qc_ok else "❌", "upload": upload_result}},
        elapsed, args.upload)
    return 0 if all_ok else 1


def run_all_signs_pipeline(args) -> int:
    """
    New pipeline: one combined video covering all 12 signs, for any timeframe
    (daily / weekly / monthly). generate_daily_assets.py → make_daily_video.py
    → upload (optional). Returns exit code (0 = success).
    """
    t_start = time.time()

    ctype    = getattr(args, "type", "daily") or "daily"
    type_dir = f"{ctype.capitalize()}All"
    base     = f"{ctype}_horoscope_{args.date}"
    json_file  = f"{base}.json"
    out_base   = f"outputs/{args.date}/{type_dir}/{base}"
    video_path  = f"{out_base}.mp4"
    assets_json = f"{out_base}_assets.json"
    thumb_path  = f"{out_base}_thumbnail.jpg"
    # Idempotency key: daily keeps the bare date (backward-compatible with the
    # existing uploads.json), weekly/monthly are namespaced so a same-day daily
    # and weekly don't collide.
    run_key = args.date if ctype == "daily" else f"{ctype}_{args.date}"

    print(f"\n{'='*60}")
    print(f"  GetMindFuelNow — {ctype.upper()} — ALL 12 SIGNS — {args.date}")
    print(f"  Period: {args.period}  |  Started: {datetime.now():%H:%M:%S}")
    print(f"{'='*60}\n")

    # ── Preflight: catch config/token/dep problems BEFORE wasting a render ──────
    if not args.skip_doctor:
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            import doctor
            healthy, lines = doctor.preflight(deep=False)
            print("[0/4] Preflight health check:")
            print("\n".join(lines))
            if not healthy:
                print("\n[ABORT] Preflight found critical issues — not rendering.", file=sys.stderr)
                doctor._email_report(False, lines)
                return 1
            print("      OK — healthy\n")
        except Exception as _de:
            print(f"  [WARN] Preflight check skipped: {_de}")

    _daemon("stop")
    print("  [INFO] Daemon paused for rendering\n")

    assets_ok = qc_ok = False
    upload_result = ""
    try:
        # ── 1. Generate all-signs assets JSON ──────────────────────────────────
        if args.skip_assets and Path(json_file).exists():
            print(f"[1/4] Assets   — reusing {json_file}")
            assets_ok = True
        else:
            print(f"[1/4] Assets   — generating all 12 signs ({ctype}) via Claude...")
            ok, out = run_captured(
                [PYTHON, "generate_daily_assets.py", args.period, args.date, ctype],
                timeout=300,
            )
            assets_ok = ok
            if ok:
                print(f"      OK → {json_file}")
            else:
                print(f"      FAILED: {out[-200:]}", file=sys.stderr)

        if not assets_ok:
            # The most likely failure stages must still email — otherwise the
            # owner's primary "did it work" signal never fires exactly when it
            # matters most.
            send_summary_email(args.date, {"all_signs": {
                "assets": "❌ generation failed", "video": "—", "quality": "—",
                "upload": "",
            }}, int(time.time() - t_start), args.upload)
            return 1

        # ── 2. Render slideshow video (retry once — transient ffmpeg/OOM/network
        #       hiccups shouldn't kill the whole day) ─────────────────────────────
        print(f"\n[2/4] Video    — rendering slideshow...")
        # 45 min per attempt: the e2-micro's sustained CPU is a fraction of a
        # core, and the higher-quality encoder needs headroom. Even worst case
        # (2 x 45 min) finishes well before the 10:00 UTC publish.
        ok = run_live([PYTHON, "make_daily_video.py", json_file], timeout=2700)
        if not ok:
            print("      FAILED — retrying once in 60s...", file=sys.stderr)
            time.sleep(60)
            ok = run_live([PYTHON, "make_daily_video.py", json_file], timeout=2700)
        if not ok:
            print("      FAILED (after retry)", file=sys.stderr)
            send_summary_email(args.date, {"all_signs": {
                "assets": "✅", "video": "❌ render failed (after retry)",
                "quality": "—", "upload": "",
            }}, int(time.time() - t_start), args.upload)
            return 1
        print("      OK")

        # ── 3. Quality check ───────────────────────────────────────────────────
        # 300s: the silence/sync checks decode the full audio track, which
        # takes >60s on the e2-micro's throttled CPU (timed out 2026-07-04).
        print(f"\n[3/4] QC       — checking {video_path}...")
        ok, out = run_captured([PYTHON, "quality_check.py", video_path], timeout=300)
        qc_ok = ok
        print(f"      {'PASS' if ok else 'FAIL'}" + (f": {out}" if not ok else ""))

        # ── 4. Upload ──────────────────────────────────────────────────────────
        if args.upload and qc_ok:
            print(f"\n[4/4] Upload   — uploading to YouTube...")
            upload_result = _upload_flow(video_path, assets_json, thumb_path, run_key, args)
    finally:
        _daemon("start")
        print("  [INFO] Daemon restarted\n")

    elapsed   = int(time.time() - t_start)
    upload_ok = (not args.upload) or upload_result.startswith("✅")
    all_ok    = assets_ok and qc_ok and upload_ok

    print(f"\n{'='*60}")
    print(f"  SUMMARY — {ctype.upper()} {args.date}  ({elapsed // 60}m {elapsed % 60}s)")
    print(f"  {'✅ ALL OK' if all_ok else '❌ ISSUES'}")
    if upload_result:
        print(f"  Upload: {upload_result}")
    print(f"  Output: outputs/{args.date}/{type_dir}/")
    print(f"{'='*60}\n")

    if all_ok:
        # Heartbeat: stamp success + ping the external healthcheck (if set)
        # so the dead-man's switch knows today's run happened.
        try:
            import heartbeat
            heartbeat.record_success()
        except Exception as _he:
            print(f"  [INFO] Heartbeat stamp skipped: {_he}")
        try:
            _prune_old_outputs()
        except Exception as _pe:
            print(f"  [INFO] Output pruning skipped: {_pe}")

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


def _prune_old_outputs(keep_days: int = 5) -> None:
    """Delete outputs/<date>/ dirs and stale per-day asset JSONs older than
    keep_days so the 20 GB disk never silently fills up.

    keep_days was 14 when the pipeline produced ~35 MB/day (one Short). The
    channel now renders ~250 MB/day (daily short + topic long-form + 3-4
    landscape predictions) plus a ~110 MB weekly tarot — a 14-day window is
    a ~4 GB steady state, roughly ALL the disk's remaining free space
    (observed live: 76.4% → 78.9% in three days). Videos are uploaded the
    same day they render; local copies exist only for debugging, so 5 days
    is plenty."""
    import shutil as _sh
    cutoff = (_utcnow() - timedelta(days=keep_days)).strftime("%Y%m%d")
    out_root = Path("outputs")
    if out_root.is_dir():
        for d in out_root.iterdir():
            if d.is_dir() and d.name.isdigit() and d.name < cutoff:
                _sh.rmtree(d, ignore_errors=True)
    # Every per-day asset JSON the generators drop in the repo dir (tiny,
    # but they accumulate forever otherwise).
    for pattern in ("daily_horoscope_*.json", "weekly_horoscope_*.json",
                    "monthly_horoscope_*.json", "weeklyfull_horoscope_*.json",
                    "topic_*.json", "sports_*.json", "prediction_*.json",
                    "tarotweekly_*.json"):
        for f in Path(".").glob(pattern):
            tag = f.stem.split("_")[-1]
            if tag.isdigit() and len(tag) == 8 and tag < cutoff:
                f.unlink(missing_ok=True)


def main():
    # Anchor to the script's directory: every relative path in the pipeline
    # (.env, youtube_token.json, logs/, outputs/) assumes it. Cron does
    # `cd $REPO` already; this makes manual runs from anywhere safe too.
    os.chdir(Path(__file__).parent)

    parser = argparse.ArgumentParser(description="Daily GetMindFuelNow Shorts pipeline")
    parser.add_argument("--date",        required=True, metavar="YYYYMMDD")
    parser.add_argument("--period",      required=True, help="e.g. 'June 2026'")
    parser.add_argument("--mode",        default="all", choices=["all", "short"],
                        help="'all' = one combined 12-sign video (default), "
                             "'short' = 12 separate per-sign videos")
    parser.add_argument("--type",        default="daily",
                        choices=["daily", "weekly", "monthly", "topic", "weeklyfull",
                                 "sports", "prediction", "tarotweekly"],
                        help="daily/weekly/monthly = combined 12-sign video; "
                             "topic = long-form astrology topic-of-the-day; "
                             "weeklyfull = long-form in-depth weekly horoscope, "
                             "Monday morning; sports = long-form daily sports "
                             "astrology predictions; prediction = short 90s "
                             "landscape prediction (needs --category); "
                             "tarotweekly = weekly all-signs tarot reading "
                             "(default: daily)")
    parser.add_argument("--category",    default="sports",
                        choices=["sports", "crypto", "political", "celebrity"],
                        help="For --type prediction: which prediction category "
                             "(default: sports)")
    parser.add_argument("--format",      default="short", choices=["short", "long"])
    parser.add_argument("--signs",       metavar="SIGN,...",
                        help="Comma-separated subset (default: all 12) — short mode only")
    parser.add_argument("--skip-assets", action="store_true",
                        help="Reuse existing JSON files instead of regenerating")
    parser.add_argument("--upload",      action="store_true",
                        help="Auto-upload completed videos to YouTube")
    parser.add_argument("--force",       action="store_true",
                        help="Re-upload even if a video for this date already exists")
    parser.add_argument("--skip-doctor",  action="store_true",
                        help="Skip the preflight health check")
    parser.add_argument("--tiktok",      action="store_true",
                        help="Also cross-post to TikTok")
    args = parser.parse_args()

    lock_fd = _acquire_pipeline_lock()
    if lock_fd is None:
        # We already WAITED (default 90 min) — a lock still held now means the
        # other render is pathologically stuck. Fail LOUDLY: exit 1 + email,
        # never a silent exit-0 that drops a day's video without a trace.
        msg = (f"Another run_daily.py held the render lock for the entire "
               f"wait window — giving up on this {getattr(args, 'type', 'daily')} "
               f"run. The other pipeline is likely stuck; check the log.")
        print(f"[ERROR] {msg}", file=sys.stderr)
        try:
            send_summary_email(args.date, {f"{args.type}_lock_timeout": {
                "assets": "❌ skipped — render lock held >90 min",
                "video": "—", "quality": "—", "upload": "",
            }}, 0, args.upload)
        except Exception:
            pass
        sys.exit(1)

    # ── Long-form daily astrology TOPIC video (monetization) ───────────────────
    if args.type == "topic":
        sys.exit(run_topic_pipeline(args))

    # ── Long-form daily SPORTS ASTROLOGY predictions (monetization) ────────────
    if args.type == "sports":
        sys.exit(run_sports_pipeline(args))

    # ── Short 90s LANDSCAPE prediction (sports/crypto/political/celebrity) ─────
    if args.type == "prediction":
        sys.exit(run_prediction_pipeline(args))

    # ── Weekly all-signs TAROT reading (long-form, real card imagery) ──────────
    if args.type == "tarotweekly":
        sys.exit(run_tarot_pipeline(args))

    # ── All-signs combined video (daily/weekly/monthly) ────────────────────────
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
