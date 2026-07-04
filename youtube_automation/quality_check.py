#!/usr/bin/env python3
"""
quality_check.py
Validates horoscope asset JSON and rendered video before upload.

Usage:
  python3 quality_check.py scorpio_short_20260616.json
  python3 quality_check.py outputs/20260616/Scorpio/scorpio_short_20260616.mp4
  python3 quality_check.py --all 20260616
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

SIGNS = [
    "aries", "taurus", "gemini", "cancer", "leo", "virgo",
    "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
]

REQUIRED_KEYS = {
    "hook_on_screen_text", "thumbnail_graphic_text", "script",
    "title", "description", "hashtags", "tags", "pinned_comment", "content_angle",
}


def check_assets(json_path: str) -> list:
    errors = []
    path = Path(json_path)

    if not path.exists():
        return [f"MISSING file: {json_path}"]

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return [f"Invalid JSON: {e}"]

    missing = REQUIRED_KEYS - set(data.keys())
    if missing:
        errors.append(f"Missing keys: {missing}")

    hook = data.get("hook_on_screen_text", "")
    if len(hook) > 25:
        errors.append(f"Hook too long ({len(hook)} chars, max 25): '{hook}'")
    hw = len(hook.split())
    if not (3 <= hw <= 5):
        errors.append(f"Hook word count {hw} (need 3–5): '{hook}'")

    thumb = data.get("thumbnail_graphic_text", "")
    tw = len(thumb.split())
    if not (2 <= tw <= 3):
        errors.append(f"Thumbnail word count {tw} (need 2–3): '{thumb}'")

    script = data.get("script", "")
    sw = len(script.split())
    if sw < 80:
        errors.append(f"Script too short: {sw} words (need ≥80)")
    elif sw > 200:
        errors.append(f"Script too long: {sw} words (need ≤200)")

    for bad in ["<speak>", "</speak>", "<break", "```", "**", "##"]:
        if bad in script:
            errors.append(f"Script contains forbidden tag/markdown: '{bad}'")

    title = data.get("title", "")
    if len(title) > 100:
        errors.append(f"Title too long: {len(title)} chars (max 100)")

    return errors


def _silence_errors(path, duration: float) -> list:
    """Use ffmpeg silencedetect to flag a track that is (almost) entirely silent
    — the 'uploaded but no audio' failure that otherwise ships unnoticed."""
    try:
        r = subprocess.run(
            ["ffmpeg", "-i", str(path), "-af",
             "silencedetect=noise=-50dB:d=2", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        log = r.stderr or ""
        total_silence = 0.0
        for line in log.splitlines():
            if "silence_duration:" in line:
                try:
                    total_silence += float(line.split("silence_duration:")[1].strip())
                except Exception:
                    pass
        # If >90% of the video is silence, the audio is effectively dead.
        if duration and total_silence > 0.90 * duration:
            return [f"Audio is {total_silence:.0f}s silent of {duration:.0f}s "
                    f"(track is effectively silent)"]
    except Exception:
        pass   # never block on the silence probe itself
    return []


def check_video(video_path: str) -> list:
    errors = []
    path = Path(video_path)

    if not path.exists():
        return [f"MISSING file: {video_path}"]

    size_mb = path.stat().st_size / 1_048_576
    if size_mb < 0.5:
        errors.append(f"Video too small: {size_mb:.1f} MB")
    elif size_mb > 150:
        errors.append(f"Video too large: {size_mb:.1f} MB (max 150 MB)")

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_streams", "-show_format", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode != 0:
            errors.append(f"ffprobe error: {result.stderr[:200]}")
        else:
            info   = json.loads(result.stdout)
            fmt    = info.get("format", {})
            streams = info.get("streams", [])

            duration = float(fmt.get("duration", 0))
            if duration < 30:
                errors.append(f"Too short: {duration:.1f}s (need ≥30s)")
            elif duration > 180:
                errors.append(f"Too long: {duration:.1f}s (need ≤180s)")

            vstreams = [s for s in streams if s.get("codec_type") == "video"]
            if not vstreams:
                errors.append("No video stream")
            else:
                w, h = vstreams[0].get("width", 0), vstreams[0].get("height", 0)
                if (w, h) != (1080, 1920):
                    errors.append(f"Wrong dimensions: {w}×{h} (need 1080×1920)")

            astreams = [s for s in streams if s.get("codec_type") == "audio"]
            if not astreams:
                errors.append("No audio stream")
            else:
                # Audio/video sync: the audio track must span the video, else the
                # voice drifts out of sync with the sign cards.
                a_dur = float(astreams[0].get("duration", 0) or fmt.get("duration", 0))
                if a_dur and abs(a_dur - duration) > 2.0:
                    errors.append(f"Audio/video desync: audio {a_dur:.1f}s vs video "
                                  f"{duration:.1f}s (>2s drift)")
                # Silence check: catch the 'uploaded but no voice/music' failure.
                errors += _silence_errors(path, duration)

    except subprocess.TimeoutExpired:
        errors.append("ffprobe timed out — VM may be under memory pressure")
    except Exception as e:
        errors.append(f"ffprobe exception: {e}")

    # Supporting files
    folder = path.parent
    is_all_signs = path.stem.startswith("daily_horoscope_")

    thumb = path.stem + "_thumbnail.jpg"
    if not (folder / thumb).exists():
        errors.append(f"Missing: {thumb}")

    if is_all_signs:
        assets_json = folder / (path.stem + "_assets.json")
        if not assets_json.exists():
            errors.append(f"Missing metadata: {path.stem}_assets.json")
    else:
        if not (folder / path.with_suffix(".srt").name).exists():
            errors.append(f"Missing: {path.with_suffix('.srt').name}")
        for meta in ["title.txt", "description.txt", "hashtags.txt",
                     "tags.txt", "pinned_comment.txt"]:
            if not (folder / meta).exists():
                errors.append(f"Missing metadata: {meta}")

    return errors


def _fmt(label: str, errors: list) -> bool:
    if not errors:
        print(f"  ✅  {label}")
        return True
    print(f"  ❌  {label}")
    for e in errors:
        print(f"       → {e}")
    return False


def main():
    parser = argparse.ArgumentParser(description="Quality check assets + videos")
    parser.add_argument("target", nargs="?", help="JSON or MP4 file to check")
    parser.add_argument("--all", metavar="YYYYMMDD",
                        help="Check all 12 signs for a date")
    args = parser.parse_args()

    if args.all:
        date = args.all
        passed = 0
        print(f"\n{'='*55}")
        print(f"  QUALITY CHECK — {date}")
        print(f"{'='*55}")
        for sign in SIGNS:
            json_path  = f"{sign}_short_{date}.json"
            video_path = f"outputs/{date}/{sign.title()}/{sign}_short_{date}.mp4"
            a_ok = _fmt(f"{sign.title():<14} assets", check_assets(json_path))
            v_ok = _fmt(f"{sign.title():<14} video",  check_video(video_path))
            if a_ok and v_ok:
                passed += 1
        print(f"\n  {passed}/12 passed")
        sys.exit(0 if passed == 12 else 1)

    elif args.target:
        t = args.target
        if t.endswith(".json"):
            errors = check_assets(t)
        elif t.endswith(".mp4"):
            errors = check_video(t)
        else:
            print(f"[ERROR] Pass a .json or .mp4 file", file=sys.stderr)
            sys.exit(1)
        ok = _fmt(t, errors)
        sys.exit(0 if ok else 1)

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
