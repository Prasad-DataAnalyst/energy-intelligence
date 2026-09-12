"""
Phase 3 — Audio Mixer
======================
Concatenates scene voiceovers, overlays background music, and exports the
final mixed WAV at broadcast loudness (-16 LUFS, -1.5 dBTP).

All mixing is done via FFmpeg for maximum reliability.
"""
import logging
import os
import subprocess
import tempfile
from typing import List

from utils.models import AudioAsset

log = logging.getLogger("viral_pipeline.audio.mixer")


def concat_voiceovers(assets: List[AudioAsset],
                      output_dir: str) -> tuple[str, float]:
    """Concatenate all per-scene WAVs into one continuous voice track."""
    if not assets:
        raise ValueError("No audio assets to concatenate")

    # Sort by scene ID
    assets = sorted(assets, key=lambda a: a.scene_id)

    list_file = os.path.join(output_dir, "_voice_list.txt")
    with open(list_file, "w") as f:
        for a in assets:
            if os.path.exists(a.voice_file):
                f.write(f"file '{os.path.abspath(a.voice_file)}'\n")

    out_path = os.path.join(output_dir, "voice_concat.wav")
    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", list_file,
        "-c", "copy",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Voice concat failed: {r.stderr[:300]}")

    total_dur = sum(a.duration for a in assets)
    log.info(f"  Voice concat: {out_path} ({total_dur:.1f}s)")
    return out_path, total_dur


def mix_voice_and_music(voice_wav: str, music_wav: str,
                        total_s: float, output_dir: str,
                        voice_db: float = -6.0,
                        music_db: float = -18.0) -> str:
    """
    Mix voice + music using FFmpeg amix / volume filters.
    Voice is at voice_db relative to full scale.
    Music is ducked to music_db and looped/trimmed to total_s.
    """
    out_path = os.path.join(output_dir, "audio_final.wav")

    # FFmpeg filter graph:
    # [0] voice: normalise + volume
    # [1] music: loop/trim + volume + fade in/out
    # amix both together
    af = (
        f"[0:a]volume={voice_db}dB,loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
        f"[1:a]aloop=loop=-1:size=2e+09,atrim=0={total_s},"
        f"volume={music_db}dB,afade=t=in:d=1,afade=t=out:d=2:st={max(0, total_s-2)}[music];"
        f"[voice][music]amix=inputs=2:duration=first:dropout_transition=0[out]"
    )

    cmd = [
        "ffmpeg", "-y",
        "-i", voice_wav,
        "-i", music_wav,
        "-filter_complex", af,
        "-map", "[out]",
        "-ar", "44100",
        "-ac", "1",
        out_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.error(f"Mix failed: {r.stderr[:400]}")
        # Fallback: just use voice without music
        import shutil
        shutil.copy(voice_wav, out_path)

    size = os.path.getsize(out_path) // 1024
    log.info(f"  Mixed audio: {out_path} ({size} KB)")
    return out_path


def run_phase3_audio(assets: List[AudioAsset],
                     music_wav: str,
                     total_s: float,
                     output_dir: str,
                     cfg: dict) -> tuple[str, str]:
    """
    Full Phase 3 audio runner.
    Returns (voice_concat_wav, mixed_final_wav).
    """
    audio_cfg   = cfg.get("audio", {})
    voice_db    = float(audio_cfg.get("voice_db", -6))
    music_db    = float(audio_cfg.get("music_db", -18))

    log.info("Phase 3: Mixing audio tracks…")
    voice_path, _ = concat_voiceovers(assets, output_dir)
    mixed_path    = mix_voice_and_music(
        voice_path, music_wav, total_s, output_dir,
        voice_db=voice_db, music_db=music_db,
    )
    return voice_path, mixed_path
