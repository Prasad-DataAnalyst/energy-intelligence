"""
generators/audio_gen.py — DriftWire326
TTS audio generation with three-tier engine priority:
  1. edge-tts  (free, no API key, high quality)
  2. ElevenLabs (premium, requires ELEVENLABS_API_KEY)
  3. pyttsx3   (offline fallback)
Produces per-segment audio files and a merged final track.
"""
import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

OUTPUT_DIR = settings.output_dir / "audio"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# edge-tts voice — energetic US male, credible for finance
EDGE_TTS_VOICE = "en-US-GuyNeural"

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_VOICE_SETTINGS = {
    "stability": 0.60,
    "similarity_boost": 0.85,
    "style": 0.35,
    "use_speaker_boost": True,
}

STAGE_DIRECTION_PATTERN = re.compile(
    r"\[(PAUSE|B-ROLL[^]]*|GRAPHIC[^]]*|ZOOM[^]]*|MUSIC[^]]*|SFX[^]]*)\]",
    re.IGNORECASE,
)


@dataclass
class AudioSegment:
    segment_name: str
    text: str
    audio_path: Optional[Path]
    duration_seconds: Optional[float]
    engine: str   # "elevenlabs" | "pyttsx3" | "gTTS"
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class AudioTrack:
    video_type: str
    segments: list[AudioSegment]
    merged_path: Optional[Path]
    total_duration_seconds: float
    engine: str
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _strip_stage_directions(text: str) -> str:
    """Remove [B-ROLL], [PAUSE], etc. — TTS shouldn't read them."""
    return STAGE_DIRECTION_PATTERN.sub("", text).strip()


