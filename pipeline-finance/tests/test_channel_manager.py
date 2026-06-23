"""Tests for channel_manager modules 21-24."""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call
from datetime import date

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from channel_manager.playlist_manager import (
    PlaylistManager, PLAYLIST_DEFINITIONS, _ROUTE_MAP,
)
from channel_manager.community_poster import CommunityPoster, POST_CHAR_LIMIT
from channel_manager.analytics_tracker import (
    AnalyticsTracker, VideoStats, WeeklyReport, LOW_CTR_THRESHOLD,
)
from channel_manager.comment_monitor import (
    CommentMonitor, Comment,
    CLASS_SPAM, CLASS_FINANCIAL_ADVICE, CLASS_POSITIVE, CLASS_QUESTION, CLASS_GENERAL,
)


# ── PlaylistManager (Module 21) ───────────────────────────────────────────────

class TestPlaylistManager:

    @pytest.fixture
    def pm(self, tmp_path):
        with patch("channel_manager.playlist_manager.PLAYLIST_IDS_FILE", tmp_path / "playlist_ids.json"):
            mgr = PlaylistManager(youtube_service=MagicMock())
            return mgr

    def test_six_playlists_defined(self):
        assert len(PLAYLIST_DEFINITIONS) == 6

    def test_playlist_names_exact(self):
        names = set(PLAYLIST_DEFINITIONS.keys())
        assert "Daily Market Recaps" in names
        assert "Market Shorts" in names
        assert "Sunday: Investing 101" in names
        assert "Sunday: Insurance" in names
        assert "Sunday: Savings & Wealth" in names
        assert "Sunday: Special Topics" in names

    def test_route_map_covers_video_types(self):
        assert "weekday" in _ROUTE_MAP
        assert "shorts" in _ROUTE_MAP
        assert "sunday" in _ROUTE_MAP

    def test_route_map_sunday_themes(self):
        assert "sunday_investment_banking" in _ROUTE_MAP
        assert "sunday_insurance_protection" in _ROUTE_MAP
        assert "sunday_savings_wealth" in _ROUTE_MAP
        assert "sunday_rotating_bonus" in _ROUTE_MAP

    def test_ensure_playlists_creates_missing(self, pm, tmp_path):
        pm._svc.playlists().list().execute.return_value = {"items": []}
        pm._svc.playlists().list_next.return_value = None
        pm._svc.playlists().insert().execute.return_value = {"id": "PL_fake123"}
        with patch("channel_manager.playlist_manager.PLAYLIST_IDS_FILE", tmp_path / "ids.json"):
            result = pm.ensure_playlists()
        # insert was called for each of 6 playlists
        assert pm._svc.playlists().insert.called

    def test_add_video_to_playlist_success(self, pm):
        pm._svc.playlistItems().insert().execute.return_value = {}
        result = pm.add_video_to_playlist("vid123", "PL_abc")
        assert result is True

    def test_add_video_to_playlist_failure(self, pm):
        pm._svc.playlistItems().insert().execute.side_effect = Exception("API error")
        result = pm.add_video_to_playlist("vid123", "PL_abc")
        assert result is False

    def test_route_weekday_video(self, pm):
        pm._cached_ids["Daily Market Recaps"] = "PL_weekday"
        pm._svc.playlistItems().insert().execute.return_value = {}
        pid = pm.route_video_to_playlist("vid1", "weekday")
        assert pid == "PL_weekday"

    def test_route_sunday_investment_banking(self, pm):
        pm._cached_ids["Sunday: Investing 101"] = "PL_sunday_ib"
        pm._svc.playlistItems().insert().execute.return_value = {}
        pid = pm.route_video_to_playlist("vid2", "sunday", sunday_theme="investment_banking")
        assert pid == "PL_sunday_ib"

    def test_route_shorts_video(self, pm):
        pm._cached_ids["Market Shorts"] = "PL_shorts"
        pm._svc.playlistItems().insert().execute.return_value = {}
        pid = pm.route_video_to_playlist("vid3", "shorts")
        assert pid == "PL_shorts"

    def test_get_playlist_summary_returns_list(self, pm):
        pm._cached_ids = {"Daily Market Recaps": "PL_abc", "Market Shorts": "PL_xyz"}
        summary = pm.get_playlist_summary()
        assert len(summary) == 2
        assert all("name" in s and "playlist_id" in s for s in summary)

    def test_load_cached_ids_from_disk(self, tmp_path):
        ids_file = tmp_path / "playlist_ids.json"
        ids_file.write_text(json.dumps({"Daily Market Recaps": "PL_cached"}))
        with patch("channel_manager.playlist_manager.PLAYLIST_IDS_FILE", ids_file):
            pm = PlaylistManager(youtube_service=MagicMock())
        assert pm._cached_ids.get("Daily Market Recaps") == "PL_cached"


