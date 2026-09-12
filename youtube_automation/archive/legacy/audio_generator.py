"""
audio_generator.py
===================
Generates two audio tracks for each daily video:

1. AI voiceover  — espeak-ng (en-us+f3 female variant, post-processed with
                   ffmpeg equaliser to sound warmer and more natural)
2. Background music — procedurally generated lo-fi ambient beat (royalty-free)

Then mixes them: voice at full volume, music at 15% (subtle backdrop).
Output: a single WAV file ready to be muxed into the MP4 by ffmpeg.
"""

import os
import subprocess
import wave
import numpy as np

from pydub import AudioSegment

# espeak-ng settings — en-us female variant 3 (closest to a young US female)
ESPEAK_VOICE  = "en-us+f3"
ESPEAK_SPEED  = 168          # words-per-minute (default 175; slightly slower = clearer)
ESPEAK_PITCH  = 58           # 0-99; default 50; higher = more feminine
ESPEAK_AMP    = 180          # 0-200; output amplitude

MUSIC_VOL     = 0.15         # background music at 15% relative to voice


# ── Script builder ───────────────────────────────────────────────────────────

def build_script(content: dict) -> str:
    """Turn daily content dict into a natural spoken narration."""
    hook     = content["hook"]
    bullets  = content["bullets"]
    takeaway = content["takeaway"]
    category = content["category"]

    lines = [
        hook,
        f"Here are 5 things about {category.lower()} that will change how you see it.",
    ]
    for i, b in enumerate(bullets, 1):
        lines.append(f"Number {i}. {b}.")
    lines += [
        f"And here is your key takeaway. {takeaway}",
        "Hit subscribe and tap the bell for your daily mind fuel. See you tomorrow.",
    ]
    return "  ".join(lines)


# ── TTS voiceover ────────────────────────────────────────────────────────────

def generate_voiceover(script: str, out_path: str):
    """
    Synthesise speech with espeak-ng, then apply ffmpeg EQ to warm up the
    tinny espeak sound (boost 200 Hz, cut 4 kHz harshness, normalise).
    Output is written to out_path (wav or mp3 determined by extension).
    """
    raw_wav = out_path.rsplit(".", 1)[0] + "_raw.wav"

    # 1. espeak-ng → raw WAV
    cmd = [
        "espeak-ng",
        "-v", ESPEAK_VOICE,
        "-s", str(ESPEAK_SPEED),
        "-p", str(ESPEAK_PITCH),
        "-a", str(ESPEAK_AMP),
        "-w", raw_wav,
        script,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"espeak-ng error: {r.stderr[:300]}")

    # 2. ffmpeg post-processing: resample to 44.1 kHz, warm EQ, loudnorm
    cmd2 = [
        "ffmpeg", "-y",
        "-i", raw_wav,
        "-af", (
            "aresample=44100,"
            "equalizer=f=200:width_type=o:width=2:g=4,"   # boost warmth
            "equalizer=f=4000:width_type=o:width=2:g=-3," # cut harshness
            "equalizer=f=8000:width_type=o:width=2:g=-5," # reduce tinnyness
            "loudnorm=I=-16:TP=-1.5:LRA=11"               # broadcast loudness
        ),
        out_path,
    ]
    r2 = subprocess.run(cmd2, capture_output=True, text=True)
    if r2.returncode != 0:
        # Fallback: just copy raw wav without EQ
        import shutil
        shutil.copy(raw_wav, out_path)

    try:
        os.remove(raw_wav)
    except OSError:
        pass

    print(f"  Voice  : {out_path}  ({os.path.getsize(out_path)//1024} KB)")


# ── Background music ─────────────────────────────────────────────────────────

def generate_background_music(duration_s: float, out_wav: str):
    """Procedurally generated lo-fi ambient beat — fully royalty-free."""
    sr  = 44100
    n   = int(sr * duration_s)
    t   = np.linspace(0, duration_s, n, endpoint=False)

    # Ambient pad — C-major pentatonic (C3 E3 G3 C4 E4)
    notes = [130.81, 164.81, 196.00, 261.63, 329.63]
    pad   = np.zeros(n)
    for freq in notes:
        vib  = 1 + 0.004 * np.sin(2 * np.pi * 4.8 * t)
        pad += (0.055 / len(notes)) * np.sin(2 * np.pi * freq * vib * t)

    # Soft kick drum at 85 BPM
    beat_s = 60 / 85
    kick   = np.zeros(n)
    for k in range(int(duration_s / beat_s) + 2):
        s = int(k * beat_s * sr)
        L = min(int(0.09 * sr), n - s)
        if L <= 0:
            continue
        env  = np.exp(-np.arange(L) / (0.018 * sr))
        swp  = np.linspace(110, 45, L)
        kick[s:s+L] += 0.07 * env * np.sin(2 * np.pi * np.cumsum(swp) / sr)

    # Hi-hat on every 8th note
    hihat = np.zeros(n)
    for k in range(int(duration_s / (beat_s / 2)) + 2):
        s = int(k * beat_s / 2 * sr)
        L = min(int(0.022 * sr), n - s)
        if L <= 0:
            continue
        env   = np.exp(-np.arange(L) / (0.004 * sr))
        noise = np.random.default_rng(k).standard_normal(L)
        hihat[s:s+L] += 0.025 * env * noise

    audio = pad + kick + hihat
    # Fade in/out 0.6 s
    fade = int(0.6 * sr)
    audio[:fade]  *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)
    audio = np.clip(audio, -1, 1)

    audio_i16 = (audio * 32767).astype(np.int16)
    with wave.open(out_wav, "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(audio_i16.tobytes())
    print(f"  Music  : {out_wav}  ({os.path.getsize(out_wav)//1024} KB)")


# ── Mixer ────────────────────────────────────────────────────────────────────

def mix_audio(voice_path: str, music_wav: str, out_wav: str, total_s: float):
    """Overlay voice on top of music, match total video duration."""
    ext = os.path.splitext(voice_path)[1].lstrip(".")
    voice = (AudioSegment.from_wav(voice_path) if ext == "wav"
             else AudioSegment.from_file(voice_path, format=ext))
    music = AudioSegment.from_wav(music_wav)

    target_ms = int(total_s * 1000)

    # Loop or trim music to match video length
    if len(music) < target_ms:
        repeats = target_ms // len(music) + 1
        music   = music * repeats
    music = music[:target_ms]

    # Lower music volume
    music = music - int(20 * (1 - MUSIC_VOL))   # ~17 dB reduction

    # Pad or trim voice
    if len(voice) < target_ms:
        silence = AudioSegment.silent(duration=target_ms - len(voice))
        voice   = voice + silence
    voice = voice[:target_ms]

    mixed = music.overlay(voice)
    mixed.export(out_wav, format="wav")
    print(f"  Mixed  : {out_wav}  ({os.path.getsize(out_wav)//1024} KB)")
    return out_wav