def _clean_for_tts(text: str) -> str:
    """Normalise text for natural TTS reading."""
    text = _strip_stage_directions(text)
    # Expand common finance abbreviations
    replacements = {
        "S&P": "S and P",
        "Q1": "Q 1", "Q2": "Q 2", "Q3": "Q 3", "Q4": "Q 4",
        "YoY": "year over year", "QoQ": "quarter over quarter",
        "MoM": "month over month", "bps": "basis points",
        "EPS": "earnings per share", "P/E": "price to earnings",
        "%": " percent", "$": " dollars ",
        "^VIX": "the VIX", "^TNX": "the 10-year Treasury yield",
    }
    for abbr, full in replacements.items():
        text = text.replace(abbr, full)
    # Remove markdown/formatting noise
    text = re.sub(r"\*{1,2}(.+?)\*{1,2}", r"\1", text)
    text = re.sub(r"#{1,4}\s", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _apply_ssml_markup(text: str) -> str:
    """
    Wrap plain text in SSML for more natural edge-tts delivery:
    - <break time="0.3s"/> after key numbers and statistics
    - <emphasis level="strong"> around percentage figures
    - rate="slow" prosody on sentences containing multiple statistics
    """
    # Idempotence guard — never double-wrap text that already contains SSML
    if re.search(r"<(speak|emphasis|break|prosody)\b", text, re.IGNORECASE):
        return text

    # Escape XML special characters so the SSML document stays well-formed
    text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

    # Wrap percentage figures with emphasis
    text = re.sub(
        r"(\d+\.?\d*\s*percent)",
        r'<emphasis level="strong">\1</emphasis>',
        text,
        flags=re.IGNORECASE,
    )
    # Add pause after standalone numbers (e.g. "$3.2 billion", "145 points").
    # NOTE: replacement must use single quotes — r"\"" in a raw string emits a
    # literal backslash, which previously produced malformed SSML.
    text = re.sub(
        r"(\$\s*[\d,]+\.?\d*\s*(?:billion|million|trillion|thousand)?)",
        r'\1<break time="0.3s"/>',
        text,
        flags=re.IGNORECASE,
    )

    # Add pause after pure numbers followed by a space (standalone stats).
    # Skip: 4-digit years ("in 2026 the market..."), numbers followed by
    # "percent" (already emphasis-wrapped) or a magnitude word (mid-phrase),
    # and digits inside already-inserted tags.
    def _break_after_number(m: re.Match) -> str:
        number, following = m.group(1), m.group(2)
        if re.fullmatch(r"(19|20)\d{2}", number):
            return m.group(0)   # years read naturally without a pause
        return f'{number}<break time="0.3s"/>{following}'

    text = re.sub(
        r"\b(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\b(\s)"
        r"(?!(?:percent|billion|million|trillion|thousand)\b)"
        r"(?![^<]*</emphasis>)",
        _break_after_number,
        text,
        flags=re.IGNORECASE,
    )
    # Wrap stat-heavy sentences (≥3 numbers) in slower prosody
    sentences = re.split(r"(?<=[.!?])\s+", text)
    processed: list[str] = []
    for sentence in sentences:
        num_count = len(re.findall(r"\d+", sentence))
        if num_count >= 3:
            sentence = f'<prosody rate="slow">{sentence}</prosody>'
        processed.append(sentence)
    body = " ".join(processed)
    return f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="en-US">{body}</speak>'


async def _edge_tts_async(text: str, voice: str, output_path: Path, use_ssml: bool = True) -> None:
    import edge_tts
    if use_ssml:
        ssml = _apply_ssml_markup(text)
        communicate = edge_tts.Communicate(ssml, voice)
    else:
        communicate = edge_tts.Communicate(text, voice)
    await communicate.save(str(output_path))


def _edge_tts(text: str, output_path: Path) -> Optional[float]:
    """Primary TTS engine — edge-tts with SSML markup for natural delivery."""
    try:
        asyncio.run(_edge_tts_async(text, EDGE_TTS_VOICE, output_path, use_ssml=True))
        word_count = len(text.split())
        duration = (word_count / 155) * 60
        logger.debug("edge-tts → %s (%.1fs)", output_path.name, duration)
        return duration
    except Exception as exc:
        logger.warning("edge-tts SSML failed, retrying plain text: %s", exc)
        # Retry without SSML (in case of malformed markup)
        try:
            asyncio.run(_edge_tts_async(text, EDGE_TTS_VOICE, output_path, use_ssml=False))
            word_count = len(text.split())
            return (word_count / 155) * 60
        except Exception as exc2:
            logger.warning("edge-tts plain text also failed: %s", exc2)
            return None


def _elevenlabs_tts(text: str, output_path: Path) -> Optional[float]:
    """Call ElevenLabs API and write MP3. Returns duration estimate."""
    if not settings.elevenlabs_api_key:
        return None

    url = ELEVENLABS_TTS_URL.format(voice_id=settings.elevenlabs_voice_id)
    headers = {
        "xi-api-key": settings.elevenlabs_api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "model_id": "eleven_turbo_v2_5",   # fast + high quality
        "voice_settings": ELEVENLABS_VOICE_SETTINGS,
    }

    for attempt in range(settings.max_retries):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=60, stream=True)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)
            # Rough duration estimate: 150 wpm average
            word_count = len(text.split())
            duration = (word_count / 150) * 60
            logger.debug("ElevenLabs TTS → %s (%.1fs)", output_path.name, duration)
            return duration
        except requests.RequestException as exc:
            wait = settings.retry_backoff_base ** attempt
            logger.warning("ElevenLabs attempt %d failed: %s — retry in %.1fs", attempt + 1, exc, wait)
            if attempt < settings.max_retries - 1:
                time.sleep(wait)

    logger.error("ElevenLabs TTS failed after %d attempts", settings.max_retries)
    return None


def _pyttsx3_tts(text: str, output_path: Path) -> Optional[float]:
    """Offline TTS fallback using pyttsx3."""
    try:
        import pyttsx3
        engine = pyttsx3.init()
        engine.setProperty("rate", 175)
        engine.setProperty("volume", 1.0)
        # Try to use a good voice
        voices = engine.getProperty("voices")
        for voice in voices:
            if "en_US" in voice.id or "english" in voice.name.lower():
                engine.setProperty("voice", voice.id)
                break
        engine.save_to_file(text, str(output_path))
        engine.runAndWait()
        word_count = len(text.split())
        return (word_count / 175) * 60
    except Exception as exc:
        logger.error("pyttsx3 TTS failed: %s", exc)
        return None


