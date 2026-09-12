"""
shorts_publisher.py
Extract highlight clips from a main video and upload as YouTube Shorts (≤60s, 9:16).
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import config
import youtube_uploader as uploader

log = logging.getLogger(__name__)

# Default short segments if script doesn't supply them:
# cold open, first value loop hook, climax hook, CTA hook
_DEFAULT_SEGMENTS = [
    {"label": "cold_open",    "start": 0,   "end": 55,  "title": "{title} — The Hook"},
    {"label": "loop1_hook",   "start": 55,  "end": 115, "title": "{title} — Key Insight"},
    {"label": "climax",       "start": 325, "end": 385, "title": "{title} — The Reveal"},
    {"label": "cta",          "start": 420, "end": 480, "title": "{title} — What To Do Now"},
]


def _ffprobe_duration(video_path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL, timeout=30)
        return float(out.strip())
    except Exception:
        return 0.0


def extract_short(
    video_path: str,
    start_s: float,
    end_s: float,
    out_path: str,
    target_h: int = 1920,
    target_w: int = 1080,
) -> str:
    """
    Cut [start_s, end_s] from video_path, centre-crop to 9:16, write to out_path.
    Returns out_path on success, raises on failure.
    """
    duration = min(end_s - start_s, 60.0)  # YouTube Shorts hard cap
    out_path = str(out_path)

    # Centre-crop to 9:16 then scale to 1080×1920
    vf = (
        f"crop=ih*9/16:ih,"
        f"scale={target_w}:{target_h}:flags=lanczos"
    )
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start_s),
        "-i", video_path,
        "-t", str(duration),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        out_path,
    ]
    log.info("Extracting short: %s → %s (%.1fs)", video_path, out_path, duration)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg short extract failed:\n{result.stderr[-1000:]}")
    return out_path


def upload_short(
    video_path: str,
    title: str,
    description: str,
    tags: Optional[list] = None,
) -> Optional[str]:
    """
    Upload video_path as a YouTube Short.
    Returns video_id on success, None on failure.
    """
    tags = tags or []
    # YouTube auto-classifies ≤60s 9:16 as Shorts; #Shorts tag reinforces it
    if "#Shorts" not in description:
        description = f"{description}\n\n#Shorts"
    if "Shorts" not in tags:
        tags = ["Shorts"] + tags

    content = {
        "title":       title[:100],
        "description": description[:5000],
        "tags":        tags[:500],
        "category_id": "28",  # Science & Technology
        "privacy":     "public",
    }
    try:
        video_id = uploader.upload_video(video_path, content)
        if video_id:
            log.info("Short uploaded: %s → %s", title, video_id)
        return video_id
    except Exception as exc:
        log.error("Short upload failed for %s: %s", title, exc)
        return None


def _segments_from_script(script) -> list:
    """Extract short segment timestamps from a CinematicScript object."""
    segments = []
    try:
        # If script has explicit short_segments attribute
        if hasattr(script, "short_segments") and script.short_segments:
            return script.short_segments
        # Otherwise derive from section timestamps
        sections = getattr(script, "sections", []) or []
        for s in sections:
            start = getattr(s, "start_time_s", None)
            end   = getattr(s, "end_time_s",   None)
            label = getattr(s, "label", "segment")
            if start is not None and end is not None and (end - start) <= 60:
                segments.append({
                    "label": label,
                    "start": float(start),
                    "end":   float(end),
                    "title": getattr(s, "hook_line", label),
                })
    except Exception as exc:
        log.warning("Could not extract segments from script: %s", exc)
    return segments


def publish_all_shorts(
    main_video_path: str,
    main_title: str,
    seo_package: Optional[dict] = None,
    script=None,
    work_dir: Optional[str] = None,
) -> list:
    """
    Orchestrate extraction and upload of all Shorts for a main video.
    Returns list of uploaded video IDs (may be empty if all failed).
    """
    work_dir = work_dir or str(Path(config.OUTPUT_DIR) / "shorts")
    Path(work_dir).mkdir(parents=True, exist_ok=True)

    seo_package = seo_package or {}
    base_title = seo_package.get("title", main_title) or main_title
    base_tags  = seo_package.get("tags", []) or []
    base_desc  = seo_package.get("description", "") or ""

    duration = _ffprobe_duration(main_video_path)
    if duration < 5:
        log.warning("Main video too short (%.1fs) to extract Shorts", duration)
        return []

    # Determine segments: script > seo_package > defaults
    if script is not None:
        segments = _segments_from_script(script)
    else:
        segments = seo_package.get("short_segments", [])

    if not segments:
        segments = [
            dict(seg) for seg in _DEFAULT_SEGMENTS
            if seg["end"] <= duration + 5
        ]

    uploaded_ids = []
    for i, seg in enumerate(segments[:5]):   # max 5 Shorts per video
        start = float(seg.get("start", 0))
        end   = min(float(seg.get("end", start + 59)), duration)
        if end - start < 5:
            continue

        label  = seg.get("label", f"short_{i+1}")
        s_title_tpl = seg.get("title", "{title} — Part {n}")
        s_title = (
            s_title_tpl
            .replace("{title}", base_title[:60])
            .replace("{n}", str(i + 1))
        )[:100]
        s_desc = f"{base_desc[:200]}\n\nFull video in bio.\n#Shorts".strip()

        out_path = Path(work_dir) / f"{label}.mp4"
        try:
            extract_short(main_video_path, start, end, str(out_path))
            vid_id = upload_short(str(out_path), s_title, s_desc, list(base_tags))
            if vid_id:
                uploaded_ids.append(vid_id)
                time.sleep(3)   # avoid quota burst
        except Exception as exc:
            log.error("Short %s failed: %s", label, exc)

    log.info("Shorts published: %d/%d", len(uploaded_ids), len(segments[:5]))
    return uploaded_ids
