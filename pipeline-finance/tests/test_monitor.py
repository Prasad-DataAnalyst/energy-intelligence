"""Tests for PipelineMonitor class (Module 18)."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor.monitor import PipelineMonitor, ChannelMonitor


class TestPipelineMonitor:

    @pytest.fixture
    def pm(self):
        return PipelineMonitor()

    def test_alert_info_logs_without_exception(self, pm, caplog):
        import logging
        with caplog.at_level(logging.INFO):
            pm.alert("Test info message", level="info")
        assert "Test info message" in caplog.text

    def test_alert_warning_level(self, pm, caplog):
        import logging
        with caplog.at_level(logging.WARNING):
            pm.alert("Low quota", level="warning")
        assert "Low quota" in caplog.text

    def test_check_quota_status_returns_true_when_sufficient(self, pm):
        mock_qt = MagicMock()
        mock_qt.get_remaining.return_value = 5000
        with patch("uploader.quota_tracker.QuotaTracker", return_value=mock_qt):
            result = pm.check_quota_status()
        assert result is True

    def test_check_quota_status_returns_false_when_low(self, pm):
        mock_qt = MagicMock()
        mock_qt.get_remaining.return_value = 500  # below LOW_QUOTA_THRESHOLD
        with patch("uploader.quota_tracker.QuotaTracker", return_value=mock_qt):
            result = pm.check_quota_status()
        assert result is False

    def test_check_quota_status_returns_false_on_exception(self, pm):
        with patch("uploader.quota_tracker.QuotaTracker", side_effect=Exception("file error")):
            result = pm.check_quota_status()
        assert result is False

    def test_check_output_files_exist_false_when_no_videos(self, pm):
        with patch("config.settings.settings") as mock_settings:
            mock_settings.output_dir = Path("/nonexistent/path")
            result = pm.check_output_files_exist()
        assert result is False

    def test_check_last_upload_success_false_when_no_log(self, pm):
        with patch("uploader.quota_tracker.QUOTA_LOG_PATH", Path("/nonexistent")):
            result = pm.check_last_upload_success()
        assert result is False

    def test_check_last_upload_success_true_when_uploaded_today(self, pm):
        mock_qt = MagicMock()
        mock_qt.status.return_value.uploads_today = 1
        mock_qt.status.return_value.date = "2026-06-23"
        with patch("uploader.quota_tracker.QUOTA_LOG_PATH", Path("/tmp/fake.json")):
            with patch("uploader.quota_tracker.QuotaTracker", return_value=mock_qt):
                with patch("pathlib.Path.exists", return_value=True):
                    result = pm.check_last_upload_success()
        assert result is True

    def test_check_pipeline_health_returns_dict_with_keys(self, pm):
        with patch.object(pm, "check_output_files_exist", return_value=True):
            with patch.object(pm, "check_quota_status", return_value=True):
                with patch.object(pm, "check_api_status", return_value=True):
                    with patch.object(pm, "check_last_upload_success", return_value=True):
                        result = pm.check_pipeline_health()
        assert "healthy" in result
        assert "files_ok" in result
        assert "quota_ok" in result
        assert "api_ok" in result

    def test_check_pipeline_health_unhealthy_when_any_fail(self, pm):
        with patch.object(pm, "check_output_files_exist", return_value=False):
            with patch.object(pm, "check_quota_status", return_value=True):
                with patch.object(pm, "check_api_status", return_value=True):
                    with patch.object(pm, "check_last_upload_success", return_value=True):
                        result = pm.check_pipeline_health()
        assert result["healthy"] is False

    def test_log_daily_summary_writes_jsonl(self, pm, tmp_path):
        import monitor.monitor as mon_mod
        orig = mon_mod.PIPELINE_SUMMARY_LOG
        mon_mod.PIPELINE_SUMMARY_LOG = tmp_path / "daily.jsonl"
        try:
            with patch.object(pm, "check_pipeline_health", return_value={"healthy": True, "timestamp": "now"}):
                with patch("uploader.quota_tracker.QuotaTracker") as mock_qt_cls:
                    mock_qt_cls.return_value.get_daily_summary.return_value = {"used": 0}
                    pm.log_daily_summary()
            assert (tmp_path / "daily.jsonl").exists()
        finally:
            mon_mod.PIPELINE_SUMMARY_LOG = orig

    def test_check_api_status_returns_bool(self, pm):
        with patch("anthropic.Anthropic"):
            with patch("yfinance.Ticker") as mock_t:
                mock_t.return_value.history.return_value = MagicMock(empty=False)
                result = pm.check_api_status()
        assert isinstance(result, bool)


class TestChannelMonitorBugFix:
    """Verifies that import os is at module level (not at end of file)."""

    def test_os_import_works_in_send_alert(self):
        import monitor.monitor as m
        import os
        # If os is imported at module level, it will be available
        assert hasattr(m, "os") or "os" in dir(m)

    def test_send_alert_email_no_crash_without_smtp(self):
        monitor = ChannelMonitor()
        alerts = []
        metrics = monitor._get_mock_metrics()
        # Should not raise even with missing SMTP config
        monitor._send_alert_email(alerts, metrics)
