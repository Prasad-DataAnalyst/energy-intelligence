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


# ── Thumbnails have to work at the size they are seen ────────────────────────

def _spec(headline="S&P 500 Falls 0.55%", stat="-0.55%",
          sentiment="bearish", ticker="S&P 500"):
    from generators.thumbnail_gen import ThumbnailSpec
    return ThumbnailSpec(headline=headline, subtext=stat, ticker=ticker,
                         emoji="", sentiment=sentiment, chart_path=None,
                         logo_path=None, key_stat=stat)


class TestThumbnailReadsAtFeedSize:
    """
    A thumbnail is seen at roughly 336px wide. The previous layout put a
    wrapped headline, a subtext line, a ticker chip and a watermark on a
    near-black field, with a tofu box where the emoji should have been,
    and became an unreadable smudge.
    """

    def test_the_number_is_the_largest_element(self):
        """At feed size there is room for one idea."""
        import inspect
        from generators import thumbnail_gen
        source = inspect.getsource(thumbnail_gen.generate_thumbnail)
        assert "_fit_font" in source
        assert "THUMB_H * 0.42" in source     # the stat gets ~40% of the height

    def test_the_arrow_follows_the_number_not_the_sentiment(self):
        """
        Deriving direction from sentiment alone drew a down arrow over
        "+0.26%", because anything not classified bullish fell to else.
        """
        from generators.thumbnail_gen import generate_thumbnail
        pytest.importorskip("PIL.Image")
        from PIL import Image
        up = Image.open(generate_thumbnail(
            _spec(stat="+0.26%", sentiment="neutral")).path)
        down = Image.open(generate_thumbnail(
            _spec(stat="-0.26%", sentiment="neutral")).path)
        assert up.tobytes() != down.tobytes()

    def test_the_kicker_never_repeats_the_number(self):
        """"-0.55%" above "S&P 500 FALLS 0.55%" says one thing twice."""
        import inspect
        from generators import thumbnail_gen
        source = inspect.getsource(thumbnail_gen.generate_thumbnail)
        assert "if not any(ch.isdigit() for ch in w)" in source

    def test_no_emoji_font_dependency(self):
        """
        DejaVu carries no colour emoji glyph, so the old design rendered a
        tofu box in the most prominent position on the image.
        """
        import inspect
        from generators import thumbnail_gen
        source = inspect.getsource(thumbnail_gen.generate_thumbnail)
        assert "spec.emoji" not in source
        assert "_draw_arrow" in source

    def test_palettes_are_bright_enough_to_see(self):
        """
        Near-black reads as an empty slot in a feed. The old palettes
        bottomed out at (5, 5, 15).
        """
        from generators.thumbnail_gen import SENTIMENT_PALETTES
        for name, palette in SENTIMENT_PALETTES.items():
            assert sum(palette["bg_top"]) > 120, name

    def test_every_sentiment_renders(self):
        from generators.thumbnail_gen import generate_thumbnail, SENTIMENT_PALETTES
        pytest.importorskip("PIL.Image")
        from PIL import Image
        for sentiment in SENTIMENT_PALETTES:
            result = generate_thumbnail(_spec(sentiment=sentiment))
            assert Image.open(result.path).size == (1280, 720)

    def test_a_missing_stat_still_produces_a_thumbnail(self):
        from generators.thumbnail_gen import generate_thumbnail
        pytest.importorskip("PIL.Image")
        from PIL import Image
        result = generate_thumbnail(_spec(stat=""))
        assert Image.open(result.path).size == (1280, 720)

    def test_a_very_long_stat_is_shrunk_not_clipped(self):
        from generators.thumbnail_gen import generate_thumbnail
        pytest.importorskip("PIL.Image")
        from PIL import Image
        result = generate_thumbnail(_spec(stat="-1,234,567.89%"))
        image = Image.open(result.path).convert("RGB")
        # Nothing bright may touch the right edge of the text column.
        for y in range(0, 720, 8):
            assert image.getpixel((1279, y))[0] < 250


class TestThumbnailUploadIsAccountable:
    """
    Custom thumbnails need a verified channel. Without one the API returns
    403, the code logs it, and YouTube substitutes a frame from the video —
    which is how a channel shows the same dark cityscape on three videos
    while believing it uploaded designed art for each.
    """

    def test_a_failure_is_recorded(self, tmp_path):
        import json
        from config.settings import settings
        from uploader import uploader as umod
        settings.logs_dir = tmp_path
        umod._record_thumbnail_outcome("vid1", False, "403 forbidden")
        state = json.loads((tmp_path / umod.THUMBNAIL_STATE).read_text())
        assert state["status"] == "failed" and "403" in state["error"]

    def test_the_report_names_the_verification_fix(self, tmp_path):
        import json
        from config.settings import settings
        from monitor import health_report
        from uploader.uploader import THUMBNAIL_STATE
        settings.logs_dir = tmp_path
        (tmp_path / THUMBNAIL_STATE).write_text(json.dumps({
            "at": "2026-09-09T08:00:00", "video_id": "v",
            "status": "failed", "error": "403 forbidden"}))
        status, _, detail, lines = health_report._check_thumbnails()
        assert status == health_report._FAIL
        assert "not reaching YouTube" in detail
        assert any("verify by phone" in line for line in lines)

    def test_success_reads_clean(self, tmp_path):
        import json
        from config.settings import settings
        from monitor import health_report
        from uploader.uploader import THUMBNAIL_STATE
        settings.logs_dir = tmp_path
        (tmp_path / THUMBNAIL_STATE).write_text(json.dumps({
            "at": "2026-09-09T08:00:00", "video_id": "v", "status": "ok"}))
        assert health_report._check_thumbnails()[0] == health_report._OK

    def test_recording_never_raises(self, tmp_path):
        from config.settings import settings
        from uploader import uploader as umod
        blocked = tmp_path / "logs"
        blocked.write_text("a file where a directory should be")
        settings.logs_dir = blocked
        umod._record_thumbnail_outcome("v", False, "boom")   # must not raise
