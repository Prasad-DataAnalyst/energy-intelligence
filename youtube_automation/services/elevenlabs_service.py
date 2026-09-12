"""
services/elevenlabs_service.py
Layer: Service Wrapper
Safety: upgradeable
Description: ElevenLabs TTS API wrapper with retry and fallback.
Input:  text (str), voice_id (str), tone (str)
Output: path to generated WAV file
"""
from __future__ import annotations
import logging
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

VOICE_MAP = {
    "authoritative": "pNInz6obpgDQGcFmaJgB",  # Adam
    "warm":          "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "urgent":        "TxGEqnHWrfWFTfGW9XjX",  # Josh
    "calm":          "EXAVITQu4vr4xnSDxMaL",  # Bella
    "energetic":     "ErXwobaYiN019PkySvjV",  # Antoni
    "shocked":       "pNInz6obpgDQGcFmaJgB",  # Adam
    "intimate":      "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "conspiratorial": "TxGEqnHWrfWFTfGW9XjX", # Josh
}

MODEL_ID = "eleven_turbo_v2_5"
VOICE_SETTINGS = {
    "stability": 0.40,
    "similarity_boost": 0.85,
    "style": 0.65,
    "use_speaker_boost": True,
}


def is_available() -> bool:
    key = os.getenv("ELEVENLABS_API_KEY", "")
    return bool(key and key != "your_key_here")


def generate(text: str, out_path: str, tone: str = "authoritative") -> str:
    """Generate speech via ElevenLabs. Returns out_path on success, raises on failure."""
    if not is_available():
        raise RuntimeError("ELEVENLABS_API_KEY not configured")

    try:
        from elevenlabs import ElevenLabs, VoiceSettings
    except ImportError:
        raise RuntimeError("elevenlabs package not installed — run: pip install elevenlabs")

    api_key = os.getenv("ELEVENLABS_API_KEY")
    voice_id = os.getenv("ELEVENLABS_VOICE_ID") or VOICE_MAP.get(tone, VOICE_MAP["authoritative"])

    client = ElevenLabs(api_key=api_key)
    settings = VoiceSettings(**VOICE_SETTINGS)

    for attempt in range(3):
        try:
            audio_bytes = b"".join(
                client.text_to_speech.convert(
                    voice_id=voice_id,
                    text=text,
                    model_id=MODEL_ID,
                    voice_settings=settings,
                )
            )
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            # ElevenLabs returns MP3 — save then convert to WAV via ffmpeg
            mp3_path = out_path.replace(".wav", "_el.mp3")
            with open(mp3_path, "wb") as f:
                f.write(audio_bytes)
            import subprocess
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, out_path],
                capture_output=True, text=True
            )
            try:
                os.remove(mp3_path)
            except OSError:
                pass
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg conversion failed: {r.stderr[:200]}")
            log.info("ElevenLabs TTS success: %s", out_path)
            return out_path
        except Exception as e:
            wait = 2 ** attempt
            log.warning("ElevenLabs attempt %d failed: %s — retrying in %ds", attempt + 1, e, wait)
            if attempt < 2:
                time.sleep(wait)
    raise RuntimeError("ElevenLabs failed after 3 attempts")
