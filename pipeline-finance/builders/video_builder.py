"""
Video builder — assembles charts, audio, text overlays, and B-roll into
a final MP4 using MoviePy. Produces 1920×1080 main videos.
"""
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir / "videos"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

BRAND = {
    "bg": "#0A0A0F",
    "primary": "#FF0033",
    "accent": "#FFD700",
    "green": "#00CC66",
    "text": "#FFFFFF",
    "text2": "#9999BB",
}


@dataclass
class VideoAssets:
    audio_path: Path
    chart_paths: list[Path]
    thumbnail_path: Optional[Path]
    script_segments: dict[str, str]
    video_type: str   # "weekday" | "sunday"
    title: str
    duration_seconds: float


@dataclass
class BuiltVideo:
    path: Path
    duration_seconds: float
    file_size_mb: float
    title: str
    video_type: str
    thumbnail_path: Optional[Path]
    built_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @property
    def is_ready(self) -> bool:
        return self.path.exists() and self.file_size_mb > 0.1


def _build_with_moviepy(assets: VideoAssets, output_path: Path) -> Optional[float]:
    """
    Primary builder using MoviePy.
    Layers: branded BG → chart slides → text overlays → audio.
    """
    try:
        from moviepy.editor import (
            AudioFileClip, ImageClip, CompositeVideoClip,
            concatenate_videoclips, TextClip, ColorClip,
        )
        from moviepy.video.fx.fadein import fadein
        from moviepy.video.fx.fadeout import fadeout

        W, H = settings.video_width, settings.video_height
        FPS = settings.video_fps
        audio_dur = assets.duration_seconds

        # Distribute chart display time evenly across video
        n_charts = max(len(assets.chart_paths), 1)
        chart_duration = audio_dur / n_charts

        clips = []

        for i, chart_path in enumerate(assets.chart_paths):
            if not chart_path.exists():
                continue

            # Chart image clip
            chart_clip = (
                ImageClip(str(chart_path))
                .set_duration(chart_duration)
                .resize((W, H))
                .set_fps(FPS)
            )
            chart_clip = fadein(chart_clip, 0.5)
            chart_clip = fadeout(chart_clip, 0.5)

            # Channel name overlay (bottom right)
            watermark = (
                TextClip(
                    "@DriftWire326",
                    fontsize=28, color="white",
                    font="DejaVu-Sans-Bold",
                )
                .set_opacity(0.6)
                .set_position(("right", "bottom"))
                .margin(right=20, bottom=20)
                .set_duration(chart_duration)
            )

            segment_clip = CompositeVideoClip([chart_clip, watermark], size=(W, H))
            clips.append(segment_clip)

        if not clips:
            # Fallback: branded color background if no charts
            bg = ColorClip((W, H), color=(10, 10, 15)).set_duration(audio_dur)
            title_text = TextClip(
                assets.title[:60],
                fontsize=64, color="white",
                font="DejaVu-Sans-Bold",
                size=(W - 200, None),
                method="caption",
            ).set_position("center").set_duration(audio_dur)
            clips = [CompositeVideoClip([bg, title_text])]

        video = concatenate_videoclips(clips, method="compose")

        # Attach audio
        audio = AudioFileClip(str(assets.audio_path))
        audio_duration = min(audio.duration, video.duration)
        audio = audio.subclip(0, audio_duration)
        video = video.set_audio(audio)
        video = video.subclip(0, audio_duration)

        video.write_videofile(
            str(output_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            bitrate=settings.video_bitrate,
            audio_bitrate=settings.audio_bitrate,
            threads=4,
            logger=None,
        )
        return audio_duration

    except ImportError:
        logger.warning("MoviePy not installed — falling back to ffmpeg")
        return None
    except Exception as exc:
        logger.error("MoviePy build failed: %s", exc)
        return None


def _build_with_ffmpeg(assets: VideoAssets, output_path: Path) -> Optional[float]:
    """
    Fallback builder using raw ffmpeg subprocess.
    Combines audio + a static/sliding chart slideshow.
    """
    logger.info("Building video with ffmpeg fallback")

    if not assets.chart_paths:
        logger.error("No chart paths provided for ffmpeg build")
        return None

    valid_charts = [p for p in assets.chart_paths if p.exists()]
    if not valid_charts:
        logger.error("No valid chart files found")
        return None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Create concat file for images (each shown for equal duration)
        dur_per_img = assets.duration_seconds / len(valid_charts)
        concat_file = tmp / "imgs.txt"
        lines = []
        for p in valid_charts:
            lines.append(f"file '{p.resolve()}'")
            lines.append(f"duration {dur_per_img:.2f}")
        # Add last frame again (ffmpeg quirk)
        lines.append(f"file '{valid_charts[-1].resolve()}'")
        concat_file.write_text("\n".join(lines))

        # Build video from images
        raw_video = tmp / "raw_video.mp4"
        cmd_video = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0",
            "-i", str(concat_file),
            "-vf", f"scale={settings.video_width}:{settings.video_height}:force_original_aspect_ratio=decrease,"
                   f"pad={settings.video_width}:{settings.video_height}:(ow-iw)/2:(oh-ih)/2:color=0A0A0F,"
                   f"fps={settings.video_fps}",
            "-c:v", "libx264", "-preset", "medium", "-b:v", settings.video_bitrate,
            str(raw_video),
        ]

        result = subprocess.run(cmd_video, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            logger.error("ffmpeg image concat failed: %s", result.stderr[-500:])
            return None

        # Mux audio + video
        cmd_mux = [
            "ffmpeg", "-y",
            "-i", str(raw_video),
            "-i", str(assets.audio_path),
            "-c:v", "copy", "-c:a", "aac",
            "-b:a", settings.audio_bitrate,
            "-shortest",
            str(output_path),
        ]
        result = subprocess.run(cmd_mux, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            logger.error("ffmpeg mux failed: %s", result.stderr[-500:])
            return None

    logger.info("ffmpeg build complete → %s", output_path)
    return assets.duration_seconds


# ── VideoBuilder class ────────────────────────────────────────────────────────

MIN_DURATION = 120
MAX_DURATION = 180
CAPTION_FONT_SIZE = 52
MUSIC_VOLUME = 0.08


class VideoBuilder:
    """Class-based video assembly for DriftWire326 main videos."""

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def build_main_video(self, assets: VideoAssets) -> BuiltVideo:
        """Convenience wrapper — build a weekday or Sunday video."""
        return build_video(assets)

    def create_ken_burns_clip(self, image_path: Path, duration: float, zoom_ratio: float = 1.05):
        """
        Apply a slow Ken Burns zoom-in effect to a static image.
        Returns a MoviePy clip or None if MoviePy unavailable.
        """
        try:
            from moviepy.editor import ImageClip
            import numpy as np

            W, H = settings.video_width, settings.video_height
            fps = settings.video_fps
            clip = ImageClip(str(image_path)).set_duration(duration).resize((W, H))

            def make_frame(t):
                progress = t / duration
                scale = 1.0 + (zoom_ratio - 1.0) * progress
                frame = clip.get_frame(t)
                from PIL import Image
                img = Image.fromarray(frame)
                new_w = int(W * scale)
                new_h = int(H * scale)
                img_resized = img.resize((new_w, new_h), Image.LANCZOS)
                x0 = (new_w - W) // 2
                y0 = (new_h - H) // 2
                cropped = img_resized.crop((x0, y0, x0 + W, y0 + H))
                return np.array(cropped)

            from moviepy.editor import VideoClip
            return VideoClip(make_frame, duration=duration).set_fps(fps)

        except Exception as exc:
            logger.warning("create_ken_burns_clip failed: %s", exc)
            return None

    def burn_captions(
        self,
        video_path: Path,
        captions: list[tuple[float, float, str]],  # [(start, end, text), ...]
        output_path: Optional[Path] = None,
    ) -> Path:
        """
        Burn captions into video using ffmpeg drawtext.
        Style: 52px bold white, black outline.
        captions: list of (start_sec, end_sec, text).
        """
        if not captions:
            return video_path

        out = output_path or video_path.parent / f"captioned_{video_path.name}"
        filter_parts = []
        for start, end, text in captions:
            safe_text = text.replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")
            filter_parts.append(
                f"drawtext=text='{safe_text}'"
                f":fontsize={CAPTION_FONT_SIZE}"
                f":fontcolor=white"
                f":borderw=3:bordercolor=black"
                f":x=(w-text_w)/2:y=h-th-80"
                f":enable='between(t,{start},{end})'"
                f":font=DejaVu-Sans-Bold"
            )
        vf = ",".join(filter_parts)

        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
               "-c:v", "libx264", "-c:a", "copy", str(out)]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error("Caption burn failed: %s", result.stderr.decode()[-300:])
            return video_path
        logger.info("Captions burned → %s", out)
        return out

    def add_lower_third(
        self,
        video_path: Path,
        label: str,
        start: float = 0.5,
        duration: float = 3.0,
        output_path: Optional[Path] = None,
    ) -> Path:
        """Overlay a lower-third title card using ffmpeg drawbox + drawtext."""
        out = output_path or video_path.parent / f"lt_{video_path.name}"
        end = start + duration
        safe = label.replace("'", "\\'")
        vf = (
            f"drawbox=x=0:y=ih-120:w=iw:h=120:color=black@0.6:t=fill"
            f":enable='between(t,{start},{end})',"
            f"drawtext=text='{safe}'"
            f":fontsize=38:fontcolor=white:font=DejaVu-Sans-Bold"
            f":x=30:y=ih-90"
            f":enable='between(t,{start},{end})'"
        )
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
               "-c:v", "libx264", "-c:a", "copy", str(out)]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error("Lower third failed: %s", result.stderr.decode()[-200:])
            return video_path
        return out

    def add_watermark(self, video_path: Path, output_path: Optional[Path] = None) -> Path:
        """Overlay @DriftWire326 watermark (top-right, semi-transparent)."""
        out = output_path or video_path.parent / f"wm_{video_path.name}"
        vf = (
            "drawtext=text='@DriftWire326'"
            ":fontsize=28:fontcolor=white@0.5"
            ":font=DejaVu-Sans"
            ":x=w-tw-20:y=20"
        )
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
               "-c:v", "libx264", "-c:a", "copy", str(out)]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error("Watermark overlay failed: %s", result.stderr.decode()[-200:])
            return video_path
        return out

    def mix_audio(
        self,
        video_path: Path,
        music_path: Path,
        music_volume: float = MUSIC_VOLUME,
        output_path: Optional[Path] = None,
    ) -> Path:
        """Mix background music at music_volume under the main voiceover."""
        out = output_path or video_path.parent / f"mixed_{video_path.name}"
        filter_complex = (
            f"[1:a]volume={music_volume}[bg];"
            "[0:a][bg]amix=inputs=2:duration=first:dropout_transition=2[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-i", str(music_path),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac",
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            logger.error("Audio mix failed: %s", result.stderr.decode()[-200:])
            return video_path
        return out

    def validate_duration(self, video_path: Path) -> tuple[bool, float]:
        """
        Check video duration is within [120, 180]s.
        Returns (is_valid, actual_duration).
        """
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
                capture_output=True, text=True, timeout=10,
            )
            dur = float(result.stdout.strip())
            valid = MIN_DURATION <= dur <= MAX_DURATION
            if not valid:
                logger.warning("Video duration %.1fs outside [%d, %d]s", dur, MIN_DURATION, MAX_DURATION)
            return valid, dur
        except Exception as exc:
            logger.error("validate_duration failed: %s", exc)
            return False, 0.0

    def export(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        crf: int = 23,
    ) -> Path:
        """
        Re-encode to H.264 1920×1080 30fps AAC (YouTube recommended spec).
        Returns the output path.
        """
        W, H = settings.video_width, settings.video_height
        fps = settings.video_fps
        out = output_path or self.output_dir / f"final_{input_path.name}"
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                   f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=0A0A0F",
            "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
            "-r", str(fps), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", settings.audio_bitrate,
            str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            logger.error("Export failed: %s", result.stderr.decode()[-300:])
            raise RuntimeError(f"Export failed for {input_path}")
        logger.info("Video exported → %s", out)
        return out


def build_video(assets: VideoAssets) -> BuiltVideo:
    """Main entry — build final video. Returns BuiltVideo metadata."""
    logger.info("Building video: '%s' (%s, %.1fs)", assets.title, assets.video_type, assets.duration_seconds)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in assets.title[:40] if c.isalnum() or c in " -_").strip().replace(" ", "_")
    output_path = OUTPUT_DIR / f"{assets.video_type}_{safe_title}_{timestamp}.mp4"

    # Try MoviePy first
    duration = _build_with_moviepy(assets, output_path)

    # Fallback to ffmpeg
    if duration is None or not output_path.exists():
        duration = _build_with_ffmpeg(assets, output_path)

    if duration is None or not output_path.exists():
        raise RuntimeError(f"Video build failed for '{assets.title}' — all methods exhausted")

    file_size = output_path.stat().st_size / (1024 * 1024) if output_path.exists() else 0

    video = BuiltVideo(
        path=output_path,
        duration_seconds=duration,
        file_size_mb=round(file_size, 2),
        title=assets.title,
        video_type=assets.video_type,
        thumbnail_path=assets.thumbnail_path,
    )

    logger.info("Video built: %s (%.1fs, %.1fMB)", output_path.name, duration, file_size)
    return video
