"""
tests/test_video_quality.py
Video production quality — the phased upgrade from "one static image" to a
modern, fast-cutting, captioned briefing.

Phase 1: concat normalization. ffmpeg's concat demuxer locks stream
parameters to the first input, so mixed image sizes silently collapsed an
11-visual sequence into a single frame for the whole video.
"""
from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PIL")
from PIL import Image


CANVAS = (1280, 720)


def _make_img(path: Path, size, colour=(40, 60, 90)):
    Image.new("RGB", size, colour).save(path)
    return path


class TestConcatNormalization:
    def test_mixed_sizes_all_become_identical(self, tmp_path):
        """The actual bug: matplotlib bbox_inches='tight' charts are all
        different sizes, PIL slides are exact — concat needs them uniform."""
        from builders.video_builder import _normalize_for_concat

        sources = [
            _make_img(tmp_path / "slide.png", CANVAS),          # PIL slide
            _make_img(tmp_path / "chart1.png", (1183, 604)),    # matplotlib
            _make_img(tmp_path / "chart2.png", (947, 812)),     # matplotlib
            _make_img(tmp_path / "photo.png", (1280, 720)),     # B-roll
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = _normalize_for_concat(sources, out_dir, *CANVAS)

        assert len(result) == 4, "no visuals may be lost"
        for p in result:
            assert Image.open(p).size == CANVAS, f"{p.name} is not canvas-sized"

    def test_aspect_ratio_preserved_by_letterboxing(self, tmp_path):
        """A tall chart must be letterboxed, never stretched."""
        from builders.video_builder import _normalize_for_concat

        src = _make_img(tmp_path / "tall.png", (400, 700), colour=(255, 0, 0))
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = _normalize_for_concat([src], out_dir, *CANVAS)
        img = Image.open(result[0])

        assert img.size == CANVAS
        # Corners are background (letterbox bars), centre is the image
        assert img.getpixel((5, 360))[0] < 60, "left edge should be padding"
        assert img.getpixel((640, 360))[0] > 200, "centre should be the image"

    def test_unreadable_file_skipped_not_fatal(self, tmp_path):
        from builders.video_builder import _normalize_for_concat

        good = _make_img(tmp_path / "good.png", (800, 600))
        bad = tmp_path / "corrupt.png"
        bad.write_text("not an image")
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = _normalize_for_concat([good, bad], out_dir, *CANVAS)

        assert len(result) == 1
        assert Image.open(result[0]).size == CANVAS

    def test_ordering_is_preserved(self, tmp_path):
        """Sequence order carries the narrative — intro must stay first."""
        from builders.video_builder import _normalize_for_concat

        sources = [
            _make_img(tmp_path / "a.png", (900, 500), colour=(255, 0, 0)),
            _make_img(tmp_path / "b.png", CANVAS, colour=(0, 255, 0)),
            _make_img(tmp_path / "c.png", (600, 400), colour=(0, 0, 255)),
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        result = _normalize_for_concat(sources, out_dir, *CANVAS)
        dominant = [max(range(3), key=lambda c: Image.open(p).getpixel((640, 360))[c])
                    for p in result]
        assert dominant == [0, 1, 2], "red, green, blue order must survive"


# ── Phase 2: word-level TTS timings ──────────────────────────────────────────

class TestWordTimings:
    def test_roundtrip_save_and_load(self, tmp_path):
        from generators.audio_gen import save_word_timings, load_word_timings
        audio = tmp_path / "seg.mp3"
        audio.write_bytes(b"\xff\xfb")
        words = [{"word": "Nasdaq", "start": 0.0, "end": 0.4},
                 {"word": "jumped", "start": 0.4, "end": 0.9}]
        save_word_timings(audio, words)
        assert load_word_timings(audio) == words

    def test_missing_timings_returns_empty(self, tmp_path):
        from generators.audio_gen import load_word_timings
        assert load_word_timings(tmp_path / "nothing.mp3") == []

    def test_empty_words_writes_nothing(self, tmp_path):
        from generators.audio_gen import save_word_timings
        audio = tmp_path / "seg.mp3"
        audio.write_bytes(b"\xff\xfb")
        assert save_word_timings(audio, []) is None

    def test_merged_timings_offset_by_segment_and_gap(self, tmp_path):
        """The drift bug this prevents: segment 2's words must be shifted by
        segment 1's duration PLUS the silence inserted between them."""
        from generators.audio_gen import (
            save_word_timings, build_merged_timings, AudioSegment,
            SEGMENT_GAP_SECONDS,
        )
        a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
        for f in (a, b):
            f.write_bytes(b"\xff\xfb")
        save_word_timings(a, [{"word": "first", "start": 0.0, "end": 1.0}])
        save_word_timings(b, [{"word": "second", "start": 0.0, "end": 1.0}])

        segs = [
            AudioSegment("HOOK", "first", a, 10.0, "edge_tts"),
            AudioSegment("MARKET", "second", b, 5.0, "edge_tts"),
        ]
        merged = build_merged_timings(segs, tmp_path / "merged.mp3")

        assert merged[0]["start"] == 0.0
        expected = 10.0 + SEGMENT_GAP_SECONDS
        assert merged[1]["start"] == expected, (
            f"second segment must start at {expected}s, got {merged[1]['start']}"
        )

    def test_merged_timings_written_beside_merged_audio(self, tmp_path):
        from generators.audio_gen import (
            save_word_timings, build_merged_timings, load_word_timings, AudioSegment,
        )
        a = tmp_path / "a.mp3"
        a.write_bytes(b"\xff\xfb")
        save_word_timings(a, [{"word": "hello", "start": 0.0, "end": 0.5}])
        merged_path = tmp_path / "final.mp3"
        build_merged_timings([AudioSegment("HOOK", "hello", a, 3.0, "edge_tts")], merged_path)
        assert len(load_word_timings(merged_path)) == 1

    def test_segments_without_audio_skipped(self, tmp_path):
        from generators.audio_gen import build_merged_timings, AudioSegment
        segs = [AudioSegment("HOOK", "x", None, None, "none")]
        assert build_merged_timings(segs, tmp_path / "m.mp3") == []

    def test_word_boundary_ticks_converted_to_seconds(self):
        """edge-tts reports 100-nanosecond ticks; 5,000,000 ticks = 0.5s."""
        import asyncio, tempfile
        from unittest.mock import patch, MagicMock
        from generators.audio_gen import _edge_tts_async

        async def fake_stream(self):
            yield {"type": "audio", "data": b"\xff\xfb"}
            yield {"type": "WordBoundary", "offset": 5_000_000,
                   "duration": 2_500_000, "text": "Nasdaq"}

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "o.mp3"
            fake = MagicMock()
            fake.stream = fake_stream.__get__(fake)
            with patch("edge_tts.Communicate", return_value=fake):
                words = asyncio.run(_edge_tts_async("Nasdaq", "v", out, use_ssml=False))

        assert words == [{"word": "Nasdaq", "start": 0.5, "end": 0.75}]


# ── Phase 3: burned-in synced captions ───────────────────────────────────────

def _words(pairs):
    """[(word, start, end), ...] → timing dicts."""
    return [{"word": w, "start": s, "end": e} for w, s, e in pairs]


class TestCaptionGrouping:
    def test_groups_into_short_phrases(self):
        from builders.caption_renderer import group_words_into_cues, MAX_WORDS_PER_CUE
        words = _words([(f"w{i}", i * 0.3, i * 0.3 + 0.25) for i in range(12)])
        cues = group_words_into_cues(words)
        assert cues, "must produce cues"
        for cue in cues:
            assert len(cue["text"].split()) <= MAX_WORDS_PER_CUE

    def test_breaks_on_sentence_end(self):
        from builders.caption_renderer import group_words_into_cues
        cues = group_words_into_cues(_words([
            ("Nasdaq", 0.0, 0.4), ("rose.", 0.4, 0.8),
            ("Tech", 0.9, 1.2), ("fell.", 1.2, 1.6),
        ]))
        assert len(cues) == 2
        assert cues[0]["text"] == "Nasdaq rose."

    def test_breaks_on_speech_pause(self):
        from builders.caption_renderer import group_words_into_cues
        # A one-second silence is a natural phrase boundary
        cues = group_words_into_cues(_words([
            ("first", 0.0, 0.4), ("part", 0.4, 0.8),
            ("second", 1.8, 2.2), ("part", 2.2, 2.6),
        ]))
        assert len(cues) == 2

    def test_cue_times_follow_the_words(self):
        from builders.caption_renderer import group_words_into_cues
        cues = group_words_into_cues(_words([("hello", 5.0, 5.5), ("there.", 5.5, 6.0)]))
        assert cues[0]["start"] == 5.0
        assert cues[0]["end"] >= 6.0

    def test_empty_input_yields_no_cues(self):
        from builders.caption_renderer import group_words_into_cues
        assert group_words_into_cues([]) == []

    def test_blank_words_ignored(self):
        from builders.caption_renderer import group_words_into_cues
        cues = group_words_into_cues(_words([("", 0.0, 0.1), ("real.", 0.1, 0.5)]))
        assert len(cues) == 1 and cues[0]["text"] == "real."


class TestAssOutput:
    def test_writes_valid_ass_structure(self, tmp_path):
        from builders.caption_renderer import build_ass_captions
        out = build_ass_captions(
            _words([("Nasdaq", 0.0, 0.5), ("jumped.", 0.5, 1.0)]),
            tmp_path / "c.ass", width=1280, height=720,
        )
        assert out is not None
        text = out.read_text()
        assert "[Script Info]" in text and "[V4+ Styles]" in text and "[Events]" in text
        assert "PlayResX: 1280" in text
        assert "Dialogue: 0,0:00:00.00," in text
        assert "Nasdaq jumped." in text

    def test_timestamp_format_handles_minutes(self, tmp_path):
        from builders.caption_renderer import build_ass_captions
        out = build_ass_captions(
            _words([("late", 125.0, 125.5)]), tmp_path / "c.ass",
        )
        assert "0:02:05.00" in out.read_text()

    def test_font_scales_with_canvas(self, tmp_path):
        from builders.caption_renderer import build_ass_captions
        small = build_ass_captions(_words([("x", 0.0, 0.5)]), tmp_path / "s.ass",
                                   width=1280, height=720).read_text()
        large = build_ass_captions(_words([("x", 0.0, 0.5)]), tmp_path / "l.ass",
                                   width=1920, height=1080).read_text()

        def size(t):
            line = [l for l in t.splitlines() if l.startswith("Style: DW")][0]
            return int(line.split(",")[2])

        assert size(large) > size(small)

    def test_no_timings_returns_none(self, tmp_path):
        from builders.caption_renderer import build_ass_captions
        assert build_ass_captions([], tmp_path / "c.ass") is None

    def test_braces_escaped_not_treated_as_ass_override(self, tmp_path):
        """Curly braces are override-tag syntax in ASS — they must not survive."""
        from builders.caption_renderer import build_ass_captions
        out = build_ass_captions(
            _words([("{\\an8}hack", 0.0, 0.5)]), tmp_path / "c.ass",
        )
        body = out.read_text().split("[Events]")[1]
        assert "{" not in body and "}" not in body

    def test_captions_for_audio_uses_saved_timings(self, tmp_path):
        from generators.audio_gen import save_word_timings
        from builders.caption_renderer import captions_for_audio
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\xff\xfb")
        save_word_timings(audio, _words([("hello", 0.0, 0.5)]))
        out = captions_for_audio(audio, tmp_path)
        assert out is not None and out.exists()

    def test_captions_for_audio_without_timings(self, tmp_path):
        from builders.caption_renderer import captions_for_audio
        audio = tmp_path / "a.mp3"
        audio.write_bytes(b"\xff\xfb")
        assert captions_for_audio(audio, tmp_path) is None


# ── Phase 4: beat system ──────────────────────────────────────────────────────

class TestBeatHighlights:
    """Numbers pulled out of the narration for on-screen stat cards."""

    SEGMENTS = {
        "HOOK": "Something big happened today.",
        "MARKET RECAP": "The S&P 500 closed up 0.66% at 5,930. "
                        "Treasury yields fell 6 basis points to 4.21%.",
        "TOP MOVERS": "Tesla dropped 3.4%. Apple added 0.8%.",
        "ECONOMIC DATA": "Jobless claims came in at 221,000 last week.",
    }

    @staticmethod
    def _words(segments, per_word=0.5):
        words, index = [], 0
        for text in segments.values():
            for token in text.split():
                words.append({"word": token,
                              "start": round(index * per_word, 3),
                              "end": round(index * per_word + 0.4, 3)})
                index += 1
        return words

    def _highlights(self, per_word=1.5):
        from builders.beat_planner import find_highlights
        return find_highlights(self.SEGMENTS, self._words(self.SEGMENTS, per_word))

    def test_percentages_become_stat_cards(self):
        values = [h["value"] for h in self._highlights()]
        assert "+0.66%" in values

    def test_label_is_the_proper_noun_not_the_verb(self):
        labels = {h["value"]: h["label"] for h in self._highlights()}
        assert labels["+0.66%"] == "S&P 500"

    def test_direction_read_from_nearest_word_not_earliest(self):
        """
        "Tesla dropped 3.4%. Apple added 0.8%" — scanning forwards finds
        "dropped" first and paints Apple's gain red.
        """
        from builders.beat_planner import find_highlights, GREEN
        segments = {"A": "Tesla dropped 3.4%. Apple added 0.8% on the day."}
        found = find_highlights(segments, self._words(segments, per_word=1.5))
        apple = [h for h in found if h["label"] == "APPLE"]
        assert apple, f"Apple's move was not detected: {found}"
        assert apple[0]["color"] == GREEN
        assert apple[0]["value"] == "+0.8%"

    def test_earlier_number_does_not_leak_into_the_label(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "Tesla dropped 3.4%. Apple added 0.8% on the day."}
        found = find_highlights(segments, self._words(segments, per_word=1.5))
        assert all("%" not in h["label"] for h in found), found

    def test_earlier_bare_number_does_not_leak_into_the_label(self):
        """"closed at 5,930. Treasury yields fell 6 bps" is two figures."""
        from builders.beat_planner import find_highlights
        segments = {"A": "The index closed at 5,930 today. "
                         "Treasury yields fell 6 basis points overnight."}
        found = find_highlights(segments, self._words(segments, per_word=1.5))
        bps = [h for h in found if h["value"] == "-6 bps"]
        assert bps and bps[0]["label"] == "TREASURY YIELDS", found

    def test_label_does_not_cross_a_sentence_boundary(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "Stocks rose on Friday. "
                         "Jobless claims came in at 221,000 last week."}
        found = find_highlights(segments, self._words(segments, per_word=1.5))
        claims = [h for h in found if h["value"] == "221,000"]
        assert claims and claims[0]["label"] == "JOBLESS CLAIMS", found

    def test_comma_grouped_number_counts_as_a_figure(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "Jobless claims came in at 221,000 last week."}
        found = find_highlights(segments, self._words(segments, per_word=1.5))
        assert [h["value"] for h in found] == ["221,000"]

    def test_clock_times_and_years_are_not_figures(self):
        """A bare number is usually a date or a time, not a statistic."""
        from builders.beat_planner import find_highlights
        segments = {"A": "Watch the print at 8:30 Eastern in 2026 next quarter."}
        assert find_highlights(segments, self._words(segments)) == []

    def test_unit_words_never_become_the_label(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "The index fell 6 basis points to 4.21% on the session."}
        found = find_highlights(segments, self._words(segments, per_word=1.5))
        assert all("BASIS" not in h["label"] and "POINTS" not in h["label"]
                   for h in found), found

    def test_lowercase_word_kept_when_a_proper_noun_precedes_it(self):
        labels = [h["label"] for h in self._highlights()]
        assert "TREASURY YIELDS" in labels

    def test_unit_word_is_folded_into_the_value(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "The index fell 6 basis points overnight."}
        found = find_highlights(segments, self._words(segments))
        assert found[0]["value"] == "-6 bps"

    def test_scale_word_is_abbreviated(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "NVIDIA added $400 billion in market value."}
        found = find_highlights(segments, self._words(segments))
        assert found[0]["value"] == "$400B"

    def test_unit_word_does_not_fire_a_second_card(self):
        """"4.2 billion" must yield one card, not one for 4.2 and one for billion."""
        from builders.beat_planner import find_highlights
        segments = {"A": "Revenue reached $4.2 billion this quarter."}
        found = find_highlights(segments, self._words(segments))
        assert len(found) == 1, found

    def test_price_level_gets_no_plus_sign(self):
        """A level is not a move — "+5,930" would be nonsense."""
        from builders.beat_planner import find_highlights
        segments = {"A": "The index climbed to 5,930 points at the close."}
        found = find_highlights(segments, self._words(segments))
        assert not found[0]["value"].startswith("+")

    def test_cards_are_spaced_out(self):
        from builders.beat_planner import MIN_STAT_GAP_SECONDS
        times = [h["time"] for h in self._highlights(per_word=0.5)]
        gaps = [b - a for a, b in zip(times, times[1:])]
        assert all(g >= MIN_STAT_GAP_SECONDS for g in gaps), gaps

    def test_no_words_means_no_cards(self):
        from builders.beat_planner import find_highlights
        assert find_highlights(self.SEGMENTS, []) == []

    def test_prose_without_numbers_yields_nothing(self):
        from builders.beat_planner import find_highlights
        segments = {"A": "Traders watched and waited for direction all session."}
        assert find_highlights(segments, self._words(segments)) == []


class TestBeatChapters:
    def test_section_names_become_chapters(self):
        from builders.beat_planner import find_chapters
        segments = {"HOOK": "one two three", "MARKET RECAP": "four five six"}
        words = [{"word": "w", "start": i * 0.5, "end": i * 0.5 + 0.4}
                 for i in range(6)]
        names = [c["text"] for c in find_chapters(segments, words)]
        assert names == ["MARKET RECAP"]

    def test_structural_sections_are_not_shown(self):
        """HOOK/CTA label the script's plumbing, not anything a viewer needs."""
        from builders.beat_planner import find_chapters
        segments = {"HOOK": "a b", "CTA": "c d", "OUTRO": "e f"}
        words = [{"word": "w", "start": i * 0.5, "end": i * 0.5 + 0.4}
                 for i in range(6)]
        assert find_chapters(segments, words) == []


class TestBeatTimeline:
    VISUALS = [Path(f"/tmp/visual_{i}.png") for i in range(11)]

    def test_beats_are_denser_than_the_visuals(self):
        """The whole point: 11 slides over 4 minutes is one change per 21s."""
        from builders.beat_planner import plan_beats
        segments = {"MARKET RECAP": " ".join(
            f"Ticker{i} rose {i}.5% today." for i in range(20))}
        words = [{"word": "w", "start": i * 2.0, "end": i * 2.0 + 1.5}
                 for i in range(120)]
        beats = plan_beats(self.VISUALS, 240.0, segments, words)
        assert len(beats) > len(self.VISUALS)

    def test_durations_sum_to_the_narration_length(self):
        from builders.beat_planner import plan_beats
        segments = {"MARKET RECAP": "The S&P 500 rose 0.66% today. " * 10}
        words = [{"word": "w", "start": i * 2.0, "end": i * 2.0 + 1.5}
                 for i in range(120)]
        beats = plan_beats(self.VISUALS, 240.0, segments, words)
        assert sum(b.duration for b in beats) == pytest.approx(240.0, abs=0.05)

    def test_beats_carrying_graphics_stay_near_the_target_interval(self):
        """
        Idle stretches are allowed to hold — see _merge_idle_beats. What must
        not drift is the pace where there is something to show.
        """
        from builders.beat_planner import plan_beats, BEAT_SECONDS
        segments = {"MARKET RECAP": " ".join(
            f"Ticker{i} rose {i}.5% today and held there." for i in range(24))}
        words = [{"word": "w", "start": i * 1.0, "end": i * 1.0 + 0.8}
                 for i in range(240)]
        beats = plan_beats(self.VISUALS, 240.0, segments, words)
        graphic = [b for b in beats if b.has_overlay]
        assert len(graphic) >= 20, len(graphic)
        assert all(b.duration <= BEAT_SECONDS * 1.6 for b in graphic)

    def test_beats_follow_the_visual_order(self):
        """Backgrounds are sequenced to the narration — beats must not reshuffle."""
        from builders.beat_planner import plan_beats
        segments = {"MARKET RECAP": "The S&P 500 rose 0.66% today. " * 10}
        words = [{"word": "w", "start": i * 2.0, "end": i * 2.0 + 1.5}
                 for i in range(120)]
        beats = plan_beats(self.VISUALS, 240.0, segments, words)
        seen = list(dict.fromkeys(b.image for b in beats))
        assert seen == self.VISUALS

    def test_idle_beats_are_merged_back_together(self):
        """
        With nothing to overlay, splitting a slide into identical copies costs
        files and decoding and changes not one pixel on screen.
        """
        from builders.beat_planner import plan_beats
        beats = plan_beats(self.VISUALS, 240.0, {}, [])
        assert len(beats) == len(self.VISUALS)

    def test_stat_spacing_exceeds_a_beat(self):
        """
        The invariant that lets the planner assign cards without arbitrating:
        cards are spaced further apart than a beat is long, so two can never
        want the same frame.
        """
        from builders.beat_planner import BEAT_SECONDS, MIN_STAT_GAP_SECONDS
        assert MIN_STAT_GAP_SECONDS > BEAT_SECONDS

    def test_every_card_gets_its_own_frame(self):
        from builders.beat_planner import plan_beats, find_highlights
        segments = {"MARKET RECAP": " ".join(
            f"Ticker{i} rose {i}.5% today and held there." for i in range(24))}
        words = [{"word": "w", "start": i * 1.0, "end": i * 1.0 + 0.8}
                 for i in range(240)]
        beats = plan_beats(self.VISUALS, 240.0, segments, words)
        placed = sum(1 for b in beats if b.stat_value)
        assert placed == len(find_highlights(segments, words))

    def test_empty_input_is_survivable(self):
        from builders.beat_planner import plan_beats
        assert plan_beats([], 240.0, {}, []) == []
        assert plan_beats(self.VISUALS, 0.0, {}, []) == []


class TestBeatRendering:
    @staticmethod
    def _background(tmp_path, size=(640, 360)):
        Image = pytest.importorskip("PIL.Image")
        path = tmp_path / "bg.png"
        Image.new("RGB", size, (10, 10, 15)).save(path)
        return path

    def test_overlay_changes_the_frame(self, tmp_path):
        from builders.beat_planner import Beat, render_beat, GREEN
        Image = pytest.importorskip("PIL.Image")
        source = self._background(tmp_path)
        beat = Beat(source, 3.5, stat_value="+0.66%", stat_label="S&P 500",
                    stat_color=GREEN)
        out = render_beat(beat, tmp_path / "beat.png", 640, 360)
        assert out.exists()
        assert list(Image.open(out).getdata()) != list(Image.open(source).getdata())

    def test_rendered_frame_matches_the_canvas(self, tmp_path):
        """Concat locks stream parameters to the first input — see Phase 1."""
        from builders.beat_planner import Beat, render_beat
        Image = pytest.importorskip("PIL.Image")
        beat = Beat(self._background(tmp_path, (500, 280)), 3.5,
                    chapter="MARKET RECAP")
        out = render_beat(beat, tmp_path / "beat.png", 640, 360)
        assert Image.open(out).size == (640, 360)

    def test_plain_beats_reuse_their_background(self, tmp_path):
        """No overlay means no reason to write a byte-identical copy."""
        from builders.beat_planner import build_beat_sequence
        pytest.importorskip("PIL.Image")
        source = self._background(tmp_path)
        sequence = build_beat_sequence([source], 12.0, {}, [], tmp_path, 640, 360)
        assert [f for f, _ in sequence] == [source]

    def test_sequence_durations_cover_the_narration(self, tmp_path):
        from builders.beat_planner import build_beat_sequence
        pytest.importorskip("PIL.Image")
        source = self._background(tmp_path)
        segments = {"MARKET RECAP": "The S&P 500 rose 0.66% today. " * 4}
        words = [{"word": "w", "start": i * 0.5, "end": i * 0.5 + 0.4}
                 for i in range(24)]
        sequence = build_beat_sequence([source], 12.0, segments, words,
                                       tmp_path, 640, 360)
        assert sum(d for _, d in sequence) == pytest.approx(12.0, abs=0.05)

    def test_unreadable_background_falls_back_to_the_plain_slide(self, tmp_path):
        """A card that will not draw is not worth losing the frame over."""
        from builders.beat_planner import build_beat_sequence
        broken = tmp_path / "broken.png"
        broken.write_text("not an image")
        segments = {"MARKET RECAP": "The S&P 500 rose 0.66% today."}
        words = [{"word": "w", "start": i * 0.5, "end": i * 0.5 + 0.4}
                 for i in range(6)]
        sequence = build_beat_sequence([broken], 12.0, segments, words,
                                       tmp_path, 640, 360)
        # The cold open draws its own frame, so it survives a corrupt slide.
        body = [f for f, _ in sequence if f.name != "hook.png"]
        assert body and all(f == broken for f in body)


# ── Phase 5: cold-open hook ───────────────────────────────────────────────────

def _timed(segments, duration=240.0):
    count = sum(len(t.split()) for t in segments.values())
    return [{"word": "w",
             "start": round(i * duration / count, 3),
             "end": round(i * duration / count + 0.3, 3)}
            for i in range(count)]


class TestHookSelection:
    def test_opens_on_the_figure_from_the_hook_line(self):
        from builders.beat_planner import pick_hook
        segments = {
            "HOOK": "NVIDIA added $400 billion in market value today.",
            "MARKET RECAP": "The S&P 500 rose 0.66%. Tesla dropped 12.5%.",
        }
        hook = pick_hook(segments, _timed(segments))
        assert (hook["label"], hook["value"]) == ("NVIDIA", "$400B")

    def test_falls_back_to_the_loudest_figure_anywhere(self):
        """A qualitative hook line still deserves a number on screen."""
        from builders.beat_planner import pick_hook
        segments = {
            "HOOK": "Something remarkable happened on Wall Street today.",
            "MARKET RECAP": "The S&P 500 rose 0.66%. Tesla dropped 12.5%.",
        }
        hook = pick_hook(segments, _timed(segments))
        assert hook["value"] == "-12.5%"

    def test_a_script_with_no_figures_has_no_hook(self):
        from builders.beat_planner import pick_hook
        segments = {"HOOK": "Markets drifted sideways.",
                    "RECAP": "Traders waited for direction."}
        assert pick_hook(segments, _timed(segments)) is None

    def test_a_single_segment_script_still_picks_one(self):
        from builders.beat_planner import pick_hook
        segments = {"ALL": "The S&P 500 rose 0.66%. Tesla dropped 12.5%."}
        assert pick_hook(segments, _timed(segments)) is not None

    def test_a_big_single_stock_move_outranks_a_market_cap_figure(self):
        from builders.beat_planner import _impact
        assert _impact({"value": "-12.5%"}) > _impact({"value": "$400B"})

    def test_a_level_scores_nothing(self):
        """"5,930" is where the index sits, not something that happened."""
        from builders.beat_planner import _impact
        assert _impact({"value": "5,930"}) == 0


class TestColdOpenRendering:
    HOOK = {"value": "+9.1%", "label": "NVIDIA", "color": (0, 196, 107)}

    def test_hook_frame_matches_the_canvas(self, tmp_path):
        from builders.beat_planner import render_hook
        Image = pytest.importorskip("PIL.Image")
        out = render_hook(self.HOOK, tmp_path / "hook.png", 640, 360)
        assert Image.open(out).size == (640, 360)

    def test_a_long_value_is_shrunk_to_fit(self, tmp_path):
        """A hook that runs off the frame is worse than a small one."""
        from builders.beat_planner import render_hook
        Image = pytest.importorskip("PIL.Image")
        wide = {"value": "-1,234,567.89%", "label": "SOMETHING VERY LONG",
                "color": (255, 75, 75)}
        out = render_hook(wide, tmp_path / "hook.png", 640, 360)
        image = Image.open(out).convert("RGB")
        # Nothing coloured may touch either edge of the frame.
        for x in (0, 639):
            column = {image.getpixel((x, y)) for y in range(0, 360, 4)}
            assert all(pixel[0] < 90 or pixel[1] < 90 for pixel in column)

    def test_direction_is_visible_in_the_frame(self, tmp_path):
        from builders.beat_planner import render_hook
        Image = pytest.importorskip("PIL.Image")
        up = render_hook(self.HOOK, tmp_path / "up.png", 640, 360)
        down = render_hook({**self.HOOK, "value": "-9.1%",
                            "color": (255, 75, 75)}, tmp_path / "down.png", 640, 360)
        assert Image.open(up).tobytes() != Image.open(down).tobytes()


class TestColdOpenSequencing:
    @staticmethod
    def _visuals(tmp_path, count=6):
        Image = pytest.importorskip("PIL.Image")
        made = []
        for i in range(count):
            path = tmp_path / f"v{i}.png"
            Image.new("RGB", (640, 360), (10, 10, 15)).save(path)
            made.append(path)
        return made

    SEGMENTS = {
        "HOOK": "NVIDIA added $400 billion in market value in a single session.",
        "MARKET RECAP": "The S&P 500 closed up 0.66% at 5,930.",
        "TOP MOVERS": "Tesla dropped 3.4%. Apple added 0.8% on heavy volume.",
        "CTA": "Subscribe for daily briefings.",
    }

    def test_the_video_opens_on_the_hook(self, tmp_path):
        from builders.beat_planner import build_beat_sequence, HOOK_SECONDS
        sequence = build_beat_sequence(
            self._visuals(tmp_path), 240.0, self.SEGMENTS,
            _timed(self.SEGMENTS), tmp_path, 640, 360)
        assert sequence[0][0].name == "hook.png"
        assert sequence[0][1] == HOOK_SECONDS

    def test_the_hook_does_not_stretch_the_video(self, tmp_path):
        """
        Audio length is fixed. Time given to the cold open has to come out of
        the body, or the visuals outrun the narration.
        """
        from builders.beat_planner import build_beat_sequence
        sequence = build_beat_sequence(
            self._visuals(tmp_path), 240.0, self.SEGMENTS,
            _timed(self.SEGMENTS), tmp_path, 640, 360)
        assert sum(d for _, d in sequence) == pytest.approx(240.0, abs=0.05)

    def test_overlays_stay_matched_to_the_narration(self, tmp_path):
        """
        The body starts after the hook, so its clock has to start there too —
        otherwise every card lands HOOK_SECONDS early and drifts.
        """
        from builders.beat_planner import plan_beats, HOOK_SECONDS
        beats = plan_beats(self._visuals(tmp_path), 240.0, self.SEGMENTS,
                           _timed(self.SEGMENTS), reserved_head=HOOK_SECONDS)
        assert beats[0].start == pytest.approx(HOOK_SECONDS)

    def test_a_short_video_gets_no_cold_open(self, tmp_path):
        """Two and a half seconds of preamble is a lot of a 6-second clip."""
        from builders.beat_planner import build_beat_sequence
        sequence = build_beat_sequence(
            self._visuals(tmp_path, 2), 6.0, self.SEGMENTS,
            _timed(self.SEGMENTS, 6.0), tmp_path, 640, 360)
        assert all(f.name != "hook.png" for f, _ in sequence)

    def test_a_script_with_no_figures_opens_as_before(self, tmp_path):
        from builders.beat_planner import build_beat_sequence
        segments = {"HOOK": "Markets drifted.", "RECAP": "Traders waited."}
        sequence = build_beat_sequence(
            self._visuals(tmp_path), 240.0, segments,
            _timed(segments), tmp_path, 640, 360)
        assert sequence[0][0].name != "hook.png"
        assert sum(d for _, d in sequence) == pytest.approx(240.0, abs=0.05)
