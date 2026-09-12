"""
production/voice_engine.py — Multi-Voice Audio Producer
=========================================================
Provider chain (best → always available):
  1. ElevenLabs API  (premium, natural)
  2. OpenAI TTS API  (gpt-4o-mini-tts)
  3. edge-tts        (Microsoft, free)
  4. espeak-ng       (offline, always available)

Each provider falls through to the next on failure.
Output: WAV file at output/<topic_slug>/voice.wav, post-processed with
FFmpeg EQ for warmth and loudnorm.
"""
import asyncio
import logging
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.voice_engine")

HERE = Path(__file__).parent.parent

# espeak-ng defaults (offline fallback)
ESPEAK_VOICE = "en-us+f3"
ESPEAK_SPEED = 168
ESPEAK_PITCH = 58
ESPEAK_AMP   = 180

# FFmpeg post-processing chain
FFmpeg_EQ = (
    "aresample=44100,"
    "equalizer=f=200:width_type=o:width=2:g=4,"
    "equalizer=f=4000:width_type=o:width=2:g=-3,"
    "equalizer=f=8000:width_type=o:width=2:g=-5,"
    "loudnorm=I=-16:TP=-1.5:LRA=11"
)


class VoiceEngine:
    def __init__(self):
        self._providers = [
            self._elevenlabs,
            self._openai_tts,
            self._edge_tts,
            self._espeak,
        ]

    # ── Script → narration text ────────────────────────────────────────────

    @staticmethod
    def _extract_narration(script: Dict) -> str:
        """Concatenate scene voiceovers into full narration text."""
        scenes = script.get("scenes", []) if isinstance(script, dict) else []
        parts  = []
        for s in scenes:
            vo = s.get("voiceover", "").strip()
            if vo:
                parts.append(vo)
        return "  ".join(parts)

    # ── Provider: ElevenLabs ───────────────────────────────────────────────

    def _elevenlabs(self, text: str, out_path: str) -> bool:
        api_key = os.getenv("ELEVENLABS_API_KEY", "")
        if not api_key:
            return False
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")
            r = sess.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={"xi-api-key": api_key,
                         "Content-Type": "application/json"},
                json={"text": text[:4500],
                      "model_id": "eleven_turbo_v2",
                      "voice_settings": {"stability": 0.5, "similarity_boost": 0.8}},
                timeout=60,
            )
            if not r.ok:
                log.debug(f"ElevenLabs failed: {r.status_code}")
                return False
            raw_mp3 = out_path.replace(".wav", "_el.mp3")
            with open(raw_mp3, "wb") as f:
                f.write(r.content)
            return self._postprocess(raw_mp3, out_path)
        except Exception as e:
            log.debug(f"ElevenLabs error: {e}")
            return False

    # ── Provider: OpenAI TTS ───────────────────────────────────────────────

    def _openai_tts(self, text: str, out_path: str) -> bool:
        api_key = os.getenv("OPENAI_API_KEY", "")
        if not api_key:
            return False
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            r = sess.post(
                "https://api.openai.com/v1/audio/speech",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json={"model": "tts-1-hd", "input": text[:4096],
                      "voice": "nova", "response_format": "mp3"},
                timeout=60,
            )
            if not r.ok:
                log.debug(f"OpenAI TTS failed: {r.status_code}")
                return False
            raw_mp3 = out_path.replace(".wav", "_oai.mp3")
            with open(raw_mp3, "wb") as f:
                f.write(r.content)
            return self._postprocess(raw_mp3, out_path)
        except Exception as e:
            log.debug(f"OpenAI TTS error: {e}")
            return False

    # ── Provider: edge-tts ────────────────────────────────────────────────

    def _edge_tts(self, text: str, out_path: str) -> bool:
        try:
            import edge_tts
            import ssl as _ssl
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE

            async def _run():
                comm = edge_tts.Communicate(text, "en-US-AriaNeural")
                tmp  = out_path.replace(".wav", "_et.mp3")
                await comm.save(tmp)
                return tmp

            tmp_mp3 = asyncio.run(_run())
            return self._postprocess(tmp_mp3, out_path)
        except Exception as e:
            log.debug(f"edge-tts error: {e}")
            return False

    # ── Provider: espeak-ng (always available) ────────────────────────────

    def _espeak(self, text: str, out_path: str) -> bool:
        try:
            raw_wav = out_path.replace(".wav", "_raw.wav")
            # Write text to temp file (avoids shell escaping issues)
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt",
                                             delete=False) as tf:
                tf.write(text)
                txt_path = tf.name

            result = subprocess.run(
                ["espeak-ng", "-v", ESPEAK_VOICE,
                 "-s", str(ESPEAK_SPEED),
                 "-p", str(ESPEAK_PITCH),
                 "-a", str(ESPEAK_AMP),
                 "-w", raw_wav, "-f", txt_path],
                capture_output=True, timeout=120,
            )
            Path(txt_path).unlink(missing_ok=True)
            if result.returncode != 0:
                log.debug(f"espeak-ng failed: {result.stderr}")
                return False
            return self._postprocess(raw_wav, out_path)
        except FileNotFoundError:
            log.debug("espeak-ng not found")
            return False
        except Exception as e:
            log.debug(f"espeak-ng error: {e}")
            return False

    # ── Post-processing ────────────────────────────────────────────────────

    @staticmethod
    def _postprocess(in_path: str, out_path: str) -> bool:
        """FFmpeg EQ + loudnorm → final WAV."""
        cmd = [
            "ffmpeg", "-y", "-i", in_path,
            "-af", FFmpeg_EQ,
            "-ar", "44100", "-ac", "1",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        if result.returncode == 0:
            Path(in_path).unlink(missing_ok=True)
            return True
        log.debug(f"FFmpeg postprocess failed: {result.stderr[-200:]}")
        return False

    # ── Music ──────────────────────────────────────────────────────────────

    @staticmethod
    def _generate_music(duration_s: float, style: str, out_path: str) -> bool:
        """Generate procedural background music via ffmpeg sine tones."""
        # Simple layered tones — replace with Suno/Udio API when available
        freq = {"tech-dark": "110", "vlog-warm": "220",
                "news-clean": "165", "motivation-epic": "130"}.get(style, "150")
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", (f"sine=frequency={freq}:duration={duration_s},"
                   "volume=0.08"),
            "-af", "loudnorm=I=-26:TP=-3:LRA=8",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        return result.returncode == 0

    @staticmethod
    def _mix(voice_path: str, music_path: str,
             out_path: str, duration_s: float) -> bool:
        """Mix voice + music with voice at -6dB, music at -18dB."""
        cmd = [
            "ffmpeg", "-y",
            "-i", voice_path,
            "-i", music_path,
            "-filter_complex",
            ("[0]volume=1.0[v];"
             "[1]volume=0.25[m];"
             "[v][m]amix=inputs=2:duration=first:normalize=0[out]"),
            "-map", "[out]",
            "-t", str(duration_s),
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=60)
        return result.returncode == 0

    # ── Main API ───────────────────────────────────────────────────────────

    def produce(self, script: Dict,
                style: str = "tech-dark",
                output_dir: Optional[str] = None) -> Optional[str]:
        """
        Generate voice audio + background music for the given script.
        Returns path to final mixed WAV, or None on complete failure.
        """
        narration = self._extract_narration(script)
        if not narration:
            log.warning("VoiceEngine: empty narration")
            return None

        duration_s = float(script.get("total_duration", 600))
        slug       = re.sub(r"[^a-z0-9]+", "_",
                            script.get("title", "video")[:40].lower())

        if output_dir:
            out_dir = Path(output_dir)
        else:
            out_dir = HERE / "output" / slug
        out_dir.mkdir(parents=True, exist_ok=True)

        voice_wav = str(out_dir / "voice.wav")
        music_wav = str(out_dir / "music.wav")
        mixed_wav = str(out_dir / "mixed_audio.wav")

        # Try each voice provider
        voice_ok = False
        for provider in self._providers:
            try:
                if provider(narration, voice_wav):
                    voice_ok = True
                    log.info(f"Voice produced via {provider.__name__}")
                    break
            except Exception as e:
                log.debug(f"Provider {provider.__name__} failed: {e}")

        if not voice_ok:
            log.error("All voice providers failed")
            return None

        # Generate music
        music_ok = self._generate_music(duration_s, style, music_wav)
        if music_ok:
            mix_ok = self._mix(voice_wav, music_wav, mixed_wav, duration_s)
            if mix_ok:
                log.info(f"Mixed audio → {mixed_wav}")
                return mixed_wav
            log.warning("Mix failed — returning voice-only")

        return voice_wav