def _merge_audio_files(segment_paths: list[Path], output_path: Path) -> Optional[float]:
    """Merge segment MP3 files using pydub or ffmpeg subprocess."""
    valid = [p for p in segment_paths if p and p.exists()]
    if not valid:
        logger.warning("No valid audio segments to merge — skipping merge")
        return None

    try:
        from pydub import AudioSegment as PydubSegment
        combined = PydubSegment.empty()
        for p in valid:
            seg = PydubSegment.from_file(str(p))
            combined += seg
            combined += PydubSegment.silent(duration=400)  # 400ms pause between segments
        combined.export(str(output_path), format="mp3", bitrate=settings.audio_bitrate)
        return len(combined) / 1000.0
    except ImportError:
        # Fallback: ffmpeg concat
        import subprocess
        list_file = output_path.parent / "concat_list.txt"
        list_file.write_text("\n".join(f"file '{p.resolve()}'" for p in valid))
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
                 "-i", str(list_file), "-c", "copy", str(output_path)],
                capture_output=True, timeout=120,
            )
            if result.returncode == 0:
                return sum((p.stat().st_size / 32000) for p in valid)
        except Exception as exc:
            logger.error("ffmpeg merge failed: %s", exc)
        finally:
            list_file.unlink(missing_ok=True)
        return None
    except Exception as exc:
        logger.error("Audio merge failed: %s", exc)
        return None


# ── Weekday voice rotation ────────────────────────────────────────────────────
_WEEKDAY_VOICES = {
    0: "en-US-GuyNeural",       # Monday — professional male
    1: "en-US-ChristopherNeural",  # Tuesday
    2: "en-US-EricNeural",      # Wednesday
    3: "en-US-GuyNeural",       # Thursday
    4: "en-US-RogerNeural",     # Friday — energetic close-of-week
    5: "en-US-GuyNeural",       # Saturday fallback
    6: "en-US-GuyNeural",       # Sunday
}

MIN_AUDIO_SECONDS = 120
MAX_AUDIO_SECONDS = 180


