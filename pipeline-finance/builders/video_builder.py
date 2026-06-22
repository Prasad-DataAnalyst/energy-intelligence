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
