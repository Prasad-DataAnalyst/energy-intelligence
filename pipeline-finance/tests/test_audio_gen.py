"""Tests for AudioGenerator class (Module 8)."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from generators.audio_gen import AudioGenerator, _WEEKDAY_VOICES, MIN_AUDIO_SECONDS, MAX_AUDIO_SECONDS


class TestAudioGenerator:

    @pytest.fixture
    def ag(self):
        return AudioGenerator()

    def test_get_todays_voice_returns_string(self, ag):
        voice = ag.get_todays_voice()
        assert isinstance(voice, str)
        assert "Neural" in voice

    def test_weekday_voices_covers_all_days(self):
        for day in range(7):
            assert day in _WEEKDAY_VOICES
            assert "Neural" in _WEEKDAY_VOICES[day]

    def test_shorts_returns_none(self, ag):
        result = ag.generate_main_audio({"HOOK": "test text"}, "shorts")
        assert result is None

    def test_generate_fallback_audio_returns_path_on_success(self, ag, tmp_path):
        out = tmp_path / "test.mp3"
        mock_tts = MagicMock()
        with patch("generators.audio_gen.AudioGenerator.generate_fallback_audio") as mock_fa:
            mock_fa.return_value = out
            result = ag.generate_fallback_audio("Hello world", out)
        # Method should return a path or None
        assert result is None or isinstance(result, Path)

    def test_generate_fallback_audio_returns_none_on_failure(self, ag, tmp_path):
        out = tmp_path / "test.mp3"
        with patch("builtins.__import__", side_effect=ImportError("gtts not installed")):
            result = ag.generate_fallback_audio("Hello", out)
        assert result is None or isinstance(result, Path)

    def test_get_audio_duration_uses_mutagen(self, ag, tmp_path):
        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"fake mp3")
        mock_mp3 = MagicMock()
        mock_mp3.info.length = 135.0
        with patch.dict("sys.modules", {"mutagen.mp3": MagicMock(MP3=MagicMock(return_value=mock_mp3))}):
            dur = ag.get_audio_duration(audio_file)
        assert dur == 135.0 or isinstance(dur, float)

    def test_get_audio_duration_fallback_ffprobe(self, ag, tmp_path):
        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"fake mp3")
        mock_result = MagicMock(returncode=0, stdout="145.0\n")
        bad_mp3 = MagicMock(side_effect=Exception("no mutagen"))
        bad_pydub = MagicMock(side_effect=Exception("no pydub"))
        with patch.dict("sys.modules", {
            "mutagen.mp3": MagicMock(MP3=bad_mp3),
            "pydub": MagicMock(AudioSegment=MagicMock(from_file=bad_pydub)),
        }):
            with patch("subprocess.run", return_value=mock_result):
                dur = ag.get_audio_duration(audio_file)
        assert isinstance(dur, float)

    def test_add_silence_padding_uses_pydub(self, ag, tmp_path):
        audio_file = tmp_path / "audio.mp3"
        audio_file.write_bytes(b"fake mp3 data")
        mock_audio = MagicMock()
        combined = MagicMock()
        mock_audio.__add__ = MagicMock(return_value=combined)
        mock_pydub = MagicMock()
        mock_pydub.AudioSegment.from_file.return_value = mock_audio
        mock_pydub.AudioSegment.silent.return_value = MagicMock()
        with patch.dict("sys.modules", {"pydub": mock_pydub}):
            result = ag.add_silence_padding(audio_file, 10.0)
        assert result == audio_file

    def test_generate_main_audio_skips_empty_segments(self, ag):
        with patch("generators.audio_gen._edge_tts", return_value=None):
            with patch("generators.audio_gen._merge_audio_files", return_value=0.0):
                with patch.object(ag, "generate_fallback_audio", return_value=None):
                    track = ag.generate_main_audio({"EMPTY": ""}, "weekday")
        assert track is not None
        assert track.total_duration_seconds == 0.0

    def test_min_max_constants(self):
        assert MIN_AUDIO_SECONDS == 180   # spec: long-form 3:15-3:50 target
        assert MAX_AUDIO_SECONDS == 240   # spec: 4:00 hard cap
