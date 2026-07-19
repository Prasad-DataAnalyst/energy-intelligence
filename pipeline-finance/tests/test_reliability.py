"""
tests/test_reliability.py
Tests for the reliability layer:
  - scheduler/pipeline_state.py  (checkpoint/resume)
  - scheduler/post_upload.py     (finalize_upload, check_topic_duplicate)
  - scheduler/deadman.py         (dead-man switch, retry)
  - builders/fallback_builder.py (emergency fallback video)
  - uploader/preflight.py        (audio + video stream quality gates)
  - uploader/uploader.py         (UploadConfig.video_type field)
  - scheduler/master_scheduler.py (job registration smoke test)
"""
import json
import os
import subprocess
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ── PipelineState ─────────────────────────────────────────────────────────────

class TestPipelineState:
    def _make_state(self, tmp_path, pipeline="weekday"):
        import scheduler.pipeline_state as ps
        original = ps.STATE_DIR
        ps.STATE_DIR = tmp_path
        state = ps.PipelineState(pipeline)
        return state, ps, original

    def test_fresh_state_no_steps_done(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            assert state.is_done("scrape_market") is False
            assert state.outcome is None
        finally:
            ps.STATE_DIR = original

    def test_mark_done_persists(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            state.mark_started("scrape_market")
            state.mark_done("scrape_market", artifacts={"json": "/tmp/x.json"})
            # Reload from disk
            state2 = ps.PipelineState("weekday")
            assert state2.is_done("scrape_market") is True
            assert state2.artifact("scrape_market", "json") == "/tmp/x.json"
        finally:
            ps.STATE_DIR = original

    def test_mark_failed_records_error(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            state.mark_started("upload")
            state.mark_failed("upload", "quota exceeded")
            assert state.is_done("upload") is False
            assert any("quota" in e for e in state._data["errors"])
        finally:
            ps.STATE_DIR = original

    def test_next_step_order(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            assert state.next_step() == "scrape_market"
            state.mark_done("scrape_market")
            assert state.next_step() == "scrape_earnings"
        finally:
            ps.STATE_DIR = original

    def test_finish_success(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            state.finish(success=True, video_id="vid42")
            state2 = ps.PipelineState("weekday")
            assert state2.outcome == "success"
            assert state2.video_id == "vid42"
        finally:
            ps.STATE_DIR = original

    def test_artifact_path_missing_file(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            state.mark_done("build_video", artifacts={"video_path": "/nonexistent/v.mp4"})
            assert state.artifact_path("build_video", "video_path") is None
        finally:
            ps.STATE_DIR = original

    def test_needs_retry_logic(self, tmp_path):
        import scheduler.pipeline_state as ps
        original = ps.STATE_DIR
        ps.STATE_DIR = tmp_path
        try:
            # Never started → no retry (main slot handles it)
            assert ps.needs_retry("weekday") is False
            # Started but not finished → retry
            state = ps.PipelineState("weekday")
            state.mark_started("scrape_market")
            assert ps.needs_retry("weekday") is True
            # Finished successfully → no retry
            state.finish(success=True)
            assert ps.needs_retry("weekday") is False
        finally:
            ps.STATE_DIR = original

    def test_corrupt_state_file_recovers(self, tmp_path):
        import scheduler.pipeline_state as ps
        original = ps.STATE_DIR
        ps.STATE_DIR = tmp_path
        try:
            bad = tmp_path / f"weekday_{date.today().isoformat()}.json"
            bad.write_text("{corrupt json!!")
            state = ps.PipelineState("weekday")
            assert state.outcome is None   # started fresh, no crash
        finally:
            ps.STATE_DIR = original

    def test_summary_shape(self, tmp_path):
        state, ps, original = self._make_state(tmp_path)
        try:
            state.mark_done("scrape_market")
            s = state.summary()
            assert s["pipeline"] == "weekday"
            assert "scrape_market" in s["steps_done"]
            assert s["next_step"] == "scrape_earnings"
        finally:
            ps.STATE_DIR = original


# ── todays_upload_recorded ───────────────────────────────────────────────────

class TestTodaysUploadRecorded:
    def test_true_when_manifest_has_today(self):
        from scheduler.pipeline_state import todays_upload_recorded
        today_record = [{"video_id": "x", "uploaded_at": f"{date.today().isoformat()}T10:00:00"}]
        with patch("uploader.uploader.load_upload_manifest", return_value=today_record):
            assert todays_upload_recorded() is True

    def test_false_when_only_old_records(self):
        from scheduler.pipeline_state import todays_upload_recorded
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        old = [{"video_id": "y", "uploaded_at": f"{yesterday}T10:00:00"}]
        with patch("uploader.uploader.load_upload_manifest", return_value=old):
            assert todays_upload_recorded() is False

    def test_false_when_empty(self):
        from scheduler.pipeline_state import todays_upload_recorded
        with patch("uploader.uploader.load_upload_manifest", return_value=[]):
            assert todays_upload_recorded() is False


# ── post_upload.finalize_upload ──────────────────────────────────────────────

class TestFinalizeUpload:
    def test_all_tasks_run_and_reported(self):
        from scheduler.post_upload import finalize_upload

        mock_result = MagicMock(success=True, video_id="vid1", quota_used=1600)
        mock_config = MagicMock()
        mock_config.title = "T"
        mock_config.video_type = "weekday"
        mock_config.playlist_id = None
        mock_config.tags = []

        with patch("uploader.uploader.record_upload") as mock_rec, \
             patch("channel_manager.playlist_manager.PlaylistManager") as MockPM, \
             patch("channel_manager.subtitle_manager.SubtitleManager") as MockSM, \
             patch("channel_manager.end_screen_manager.EndScreenManager") as MockESM, \
             patch("channel_manager.post_manager.PostManager") as MockPostM, \
             patch("channel_manager.content_tracker.ContentTracker") as MockCT:
            MockPM.return_value.route_video_to_playlist.return_value = "PL123"
            MockSM.return_value.upload_captions.return_value = True
            MockESM.return_value.add_end_screen_to_recent.return_value = True
            MockPostM.return_value.pin_disclaimer_comment.return_value = "cmt1"
            MockCT.return_value.record_content.return_value = MagicMock()

            results = finalize_upload(
                video_id="vid1",
                video_type="weekday",
                title="Test Video",
                upload_result=mock_result,
                upload_config=mock_config,
                script_text="Hello market recap.",
                topic="market recap",
                video_duration_seconds=180.0,
            )

        assert results["manifest"] is True
        assert results["playlist"] == "PL123"
        assert results["captions"] is True
        assert results["end_screen"] is True
        assert results["pinned_comment"] == "cmt1"
        assert results["content_tracker"] is True
        mock_rec.assert_called_once()

    def test_one_failure_does_not_block_others(self):
        from scheduler.post_upload import finalize_upload

        with patch("uploader.uploader.record_upload"), \
             patch("channel_manager.playlist_manager.PlaylistManager",
                   side_effect=Exception("playlist API down")), \
             patch("channel_manager.subtitle_manager.SubtitleManager") as MockSM, \
             patch("channel_manager.end_screen_manager.EndScreenManager") as MockESM, \
             patch("channel_manager.post_manager.PostManager") as MockPostM, \
             patch("channel_manager.content_tracker.ContentTracker") as MockCT:
            MockSM.return_value.upload_captions.return_value = True
            MockESM.return_value.add_end_screen_to_recent.return_value = True
            MockPostM.return_value.pin_disclaimer_comment.return_value = "cmt2"

            results = finalize_upload(
                video_id="vid2",
                video_type="weekday",
                title="Test",
                upload_result=MagicMock(success=True, video_id="vid2", quota_used=1600),
                upload_config=MagicMock(title="T", video_type="weekday",
                                        playlist_id=None, tags=[]),
                script_text="text",
                video_duration_seconds=120.0,
            )

        assert results["playlist"] is False       # failed
        assert results["captions"] is True        # still ran
        assert results["pinned_comment"] == "cmt2"

    def test_no_script_skips_captions(self):
        from scheduler.post_upload import finalize_upload
        with patch("uploader.uploader.record_upload"), \
             patch("channel_manager.playlist_manager.PlaylistManager") as MockPM, \
             patch("channel_manager.end_screen_manager.EndScreenManager"), \
             patch("channel_manager.post_manager.PostManager") as MockPostM, \
             patch("channel_manager.content_tracker.ContentTracker"):
            MockPM.return_value.route_video_to_playlist.return_value = "PL1"
            MockPostM.return_value.pin_disclaimer_comment.return_value = "c"
            results = finalize_upload(
                video_id="v", video_type="weekday", title="t",
                upload_result=MagicMock(success=True, video_id="v", quota_used=1600),
                upload_config=MagicMock(title="t", video_type="weekday",
                                        playlist_id=None, tags=[]),
                script_text="",       # no script
                video_duration_seconds=0,   # no duration
            )
        assert results["captions"] is False
        assert results["end_screen"] is False

    def test_never_raises(self):
        from scheduler.post_upload import finalize_upload
        # Everything explodes — finalize_upload must still return a dict
        with patch("uploader.uploader.record_upload", side_effect=Exception("x")), \
             patch("channel_manager.playlist_manager.PlaylistManager", side_effect=Exception("x")), \
             patch("channel_manager.subtitle_manager.SubtitleManager", side_effect=Exception("x")), \
             patch("channel_manager.end_screen_manager.EndScreenManager", side_effect=Exception("x")), \
             patch("channel_manager.post_manager.PostManager", side_effect=Exception("x")), \
             patch("channel_manager.content_tracker.ContentTracker", side_effect=Exception("x")):
            results = finalize_upload(
                video_id="v", video_type="weekday", title="t",
                upload_result=MagicMock(), upload_config=MagicMock(),
                script_text="s", video_duration_seconds=100,
            )
        assert all(v is False for v in results.values())


class TestCheckTopicDuplicate:
    def test_duplicate_detected(self):
        from scheduler.post_upload import check_topic_duplicate
        with patch("channel_manager.content_tracker.ContentTracker") as MockCT:
            MockCT.return_value.is_duplicate.return_value = (True, 0.8)
            assert check_topic_duplicate("same topic") is True

    def test_unique_allowed(self):
        from scheduler.post_upload import check_topic_duplicate
        with patch("channel_manager.content_tracker.ContentTracker") as MockCT:
            MockCT.return_value.is_duplicate.return_value = (False, 0.1)
            assert check_topic_duplicate("new topic") is False

    def test_error_allows_topic(self):
        from scheduler.post_upload import check_topic_duplicate
        with patch("channel_manager.content_tracker.ContentTracker",
                   side_effect=Exception("disk error")):
            assert check_topic_duplicate("any topic") is False


# ── Dead-man switch ───────────────────────────────────────────────────────────

class TestDeadman:
    def test_ok_when_upload_recorded(self):
        from scheduler.deadman import check_todays_upload
        with patch("scheduler.pipeline_state.todays_upload_recorded", return_value=True):
            assert check_todays_upload(is_content_day=True) is True

    def test_skipped_on_non_content_day(self):
        from scheduler.deadman import check_todays_upload
        assert check_todays_upload(is_content_day=False) is True

    def test_alert_fires_when_no_upload(self):
        from scheduler import deadman
        with patch("scheduler.pipeline_state.todays_upload_recorded", return_value=False), \
             patch.object(deadman, "_send_email") as mock_email:
            result = deadman.check_todays_upload(is_content_day=True)
        assert result is False
        mock_email.assert_called()
        subject = mock_email.call_args[0][0]
        assert "NO UPLOAD" in subject

    def test_no_fallback_upload_by_default(self):
        from scheduler import deadman
        with patch("scheduler.pipeline_state.todays_upload_recorded", return_value=False), \
             patch.object(deadman, "_send_email"), \
             patch("builders.fallback_builder.build_and_upload_fallback") as mock_fb:
            os.environ.pop("FALLBACK_AUTO_UPLOAD", None)
            deadman.check_todays_upload(is_content_day=True)
        mock_fb.assert_not_called()

    def test_fallback_upload_when_enabled(self):
        from scheduler import deadman
        with patch("scheduler.pipeline_state.todays_upload_recorded", return_value=False), \
             patch.object(deadman, "_send_email"), \
             patch("builders.fallback_builder.build_and_upload_fallback",
                   return_value="vid99") as mock_fb, \
             patch.dict(os.environ, {"FALLBACK_AUTO_UPLOAD": "true"}):
            deadman.check_todays_upload(is_content_day=True)
        mock_fb.assert_called_once()

    def test_email_skipped_without_smtp_config(self):
        from scheduler.deadman import _send_email
        env_clear = {k: "" for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "ALERT_EMAIL")}
        with patch.dict(os.environ, env_clear):
            assert _send_email("subject", "body") is False

    def test_retry_if_needed_runs_pipeline(self):
        from scheduler import deadman
        with patch("scheduler.pipeline_state.needs_retry", return_value=True), \
             patch("scheduler.weekday_scheduler.WeekdayScheduler") as MockWS:
            deadman.retry_if_needed("weekday")
        MockWS.return_value.run.assert_called_once()

    def test_retry_if_needed_skips_when_done(self):
        from scheduler import deadman
        with patch("scheduler.pipeline_state.needs_retry", return_value=False), \
             patch("scheduler.weekday_scheduler.WeekdayScheduler") as MockWS:
            deadman.retry_if_needed("weekday")
        MockWS.return_value.run.assert_not_called()


# ── Fallback builder ──────────────────────────────────────────────────────────

class TestFallbackBuilder:
    def test_evergreen_script_has_compliance(self):
        from builders.fallback_builder import build_fallback_script
        with patch("builders.fallback_builder._latest_market_json", return_value=None):
            script = build_fallback_script()
        assert "Narration is AI-generated" in script
        from config.settings import settings
        assert settings.disclaimer_text in script

    def test_cached_data_script_fills_numbers(self):
        from builders.fallback_builder import build_fallback_script
        cached = {
            "sp500": {"price": 5000.5, "change_pct": 1.2},
            "nasdaq": {"change_pct": -0.8},
            "vix": {"price": 17.3},
        }
        with patch("builders.fallback_builder._latest_market_json", return_value=cached):
            script = build_fallback_script()
        assert "5000.5" in script
        assert "17.3" in script
        assert "Narration is AI-generated" in script

    def test_cached_data_script_survives_missing_keys(self):
        from builders.fallback_builder import build_fallback_script
        with patch("builders.fallback_builder._latest_market_json", return_value={"weird": 1}):
            script = build_fallback_script()
        assert "Narration is AI-generated" in script

    def test_build_aborts_without_tts(self, tmp_path):
        from builders import fallback_builder as fb
        with patch.object(fb, "FALLBACK_DIR", tmp_path), \
             patch.object(fb, "_tts_to_file", return_value=False):
            assert fb.build_fallback_video() is None

    def test_build_aborts_without_ffmpeg(self, tmp_path):
        from builders import fallback_builder as fb

        def fake_tts(text, out):
            out.write_bytes(b"\xff\xfb" + b"\x00" * 2000)
            return True

        with patch.object(fb, "FALLBACK_DIR", tmp_path), \
             patch.object(fb, "_tts_to_file", side_effect=fake_tts), \
             patch.object(fb, "_render_static_frame", return_value=True), \
             patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg")):
            assert fb.build_fallback_video() is None

    def test_render_static_frame(self, tmp_path):
        from builders.fallback_builder import _render_static_frame
        out = tmp_path / "frame.png"
        assert _render_static_frame(out, "Test Headline") is True
        assert out.exists() and out.stat().st_size > 1000

    def test_upload_respects_quota_gate(self, tmp_path):
        from builders import fallback_builder as fb
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 5000)
        with patch.object(fb, "build_fallback_video", return_value=video), \
             patch("uploader.quota_tracker.QuotaTracker") as MockQT:
            MockQT.return_value.can_upload.return_value = False
            assert fb.build_and_upload_fallback() is None


# ── Preflight quality gate extensions ─────────────────────────────────────────

class TestPreflightQualityGate:
    def test_audio_missing_file_is_error(self, tmp_path):
        from uploader.preflight import PreflightChecker, PreflightResult
        result = PreflightResult(passed=True)
        PreflightChecker(quota_tracker=MagicMock()).check_audio(
            tmp_path / "missing.mp3", result
        )
        assert result.passed is False

    def test_audio_none_is_warning_only(self):
        from uploader.preflight import PreflightChecker, PreflightResult
        result = PreflightResult(passed=True)
        PreflightChecker(quota_tracker=MagicMock()).check_audio(None, result)
        assert result.passed is True
        assert result.warnings

    def test_video_stream_check_no_ffprobe(self, tmp_path):
        from uploader.preflight import PreflightChecker, PreflightResult
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 5000)
        result = PreflightResult(passed=True)
        with patch("subprocess.run", side_effect=FileNotFoundError("ffprobe")):
            PreflightChecker(quota_tracker=MagicMock())._check_video_streams(video, result)
        assert result.passed is True   # warning only
        assert any("ffprobe" in w for w in result.warnings)

    def test_video_stream_check_missing_audio_stream(self, tmp_path):
        from uploader.preflight import PreflightChecker, PreflightResult
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 5000)
        proc = MagicMock(returncode=0, stdout=json.dumps({
            "streams": [{"codec_type": "video"}]
        }))
        result = PreflightResult(passed=True)
        with patch("subprocess.run", return_value=proc):
            PreflightChecker(quota_tracker=MagicMock())._check_video_streams(video, result)
        assert result.passed is False
        assert any("audio stream" in e.lower() for e in result.errors)

    def test_video_stream_check_both_streams_ok(self, tmp_path):
        from uploader.preflight import PreflightChecker, PreflightResult
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00" * 5000)
        proc = MagicMock(returncode=0, stdout=json.dumps({
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}]
        }))
        result = PreflightResult(passed=True)
        with patch("subprocess.run", return_value=proc):
            PreflightChecker(quota_tracker=MagicMock())._check_video_streams(video, result)
        assert result.passed is True


