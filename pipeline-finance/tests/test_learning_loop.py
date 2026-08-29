"""
tests/test_learning_loop.py — videos learning from each other.

Hooks and styles were chosen by random.choice for the life of the project
while an EMA scoring system sat wired to nothing. Selection without
feedback is a slower random choice; feedback without selection is a
spreadsheet nobody reads. Both halves had been built and never met.
"""
import json
from collections import Counter
from datetime import date, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def logs(tmp_path):
    from config.settings import settings
    settings.logs_dir = tmp_path
    (tmp_path / "analytics").mkdir()
    import importlib
    import channel_manager.performance_tracker as pt
    importlib.reload(pt)
    return tmp_path


OPTIONS = ["alpha", "bravo", "charlie", "delta"]


class TestSelectionExplores:
    def test_untried_options_are_tried_first(self, logs):
        """An option that never gets picked never earns a score."""
        from channel_manager.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker()
        picks = {tracker.choose("styles", OPTIONS) for _ in range(200)}
        assert picks == set(OPTIONS)

    def test_a_proven_winner_is_favoured(self, logs):
        from channel_manager.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker()
        for _ in range(5):
            tracker.record_performance(style="alpha", ctr=0.09, views=900)
            for other in OPTIONS[1:]:
                tracker.record_performance(style=other, ctr=0.01, views=50)
        picks = Counter(tracker.choose("styles", OPTIONS) for _ in range(500))
        assert picks["alpha"] > 300

    def test_the_losers_keep_getting_sampled(self, logs):
        """
        Without this the first option to land on a good day wins forever and
        the channel optimises itself into whatever it happened to try first.
        """
        from channel_manager.performance_tracker import PerformanceTracker
        tracker = PerformanceTracker()
        for _ in range(5):
            tracker.record_performance(style="alpha", ctr=0.09, views=900)
            for other in OPTIONS[1:]:
                tracker.record_performance(style=other, ctr=0.01, views=50)
        picks = Counter(tracker.choose("styles", OPTIONS) for _ in range(1000))
        assert sum(v for k, v in picks.items() if k != "alpha") > 100

    def test_a_single_lucky_result_is_not_trusted_yet(self, logs):
        from channel_manager.performance_tracker import (
            PerformanceTracker, MIN_OBSERVATIONS)
        tracker = PerformanceTracker()
        for option in OPTIONS:
            tracker.record_performance(style=option, ctr=0.01, views=10)
        tracker.record_performance(style="delta", ctr=0.5, views=99999)
        entry = tracker._state["styles"]["delta"]
        assert entry.observations < MIN_OBSERVATIONS

    def test_no_options_returns_the_default(self, logs):
        from channel_manager.performance_tracker import PerformanceTracker
        assert PerformanceTracker().choose("styles", [], default="fallback") == "fallback"


class TestScriptGenUsesTheTracker:
    def test_style_selection_goes_through_scoring(self, logs):
        from generators import script_gen
        assert "style = _pick_style()" in \
            __import__("inspect").getsource(script_gen.generate_weekday_script)

    def test_a_broken_tracker_falls_back_to_random(self, logs, monkeypatch):
        """An unreadable score file should cost learning, not a video."""
        from generators import script_gen
        from config.settings import SCRIPT_STYLES
        monkeypatch.setattr(
            "channel_manager.performance_tracker.PerformanceTracker",
            lambda: (_ for _ in ()).throw(RuntimeError("corrupt state")))
        assert script_gen._pick_style() in SCRIPT_STYLES
        assert script_gen._pick_hook() in script_gen.HOOK_VARIATIONS

    def test_the_hook_survives_onto_the_script(self, logs, monkeypatch):
        """Nothing downstream can report back which hook ran otherwise."""
        from generators import script_gen
        monkeypatch.setattr(script_gen, "_call_claude",
                            lambda system, prompt: ("[HOOK]\nHi.\n[MARKET]\nUp.", 10))
        monkeypatch.setattr(script_gen, "_trending_context", lambda: "")
        result = script_gen.generate_weekday_script("m", "e", "c")
        assert result.hook and result.hook in script_gen.HOOK_VARIATIONS


