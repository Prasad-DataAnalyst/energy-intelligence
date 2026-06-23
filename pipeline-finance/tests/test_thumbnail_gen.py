"""Tests for ThumbnailGenerator class (Module 10)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.thumbnail_gen import ThumbnailGenerator, ThumbnailFile, MAX_THUMB_BYTES


class TestThumbnailGenerator:

    @pytest.fixture
    def tg(self, tmp_path):
        return ThumbnailGenerator(output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        d = tmp_path / "thumbs"
        ThumbnailGenerator(output_dir=d)
        assert d.exists()

    def test_make_headline_strips_stop_words(self, tg):
        headline = tg._make_headline("The Stock Market Is Crashing Today")
        assert "THE" in headline or "STOCK" in headline
        words = headline.split()
        assert len(words) <= 3

    def test_make_headline_caps_result(self, tg):
        result = tg._make_headline("apple earnings beat")
        assert result == result.upper()

    def test_validate_file_size_returns_true_for_small_file(self, tg, tmp_path):
        f = tmp_path / "small.jpg"
        f.write_bytes(b"x" * 1000)  # 1KB
        assert tg.validate_file_size(f) is True

    def test_validate_file_size_returns_false_for_missing(self, tg, tmp_path):
        f = tmp_path / "missing.jpg"
        assert tg.validate_file_size(f) is False

    def test_validate_file_size_recompresses_large_file(self, tg, tmp_path):
        f = tmp_path / "large.jpg"
        f.write_bytes(b"x" * (MAX_THUMB_BYTES + 1))
        mock_img = MagicMock()
        # Simulate that save reduces the file size on first recompression
        def fake_save(path, fmt, **kw):
            f.write_bytes(b"x" * 500_000)
        mock_img.save.side_effect = fake_save
        with patch("PIL.Image.open", return_value=mock_img):
            result = tg.validate_file_size(f)
        assert result is True

    def test_generate_weekday_thumbnail_bullish_sentiment(self, tg):
        mock_thumb = MagicMock(spec=ThumbnailFile)
        mock_thumb.path = tg.output_dir / "thumb.jpg"
        mock_thumb.path.write_bytes(b"x" * 100)
        with patch("generators.thumbnail_gen.generate_thumbnail", return_value=mock_thumb):
            result = tg.generate_weekday_thumbnail("SPY Crashes", "-5%", "SPY", "tier1")
        assert isinstance(result, ThumbnailFile)

    def test_generate_weekday_thumbnail_bearish_on_minus_stat(self, tg):
        from generators.thumbnail_gen import ThumbnailSpec
        captured = {}
        def fake_generate(spec, chart_path=None):
            captured["sentiment"] = spec.sentiment
            mock_thumb = MagicMock(spec=ThumbnailFile)
            mock_thumb.path = tg.output_dir / "thumb.jpg"
            mock_thumb.path.write_bytes(b"x" * 100)
            return mock_thumb
        with patch("generators.thumbnail_gen.generate_thumbnail", side_effect=fake_generate):
            tg.generate_weekday_thumbnail("Market Falls", "-2.5%", tier="tier2")
        assert captured["sentiment"] == "bearish"

    def test_generate_sunday_thumbnail_uses_education_emoji(self, tg):
        from generators.thumbnail_gen import ThumbnailSpec
        captured = {}
        def fake_generate(spec, chart_path=None):
            captured["emoji"] = spec.emoji
            mock_thumb = MagicMock(spec=ThumbnailFile)
            mock_thumb.path = tg.output_dir / "thumb.jpg"
            mock_thumb.path.write_bytes(b"x" * 100)
            return mock_thumb
        with patch("generators.thumbnail_gen.generate_thumbnail", side_effect=fake_generate):
            tg.generate_sunday_thumbnail("How Bonds Work", "investment_banking")
        assert captured["emoji"] == "🎓"

    def test_max_thumb_bytes_is_2mb(self):
        assert MAX_THUMB_BYTES == 2 * 1024 * 1024
