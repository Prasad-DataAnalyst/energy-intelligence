"""
modules/production/cinematic_audio_engine.py
Broadcast-quality audio: edge-tts → noise reduction → EQ → compression → loudness normalize
Replaces: audio_generator.py (espeak-ng)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import shutil
import subprocess
import tempfile
import wave
from typing import List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Voice maps
# ---------------------------------------------------------------------------

VOICE_MAP: dict[str, str] = {
    "authoritative": "en-US-GuyNeural",
    "warm":          "en-US-AriaNeural",
    "urgent":        "en-US-DavisNeural",
    "calm":          "en-US-JennyNeural",
    "energetic":     "en-US-TonyNeural",
}

# Chord frequencies (Am - F - C - G progression, root notes)
# Am: A3-C4-E4  F: F3-A3-C4  C: C3-E3-G3  G: G3-B3-D4
CHORD_PROG = [
    [220.00, 261.63, 329.63],   # Am
    [174.61, 220.00, 261.63],   # F
    [130.81, 164.81, 196.00],   # C
    [196.00, 246.94, 293.66],   # G
]

BPM = 95


# ---------------------------------------------------------------------------
# Helper: write raw PCM array as WAV
# ---------------------------------------------------------------------------

def _save_wav(y: np.ndarray, sr: int, path: str) -> None:
    """Write float32 array (−1..1) to a mono WAV file."""
    y_clipped = np.clip(y, -1.0, 1.0)
    y_i16 = (y_clipped * 32767).astype(np.int16)
    with wave.open(path, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(y_i16.tobytes())


# ---------------------------------------------------------------------------
# Fallback espeak-ng helper
# ---------------------------------------------------------------------------

def _espeak_fallback(text: str, out_path: str) -> bool:
    """Try generating audio via espeak-ng. Return True on success."""
    try:
        result = subprocess.run(
            ["espeak-ng", "-v", "en-us+f3", "-s", "165", "-w", out_path, text],
            capture_output=True, text=True, timeout=60,
        )
        if result.returncode == 0 and os.path.exists(out_path):
            logger.info("Voiceover generated via espeak-ng (fallback2)")
            return True
        logger.warning("espeak-ng failed: %s", result.stderr[:200])
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("espeak-ng not available: %s", exc)
    return False


# ---------------------------------------------------------------------------
# ElevenLabs fallback helper
# ---------------------------------------------------------------------------

def _elevenlabs_fallback(text: str, out_path: str) -> bool:
    """Try ElevenLabs API if ELEVENLABS_API_KEY is set. Return True on success."""
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return False
    try:
        import requests  # type: ignore

        voice_id = "21m00Tcm4TlvDq8ikWAM"  # Rachel — authoritative, clear
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "xi-api-key": api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "text": text,
            "model_id": "eleven_monolingual_v1",
            "voice_settings": {"stability": 0.55, "similarity_boost": 0.75},
        }
        resp = requests.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code == 200:
            mp3_path = out_path.rsplit(".", 1)[0] + "_el.mp3"
            with open(mp3_path, "wb") as f:
                f.write(resp.content)
            # Convert mp3 → wav via pydub if available
            try:
                from pydub import AudioSegment  # type: ignore
                seg = AudioSegment.from_mp3(mp3_path)
                seg.export(out_path, format="wav")
                os.remove(mp3_path)
            except Exception:
                shutil.move(mp3_path, out_path)
            logger.info("Voiceover generated via ElevenLabs (fallback1)")
            return True
        logger.warning("ElevenLabs returned HTTP %d", resp.status_code)
    except Exception as exc:
        logger.warning("ElevenLabs error: %s", exc)
    return False


# ---------------------------------------------------------------------------
# EQ helpers using scipy biquad filters
# ---------------------------------------------------------------------------

def _design_shelf(sr: int, freq: float, gain_db: float, shelf_type: str,
                  Q: float = 0.707) -> tuple:
    """
    Design a low or high shelf biquad filter.
    shelf_type: 'low' or 'high'.
    Returns (b, a) coefficients for scipy.signal.filtfilt.
    """
    from scipy import signal  # type: ignore

    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq / sr
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2 * Q)

    if shelf_type == "low":
        b0 =  A * ((A + 1) - (A - 1) * cos_w0 + 2 * math.sqrt(A) * alpha)
        b1 =  2 * A * ((A - 1) - (A + 1) * cos_w0)
        b2 =  A * ((A + 1) - (A - 1) * cos_w0 - 2 * math.sqrt(A) * alpha)
        a0 =      (A + 1) + (A - 1) * cos_w0 + 2 * math.sqrt(A) * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cos_w0)
        a2 =      (A + 1) + (A - 1) * cos_w0 - 2 * math.sqrt(A) * alpha
    else:  # high
        b0 =  A * ((A + 1) + (A - 1) * cos_w0 + 2 * math.sqrt(A) * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cos_w0)
        b2 =  A * ((A + 1) + (A - 1) * cos_w0 - 2 * math.sqrt(A) * alpha)
        a0 =      (A + 1) - (A - 1) * cos_w0 + 2 * math.sqrt(A) * alpha
        a1 =  2 * ((A - 1) - (A + 1) * cos_w0)
        a2 =      (A + 1) - (A - 1) * cos_w0 - 2 * math.sqrt(A) * alpha

    return (
        np.array([b0 / a0, b1 / a0, b2 / a0]),
        np.array([1.0, a1 / a0, a2 / a0]),
    )


def _design_peak(sr: int, freq: float, gain_db: float, Q: float = 2.0) -> tuple:
    """Design a peaking EQ biquad filter. Returns (b, a)."""
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * math.pi * freq / sr
    cos_w0 = math.cos(w0)
    sin_w0 = math.sin(w0)
    alpha = sin_w0 / (2 * Q)

    b0 = 1 + alpha * A
    b1 = -2 * cos_w0
    b2 = 1 - alpha * A
    a0 = 1 + alpha / A
    a1 = -2 * cos_w0
    a2 = 1 - alpha / A

    return (
        np.array([b0 / a0, b1 / a0, b2 / a0]),
        np.array([1.0, a1 / a0, a2 / a0]),
    )


# ---------------------------------------------------------------------------
# ADSR envelope
# ---------------------------------------------------------------------------

def _adsr(n: int, sr: int, attack_s: float, decay_s: float,
          sustain_level: float, release_s: float) -> np.ndarray:
    env = np.zeros(n)
    att = min(int(attack_s * sr), n)
    dec = min(int(decay_s * sr), n - att)
    rel = min(int(release_s * sr), n)
    sus_end = n - rel

    if att > 0:
        env[:att] = np.linspace(0, 1, att)
    if dec > 0:
        env[att:att + dec] = np.linspace(1, sustain_level, dec)
    if att + dec < sus_end:
        env[att + dec:sus_end] = sustain_level
    if rel > 0 and sus_end >= 0:
        env[sus_end:sus_end + rel] = np.linspace(sustain_level, 0, rel)

    return env


# ---------------------------------------------------------------------------
# Simple reverb (Schroeder allpass + comb)
# ---------------------------------------------------------------------------

def _simple_reverb(y: np.ndarray, sr: int, room_size: float = 0.4,
                   wet: float = 0.18) -> np.ndarray:
    """Very lightweight algorithmic reverb using delay lines."""
    # Comb filter delays in ms (Schroeder values scaled by room_size)
    delays_ms = [29.7, 37.1, 41.1, 43.7]
    comb_gain = 0.82 * room_size + 0.10

    out = np.zeros_like(y)
    for d_ms in delays_ms:
        d = int(d_ms * sr / 1000)
        if d >= len(y):
            continue
        delayed = np.zeros_like(y)
        delayed[d:] = y[:-d] if d > 0 else y
        out += comb_gain * delayed

    # Simple allpass
    ap_delay = int(5 * sr / 1000)
    if ap_delay < len(y):
        ap_gain = 0.5
        ap_out = np.zeros_like(out)
        for i in range(ap_delay, len(out)):
            ap_out[i] = -ap_gain * out[i] + out[i - ap_delay] + ap_gain * ap_out[i - ap_delay]
        out = ap_out

    return y * (1 - wet) + out * wet


# ===========================================================================
# Main class
# ===========================================================================

class CinematicAudioEngine:
    """
    Broadcast-quality audio pipeline:
      edge-tts → noisereduce → EQ → compression → pyloudnorm → WAV 44100 Hz
    """

    def __init__(self, tmp_dir: Optional[str] = None) -> None:
        self.tmp_dir = tmp_dir or tempfile.gettempdir()
        os.makedirs(self.tmp_dir, exist_ok=True)

    # -----------------------------------------------------------------------
    # 1. Voiceover generation
    # -----------------------------------------------------------------------

    async def generate_voiceover(
        self, text: str, out_path: str, tone: str = "authoritative"
    ) -> str:
        """
        Generate broadcast-quality voiceover audio.

        Primary:   edge-tts (free, Microsoft neural voices)
        Fallback1: ElevenLabs (if ELEVENLABS_API_KEY set)
        Fallback2: espeak-ng
        Fallback3: silent WAV (never crashes)

        Audio is run through the full post-processing pipeline after generation.
        Returns out_path.
        """
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        raw_path = os.path.join(
            self.tmp_dir, f"voice_raw_{os.getpid()}.wav"
        )

        generated = False

        # ── Primary: edge-tts ──────────────────────────────────────────────
        try:
            import edge_tts  # type: ignore

            voice = VOICE_MAP.get(tone, "en-US-AriaNeural")
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(raw_path)

            if os.path.exists(raw_path) and os.path.getsize(raw_path) > 0:
                logger.info("Voiceover generated via edge-tts voice=%s", voice)
                generated = True
            else:
                logger.warning("edge-tts produced empty file")
        except ImportError:
            logger.warning("edge-tts not installed; pip install edge-tts")
        except Exception as exc:
            logger.warning("edge-tts error: %s", exc)

        # ── Fallback 1: ElevenLabs ─────────────────────────────────────────
        if not generated:
            if _elevenlabs_fallback(text, raw_path):
                generated = True

        # ── Fallback 2: espeak-ng ──────────────────────────────────────────
        if not generated:
            if _espeak_fallback(text, raw_path):
                generated = True

        # ── Fallback 3: 1 second of silence so pipeline never crashes ──────
        if not generated:
            logger.error("All TTS backends failed — generating silent WAV")
            sr_fallback = 44100
            silence = np.zeros(sr_fallback, dtype=np.float32)
            _save_wav(silence, sr_fallback, raw_path)

        # ── Post-processing ────────────────────────────────────────────────
        try:
            result = self._post_process_audio(raw_path, out_path)
        except Exception as exc:
            logger.error("Post-processing failed (%s); copying raw audio", exc)
            shutil.copy2(raw_path, out_path)
            result = out_path
        finally:
            try:
                os.remove(raw_path)
            except OSError:
                pass

        size_kb = os.path.getsize(result) // 1024 if os.path.exists(result) else 0
        logger.info("Voiceover ready: %s  (%d KB)", result, size_kb)
        return result

    # -----------------------------------------------------------------------
    # 2. Audio post-processing pipeline
    # -----------------------------------------------------------------------

    def _post_process_audio(self, input_path: str, output_path: str) -> str:
        """
        Five-stage broadcast voice chain:
          1. Load (librosa)
          2. Noise reduction (noisereduce)
          3. EQ (scipy biquad filters)
          4. Dynamic compression
          5. Loudness normalisation to −14 LUFS (YouTube standard)
        """
        # ── Step 1: Load ───────────────────────────────────────────────────
        try:
            import librosa  # type: ignore
            y, sr = librosa.load(input_path, sr=22050, mono=True)
            logger.debug("Loaded %s  sr=%d  samples=%d", input_path, sr, len(y))
        except ImportError:
            logger.warning("librosa not installed; using soundfile fallback")
            import soundfile as sf  # type: ignore
            y, sr = sf.read(input_path, always_2d=False)
            if y.ndim == 2:
                y = y.mean(axis=1)
            y = y.astype(np.float32)

        if len(y) == 0:
            raise ValueError("Audio array is empty after loading")

        # ── Step 2: Noise reduction ────────────────────────────────────────
        try:
            import noisereduce as nr  # type: ignore
            y = nr.reduce_noise(y=y, sr=sr, stationary=True, prop_decrease=0.75)
            logger.debug("Noise reduction applied")
        except ImportError:
            pass  # skip if not installed

        # ── Step 3: Broadcast voice EQ ────────────────────────────────────
        try:
            from scipy import signal  # type: ignore

            # High-pass at 80 Hz — remove low rumble / mic handling noise
            b_hp, a_hp = signal.butter(4, 80 / (sr / 2), btype="high")
            y = signal.filtfilt(b_hp, a_hp, y)

            # Low-shelf +2 dB warmth at 200 Hz
            b_ls, a_ls = _design_shelf(sr, 200, gain_db=2.0, shelf_type="low")
            y = signal.filtfilt(b_ls, a_ls, y)

            # Presence peak +3 dB at 3 kHz — cuts through mix, aids clarity
            b_pk, a_pk = _design_peak(sr, 3000, gain_db=3.0, Q=1.5)
            y = signal.filtfilt(b_pk, a_pk, y)

            # High-shelf gentle cut −2 dB at 8 kHz — tame harshness
            b_hs, a_hs = _design_shelf(sr, 8000, gain_db=-2.0, shelf_type="high")
            y = signal.filtfilt(b_hs, a_hs, y)

            logger.debug("Broadcast EQ applied (HP80 + LS200 + PK3k + HS8k)")
        except ImportError:
            logger.warning("scipy not available; EQ stage skipped")

        # ── Step 4: Dynamic compression (3:1 ratio above −12 dB) ──────────
        threshold = 0.25   # ≈ −12 dBFS
        ratio = 3.0
        abs_y = np.abs(y)
        mask = abs_y > threshold
        y[mask] = (
            np.sign(y[mask])
            * (threshold + (abs_y[mask] - threshold) / ratio)
        )
        logger.debug("Compression applied (threshold=%.2f ratio=%.1f:1)", threshold, ratio)

        # ── Step 5: Loudness normalisation to −14 LUFS ────────────────────
        try:
            import pyloudnorm as pyln  # type: ignore

            # pyloudnorm needs float64 and may need 2-D for stereo; keep mono
            y64 = y.astype(np.float64)
            meter = pyln.Meter(sr)  # EBU R128
            loudness = meter.integrated_loudness(y64)
            if np.isfinite(loudness) and loudness < 0:
                y64 = pyln.normalize.loudness(y64, loudness, -14.0)
            # True-peak limiter at −1 dBTP
            y = np.clip(y64, -0.891, 0.891).astype(np.float32)
            logger.debug("pyloudnorm: %.1f LUFS → −14 LUFS", loudness)
        except ImportError:
            logger.warning("pyloudnorm not available; using RMS normalisation")
            rms = float(np.sqrt(np.mean(y ** 2)))
            if rms > 1e-8:
                target_rms = 0.1
                y = y * (target_rms / rms)
            y = np.clip(y, -0.891, 0.891)

        # ── Step 6: Save as WAV 44100 Hz ──────────────────────────────────
        try:
            import soundfile as sf  # type: ignore
            sf.write(output_path, y, sr, subtype="PCM_16")
        except ImportError:
            _save_wav(y, sr, output_path)

        logger.debug("Post-processed audio saved: %s", output_path)
        return output_path

    # -----------------------------------------------------------------------
    # 3. Background music generation
    # -----------------------------------------------------------------------

    def generate_background_music(
        self, duration_s: float, out_path: str, energy: str = "medium"
    ) -> str:
        """
        Attempt to download royalty-free music from Pixabay.
        Falls back to procedurally generated music (Am–F–C–G, 95 BPM).

        energy: "low" | "medium" | "high"
        """
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        downloaded = self._try_download_music(duration_s, out_path, energy)
        if downloaded:
            return out_path

        logger.info("Generating procedural background music (energy=%s)", energy)
        return self._generate_procedural_music(duration_s, out_path, energy)

    def _try_download_music(
        self, duration_s: float, out_path: str, energy: str
    ) -> bool:
        """Try Pixabay music search + download. Return True if successful."""
        try:
            import requests  # type: ignore

            query_map = {
                "low":    "cinematic ambient calm",
                "medium": "lo-fi background chill",
                "high":   "cinematic energetic uplifting",
            }
            query = query_map.get(energy, "lo-fi background")

            # Pixabay free music API (no auth required for basic search)
            url = "https://pixabay.com/api/music/"
            params = {
                "key": os.environ.get("PIXABAY_API_KEY", ""),
                "q": query,
                "duration_from": max(30, int(duration_s) - 30),
                "per_page": 5,
            }
            # If no key, the endpoint returns 400 — skip silently
            resp = requests.get(url, params=params, timeout=15)
            if resp.status_code != 200:
                logger.debug("Pixabay returned HTTP %d", resp.status_code)
                return False

            data = resp.json()
            hits = data.get("hits", [])
            if not hits:
                return False

            download_url = hits[0].get("audio", {}).get("preview")
            if not download_url:
                return False

            audio_resp = requests.get(download_url, timeout=60, stream=True)
            if audio_resp.status_code != 200:
                return False

            tmp_mp3 = out_path + ".tmp.mp3"
            with open(tmp_mp3, "wb") as f:
                for chunk in audio_resp.iter_content(8192):
                    f.write(chunk)

            # Convert to WAV and loop/trim to duration_s
            self._convert_and_loop(tmp_mp3, out_path, duration_s)
            os.remove(tmp_mp3)
            logger.info("Background music downloaded from Pixabay")
            return True

        except Exception as exc:
            logger.debug("Pixabay download skipped: %s", exc)
            return False

    def _convert_and_loop(self, src: str, dst_wav: str, duration_s: float) -> None:
        """Load src, loop or trim to duration_s, save as WAV."""
        try:
            from pydub import AudioSegment  # type: ignore

            seg = AudioSegment.from_file(src)
            target_ms = int(duration_s * 1000)
            if len(seg) < target_ms:
                reps = math.ceil(target_ms / len(seg))
                seg = seg * reps
            seg = seg[:target_ms]
            seg.export(dst_wav, format="wav")
        except Exception:
            # Try soundfile / librosa
            try:
                import librosa  # type: ignore
                import soundfile as sf  # type: ignore
                y, sr = librosa.load(src, sr=44100, mono=True, duration=duration_s)
                target_n = int(duration_s * sr)
                if len(y) < target_n:
                    reps = math.ceil(target_n / len(y))
                    y = np.tile(y, reps)
                y = y[:target_n]
                sf.write(dst_wav, y, sr)
            except Exception as exc2:
                raise RuntimeError(f"Could not convert {src}: {exc2}") from exc2

    def _generate_procedural_music(
        self, duration_s: float, out_path: str, energy: str = "medium"
    ) -> str:
        """
        Synthesise a full lo-fi ambient track: pad + bass + hi-hat + optional kick.
        Chord progression: Am – F – C – G (95 BPM, each chord lasts 2 beats).
        """
        sr = 44100
        n = int(sr * duration_s)
        t = np.linspace(0, duration_s, n, endpoint=False)
        beat_s = 60.0 / BPM
        chord_dur_s = beat_s * 2  # 2 beats per chord

        rng = np.random.default_rng(42)
        audio = np.zeros(n, dtype=np.float64)

        # ── Pad (sustained chord tones with vibrato + ADSR) ────────────────
        pad = np.zeros(n, dtype=np.float64)
        chord_n = int(chord_dur_s * sr)
        chord_idx = 0
        pos = 0
        while pos < n:
            chord = CHORD_PROG[chord_idx % 4]
            block_len = min(chord_n, n - pos)
            block_t = np.arange(block_len) / sr
            env = _adsr(block_len, sr,
                        attack_s=0.12, decay_s=0.08, sustain_level=0.75, release_s=0.20)
            chord_wave = np.zeros(block_len)
            for freq in chord:
                vib = 1 + 0.003 * np.sin(2 * np.pi * 5.1 * block_t)
                # Fundamental + subtle 2nd harmonic
                chord_wave += (0.6 * np.sin(2 * np.pi * freq * vib * block_t)
                               + 0.15 * np.sin(2 * np.pi * 2 * freq * vib * block_t))
            pad[pos:pos + block_len] += env * chord_wave * (0.045 / len(chord))
            chord_idx += 1
            pos += chord_n

        audio += pad

        # ── Bass line (root note, one octave down, staccato) ───────────────
        bass = np.zeros(n, dtype=np.float64)
        chord_idx = 0
        pos = 0
        while pos < n:
            chord = CHORD_PROG[chord_idx % 4]
            root_freq = chord[0] / 2.0  # one octave down
            block_len = min(chord_n, n - pos)
            note_len = min(int(0.18 * sr), block_len)
            env = _adsr(note_len, sr, 0.01, 0.04, 0.50, 0.08)
            block_t = np.arange(note_len) / sr
            wave = (np.sin(2 * np.pi * root_freq * block_t)
                    + 0.25 * np.sin(2 * np.pi * 2 * root_freq * block_t))
            bass[pos:pos + note_len] += env * wave * 0.10
            chord_idx += 1
            pos += chord_n

        audio += bass

        # ── Hi-hat (every half-beat, subtle) ──────────────────────────────
        if energy in ("medium", "high"):
            hihat = np.zeros(n, dtype=np.float64)
            step_s = beat_s / 2
            k = 0
            while True:
                s = int(k * step_s * sr)
                if s >= n:
                    break
                L = min(int(0.025 * sr), n - s)
                if L > 0:
                    env = np.exp(-np.arange(L) / (0.005 * sr))
                    noise = rng.standard_normal(L)
                    # Accent every 2nd hihat slightly
                    vol = 0.018 if k % 2 == 0 else 0.011
                    hihat[s:s + L] += vol * env * noise
                k += 1
            audio += hihat

        # ── Kick drum (on beat 1 and beat 3) ──────────────────────────────
        if energy in ("medium", "high"):
            kick_beats = [0, 2]  # within a 4-beat bar
            bar_s = beat_s * 4
            kick = np.zeros(n, dtype=np.float64)
            bar = 0
            while True:
                bar_start = int(bar * bar_s * sr)
                if bar_start >= n:
                    break
                for b in kick_beats:
                    s = bar_start + int(b * beat_s * sr)
                    if s >= n:
                        break
                    L = min(int(0.09 * sr), n - s)
                    if L > 0:
                        env = np.exp(-np.arange(L) / (0.018 * sr))
                        sweep = np.linspace(110, 45, L)
                        phase = np.cumsum(sweep) / sr
                        kick[s:s + L] += 0.08 * env * np.sin(2 * np.pi * phase)
                bar += 1
            audio += kick

        # ── Snare / clap on beat 2 and 4 (high energy only) ───────────────
        if energy == "high":
            bar_s = beat_s * 4
            snare = np.zeros(n, dtype=np.float64)
            bar = 0
            while True:
                bar_start = int(bar * bar_s * sr)
                if bar_start >= n:
                    break
                for b in [1, 3]:
                    s = bar_start + int(b * beat_s * sr)
                    if s >= n:
                        break
                    L = min(int(0.05 * sr), n - s)
                    if L > 0:
                        env = np.exp(-np.arange(L) / (0.008 * sr))
                        noise = rng.standard_normal(L)
                        # Tonal element (snare body ~200 Hz)
                        tone_wave = np.sin(2 * np.pi * 200 * np.arange(L) / sr)
                        snare[s:s + L] += 0.04 * env * (0.5 * noise + 0.5 * tone_wave)
                bar += 1
            audio += snare

        # ── Occasional sparse piano note ───────────────────────────────────
        if energy in ("medium", "high"):
            piano_times = np.arange(0, duration_s, beat_s * 8) + beat_s * 4
            piano = np.zeros(n, dtype=np.float64)
            all_tones = [f for chord in CHORD_PROG for f in chord]
            for i, pt in enumerate(piano_times):
                s = int(pt * sr)
                if s >= n:
                    break
                freq = all_tones[i % len(all_tones)] * 2  # upper octave
                L = min(int(1.2 * sr), n - s)
                if L > 0:
                    env = _adsr(L, sr, 0.005, 0.15, 0.30, 0.40)
                    block_t = np.arange(L) / sr
                    piano[s:s + L] += env * 0.025 * np.sin(2 * np.pi * freq * block_t)
            audio += piano

        # ── Reverb ────────────────────────────────────────────────────────
        room = {"low": 0.6, "medium": 0.4, "high": 0.25}.get(energy, 0.4)
        wet = {"low": 0.30, "medium": 0.18, "high": 0.10}.get(energy, 0.18)
        audio = _simple_reverb(audio.astype(np.float32), sr,
                               room_size=room, wet=wet).astype(np.float64)

        # ── Low energy: keep pad only (remove drums already zeroed) ────────
        # (for "low" we simply don't add kick/hihat/snare above)

        # ── Fade in/out ────────────────────────────────────────────────────
        fade_s = min(0.8, duration_s * 0.05)
        fade_n = int(fade_s * sr)
        audio[:fade_n] *= np.linspace(0, 1, fade_n)
        audio[-fade_n:] *= np.linspace(1, 0, fade_n)

        # ── Normalise to −18 dBFS (leaves headroom for ducking) ───────────
        peak = np.max(np.abs(audio))
        if peak > 1e-8:
            audio = audio * (0.126 / peak)   # −18 dBFS

        audio = np.clip(audio, -1.0, 1.0).astype(np.float32)
        _save_wav(audio, sr, out_path)
        size_kb = os.path.getsize(out_path) // 1024
        logger.info("Procedural music written: %s  (%d KB)", out_path, size_kb)
        return out_path

    # -----------------------------------------------------------------------
    # 4. Audio mixing with ducking
    # -----------------------------------------------------------------------

    def mix_audio(
        self,
        voice_path: str,
        music_path: str,
        out_path: str,
        total_s: float,
        duck_level: float = -22.0,
    ) -> str:
        """
        Mix voice + music with proper ducking.

        Voice: 0 dB reference
        Music: duck_level dB when voice is active
               duck_level + 8 dB during silence gaps (brief lift)
        Crossfade in/out: 300 ms
        Final mix: normalised to −14 LUFS.
        """
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

        try:
            from pydub import AudioSegment  # type: ignore
            from pydub.effects import normalize  # type: ignore

            # Load both tracks
            ext_v = os.path.splitext(voice_path)[1].lstrip(".")
            voice = (AudioSegment.from_wav(voice_path) if ext_v == "wav"
                     else AudioSegment.from_file(voice_path, format=ext_v))
            ext_m = os.path.splitext(music_path)[1].lstrip(".")
            music = (AudioSegment.from_wav(music_path) if ext_m == "wav"
                     else AudioSegment.from_file(music_path, format=ext_m))

            target_ms = int(total_s * 1000)

            # Loop music to fill target duration
            if len(music) < target_ms:
                reps = math.ceil(target_ms / len(music))
                music = music * reps
            music = music[:target_ms]

            # Pad or trim voice
            if len(voice) < target_ms:
                silence = AudioSegment.silent(duration=target_ms - len(voice))
                voice = voice + silence
            voice = voice[:target_ms]

            # Convert duck_level dB → linear amplitude ratio
            duck_linear = 10 ** (duck_level / 20.0)           # e.g. −22 dB ≈ 0.079
            lift_linear = 10 ** ((duck_level + 8.0) / 20.0)   # −14 dB during silence

            # Analyse voice for silence (using raw samples)
            voice_samples = np.array(voice.get_array_of_samples(), dtype=np.float32)
            voice_samples /= (2 ** (voice.sample_width * 8 - 1))
            frame_ms = 50
            frame_n = int(frame_ms * voice.frame_rate / 1000)
            silence_thresh = 0.01  # RMS below this → silence

            # Build per-sample gain envelope for music
            music_gain = np.ones(len(voice_samples), dtype=np.float32)
            n_frames = len(voice_samples) // frame_n
            for fi in range(n_frames):
                sl = fi * frame_n
                er = min(sl + frame_n, len(voice_samples))
                rms = float(np.sqrt(np.mean(voice_samples[sl:er] ** 2)))
                target_g = lift_linear if rms < silence_thresh else duck_linear
                music_gain[sl:er] = target_g

            # Smooth the gain envelope (300 ms crossfade = ~6 frames)
            from scipy.ndimage import uniform_filter1d  # type: ignore
            smooth_n = int(0.3 * voice.frame_rate)  # 300 ms in samples
            music_gain = uniform_filter1d(music_gain, size=max(1, smooth_n))

            # Apply gain envelope to music samples
            music_samples = np.array(music.get_array_of_samples(), dtype=np.float32)
            # Handle stereo music (interleaved)
            n_ch = music.channels
            if n_ch == 2:
                gain_stereo = np.repeat(music_gain[:len(music_samples) // 2], 2)
                music_samples = music_samples[:len(gain_stereo)] * gain_stereo
            else:
                music_samples = music_samples[:len(music_gain)] * music_gain

            # Rebuild music AudioSegment from modified samples
            import array as arr
            music_arr = arr.array(music.array_type, music_samples.astype(np.int16))
            music_ducked = music._spawn(music_arr)

            # Overlay voice on ducked music
            mixed = music_ducked.overlay(voice)

            # Normalise final mix
            mixed = normalize(mixed)

            mixed.export(out_path, format="wav")
            size_kb = os.path.getsize(out_path) // 1024
            logger.info("Mixed audio: %s  (%d KB)", out_path, size_kb)
            return out_path

        except ImportError:
            logger.warning("pydub not available — falling back to numpy mix")
            return self._numpy_mix_fallback(
                voice_path, music_path, out_path, total_s, duck_level
            )

    def _numpy_mix_fallback(
        self,
        voice_path: str,
        music_path: str,
        out_path: str,
        total_s: float,
        duck_level: float,
    ) -> str:
        """Simple numpy-based mix without ducking (pydub unavailable)."""
        try:
            import soundfile as sf  # type: ignore
        except ImportError:
            import librosa  # type: ignore
            import soundfile as sf  # type: ignore

        target_n = int(total_s * 44100)

        def _load_and_resize(path: str, target: int) -> np.ndarray:
            try:
                import librosa  # type: ignore
                y, _ = librosa.load(path, sr=44100, mono=True)
            except ImportError:
                import soundfile as sf2  # type: ignore
                y, _ = sf2.read(path, always_2d=False)
                if y.ndim == 2:
                    y = y.mean(axis=1)
            if len(y) < target:
                reps = math.ceil(target / len(y))
                y = np.tile(y, reps)
            return y[:target].astype(np.float32)

        voice = _load_and_resize(voice_path, target_n)
        music = _load_and_resize(music_path, target_n)

        music_gain = 10 ** (duck_level / 20.0)
        mixed = voice + music * music_gain
        peak = np.max(np.abs(mixed))
        if peak > 1e-8:
            mixed = mixed / peak * 0.891

        import soundfile as sf  # type: ignore
        sf.write(out_path, mixed, 44100)
        return out_path

    # -----------------------------------------------------------------------
    # 5. Scene-by-scene audio generation
    # -----------------------------------------------------------------------

    def generate_scene_audio(
        self, scenes: List[dict], out_dir: str
    ) -> List[str]:
        """
        Generate per-scene voiceover WAV files.

        Each scene dict:
          {
            "voiceover":        "Text to speak",
            "tone":             "urgent",          # optional
            "duration_seconds": 8,                 # optional, not enforced on TTS
          }

        Returns list of absolute paths to generated WAV files.
        """
        os.makedirs(out_dir, exist_ok=True)
        results: List[str] = []

        for i, scene in enumerate(scenes):
            text = scene.get("voiceover", "").strip()
            tone = scene.get("tone", "authoritative")
            out_path = os.path.join(out_dir, f"scene_{i:03d}_voice.wav")

            if not text:
                logger.warning("Scene %d has no voiceover text — skipping", i)
                continue

            try:
                path = asyncio.run(
                    self.generate_voiceover(text, out_path, tone=tone)
                )
                results.append(path)
                logger.info("Scene %d audio: %s", i, path)
            except Exception as exc:
                logger.error("Scene %d audio failed: %s", i, exc)
                # Produce silent placeholder so pipeline doesn't stall
                sr = 44100
                dur = float(scene.get("duration_seconds", 5))
                silence = np.zeros(int(dur * sr), dtype=np.float32)
                _save_wav(silence, sr, out_path)
                results.append(out_path)

        return results

    # -----------------------------------------------------------------------
    # 6. Script builder
    # -----------------------------------------------------------------------

    def build_script_text(self, content: dict) -> str:
        """
        Build a natural broadcast-style script from a content dict.

        Keys used:
          hook, bullets, takeaway, category

        Design principles:
          - Open with the hook (no "welcome back")
          - Natural transitions — no "Number 1, Number 2"
          - Power words: "shocking", "discover", "secret", "revealed"
          - Pauses ("...") between major sections for breath and pacing
          - Emphasis markers for future SSML-aware TTS
        """
        hook     = str(content.get("hook", "")).strip()
        bullets  = list(content.get("bullets", []))
        takeaway = str(content.get("takeaway", "")).strip()
        category = str(content.get("category", "this topic")).strip()

        # Transition phrases — more varied than "Number N"
        transitions = [
            "Here's what most people never discover:",
            "But that's just the beginning...",
            "What's even more shocking is this:",
            "And here's the part that will surprise you:",
            "This next one is rarely talked about:",
            "Pay close attention to this:",
            "Very few people know about this:",
            "And this is where it gets truly fascinating:",
        ]

        lines: List[str] = []

        # Open strong
        if hook:
            lines.append(hook)
            lines.append("...")

        # Body bullets
        for i, bullet in enumerate(bullets):
            bullet = str(bullet).strip()
            if not bullet:
                continue
            transition = transitions[i % len(transitions)]
            lines.append(transition)
            lines.append(bullet + ".")
            if i < len(bullets) - 1:
                lines.append("...")

        # Key takeaway
        if takeaway:
            lines.append("...")
            lines.append(
                f"The secret to understanding {category.lower()} comes down to this:"
            )
            lines.append(takeaway)

        # Close — no "hit subscribe" filler, sharp CTA
        lines.append(
            "... Drop a comment below if this changed how you see it."
        )

        return "  ".join(lines)

    def generate_voiceover_sync(self, text: str, path: str, tone: str = "authoritative") -> str:
        """Synchronous wrapper — safe to call from non-async code."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    future = pool.submit(asyncio.run, self.generate_voiceover(text, path, tone=tone))
                    return future.result()
            else:
                return loop.run_until_complete(self.generate_voiceover(text, path, tone=tone))
        except RuntimeError:
            return asyncio.run(self.generate_voiceover(text, path, tone=tone))


