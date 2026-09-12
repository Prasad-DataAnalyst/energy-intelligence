"""
Phase 5 — Caption Generator
=============================
Uses OpenAI Whisper (if installed) to get precise word-level timestamps,
otherwise builds an SRT file from scene voiceover timings.
Captions are burned into the video via FFmpeg subtitles filter.
"""
import logging
import os
import re
import subprocess
import tempfile
from typing import List, Tuple, Optional

from utils.models import Script, AudioAsset

log = logging.getLogger("viral_pipeline.captions")


SubEntry = Tuple[float, float, str]   # (start_s, end_s, text)


# ── Whisper-based alignment (optional) ───────────────────────────────────────

def whisper_align(audio_path: str) -> List[SubEntry]:
    """Run Whisper to get word-level timestamps. Returns empty list if unavailable."""
    try:
        import whisper
        model = whisper.load_model("tiny")
        result = model.transcribe(audio_path, word_timestamps=True)
        entries: List[SubEntry] = []
        for seg in result.get("segments", []):
            for word in seg.get("words", []):
                entries.append((
                    word["start"],
                    word["end"],
                    word["word"].strip(),
                ))
        log.info(f"  Whisper: {len(entries)} word timestamps")
        return entries
    except ImportError:
        log.debug("Whisper not installed — using scene-based captions")
        return []
    except Exception as e:
        log.warning(f"Whisper failed: {e}")
        return []


# ── Scene-timing fallback ──────────────────────────────────────────────────────

def scene_captions(script: Script, audio_assets: List[AudioAsset]) -> List[SubEntry]:
    """Build captions from scene voiceover text with estimated word timings."""
    aa_map = {a.scene_id: a for a in audio_assets}
    entries: List[SubEntry] = []
    cursor = 0.0

    for scene in script.scenes:
        asset   = aa_map.get(scene.id)
        dur     = asset.duration if asset else scene.duration
        text    = scene.voiceover.strip()
        words   = text.split()
        if not words:
            cursor += dur
            continue

        # Chunk into ~6-word subtitle lines
        chunk_size = 6
        chunks = [words[i:i+chunk_size] for i in range(0, len(words), chunk_size)]
        time_per_chunk = dur / max(len(chunks), 1)

        for chunk in chunks:
            start = cursor
            end   = cursor + time_per_chunk
            entries.append((start, end, " ".join(chunk)))
            cursor = end

    return entries


def _to_srt_time(s: float) -> str:
    h  = int(s // 3600)
    m  = int((s % 3600) // 60)
    sc = s % 60
    ms = int((sc % 1) * 1000)
    return f"{h:02d}:{m:02d}:{int(sc):02d},{ms:03d}"


def write_srt(entries: List[SubEntry], srt_path: str) -> str:
    with open(srt_path, "w") as f:
        for i, (start, end, text) in enumerate(entries, 1):
            f.write(f"{i}\n")
            f.write(f"{_to_srt_time(start)} --> {_to_srt_time(end)}\n")
            f.write(f"{text}\n\n")
    return srt_path


def burn_captions(video_path: str, srt_path: str,
                  output_path: str, style: str = "tech-dark",
                  width: int = 1280) -> str:
    """Burn subtitles into video using FFmpeg subtitles filter."""
    fs = max(18, width // 45)
    # Style: white text, black outline, bottom of screen
    sub_style = (
        f"FontName=DejaVu Sans Bold,FontSize={fs},"
        "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
        "BackColour=&H80000000,Bold=1,"
        "Outline=2,Shadow=1,Alignment=2,"
        "MarginV=40"
    )
    # Escape path for FFmpeg filter
    safe_srt = srt_path.replace("\\", "/").replace(":", "\\:")
    vf = f"subtitles='{safe_srt}':force_style='{sub_style}'"

    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        output_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log.warning(f"Caption burn failed: {r.stderr[:300]} — using video without captions")
        return video_path

    log.info(f"  Captions burned → {output_path}")
    return output_path


def add_captions_to_video(video_path: str, script: Script,
                          audio_assets: List[AudioAsset],
                          output_dir: str, cfg: dict) -> str:
    """Full caption workflow — returns final video path."""
    mixed_audio = os.path.join(output_dir, "audio_final.wav")
    srt_path    = os.path.join(output_dir, "captions.srt")
    out_path    = os.path.join(output_dir, "final_captioned.mp4")

    log.info("Phase 5: Generating captions…")

    # Try Whisper on the mixed audio track
    entries: List[SubEntry] = []
    if os.path.exists(mixed_audio):
        entries = whisper_align(mixed_audio)

    # Fall back to scene timings
    if not entries:
        entries = scene_captions(script, audio_assets)

    write_srt(entries, srt_path)
    log.info(f"  SRT: {len(entries)} entries → {srt_path}")

    vid_cfg = cfg.get("video", {})
    w       = int(vid_cfg.get("resolution", "1280x720").split("x")[0])
    style   = vid_cfg.get("style_preset", "tech-dark")

    return burn_captions(video_path, srt_path, out_path, style, w)
