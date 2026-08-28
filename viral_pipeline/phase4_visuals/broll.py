"""
Phase 4 — B-roll / Stock Footage Fetcher
==========================================
Fetches short video clips from Pexels or Pixabay that match each scene's
broll_description.  Falls back to a looped still image if no API key is set.
"""
import logging
import os
import re
import subprocess
from typing import Optional

from utils.api_client import get_session, download_file
from utils.models import Scene, VisualAsset

log = logging.getLogger("viral_pipeline.visuals.broll")

PEXELS_BASE   = "https://api.pexels.com/videos/search"
PIXABAY_BASE  = "https://pixabay.com/api/videos/"


def _sanitise_query(text: str) -> str:
    return re.sub(r"[^\w\s]", "", text).strip()[:100]


def fetch_pexels_clip(query: str, output_path: str,
                      min_w: int = 1280, min_h: int = 720,
                      duration_hint: float = 15) -> bool:
    api_key = os.getenv("PEXELS_API_KEY", "")
    if not api_key:
        return False
    try:
        sess = get_session()
        r = sess.get(
            PEXELS_BASE,
            params={"query": query, "per_page": 10,
                    "orientation": "landscape", "size": "large"},
            headers={"Authorization": api_key},
            timeout=20,
        )
        if not r.ok:
            return False
        vids = r.json().get("videos", [])
        if not vids:
            return False
        # Pick video closest to target duration
        best = min(vids, key=lambda v: abs(v.get("duration", 0) - duration_hint))
        # Find HD video file
        files = best.get("video_files", [])
        hd_files = [f for f in files
                    if f.get("width", 0) >= min_w and f.get("height", 0) >= min_h]
        if not hd_files:
            hd_files = files
        if not hd_files:
            return False
        url = hd_files[0]["link"]
        download_file(url, output_path)
        log.info(f"  Pexels clip: '{query}' → {os.path.getsize(output_path)//1024} KB")
        return True
    except Exception as e:
        log.warning(f"Pexels ({query}): {e}")
        return False


def fetch_pixabay_clip(query: str, output_path: str,
                       duration_hint: float = 15) -> bool:
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return False
    try:
        sess = get_session()
        r = sess.get(
            PIXABAY_BASE,
            params={"key": api_key, "q": query,
                    "video_type": "film", "per_page": 5,
                    "min_width": 1280},
            timeout=20,
        )
        if not r.ok:
            return False
        hits = r.json().get("hits", [])
        if not hits:
            return False
        best = min(hits, key=lambda h: abs(h.get("duration", 0) - duration_hint))
        url = (best.get("videos", {}).get("large", {}).get("url")
               or best.get("videos", {}).get("medium", {}).get("url", ""))
        if not url:
            return False
        download_file(url, output_path)
        log.info(f"  Pixabay clip: '{query}' → {os.path.getsize(output_path)//1024} KB")
        return True
    except Exception as e:
        log.warning(f"Pixabay ({query}): {e}")
        return False


def still_to_video(image_path: str, out_path: str,
                   duration: float, width: int = 1280,
                   height: int = 720, fps: int = 15) -> str:
    """Convert a static image to a short looping video clip."""
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-t", str(duration),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
               f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
        "-r", str(fps),
        "-c:v", "libx264",
        "-crf", "22",
        "-preset", "veryfast",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        log.error(f"still_to_video failed: {r.stderr[:200]}")
    return out_path


def get_broll(scene: Scene, visual_asset: VisualAsset,
              output_dir: str, cfg: dict) -> Optional[str]:
    """
    Attempt to fetch B-roll for a scene.
    Returns path to MP4 clip or None.
    """
    query   = _sanitise_query(scene.broll_description or scene.visual[:80])
    out_mp4 = os.path.join(output_dir, f"broll_{scene.id:02d}.mp4")
    dur     = scene.duration

    provider = cfg.get("visuals", {}).get("broll_provider", "pexels")

    if provider == "pexels":
        if fetch_pexels_clip(query, out_mp4, duration_hint=dur):
            return out_mp4
    elif provider == "pixabay":
        if fetch_pixabay_clip(query, out_mp4, duration_hint=dur):
            return out_mp4

    # Fallback: convert the generated still image to video
    if visual_asset.image_file and os.path.exists(visual_asset.image_file):
        vid_cfg = cfg.get("video", {})
        res_str = vid_cfg.get("resolution", "1280x720")
        w, h    = map(int, res_str.lower().split("x"))
        fps     = int(vid_cfg.get("fps", 15))
        still_to_video(visual_asset.image_file, out_mp4, dur, w, h, fps)
        return out_mp4

    return None
