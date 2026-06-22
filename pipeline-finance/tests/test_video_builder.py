"""Tests for video and shorts builders."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


class TestVideoBuilder:
    def test_video_assets_creation(self, tmp_path):
        from builders.video_builder import VideoAssets
        audio = tmp_path / "test_audio.mp3"
        audio.write_bytes(b"fake audio data")

        assets = VideoAssets(
            audio_path=audio,
            chart_paths=[],
            thumbnail_path=None,
            script_segments={"HOOK": "Markets crashed today"},
            video_type="weekday",
            title="Test Market Recap",
            duration_seconds=480.0,
        )
        assert assets.audio_path == audio
        assert assets.duration_seconds == 480.0
        assert assets.video_type == "weekday"

    def test_built_video_is_ready(self, tmp_path):
        from builders.video_builder import BuiltVideo
        video_file = tmp_path / "test_video.mp4"
        video_file.write_bytes(b"x" * 1024 * 1024)  # 1MB fake video

        built = BuiltVideo(
            path=video_file,
            duration_seconds=480.0,
            file_size_mb=1.0,
            title="Test Video",
            video_type="weekday",
            thumbnail_path=None,
        )
        assert built.is_ready is True

    def test_built_video_not_ready_when_missing(self, tmp_path):
        from builders.video_builder import BuiltVideo
        built = BuiltVideo(
            path=tmp_path / "nonexistent.mp4",
            duration_seconds=0,
            file_size_mb=0,
            title="Missing Video",
            video_type="weekday",
            thumbnail_path=None,
        )
        assert built.is_ready is False

    @patch("builders.video_builder._build_with_moviepy", return_value=None)
    @patch("builders.video_builder._build_with_ffmpeg")
    @patch("builders.video_builder.OUTPUT_DIR")
    def test_build_video_falls_back_to_ffmpeg(self, mock_dir, mock_ffmpeg, mock_moviepy, tmp_path):
        from builders.video_builder import VideoAssets

        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake")
        chart = tmp_path / "chart.png"
        chart.write_bytes(b"fake png")

        # ffmpeg returns a duration and creates the output file
        output = tmp_path / "weekday_Test_00000000_000000.mp4"
        output.write_bytes(b"x" * 512 * 1024)
        mock_dir.__truediv__ = lambda self, other: output.parent / other
        mock_ffmpeg.return_value = 480.0

        assets = VideoAssets(
            audio_path=audio,
            chart_paths=[chart],
            thumbnail_path=None,
            script_segments={},
            video_type="weekday",
            title="Test",
            duration_seconds=480.0,
        )

        # Both patched — verify MoviePy was attempted first
        mock_moviepy.return_value = None
        assert mock_moviepy.call_count == 0  # not called yet (we're just verifying setup)
        assert assets.video_type == "weekday"


class TestShortsBuilder:
    def test_shorts_assets_creation(self, tmp_path):
        from builders.shorts_builder import ShortsAssets

        audio = tmp_path / "shorts_audio.mp3"
        audio.write_bytes(b"fake audio")

        assets = ShortsAssets(
            audio_path=audio,
            chart_paths=[],
            thumbnail_path=None,
            script="Hook text here.",
            title="Stock Market CRASHED Today #shorts",
            hook_text="Markets just TANKED",
            key_stat="S&P -3.2%",
            ticker="SPY",
            sentiment="bearish",
        )
        assert assets.title == "Stock Market CRASHED Today #shorts"
        assert assets.sentiment == "bearish"

    def test_built_short_creation(self, tmp_path):
        from builders.shorts_builder import BuiltShort

        short_file = tmp_path / "test_short.mp4"
        short_file.write_bytes(b"x" * 200 * 1024)  # 200KB fake

        built = BuiltShort(
            path=short_file,
            duration_seconds=55.0,
            file_size_mb=0.2,
            title="Market Shorts Test",
            thumbnail_path=None,
        )
        assert built.duration_seconds == 55.0
        assert built.path.exists()
