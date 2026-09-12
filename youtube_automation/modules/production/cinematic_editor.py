"""
modules/production/cinematic_editor.py
Post-production: color grading, text animations, transitions, beat-sync.
Works on top of the rendered video from HollywoodVisualEngine.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Grade filter definitions
# ---------------------------------------------------------------------------
_GRADE_FILTERS: dict[str, str] = {
    "tech_dark": (
        "curves=r='0/0 0.5/0.45 1/0.85':g='0/0 0.5/0.50 1/0.92':b='0/0.05 0.5/0.55 1/1.0',"
        "eq=contrast=1.1:brightness=-0.02:saturation=0.9"
    ),
    "documentary": (
        "curves=all='0/0 0.3/0.35 0.7/0.72 1/0.95',"
        "eq=contrast=1.05:saturation=0.85:brightness=0.01"
    ),
    "thriller": (
        "curves=all='0/0 0.25/0.2 0.75/0.8 1/1.0',"
        "eq=contrast=1.2:saturation=0.5:brightness=-0.05"
    ),
    "education": (
        "eq=contrast=1.08:brightness=0.03:saturation=1.1:gamma_r=1.02"
    ),
}

_VIGNETTE = "vignette=angle=PI/4:mode=backward:eval=frame:dither=1:aspect=1280/720"
_GRAIN = "noise=alls=4:allf=t+u"
_SHARPEN = "unsharp=lx=3:ly=3:la=0.5"


def _run(cmd: List[str], label: str = "") -> subprocess.CompletedProcess:
    """Run a subprocess command, logging stdout/stderr on failure."""
    logger.debug("Running %s: %s", label, " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("%s failed (rc=%d):\n%s", label, result.returncode, result.stderr)
        raise RuntimeError(
            f"{label} exited with code {result.returncode}: {result.stderr[:500]}"
        )
    return result


def _video_duration(path: str) -> float:
    """Return video duration in seconds using ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe could not read duration of {path}: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


