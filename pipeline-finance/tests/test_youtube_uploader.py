"""Tests for YouTubeUploader class and QuotaTracker new methods (Modules 13 and 14)."""
import json
import sys
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from uploader.uploader import YouTubeUploader, UPLOAD_GAP_MINUTES, MIN_QUOTA_TO_UPLOAD
from uploader.quota_tracker import QuotaTracker


class TestYouTubeUploader:

    @pytest.fixture
    def uploader(self):
        qt = MagicMock()
        qt.can_upload.return_value = True
        return YouTubeUploader(quota_tracker=qt)

    def test_init_with_quota_tracker(self, uploader):
        assert uploader._quota is not None

    def test_check_quota_false_when_quota_low(self):
        qt = MagicMock()
        qt.can_upload.return_value = False
        u = YouTubeUploader(qt)
        assert u._check_quota() is False

    def test_check_quota_true_when_no_tracker(self):
        u = YouTubeUploader(quota_tracker=None)
        assert u._check_quota() is True

    def test_upload_main_video_returns_error_on_low_quota(self, tmp_path):
        qt = MagicMock()
        qt.can_upload.return_value = False
        u = YouTubeUploader(qt)
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        result = u.upload_main_video(video, "Title", "Desc", ["tag"])
        assert result.success is False
        assert "Quota" in result.error

    def test_upload_short_auto_appends_shorts_tag(self, uploader, tmp_path):
        video = tmp_path / "short.mp4"
        video.write_bytes(b"fake")
        captured = {}
        def fake_upload_full(video_path, config, thumbnail_path=None, quota_tracker=None):
            captured["title"] = config.title
            return MagicMock(success=True, video_id="abc123")
        with patch("uploader.uploader.upload_full", side_effect=fake_upload_full):
            uploader._last_upload_at = None  # no gap enforcement
            uploader.upload_short(video, "Market Drop", "Desc", ["tag"])
        assert "#Shorts" in captured["title"]

    def test_upload_short_no_double_shorts_tag(self, uploader, tmp_path):
        video = tmp_path / "short.mp4"
        video.write_bytes(b"fake")
        captured = {}
        def fake_upload_full(video_path, config, thumbnail_path=None, quota_tracker=None):
            captured["title"] = config.title
            return MagicMock(success=True, video_id="abc123")
        with patch("uploader.uploader.upload_full", side_effect=fake_upload_full):
            uploader._last_upload_at = None
            uploader.upload_short(video, "Market Drop #Shorts", "Desc", [])
        assert captured["title"].count("#Shorts") == 1

    def test_set_thumbnail_delegates_to_set_thumbnail(self, uploader, tmp_path):
        thumb = tmp_path / "thumb.jpg"
        thumb.write_bytes(b"fake jpg")
        with patch("uploader.uploader.set_thumbnail", return_value=True) as mock_st:
            result = uploader.set_thumbnail("vid123", thumb)
        mock_st.assert_called_once_with("vid123", thumb)

    def test_process_failed_queue_empty_when_no_file(self, uploader, tmp_path):
        with patch("uploader.uploader.FAILED_QUEUE_PATH", tmp_path / "nonexistent.json"):
            results = uploader.process_failed_queue()
        assert results == []

    def test_queue_failed_writes_to_json(self, uploader, tmp_path):
        from uploader.uploader import UploadConfig
        config = UploadConfig(title="Test", description="Desc", tags=["tag"])
        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")
        queue_path = tmp_path / "queue.json"
        with patch("uploader.uploader.FAILED_QUEUE_PATH", queue_path):
            uploader._queue_failed(video, config, None)
        assert queue_path.exists()
        data = json.loads(queue_path.read_text())
        assert len(data) == 1
        assert data[0]["title"] == "Test"

    def test_upload_gap_constants(self):
        assert UPLOAD_GAP_MINUTES == 30
        assert MIN_QUOTA_TO_UPLOAD == 1700


class TestQuotaTrackerNewMethods:

    @pytest.fixture
    def qt(self, tmp_path):
        log = tmp_path / "quota.json"
        return QuotaTracker(log_path=log)

    def test_load_state_returns_daily_state(self, qt):
        state = qt.load_state()
        assert hasattr(state, "date")
        assert hasattr(state, "remaining")

    def test_save_state_persists_to_disk(self, qt):
        qt._state.total_used = 500
        qt.save_state()
        reloaded = QuotaTracker(log_path=qt.log_path)
        assert reloaded.status().total_used == 500

    def test_reset_daily_clears_counters(self, qt):
        qt.record_upload("vid1", "Test Video")
        qt.reset_daily()
        assert qt.status().total_used == 0
        assert qt.status().uploads_today == 0

    def test_log_usage_records_custom_operation(self, qt):
        qt.log_usage("thumbnail.set", cost=50, note="test thumbnail")
        assert qt.status().total_used == 50

    def test_log_usage_uses_default_cost(self, qt):
        qt.log_usage("videos.list")
        assert qt.status().total_used == 1  # default cost for videos.list

    def test_get_remaining_reflects_usage(self, qt):
        before = qt.get_remaining()
        qt.log_usage("video.insert", cost=1600)
        after = qt.get_remaining()
        assert after == before - 1600

    def test_get_daily_summary_has_required_keys(self, qt):
        summary = qt.get_daily_summary()
        for key in ("date", "total_used", "remaining", "limit", "utilization_pct",
                    "uploads_today", "can_upload"):
            assert key in summary

    def test_alert_low_quota_returns_true_when_low(self, qt):
        qt._state.remaining = 500
        result = qt.alert_low_quota(threshold=2000)
        assert result is True

    def test_alert_low_quota_returns_false_when_sufficient(self, qt):
        qt._state.remaining = 8000
        result = qt.alert_low_quota(threshold=2000)
        assert result is False
