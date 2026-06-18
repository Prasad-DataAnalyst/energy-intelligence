#!/usr/bin/env python3
"""
run_daily.py
Master daily runner: generate assets → render video → quality check, all 12 signs.

Usage:
  python3 run_daily.py --date 20260616 --period "June 2026" --format short
  python3 run_daily.py --date 20260617 --period "June 2026" --signs scorpio,leo
  python3 run_daily.py --date 20260616 --period "June 2026" --skip-assets
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PYTHON = sys.executable   # use same interpreter / venv as caller

SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]


def run_captured(cmd: list, timeout: int = 120) -> tuple:
    """Run command, capture output. Returns (success, output_text)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except subprocess.TimeoutExpired:
        return False, f"TIMEOUT after {timeout}s"
    except Exception as e:
        return False, str(e)


def run_live(cmd: list, timeout: int = 1200) -> bool:
    """Run command with live stdout (for long video rendering steps)."""
    try:
        r = subprocess.run(cmd, timeout=timeout)
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        print(f"  [TIMEOUT after {timeout}s]")
        return False
    except Exception as e:
        print(f"  [ERROR] {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Daily GetMindFuelNow Shorts pipeline"
    )
    parser.add_argument("--date",   required=True, metavar="YYYYMMDD")
    parser.add_argument("--period", required=True, help="e.g. 'June 2026'")
    parser.add_argument("--format", default="short", choices=["short", "long"])
    parser.add_argument("--signs",  metavar="SIGN,...",
                        help="Comma-separated subset (default: all 12)")
    parser.add_argument("--skip-assets", action="store_true",
                        help="Reuse existing JSON files instead of regenerating")
    parser.add_argument("--upload", action="store_true",
                        help="Auto-upload completed videos to YouTube")
    parser.add_argument("--tiktok", action="store_true",
                        help="Also cross-post completed videos to TikTok")
    args = parser.parse_args()

    signs = ([s.strip().lower() for s in args.signs.split(",")]
             if args.signs else SIGNS)

    print(f"\n{'='*60}")
    print(f"  GetMindFuelNow Daily Pipeline — {args.date}")
    print(f"  Period: {args.period}  |  Format: {args.format}")
    print(f"  Signs: {len(signs)}  |  Started: {datetime.now():%H:%M:%S}")
    print(f"{'='*60}\n")

    results  = {}
    t_start  = time.time()

    # Pause the daemon so video rendering gets full CPU + RAM
    subprocess.run(["sudo", "systemctl", "stop", "getmindfuelnow"],
                   capture_output=True)
    print("  [INFO] Daemon paused for rendering — will restart after all signs\n")

    for idx, sign in enumerate(signs, 1):
        print(f"\n[{idx:02d}/{len(signs):02d}] ── {sign.upper()} ──────────────────────────")
        r = {}

        # ── 1. Assets ─────────────────────────────────────────────────────────
        json_file = f"{sign}_short_{args.date}.json"
        if args.skip_assets and Path(json_file).exists():
            r["assets"] = "✅ existing"
            print(f"  [1/3] Assets   — reusing {json_file}")
        else:
            print(f"  [1/3] Assets   — generating...")
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
        print(f"  [2/3] Video    — rendering (live output below)...")
        ok = run_live(
            [PYTHON, "make_shorts_video.py", json_file],
            timeout=1800,
        )
        r["video"] = "✅" if ok else "❌ render failed"
        if not ok:
            results[sign] = r
            continue

        # ── 3. Quality check ──────────────────────────────────────────────────
        video_path = f"outputs/{args.date}/{sign.title()}/{sign}_short_{args.date}.mp4"
        print(f"  [3/3] QC       — checking {video_path}")
        ok, out = run_captured(
            [PYTHON, "quality_check.py", video_path],
            timeout=60,
        )
        r["quality"] = "✅" if ok else f"❌ {out[-120:]}"
        print(f"         {'PASS' if ok else 'FAIL'}" +
              (f"\n{out}" if not ok else ""))

        # ── 4. Upload (optional) ──────────────────────────────────────────────
        if args.upload and r.get("quality", "").startswith("✅"):
            print(f"  [4/4] Upload   — uploading to YouTube...")
            try:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent))
                from youtube_uploader import upload_video, upload_thumbnail, post_comment, pin_comment
                assets_json = f"outputs/{args.date}/{sign.title()}/{sign}_{args.date}_assets.json"
                assets      = json.loads(Path(assets_json).read_text(encoding="utf-8"))
                content     = {
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

        # ── 5. TikTok (optional) ─────────────────────────────────────────────
        if args.tiktok and r.get("quality", "").startswith("✅"):
            print(f"  [5/5] TikTok   — cross-posting...")
            try:
                import sys as _sys
                _sys.path.insert(0, str(Path(__file__).parent))
                from tiktok_uploader import upload_to_tiktok
                assets_json = f"outputs/{args.date}/{sign.title()}/{sign}_{args.date}_assets.json"
                assets      = json.loads(Path(assets_json).read_text(encoding="utf-8"))
                pub_id = upload_to_tiktok(
                    video_path,
                    assets.get("title", ""),
                    assets.get("hashtags", []),
                )
                r["tiktok"] = f"✅ {pub_id[:12]}"
                print(f"         OK: publish_id {pub_id}")
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
        a = r.get("assets",  "—")
        v = r.get("video",   "—")
        q = r.get("quality", "—")
        u = r.get("upload", "")
        all_ok = all(str(x).startswith("✅") for x in [a, v, q] if x)
        icon   = "✅" if all_ok else "❌"
        tt = r.get("tiktok", "")
        print(f"  {icon}  {sign.title():<14}  assets:{a}  video:{v}  qc:{q}" +
              (f"  yt:{u}"  if u  else "") +
              (f"  tt:{tt}" if tt else ""))
        if all_ok:
            passed += 1

    print(f"\n  {passed}/{len(signs)} signs complete")
    print(f"  Output: outputs/{args.date}/")
    print(f"{'='*60}\n")

    # Restart daemon now that rendering is done
    subprocess.run(["sudo", "systemctl", "start", "getmindfuelnow"],
                   capture_output=True)
    print("  [INFO] Daemon restarted\n")

    sys.exit(0 if passed == len(signs) else 1)


if __name__ == "__main__":
    main()
