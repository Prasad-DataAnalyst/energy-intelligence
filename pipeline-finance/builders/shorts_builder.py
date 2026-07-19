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

        # Round channel-logo badge, top-right (channel branding)
        try:
            from builders.logo_overlay import get_round_logo
            logo_size = max(90, SW // 9)
            logo_png = get_round_logo(logo_size)
            if logo_png:
                overlays.append(
                    ImageClip(str(logo_png), transparent=True)
                    .set_position((SW - logo_size - 30, 40))
                    .set_duration(total_dur)
                )
        except Exception as exc:
            logger.warning("Shorts logo badge failed (non-fatal): %s", exc)

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
                "-c:v", "libx264", "-preset", "veryfast",
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

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)
        if result.returncode == 0:
            return target_dur
        logger.error("ffmpeg Shorts build failed: %s", result.stderr[-500:])
        return None


# ── ShortsBuilder class ───────────────────────────────────────────────────────

SHORTS_HARD_LIMIT = 60  # seconds — hard cap
CARD_STYLES = {
    "hook":     {"font_color": "white",   "bg_color": (10, 10, 15),    "font_size": 72},
    "stat":     {"font_color": "#FFD700", "bg_color": (10, 10, 15),    "font_size": 96},
    "context":  {"font_color": "#9999BB", "bg_color": (18, 18, 26),    "font_size": 52},
    "callout":  {"font_color": "#00CC66", "bg_color": (0, 30, 10),     "font_size": 64},
    "cta":      {"font_color": "white",   "bg_color": (80, 0, 20),     "font_size": 56},
}


class ShortsOverLimitError(Exception):
    """Raised when a built Short exceeds the 60-second hard limit."""


