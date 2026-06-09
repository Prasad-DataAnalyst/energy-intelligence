"""
modules/evolution/quality_comparator.py
Compares old vs new video quality. Scores both and shows improvement.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_ffprobe(path: str) -> List[dict]:
    """Return list of stream dicts from ffprobe, or [] on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_streams",
        "-of", "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("ffprobe failed on %s: %s", path, result.stderr[:200])
        return []
    try:
        return json.loads(result.stdout).get("streams", [])
    except (json.JSONDecodeError, KeyError):
        return []


def _run_ffprobe_format(path: str) -> dict:
    """Return format section from ffprobe, or {} on failure."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format",
        "-of", "json",
        path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning("ffprobe (format) failed on %s: %s", path, result.stderr[:200])
        return {}
    try:
        return json.loads(result.stdout).get("format", {})
    except (json.JSONDecodeError, KeyError):
        return {}


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Main class
# ---------------------------------------------------------------------------

class QualityComparator:
    """
    Analyses and compares old vs new video files across visual, audio, and
    content quality dimensions.
    """

    # ------------------------------------------------------------------
    # 1. Analyse a single video
    # ------------------------------------------------------------------
    def analyze_video(self, video_path: str) -> dict:
        """
        Return a nested quality-metrics dict for *video_path*.

        Structure
        ---------
        {
            "visual": {
                "resolution_score": float,   # 0-100
                "sharpness_score":  float,   # 0-100
                "color_richness":   float,   # 0-100
                "motion_smoothness":float,   # 0-100
                "brightness_balance":float,  # 0-100
            },
            "audio": {
                "loudness_score":   float,
                "dynamic_range":    float,
                "clarity_score":    float,
                "noise_floor":      float,
            },
            "content": {
                "duration_score":   float,
                "scene_variety":    float,
            },
        }

        Any sub-score that cannot be computed falls back to a neutral 50.
        """
        metrics: dict = {
            "visual": {},
            "audio": {},
            "content": {},
        }

        streams = _run_ffprobe(path=video_path)
        fmt = _run_ffprobe_format(path=video_path)
        duration = _safe_float(fmt.get("duration"), 0.0)

        # --- Visual metrics (OpenCV) ---
        metrics["visual"] = self._visual_metrics(video_path, streams)

        # --- Audio metrics (librosa + ffprobe) ---
        metrics["audio"] = self._audio_metrics(video_path, streams)

        # --- Content metrics ---
        metrics["content"] = self._content_metrics(duration, metrics["visual"])

        return metrics

    # ------------------------------------------------------------------
    # Visual helpers
    # ------------------------------------------------------------------
    def _visual_metrics(self, video_path: str, streams: List[dict]) -> dict:
        defaults = {
            "resolution_score": 50.0,
            "sharpness_score": 50.0,
            "color_richness": 50.0,
            "motion_smoothness": 50.0,
            "brightness_balance": 50.0,
        }

        # Resolution from ffprobe
        video_streams = [s for s in streams if s.get("codec_type") == "video"]
        if video_streams:
            vs = video_streams[0]
            w = _safe_float(vs.get("width"), 0)
            h = _safe_float(vs.get("height"), 0)
            defaults["resolution_score"] = self._resolution_score(int(w), int(h))

        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            logger.warning("cv2/numpy not available; visual sub-scores will be defaults.")
            return defaults

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            logger.warning("cv2 cannot open %s; using default visual scores.", video_path)
            return defaults

        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        interval_frames = max(1, int(fps * 5))   # sample every 5 s

        sharpness_vals: List[float] = []
        mean_lum_vals: List[float] = []
        hist_spreads: List[float] = []
        frame_diffs: List[float] = []
        prev_gray: Optional[Any] = None

        frame_idx = 0
        while True:
            cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
            ret, frame = cap.read()
            if not ret:
                break

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            # Sharpness: Laplacian variance
            lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
            sharpness_vals.append(lap_var)

            # Brightness: mean luminance in YCrCb
            ycrcb = cv2.cvtColor(frame, cv2.COLOR_BGR2YCrCb)
            mean_lum_vals.append(float(np.mean(ycrcb[:, :, 0])))

            # Color richness: std of each BGR histogram
            spread = 0.0
            for ch in range(3):
                hist = cv2.calcHist([frame], [ch], None, [256], [0, 256])
                spread += float(np.std(hist))
            hist_spreads.append(spread / 3.0)

            # Motion smoothness: mean abs frame diff
            if prev_gray is not None:
                diff = float(np.mean(np.abs(gray.astype(float) - prev_gray.astype(float))))
                frame_diffs.append(diff)
            prev_gray = gray

            frame_idx += interval_frames
            if frame_idx >= total:
                break

        cap.release()

        # Normalise scores
        if sharpness_vals:
            mean_lap = float(sum(sharpness_vals) / len(sharpness_vals))
            # >500 → 100, <50 → 0, linear in between
            defaults["sharpness_score"] = _clamp((mean_lap - 50) / (500 - 50) * 100)

        if hist_spreads:
            # Typical spread 800–3000; normalise
            mean_spread = float(sum(hist_spreads) / len(hist_spreads))
            defaults["color_richness"] = _clamp((mean_spread - 200) / (3000 - 200) * 100)

        if frame_diffs:
            mean_diff = float(sum(frame_diffs) / len(frame_diffs))
            # Too low (<1) → static, too high (>30) → choppy; peak at 5–15
            if mean_diff < 1.0:
                smoothness = 20.0
            elif mean_diff > 30.0:
                smoothness = max(0.0, 100.0 - (mean_diff - 30.0) * 3.0)
            else:
                smoothness = _clamp(40.0 + (mean_diff - 1.0) / 14.0 * 60.0)
            defaults["motion_smoothness"] = smoothness

        if mean_lum_vals:
            mean_lum = float(sum(mean_lum_vals) / len(mean_lum_vals))
            # Ideal 90–160 out of 255; penalise too dark (<60) or too bright (>200)
            if 90 <= mean_lum <= 160:
                brightness = 100.0
            elif mean_lum < 90:
                brightness = _clamp(mean_lum / 90.0 * 100.0)
            else:
                brightness = _clamp((255.0 - mean_lum) / (255.0 - 160.0) * 100.0)
            defaults["brightness_balance"] = brightness

        return defaults

    @staticmethod
    def _resolution_score(w: int, h: int) -> float:
        if w >= 1920 and h >= 1080:
            return 100.0
        if w >= 1280 and h >= 720:
            return 70.0
        if w >= 854 and h >= 480:
            return 45.0
        if w > 0 and h > 0:
            return 20.0
        return 0.0

    # ------------------------------------------------------------------
    # Audio helpers
    # ------------------------------------------------------------------
    def _audio_metrics(self, video_path: str, streams: List[dict]) -> dict:
        defaults = {
            "loudness_score": 50.0,
            "dynamic_range": 50.0,
            "clarity_score": 50.0,
            "noise_floor": 50.0,
        }

        audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
        if not audio_streams:
            logger.debug("No audio streams found in %s.", video_path)
            return defaults

        try:
            import librosa  # type: ignore
            import numpy as np  # type: ignore
        except ImportError:
            logger.warning("librosa/numpy not available; audio sub-scores will be defaults.")
            return defaults

        try:
            y, sr = librosa.load(video_path, duration=60, mono=True)
        except Exception as exc:
            logger.warning("librosa.load failed on %s: %s", video_path, exc)
            return defaults

        if len(y) == 0:
            return defaults

        import numpy as np

        # RMS → approximate LUFS (rough approximation)
        rms = float(np.sqrt(np.mean(y ** 2)))
        if rms > 0:
            lufs_approx = 20 * np.log10(rms) - 23  # rough offset to LUFS-like value
        else:
            lufs_approx = -70.0

        # Loudness score: peak at -14 LUFS ± 1 dB
        ideal_lufs = -14.0
        lufs_dev = abs(lufs_approx - ideal_lufs)
        defaults["loudness_score"] = _clamp(100.0 - lufs_dev * 6.0)

        # Dynamic range: difference between 95th and 5th percentile of abs amplitude
        amp = np.abs(y)
        p95 = float(np.percentile(amp, 95))
        p05 = float(np.percentile(amp, 5))
        dr_db = 20 * np.log10(p95 / (p05 + 1e-9))
        # Score: 100 if >6 dB, linearly degrades below 6 dB
        defaults["dynamic_range"] = _clamp((dr_db / 6.0) * 100.0)

        # Clarity: mid-frequency energy ratio (300–3400 Hz → speech band)
        n_fft = 2048
        hop = n_fft // 2
        S = np.abs(librosa.stft(y, n_fft=n_fft, hop_length=hop))
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
        speech_mask = (freqs >= 300) & (freqs <= 3400)
        total_energy = float(np.sum(S ** 2)) + 1e-9
        speech_energy = float(np.sum(S[speech_mask, :] ** 2))
        clarity_ratio = speech_energy / total_energy
        # Typical speech-dominant audio: 0.4–0.7
        defaults["clarity_score"] = _clamp((clarity_ratio - 0.1) / 0.6 * 100.0)

        # Noise floor: 10th-percentile RMS in 1-second windows
        frame_len = sr
        n_frames = len(y) // frame_len
        if n_frames > 0:
            frame_rms = [
                float(np.sqrt(np.mean(y[i * frame_len:(i + 1) * frame_len] ** 2)))
                for i in range(n_frames)
            ]
            noise_rms = float(np.percentile(frame_rms, 10))
            noise_db = 20 * np.log10(noise_rms + 1e-9)
            # Quieter floor → better; target < -40 dBFS
            defaults["noise_floor"] = _clamp(-noise_db / 60.0 * 100.0)

        return defaults

    # ------------------------------------------------------------------
    # Content helpers
    # ------------------------------------------------------------------
    def _content_metrics(self, duration: float, visual: dict) -> dict:
        # Duration score
        if duration >= 8 * 60 and duration <= 12 * 60:
            duration_score = 100.0
        elif duration >= 5 * 60:
            duration_score = 80.0
        elif duration >= 2 * 60:
            duration_score = 60.0
        elif duration > 0:
            duration_score = 30.0
        else:
            duration_score = 0.0

        # Scene variety: variance of motion_smoothness is not available at this
        # stage, so we use the computed motion_smoothness as a proxy.
        # High motion with variance implies scene changes → more variety.
        motion = visual.get("motion_smoothness", 50.0)
        # Remap: 30–70 motion → higher variety; extremes → less variety
        scene_variety = 100.0 - abs(motion - 55.0) * 1.5
        scene_variety = _clamp(scene_variety)

        return {
            "duration_score": duration_score,
            "scene_variety": scene_variety,
        }

    # ------------------------------------------------------------------
    # 2. Compare two videos
    # ------------------------------------------------------------------
    def compare(self, old_path: str, new_path: str) -> dict:
        """
        Analyse both videos and return a comparison dict with overall scores,
        per-metric breakdowns, and a list of human-readable improvements.

        Parameters
        ----------
        old_path: Path to the reference (older) video.
        new_path: Path to the improved (newer) video.

        Returns
        -------
        {
            "old_score":       float,
            "new_score":       float,
            "improvement_pct": float,
            "old_metrics":     dict,
            "new_metrics":     dict,
            "improvements":    List[str],
        }
        """
        logger.info("Analysing OLD video: %s", old_path)
        old_metrics = self.analyze_video(old_path)
        logger.info("Analysing NEW video: %s", new_path)
        new_metrics = self.analyze_video(new_path)

        old_score = self._weighted_score(old_metrics)
        new_score = self._weighted_score(new_metrics)

        if old_score > 0:
            improvement = (new_score - old_score) / old_score * 100.0
        else:
            improvement = 0.0

        improvements = self._list_improvements(old_metrics, new_metrics)

        return {
            "old_score": round(old_score, 1),
            "new_score": round(new_score, 1),
            "improvement_pct": round(improvement, 1),
            "old_metrics": old_metrics,
            "new_metrics": new_metrics,
            "improvements": improvements,
        }

    @staticmethod
    def _weighted_score(metrics: dict) -> float:
        """Compute a single weighted score from metric groups."""
        weights = {"visual": 0.40, "audio": 0.30, "content": 0.30}

        group_scores: dict[str, float] = {}
        for group, w in weights.items():
            group_data = metrics.get(group, {})
            if not group_data:
                group_scores[group] = 50.0
                continue
            vals = [v for v in group_data.values() if isinstance(v, (int, float))]
            group_scores[group] = sum(vals) / len(vals) if vals else 50.0

        return sum(group_scores[g] * weights[g] for g in weights)

    @staticmethod
    def _list_improvements(old: dict, new: dict) -> List[str]:
        """
        Produce a list of human-readable improvement strings for metrics that
        improved by at least 5 points.
        """
        label_map = {
            "visual.resolution_score":   "Resolution",
            "visual.sharpness_score":    "Sharpness",
            "visual.color_richness":     "Color richness",
            "visual.motion_smoothness":  "Motion smoothness",
            "visual.brightness_balance": "Brightness balance",
            "audio.loudness_score":      "Audio loudness",
            "audio.dynamic_range":       "Dynamic range",
            "audio.clarity_score":       "Clarity (speech intelligibility)",
            "audio.noise_floor":         "Noise floor",
            "content.duration_score":    "Video duration",
            "content.scene_variety":     "Scene variety",
        }

        improvements: List[str] = []
        for key, label in label_map.items():
            group, metric = key.split(".", 1)
            old_val = _safe_float(old.get(group, {}).get(metric), 0.0)
            new_val = _safe_float(new.get(group, {}).get(metric), 0.0)
            delta = new_val - old_val
            if delta >= 5.0:
                improvements.append(
                    f"{label} improved by {delta:.1f} points "
                    f"({old_val:.0f} → {new_val:.0f})"
                )

        if not improvements:
            improvements.append("No significant improvements detected across metrics.")

        return improvements

    # ------------------------------------------------------------------
    # 3. Generate formatted report
    # ------------------------------------------------------------------
    def generate_report(self, comparison: dict, out_path: str) -> str:
        """
        Write a formatted quality comparison report to *out_path*.

        Parameters
        ----------
        comparison: Dict returned by :meth:`compare`.
        out_path:   File path where the report will be saved.

        Returns
        -------
        out_path on success.
        """
        old = comparison.get("old_metrics", {})
        new = comparison.get("new_metrics", {})
        improvements = comparison.get("improvements", [])

        def g(metrics: dict, group: str, key: str) -> str:
            val = metrics.get(group, {}).get(key, None)
            return f"{val:.0f}" if val is not None else "N/A"

        lines: List[str] = [
            "═══════════════════════════════════════════════",
            "QUALITY COMPARISON REPORT",
            f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "═══════════════════════════════════════════════",
            f"OLD VIDEO SCORE: {comparison.get('old_score', 'N/A')}/100",
            f"NEW VIDEO SCORE: {comparison.get('new_score', 'N/A')}/100",
            f"IMPROVEMENT:     +{comparison.get('improvement_pct', 'N/A')}%",
            "",
            "── VISUAL ──────────────────────────────────",
            (
                f"Resolution:    OLD {g(old,'visual','resolution_score')}/100 "
                f"→ NEW {g(new,'visual','resolution_score')}/100"
            ),
            (
                f"Sharpness:     OLD {g(old,'visual','sharpness_score')}/100 "
                f"→ NEW {g(new,'visual','sharpness_score')}/100"
            ),
            (
                f"Color:         OLD {g(old,'visual','color_richness')}/100 "
                f"→ NEW {g(new,'visual','color_richness')}/100"
            ),
            (
                f"Motion:        OLD {g(old,'visual','motion_smoothness')}/100 "
                f"→ NEW {g(new,'visual','motion_smoothness')}/100"
            ),
            (
                f"Brightness:    OLD {g(old,'visual','brightness_balance')}/100 "
                f"→ NEW {g(new,'visual','brightness_balance')}/100"
            ),
            "",
            "── AUDIO ───────────────────────────────────",
            (
                f"Loudness:      OLD {g(old,'audio','loudness_score')}/100 "
                f"→ NEW {g(new,'audio','loudness_score')}/100"
            ),
            (
                f"Dynamic Range: OLD {g(old,'audio','dynamic_range')}/100 "
                f"→ NEW {g(new,'audio','dynamic_range')}/100"
            ),
            (
                f"Clarity:       OLD {g(old,'audio','clarity_score')}/100 "
                f"→ NEW {g(new,'audio','clarity_score')}/100"
            ),
            (
                f"Noise Floor:   OLD {g(old,'audio','noise_floor')}/100 "
                f"→ NEW {g(new,'audio','noise_floor')}/100"
            ),
            "",
            "── CONTENT ─────────────────────────────────",
            (
                f"Duration:      OLD {g(old,'content','duration_score')}/100 "
                f"→ NEW {g(new,'content','duration_score')}/100"
            ),
            (
                f"Scene Variety: OLD {g(old,'content','scene_variety')}/100 "
                f"→ NEW {g(new,'content','scene_variety')}/100"
            ),
            "",
            "KEY IMPROVEMENTS:",
        ]

        for imp in improvements:
            lines.append(f"  {imp}")

        lines.append("═══════════════════════════════════════════════")

        report_text = "\n".join(lines)

        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(report_text)
            fh.write("\n")

        logger.info("Quality report written → %s", out_path)
        return out_path
