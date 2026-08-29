"""
tests/test_performance_report.py — is anyone watching?

Analytics were pulled daily and never aggregated. Every other health check
answers "is the machine running"; these are the only ones that answer "is
it working", and the format breakdown is what the publishing mix turns on.
"""
import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def logs(tmp_path):
    from config.settings import settings
    settings.logs_dir = tmp_path
    (tmp_path / "analytics").mkdir()
    return tmp_path


def _weekly(logs, **overrides):
    report = {"week_of": "2026-W35", "total_views": 6420, "avg_ctr": 0.055,
              "total_watch_time_minutes": 1830.0, "video_count": 17,
              "subscribers_gained": 34, "top_video_id": "sun1",
              "top_video_views": 410, "low_ctr_videos": []}
    report.update(overrides)
    (logs / "analytics" / "weekly_report_2026-W35.json").write_text(
        json.dumps(report))
    return report


def _stat(vid, views=100, dur=40.0, ctr=0.03):
    return {"video_id": vid, "views": views, "impressions": views * 20,
            "ctr": ctr, "watch_time_minutes": views * dur / 60,
            "avg_view_duration_seconds": dur, "subscribers_gained": 0,
            "likes": 0, "comments": 0}


def _daily(logs, stats, when=None):
    when = (when or date.today()).isoformat()
    (logs / "analytics" / f"{when}.json").write_text(json.dumps(stats))


class TestWeeklyPerformance:
    def test_a_healthy_week_reads_ok(self, logs):
        from monitor import health_report
        _weekly(logs)
        status, _, detail, _ = health_report._check_performance()
        assert status == health_report._OK
        assert "6,420 views" in detail

    def test_no_report_yet_warns_rather_than_failing(self, logs):
        from monitor import health_report
        status, _, detail, _ = health_report._check_performance()
        assert status == health_report._WARN
        assert "no weekly report yet" in detail

    def test_zero_views_says_so_plainly(self, logs):
        """A channel nobody watches is not broken, it is new — say which."""
        from monitor import health_report
        _weekly(logs, total_views=0, avg_ctr=0.0)
        status, _, detail, _ = health_report._check_performance()
        assert status == health_report._WARN
        assert "nothing is being watched yet" in detail

    def test_weak_ctr_names_the_lever(self, logs):
        from monitor import health_report
        _weekly(logs, avg_ctr=0.018)
        status, _, detail, _ = health_report._check_performance()
        assert status == health_report._WARN
        assert "titles and thumbnails" in detail

    def test_low_ctr_videos_are_surfaced_as_swap_candidates(self, logs):
        from monitor import health_report
        _weekly(logs, low_ctr_videos=["a", "b", "c"])
        _, _, _, lines = health_report._check_performance()
        assert any("title swap" in line for line in lines)

    def test_derived_per_video_numbers_are_shown(self, logs):
        from monitor import health_report
        _weekly(logs)
        _, _, _, lines = health_report._check_performance()
        assert any("views per video" in line for line in lines)
        assert any("average view duration" in line for line in lines)

    def test_a_corrupt_report_is_survivable(self, logs):
        from monitor import health_report
        (logs / "analytics" / "weekly_report_2026-W35.json").write_text("{ bad")
        assert health_report._check_performance()[0] == health_report._WARN


class TestFormatPerformance:
    """
    Recaps expire in a day and compete with everyone; explainers accumulate
    search traffic for years. Whether to keep two recap slots is a real
    decision and it should be made on these numbers.
    """

    @staticmethod
    def _manifest(pairs):
        return [{"video_id": vid, "video_type": kind, "title": vid,
                 "uploaded_at": date.today().isoformat() + "T10:00:00"}
                for vid, kind in pairs]

    def test_formats_are_compared_side_by_side(self, logs):
        from monitor import health_report
        manifest = self._manifest(
            [("r1", "weekday"), ("r2", "weekday"), ("s1", "sunday")])
        _daily(logs, [_stat("r1", 100, 40), _stat("r2", 140, 44),
                      _stat("s1", 340, 165)])
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            status, _, _, lines = health_report._check_format_performance()
        assert status == health_report._OK
        assert any("weekday" in line for line in lines)
        assert any("sunday" in line for line in lines)

    def test_the_strongest_format_is_listed_first(self, logs):
        from monitor import health_report
        manifest = self._manifest([("r1", "weekday"), ("s1", "sunday")])
        _daily(logs, [_stat("r1", 100), _stat("s1", 900)])
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            _, _, _, lines = health_report._check_format_performance()
        assert "sunday" in lines[0]

    def test_the_newest_observation_of_a_video_wins(self, logs):
        """
        The daily pull writes one file per day and a video appears in many.
        Summing them would multiply a single video's views by its age.
        """
        from monitor import health_report
        manifest = self._manifest([("r1", "weekday")])
        _daily(logs, [_stat("r1", 100)], when=date.today() - timedelta(days=3))
        _daily(logs, [_stat("r1", 500)], when=date.today())
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            _, _, _, lines = health_report._check_format_performance()
        assert "500" in lines[0] and "1 videos" in lines[0]

    def test_weekly_reports_are_not_read_as_daily_stats(self, logs):
        from monitor import health_report
        _weekly(logs)
        manifest = self._manifest([("r1", "weekday")])
        _daily(logs, [_stat("r1", 100)])
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            status, _, detail, _ = health_report._check_format_performance()
        assert status == health_report._OK
        assert "1 videos" in detail or "1 video" in detail

    def test_stats_outside_the_window_are_ignored(self, logs):
        from monitor import health_report
        manifest = self._manifest([("r1", "weekday")])
        _daily(logs, [_stat("r1")], when=date.today() - timedelta(days=200))
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            status, _, detail, _ = health_report._check_format_performance()
        assert status == health_report._WARN
        assert "no analytics" in detail

    def test_no_uploads_is_not_an_error(self, logs):
        from monitor import health_report
        with patch("uploader.uploader.load_upload_manifest", return_value=[]):
            assert health_report._check_format_performance()[0] == health_report._OK

    def test_a_video_missing_from_the_manifest_is_still_counted(self, logs):
        """Analytics can outlive a manifest entry; do not silently drop it."""
        from monitor import health_report
        _daily(logs, [_stat("ghost", 200)])
        with patch("uploader.uploader.load_upload_manifest",
                   return_value=self._manifest([("r1", "weekday")])):
            _, _, _, lines = health_report._check_format_performance()
        assert any("unknown" in line for line in lines)

    def test_a_corrupt_daily_file_does_not_hide_the_rest(self, logs):
        from monitor import health_report
        (logs / "analytics" / "2026-01-01.json").write_text("{ bad")
        _daily(logs, [_stat("r1", 300)])
        with patch("uploader.uploader.load_upload_manifest",
                   return_value=self._manifest([("r1", "weekday")])):
            status, _, _, lines = health_report._check_format_performance()
        assert status == health_report._OK and lines


