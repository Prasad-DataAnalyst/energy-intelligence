"""
builders/beat_planner.py — turn a handful of static slides into a beat track.

A finance slideshow that holds one image for twenty seconds reads as a
lecture. Modern short-and-mid-form video changes *something* on screen
every three to four seconds, which is what keeps a viewer from swiping.

Rendering sixty distinct backgrounds is not affordable on a free-tier VM,
so this module does the cheap version of the same thing: it keeps the
existing narrative slides as backgrounds and layers timed graphics on top
— a section kicker when the script moves to a new segment, and a stat card
when the narration says a number worth seeing.

The overlays are baked into the still frames with PIL before ffmpeg ever
runs. That matters: the alternative — one `overlay`/`drawtext` filter per
beat, gated with enable='between(t,a,b)' — grows the filtergraph past what
a shared-core instance renders in time, while compositing sixty PNGs costs
a few seconds of CPU and nothing at encode time.
"""
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

# Target seconds between visual changes. Three to four seconds is the
# rhythm most finance channels settle on: fast enough to hold attention,
# slow enough to actually read a number.
BEAT_SECONDS = 3.5
MIN_BEAT_SECONDS = 2.0

# Never put a stat card on back-to-back beats. Numbers land harder with
# space around them, and a card on every beat turns into wallpaper. Keeping
# this above BEAT_SECONDS is also what guarantees two cards can never want
# the same frame, so the planner never has to arbitrate between them.
MIN_STAT_GAP_SECONDS = 4.5

# Palette shared with slide_renderer so beats look native to the slides.
SURFACE = (20, 21, 30)
TEXT    = (240, 242, 245)
MUTED   = (140, 148, 165)
GREEN   = (0, 196, 107)
RED     = (255, 75, 75)
ACCENT  = (120, 190, 255)

_PUNCT = " \t\n.,;:!?()[]\"'—-"

# Words that turn a bare number into a magnitude, and how to abbreviate.
_SCALE_WORDS = {"billion": "B", "million": "M", "trillion": "T"}
_UNIT_FOLLOW = set(_SCALE_WORDS) | {
    "percent", "points", "point", "basis", "bps", "dollars",
}

_UP_WORDS = {
    "up", "gain", "gained", "gains", "rose", "rising", "jumped", "jumps",
    "surged", "surge", "surges", "climbed", "rallied", "higher", "added",
    "advanced", "soared", "popped", "rebounded",
}
_DOWN_WORDS = {
    "down", "fell", "falls", "fall", "dropped", "drop", "slid", "slipped",
    "sank", "declined", "lower", "lost", "losses", "tumbled", "plunged",
    "retreated", "slumped",
}

# Filler that must never end up as a stat card's label.
_LABEL_STOP = _UP_WORDS | _DOWN_WORDS | {
    "the", "a", "an", "and", "but", "was", "were", "is", "are", "at", "to",
    "of", "on", "in", "by", "for", "with", "that", "this", "it", "its",
    "closed", "close", "ended", "end", "finished", "today", "yesterday",
    "session", "while", "as", "after", "before", "than", "then", "about",
    "roughly", "nearly", "just", "over", "under", "another", "also",
    "held", "holds", "came", "come", "stood", "stands", "sits", "sat",
    "hit", "hits", "reached", "settled", "traded", "trades", "printed",
    "expected", "below", "above", "versus", "against", "near", "around",
} | _UNIT_FOLLOW   # "basis points" names the unit, never the instrument


@dataclass
class Beat:
    """One still frame in the finished sequence."""
    image: Path
    duration: float
    start: float = 0.0
    chapter: Optional[str] = None
    stat_value: Optional[str] = None
    stat_label: Optional[str] = None
    stat_color: tuple = ACCENT

    @property
    def has_overlay(self) -> bool:
        return bool(self.chapter or self.stat_value)


# ── Narration analysis ────────────────────────────────────────────────────────

def _tokenize(segments: dict) -> tuple[list, list]:
    """
    Flatten the script into whitespace tokens, plus the token index at which
    each segment starts. Dicts keep insertion order and the parser builds
    them in document order, so this is narration order.
    """
    tokens: list[str] = []
    starts: list[tuple[int, str]] = []
    for name, text in (segments or {}).items():
        starts.append((len(tokens), str(name).strip()))
        tokens.extend(str(text).split())
    return tokens, starts


