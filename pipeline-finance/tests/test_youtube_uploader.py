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


# ── Videos have to actually become visible ───────────────────────────────────

class TestUploadsBecomeVisible:
    """
    The manifest records that an upload call succeeded. It says nothing
    about whether the video ever became visible — which is the difference
    between "we published four videos" and "the channel is empty".
    """

    @staticmethod
    def _status(**kwargs):
        from uploader.uploader import UploadConfig
        base = dict(title="t", description="d", tags=[], privacy="private")
        base.update(kwargs)
        return UploadConfig(**base).to_youtube_body()["status"]

    def test_no_publish_time_means_private_forever(self):
        """
        This is what shipped: UploadConfig falls through to privacy
        "private" and leaves it there, with nothing to ever flip it public.
        """
        status = self._status()
        assert status["privacyStatus"] == "private"
        assert "publishAt" not in status

    def test_a_publish_time_schedules_the_video(self):
        from datetime import datetime, timedelta, timezone
        status = self._status(
            publish_at=datetime.now(timezone.utc) + timedelta(minutes=2))
        assert status["publishAt"]
        # YouTube requires private at upload when publishAt is set.
        assert status["privacyStatus"] == "private"

    def test_shorts_are_uploaded_with_a_publish_time(self):
        """
        The Shorts pipeline called upload_short without one, so every Short
        went up invisible and stayed that way.
        """
        import inspect
        from scheduler import short_pipeline
        source = inspect.getsource(short_pipeline.run_themed_short)
        assert "upload_short(" in source
        upload_call = source[source.index("upload_short("):]
        assert "publish_at=" in upload_call[:400], \
            "Shorts must be given a publish time or they stay private forever"


class TestVerifyUploads:
    def test_hidden_videos_are_counted_and_reported(self, capsys):
        from monitor import health_report
        manifest = [
            {"video_id": "pub1", "title": "public one",
             "uploaded_at": "2026-08-28T12:00:00"},
            {"video_id": "priv1", "title": "stuck private",
             "uploaded_at": "2026-08-28T18:00:00"},
        ]
        statuses = {
            "pub1": {"video_id": "pub1", "privacy": "public",
                     "processing_status": "succeeded"},
            "priv1": {"video_id": "priv1", "privacy": "private",
                      "processing_status": "succeeded"},
        }
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest), \
             patch("uploader.uploader.YouTubeUploader") as MockUploader, \
             patch("uploader.quota_tracker.QuotaTracker"):
            MockUploader.return_value.verify_upload_status.side_effect = \
                lambda vid: statuses[vid]
            hidden = health_report.verify_uploads()
        assert hidden == 1
        out = capsys.readouterr().out
        assert "NOT publicly visible" in out
        assert "priv1" in out

    def test_all_public_reports_clean(self, capsys):
        from monitor import health_report
        manifest = [{"video_id": "a", "title": "t", "uploaded_at": "2026-08-28T12:00:00"}]
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest), \
             patch("uploader.uploader.YouTubeUploader") as MockUploader, \
             patch("uploader.quota_tracker.QuotaTracker"):
            MockUploader.return_value.verify_upload_status.return_value = {
                "video_id": "a", "privacy": "public", "processing_status": "succeeded"}
            assert health_report.verify_uploads() == 0
        assert "are public" in capsys.readouterr().out

    def test_an_empty_manifest_is_not_an_error(self, capsys):
        from monitor import health_report
        with patch("uploader.uploader.load_upload_manifest", return_value=[]):
            assert health_report.verify_uploads() == 0


class TestPublishTiming:
    """
    Videos were built at the right moment and then scheduled for a different
    one. The slot times ARE the publishing schedule; scheduling a second
    time on top only moves each video away from the news it was written for.
    """

    def test_a_video_publishes_shortly_after_it_is_built(self):
        from datetime import datetime, timezone
        from scheduler.weekday_scheduler import (
            _next_publish_time, PUBLISH_DELAY_MINUTES)
        minutes = (_next_publish_time()
                   - datetime.now(timezone.utc)).total_seconds() / 60
        assert 0 < minutes <= PUBLISH_DELAY_MINUTES + 1

    def test_the_delay_leaves_room_for_processing(self):
        """Published before YouTube finishes processing, it goes live raw."""
        from scheduler.weekday_scheduler import PUBLISH_DELAY_MINUTES
        assert PUBLISH_DELAY_MINUTES >= 2

    def test_the_premarket_video_no_longer_waits_for_the_close(self):
        """
        Built at 08:00 to say what is at stake today, it was going live at
        17:00 — after the session it was previewing had ended.
        """
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        from config.settings import settings
        from scheduler.weekday_scheduler import _next_publish_time
        published = _next_publish_time().astimezone(ZoneInfo(settings.timezone))
        built = datetime.now(ZoneInfo(settings.timezone))
        assert published.date() == built.date()
        assert (published - built).total_seconds() < 3600

    def test_the_sunday_pipeline_does_not_schedule_a_week_out(self):
        """
        Its old logic aimed at 11:00 ET and pushed a week forward if it was
        already past — but the job fires AT 11:00 and takes most of an hour
        to build, so it was always past. Every Sunday video was scheduled to
        appear the following Sunday.
        """
        import inspect
        from scheduler import sunday_scheduler
        source = inspect.getsource(sunday_scheduler)
        assert "timedelta(days=7)" not in source


class TestReleasingStuckVideos:
    def test_private_videos_are_published(self, capsys):
        from monitor import health_report
        manifest = [{"video_id": "stuck", "title": "sunday deep dive",
                     "uploaded_at": "2026-08-30T15:48:00"}]
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest), \
             patch("uploader.uploader.YouTubeUploader") as MockUploader, \
             patch("uploader.quota_tracker.QuotaTracker"):
            MockUploader.return_value.verify_upload_status.return_value = {
                "video_id": "stuck", "privacy": "private"}
            MockUploader.return_value.publish_now.return_value = True
            assert health_report.release_private_uploads() == 1
            MockUploader.return_value.publish_now.assert_called_once_with("stuck")
        assert "published" in capsys.readouterr().out

    def test_public_videos_are_left_alone(self):
        from monitor import health_report
        manifest = [{"video_id": "live", "title": "t",
                     "uploaded_at": "2026-08-28T12:00:00"}]
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest), \
             patch("uploader.uploader.YouTubeUploader") as MockUploader, \
             patch("uploader.quota_tracker.QuotaTracker"):
            MockUploader.return_value.verify_upload_status.return_value = {
                "video_id": "live", "privacy": "public"}
            assert health_report.release_private_uploads() == 0
            MockUploader.return_value.publish_now.assert_not_called()

    def test_a_failed_publish_is_reported_not_counted(self, capsys):
        from monitor import health_report
        manifest = [{"video_id": "bad", "title": "t",
                     "uploaded_at": "2026-08-28T12:00:00"}]
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest), \
             patch("uploader.uploader.YouTubeUploader") as MockUploader, \
             patch("uploader.quota_tracker.QuotaTracker"):
            MockUploader.return_value.verify_upload_status.return_value = {
                "video_id": "bad", "privacy": "private"}
            MockUploader.return_value.publish_now.return_value = False
            assert health_report.release_private_uploads() == 0
        assert "failed" in capsys.readouterr().out

    def test_publish_now_clears_the_scheduled_time(self):
        """
        YouTube rejects an update that sets a video public while a future
        publishAt is still attached to it.
        """
        import inspect
        from uploader.uploader import YouTubeUploader
        source = inspect.getsource(YouTubeUploader.publish_now)
        assert '"publishAt": None' in source
        assert '"privacyStatus": "public"' in source