class AudioGenerator:
    """Class-based TTS generation with validation and fallback chain."""

    def get_todays_voice(self) -> str:
        """Return an edge-tts voice name keyed to today's weekday."""
        from datetime import date
        return _WEEKDAY_VOICES.get(date.today().weekday(), EDGE_TTS_VOICE)

    def generate_main_audio(
        self,
        script_segments: dict[str, str],
        video_type: str,
        voice: Optional[str] = None,
    ) -> Optional[AudioTrack]:
        """
        Generate and merge audio for all script segments.
        Validates total duration is 120–180s for weekday/sunday.
        Shorts (no voiceover) return None immediately.
        Falls back to gTTS if edge-tts fails.
        """
        if video_type == "shorts":
            logger.info("Shorts require no voiceover — skipping audio generation")
            return None

        chosen_voice = voice or self.get_todays_voice()
        logger.info("Generating main audio | type=%s | voice=%s", video_type, chosen_voice)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        audio_segs: list[AudioSegment] = []
        total_est = 0.0

        for seg_name, text in script_segments.items():
            clean = _clean_for_tts(text)
            if not clean:
                continue

            fname = f"{video_type}_{seg_name.lower().replace(' ', '_')}_{timestamp}.mp3"
            seg_path = OUTPUT_DIR / fname

            # edge-tts first
            dur = _edge_tts(clean, seg_path)
            engine_used = "edge_tts"

            # gTTS fallback
            if dur is None:
                seg_path = self.generate_fallback_audio(clean, seg_path)
                if seg_path:
                    dur = self.get_audio_duration(seg_path)
                    engine_used = "gTTS"

            audio_segs.append(AudioSegment(
                segment_name=seg_name,
                text=clean,
                audio_path=seg_path if (seg_path and seg_path.exists()) else None,
                duration_seconds=dur,
                engine=engine_used,
            ))
            if dur:
                total_est += dur

        valid_paths = [s.audio_path for s in audio_segs if s.audio_path]
        merged_fname = f"{video_type}_final_audio_{timestamp}.mp3"
        merged_path = OUTPUT_DIR / merged_fname
        merge_dur = _merge_audio_files(valid_paths, merged_path)
        total_dur = merge_dur or total_est

        if not valid_paths:
            logger.warning(
                "No audio segments generated for %s (%d script segments) — "
                "returning empty track", video_type, len(script_segments),
            )
        # Validate duration (only meaningful when real audio was produced)
        elif not (MIN_AUDIO_SECONDS <= total_dur <= MAX_AUDIO_SECONDS):
            logger.warning(
                "Audio duration %.1fs outside target [%d, %d]s — padding/trimming",
                total_dur, MIN_AUDIO_SECONDS, MAX_AUDIO_SECONDS,
            )
            if total_dur < MIN_AUDIO_SECONDS and merged_path.exists():
                merged_path = self.add_silence_padding(merged_path, MIN_AUDIO_SECONDS - total_dur)
                total_dur = MIN_AUDIO_SECONDS
            # Over-length videos are flagged but not trimmed automatically (manual review)

        track = AudioTrack(
            video_type=video_type,
            segments=audio_segs,
            merged_path=merged_path if merged_path.exists() else None,
            total_duration_seconds=total_dur,
            engine="edge_tts",
        )
        logger.info("Audio complete — %.1fs | %d segments", total_dur, len(audio_segs))
        return track

    def generate_fallback_audio(self, text: str, output_path: Path) -> Optional[Path]:
        """gTTS offline fallback — generates an MP3 at output_path."""
        try:
            from gtts import gTTS
            tts = gTTS(text=text, lang="en", slow=False)
            tts.save(str(output_path))
            logger.info("gTTS fallback audio saved → %s", output_path)
            return output_path
        except Exception as exc:
            logger.error("gTTS fallback failed: %s", exc)
            return None

    def get_audio_duration(self, audio_path: Path) -> float:
        """Return duration in seconds using mutagen or pydub or ffprobe."""
        try:
            from mutagen.mp3 import MP3
            return MP3(str(audio_path)).info.length
        except Exception as exc:
            logger.debug("mutagen duration probe failed for %s: %s", audio_path.name, exc)

        try:
            from pydub import AudioSegment as Pydub
            return len(Pydub.from_file(str(audio_path))) / 1000.0
        except Exception as exc:
            logger.debug("pydub duration probe failed for %s: %s", audio_path.name, exc)

        try:
            import subprocess
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return float(result.stdout.strip())
        except Exception as exc:
            logger.debug("ffprobe duration probe failed for %s: %s", audio_path.name, exc)

        # fallback: word-count estimate
        word_count = len(audio_path.stem.split())
        return (word_count / 150) * 60

    def add_silence_padding(self, audio_path: Path, seconds: float) -> Path:
        """Append silence to audio_path to meet minimum duration. Returns path."""
        try:
            from pydub import AudioSegment as Pydub
            audio = Pydub.from_file(str(audio_path))
            silence = Pydub.silent(duration=int(seconds * 1000))
            padded = audio + silence
            padded.export(str(audio_path), format="mp3")
            logger.info("Added %.1fs silence padding to %s", seconds, audio_path.name)
            return audio_path
        except Exception:
            # ffmpeg fallback
            import subprocess
            padded = audio_path.parent / f"padded_{audio_path.name}"
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(audio_path),
                     "-af", f"apad=pad_dur={seconds:.1f}", str(padded)],
                    capture_output=True, timeout=30,
                )
                if padded.exists():
                    audio_path.unlink(missing_ok=True)
                    padded.rename(audio_path)
            except Exception as exc:
                logger.error("Silence padding failed: %s", exc)
            return audio_path