def _token_time(index: int, n_tokens: int, words: list) -> Optional[float]:
    """
    Map a script token index onto the narration clock.

    The word timings and the script tokens describe the same speech, but
    they do not tokenize identically — SSML markup is stripped and the
    engine expands "0.66%" into several spoken words. Matching them
    one-to-one is brittle, so scale proportionally instead: it puts a
    highlight inside the right sentence, which is all the placement needs.
    """
    if not words or n_tokens <= 0:
        return None
    position = min(int(index * len(words) / n_tokens), len(words) - 1)
    try:
        return float(words[position].get("start", 0.0))
    except (AttributeError, TypeError, ValueError):
        return None


def _clean(token: str) -> str:
    return token.strip(_PUNCT)


_GROUPED_NUMBER = re.compile(r"\d{1,3},\d{3}")


def _is_value(token: str, next_token: str) -> bool:
    """
    Is this token a number worth putting on screen?

    A bare number is not enough — scripts are full of years, quarters and
    "8:30 Eastern", none of which deserve a card. It qualifies on a unit
    ("%", "$", "basis points", "billion") or on comma grouping, which in
    practice only ever marks a real figure: 221,000 claims, 5,930 on the
    index. Times and years never carry a comma.
    """
    core = _clean(token)
    if not any(ch.isdigit() for ch in core):
        return False
    if core.endswith("%") or core.startswith("$"):
        return True
    if _GROUPED_NUMBER.search(core):
        return True
    return _clean(next_token).lower() in _UNIT_FOLLOW


def _direction(tokens: list, index: int) -> int:
    """
    +1 / -1 / 0 from the words immediately before a number.

    Scanned nearest-first, which is the whole point: "Tesla dropped 3.4%.
    Apple added 0.8%" has both a fall and a rise within four tokens, and
    reading forwards paints Apple's gain red.
    """
    for back in range(index - 1, max(-1, index - 5), -1):
        word = _clean(tokens[back]).lower()
        if word in _UP_WORDS:
            return 1
        if word in _DOWN_WORDS:
            return -1
    return 0


def _format_value(tokens: list, index: int, direction: int) -> str:
    """Render the on-screen number, folding in the unit word that follows."""
    core = _clean(tokens[index])
    following = _clean(tokens[index + 1]).lower() if index + 1 < len(tokens) else ""

    if following in _SCALE_WORDS:
        core = f"{core}{_SCALE_WORDS[following]}"
    elif following == "percent" and not core.endswith("%"):
        core = f"{core}%"
    elif following in {"points", "point"}:
        core = f"{core} pts"
    elif following in {"basis", "bps"}:
        core = f"{core} bps"

    # Sign only where it means something: a move, not a level. "%" and "bps"
    # are always moves; bare index points are usually a level ("climbed to
    # 5,930 points"), and "+5,930 pts" reads as nonsense.
    if direction and not core.startswith(("+", "-")) and (
        core.endswith("%") or core.endswith("bps")
    ):
        core = ("+" if direction > 0 else "-") + core
    return core


def _label_for(tokens: list, index: int) -> str:
    """
    Name the thing the number belongs to, read backwards from it.

    Walks back for a proper-noun phrase — "S&P 500", "NVIDIA", "Jobless
    claims" — which is what an analyst would put on the card. Three rules
    earn their keep: an earlier number ends the phrase (otherwise "Tesla
    dropped 3.4%. Apple added 0.8%" labels Apple's move "3.4% APPLE"),
    filler is skipped while the phrase has not started but ends it once it
    has, and a lowercase word is held pending in case a proper noun sits
    behind it ("yields" alone is meaningless, "Treasury yields" is not).
    """
    run: list[str] = []
    pending: list[str] = []
    for back in range(index - 1, max(-1, index - 7), -1):
        raw = tokens[back]
        word = _clean(raw)
        if not word:
            break
        # A sentence boundary is a hard stop: without it "closed at 5,930 on
        # Friday. Jobless claims came in at 221,000" labels the claims figure
        # "FRIDAY JOBLESS CLAIMS".
        if raw.rstrip("\"')]").endswith((".", "!", "?")):
            break
        if "%" in word or "$" in word:
            break
        if word.lower() in _LABEL_STOP:
            if run:
                break
            pending = []
            continue
        if word[0].isdigit():
            # A digit belongs to a name only as its tail — "S&P 500", "Russell
            # 2000" — which means a proper noun sits directly behind it and the
            # phrase has not started yet. Anywhere else it is a different
            # number, and a boundary: without this, "closed at 5,930. Treasury
            # yields fell 6 basis points" labels the yield move "5,930
            # TREASURY YIELDS".
            behind = _clean(tokens[back - 1]) if back > 0 else ""
            if run or not behind or not behind[0].isupper() \
                    or behind.lower() in _LABEL_STOP:
                break
            run.insert(0, word)
            continue
        if word[0].isupper():
            run = [word] + pending + run
            pending = []
            continue
        if not run and len(pending) < 2:
            pending.insert(0, word)
            continue
        break

    if run:
        return " ".join(run)[:22].upper()
    # No name found: a generic label beats putting a stray verb on screen.
    return "MARKETS"


