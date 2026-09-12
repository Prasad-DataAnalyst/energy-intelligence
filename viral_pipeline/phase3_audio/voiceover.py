"""
Phase 3 — Voiceover Synthesiser
=================================
Tries providers in priority order:
  1. ElevenLabs API  (premium, neural, natural-sounding)
  2. edge-tts        (Microsoft Neural TTS, free — may be blocked from cloud)
  3. espeak-ng       (offline, always available after apt install)

Each scene gets its own WAV file at 44.1 kHz mono.
"""
import asyncio
import logging
import os
import re
import ssl
import subprocess
import tempfile
from typing import List, Optional

from utils.models import Scene, AudioAsset
from utils.api_client import get_session

log = logging.getLogger("viral_pipeline.audio.voiceover")


# ── ElevenLabs ────────────────────────────────────────────────────────────────

ELEVENLABS_BASE   = "https://api.elevenlabs.io/v1"
DEFAULT_XI_VOICE  = "21m00Tcm4TlvDq8ikWAM"   # Rachel — clear US female


def _xi_voice_id(cfg: dict) -> str:
    return (os.getenv("ELEVENLABS_VOICE_ID")
            or cfg.get("audio", {}).get("elevenlabs_voice_id", DEFAULT_XI_VOICE))


def synth_elevenlabs(text: str, out_wav: str, cfg: dict) -> bool:
    api_key = os.getenv("ELEVENLABS_API_KEY", "")
    if not api_key:
        return False
    voice_id = _xi_voice_id(cfg)
    url = f"{ELEVENLABS_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": "eleven_turbo_v2",
        "voice_settings": {
            "stability": 0.45,
            "similarity_boost": 0.80,
            "style": 0.35,
            "use_speaker_boost": True,
        },
    }
    try:
        sess = get_session()
        r = sess.post(url, json=body, headers=headers, timeout=60)
        if not r.ok:
            log.warning(f"ElevenLabs HTTP {r.status_code}: {r.text[:200]}")
            return False
        mp3_tmp = out_wav.replace(".wav", "_xi.mp3")
        with open(mp3_tmp, "wb") as f:
            f.write(r.content)
        # Convert MP3 → WAV at 44.1 kHz
        cmd = ["ffmpeg", "-y", "-i", mp3_tmp,
               "-ar", "44100", "-ac", "1", out_wav]
        res = subprocess.run(cmd, capture_output=True)
        os.remove(mp3_tmp)
        return res.returncode == 0
    except Exception as e:
        log.warning(f"ElevenLabs error: {e}")
        return False


# ── edge-tts ──────────────────────────────────────────────────────────────────

async def _edge_tts_async(text: str, out_mp3: str, voice: str) -> bool:
    try:
        import edge_tts
        import edge_tts.communicate as _ec
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        _ec._SSL_CTX = ctx
        comm = edge_tts.Communicate(text, voice, rate="+10%", volume="+15%")
        await comm.save(out_mp3)
        return True
    except Exception as e:
        log.warning(f"edge-tts error: {e}")
        return False


def synth_edge_tts(text: str, out_wav: str, cfg: dict) -> bool:
    voice = cfg.get("audio", {}).get("edge_tts_voice", "en-US-AriaNeural")
    mp3_tmp = out_wav.replace(".wav", "_edge.mp3")
    ok = asyncio.run(_edge_tts_async(text, mp3_tmp, voice))
    if not ok:
        return False
    cmd = ["ffmpeg", "-y", "-i", mp3_tmp, "-ar", "44100", "-ac", "1", out_wav]
    res = subprocess.run(cmd, capture_output=True)
    try:
        os.remove(mp3_tmp)
    except OSError:
        pass
    return res.returncode == 0


# ── espeak-ng (offline fallback) ──────────────────────────────────────────────

