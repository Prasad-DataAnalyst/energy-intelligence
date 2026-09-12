"""
Phase 5 — Video Assembler
==========================
Concatenates processed scene clips, adds text overlays (hook, lower-thirds,
progress bar, watermark), applies colour grade, and muxes with mixed audio.

All heavy lifting via FFmpeg filter graphs for reliability and speed.
"""
import json
import logging
import math
import os
import subprocess
import tempfile
import textwrap
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont
import numpy as np

from utils.models import Script, Scene, VisualAsset, AudioAsset

log = logging.getLogger("viral_pipeline.assembly")

# ── Colour grade LUTs (as FFmpeg curves) ─────────────────────────────────────
GRADE_CURVES = {
    "tech-dark":       "curves=r='0/0 0.5/0.45 1/0.9':g='0/0 0.5/0.5 1/0.95':b='0/0.05 0.5/0.55 1/1'",
    "vlog-warm":       "curves=r='0/0 0.5/0.55 1/1':g='0/0 0.5/0.5 1/0.95':b='0/0 0.5/0.45 1/0.85'",
    "news-clean":      "curves=r='0/0 0.5/0.5 1/1':g='0/0 0.5/0.5 1/1':b='0/0 0.5/0.5 1/1'",
    "motivation-epic": "curves=r='0/0 0.4/0.3 1/0.85':g='0/0 0.5/0.45 1/0.9':b='0/0.05 0.5/0.55 1/1'",
}

