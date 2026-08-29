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
