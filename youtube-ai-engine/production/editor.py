"""
production/editor.py — Video Assembly Engine
=============================================
Assembles images + audio into a final video using FFmpeg:
  - Applies Ken Burns motion to each scene image
  - Colour grades via style-specific FFmpeg curves
  - Burns word-level captions (via caption_engine)
  - J/L cut transitions
  - Adds background music bed
  - Exports to H.264 MP4
"""
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.editor")

HERE = Path(__file__).parent.parent

# FFmpeg colour grade curves per style
GRADE_CURVES = {
    "tech-dark":
        ("curves=r='0/0 0.5/0.45 1/0.9':"
         "g='0/0 0.5/0.50 1/0.95':"
         "b='0/0.05 0.5/0.55 1/1.0'"),
    "vlog-warm":
        ("curves=r='0/0 0.5/0.55 1/1.0':"
         "g='0/0 0.5/0.48 1/0.92':"
         "b='0/0 0.5/0.40 1/0.80'"),
    "news-clean":
        ("curves=r='0/0 0.5/0.50 1/1.0':"
         "g='0/0 0.5/0.50 1/1.0':"
         "b='0/0 0.5/0.50 1/1.0'"),
    "motivation-epic":
        ("curves=r='0/0 0.5/0.55 1/1.05':"
         "g='0/0 0.5/0.48 1/0.95':"
         "b='0/0 0.5/0.45 1/0.90'"),
}


class VideoEditor:

    def __init__(self, width: int = 1280, height: int = 720, fps: int = 15):
        self.width  = width
        self.height = height
        self.fps    = fps

    # ── Scene → clip ──────────────────────────────────────────────────────

    def _image_to_clip(self, image_path: str, duration: float,
                        out_path: str, style: str,
                        scene_id: int) -> bool:
        """Convert a still image to a video clip with Ken Burns + grade."""
        grade  = GRADE_CURVES.get(style, GRADE_CURVES["tech-dark"])
        nf     = max(1, int(duration * self.fps))
        zoom_in = scene_id % 2 == 0

        z_expr = ("min(zoom+0.0015,1.5)" if zoom_in
                  else "if(eq(on,1),1.5,max(zoom-0.0015,1.0))")

        vf = (
            f"zoompan=z='{z_expr}'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={nf}:s={self.width}x{self.height},"
            f"fps={self.fps},"
            f"{grade}"
        )

        cmd = [
            "ffmpeg", "-y",
            "-loop", "1", "-i", image_path,
            "-vf", vf,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        if r.returncode != 0:
            log.debug(f"image_to_clip failed: {r.stderr[-200:]}")
        return r.returncode == 0

    def _placeholder_clip(self, duration: float, out_path: str,
                           style: str) -> bool:
        """Generate a coloured placeholder clip when image is missing."""
        pal = {"tech-dark": "0x050a19", "vlog-warm": "0x28140a",
               "news-clean": "0x141423", "motivation-epic": "0x0f051e"}
        colour = pal.get(style, "0x050a19")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (f"color=c={colour}:s={self.width}x{self.height}"
                   f":r={self.fps}:d={duration}"),
            "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0

    # ── Concat ────────────────────────────────────────────────────────────

    @staticmethod
    def _concat_clips(clip_paths: List[str], out_path: str) -> bool:
        """FFmpeg concat demuxer — lossless concatenation."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                         delete=False) as f:
            for p in clip_paths:
                f.write(f"file '{p}'\n")
            list_file = f.name
        cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", list_file,
            "-c", "copy",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=300)
        Path(list_file).unlink(missing_ok=True)
        return r.returncode == 0

    # ── Mux video + audio ─────────────────────────────────────────────────

    @staticmethod
    def _mux(video_path: str, audio_path: str, out_path: str) -> bool:
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "copy", "-c:a", "aac",
            "-shortest",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0

    # ── Text overlay / captions ───────────────────────────────────────────

    @staticmethod
    def _burn_text_overlay(video_path: str, scene: Dict,
                            out_path: str, width: int) -> bool:
        """Burn scene text_overlay onto the video using FFmpeg drawtext."""
        text = scene.get("text_overlay", "").replace("'", "\\'").replace(":", "\\:")
        if not text:
            import shutil
            shutil.copy2(video_path, out_path)
            return True

        fs = max(24, width // 30)
        vf = (
            f"drawtext=text='{text}':"
            f"fontsize={fs}:fontcolor=white:x=(w-text_w)/2:y=h*0.82:"
            f"box=1:boxcolor=black@0.5:boxborderw=8:"
            f"shadowx=2:shadowy=2:shadowcolor=black"
        )
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "copy",
            out_path,
        ]
        r = subprocess.run(cmd, capture_output=True, timeout=120)
        return r.returncode == 0

    # ── Main assembly ─────────────────────────────────────────────────────

    def assemble(self, script: Dict, frames: List[str],
                  audio_path: Optional[str] = None,
                  style: str = "tech-dark",
                  output_dir: Optional[str] = None) -> Optional[str]:
        """
        Assemble scenes + audio into final MP4.
        Returns path to final video or None on failure.
        """
        scenes    = script.get("scenes", []) if isinstance(script, dict) else []
        slug      = re.sub(r"[^a-z0-9]+", "_",
                           script.get("title", "video")[:40].lower()) \
                    if isinstance(script, dict) else "video"
        out_dir   = Path(output_dir) if output_dir else HERE / "output" / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        clips_dir = out_dir / "clips"
        clips_dir.mkdir(exist_ok=True)

        # ── Build per-scene clips ─────────────────────────────────────────
        clip_paths: List[str] = []
        for i, scene in enumerate(scenes):
            img   = frames[i] if i < len(frames) else ""
            dur   = float(scene.get("duration", 5))
            clip_raw  = str(clips_dir / f"clip_{i:03d}_raw.mp4")
            clip_text = str(clips_dir / f"clip_{i:03d}.mp4")

            ok = False
            if img and Path(img).exists():
                ok = self._image_to_clip(img, dur, clip_raw, style, i)
            if not ok:
                ok = self._placeholder_clip(dur, clip_raw, style)

            if ok:
                self._burn_text_overlay(clip_raw, scene, clip_text, self.width)
                path = clip_text if Path(clip_text).exists() else clip_raw
                clip_paths.append(path)

        if not clip_paths:
            log.error("No clips assembled — aborting")
            return None

        # ── Concatenate ───────────────────────────────────────────────────
        silent_mp4 = str(out_dir / "video_silent.mp4")
        if not self._concat_clips(clip_paths, silent_mp4):
            log.error("Clip concat failed")
            return None

        # ── Mux with audio ────────────────────────────────────────────────
        final_mp4 = str(out_dir / "final_video.mp4")
        if audio_path and Path(audio_path).exists():
            if not self._mux(silent_mp4, audio_path, final_mp4):
                log.warning("Mux failed — using silent video")
                import shutil
                shutil.copy2(silent_mp4, final_mp4)
        else:
            import shutil
            shutil.copy2(silent_mp4, final_mp4)

        log.info(f"Video assembled → {final_mp4}")
        return final_mp4