class TestWeeklyJobIsScheduled:
    def test_the_report_runs_on_a_schedule(self):
        """
        run_weekly_report has always been documented as "called every Monday
        by the scheduler" and never was.
        """
        import inspect
        from scheduler import master_scheduler
        source = inspect.getsource(master_scheduler.start_scheduler)
        assert 'id="weekly_analytics"' in source
        assert "run_weekly_analytics" in source

    def test_the_job_survives_a_failing_api(self):
        from scheduler.master_scheduler import run_weekly_analytics
        with patch("channel_manager.analytics_tracker.AnalyticsTracker") as mock:
            mock.return_value.run_weekly_report.side_effect = RuntimeError("403")
            run_weekly_analytics()      # must not raise


# ── Slots that fired but published nothing ───────────────────────────────────

class TestTodaysSlots:
    """
    The Shorts pipeline keeps no checkpoint, so a Short that starts and dies
    leaves no trace anywhere — not in the pipeline runs, not in the manifest.
    Nothing else in the report would notice it never published.
    """

    from datetime import datetime as _datetime
    TZ = None

    @staticmethod
    def _at(hour, minute=0, weekday_date=(2026, 8, 29)):
        from zoneinfo import ZoneInfo
        from config.settings import settings
        return TestTodaysSlots._datetime(
            *weekday_date, hour, minute, tzinfo=ZoneInfo(settings.timezone))

    @staticmethod
    def _run(now, manifest):
        from datetime import datetime
        from monitor import health_report

        class FakeDT(datetime):
            @classmethod
            def now(cls, tz=None):
                return now

        with patch.object(health_report, "datetime", FakeDT), \
             patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            return health_report._check_todays_slots()

    def test_a_slot_that_published_nothing_fails(self):
        """Saturday 11:00 Short fired, no Short in the manifest."""
        from monitor import health_report
        status, _, detail, lines = self._run(
            self._at(13), [{"video_id": "x", "video_type": "weekday",
                            "uploaded_at": "2026-08-28T18:26:00"}])
        assert status == health_report._FAIL
        assert "produced nothing" in detail
        assert any("Saturday Short" in line for line in lines)

    def test_a_slot_that_published_passes(self):
        from monitor import health_report
        status, _, _, _ = self._run(
            self._at(13), [{"video_id": "x", "video_type": "shorts",
                            "uploaded_at": "2026-08-29T11:12:00"}])
        assert status == health_report._OK

    def test_a_recent_slot_is_still_inside_the_build_window(self):
        """A long-form build takes minutes; do not cry wolf mid-render."""
        from monitor import health_report
        status, _, detail, _ = self._run(self._at(11, 30), [])
        assert status == health_report._OK
        assert "none due yet" in detail

    def test_yesterdays_upload_does_not_count_for_today(self):
        from monitor import health_report
        status, _, _, _ = self._run(
            self._at(13), [{"video_id": "x", "video_type": "shorts",
                            "uploaded_at": "2026-08-28T11:12:00"}])
        assert status == health_report._FAIL

    def test_before_any_slot_fires_nothing_is_due(self):
        from monitor import health_report
        status, _, detail, _ = self._run(self._at(6), [])
        assert status == health_report._OK
        assert "none due yet" in detail

    def test_a_weekday_counts_only_slots_past_their_grace(self):
        """
        Monday 18:00. Pre-market (08:00) and the midday Short (12:30) are
        long done. Post-market fired at 17:15, 45 minutes ago, and is still
        inside the build window — counting it would cry wolf during a render.
        """
        from monitor import health_report
        status, _, detail, lines = self._run(
            self._at(18, 0, weekday_date=(2026, 8, 31)), [])
        assert status == health_report._FAIL
        assert "2 of 2 slot(s)" in detail
        assert not any("Post-market" in line for line in lines)

    def test_a_weekday_late_evening_includes_post_market(self):
        from monitor import health_report
        status, _, detail, lines = self._run(
            self._at(19, 0, weekday_date=(2026, 8, 31)), [])
        assert status == health_report._FAIL
        assert "3 of 3 slot(s)" in detail
        assert any("Post-market" in line for line in lines)

    def test_an_unreadable_manifest_warns_rather_than_failing(self):
        from monitor import health_report
        with patch("uploader.uploader.load_upload_manifest",
                   side_effect=RuntimeError("corrupt")):
            status, _, detail, _ = health_report._check_todays_slots()
        assert status == health_report._WARN
        assert "manifest unreadable" in detail
