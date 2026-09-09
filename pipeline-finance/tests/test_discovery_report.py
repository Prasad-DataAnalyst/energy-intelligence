"""
tests/test_discovery_report.py — the diagnosis has to distinguish the three
causes of "no views", because they need completely different work and the
existing weekly totals cannot tell them apart.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from monitor.discovery_report import (
    diagnose, ALGORITHMIC_SOURCES, MIN_IMPRESSIONS_FOR_CTR,
    _OK, _WARN, _FAIL,
)


def _channel(impressions=0, views=0, ctr=0.0, watch_minutes=0.0, subs=0,
             error=None, impressions_available=True):
    data = {
        "total_impressions": impressions,
        "total_views": views,
        "avg_ctr": ctr,
        "total_watch_time_minutes": watch_minutes,
        "subscribers_gained": subs,
        "impressions_available": impressions_available,
    }
    if error:
        data["error"] = error
    return data


class TestTheThreeBottlenecks:

    def test_almost_no_impressions_is_a_distribution_problem(self):
        """
        The case this channel is actually in. Better thumbnails convert
        impressions you already have, and there are none to convert.
        """
        result = diagnose(_channel(impressions=180, views=9, ctr=0.05),
                          {"SUBSCRIBER": 7, "NO_LINK_OTHER": 2}, videos=27)
        assert result.bottleneck == "distribution"
        assert result.status == _FAIL
        assert "180" in result.verdict

    def test_impressions_without_clicks_is_a_packaging_problem(self):
        result = diagnose(_channel(impressions=40_000, views=420, ctr=0.011,
                                   watch_minutes=900),
                          {"RELATED_VIDEO": 300, "SUBSCRIBER": 120}, videos=27)
        assert result.bottleneck == "packaging"
        assert "1.1%" in result.verdict

    def test_clicks_without_watch_time_is_a_retention_problem(self):
        result = diagnose(_channel(impressions=40_000, views=2000, ctr=0.05,
                                   watch_minutes=200),   # 6 seconds a view
                          {"SUBSCRIBER": 2000}, videos=27)
        assert result.bottleneck == "retention"

    def test_a_healthy_channel_is_not_told_to_fix_something(self):
        result = diagnose(_channel(impressions=40_000, views=2400, ctr=0.06,
                                   watch_minutes=4000),
                          {"SUBSCRIBER": 2400}, videos=27)
        assert result.bottleneck == "none"
        assert result.status == _OK


class TestTrafficSources:

    def test_traffic_the_owner_sent_does_not_count_as_distribution(self):
        """
        Views from links and channel pages are views someone was sent. A
        channel living on those is not being distributed, however good the
        raw view count looks.
        """
        result = diagnose(
            _channel(impressions=5_000, views=300, ctr=0.06, watch_minutes=900),
            {"EXT_URL": 210, "YT_CHANNEL": 60, "SUBSCRIBER": 30}, videos=27)
        assert result.bottleneck == "distribution"
        assert "not being distributed" in result.verdict

    def test_the_same_views_from_youtube_itself_are_not_flagged(self):
        result = diagnose(
            _channel(impressions=5_000, views=300, ctr=0.06, watch_minutes=900),
            {"SUBSCRIBER": 210, "RELATED_VIDEO": 60, "SHORTS": 30}, videos=27)
        assert result.bottleneck != "distribution"

    def test_the_shorts_feed_counts_as_youtube_distributing(self):
        assert "SHORTS" in ALGORITHMIC_SOURCES
        assert "EXT_URL" not in ALGORITHMIC_SOURCES


class TestMissingDataIsNotAFinding:
    """
    "No algorithmic traffic" and "the API call failed" produce identical
    numbers. Reading one as the other would send weeks of work at the wrong
    problem.
    """

    def test_an_api_error_is_reported_as_an_error(self):
        result = diagnose(_channel(error="insufficientPermissions"), {}, videos=27)
        assert result.bottleneck == "unknown"
        assert result.status == _FAIL
        assert any("analytics_token" in note for note in result.notes)

    def test_no_data_at_all_is_not_called_a_distribution_problem(self):
        result = diagnose(_channel(), {}, videos=27)
        assert result.bottleneck == "unknown"
        assert any("24" in note or "verify-uploads" in note
                   for note in result.notes)

    def test_an_api_error_does_not_discard_the_traffic_data(self):
        """
        Sources are a separate query and usually still arrive. Declaring "no
        diagnosis possible" while holding a full traffic split threw away
        the answer to the more important question.
        """
        result = diagnose(
            _channel(error="Unknown identifier (impressions)"),
            {"SHORTS": 42, "YT_SEARCH": 25, "SUBSCRIBER": 8,
             "RELATED_VIDEO": 4, "NO_LINK_OTHER": 2}, videos=27)
        assert result.bottleneck == "volume"
        assert result.numbers["views"] == 81
        assert any("unavailable" in note for note in result.notes)

    def test_unreported_impressions_are_not_read_as_zero_impressions(self):
        """
        A channel that does not report impressions and a channel with no
        impressions are the same zero, and calling the first one a
        distribution failure would be a fabricated finding.
        """
        result = diagnose(
            _channel(views=900, watch_minutes=2000, impressions_available=False),
            {"SUBSCRIBER": 700, "RELATED_VIDEO": 200}, videos=10)
        assert result.bottleneck != "distribution"
        assert any("Studio" in note for note in result.notes)

    def test_missing_traffic_sources_are_flagged_as_missing(self):
        """An empty source split must not read as an empty result."""
        result = diagnose(_channel(impressions=40_000, views=420, ctr=0.011,
                                   watch_minutes=900), {}, videos=27)
        assert any("missing, not empty" in note for note in result.notes)


class TestThresholdsAreHonest:

    def test_click_through_is_not_judged_on_too_few_impressions(self):
        """3% of 40 impressions is one click and says nothing."""
        result = diagnose(
            _channel(impressions=MIN_IMPRESSIONS_FOR_CTR - 1, views=2, ctr=0.001),
            {"SUBSCRIBER": 2}, videos=10)
        assert result.bottleneck == "distribution"
        assert any("cannot be judged" in note for note in result.notes)

    def test_the_numbers_behind_the_verdict_are_always_returned(self):
        result = diagnose(_channel(impressions=40_000, views=420, ctr=0.011,
                                   watch_minutes=900),
                          {"SUBSCRIBER": 420}, videos=27)
        assert result.numbers["impressions"] == 40_000
        assert result.numbers["impressions_per_video"] == pytest.approx(1481.5)
        assert result.numbers["seconds_per_view"] == pytest.approx(128.6, abs=0.2)

    def test_zero_videos_does_not_divide_by_zero(self):
        assert diagnose(_channel(impressions=5, views=1), {}, videos=0) is not None


class TestVolumeIsNotSuppression:
    """
    The real shape of this channel: YouTube is showing the videos, in very
    small batches. That is a different problem from being suppressed, and
    calling it suppression sends the work in the wrong direction.
    """

    _SOURCES = {"SHORTS": 42, "YT_SEARCH": 25, "SUBSCRIBER": 8,
                "RELATED_VIDEO": 4, "NO_LINK_OTHER": 2, "EXT_URL": 2,
                "YT_CHANNEL": 1}

    def test_healthy_sources_with_tiny_numbers_is_a_volume_problem(self):
        result = diagnose(
            _channel(views=84, watch_minutes=40, impressions_available=False),
            self._SOURCES, videos=27)
        assert result.bottleneck == "volume"
        assert "IS distributing" in result.verdict

    def test_it_names_the_surface_already_working(self):
        result = diagnose(
            _channel(views=84, watch_minutes=40, impressions_available=False),
            self._SOURCES, videos=27)
        assert any("Shorts feed" in note for note in result.notes)

    def test_it_says_plainly_that_this_is_not_suppression(self):
        result = diagnose(
            _channel(views=84, watch_minutes=40, impressions_available=False),
            self._SOURCES, videos=27)
        assert any("suppressed" in note.lower() for note in result.notes)
        assert "not being distributed" not in result.verdict

    def test_retention_is_not_judged_on_a_handful_of_views(self):
        """
        84 views at 28 seconds is not a retention finding — one long session
        moves that average by seconds. The old rule called it one.
        """
        result = diagnose(
            _channel(views=84, watch_minutes=40, impressions_available=False),
            self._SOURCES, videos=27)
        assert result.bottleneck != "retention"

    def test_retention_is_judged_once_there_are_enough_views(self):
        big = {source: views * 20 for source, views in self._SOURCES.items()}
        result = diagnose(
            _channel(views=1680, watch_minutes=280, impressions_available=False),
            big, videos=27)
        assert result.bottleneck == "retention"

    def test_the_same_sources_at_real_scale_are_not_a_volume_problem(self):
        big = {source: views * 400 for source, views in self._SOURCES.items()}
        result = diagnose(
            _channel(views=33_600, watch_minutes=40_000, impressions_available=False),
            big, videos=27)
        assert result.bottleneck != "volume"


class TestAnalyticsMetricNames:
    """
    The query used "impressions" and "impressionClickThroughRate", which are
    not identifiers this API has ever accepted. One bad metric fails the
    whole request, so the channel returned no views and no watch time
    either — the diagnosis lost everything to a naming error.
    """

    def test_the_impression_metrics_use_their_real_names(self):
        from channel_manager.analytics_tracker import _IMPRESSION_METRICS
        assert _IMPRESSION_METRICS == (
            "videoThumbnailImpressions,videoThumbnailImpressionsClickRate")

    def test_core_metrics_never_include_the_rejected_identifiers(self):
        from channel_manager.analytics_tracker import _CORE_METRICS
        assert "impressionClickThroughRate" not in _CORE_METRICS
        assert ",impressions" not in _CORE_METRICS

    def test_a_rejected_impression_metric_does_not_cost_the_other_data(self):
        from unittest.mock import MagicMock
        from channel_manager.analytics_tracker import (
            _query_with_optional_impressions, _CORE_METRICS)

        attempts = []

        class Analytics:
            def reports(self):
                return self

            def query(self, **kw):
                attempts.append(kw["metrics"])
                if "videoThumbnailImpressions" in kw["metrics"]:
                    raise RuntimeError("Unknown identifier "
                                       "(videoThumbnailImpressions)")
                return MagicMock(execute=lambda: {"rows": [[84, 40.0, 1]]})

        rows, had_impressions, error = _query_with_optional_impressions(
            Analytics(), ids="channel==mine", startDate="a", endDate="b",
            metrics=_CORE_METRICS)
        assert rows == [[84, 40.0, 1]]
        assert had_impressions is False
        assert error is None
        assert len(attempts) == 2

    def test_a_real_failure_is_not_swallowed_as_a_missing_metric(self):
        from channel_manager.analytics_tracker import (
            _query_with_optional_impressions, _CORE_METRICS)

        class Analytics:
            def reports(self):
                return self

            def query(self, **kw):
                raise RuntimeError("insufficientPermissions")

        rows, had_impressions, error = _query_with_optional_impressions(
            Analytics(), ids="x", startDate="a", endDate="b",
            metrics=_CORE_METRICS)
        assert rows == [] and error is not None


class TestOnDemandAnalyticsPull:
    """
    The pull only ran at 21:30 ET, so a fix to it could not be verified
    until the next morning — and it had walked an empty video list for its
    whole life with no way to see that.
    """

    def test_an_empty_video_list_is_reported_as_the_failure_it_is(self, capsys,
                                                                  monkeypatch):
        """Zero videos and zero views look identical in the totals."""
        import main
        from unittest.mock import MagicMock

        tracker = MagicMock()
        tracker._list_recent_video_ids.return_value = []
        monkeypatch.setattr(
            "channel_manager.analytics_tracker.AnalyticsTracker",
            lambda *a, **k: tracker)

        assert main.cmd_pull_analytics() == 1
        out = capsys.readouterr().out
        assert "Videos found: 0" in out
        assert "nothing to walk" in out
        tracker.run_daily_pull.assert_not_called()

    def test_videos_with_no_stats_yet_is_not_an_error(self, capsys, monkeypatch):
        """YouTube lags 24-48h; a young channel genuinely has none."""
        import main
        from unittest.mock import MagicMock

        tracker = MagicMock()
        tracker._list_recent_video_ids.return_value = ["v1", "v2"]
        tracker.run_daily_pull.return_value = []
        monkeypatch.setattr(
            "channel_manager.analytics_tracker.AnalyticsTracker",
            lambda *a, **k: tracker)

        assert main.cmd_pull_analytics() == 0
        assert "24" in capsys.readouterr().out

    def test_it_prints_what_it_collected(self, capsys, monkeypatch):
        import main
        from unittest.mock import MagicMock
        from channel_manager.analytics_tracker import VideoStats

        tracker = MagicMock()
        tracker._list_recent_video_ids.return_value = ["v1", "v2"]
        tracker.run_daily_pull.return_value = [
            VideoStats(video_id="v1", date="2026-09-09", views=12,
                       watch_time_minutes=8.5, ctr=0.031),
            VideoStats(video_id="v2", date="2026-09-09", views=3,
                       watch_time_minutes=1.2, ctr=0.02),
        ]
        monkeypatch.setattr(
            "channel_manager.analytics_tracker.AnalyticsTracker",
            lambda *a, **k: tracker)

        assert main.cmd_pull_analytics() == 0
        out = capsys.readouterr().out
        assert "Stats collected: 2 of 2" in out
        assert "v1" in out and "12" in out
        # Busiest first, so the useful line is not buried.
        assert out.index("v1") < out.index("v2")
