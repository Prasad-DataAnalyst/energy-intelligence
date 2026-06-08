"""
Phase 3 — Background Music
============================
Priority:
  1. Pixabay API (royalty-free, search by mood)
  2. Procedurally generated lo-fi beat (numpy, always works)
"""
import logging
import math
import os
import re
import subprocess
import wave
import numpy as np

from utils.api_client import get_session, download_file

log = logging.getLogger("viral_pipeline.audio.music")

PIXABAY_BASE = "https://pixabay.com/api/videos/music/"

# Mood mapping for each style preset
MOOD_MAP = {
    "tech-dark":       ["inspiring", "electronic", "future"],
    "vlog-warm":       ["happy", "acoustic", "upbeat"],
    "news-clean":      ["corporate", "background", "motivational"],
    "motivation-epic": ["epic", "cinematic", "powerful"],
}


def fetch_pixabay_music(style: str, duration_hint: float,
                        output_path: str) -> bool:
    api_key = os.getenv("PIXABAY_API_KEY", "")
    if not api_key:
        return False

    moods = MOOD_MAP.get(style, ["background"])
    sess = get_session()

    for mood in moods:
        try:
            params = {
                "key": api_key,
                "q": mood,
                "music_genre": "",
                "order": "popular",
                "per_page": 5,
            }
            r = sess.get(PIXABAY_BASE, params=params, timeout=15)
            if not r.ok:
                continue
            hits = r.json().get("hits", [])
            if not hits:
                continue
            # Pick track closest to target duration
            best = min(hits,
                       key=lambda h: abs(h.get("duration", 0) - duration_hint))
            url = best.get("audio", {}).get("url") or best.get("url", "")
            if url:
                download_file(url, output_path)
                # Convert to WAV 44.1kHz
                wav_path = output_path.replace(".mp3", ".wav")
                cmd = ["ffmpeg", "-y", "-i", output_path,
                       "-ar", "44100", "-ac", "1", wav_path]
                r2 = subprocess.run(cmd, capture_output=True)
                if r2.returncode == 0:
                    os.replace(wav_path, output_path.replace(".mp3", ".wav"))
                    log.info(f"  Music: Pixabay '{mood}' track downloaded")
                    return True
        except Exception as e:
            log.warning(f"Pixabay music ({mood}): {e}")

    return False


def generate_procedural_music(duration_s: float, style: str,
                               out_wav: str) -> str:
    """
    Procedurally generated background music that fits each style:
    - tech-dark:       driving electronic with synth bass
    - vlog-warm:       gentle acoustic-style pad
    - news-clean:      minimal tense pad
    - motivation-epic: epic cinematic swell
    """
    sr  = 44100
    n   = int(sr * duration_s)
    t   = np.linspace(0, duration_s, n, endpoint=False)
    audio = np.zeros(n)

    if style in ("tech-dark", "motivation-epic"):
        # Driving pulse: minor pentatonic (A2 C3 D3 E3 G3)
        notes = [110.0, 130.81, 146.83, 164.81, 196.00]
        bpm   = 120 if style == "motivation-epic" else 100
        beat  = 60 / bpm

        # Synth pad
        for freq in notes:
            lfo = 1 + 0.006 * np.sin(2 * np.pi * 0.3 * t)
            audio += (0.04 / len(notes)) * np.sin(2 * np.pi * freq * lfo * t)

        # Sub bass pulse on every beat
        for k in range(int(duration_s / beat) + 2):
            s = int(k * beat * sr)
            L = min(int(0.12 * sr), n - s)
            if L <= 0:
                continue
            env = np.exp(-np.arange(L) / (0.025 * sr))
            swp = np.linspace(80, 35, L)
            audio[s:s+L] += 0.09 * env * np.sin(
                2 * np.pi * np.cumsum(swp) / sr)

        # Hi-hat on 8th notes
        for k in range(int(duration_s / (beat/2)) + 2):
            s = int(k * beat / 2 * sr)
            L = min(int(0.02 * sr), n - s)
            if L <= 0:
                continue
            env   = np.exp(-np.arange(L) / (0.003 * sr))
            noise = np.random.default_rng(k + 99).standard_normal(L)
            audio[s:s+L] += 0.018 * env * noise

        # Epic: add rising string swell
        if style == "motivation-epic":
            swell_freq = 220.0 * (1 + 0.5 * t / duration_s)
            audio += 0.03 * np.sin(2 * np.pi * np.cumsum(swell_freq) / sr)

    elif style == "vlog-warm":
        # Gentle C-major arpeggio
        notes = [261.63, 329.63, 392.00, 523.25]  # C4 E4 G4 C5
        arp_speed = 4  # Hz
        for i, freq in enumerate(notes):
            phase_off = i / len(notes)
            gate = (np.sin(2 * np.pi * arp_speed * t - phase_off * 2 * np.pi) > 0.2)
            audio += 0.03 * gate * np.sin(2 * np.pi * freq * t)

        # Soft pad
        for freq in [130.81, 196.00, 261.63]:
            audio += 0.015 * np.sin(2 * np.pi * freq * t)

    else:  # news-clean
        # Minimal tense pad: D minor (D3 F3 A3)
        for freq in [146.83, 174.61, 220.00]:
            lfo = 1 + 0.002 * np.sin(2 * np.pi * 0.2 * t)
            audio += 0.025 * np.sin(2 * np.pi * freq * lfo * t)

    # Fade in/out
    fade = min(int(0.8 * sr), n // 4)
    audio[:fade]  *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)
    audio = np.clip(audio, -1, 1)

    audio_i16 = (audio * 32767).astype(np.int16)
    with wave.open(out_wav, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_i16.tobytes())

    log.info(f"  Music: procedural '{style}' — {os.path.getsize(out_wav)//1024} KB")
    return out_wav


def get_background_music(total_s: float, style: str,
                         output_dir: str) -> str:
    """Return path to a background music WAV for the full video."""
    os.makedirs(output_dir, exist_ok=True)
    mp3_path = os.path.join(output_dir, "music.mp3")
    wav_path = os.path.join(output_dir, "music.wav")

    if fetch_pixabay_music(style, total_s, mp3_path):
        final = mp3_path.replace(".mp3", ".wav")
        if os.path.exists(final):
            return final

    return generate_procedural_music(total_s + 3, style, wav_path)