def synth_espeak(text: str, out_wav: str, cfg: dict) -> bool:
    audio_cfg = cfg.get("audio", {})
    voice   = audio_cfg.get("espeak_voice",  "en-us+f3")
    speed   = str(audio_cfg.get("espeak_speed", 168))
    pitch   = str(audio_cfg.get("espeak_pitch", 58))
    amp     = "180"
    sr      = str(audio_cfg.get("sample_rate", 44100))

    raw_wav = out_wav.replace(".wav", "_raw.wav")
    cmd = ["espeak-ng", "-v", voice, "-s", speed,
           "-p", pitch, "-a", amp, "-w", raw_wav, text]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"espeak-ng error: {r.stderr[:200]}")
        return False

    # Post-process: resample + EQ warmup + loudnorm
    af = (
        f"aresample={sr},"
        "equalizer=f=200:width_type=o:width=2:g=4,"
        "equalizer=f=4000:width_type=o:width=2:g=-3,"
        "equalizer=f=8000:width_type=o:width=2:g=-5,"
        "loudnorm=I=-16:TP=-1.5:LRA=11"
    )
    cmd2 = ["ffmpeg", "-y", "-i", raw_wav, "-af", af, out_wav]
    r2 = subprocess.run(cmd2, capture_output=True)
    try:
        os.remove(raw_wav)
    except OSError:
        pass
    return r2.returncode == 0


# ── Smart dispatcher ──────────────────────────────────────────────────────────

PROVIDER_CHAIN = [
    ("elevenlabs", synth_elevenlabs),
    ("edge_tts",   synth_edge_tts),
    ("espeak",     synth_espeak),
]


def synth_scene(text: str, out_wav: str, cfg: dict,
                force_provider: Optional[str] = None) -> str:
    """Synthesise one scene's voiceover. Returns provider name used."""
    preferred = force_provider or cfg.get("audio", {}).get("voice_provider", "elevenlabs")

    chain = PROVIDER_CHAIN.copy()
    # Move preferred to front
    chain.sort(key=lambda x: (0 if x[0] == preferred else 1))

    for name, fn in chain:
        log.debug(f"  Trying voice provider: {name}")
        ok = fn(text, out_wav, cfg)
        if ok and os.path.exists(out_wav) and os.path.getsize(out_wav) > 1000:
            log.info(f"  Voice [{name}]: {os.path.getsize(out_wav)//1024} KB")
            return name
        else:
            log.warning(f"  Provider '{name}' failed or produced empty file")

    # Last resort: generate silence
    log.error("All TTS providers failed — generating silence")
    _generate_silence(out_wav, duration_s=_estimate_duration(text), cfg=cfg)
    return "silence"


def _estimate_duration(text: str) -> float:
    words = len(text.split())
    return max(2.0, words / 2.8)   # ~2.8 words/sec speaking rate


def _generate_silence(out_wav: str, duration_s: float, cfg: dict) -> None:
    sr = cfg.get("audio", {}).get("sample_rate", 44100)
    cmd = ["ffmpeg", "-y", "-f", "lavfi",
           f"-i", f"anullsrc=r={sr}:cl=mono",
           "-t", str(duration_s), out_wav]
    subprocess.run(cmd, capture_output=True)


def get_audio_duration(wav_path: str) -> float:
    """Use ffprobe to get exact duration of a WAV file."""
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", wav_path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def run_voiceover(scenes: List[Scene], output_dir: str,
                  cfg: dict) -> List[AudioAsset]:
    """Generate voiceover WAV for every scene."""
    from tqdm import tqdm
    assets: List[AudioAsset] = []

    with tqdm(scenes, desc="  Voiceover", unit="scene") as bar:
        for scene in bar:
            out_wav = os.path.join(output_dir, f"voice_scene_{scene.id:02d}.wav")
            provider = synth_scene(scene.voiceover, out_wav, cfg)
            dur = get_audio_duration(out_wav) if os.path.exists(out_wav) else scene.duration
            assets.append(AudioAsset(
                scene_id=scene.id,
                voice_file=out_wav,
                duration=dur,
                provider=provider,
            ))

    return assets