def generate_audio(script_segments: dict[str, str], video_type: str) -> AudioTrack:
    """
    Generate TTS audio for each script segment and merge into a final track.
    Tries ElevenLabs first, falls back to pyttsx3.
    """
    logger.info("Generating audio for %s (%d segments)", video_type, len(script_segments))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    engine_name = "edge_tts"   # optimistic — updated per-segment if fallback used

    audio_segments: list[AudioSegment] = []
    total_duration = 0.0

    for seg_name, text in script_segments.items():
        clean_text = _clean_for_tts(text)
        if not clean_text:
            continue

        seg_filename = f"{video_type}_{seg_name.lower().replace(' ', '_')}_{timestamp}.mp3"
        seg_path = OUTPUT_DIR / seg_filename
        duration = None
        used_engine = "edge_tts"

        # Priority 1: edge-tts (free, no key needed)
        duration = _edge_tts(clean_text, seg_path)

        # Priority 2: ElevenLabs (premium quality)
        if duration is None and settings.elevenlabs_api_key:
            duration = _elevenlabs_tts(clean_text, seg_path)
            if duration:
                used_engine = "elevenlabs"

        # Priority 3: pyttsx3 (offline last resort)
        if duration is None:
            duration = _pyttsx3_tts(clean_text, seg_path)
            if duration:
                used_engine = "pyttsx3"

        if duration is None:
            logger.error("All TTS engines failed for segment '%s'", seg_name)
            seg_path = None
            used_engine = "none"

        audio_segments.append(AudioSegment(
            segment_name=seg_name,
            text=clean_text,
            audio_path=seg_path,
            duration_seconds=duration,
            engine=used_engine,
        ))
        if used_engine != "edge_tts" and used_engine != "none":
            engine_name = used_engine   # track dominant fallback for the track
        if duration:
            total_duration += duration

    # Merge all segments
    valid_paths = [s.audio_path for s in audio_segments if s.audio_path]
    merged_filename = f"{video_type}_final_audio_{timestamp}.mp3"
    merged_path = OUTPUT_DIR / merged_filename
    merge_duration = _merge_audio_files(valid_paths, merged_path)
    if merge_duration:
        total_duration = merge_duration

    track = AudioTrack(
        video_type=video_type,
        segments=audio_segments,
        merged_path=merged_path if merged_path.exists() else None,
        total_duration_seconds=total_duration,
        engine=engine_name,
    )

    logger.info("Audio generation complete — %.1fs total, engine: %s", total_duration, engine_name)
    return track


# ── Loudness Normalization ────────────────────────────────────────────────────

_TARGET_LUFS = -16.0   # YouTube recommended loudness target
_TRUE_PEAK_DB = -1.5   # YouTube true-peak ceiling


def normalize_loudness(audio_path: Path, output_path: Optional[Path] = None) -> Optional[Path]:
    """
    Normalize audio to -16 LUFS / -1.5 dBTP using FFmpeg loudnorm filter.
    If output_path is None, overwrites the input file in-place (via temp file).
    Returns the output path on success, None on failure.

    Requires: ffmpeg installed on system PATH.
    """
    import subprocess
    import shutil

    if output_path is None:
        out = audio_path.with_suffix(".norm.mp3")
    else:
        out = output_path

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", str(audio_path),
        "-af", f"loudnorm=I={_TARGET_LUFS}:TP={_TRUE_PEAK_DB}:LRA=11",
        "-codec:a", "libmp3lame",
        "-q:a", "2",
        str(out),
    ]

    try:
        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.error("FFmpeg loudnorm failed: %s", result.stderr[-500:])
            return None

        if output_path is None:
            # Replace original with normalized version
            import os
            os.replace(out, audio_path)
            logger.info("Loudness normalized in-place: %s (→ %.1f LUFS)", audio_path.name, _TARGET_LUFS)
            return audio_path
        else:
            logger.info("Loudness normalized: %s → %s", audio_path.name, out.name)
            return out

    except FileNotFoundError:
        logger.warning("ffmpeg not found — loudness normalization skipped")
        return None
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg loudnorm timed out for %s", audio_path.name)
        return None
    except Exception as exc:
        logger.error("Loudness normalization error: %s", exc)
        return None