class TestAttributionReachesTheManifest:
    def test_style_and_hook_are_recorded_with_the_upload(self, tmp_path):
        from uploader import uploader as umod
        from uploader.uploader import UploadConfig
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "manifest.jsonl"
        try:
            result = type("R", (), {"success": True, "video_id": "v1",
                                    "quota_used": 1600})()
            config = UploadConfig(title="T", description="d", tags=[],
                                  script_style="shocked_reaction",
                                  script_hook="Wall Street did not see this coming...")
            umod.record_upload(result, config)
            record = json.loads(umod._UPLOAD_MANIFEST_PATH.read_text().strip())
            assert record["script_style"] == "shocked_reaction"
            assert record["script_hook"].startswith("Wall Street")
        finally:
            umod._UPLOAD_MANIFEST_PATH = original

    def test_a_non_string_attribution_cannot_break_the_manifest(self, tmp_path):
        """
        The manifest is what the dead-man switch reads. Losing an entry to an
        attribution field would be a bad trade.
        """
        from unittest.mock import MagicMock
        from uploader import uploader as umod
        original = umod._UPLOAD_MANIFEST_PATH
        umod._UPLOAD_MANIFEST_PATH = tmp_path / "manifest.jsonl"
        try:
            result = MagicMock(success=True, video_id="v2", quota_used=1600)
            config = MagicMock()
            config.title, config.video_type = "T", "weekday"
            config.playlist_id, config.tags = None, []
            umod.record_upload(result, config)
            record = json.loads(umod._UPLOAD_MANIFEST_PATH.read_text().strip())
            assert record["video_id"] == "v2"
            assert record["script_style"] == ""
        finally:
            umod._UPLOAD_MANIFEST_PATH = original


class TestFeedbackClosesTheLoop:
    @staticmethod
    def _published(logs, count=20, winner="shocked_reaction"):
        from config.settings import SCRIPT_STYLES
        manifest, stats = [], []
        for i in range(count):
            style = SCRIPT_STYLES[i % len(SCRIPT_STYLES)]
            good = style == winner
            manifest.append({"video_id": f"v{i}", "video_type": "weekday",
                             "uploaded_at": f"2026-08-{(i % 28) + 1:02d}T08:00:00",
                             "script_style": style, "script_hook": f"hook{i % 3}"})
            stats.append({"video_id": f"v{i}", "views": 900 if good else 120,
                          "ctr": 0.075 if good else 0.02,
                          "watch_time_minutes": 160 if good else 40})
        (logs / "analytics" / f"{date.today().isoformat()}.json").write_text(
            json.dumps(stats))
        return manifest

    def test_results_become_scores(self, logs):
        from channel_manager.performance_tracker import (
            apply_recent_performance, PerformanceTracker)
        manifest = self._published(logs)
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            assert apply_recent_performance() == 20
        scores = PerformanceTracker().get_scores("styles")
        assert scores[0]["label"] == "shocked_reaction"

    def test_scoring_is_idempotent(self, logs):
        """
        Re-running the weekly job must not stack the same result repeatedly
        and inflate whatever ran most recently.
        """
        from channel_manager.performance_tracker import apply_recent_performance
        manifest = self._published(logs)
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            first = apply_recent_performance()
            second = apply_recent_performance()
        assert first == 20 and second == 0

    def test_videos_published_before_attribution_are_skipped(self, logs):
        from channel_manager.performance_tracker import apply_recent_performance
        manifest = [{"video_id": "old", "video_type": "weekday",
                     "uploaded_at": "2026-07-01T08:00:00"}]
        (logs / "analytics" / f"{date.today().isoformat()}.json").write_text(
            json.dumps([{"video_id": "old", "views": 100, "ctr": 0.03,
                         "watch_time_minutes": 40}]))
        with patch("uploader.uploader.load_upload_manifest", return_value=manifest):
            assert apply_recent_performance() == 0

    def test_no_analytics_is_not_an_error(self, logs):
        from channel_manager.performance_tracker import apply_recent_performance
        with patch("uploader.uploader.load_upload_manifest", return_value=[]):
            assert apply_recent_performance() == 0

    def test_the_loop_is_scheduled(self):
        import inspect
        from scheduler import master_scheduler
        source = inspect.getsource(master_scheduler.run_weekly_analytics)
        assert "apply_recent_performance" in source

    def test_feedback_failure_does_not_break_the_weekly_job(self):
        from scheduler.master_scheduler import run_weekly_analytics
        with patch("channel_manager.performance_tracker.apply_recent_performance",
                   side_effect=RuntimeError("boom")), \
             patch("channel_manager.analytics_tracker.AnalyticsTracker"):
            run_weekly_analytics()      # must not raise


class TestLearningIsVisible:
    def test_no_scores_yet_is_reported_as_correct(self, logs):
        from monitor import health_report
        status, _, detail, _ = health_report._check_learning()
        assert status == health_report._OK
        assert "still random" in detail

    def test_learned_scores_are_shown(self, logs):
        from channel_manager.performance_tracker import PerformanceTracker
        from monitor import health_report
        tracker = PerformanceTracker()
        for _ in range(4):
            tracker.record_performance(style="alpha", hook="h1", ctr=0.08, views=800)
        status, _, _, lines = health_report._check_learning()
        assert status == health_report._OK
        assert any("best style" in line for line in lines)

    def test_a_provisional_score_says_so(self, logs):
        from channel_manager.performance_tracker import PerformanceTracker
        from monitor import health_report
        PerformanceTracker().record_performance(style="alpha", ctr=0.08, views=800)
        _, _, _, lines = health_report._check_learning()
        assert any("provisional" in line for line in lines)