# ── UploadConfig.video_type ───────────────────────────────────────────────────

class TestUploadConfigVideoType:
    def test_default_video_type(self):
        from uploader.uploader import UploadConfig
        cfg = UploadConfig(title="t", description="d", tags=[])
        assert cfg.video_type == "weekday"

    def test_video_type_settable(self):
        from uploader.uploader import UploadConfig
        cfg = UploadConfig(title="t", description="d", tags=[], video_type="sunday")
        assert cfg.video_type == "sunday"

    def test_record_upload_uses_real_field(self, tmp_path):
        """record_upload must work with a real UploadConfig (not just mocks)."""
        from uploader import uploader as umod
        from uploader.uploader import UploadConfig
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "m.jsonl"
        try:
            cfg = UploadConfig(title="Real", description="d", tags=["a"], video_type="shorts")
            result = MagicMock(success=True, video_id="rv1", quota_used=1600)
            umod.record_upload(result, cfg)
            record = json.loads(umod._UPLOAD_MANIFEST_PATH.read_text().strip())
            assert record["video_type"] == "shorts"
        finally:
            umod._UPLOAD_MANIFEST_PATH = original


# ── Master scheduler job registration ─────────────────────────────────────────

class TestMasterSchedulerJobs:
    def test_all_expected_jobs_registered(self):
        """Smoke test: start_scheduler registers every expected job id."""
        from scheduler import master_scheduler as ms

        registered: list[str] = []

        class FakeScheduler:
            def __init__(self, timezone=None):
                pass
            def add_job(self, fn, trigger, id=None, name=None, **kw):
                registered.append(id)
            def get_jobs(self):
                return []
            def start(self):
                raise KeyboardInterrupt   # exit immediately
            def shutdown(self):
                pass

        # Inject fake apscheduler modules so the test runs without the package
        import sys
        import types
        fake_blocking = types.ModuleType("apscheduler.schedulers.blocking")
        fake_blocking.BlockingScheduler = FakeScheduler
        fake_cron = types.ModuleType("apscheduler.triggers.cron")
        fake_cron.CronTrigger = MagicMock()
        fake_root = types.ModuleType("apscheduler")
        fake_schedulers = types.ModuleType("apscheduler.schedulers")
        fake_triggers = types.ModuleType("apscheduler.triggers")
        modules = {
            "apscheduler": fake_root,
            "apscheduler.schedulers": fake_schedulers,
            "apscheduler.schedulers.blocking": fake_blocking,
            "apscheduler.triggers": fake_triggers,
            "apscheduler.triggers.cron": fake_cron,
        }
        with patch.dict(sys.modules, modules), \
             patch.object(ms, "_setup_logging"):
            ms.start_scheduler()

        expected = {
            "weekday_premarket", "weekday_postmarket", "sunday_educational",
            "monitor_check", "heartbeat",
            "pipeline_retry_weekday", "pipeline_retry_sunday",
            "deadman_check", "comment_check", "analytics_pull",
            "community_post", "description_refresh",
        }
        assert expected.issubset(set(registered)), f"Missing: {expected - set(registered)}"