class ShortsBuilder:
    """
    Class-based YouTube Shorts builder.
    NO VOICEOVER — visual cards + background music only.
    Hard ≤60s limit enforced via ShortsOverLimitError.
    """

    MUSIC_VOLUME = 0.15
    MUSIC_HISTORY_FILE = settings.logs_dir / "shorts_music_history.json"

    def __init__(self, output_dir: Optional[Path] = None):
        self.output_dir = output_dir or OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def parse_cards(self, script: str) -> list[dict]:
        """
        Parse a 5-card Shorts script into card dicts.
        Expects [CARD N] or [HOOK/STAT/CONTEXT/CALLOUT/CTA] markers.
        Returns up to 5 cards: {type, text}.
        """
        import re
        cards = []
        # Try [CARD N] format first
        pattern = re.compile(r"\[CARD\s*\d+\](.*?)(?=\[CARD|\Z)", re.DOTALL | re.IGNORECASE)
        matches = pattern.findall(script)
        if matches:
            types = list(CARD_STYLES.keys())
            for i, content in enumerate(matches[:5]):
                cards.append({"type": types[min(i, len(types) - 1)], "text": content.strip()})
            return cards

        # Try [TYPE] section markers
        for card_type in CARD_STYLES:
            pat = re.compile(
                rf"\[{card_type.upper()}\](.*?)(?=\[[A-Z]|\Z)", re.DOTALL | re.IGNORECASE
            )
            m = pat.search(script)
            if m:
                cards.append({"type": card_type, "text": m.group(1).strip()})

        if not cards:
            # Fallback: split into equal chunks
            lines = [l.strip() for l in script.splitlines() if l.strip()]
            types = list(CARD_STYLES.keys())
            chunk = max(1, len(lines) // 5)
            for i in range(5):
                chunk_text = " ".join(lines[i * chunk: (i + 1) * chunk])
                cards.append({"type": types[i], "text": chunk_text or f"Card {i+1}"})

        return cards[:5]

    def create_text_card(
        self,
        text: str,
        card_type: str = "hook",
        duration: float = 3.0,
        chart_path: Optional[Path] = None,
    ):
        """
        Create a MoviePy text card clip (1080×1920).
        Returns MoviePy clip or None.
        card_type: one of 'hook', 'stat', 'context', 'callout', 'cta'.
        """
        try:
            from moviepy.editor import TextClip, ColorClip, CompositeVideoClip
            style = CARD_STYLES.get(card_type, CARD_STYLES["hook"])
            bg = ColorClip((SW, SH), color=style["bg_color"]).set_duration(duration)
            text_clip = (
                TextClip(
                    text,
                    fontsize=style["font_size"],
                    color=style["font_color"],
                    font="DejaVu-Sans-Bold",
                    size=(SW - 80, None),
                    method="caption",
                )
                .set_position("center")
                .set_duration(duration)
            )
            composite = CompositeVideoClip([bg, text_clip], size=(SW, SH))
            if chart_path:
                composite = self.composite_card_over_chart(composite, chart_path, duration)
            return composite
        except Exception as exc:
            logger.error("create_text_card failed: %s", exc)
            return None

    def composite_card_over_chart(self, card_clip, chart_path: Path, duration: float):
        """Blend a chart image behind the text card at 40% opacity."""
        try:
            from moviepy.editor import ImageClip, CompositeVideoClip
            chart = (
                ImageClip(str(chart_path))
                .set_duration(duration)
                .resize((SW, SH))
                .set_opacity(0.4)
            )
            return CompositeVideoClip([chart, card_clip], size=(SW, SH)).set_duration(duration)
        except Exception as exc:
            logger.warning("composite_card_over_chart failed: %s", exc)
            return card_clip

    def add_background_music(self, video_path: Path, output_path: Optional[Path] = None) -> Path:
        """
        Mix background music at 0.15 volume.
        Picks a track from assets/music/ avoiding last 7 days of plays.
        """
        music_dir = settings.assets_dir / "music"
        if not music_dir.exists():
            logger.warning("No music directory at %s — skipping music mix", music_dir)
            return video_path

        tracks = list(music_dir.glob("*.mp3")) + list(music_dir.glob("*.wav"))
        if not tracks:
            return video_path

        # 7-day no-repeat history
        history = self._load_music_history()
        available = [str(t.name) for t in tracks if t.name not in history]
        if not available:
            history = []
            available = [t.name for t in tracks]

        chosen_name = available[0]
        chosen = music_dir / chosen_name
        self._save_music_history(history + [chosen_name])

        out = output_path or video_path.parent / f"music_{video_path.name}"
        filter_complex = (
            f"[1:a]volume={self.MUSIC_VOLUME}[bg];"
            "[0:a][bg]amix=inputs=2:duration=first[aout]"
        )
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-stream_loop", "-1", "-i", str(chosen),
            "-filter_complex", filter_complex,
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac",
            "-shortest", str(out),
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=900)
        if result.returncode != 0:
            logger.error("Music mix failed: %s", result.stderr.decode()[-200:])
            return video_path
        return out

    def add_shorts_tag(self, title: str) -> str:
        """Append #Shorts to title if not already present."""
        if "#Shorts" not in title and "#shorts" not in title:
            return title.rstrip() + " #Shorts"
        return title

    def add_handle_watermark(self, video_path: Path, output_path: Optional[Path] = None) -> Path:
        """Overlay @DriftWire326 at bottom-center of video."""
        out = output_path or video_path.parent / f"hw_{video_path.name}"
        vf = (
            "drawtext=text='@DriftWire326'"
            ":fontsize=36:fontcolor=white@0.7"
            ":font=DejaVu-Sans"
            ":x=(w-text_w)/2:y=h-60"
        )
        cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf,
               "-c:v", "libx264", "-c:a", "copy", str(out)]
        result = subprocess.run(cmd, capture_output=True, timeout=900)
        if result.returncode != 0:
            logger.error("Handle watermark failed: %s", result.stderr.decode()[-200:])
            return video_path
        return out

    def build_short_from_script(
        self,
        script: str,
        title: str,
        ticker: Optional[str] = None,
        chart_paths: Optional[list[Path]] = None,
        music: bool = True,
    ) -> BuiltShort:
        """
        Full Shorts build from a raw script string.
        Parses 5 cards, assembles video, enforces ≤60s hard limit.
        """
        cards = self.parse_cards(script)
        logger.info("Building Short from script | %d cards | title=%s", len(cards), title[:50])

        # Build card clips
        try:
            from moviepy.editor import concatenate_videoclips
            card_dur = SHORTS_HARD_LIMIT / max(len(cards), 1)
            clips = []
            for i, card in enumerate(cards):
                cp = chart_paths[i % len(chart_paths)] if chart_paths else None
                clip = self.create_text_card(card["text"], card["type"], card_dur, cp)
                if clip:
                    clips.append(clip)

            if not clips:
                raise RuntimeError("No card clips generated")

            handle_wm_clip = None
            from moviepy.editor import TextClip
            try:
                handle_wm_clip = (
                    TextClip("@DriftWire326", fontsize=32, color="white@0.7",
                             font="DejaVu-Sans")
                    .set_opacity(0.7)
                    .set_position(("center", SH - 70))
                    .set_duration(sum(c.duration for c in clips))
                )
            except Exception:
                pass

            from moviepy.editor import CompositeVideoClip
            final_video = concatenate_videoclips(clips, method="compose")
            if handle_wm_clip:
                final_video = CompositeVideoClip([final_video, handle_wm_clip])

            # Round channel-logo badge, top-right (channel branding)
            try:
                from moviepy.editor import ImageClip as _ImageClip
                from builders.logo_overlay import get_round_logo
                logo_size = max(90, SW // 9)
                logo_png = get_round_logo(logo_size)
                if logo_png:
                    logo_clip = (
                        _ImageClip(str(logo_png), transparent=True)
                        .set_position((SW - logo_size - 30, 40))
                        .set_duration(final_video.duration)
                    )
                    final_video = CompositeVideoClip([final_video, logo_clip])
            except Exception as exc:
                logger.warning("Shorts logo badge failed (non-fatal): %s", exc)

            dur = final_video.duration
            if dur > SHORTS_HARD_LIMIT:
                raise ShortsOverLimitError(
                    f"Short duration {dur:.1f}s exceeds hard limit of {SHORTS_HARD_LIMIT}s"
                )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe = "".join(c for c in title[:25] if c.isalnum() or c in " -_").strip().replace(" ", "_")
            out_path = self.output_dir / f"short_{safe}_{timestamp}.mp4"

            final_video.write_videofile(
                str(out_path), fps=settings.video_fps,
                codec="libx264", audio_codec="aac",
                bitrate="6000k", logger=None,
            )

        except ShortsOverLimitError:
            raise
        except ImportError:
            logger.warning("MoviePy unavailable — using placeholder Short")
            assets = ShortsAssets(
                audio_path=Path("/dev/null"),
                chart_paths=chart_paths or [],
                thumbnail_path=None,
                script=script,
                title=title,
                hook_text=cards[0]["text"][:80] if cards else title,
                key_stat="",
                ticker=ticker,
                sentiment="neutral",
            )
            return build_short(assets)
        except Exception as exc:
            logger.error("Short build failed: %s", exc)
            raise RuntimeError(f"Short build failed: {exc}")

        if music:
            out_path = self.add_background_music(out_path)

        title = self.add_shorts_tag(title)
        file_size = out_path.stat().st_size / (1024 * 1024) if out_path.exists() else 0.0
        short = BuiltShort(
            path=out_path,
            duration_seconds=final_video.duration,
            file_size_mb=round(file_size, 2),
            title=title,
            thumbnail_path=chart_paths[0] if chart_paths else None,
        )
        logger.info("Short built: %s (%.1fs)", out_path.name, short.duration_seconds)
        return short

    def _load_music_history(self) -> list[str]:
        import json
        if self.MUSIC_HISTORY_FILE.exists():
            try:
                return json.loads(self.MUSIC_HISTORY_FILE.read_text())
            except Exception as exc:
                logger.debug("Music history unreadable (%s) — starting fresh", exc)
        return []

    def _save_music_history(self, history: list[str]) -> None:
        import json
        self.MUSIC_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.MUSIC_HISTORY_FILE.write_text(json.dumps(history[-7:]))


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
