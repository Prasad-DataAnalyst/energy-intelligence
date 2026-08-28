"""
services/openai_tts_service.py
Layer: Service Wrapper
Safety: upgradeable
Description: OpenAI TTS API wrapper (tts-1-hd).
"""
from __future__ import annotations
import logging
import os
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)

VOICE_MAP = {
    "authoritative": "onyx",
    "warm":          "nova",
    "urgent":        "echo",
    "calm":          "shimmer",
    "energetic":     "fable",
    "shocked":       "onyx",
    "intimate":      "nova",
    "conspiratorial": "echo",
}


def is_available() -> bool:
    key = os.getenv("OPENAI_API_KEY", "")
    return bool(key and key != "your_key_here")


def generate(text: str, out_path: str, tone: str = "authoritative") -> str:
    if not is_available():
        raise RuntimeError("OPENAI_API_KEY not configured")
    try:
        from openai import OpenAI
    except ImportError:
        raise RuntimeError("openai package not installed — run: pip install openai")

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    voice = VOICE_MAP.get(tone, "onyx")

    for attempt in range(3):
        try:
            response = client.audio.speech.create(
                model="tts-1-hd",
                voice=voice,
                input=text,
            )
            mp3_path = out_path.replace(".wav", "_oai.mp3")
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            response.stream_to_file(mp3_path)
            r = subprocess.run(
                ["ffmpeg", "-y", "-i", mp3_path, out_path],
                capture_output=True, text=True
            )
            try:
                os.remove(mp3_path)
            except OSError:
                pass
            if r.returncode != 0:
                raise RuntimeError(f"ffmpeg: {r.stderr[:200]}")
            log.info("OpenAI TTS success: %s", out_path)
            return out_path
        except Exception as e:
            wait = 2 ** attempt
            log.warning("OpenAI TTS attempt %d failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(wait)
    raise RuntimeError("OpenAI TTS failed after 3 attempts")