class TestMarketstackBackup:
    def test_skipped_without_key(self):
        from builders import fallback_builder as fb
        with patch.object(fb.settings, "marketstack_api_key", "", create=True):
            assert fb._marketstack_snapshot() is None

    def test_snapshot_shape(self):
        from builders import fallback_builder as fb
        fake = MagicMock()
        fake.raise_for_status.return_value = None
        fake.json.return_value = {"data": [{"close": 743.5, "open": 750.0}]}
        with patch.object(fb.settings, "marketstack_api_key", "k", create=True), \
             patch("requests.get", return_value=fake):
            snap = fb._marketstack_snapshot()
        assert snap and "sp500" in snap
        assert snap["sp500"]["price"] == 743.5

    def test_api_error_is_non_fatal(self):
        from builders import fallback_builder as fb
        with patch.object(fb.settings, "marketstack_api_key", "k", create=True), \
             patch("requests.get", side_effect=Exception("down")):
            assert fb._marketstack_snapshot() is None

    def test_fallback_script_uses_backup_when_no_cache(self):
        from builders import fallback_builder as fb
        backup = {"sp500": {"price": 743.5, "change_pct": -0.87},
                  "nasdaq": {"change_pct": -1.2}, "vix": {"price": 19.0}}
        with patch.object(fb, "_latest_market_json", return_value=None), \
             patch.object(fb, "_marketstack_snapshot", return_value=backup):
            script = fb.build_fallback_script()
        assert "743.5" in script
        assert "Narration is AI-generated" in script
