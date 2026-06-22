"""
Shorts builder — 1080×1920 vertical video, 55s max, punchy cuts.
Optimised for YouTube Shorts algorithm: first 3s hook is critical.
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

OUTPUT_DIR = settings.output_dir / "shorts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

SW, SH = settings.shorts_width, settings.shorts_height  # 1080×1920


@dataclass
class ShortsAssets:
    audio_path: Path
    chart_paths: list[Path]       # vertical/square crops preferred
    thumbnail_path: Optional[Path]
    script: str
    title: str
    hook_text: str                 # first 3s text overlay
    key_stat: str                  # displayed prominently
    ticker: Optional[str]
    sentiment: str


@dataclass
class BuiltShort:
    path: Path
    duration_seconds: float
    file_size_mb: float
    title: str
    thumbnail_path: Optional[Path]
    built_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _crop_to_vertical(input_path: Path, output_path: Path) -> bool:
    """Crop a 16:9 chart to 9:16 vertical format for Shorts."""
    crop_w = 1080
    crop_h = 1920
    # Center-crop: take the middle portion of the image
    cmd = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", (
            f"scale=-1:{crop_h},"
            f"crop={crop_w}:{crop_h}:(iw-{crop_w})/2:0"
        ),
        str(output_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    return result.returncode == 0


def _build_shorts_with_moviepy(assets: ShortsAssets, output_path: Path) -> Optional[float]:
    try:
        from moviepy.editor import (
            AudioFileClip, ImageClip, TextClip, ColorClip,
            CompositeVideoClip, concatenate_videoclips,
        )
        from moviepy.video.fx.fadein import fadein

        audio = AudioFileClip(str(assets.audio_path))
        total_dur = min(audio.duration, settings.shorts_duration_target)
        audio = audio.subclip(0, total_dur)

        # Background: dark brand color
        bg = ColorClip((SW, SH), color=(10, 10, 15)).set_duration(total_dur)

        # Hook text (first 3 seconds — large, centered)
        hook_clip = (
            TextClip(
                assets.hook_text,
                fontsize=72, color="white",
                font="DejaVu-Sans-Bold",
                size=(SW - 80, None),
                method="caption",
            )
            .set_position("center")
            .set_duration(3)
        )

        # Key stat overlay (middle of video)
        stat_clip = (
            TextClip(
                assets.key_stat,
                fontsize=96, color="#FFD700",
                font="DejaVu-Sans-Bold",
                size=(SW - 80, None),
                method="caption",
            )
            .set_position(("center", SH // 3))
            .set_start(3)
            .set_duration(total_dur - 3)
        )

        # Ticker badge if present
        overlays = [bg, hook_clip, stat_clip]
        if assets.ticker:
            ticker_clip = (
                TextClip(
                    f"${assets.ticker}",
                    fontsize=56, color="#0A0A0F",
                    bg_color="#FFD700",
                    font="DejaVu-Sans-Bold",
                )
                .set_position((40, 40))
                .set_duration(total_dur)
            )
            overlays.append(ticker_clip)

        # Channel name
        channel_clip = (
            TextClip(
                "@DriftWire326",
                fontsize=32, color="white",
                font="DejaVu-Sans",
            )
            .set_opacity(0.7)
            .set_position(("center", SH - 80))
            .set_duration(total_dur)
        )
        overlays.append(channel_clip)

        # Embed chart(s) if available
        if assets.chart_paths:
            chart_path = assets.chart_paths[0]
            if chart_path.exists():
                chart_clip = (
                    ImageClip(str(chart_path))
                    .set_duration(total_dur - 10)
                    .set_start(5)
                    .resize(width=SW)
                    .set_position(("center", SH // 2 - 50))
                    .set_opacity(0.4)
                )
                overlays.append(chart_clip)

        final = CompositeVideoClip(overlays, size=(SW, SH))
        final = final.set_audio(audio)

        final.write_videofile(
            str(output_path),
            fps=settings.video_fps,
            codec="libx264",
            audio_codec="aac",
            bitrate="6000k",
            audio_bitrate=settings.audio_bitrate,
            threads=4,
            logger=None,
        )
        return total_dur

    except ImportError:
        logger.warning("MoviePy not available for Shorts — using ffmpeg fallback")
        return None
    except Exception as exc:
        logger.error("Shorts MoviePy build failed: %s", exc)
        return None


def _build_shorts_with_ffmpeg(assets: ShortsAssets, output_path: Path) -> Optional[float]:
    """ffmpeg fallback: scale/pad chart to 9:16 + attach audio."""
    logger.info("Building Short with ffmpeg")

    valid_charts = [p for p in assets.chart_paths if p.exists()]
    bg_input = str(valid_charts[0]) if valid_charts else None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        if bg_input:
            # Scale to 9:16, pad with brand color
            scaled = tmp / "scaled.png"
            cmd_scale = [
                "ffmpeg", "-y", "-i", bg_input,
                "-vf", f"scale={SW}:-1,pad={SW}:{SH}:(ow-iw)/2:(oh-ih)/2:color=0A0A0F",
                str(scaled),
            ]
            subprocess.run(cmd_scale, capture_output=True, timeout=30)
            bg_src = str(scaled) if scaled.exists() else None
        else:
            bg_src = None

        target_dur = settings.shorts_duration_target

        if bg_src:
            cmd = [
                "ffmpeg", "-y",
                "-loop", "1", "-i", bg_src,
                "-i", str(assets.audio_path),
                "-c:v", "libx264", "-preset", "fast",
                "-c:a", "aac", "-b:a", settings.audio_bitrate,
                "-shortest", "-t", str(target_dur),
                "-s", f"{SW}x{SH}",
                "-r", str(settings.video_fps),
                str(output_path),
            ]
        else:
            # Pure audio → dark background video
            cmd = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", f"color=c=0A0A0F:s={SW}x{SH}:r={settings.video_fps}",
                "-i", str(assets.audio_path),
                "-c:v", "libx264", "-c:a", "aac",
                "-b:a", settings.audio_bitrate,
                "-shortest", "-t", str(target_dur),
                str(output_path),
            ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0:
            return target_dur
        logger.error("ffmpeg Shorts build failed: %s", result.stderr[-500:])
        return None


def build_short(assets: ShortsAssets) -> BuiltShort:
    """Build a YouTube Short. Returns BuiltShort metadata."""
    logger.info("Building Short: '%s'", assets.title)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "".join(c for c in assets.title[:30] if c.isalnum() or c in " -_").strip().replace(" ", "_")
    output_path = OUTPUT_DIR / f"short_{safe_title}_{timestamp}.mp4"

    duration = _build_shorts_with_moviepy(assets, output_path)
    if duration is None:
        duration = _build_shorts_with_ffmpeg(assets, output_path)

    if duration is None or not output_path.exists():
        raise RuntimeError(f"Shorts build failed for '{assets.title}'")

    file_size = output_path.stat().st_size / (1024 * 1024)

    short = BuiltShort(
        path=output_path,
        duration_seconds=duration,
        file_size_mb=round(file_size, 2),
        title=assets.title,
        thumbnail_path=assets.thumbnail_path,
    )

    logger.info("Short built: %s (%.1fs, %.1fMB)", output_path.name, duration, file_size)
    return short
