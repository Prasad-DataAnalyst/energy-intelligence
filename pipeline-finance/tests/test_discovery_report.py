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


def _channel(impressions=0, views=0, ctr=0.0, watch_minutes=0.0, subs=0, error=None):
    data = {
        "total_impressions": impressions,
        "total_views": views,
        "avg_ctr": ctr,
        "total_watch_time_minutes": watch_minutes,
        "subscribers_gained": subs,
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
                          {"NO_LINK_OTHER": 9}, videos=27)
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
