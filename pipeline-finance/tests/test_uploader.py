"""Tests for uploader and quota tracker modules."""
import json
import pytest
from pathlib import Path
from datetime import date
from unittest.mock import patch, MagicMock


class TestQuotaTracker:
    def test_fresh_state_has_full_quota(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker, DAILY_QUOTA_LIMIT
        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        status = tracker.status()
        assert status.total_used == 0
        assert status.remaining == DAILY_QUOTA_LIMIT
        assert status.uploads_today == 0

    def test_can_upload_returns_true_when_quota_available(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker
        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        assert tracker.can_upload() is True

    def test_record_upload_deducts_quota(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker, QUOTA_COSTS
        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        tracker.record_upload("vid123", "Test Video")
        status = tracker.status()
        assert status.total_used == QUOTA_COSTS["video.insert"]
        assert status.uploads_today == 1
        assert status.remaining == 10_000 - QUOTA_COSTS["video.insert"]

    def test_quota_persists_to_file(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker
        log = tmp_path / "quota.json"
        tracker = QuotaTracker(log_path=log)
        tracker.record_upload("vid_abc", "Persisted Video")

        # New instance should load from file
        tracker2 = QuotaTracker(log_path=log)
        assert tracker2.status().uploads_today == 1
        assert tracker2.status().total_used > 0

    def test_can_upload_false_when_upload_limit_reached(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker
        from config.settings import settings

        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        # Force uploads_today to the limit
        tracker._state.uploads_today = settings.daily_upload_quota
        assert tracker.can_upload() is False

    def test_can_upload_false_when_quota_exhausted(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker, DAILY_QUOTA_LIMIT
        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        tracker._state.total_used = DAILY_QUOTA_LIMIT - 100  # not enough for an upload
        tracker._state.remaining = 100
        assert tracker.can_upload() is False

    def test_report_format(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker
        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        report = tracker.report()
        assert "YouTube API Quota" in report
        assert "Used:" in report
        assert "Remaining:" in report
        assert "Can Upload:" in report

    def test_operation_cost_recorded(self, tmp_path):
        from uploader.quota_tracker import QuotaTracker, QUOTA_COSTS
        tracker = QuotaTracker(log_path=tmp_path / "quota.json")
        tracker.record_operation("thumbnail.set", "vid123")
        assert tracker.status().total_used == QUOTA_COSTS["thumbnail.set"]


class TestUploadConfig:
    def test_to_youtube_body_basic(self):
        from uploader.uploader import UploadConfig
        config = UploadConfig(
            title="Test Video Title",
            description="Test description here.",
            tags=["test", "finance"],
            category="Finance",
            privacy="public",
        )
        body = config.to_youtube_body()
        assert body["snippet"]["title"] == "Test Video Title"
        assert "2" in body["snippet"]["categoryId"]  # Finance category ID
        assert body["status"]["privacyStatus"] == "public"

    def test_to_youtube_body_with_schedule(self):
        from uploader.uploader import UploadConfig
        from datetime import datetime, timezone
        publish_time = datetime(2026, 6, 24, 17, 0, 0, tzinfo=timezone.utc)
        config = UploadConfig(
            title="Scheduled Video",
            description="Scheduled post",
            tags=["investing"],
            publish_at=publish_time,
        )
        body = config.to_youtube_body()
        assert body["status"]["privacyStatus"] == "private"
        assert "publishAt" in body["status"]
        assert "2026-06-24" in body["status"]["publishAt"]

    def test_title_truncated_at_100_chars(self):
        from uploader.uploader import UploadConfig
        long_title = "A" * 150
        config = UploadConfig(title=long_title, description="desc", tags=[])
        body = config.to_youtube_body()
        assert len(body["snippet"]["title"]) <= 100

    def test_description_truncated_at_5000_chars(self):
        from uploader.uploader import UploadConfig
        long_desc = "B" * 6000
        config = UploadConfig(title="title", description=long_desc, tags=[])
        body = config.to_youtube_body()
        assert len(body["snippet"]["description"]) <= 5000

    def test_upload_result_youtube_url(self):
        from uploader.uploader import UploadResult
        result = UploadResult(
            success=True, video_id="dQw4w9WgXcQ", video_url=None,
            title="Test", upload_time_seconds=30.0, file_size_mb=100.0,
        )
        assert result.youtube_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_upload_result_no_url_when_failed(self):
        from uploader.uploader import UploadResult
        result = UploadResult(
            success=False, video_id=None, video_url=None,
            title="Failed", upload_time_seconds=0, file_size_mb=0,
            error="Auth failed",
        )
        assert result.youtube_url is None

    def test_upload_video_file_not_found(self, tmp_path):
        from uploader.uploader import upload_video, UploadConfig
        config = UploadConfig(title="Test", description="desc", tags=[])
        result = upload_video(tmp_path / "nonexistent.mp4", config)
        assert result.success is False
        assert "not found" in result.error.lower()