# ===========================================================================
# Synchronous convenience wrapper
# ===========================================================================

def generate_voiceover_sync(
    text: str, path: str, tone: str = "authoritative"
) -> str:
    """
    Synchronous wrapper around CinematicAudioEngine.generate_voiceover.
    Safe to call from non-async code.
    """
    engine = CinematicAudioEngine()
    return asyncio.run(engine.generate_voiceover(text, path, tone=tone))


# ===========================================================================
# Quick self-test
# ===========================================================================

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s  %(levelname)-7s  %(name)s  %(message)s",
    )

    engine = CinematicAudioEngine(tmp_dir="/tmp/cinematic_test")

    # --- Voiceover test -------------------------------------------------------
    test_text = (
        "The world's energy grid is changing faster than most people realize. "
        "What you discover in the next thirty seconds will completely reshape "
        "how you think about electricity, demand, and the secret economics "
        "that keep the lights on every single night."
    )
    voice_out = "/tmp/cinematic_test/test_voice.wav"
    print("Generating voiceover...")
    path = asyncio.run(
        engine.generate_voiceover(test_text, voice_out, tone="authoritative")
    )
    print(f"Voice: {path}  size={os.path.getsize(path)//1024} KB")

    # --- Music test -----------------------------------------------------------
    music_out = "/tmp/cinematic_test/test_music.wav"
    print("Generating background music...")
    engine.generate_background_music(30.0, music_out, energy="medium")
    print(f"Music: {music_out}  size={os.path.getsize(music_out)//1024} KB")

    # --- Mix test -------------------------------------------------------------
    mix_out = "/tmp/cinematic_test/test_mix.wav"
    print("Mixing...")
    engine.mix_audio(voice_out, music_out, mix_out, total_s=30.0)
    print(f"Mix:   {mix_out}  size={os.path.getsize(mix_out)//1024} KB")

    # --- Script builder test --------------------------------------------------
    content = {
        "hook": "The shocking truth about solar power that nobody in the industry wants you to know.",
        "bullets": [
            "Solar panels degrade roughly half a percent every year — meaning a 25-year-old panel is only 87% as efficient",
            "The largest battery storage facility in the world can power all of California for just four minutes",
            "Wind and solar together now produce cheaper electricity than any coal plant on Earth",
        ],
        "takeaway": "Renewables are not the future — they are already the cheapest energy source on the planet right now.",
        "category": "renewable energy",
    }
    script = engine.build_script_text(content)
    print("\n--- Generated Script ---")
    print(script)
    print("-----------------------")
    sys.exit(0)
