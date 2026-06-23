"""Tests for VideoBuilder and ShortsBuilder classes (Modules 11 and 12)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from builders.video_builder import VideoBuilder
from builders.shorts_builder import ShortsBuilder, ShortsOverLimitError, CARD_STYLES


class TestVideoBuilderClass:

    @pytest.fixture
    def vb(self, tmp_path):
        return VideoBuilder(output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        d = tmp_path / "videos"
        VideoBuilder(output_dir=d)
        assert d.exists()

    def test_validate_duration_ok(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=0, stdout="150.0\n")
        with patch("subprocess.run", return_value=mock_result):
            valid, dur = vb.validate_duration(f)
        assert valid is True
        assert dur == 150.0

    def test_validate_duration_too_short(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=0, stdout="60.0\n")
        with patch("subprocess.run", return_value=mock_result):
            valid, dur = vb.validate_duration(f)
        assert valid is False

    def test_validate_duration_too_long(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=0, stdout="200.0\n")
        with patch("subprocess.run", return_value=mock_result):
            valid, dur = vb.validate_duration(f)
        assert valid is False

    def test_add_watermark_returns_video_path_on_failure(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=1, stderr=b"error")
        with patch("subprocess.run", return_value=mock_result):
            result = vb.add_watermark(f)
        assert result == f

    def test_mix_audio_returns_video_path_on_failure(self, vb, tmp_path):
        video = tmp_path / "video.mp4"
        music = tmp_path / "music.mp3"
        video.write_bytes(b"video")
        music.write_bytes(b"music")
        mock_result = MagicMock(returncode=1, stderr=b"error")
        with patch("subprocess.run", return_value=mock_result):
            result = vb.mix_audio(video, music)
        assert result == video

    def test_burn_captions_returns_original_on_empty(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        result = vb.burn_captions(f, [])
        assert result == f

    def test_burn_captions_calls_ffmpeg(self, vb, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        out = tmp_path / "captioned_video.mp4"
        out.write_bytes(b"output")
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            result = vb.burn_captions(f, [(0.0, 5.0, "Hello world")])
        assert result is not None

    def test_export_raises_on_failure(self, vb, tmp_path):
        f = tmp_path / "input.mp4"
        f.write_bytes(b"fake")
        mock_result = MagicMock(returncode=1, stderr=b"error msg")
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError):
                vb.export(f)


class TestShortsBuilderClass:

    @pytest.fixture
    def sb(self, tmp_path):
        return ShortsBuilder(output_dir=tmp_path)

    def test_init_creates_output_dir(self, tmp_path):
        d = tmp_path / "shorts"
        ShortsBuilder(output_dir=d)
        assert d.exists()

    def test_add_shorts_tag_appends_when_missing(self, sb):
        title = "Market Crash Today"
        result = sb.add_shorts_tag(title)
        assert "#Shorts" in result

    def test_add_shorts_tag_no_double_append(self, sb):
        title = "Market Crash #Shorts"
        result = sb.add_shorts_tag(title)
        assert result.count("#Shorts") == 1

    def test_add_shorts_tag_handles_lowercase(self, sb):
        title = "Market Crash #shorts"
        result = sb.add_shorts_tag(title)
        assert "#Shorts" not in result or result.count("#shorts") == 1

    def test_parse_cards_from_section_markers(self, sb):
        script = "[HOOK]\nOpen strong\n[STAT]\n$1.2T GDP\n[CONTEXT]\nFed meeting\n[CALLOUT]\nBig deal\n[CTA]\nSubscribe"
        cards = sb.parse_cards(script)
        assert len(cards) >= 4
        types = [c["type"] for c in cards]
        assert "hook" in types or "stat" in types

    def test_parse_cards_from_card_markers(self, sb):
        script = "[CARD 1]\nHook text\n[CARD 2]\n$500B\n[CARD 3]\nContext\n[CARD 4]\nCallout\n[CARD 5]\nCTA"
        cards = sb.parse_cards(script)
        assert len(cards) == 5

    def test_parse_cards_fallback_on_unstructured(self, sb):
        script = "Line 1\nLine 2\nLine 3"
        cards = sb.parse_cards(script)
        assert len(cards) == 5  # fallback always returns 5

    def test_parse_cards_max_5(self, sb):
        script = "\n".join(f"[CARD {i}]\nText {i}" for i in range(1, 10))
        cards = sb.parse_cards(script)
        assert len(cards) <= 5

    def test_card_styles_has_5_types(self):
        assert len(CARD_STYLES) == 5
        assert "hook" in CARD_STYLES
        assert "cta" in CARD_STYLES

    def test_add_background_music_returns_original_when_no_music_dir(self, sb, tmp_path):
        video = tmp_path / "short.mp4"
        video.write_bytes(b"fake")
        with patch("config.settings.settings") as mock_settings:
            mock_settings.assets_dir = tmp_path  # no music subdir
            result = sb.add_background_music(video)
        assert result == video

    def test_music_history_persists_7_entries(self, sb):
        history = [f"track{i}.mp3" for i in range(10)]
        sb._save_music_history(history)
        loaded = sb._load_music_history()
        assert len(loaded) == 7

    def test_shorts_over_limit_error_is_exception(self):
        with pytest.raises(ShortsOverLimitError):
            raise ShortsOverLimitError("Over 60s")