VIGNETTE_FILTER = "vignette=angle=PI/4:mode=forward"
GRAIN_FILTER    = "noise=alls=4:allf=t+u"


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    paths = (["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
               "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"]
              if bold else
              ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for p in paths:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _scale(px: int, w: int, ref: int = 1920) -> int:
    return max(10, int(px * w / ref))


# ── Per-scene text overlay via PIL (burned into frames during motion step) ───

def render_text_overlay(scene: Scene, width: int, height: int,
                        style: str, frame_t: float) -> Optional[Image.Image]:
    """
    Render scene text overlay onto a transparent PIL image.
    frame_t = 0.0 to 1.0 (position within scene).
    """
    if not scene.text_overlay:
        return None

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw    = ImageDraw.Draw(overlay)

    acc_colours = {
        "tech-dark":       (0, 212, 255),
        "vlog-warm":       (255, 170, 50),
        "news-clean":      (220, 40, 40),
        "motivation-epic": (180, 80, 255),
    }
    acc = acc_colours.get(style, (255, 255, 255))

    # Animate: slide up from bottom on entry (first 30% of scene)
    slide_max  = height * 0.05
    ease_t     = min(1.0, frame_t / 0.3)
    ease_val   = ease_t * ease_t * (3 - 2 * ease_t)
    slide_off  = int(slide_max * (1 - ease_val))

    text = scene.text_overlay
    fs   = _scale(56, width)
    font = _font(fs, bold=True)

    # Position: bottom third
    y = int(height * 0.78) + slide_off

    # Shadow
    draw.text((width // 2 + 3, y + 3), text, font=font,
              fill=(0, 0, 0, 180), anchor="mm")
    # Main text
    draw.text((width // 2, y), text, font=font,
              fill=(*acc, 230), anchor="mm")

    # Underline
    tw = draw.textlength(text, font=font)
    draw.rectangle([width // 2 - tw // 2, y + fs // 2 + 6,
                    width // 2 + tw // 2, y + fs // 2 + 10],
                   fill=(*acc, 200))
    return overlay


# ── FFmpeg scene clip builder ─────────────────────────────────────────────────

def build_scene_clip(scene: Scene, visual: VisualAsset,
                     voice_asset: AudioAsset,
                     output_dir: str, cfg: dict) -> str:
    """
    Combine visual (video) + voiceover audio for one scene into a single clip.
    Text overlays are drawn via drawtext filter for maximum speed.
    """
    vid_cfg = cfg.get("video", {})
    res     = vid_cfg.get("resolution", "1280x720")
    w, h    = map(int, res.lower().split("x"))
    fps     = int(vid_cfg.get("fps", 15))
    style   = vid_cfg.get("style_preset", "tech-dark")

    out_clip = os.path.join(output_dir, f"clip_{scene.id:02d}.mp4")
    vis_src  = (visual.processed_file
                if visual.processed_file and os.path.exists(visual.processed_file)
                else visual.image_file)

    if not vis_src or not os.path.exists(vis_src):
        log.warning(f"Scene {scene.id}: no visual source, skipping clip")
        return ""

    voice_src = voice_asset.voice_file if os.path.exists(voice_asset.voice_file) else None

    # Build drawtext overlay for scene text
    dt_filters = []
    if scene.text_overlay:
        safe_text = scene.text_overlay.replace("'", "\\'").replace(":", "\\:")
        acc_hex = {"tech-dark": "00D4FF", "vlog-warm": "FFAA32",
                   "news-clean": "DC2828", "motivation-epic": "B450FF"}
        colour = acc_hex.get(style, "FFFFFF")
        fs = max(20, w // 18)
        dt_filters.append(
            f"drawtext=text='{safe_text}'"
            f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            f":fontsize={fs}:fontcolor=0x{colour}"
            f":x=(w-text_w)/2:y=h*0.78"
            f":shadowx=3:shadowy=3:shadowcolor=black"
            f":enable='between(t,0,{scene.duration})'"
        )

    # Watermark
    dt_filters.append(
        "drawtext=text='@GetMindFuelNow'"
        f":fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        f":fontsize={max(14, w//55)}:fontcolor=gray"
        f":x=w-tw-{max(10, w//80)}:y={max(8, h//60)}"
    )

    vf_parts = [
        f"scale={w}:{h}:force_original_aspect_ratio=decrease",
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black",
        "setsar=1",
    ]

    # Colour grade
    grade = GRADE_CURVES.get(style, "")
    if grade:
        vf_parts.append(grade)

    vf_parts.extend(dt_filters)

    vf = ",".join(vf_parts)

    # Determine if vis_src is a video or image
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", vis_src],
        capture_output=True, text=True
    )
    is_video = "video" in probe.stdout

    cmd = ["ffmpeg", "-y"]
    if not is_video:
        cmd += ["-loop", "1"]
    cmd += ["-i", vis_src]
    if voice_src:
        cmd += ["-i", voice_src]
    cmd += ["-t", str(scene.duration)]
    cmd += ["-vf", vf, "-r", str(fps)]
    cmd += ["-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p"]
    if voice_src:
        cmd += ["-c:a", "aac", "-b:a", "128k", "-shortest"]
    else:
        cmd += ["-an"]
    cmd.append(out_clip)

    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"Scene {scene.id} clip build failed: {r.stderr[:300]}")
        return ""

    return out_clip


# ── Concat all clips ──────────────────────────────────────────────────────────

def concat_clips(clip_paths: List[str], output_path: str) -> str:
    """Concatenate scene clips into final silent video."""
    valid = [p for p in clip_paths if p and os.path.exists(p)]
    if not valid:
        raise RuntimeError("No valid clips to concatenate")

    list_file = output_path.replace(".mp4", "_list.txt")
    with open(list_file, "w") as f:
        for p in valid:
            f.write(f"file '{os.path.abspath(p)}'\n")

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Concat failed: {r.stderr[:300]}")

    try:
        os.remove(list_file)
    except OSError:
        pass

    log.info(f"  Concat: {len(valid)} clips → {output_path}")
    return output_path


# ── Mux final audio into video ────────────────────────────────────────────────

def mux_audio(video_path: str, audio_path: str,
              output_path: str, crf: int = 20) -> str:
    """Replace audio track in video with mixed audio."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"Audio mux failed: {r.stderr[:300]}")
        return video_path   # fallback: return video without replaced audio

    size = os.path.getsize(output_path) / 1_048_576
    log.info(f"  Final: {output_path} ({size:.1f} MB)")
    return output_path


def run_phase5(script: Script,
               visual_assets: List[VisualAsset],
               audio_assets: List[AudioAsset],
               mixed_audio: str,
               output_dir: str,
               cfg: dict) -> str:
    """Full Phase 5 assembly. Returns path to final MP4."""
    from tqdm import tqdm

    log.info("Phase 5: Assembling video…")
    clips_dir = os.path.join(output_dir, "clips")
    os.makedirs(clips_dir, exist_ok=True)

    # Map scene_id → assets
    va_map = {v.scene_id: v for v in visual_assets}
    aa_map = {a.scene_id: a for a in audio_assets}

    clip_paths = []
    with tqdm(script.scenes, desc="  Building clips", unit="scene") as bar:
        for scene in bar:
            vis  = va_map.get(scene.id)
            aud  = aa_map.get(scene.id)
            if vis is None or aud is None:
                log.warning(f"Scene {scene.id}: missing visual or audio asset")
                continue
            clip = build_scene_clip(scene, vis, aud, clips_dir, cfg)
            if clip:
                clip_paths.append(clip)

    silent_mp4  = os.path.join(output_dir, "silent.mp4")
    concat_clips(clip_paths, silent_mp4)

    final_mp4 = os.path.join(output_dir, "final_video.mp4")
    mux_audio(silent_mp4, mixed_audio, final_mp4)

    return final_mp4
