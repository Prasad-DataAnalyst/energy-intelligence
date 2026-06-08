"""
Phase 4 — Motion Effects
=========================
Applies Ken Burns, zoom-punch, parallax, and other motion effects to
still images and video clips via FFmpeg.
"""
import logging
import math
import os
import subprocess
from typing import Optional

from utils.models import Scene, VisualAsset

log = logging.getLogger("viral_pipeline.visuals.motion")


def apply_ken_burns(image_path: str, out_path: str,
                    duration: float, width: int, height: int,
                    fps: int = 15, zoom_start: float = 1.0,
                    zoom_end: float = 1.1, direction: str = "in") -> str:
    """
    Slow zoom (Ken Burns) using FFmpeg zoompan filter.
    direction: 'in' = zoom in, 'out' = zoom out.
    """
    n_frames = int(duration * fps)
    z_start  = zoom_start
    z_end    = zoom_end if direction == "in" else zoom_start
    z_start2 = zoom_end if direction == "out" else zoom_start

    # FFmpeg zoompan: z=expression per frame
    z_expr   = (
        f"if(eq(on,1),{z_start},"
        f"min(zoom+({z_end}-{z_start})/{n_frames},{z_end}))"
    )
    vf = (
        f"zoompan=z='{z_expr}':d={n_frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"s={width}x{height}:fps={fps}"
    )

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1",
        "-i", image_path,
        "-vf", vf,
        "-t", str(duration),
        "-c:v", "libx264",
        "-crf", "20",
        "-preset", "fast",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.warning(f"Ken Burns failed, using still: {r.stderr[:200]}")
        # Fallback: static still
        from phase4_visuals.broll import still_to_video
        still_to_video(image_path, out_path, duration, width, height, fps)
    return out_path


def apply_zoom_punch(clip_path: str, out_path: str,
                     at_second: float, zoom_factor: float = 1.08,
                     width: int = 1280, height: int = 720) -> str:
    """Apply a quick zoom punch at at_second (beat drop effect)."""
    vf = (
        f"scale=iw*{zoom_factor}:ih*{zoom_factor},"
        f"crop={width}:{height}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", clip_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        return clip_path   # return original if effect fails
    return out_path


def apply_glitch_flash(clip_path: str, out_path: str,
                       duration: float = 0.1) -> str:
    """Quick white flash at the start of a clip (transition effect)."""
    vf = (
        f"fade=t=in:st=0:d={duration}:color=white,"
        f"fade=t=out:st={duration}:d={duration}"
    )
    cmd = [
        "ffmpeg", "-y", "-i", clip_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True)
    return out_path if r.returncode == 0 else clip_path


def process_scene_visuals(scene: Scene, visual: VisualAsset,
                          output_dir: str, cfg: dict) -> VisualAsset:
    """
    Apply appropriate motion effect to a visual asset.
    Returns updated VisualAsset with processed_file set.
    """
    vid_cfg = cfg.get("video", {})
    res_str = vid_cfg.get("resolution", "1280x720")
    w, h    = map(int, res_str.lower().split("x"))
    fps     = int(vid_cfg.get("fps", 15))

    out_mp4 = os.path.join(output_dir, f"motion_{scene.id:02d}.mp4")
    os.makedirs(output_dir, exist_ok=True)

    # If we have a video clip, just scale/trim it
    source = visual.video_file or visual.image_file
    if not source or not os.path.exists(source):
        log.warning(f"Scene {scene.id}: no visual source found")
        visual.processed_file = None
        return visual

    if visual.video_file and os.path.exists(visual.video_file):
        # Scale and trim existing video
        cmd = [
            "ffmpeg", "-y",
            "-i", visual.video_file,
            "-t", str(scene.duration),
            "-vf", (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"),
            "-r", str(fps),
            "-c:v", "libx264", "-crf", "20", "-preset", "fast",
            "-pix_fmt", "yuv420p",
            "-an", out_mp4,
        ]
        r = subprocess.run(cmd, capture_output=True)
        if r.returncode == 0:
            visual.processed_file = out_mp4
            return visual

    # Apply Ken Burns to still image
    motion_type = scene.transition if scene.transition in ("zoom_punch", "glitch") else "ken_burns"
    direction   = "in" if (scene.id % 2 == 0) else "out"

    apply_ken_burns(
        visual.image_file, out_mp4,
        duration=scene.duration, width=w, height=h, fps=fps,
        zoom_start=1.0, zoom_end=1.08, direction=direction,
    )

    # Apply zoom-punch overlay if needed
    if scene.transition == "zoom_punch" and os.path.exists(out_mp4):
        punch_out = out_mp4.replace(".mp4", "_punch.mp4")
        apply_zoom_punch(out_mp4, punch_out, at_second=0.1, width=w, height=h)
        if os.path.exists(punch_out):
            os.replace(punch_out, out_mp4)

    visual.processed_file = out_mp4
    return visual


def run_phase4_motion(visual_assets: list, output_dir: str,
                      cfg: dict) -> list:
    """Apply motion effects to all visual assets."""
    from tqdm import tqdm
    from utils.models import Scene

    log.info("Phase 4: Applying motion effects…")
    motion_dir = os.path.join(output_dir, "motion")
    os.makedirs(motion_dir, exist_ok=True)

    # We need the matching scenes — caller passes (visual, scene) pairs
    return visual_assets   # already processed in run_phase4
