"""
builders/caption_renderer.py — DriftWire326 modern video layer
Burned-in captions synced to real speech.

Most viewers meet a finance video muted in a feed, so on-screen words are
the difference between a scroll and a watch. These captions are driven by
edge-tts WordBoundary offsets (see generators/audio_gen.py), not by a
words-per-minute estimate, so they land on the spoken word instead of
drifting apart over four minutes.

Output is an ASS subtitle file rather than drawtext filters: one `subtitles`
filter renders hundreds of cues at negligible CPU cost, while hundreds of
chained drawtext filters would bloat the command line and slow the encode.
"""
import logging
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Short phrases read faster than full sentences on a small screen.
MAX_WORDS_PER_CUE = 4
MAX_CUE_SECONDS = 2.5
MIN_CUE_SECONDS = 0.5
# A gap longer than this ends the phrase — it marks a natural pause.
PHRASE_BREAK_GAP = 0.35

# Units that must stay attached to the number preceding them.
_UNIT_WORDS = {"percent", "points", "point", "billion", "million",
               "trillion", "dollars", "basis"}


def group_words_into_cues(words: list) -> list:
    """
    Group word timings into short caption cues.

    Breaks on: sentence-ending punctuation, a natural pause in the speech,
    the word cap, or the duration cap — whichever comes first.
    """
    cues: list = []
    current: list = []

    def _flush() -> None:
        if not current:
            return
        text = " ".join(w["word"] for w in current).strip()
        if text:
            start = current[0]["start"]
            end = max(current[-1]["end"], start + MIN_CUE_SECONDS)
            cues.append({"text": text, "start": start, "end": end})
        current.clear()

    cleaned = [w for w in words if w.get("word")]
    for index, word in enumerate(cleaned):
        if current:
            gap = word["start"] - current[-1]["end"]
            span = word["end"] - current[0]["start"]
            over_cap = len(current) >= MAX_WORDS_PER_CUE
            # Never strand a unit from its number: breaking "0.66 | percent"
            # across cues reads badly, so allow one extra word for the unit.
            next_word = cleaned[index + 1]["word"].lower().strip(".,") if index + 1 < len(cleaned) else ""
            holds_unit = over_cap and next_word in _UNIT_WORDS and len(current) < MAX_WORDS_PER_CUE + 1
            if (gap > PHRASE_BREAK_GAP
                    or (over_cap and not holds_unit)
                    or span > MAX_CUE_SECONDS):
                _flush()
        current.append(word)
        if word["word"].rstrip().endswith((".", "!", "?")):
            _flush()

    _flush()
    return cues


def _ass_timestamp(seconds: float) -> str:
    """ASS uses H:MM:SS.cc (centiseconds)."""
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def build_ass_captions(
    words: list,
    output_path: Path,
    width: Optional[int] = None,
    height: Optional[int] = None,
) -> Optional[Path]:
    """
    Write an ASS subtitle file: bold, centred in the lower third, heavy
    outline so it stays readable over charts and photography.
    Returns the path, or None if there is nothing to render.
    """
    cues = group_words_into_cues(words)
    if not cues:
        logger.info("No word timings — skipping burned-in captions")
        return None

    width = width or settings.video_width
    height = height or settings.video_height
    # Scale type with the canvas so 720p and 1080p look the same.
    font_size = max(int(height * 0.055), 22)
    margin_v = int(height * 0.10)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: DW,DejaVu Sans,{font_size},&H00FFFFFF,&H00101010,&H80000000,-1,1,4,2,2,60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    lines = [
        f"Dialogue: 0,{_ass_timestamp(c['start'])},{_ass_timestamp(c['end'])},"
        f"DW,,0,0,0,,{_ass_escape(c['text'])}"
        for c in cues
    ]

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        logger.info("Captions: %d cues → %s", len(cues), output_path.name)
        return output_path
    except Exception as exc:
        logger.warning("Caption file write failed (non-fatal): %s", exc)
        return None


def captions_for_audio(audio_path: Path, output_dir: Path) -> Optional[Path]:
    """Convenience: load the timings saved beside an audio file and render."""
    try:
        from generators.audio_gen import load_word_timings
        words = load_word_timings(audio_path)
        if not words:
            return None
        return build_ass_captions(words, output_dir / "captions.ass")
    except Exception as exc:
        logger.warning("Caption generation failed (non-fatal): %s", exc)
        return None