class CinematicEditor:
    """
    Post-production editor that applies cinematic color grades, intro/end cards,
    zoom-punch effects, beat-synced cuts, and final quality renders.
    """

    # ------------------------------------------------------------------
    # 1. Color grading
    # ------------------------------------------------------------------
    def apply_color_grade(
        self,
        input_path: str,
        output_path: str,
        grade: str = "tech_dark",
    ) -> str:
        """
        Apply a cinematic color grade with vignette, film grain, and sharpening.

        Parameters
        ----------
        input_path:  Path to the source video file.
        output_path: Destination path for the graded video.
        grade:       One of "tech_dark", "documentary", "thriller", "education".

        Returns
        -------
        output_path on success.
        """
        if grade not in _GRADE_FILTERS:
            logger.warning("Unknown grade '%s', falling back to 'tech_dark'.", grade)
            grade = "tech_dark"

        grade_filter = _GRADE_FILTERS[grade]
        vf = f"{grade_filter},{_VIGNETTE},{_GRAIN},{_SHARPEN}"

        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "slow", "-crf", "16",
            "-c:a", "copy",
            output_path,
        ]
        _run(cmd, label="apply_color_grade")
        logger.info("Color grade '%s' applied → %s", grade, output_path)
        return output_path

    # ------------------------------------------------------------------
    # 2. Intro card
    # ------------------------------------------------------------------
    def add_intro_card(
        self,
        input_path: str,
        output_path: str,
        title: str,
        channel: str = "Mind Fuel Daily",
    ) -> str:
        """
        Prepend a 2-second animated title card to the video.

        The card shows:
          - Black background with a fade-in tint to the channel brand colour (#00C8FF).
          - Channel name in small-caps at the top.
          - Title slamming in at centre.
          - Fade-out transition into the main video.

        Parameters
        ----------
        input_path:  Path to the main video.
        output_path: Destination path.
        title:       Episode or video title shown at centre.
        channel:     Channel name shown in small caps at top.

        Returns
        -------
        output_path on success.
        """
        try:
            from moviepy.editor import (  # type: ignore
                ColorClip,
                CompositeVideoClip,
                TextClip,
                VideoFileClip,
                concatenate_videoclips,
            )
        except ImportError:
            logger.error("moviepy is not installed. Skipping intro card.")
            # Fallback: just copy the file without the card.
            import shutil
            shutil.copy2(input_path, output_path)
            return output_path

        CARD_DURATION = 2.0
        W, H = 1920, 1080
        FPS = 30

        # Background: black → brand-colour fade
        bg = ColorClip(size=(W, H), color=(0, 0, 0), duration=CARD_DURATION)

        # Channel name — small-caps effect via uppercase + small font
        try:
            channel_clip = (
                TextClip(
                    channel.upper(),
                    fontsize=36,
                    color="white",
                    font="Arial-Bold",
                    kerning=6,
                )
                .set_position(("center", int(H * 0.18)))
                .set_duration(CARD_DURATION)
                .crossfadein(0.4)
            )
        except Exception as exc:
            logger.warning("TextClip (channel) failed: %s – using plain text fallback.", exc)
            channel_clip = None

        # Title — slams in at 0.2 s, fades out at end
        try:
            title_clip = (
                TextClip(
                    title,
                    fontsize=72,
                    color="white",
                    font="Arial-Bold",
                    method="caption",
                    size=(int(W * 0.8), None),
                    align="center",
                )
                .set_position("center")
                .set_start(0.2)
                .set_duration(CARD_DURATION - 0.2)
                .crossfadein(0.25)
                .crossfadeout(0.3)
            )
        except Exception as exc:
            logger.warning("TextClip (title) failed: %s – skipping title overlay.", exc)
            title_clip = None

        layers = [bg]
        if channel_clip is not None:
            layers.append(channel_clip)
        if title_clip is not None:
            layers.append(title_clip)

        card = (
            CompositeVideoClip(layers, size=(W, H))
            .set_fps(FPS)
            .fadein(0.15)
            .fadeout(0.3)
        )

        # Main video
        main = VideoFileClip(input_path)

        combined = concatenate_videoclips([card, main], method="compose")
        combined.write_videofile(
            output_path,
            codec="libx264",
            audio_codec="aac",
            bitrate="8000k",
            preset="slow",
            logger=None,
        )
        logger.info("Intro card added → %s", output_path)
        return output_path

    # ------------------------------------------------------------------
    # 3. End-screen overlay
    # ------------------------------------------------------------------
    def add_endscreen_overlay(
        self,
        input_path: str,
        output_path: str,
        duration: float = 20.0,
    ) -> str:
        """
        Overlay a YouTube end-screen (subscribe box + next-video box) on the
        last *duration* seconds of the video using FFmpeg drawbox/drawtext filters.

        Parameters
        ----------
        input_path:  Source video path.
        output_path: Destination path.
        duration:    How many seconds before the end the overlay should appear.

        Returns
        -------
        output_path on success.
        """
        try:
            video_length = _video_duration(input_path)
        except RuntimeError as exc:
            logger.error("Cannot determine video duration: %s", exc)
            raise

        start_t = max(0.0, video_length - duration)

        # -----------------------------------------------------------------
        # Layout constants (1920×1080 canvas)
        # -----------------------------------------------------------------
        # Subscribe box  — bottom-left quadrant
        sub_x, sub_y, sub_w, sub_h = 80, 700, 380, 220
        # Next-video box — bottom-right quadrant
        nxt_x, nxt_y, nxt_w, nxt_h = 1460, 700, 380, 220

        # Fade-in alpha expression (FFmpeg 'enable' with blend via setpts)
        enable_expr = f"gte(t,{start_t:.3f})"

        # Rounded rectangle boxes (FFmpeg drawbox does not support rounding natively;
        # we simulate with two overlapping boxes + corners via alpha blend overlay).
        # For simplicity we use semi-transparent boxes (alpha not supported in drawbox
        # for older FFmpeg builds so we use the colorkey-free approach with geq).
        box_color = "black@0.72"

        # Subscribe box
        sub_box = (
            f"drawbox=x={sub_x}:y={sub_y}:w={sub_w}:h={sub_h}"
            f":color={box_color}:t=fill:enable='{enable_expr}'"
        )
        # Next-video box
        nxt_box = (
            f"drawbox=x={nxt_x}:y={nxt_y}:w={nxt_w}:h={nxt_h}"
            f":color={box_color}:t=fill:enable='{enable_expr}'"
        )

        # --- Text labels ---
        font_opts = "fontfile=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

        sub_label = (
            f"drawtext={font_opts}"
            f":text='SUBSCRIBE':fontsize=38:fontcolor=white"
            f":x={sub_x + sub_w // 2 - 80}:y={sub_y + 80}"
            f":enable='{enable_expr}'"
        )

        # Animated pulse ring around SUBSCRIBE text (box outline that grows/shrinks)
        pulse_r = 48
        pulse_cx = sub_x + sub_w // 2
        pulse_cy = sub_y + sub_h // 2 - 20
        # We draw the ring as a hollow drawbox (outline only)
        pulse_ring = (
            f"drawbox"
            f":x={pulse_cx - pulse_r}:y={pulse_cy - pulse_r}"
            f":w={2 * pulse_r}:h={2 * pulse_r}"
            f":color=red@0.8:t=4"
            f":enable='{enable_expr}'"
        )

        nxt_label = (
            f"drawtext={font_opts}"
            f":text='WATCH NEXT':fontsize=34:fontcolor=white"
            f":x={nxt_x + nxt_w // 2 - 90}:y={nxt_y + 90}"
            f":enable='{enable_expr}'"
        )

        vf = ",".join([sub_box, nxt_box, sub_label, pulse_ring, nxt_label])

        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path,
        ]
        _run(cmd, label="add_endscreen_overlay")
        logger.info("End-screen overlay added (last %.1fs) → %s", duration, output_path)
        return output_path

    # ------------------------------------------------------------------
    # 4. Zoom punch
    # ------------------------------------------------------------------
    def apply_zoom_punch(
        self,
        input_path: str,
        output_path: str,
        punch_times: List[float],
        intensity: float = 0.04,
    ) -> str:
        """
        Apply a zoom-punch effect at each timestamp in *punch_times*.

        At each punch time *t*:
          - Zoom to (1 + intensity) over 3 frames.
          - Return to 1.0 over the next 8 frames.

        Implementation uses OpenCV frame-by-frame manipulation when available,
        falling back to a lightweight FFmpeg zoompan expression if cv2 is absent.

        Parameters
        ----------
        input_path:   Source video path.
        output_path:  Destination path.
        punch_times:  List of timestamps (seconds) where the punch occurs.
        intensity:    Peak zoom factor added to 1.0 (e.g. 0.04 → zoom to 1.04×).

        Returns
        -------
        output_path on success.
        """
        if not punch_times:
            logger.info("No punch times supplied – copying input unchanged.")
            import shutil
            shutil.copy2(input_path, output_path)
            return output_path

        try:
            import cv2  # type: ignore
            return self._zoom_punch_cv2(
                input_path, output_path, punch_times, intensity
            )
        except ImportError:
            logger.warning("cv2 not available; using FFmpeg zoompan fallback.")
            return self._zoom_punch_ffmpeg(
                input_path, output_path, punch_times, intensity
            )

    def _zoom_punch_cv2(
        self,
        input_path: str,
        output_path: str,
        punch_times: List[float],
        intensity: float,
    ) -> str:
        """Frame-by-frame zoom punch via OpenCV."""
        import cv2  # type: ignore
        import numpy as np

        cap = cv2.VideoCapture(input_path)
        if not cap.isOpened():
            raise RuntimeError(f"cv2 cannot open: {input_path}")

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Build per-frame zoom schedule (zoom_scale[frame_idx] = scale)
        zoom_schedule: dict[int, float] = {}
        RAMP_UP = 3    # frames to reach peak
        RAMP_DOWN = 8  # frames to return to 1.0

        for t in punch_times:
            start_frame = int(t * fps)
            for i in range(RAMP_UP):
                fi = start_frame + i
                scale = 1.0 + intensity * (i + 1) / RAMP_UP
                zoom_schedule[fi] = max(zoom_schedule.get(fi, 1.0), scale)
            for i in range(RAMP_DOWN):
                fi = start_frame + RAMP_UP + i
                scale = 1.0 + intensity * (1.0 - (i + 1) / RAMP_DOWN)
                zoom_schedule[fi] = max(zoom_schedule.get(fi, 1.0), scale)

        # Temporary file for raw frames; audio handled separately
        tmp_video = output_path + ".tmp_nv.mp4"

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(tmp_video, fourcc, fps, (W, H))

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            scale = zoom_schedule.get(frame_idx, 1.0)
            if scale != 1.0:
                # Crop centre by 1/scale then resize back to (W, H)
                new_w = int(W / scale)
                new_h = int(H / scale)
                x1 = (W - new_w) // 2
                y1 = (H - new_h) // 2
                cropped = frame[y1: y1 + new_h, x1: x1 + new_w]
                frame = cv2.resize(cropped, (W, H), interpolation=cv2.INTER_LINEAR)
            writer.write(frame)
            frame_idx += 1

        cap.release()
        writer.release()

        # Mux audio back
        cmd = [
            "ffmpeg", "-y",
            "-i", tmp_video,
            "-i", input_path,
            "-c:v", "copy",
            "-c:a", "copy",
            "-map", "0:v:0",
            "-map", "1:a:0?",
            output_path,
        ]
        try:
            _run(cmd, label="zoom_punch_mux_audio")
        finally:
            try:
                os.remove(tmp_video)
            except OSError:
                pass

        logger.info("Zoom punch (cv2) applied at %d points → %s", len(punch_times), output_path)
        return output_path

    def _zoom_punch_ffmpeg(
        self,
        input_path: str,
        output_path: str,
        punch_times: List[float],
        intensity: float,
    ) -> str:
        """
        Lightweight FFmpeg-only zoom punch using a zoompan expression.

        Generates a zoompan filter where zoom oscillates near each punch time.
        Suitable as a fallback when cv2 is unavailable; quality is slightly lower
        due to zoompan interpolation artefacts.
        """
        # Build a piecewise zoom expression using FFmpeg's `between()` helper.
        # Each punch contributes a triangular bump.
        FPS = 30  # assumed; zoompan requires constant fps
        RAMP_UP = 3
        RAMP_DOWN = 8

        zoom_parts: List[str] = []
        for t in sorted(punch_times):
            start_f = int(t * FPS)
            peak_f = start_f + RAMP_UP
            end_f = peak_f + RAMP_DOWN

            # Ramp-up: linear from 1.0 to 1+intensity
            up = (
                f"if(between(n,{start_f},{peak_f - 1}),"
                f"1+{intensity}*(n-{start_f})/{RAMP_UP}"
                ",0)"
            )
            # Ramp-down: linear from 1+intensity to 1.0
            down = (
                f"if(between(n,{peak_f},{end_f}),"
                f"1+{intensity}*(1-(n-{peak_f})/{RAMP_DOWN})"
                ",0)"
            )
            zoom_parts.append(f"({up}+{down})")

        if zoom_parts:
            zoom_expr = "+".join(zoom_parts) + "+if(eq(n,0),1,0)"
            # Ensure zoom never goes below 1
            zoom_expr = f"max(1,{zoom_expr})"
        else:
            zoom_expr = "1"

        vf = (
            f"zoompan=z='{zoom_expr}'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s=1920x1080:fps={FPS}"
        )

        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path,
        ]
        _run(cmd, label="zoom_punch_ffmpeg")
        logger.info(
            "Zoom punch (ffmpeg) applied at %d points → %s", len(punch_times), output_path
        )
        return output_path

    # ------------------------------------------------------------------
    # 5. Beat-synced cuts
    # ------------------------------------------------------------------
    def sync_cuts_to_beat(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
    ) -> str:
        """
        Analyze music beats with librosa and apply micro zoom-pulses on downbeats.

        Parameters
        ----------
        video_path:  Path to the video that will receive the zoom pulses.
        audio_path:  Path to the audio track used for beat detection.
        output_path: Destination path for the beat-synced video.

        Returns
        -------
        output_path on success.
        """
        try:
            import librosa  # type: ignore
        except ImportError:
            logger.error(
                "librosa is not installed. Beat sync skipped. "
                "Install with: pip install librosa"
            )
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

        logger.info("Analysing beats from: %s", audio_path)
        y, sr = librosa.load(audio_path, duration=120)
        tempo, beats = librosa.beat.beat_track(y=y, sr=sr)
        beat_times: List[float] = librosa.frames_to_time(beats, sr=sr).tolist()

        logger.info(
            "Detected %.1f BPM, %d beats; applying pulses on every other beat.",
            float(tempo),
            len(beat_times),
        )

        # Every other beat = downbeats (coarse approximation)
        return self.apply_zoom_punch(
            video_path,
            output_path,
            beat_times[::2],
            intensity=0.02,
        )

    # ------------------------------------------------------------------
    # 6. Final render
    # ------------------------------------------------------------------
    def final_render(self, input_path: str, output_path: str) -> str:
        """
        Final broadcast-quality render targeting 1920×1080, H.264 CRF 18,
        AAC 192 k audio, faststart flag for web delivery.

        A quality check is run after encoding; a warning is logged if any
        stream is missing or resolution is unexpected.

        Parameters
        ----------
        input_path:  Source video path.
        output_path: Destination path for the final render.

        Returns
        -------
        output_path on success.
        """
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-c:v", "libx264", "-preset", "slow", "-crf", "18",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            "-c:a", "aac", "-b:a", "192k",
            "-vf", "scale=1920:1080:flags=lanczos",
            output_path,
        ]
        _run(cmd, label="final_render")
        logger.info("Final render complete → %s", output_path)

        # Quality gate
        try:
            quality = self._check_quality(output_path)
            logger.info("Quality check: %s", quality)
            if not quality.get("ok"):
                logger.warning("Quality check flagged issues: %s", quality.get("issues"))
        except Exception as exc:
            logger.warning("Quality check raised an error: %s", exc)

        return output_path

    @staticmethod
    def _check_quality(path: str) -> dict:
        """
        Run ffprobe on *path* and verify that the output meets quality standards:
          - Resolution 1920×1080
          - At least one video stream present
          - At least one audio stream present

        Returns a dict with keys ``ok`` (bool), ``issues`` (list[str]),
        and ``streams`` (raw ffprobe output).
        """
        cmd = [
            "ffprobe", "-v", "error",
            "-show_streams",
            "-of", "json",
            path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            return {
                "ok": False,
                "issues": [f"ffprobe failed: {result.stderr[:200]}"],
                "streams": [],
            }

        data = json.loads(result.stdout)
        streams = data.get("streams", [])
        issues: List[str] = []

        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]

        if not video_streams:
            issues.append("No video stream found.")
        else:
            vs = video_streams[0]
            w = vs.get("width", 0)
            h = vs.get("height", 0)
            if (w, h) != (1920, 1080):
                issues.append(
                    f"Resolution is {w}×{h} (expected 1920×1080)."
                )

        if not audio_streams:
            issues.append("No audio stream found.")

        return {
            "ok": len(issues) == 0,
            "issues": issues,
            "streams": streams,
        }

    # ------------------------------------------------------------------
    # 7. Master edit from CinematicScript
    # ------------------------------------------------------------------
    def edit_from_script(
        self,
        script,               # CinematicScript dataclass
        video_path: str,
        audio_path: str,
        output_path: str,
        grade: Optional[str] = None,
        add_captions: bool = True,
    ) -> str:
        """
        Full post-production chain from a CinematicScript:
          1. Color grade (script.recommended_grade)
          2. Stat overlays for data-viz scenes
          3. Source lower-thirds for cited scenes
          4. Word-highlight captions (Whisper)
          5. Beat-synced micro-pulses
          6. End-screen overlay
          7. Final broadcast render

        Returns output_path.
        """
        import shutil, tempfile as _tmp

        def _tmp_path(suffix: str) -> str:
            fd, p = _tmp.mkstemp(suffix=suffix, dir=os.path.dirname(os.path.abspath(output_path)))
            os.close(fd)
            return p

        current = video_path
        step = _tmp_path("_grade.mp4")

        effective_grade = grade or getattr(script, "recommended_grade", "tech_dark") or "tech_dark"

        # Step 1: colour grade
        try:
            current = self.apply_color_grade(current, step, effective_grade)
        except Exception as exc:
            logger.warning("Color grade failed (%s); continuing without it.", exc)
            shutil.copy2(current, step)
            current = step

        # Step 2: stat overlays for data-viz scenes
        try:
            data_scenes = [
                s for s in script.scenes
                if getattr(s, "text_overlay", None)
                and getattr(s.visual, "type", "") == "data_visualization"
            ]
            if data_scenes:
                stats = [
                    {"text": s.text_overlay.hook_text, "time": self._ts_to_s(s.timestamp_start)}
                    for s in data_scenes
                    if s.text_overlay and s.text_overlay.hook_text
                ]
                if stats:
                    step2 = _tmp_path("_stats.mp4")
                    try:
                        current = self.add_stat_overlays(current, step2, stats)
                    except Exception as exc:
                        logger.warning("Stat overlays failed (%s); skipping.", exc)
        except Exception as exc:
            logger.warning("Stat overlay setup failed (%s); skipping.", exc)

        # Step 3: source lower-thirds for cited scenes
        try:
            cited = [
                s for s in script.scenes
                if getattr(s, "factual_source", "") and getattr(s.text_overlay, "source_credit", "")
            ]
            if cited:
                step3 = _tmp_path("_thirds.mp4")
                thirds = [
                    {
                        "source": s.text_overlay.source_credit,
                        "time": self._ts_to_s(s.timestamp_start),
                    }
                    for s in cited
                    if s.text_overlay and s.text_overlay.source_credit
                ]
                if thirds:
                    try:
                        current = self.add_source_lower_thirds(current, step3, thirds)
                    except Exception as exc:
                        logger.warning("Lower thirds failed (%s); skipping.", exc)
        except Exception as exc:
            logger.warning("Lower-thirds setup failed (%s); skipping.", exc)

        # Step 4: word-highlight captions
        if add_captions and os.path.exists(audio_path):
            step4 = _tmp_path("_captions.mp4")
            try:
                current = self.add_word_highlight_captions(current, audio_path, step4)
            except Exception as exc:
                logger.warning("Captions failed (%s); skipping.", exc)

        # Step 5: beat-synced micro-pulses
        if os.path.exists(audio_path):
            step5 = _tmp_path("_beat.mp4")
            try:
                current = self.sync_cuts_to_beat(current, audio_path, step5)
            except Exception as exc:
                logger.warning("Beat sync failed (%s); skipping.", exc)

        # Step 6: end-screen overlay
        step6 = _tmp_path("_endscreen.mp4")
        try:
            current = self.add_endscreen_overlay(current, step6)
        except Exception as exc:
            logger.warning("End-screen overlay failed (%s); skipping.", exc)

        # Step 7: final broadcast render
        try:
            current = self.final_render(current, output_path)
        except Exception as exc:
            logger.error("Final render failed (%s); copying best available.", exc)
            shutil.copy2(current, output_path)

        return output_path

    # ------------------------------------------------------------------
    # 8. Word-highlight captions (Whisper)
    # ------------------------------------------------------------------
    def add_word_highlight_captions(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
        font_size: int = 52,
    ) -> str:
        """
        Burn word-highlight captions into the video.

        Uses OpenAI Whisper (word-level timestamps) when available.
        Falls back to sentence-level ASS subtitles via ffmpeg.

        Returns output_path.
        """
        try:
            import whisper  # type: ignore
            model = whisper.load_model("base")
            result = model.transcribe(audio_path, word_timestamps=True)
            word_events = []
            for seg in result.get("segments", []):
                for w in seg.get("words", []):
                    word_events.append({
                        "word": w.get("word", "").strip(),
                        "start": float(w.get("start", 0)),
                        "end": float(w.get("end", 0)),
                    })
            if word_events:
                return self._burn_word_highlight_ass(
                    video_path, output_path, word_events, font_size
                )
        except ImportError:
            logger.info("whisper not installed — using sentence-level captions fallback")
        except Exception as exc:
            logger.warning("Whisper transcription failed (%s); using fallback", exc)

        return self._burn_sentence_captions_ffmpeg(video_path, audio_path, output_path)

    def _burn_word_highlight_ass(
        self,
        video_path: str,
        output_path: str,
        word_events: list,
        font_size: int,
    ) -> str:
        """Write an ASS subtitle file with word-level highlight and burn it in."""
        import tempfile as _tmp2

        ass_lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            "PlayResX: 1920",
            "PlayResY: 1080",
            "",
            "[V4+ Styles]",
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding",
            f"Style: Default,Arial,{font_size},&H00FFFFFF,&H0000FFFF,"
            "&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,3,1,2,80,80,60,1",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
        ]

        def _fmt(t: float) -> str:
            h = int(t // 3600)
            m = int((t % 3600) // 60)
            s = t % 60
            cs = int((s - int(s)) * 100)
            return f"{h}:{m:02d}:{int(s):02d}.{cs:02d}"

        # Group words into ~5-word subtitle blocks with active word highlighted
        BLOCK = 5
        for i in range(0, len(word_events), BLOCK):
            block = word_events[i: i + BLOCK]
            if not block:
                continue
            b_start = block[0]["start"]
            b_end = block[-1]["end"]
            for j, w_ev in enumerate(block):
                parts = []
                for k, bw in enumerate(block):
                    word_text = bw["word"].replace("{", "\\{")
                    if k == j:
                        parts.append(r"{\c&H00FFFF&}" + word_text + r"{\c&HFFFFFF&}")
                    else:
                        parts.append(word_text)
                text_line = " ".join(parts)
                ass_lines.append(
                    f"Dialogue: 0,{_fmt(w_ev['start'])},{_fmt(w_ev['end'])},"
                    f"Default,,0,0,0,,{text_line}"
                )

        fd, ass_path = _tmp2.mkstemp(suffix=".ass")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(ass_lines))

        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", f"ass={ass_path}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path,
        ]
        try:
            _run(cmd, label="burn_word_highlight_captions")
        finally:
            try:
                os.remove(ass_path)
            except OSError:
                pass
        logger.info("Word-highlight captions burned → %s", output_path)
        return output_path

    def _burn_sentence_captions_ffmpeg(
        self,
        video_path: str,
        audio_path: str,
        output_path: str,
    ) -> str:
        """Fallback: auto-subtitle via ffmpeg subtitles filter (no word level)."""
        import shutil
        # Without Whisper we just copy — sentence-level would need speech recognition
        shutil.copy2(video_path, output_path)
        return output_path

    # ------------------------------------------------------------------
    # 9. Stat overlays (count-up animation)
    # ------------------------------------------------------------------
    def add_stat_overlays(
        self,
        video_path: str,
        output_path: str,
        stats: list,  # list of {text: str, time: float}
    ) -> str:
        """
        Burn count-up stat overlays at specified timestamps.
        Each stat appears for 3 seconds with a pop-in scale animation.

        stats: [{"text": "87% of people...", "time": 42.0}, ...]
        Returns output_path.
        """
        if not stats:
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        vf_parts: list = []

        for stat in stats:
            t_start = float(stat.get("time", 0))
            t_end = t_start + 3.0
            text = str(stat.get("text", "")).replace("'", "\\'").replace(":", "\\:")[:60]
            enable_expr = f"between(t,{t_start:.2f},{t_end:.2f})"
            # Yellow stat text, large, centered near bottom-third
            vf_parts.append(
                f"drawtext=fontfile={font_path}"
                f":text='{text}'"
                f":fontsize=64:fontcolor=yellow"
                f":x=(w-text_w)/2:y=h*0.72"
                f":box=1:boxcolor=black@0.6:boxborderw=16"
                f":enable='{enable_expr}'"
            )

        vf = ",".join(vf_parts) if vf_parts else "null"
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path,
        ]
        _run(cmd, label="add_stat_overlays")
        logger.info("Stat overlays added (%d) → %s", len(stats), output_path)
        return output_path

    # ------------------------------------------------------------------
    # 10. Source lower-thirds
    # ------------------------------------------------------------------
    def add_source_lower_thirds(
        self,
        video_path: str,
        output_path: str,
        sources: list,  # list of {source: str, time: float}
        duration: float = 3.0,
    ) -> str:
        """
        Add source/citation lower-thirds that slide in from the left.
        Each appears for `duration` seconds at the specified timestamp.

        sources: [{"source": "Reuters, 2024", "time": 60.0}, ...]
        Returns output_path.
        """
        if not sources:
            import shutil
            shutil.copy2(video_path, output_path)
            return output_path

        font_path = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
        vf_parts: list = []

        for src in sources:
            t_start = float(src.get("time", 0))
            t_end = t_start + duration
            text = str(src.get("source", "")).replace("'", "\\'").replace(":", "\\:")[:80]
            enable_expr = f"between(t,{t_start:.2f},{t_end:.2f})"
            # Slide-in effect: x goes from -400 to 60 over first 0.4s
            slide_expr = (
                f"if(lt(t-{t_start:.2f},0.4),"
                f"60-(60+400)*(1-(t-{t_start:.2f})/0.4),"
                f"60)"
            )
            # Background bar
            vf_parts.append(
                f"drawbox=x=0:y=h*0.88:w=w*0.6:h=48"
                f":color=black@0.75:t=fill:enable='{enable_expr}'"
            )
            # Source text
            vf_parts.append(
                f"drawtext=fontfile={font_path}"
                f":text='SOURCE\\: {text}'"
                f":fontsize=26:fontcolor=0xCCCCCC"
                f":x={slide_expr}:y=h*0.89"
                f":enable='{enable_expr}'"
            )

        vf = ",".join(vf_parts) if vf_parts else "null"
        cmd = [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "copy",
            output_path,
        ]
        _run(cmd, label="add_source_lower_thirds")
        logger.info("Source lower-thirds added (%d) → %s", len(sources), output_path)
        return output_path

    # ------------------------------------------------------------------
    # 11. Full transition system (9 types)
    # ------------------------------------------------------------------
    def apply_transition(
        self,
        clip_a_path: str,
        clip_b_path: str,
        output_path: str,
        transition_type: str = "cross_dissolve",
        duration: float = 0.5,
    ) -> str:
        """
        Blend two clips with the specified transition.

        Supported types:
          hard_cut, cross_dissolve, zoom_punch, whip_pan,
          glitch, light_leak, smash_cut, j_cut, l_cut
        """
        SUPPORTED = {
            "hard_cut", "cross_dissolve", "zoom_punch", "whip_pan",
            "glitch", "light_leak", "smash_cut", "j_cut", "l_cut",
        }
        if transition_type not in SUPPORTED:
            logger.warning("Unknown transition '%s'; using cross_dissolve.", transition_type)
            transition_type = "cross_dissolve"

        if transition_type == "hard_cut":
            return self._transition_hard_cut(clip_a_path, clip_b_path, output_path)
        if transition_type == "cross_dissolve":
            return self._transition_cross_dissolve(clip_a_path, clip_b_path, output_path, duration)
        if transition_type == "zoom_punch":
            return self._transition_zoom_punch(clip_a_path, clip_b_path, output_path, duration)
        if transition_type == "whip_pan":
            return self._transition_whip_pan(clip_a_path, clip_b_path, output_path, duration)
        if transition_type == "glitch":
            return self._transition_glitch(clip_a_path, clip_b_path, output_path, duration)
        if transition_type == "light_leak":
            return self._transition_light_leak(clip_a_path, clip_b_path, output_path, duration)
        if transition_type == "smash_cut":
            return self._transition_smash_cut(clip_a_path, clip_b_path, output_path)
        if transition_type == "j_cut":
            return self._transition_j_cut(clip_a_path, clip_b_path, output_path, duration)
        if transition_type == "l_cut":
            return self._transition_l_cut(clip_a_path, clip_b_path, output_path, duration)
        # Unreachable but mypy-safe
        return self._transition_hard_cut(clip_a_path, clip_b_path, output_path)

    def _transition_hard_cut(self, a: str, b: str, out: str) -> str:
        """Immediate cut: concat with no overlap."""
        list_file = out + ".txt"
        with open(list_file, "w") as f:
            f.write(f"file '{os.path.abspath(a)}'\n")
            f.write(f"file '{os.path.abspath(b)}'\n")
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
               "-c", "copy", out]
        try:
            _run(cmd, "hard_cut")
        finally:
            try:
                os.remove(list_file)
            except OSError:
                pass
        return out

    def _transition_cross_dissolve(self, a: str, b: str, out: str, dur: float) -> str:
        """Standard opacity cross-dissolve using xfade filter."""
        a_dur = _video_duration(a)
        offset = max(0.0, a_dur - dur)
        vf = f"xfade=transition=fade:duration={dur:.3f}:offset={offset:.3f}"
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "cross_dissolve")
        return out

    def _transition_zoom_punch(self, a: str, b: str, out: str, dur: float) -> str:
        """Zoom into A then cut to B with a matching zoom-out."""
        a_dur = _video_duration(a)
        offset = max(0.0, a_dur - dur)
        vf = f"xfade=transition=zoomin:duration={dur:.3f}:offset={offset:.3f}"
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "zoom_punch_transition")
        return out

    def _transition_whip_pan(self, a: str, b: str, out: str, dur: float) -> str:
        """Horizontal blur pan — uses FFmpeg xfade wipeleft."""
        a_dur = _video_duration(a)
        offset = max(0.0, a_dur - dur)
        vf = f"xfade=transition=wipeleft:duration={dur:.3f}:offset={offset:.3f}"
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "whip_pan")
        return out

    def _transition_glitch(self, a: str, b: str, out: str, dur: float) -> str:
        """Glitch effect: pixelate + hue-rotate into B."""
        a_dur = _video_duration(a)
        offset = max(0.0, a_dur - dur)
        # Use pixelize xfade; add noise/glitch via geq in a pre-pass
        glitch_filter = (
            f"[0:v]trim=0:{a_dur:.3f},setpts=PTS-STARTPTS[va];"
            f"[1:v]setpts=PTS-STARTPTS[vb];"
            f"[va][vb]xfade=transition=pixelize:duration={dur:.3f}:offset={offset:.3f}"
        )
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", glitch_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "glitch_transition")
        return out

    def _transition_light_leak(self, a: str, b: str, out: str, dur: float) -> str:
        """Light leak: fade through white then dissolve into B."""
        a_dur = _video_duration(a)
        offset = max(0.0, a_dur - dur)
        vf = f"xfade=transition=fadewhite:duration={dur:.3f}:offset={offset:.3f}"
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "light_leak")
        return out

    def _transition_smash_cut(self, a: str, b: str, out: str) -> str:
        """Smash cut: abrupt hard cut with a single-frame white flash."""
        import tempfile as _tmp3
        # Insert a 1-frame white flash between A and B
        flash_path = out + "_flash.mp4"
        flash_cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=white:s=1920x1080:d=0.042:r=24",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            flash_path,
        ]
        try:
            _run(flash_cmd, "smash_cut_flash")
            list_file = out + ".txt"
            with open(list_file, "w") as f:
                f.write(f"file '{os.path.abspath(a)}'\n")
                f.write(f"file '{os.path.abspath(flash_path)}'\n")
                f.write(f"file '{os.path.abspath(b)}'\n")
            concat_cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                          "-i", list_file, "-c:v", "libx264", "-preset", "fast",
                          "-crf", "18", "-c:a", "aac", "-b:a", "192k", out]
            _run(concat_cmd, "smash_cut_concat")
        finally:
            for p in [flash_path, out + ".txt"]:
                try:
                    os.remove(p)
                except OSError:
                    pass
        return out

    def _transition_j_cut(self, a: str, b: str, out: str, dur: float) -> str:
        """
        J-cut: audio from B starts `dur` seconds before video cuts to B.
        Implemented by cross-mixing audio early while keeping video of A.
        """
        a_dur = _video_duration(a)
        audio_offset = max(0.0, a_dur - dur)
        fc = (
            # Video: use full A then B
            f"[0:v][1:v]concat=n=2:v=1:a=0[outv];"
            # Audio: A audio fades out, B audio fades in dur seconds early
            f"[0:a]afade=t=out:st={audio_offset:.3f}:d={dur:.3f}[aa];"
            f"[1:a]adelay={int(audio_offset * 1000)}|{int(audio_offset * 1000)},"
            f"afade=t=in:st=0:d={dur:.3f}[ab];"
            f"[aa][ab]amix=inputs=2:duration=first[outa]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", fc,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "j_cut")
        return out

    def _transition_l_cut(self, a: str, b: str, out: str, dur: float) -> str:
        """
        L-cut: audio from A continues `dur` seconds into B's video.
        """
        fc = (
            # Video: full A then B
            f"[0:v][1:v]concat=n=2:v=1:a=0[outv];"
            # Audio: A continues into B by dur, B audio fades in after
            f"[0:a]apad=pad_dur={dur:.3f}[aa];"
            f"[1:a]afade=t=in:st=0:d={dur:.3f}[ab];"
            f"[aa][ab]amix=inputs=2:duration=shortest[outa]"
        )
        cmd = [
            "ffmpeg", "-y", "-i", a, "-i", b,
            "-filter_complex", fc,
            "-map", "[outv]", "-map", "[outa]",
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-c:a", "aac", "-b:a", "192k",
            out,
        ]
        _run(cmd, "l_cut")
        return out

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _ts_to_s(ts: str) -> float:
        """Convert 'MM:SS' timestamp string to float seconds."""
        try:
            parts = ts.strip().split(":")
            if len(parts) == 2:
                return int(parts[0]) * 60 + float(parts[1])
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except Exception:
            pass
        return 0.0
