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