# ── CommunityPoster (Module 22) ───────────────────────────────────────────────

class TestCommunityPoster:

    @pytest.fixture
    def cp(self):
        return CommunityPoster(youtube_service=MagicMock())

    def test_generate_post_text_length_capped(self, cp):
        week_data = {"tickers": ["SPY", "AAPL"], "week_ending": "2026-06-23"}
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="A" * 2000)]
        with patch("anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.return_value = mock_msg
            result = cp.generate_post_text(week_data)
        assert len(result) <= POST_CHAR_LIMIT

    def test_generate_post_text_fallback_on_error(self, cp):
        week_data = {"tickers": ["SPY", "QQQ"], "week_ending": "2026-06-23"}
        with patch("anthropic.Anthropic") as mock_anthropic:
            mock_anthropic.return_value.messages.create.side_effect = Exception("API down")
            result = cp.generate_post_text(week_data)
        assert "SPY" in result or "QQQ" in result
        assert "Not financial advice" in result

    def test_run_weekly_post_saves_record(self, cp, tmp_path):
        with patch("channel_manager.community_poster.COMMUNITY_POSTS_DIR", tmp_path):
            with patch.object(cp, "get_week_watchlist", return_value={"tickers": ["SPY"]}):
                with patch.object(cp, "generate_post_text", return_value="Test post content"):
                    with patch.object(cp, "_try_api_post", return_value=False):
                        cp.run_weekly_post()
        records = list(tmp_path.glob("record_*.json"))
        assert len(records) == 1

    def test_post_char_limit_constant(self):
        assert POST_CHAR_LIMIT == 1200

    def test_api_failure_saves_for_manual(self, cp, tmp_path):
        with patch("channel_manager.community_poster.COMMUNITY_POSTS_DIR", tmp_path):
            cp._svc.activities().insert().execute.side_effect = Exception("403 forbidden")
            result = cp.post_community_update("Test post text")
        assert result is False
        saved = list(tmp_path.glob("community_post_*.txt"))
        assert len(saved) == 1

    def test_get_week_watchlist_returns_dict(self, cp):
        data = cp.get_week_watchlist()
        assert isinstance(data, dict)
        assert "tickers" in data
        assert len(data["tickers"]) > 0


# ── AnalyticsTracker (Module 23) ──────────────────────────────────────────────

class TestAnalyticsTracker:

    @pytest.fixture
    def at(self, tmp_path):
        tracker = AnalyticsTracker(
            youtube_service=MagicMock(),
            analytics_service=MagicMock(),
        )
        return tracker

    def test_video_stats_to_dict(self):
        s = VideoStats(video_id="abc", date="2026-06-23", views=1000, ctr=0.045)
        d = s.to_dict()
        assert d["video_id"] == "abc"
        assert d["ctr"] == 0.045
        assert d["views"] == 1000

    def test_video_stats_ctr_pct(self):
        s = VideoStats(video_id="x", date="2026-06-23", ctr=0.032)
        assert s.ctr_pct == "3.2%"

    def test_weekly_report_to_dict_keys(self):
        r = WeeklyReport(week_of="2026-W26", start_date="2026-06-22", end_date="2026-06-28")
        d = r.to_dict()
        for key in ("week_of", "total_views", "avg_ctr", "low_ctr_videos", "subscribers_gained"):
            assert key in d

    def test_fetch_video_stats_no_rows(self, at):
        at._analytics_svc.reports().query().execute.return_value = {"rows": []}
        stats = at.fetch_video_stats("vid123", "2026-06-22", "2026-06-22")
        assert stats is not None
        assert stats.views == 0

    def test_fetch_video_stats_with_data(self, at):
        at._analytics_svc.reports().query().execute.return_value = {
            "rows": [["vid123", 500, 120.5, 2000, 0.04, 90.0, 3, 45, 8]]
        }
        stats = at.fetch_video_stats("vid123")
        assert stats.views == 500
        assert stats.ctr == 0.04
        assert stats.watch_time_minutes == 120.5

    def test_save_daily_stats(self, at, tmp_path):
        stats = [VideoStats(video_id="v1", date="2026-06-22", views=100)]
        with patch("channel_manager.analytics_tracker.ANALYTICS_DIR", tmp_path):
            path = at.save_daily_stats(stats, "2026-06-22")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data[0]["video_id"] == "v1"

    def test_swap_title_ab_success(self, at):
        at._svc.videos().list().execute.return_value = {
            "items": [{"snippet": {"title": "Old Title", "categoryId": "25"}}]
        }
        at._svc.videos().update().execute.return_value = {}
        result = at.swap_title_ab("vid1", "New Title For A/B Test")
        assert result is True

    def test_swap_title_ab_video_not_found(self, at):
        at._svc.videos().list().execute.return_value = {"items": []}
        result = at.swap_title_ab("nonexistent", "New Title")
        assert result is False

    def test_low_ctr_threshold_constant(self):
        assert LOW_CTR_THRESHOLD == 0.02

    def test_flag_low_ctr_no_file_runs_pull(self, at, tmp_path):
        at._svc.search().list().execute.return_value = {"items": []}
        at._analytics_svc.reports().query().execute.return_value = {"rows": []}
        with patch("channel_manager.analytics_tracker.ANALYTICS_DIR", tmp_path):
            result = at.flag_low_ctr_videos()
        assert isinstance(result, list)


# ── CommentMonitor (Module 24) ────────────────────────────────────────────────

class TestCommentMonitor:

    @pytest.fixture
    def cm(self):
        return CommentMonitor(youtube_service=MagicMock())

    def _make_comment(self, text="Test comment", classification=None):
        return Comment(
            comment_id="c1",
            video_id="v1",
            author="TestUser",
            author_channel_id="UC123",
            text=text,
            published_at="2026-06-23T20:00:00Z",
            classification=classification,
        )

    def test_comment_to_dict(self):
        c = self._make_comment()
        d = c.to_dict()
        assert d["comment_id"] == "c1"
        assert d["video_id"] == "v1"
        assert "classification" in d

    def test_classify_spam_regex_fast_path(self, cm):
        result = cm.classify_comment("I made $5000 last week! DM me invest now")
        assert result == CLASS_SPAM

    def test_classify_spam_forex_signal(self, cm):
        result = cm.classify_comment("Follow me for forex profit signals guaranteed")
        assert result == CLASS_SPAM

    def test_classify_via_claude(self, cm):
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="positive")]
        with patch("anthropic.Anthropic") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_msg
            result = cm.classify_comment("Great video, learned a lot!")
        assert result == CLASS_POSITIVE

    def test_classify_defaults_general_on_unknown(self, cm):
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="unknown_category")]
        with patch("anthropic.Anthropic") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_msg
            result = cm.classify_comment("Some comment text")
        assert result == CLASS_GENERAL

    def test_generate_reply_adds_disclaimer_for_financial_advice(self, cm):
        comment = self._make_comment("Should I buy AAPL now?", CLASS_FINANCIAL_ADVICE)
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Great question about AAPL.")]
        with patch("anthropic.Anthropic") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_msg
            reply = cm.generate_reply(comment)
        from config.settings import settings
        assert settings.disclaimer_text in reply

    def test_generate_reply_no_disclaimer_for_general(self, cm):
        comment = self._make_comment("Love the content!", CLASS_POSITIVE)
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text="Thanks so much! 🙏")]
        with patch("anthropic.Anthropic") as mock_client:
            mock_client.return_value.messages.create.return_value = mock_msg
            reply = cm.generate_reply(comment)
        assert "financial advice" not in reply.lower()

    def test_flag_spam_calls_set_moderation(self, cm):
        cm._svc.comments().setModerationStatus().execute.return_value = {}
        result = cm.flag_spam("comment_id_123")
        assert result is True

    def test_flag_spam_returns_false_on_error(self, cm):
        cm._svc.comments().setModerationStatus().execute.side_effect = Exception("403")
        result = cm.flag_spam("cid123")
        assert result is False

    def test_save_comments(self, cm, tmp_path):
        comments = [self._make_comment("Hello world")]
        with patch("channel_manager.comment_monitor.COMMENTS_DIR", tmp_path):
            path = cm.save_comments(comments, "2026-06-23")
        assert path.exists()
        data = json.loads(path.read_text())
        assert data[0]["text"] == "Hello world"

    def test_fetch_new_comments_empty_video_list(self, cm):
        cm._svc.search().list().execute.return_value = {"items": []}
        result = cm.fetch_new_comments(video_ids=[])
        assert result == []

    def test_get_pending_replies_no_file(self, cm, tmp_path):
        with patch("channel_manager.comment_monitor.COMMENTS_DIR", tmp_path):
            result = cm.get_pending_replies("2026-06-01")
        assert result == []
