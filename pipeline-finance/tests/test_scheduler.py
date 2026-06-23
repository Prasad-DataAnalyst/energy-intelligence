"""Tests for WeekdayScheduler and SundayScheduler (Modules 15 and 16)."""
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from scheduler.weekday_scheduler import WeekdayScheduler
from scheduler.sunday_scheduler import SundayScheduler, _THEME_CYCLE


class TestWeekdayScheduler:

    @pytest.fixture
    def ws(self):
        return WeekdayScheduler()

    def test_init_sets_run_id(self, ws):
        assert len(ws.run_id) == 15  # YYYYMMDD_HHMMSS

    def test_is_market_day_monday(self, ws):
        monday = datetime(2026, 6, 22, 10, 0)  # Monday
        assert ws.is_market_day(monday) is True

    def test_is_market_day_saturday(self, ws):
        saturday = datetime(2026, 6, 20, 10, 0)  # Saturday
        assert ws.is_market_day(saturday) is False

    def test_is_market_day_sunday(self, ws):
        sunday = datetime(2026, 6, 21, 10, 0)  # Sunday
        assert ws.is_market_day(sunday) is False

    def test_is_market_day_holiday(self, ws):
        # Christmas 2026
        christmas = datetime(2026, 12, 25, 10, 0)
        assert ws.is_market_day(christmas) is False

    def test_is_market_day_regular_friday(self, ws):
        friday = datetime(2026, 6, 19, 10, 0)  # Friday
        assert ws.is_market_day(friday) is True

    def test_upload_morning_handles_exception_gracefully(self, ws):
        with patch("uploader.quota_tracker.QuotaTracker", side_effect=Exception("no DB")):
            ws.upload_morning()  # must not raise
        assert any("upload_morning" in e for e in ws.errors)

    def test_upload_midday_skips_on_low_quota(self, ws):
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = False
        with patch("uploader.quota_tracker.QuotaTracker", return_value=mock_qt):
            ws.upload_midday()
        assert not any("upload_midday" in e for e in ws.errors)

    def test_upload_afternoon_skips_on_low_quota(self, ws):
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = False
        with patch("uploader.quota_tracker.QuotaTracker", return_value=mock_qt):
            ws.upload_afternoon()
        assert not any("upload_afternoon" in e for e in ws.errors)

    def test_run_morning_pipeline_calls_run(self, ws):
        with patch.object(ws, "run", return_value=MagicMock()) as mock_run:
            ws.run_morning_pipeline()
        mock_run.assert_called_once()


class TestSundayScheduler:

    @pytest.fixture
    def ss(self):
        return SundayScheduler()

    def test_init_sets_run_id(self, ss):
        assert len(ss.run_id) == 15

    def test_get_week_in_cycle_returns_0_to_3(self, ss):
        idx = ss.get_week_in_cycle()
        assert 0 <= idx <= 3

    def test_get_sunday_theme_returns_valid_theme(self, ss):
        theme = ss.get_sunday_theme()
        assert theme in _THEME_CYCLE

    def test_theme_cycle_has_4_entries(self):
        assert len(_THEME_CYCLE) == 4
        assert "investment_banking" in _THEME_CYCLE
        assert "rotating_bonus" in _THEME_CYCLE

    def test_get_sunday_topic_returns_dict_with_title(self, ss):
        mock_library = {
            "topics": [{"title": "How Bonds Work", "theme": "savings_wealth"}],
            "rotation_schedule": {"weights": {"medium": 1}, "cooldown_weeks": 8},
            "last_used": {},
        }
        with patch("scheduler.sunday_scheduler._load_topic_library", return_value=mock_library):
            topic = ss.get_sunday_topic("savings_wealth")
        assert "title" in topic

    def test_get_sunday_topic_fallback_on_library_failure(self, ss):
        with patch("scheduler.sunday_scheduler._load_topic_library", side_effect=Exception("no file")):
            topic = ss.get_sunday_topic("investment_banking")
        assert "title" in topic

    def test_upload_morning_sunday_handles_exception(self, ss):
        with patch("uploader.quota_tracker.QuotaTracker", side_effect=Exception("quota error")):
            ss.upload_morning_sunday()
        assert any("upload_morning_sunday" in e for e in ss.errors)

    def test_upload_afternoon_sunday_skips_on_low_quota(self, ss):
        mock_qt = MagicMock()
        mock_qt.can_upload.return_value = False
        with patch("uploader.quota_tracker.QuotaTracker", return_value=mock_qt):
            ss.upload_afternoon_sunday()
        assert not any("upload_afternoon_sunday" in e for e in ss.errors)

    def test_run_sunday_pipeline_delegates_to_run(self, ss):
        with patch.object(ss, "run", return_value=MagicMock()) as mock_run:
            ss.run_sunday_pipeline()
        mock_run.assert_called_once()