def find_highlights(segments: dict, words: list) -> list[dict]:
    """
    Pull the numbers worth putting on screen out of the script, each with a
    timestamp, a label and a direction colour.
    """
    tokens, _ = _tokenize(segments)
    if not tokens:
        return []

    found: list[dict] = []
    index = 0
    while index < len(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if not _is_value(tokens[index], following):
            index += 1
            continue
        direction = _direction(tokens, index)
        found.append({
            "time": _token_time(index, len(tokens), words),
            "value": _format_value(tokens, index, direction),
            "label": _label_for(tokens, index),
            "color": GREEN if direction > 0 else RED if direction < 0 else ACCENT,
        })
        # Skip the unit word so "4.2 billion" cannot also fire on "billion".
        index += 2 if _clean(following).lower() in _UNIT_FOLLOW else 1

    timed = [h for h in found if h["time"] is not None]
    timed.sort(key=lambda h: h["time"])

    spaced: list[dict] = []
    for highlight in timed:
        if spaced and highlight["time"] - spaced[-1]["time"] < MIN_STAT_GAP_SECONDS:
            continue
        spaced.append(highlight)
    return spaced


def find_chapters(segments: dict, words: list) -> list[dict]:
    """Section kickers, timed to where each script segment begins."""
    tokens, starts = _tokenize(segments)
    if not tokens:
        return []
    chapters = []
    for token_index, name in starts:
        # The hook is the video opening — a "HOOK" kicker tells nobody anything.
        if not name or name.upper() in {"HOOK", "CTA", "OUTRO", "DISCLAIMER"}:
            continue
        moment = _token_time(token_index, len(tokens), words)
        if moment is not None:
            chapters.append({"time": moment, "text": name.upper()[:28]})
    return chapters


# ── Beat timeline ─────────────────────────────────────────────────────────────

def plan_beats(visuals: list, duration_seconds: float, segments: dict,
               words: list, beat_seconds: float = BEAT_SECONDS) -> list:
    """
    Subdivide each background's share of the runtime into beats, then attach
    the chapter and stat overlays whose timestamps land inside them.
    """
    if not visuals or duration_seconds <= 0:
        return []

    span = duration_seconds / len(visuals)
    beats: list[Beat] = []
    for position, visual in enumerate(visuals):
        subdivisions = max(1, int(round(span / max(beat_seconds, MIN_BEAT_SECONDS))))
        length = span / subdivisions
        for step in range(subdivisions):
            beats.append(Beat(image=Path(visual), duration=length,
                              start=position * span + step * length))

    for chapter in find_chapters(segments, words):
        beat = _beat_at(beats, chapter["time"])
        if beat is not None and not beat.chapter:
            beat.chapter = chapter["text"]

    for highlight in find_highlights(segments, words):
        beat = _beat_at(beats, highlight["time"])
        if beat is not None and not beat.stat_value:
            beat.stat_value = highlight["value"]
            beat.stat_label = highlight["label"]
            beat.stat_color = highlight["color"]

    return _merge_idle_beats(beats)


def _beat_at(beats: list, moment: float):
    for beat in beats:
        if beat.start <= moment < beat.start + beat.duration:
            return beat
    return beats[-1] if beats and moment >= beats[-1].start else None


def _merge_idle_beats(beats: list) -> list:
    """
    Collapse neighbouring beats that show the same background with nothing
    layered on it. Splitting an image into identical copies is pure cost —
    more files, more decoding, and not one extra pixel of change on screen.

    A stretch of narration with no numbers in it therefore still holds one
    slide for its full span. That is honest rather than ideal: faking a cut
    between identical frames does not make anything happen on screen. Making
    those stretches move is what Ken Burns and crossfades are for.
    """
    merged: list[Beat] = []
    for beat in beats:
        previous = merged[-1] if merged else None
        if (previous is not None and not previous.has_overlay
                and not beat.has_overlay and previous.image == beat.image):
            previous.duration += beat.duration
            continue
        merged.append(beat)
    return merged


# ── Rendering ─────────────────────────────────────────────────────────────────

def _font(px: int, bold: bool = True):
    from PIL import ImageFont
    name = ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    try:
        return ImageFont.truetype(name, px)
    except Exception:
        return ImageFont.load_default()


def render_beat(beat: Beat, dest: Path, width: int, height: int) -> Path:
    """
    Composite a beat's overlays onto its background and save the frame.

    Everything is drawn down the left side of the upper half: the logo badge
    is burned in at the top right and the captions own the bottom third, so
    that is the only column left where a card is guaranteed not to collide
    with something else.
    """
    from PIL import Image, ImageDraw

    base = Image.open(beat.image).convert("RGBA")
    if base.size != (width, height):
        base = base.resize((width, height), Image.LANCZOS)
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    left = int(width * 0.045)
    cursor = int(height * 0.085)

    if beat.chapter:
        font = _font(max(int(height * 0.030), 14))
        bbox = draw.textbbox((0, 0), beat.chapter, font=font)
        text_h = bbox[3] - bbox[1]
        rule_w = max(int(width * 0.004), 3)
        draw.rounded_rectangle(
            (left, cursor, left + rule_w, cursor + text_h + int(height * 0.014)),
            radius=rule_w, fill=ACCENT,
        )
        draw.text((left + rule_w + int(width * 0.012), cursor), beat.chapter,
                  font=font, fill=TEXT)
        cursor += text_h + int(height * 0.055)

    if beat.stat_value:
        label = beat.stat_label or "MARKETS"
        label_font = _font(max(int(height * 0.026), 12), bold=False)
        value_font = _font(max(int(height * 0.085), 28))
        label_box = draw.textbbox((0, 0), label, font=label_font)
        value_box = draw.textbbox((0, 0), beat.stat_value, font=value_font)
        label_w, label_h = label_box[2] - label_box[0], label_box[3] - label_box[1]
        value_w, value_h = value_box[2] - value_box[0], value_box[3] - value_box[1]

        pad = int(height * 0.030)
        gap = int(height * 0.018)
        bar = max(int(width * 0.005), 4)
        panel_w = max(label_w, value_w) + pad * 2 + bar
        panel_h = label_h + gap + value_h + pad * 2
        panel = (left, cursor, left + panel_w, cursor + panel_h)

        draw.rounded_rectangle(panel, radius=int(height * 0.018),
                               fill=SURFACE + (232,))
        draw.rounded_rectangle((left, cursor, left + bar, cursor + panel_h),
                               radius=bar // 2, fill=beat.stat_color)
        text_x = left + bar + pad
        draw.text((text_x, cursor + pad - label_box[1]), label,
                  font=label_font, fill=MUTED)
        draw.text((text_x, cursor + pad + label_h + gap - value_box[1]),
                  beat.stat_value, font=value_font, fill=beat.stat_color)

    dest.parent.mkdir(parents=True, exist_ok=True)
    Image.alpha_composite(base, layer).convert("RGB").save(dest)
    return dest


def build_beat_sequence(visuals: list, duration_seconds: float, segments: dict,
                        words: list, tmp_dir: Path,
                        width: Optional[int] = None,
                        height: Optional[int] = None) -> list:
    """
    Plan and render the beat track.

    Returns [(image_path, seconds), ...] for the concat demuxer, or [] to
    tell the caller to fall back to plain equal-length slides. Beats with no
    overlay reuse their background file untouched rather than writing an
    identical copy.
    """
    width = width or settings.video_width
    height = height or settings.video_height
    try:
        beats = plan_beats(visuals, duration_seconds, segments, words)
    except Exception as exc:
        logger.warning("Beat planning failed (non-fatal): %s", exc)
        return []
    if not beats:
        return []

    sequence: list[tuple[Path, float]] = []
    overlaid = 0
    for index, beat in enumerate(beats):
        frame = beat.image
        if beat.has_overlay:
            try:
                frame = render_beat(beat, Path(tmp_dir) / f"beat_{index:03d}.png",
                                    width, height)
                overlaid += 1
            except Exception as exc:
                # A card that will not draw is not worth losing the frame over.
                logger.warning("Beat %d overlay failed, using plain slide: %s",
                               index, exc)
                frame = beat.image
        sequence.append((Path(frame), beat.duration))

    logger.info("Beat track: %d beats over %.0fs (~%.1fs each, %d with graphics)",
                len(sequence), duration_seconds,
                duration_seconds / max(len(sequence), 1), overlaid)
    return sequence
