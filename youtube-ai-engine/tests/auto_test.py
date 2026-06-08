"""
tests/auto_test.py — Automated test suite for the AI engine
============================================================
Run by CodeAuditor after each code upgrade to verify nothing broke.
Also runnable manually: python tests/auto_test.py
Exit code 0 = all pass, 1 = failure.
"""
import importlib
import json
import logging
import sys
import traceback
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(level=logging.WARNING)

# ── Test cases ─────────────────────────────────────────────────────────────────


class TestMemory(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")

    def test_schema_created(self):
        with self.mem._conn() as conn:
            tables = {r[0] for r in
                      conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        expected = {"videos", "trend_history", "errors", "upgrades",
                    "config_performance", "ab_tests"}
        self.assertTrue(expected.issubset(tables), f"Missing tables: {expected - tables}")

    def test_save_and_retrieve_video(self):
        vid_id = self.mem.save_video({
            "date": "2025-01-01", "topic": "Test Topic",
            "title": "Test Title", "youtube_id": "abc123",
        })
        self.assertIsInstance(vid_id, int)
        self.assertGreater(vid_id, 0)

    def test_performance_summary_defaults(self):
        summary = self.mem.performance_summary()
        self.assertIn("total_videos", summary)
        self.assertIn("avg_ctr", summary)

    def test_config_performance_update(self):
        # Insert 3 times to satisfy sample_size >= 3 threshold
        for _ in range(3):
            self.mem.update_config_performance(
                "style_preset", "tech-dark", ctr=5.0, avd=42.0, rpm=3.0
            )
        best = self.mem.get_best_config("style_preset")
        self.assertEqual(best, "tech-dark")

    def test_save_trends(self):
        trends = [{"title": "AI News", "source": "test", "score": 0.9}]
        self.mem.save_trends(trends)
        # Should not raise

    def test_log_error(self):
        self.mem.log_error("test_module", "test.py", "TestError", "msg", "traceback")
        # Should not raise

    def test_log_upgrade(self):
        self.mem.log_upgrade("test.py", "weekly_audit",
                             "abc123", "def456", test_passed=True)
        # Should not raise

    def test_recent_topics(self):
        topics = self.mem.recent_topics(days=7)
        self.assertIsInstance(topics, list)


class TestBrain(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        mem = Memory(":memory:")
        try:
            from core.brain import Brain
            self.brain = Brain()
            self.brain.memory = mem   # inject test memory directly
        except ImportError:
            self.skipTest("anthropic module not available")

    def test_decide_topic_fallback(self):
        topic = self.brain.decide_topic([], [])
        self.assertIn("title", topic)

    def test_decide_topic_from_candidates(self):
        candidates = [
            {"title": "AI Revolution 2025", "score": 0.9, "source": "test"},
            {"title": "Clean Energy Boom",   "score": 0.8, "source": "test"},
        ]
        with patch.object(self.brain, "ask", return_value='{"title": "AI Revolution 2025", "reason": "high CTR", "score": 0.9}'):
            topic = self.brain.decide_topic(candidates, [])
        self.assertIn("title", topic)

    def test_decide_style(self):
        with patch.object(self.brain, "ask", return_value="tech-dark"):
            style = self.brain.decide_style("AI topic")
        self.assertIn(style, ["tech-dark", "vlog-warm", "news-clean", "motivation-epic"])

    def test_decide_video_length_default(self):
        length = self.brain.decide_video_length()
        self.assertIsInstance(length, int)
        self.assertGreater(length, 0)

    def test_daily_brief(self):
        brief = self.brain.daily_brief()
        self.assertIsInstance(brief, str)
        self.assertIn("BRAIN", brief)


class TestSelfHealer(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        try:
            from core.self_healer import SelfHealer
            mem = Memory(":memory:")
            self.healer = SelfHealer(mem)
        except ImportError:
            self.skipTest("anthropic module not available")

    def test_wrap_success(self):
        result = self.healer.wrap(lambda: 42)
        self.assertEqual(result, 42)

    def test_wrap_with_fallback(self):
        def bad_fn():
            raise ValueError("test error")

        result = self.healer.wrap(bad_fn, fallback="default")
        self.assertEqual(result, "default")


class TestScriptWriter(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        import production.script_writer  # ensure module is loaded before patching
        mem = Memory(":memory:")
        self.writer = production.script_writer.ScriptWriter(mem)

    def test_fallback_script_structure(self):
        script = self.writer._fallback_script(
            "AI News Today", "tech-dark", 600, "youtube"
        )
        self.assertIn("scenes", script)
        self.assertIn("title", script)
        self.assertIn("hook", script)
        self.assertGreater(len(script["scenes"]), 0)

    def test_generate_without_api_key(self):
        topic  = {"title": "Test Topic", "score": 0.9}
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            script = self.writer.generate(topic)
        self.assertIn("scenes", script)


class TestThumbnailAI(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        from production.thumbnail_ai import ThumbnailAI
        mem = Memory(":memory:")
        self.thumb = ThumbnailAI(mem, width=320, height=180)

    def test_variant_a(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "thumb_a.jpg")
            ok  = self.thumb._variant_face_text(
                {"title": "Test Video Title for AI"}, "tech-dark", out
            )
            # If PIL is available, the file should be created
            self.assertIsInstance(ok, bool)
            if ok:
                self.assertTrue(os.path.exists(out))

    def test_variant_b(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "thumb_b.jpg")
            ok  = self.thumb._variant_bold_stat(
                {"title": "10 Things About AI"}, "tech-dark", out
            )
            self.assertIsInstance(ok, bool)
            if ok:
                self.assertTrue(os.path.exists(out))


class TestCaptionEngine(unittest.TestCase):

    def setUp(self):
        from production.caption_engine import CaptionEngine
        self.ce = CaptionEngine()

    def test_script_to_words(self):
        script = {
            "scenes": [
                {"voiceover": "Hello world this is a test", "duration": 5},
                {"voiceover": "Second scene content here", "duration": 5},
            ]
        }
        segs = self.ce._script_to_words(script)
        self.assertGreater(len(segs), 0)
        for s in segs:
            self.assertIn("start", s)
            self.assertIn("end", s)
            self.assertIn("text", s)

    def test_srt_format(self):
        segs = [{"start": 0.0, "end": 2.5, "text": "Hello world"}]
        srt  = self.ce.to_srt(segs)
        self.assertIn("00:00:00,000 --> 00:00:02,500", srt)
        self.assertIn("Hello world", srt)


class TestSEOOptimizer(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        mem = Memory(":memory:")
        try:
            from publishing.seo_optimizer import SEOOptimizer
            self.seo = SEOOptimizer(mem)
        except ImportError:
            self.skipTest("anthropic module not available")

    def test_fallback_metadata(self):
        topic  = {"title": "AI Replaces Jobs in 2025"}
        script = {"hook": "The world is changing faster than ever.",
                  "scenes": []}
        meta   = self.seo._fallback_metadata(topic, script)
        self.assertIn("title", meta)
        self.assertIn("description", meta)
        self.assertIn("tags", meta)
        self.assertIsInstance(meta["tags"], list)

    def test_build_chapters(self):
        script = {
            "scenes": [
                {"section": "hook",    "duration": 3},
                {"section": "amplify", "duration": 4},
                {"section": "hook",    "duration": 3},
            ]
        }
        chapters = self.seo._build_chapters(script)
        self.assertEqual(len(chapters), 2)   # deduplicated


class TestCompetitorSpy(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        mem = Memory(":memory:")
        from intelligence.competitor_spy import CompetitorSpy
        self.spy = CompetitorSpy(mem)

    def test_parse_duration(self):
        self.assertEqual(self.spy._parse_duration("PT1H2M3S"), 3723)
        self.assertEqual(self.spy._parse_duration("PT30S"),    30)
        self.assertEqual(self.spy._parse_duration("PT5M"),     300)

    def test_title_patterns_empty(self):
        patterns = self.spy.extract_title_patterns([])
        self.assertIsInstance(patterns, list)

    def test_title_patterns_numbers(self):
        videos = [{"title": "5 Things About AI"}, {"title": "10 Ways to Save Energy"},
                  {"title": "3 Reasons Why This Matters"}]
        patterns = self.spy.extract_title_patterns(videos)
        self.assertTrue(any("number" in p.lower() for p in patterns))


class TestSchedulerOptimizer(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        mem = Memory(":memory:")
        from publishing.scheduler_optimizer import SchedulerOptimizer
        self.opt = SchedulerOptimizer(mem)

    def test_next_publish_slot_is_future(self):
        from datetime import datetime, timezone
        slot = self.opt.next_publish_slot(min_hours_from_now=1)
        dt   = datetime.strptime(slot, "%Y-%m-%dT%H:%M:%S.000Z").replace(tzinfo=timezone.utc)
        self.assertGreater(dt, datetime.now(timezone.utc))

    def test_schedule_report(self):
        report = self.opt.schedule_report()
        self.assertIn("best_day", report)
        self.assertIn("next_slot", report)


class TestMetaLearner(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")
        try:
            from evolution.meta_learner import MetaLearner, CADENCE
            self.ML = MetaLearner
            self.CADENCE = CADENCE
            self.ml = MetaLearner(self.mem)
        except ImportError:
            self.skipTest("anthropic module not available")

    def _add_videos(self, n, ctr=4.0):
        base = self.mem.video_count()
        for i in range(n):
            idx = base + i
            self.mem.save_video({
                "date": f"2025-01-{(idx % 28) + 1:02d}", "topic": f"Topic {idx}",
                "title": f"Video {idx}", "youtube_id": f"yt{idx}",
                "script_style": "tech-dark", "video_length_s": 600,
            })
            # give it analytics so trend has data
            vid = self.mem.get_video_stats(n=1)[0]["id"]
            self.mem.update_video_analytics(vid, ctr=ctr, avd_pct=45.0, rpm=3.0)

    def test_should_run_cadence(self):
        self.assertFalse(self.ml.should_run())          # 0 videos
        self._add_videos(self.CADENCE - 1)
        self.assertFalse(self.ml.should_run())          # below milestone
        self._add_videos(1)                             # now exactly CADENCE
        self.assertTrue(self.ml.should_run())

    def test_milestone_not_repeated(self):
        self._add_videos(self.CADENCE)
        self.assertTrue(self.ml.should_run())
        self.mem.record_meta_run(
            video_count=self.CADENCE, milestone=1,
            report={}, plan={}, results={}, actions_done=0,
        )
        self.assertFalse(self.ml.should_run())          # milestone consumed

    def test_gather_self_report(self):
        self._add_videos(self.CADENCE)
        report = self.ml.gather_self_report()
        self.assertEqual(report["video_count"], self.CADENCE)
        self.assertIn("trend", report)
        self.assertIn("errors", report)
        self.assertEqual(len(report["trend"]), self.CADENCE)

    def test_fallback_plan_structure(self):
        self._add_videos(self.CADENCE)
        report = self.ml.gather_self_report()
        plan   = self.ml._fallback_plan(report)
        self.assertIn("actions", plan)
        self.assertIsInstance(plan["actions"], list)
        self.assertGreater(len(plan["actions"]), 0)

    def test_execute_note_action(self):
        plan = {"actions": [{"action": "note", "text": "all good"}]}
        execd = self.ml.execute_plan(plan)
        self.assertEqual(execd["actions_done"], 1)
        self.assertEqual(execd["results"][0]["status"], "noted")

    def test_tune_config_rejects_unlisted_key(self):
        res = self.ml._do_tune_config({"key": "audio.voice_provider", "value": "x"})
        self.assertEqual(res["status"], "skipped")

    def test_tune_config_rejects_out_of_range(self):
        res = self.ml._do_tune_config({"key": "video.fps", "value": 999})
        self.assertEqual(res["status"], "skipped")

    def test_prefer_style_rejects_invalid(self):
        res = self.ml._do_prefer_style({"style": "not-a-real-style"})
        self.assertEqual(res["status"], "skipped")

    def test_audit_file_rejects_path_escape(self):
        res = self.ml._do_audit_file({"path": "../../etc/passwd"})
        self.assertEqual(res["status"], "skipped")


class TestViewerPsychology(unittest.TestCase):

    def setUp(self):
        from production.viewer_psychology import ViewerPsychology, EngagementTooLow
        self.VP  = ViewerPsychology
        self.Err = EngagementTooLow
        self.vp  = ViewerPsychology()

    def _make_script(self, n_scenes=8, dur=30, hook="", outro=""):
        scenes = []
        for i in range(n_scenes):
            scenes.append({
                "id": i + 1,
                "section": "value_loop",
                "duration_s": dur,
                "narration": f"Scene {i} narration with some why and how details.",
                "text": "",
            })
        return {
            "title": "The Secret Nobody Tells You About Solar Energy",
            "hook":  hook or "You won't believe what solar panels actually cost in 2025.",
            "outro": outro or "What do you think — hype or future? Comment below.",
            "scenes": scenes,
            "duration_s": n_scenes * dur,
        }

    def test_score_returns_float(self):
        script = self._make_script()
        score  = self.vp.score(script)
        self.assertIsInstance(score, float)
        self.assertGreater(score, 0)
        self.assertLessEqual(score, 100)

    def test_strong_script_passes_threshold(self):
        script = self._make_script()
        # Inject all the signals the scorer looks for
        script["hook"]  = "Wait — you're losing money every month because of this solar mistake. Stay until the end to see the shocking proof."
        script["outro"] = "Type A if you think solar is overhyped, B if it's the future — comment below, I read every reply."
        for i, s in enumerate(script["scenes"]):
            s["narration"] = (
                "Coming up next: the one number that changes everything. "
                "Wait — stop everything. Here's why most people get this wrong. "
                "Stay with me — the next part is where it gets interesting. "
                "By the end of this video you'll know exactly how to fix it."
            )
        try:
            enhanced = self.vp.engineer(script)
            self.assertGreaterEqual(enhanced.get("psychology_score", 0), 75)
        except self.Err:
            self.fail("Strong script should not raise EngagementTooLow")

    def test_heuristic_upgrade_adds_hook(self):
        script = self._make_script(hook="")
        script["hook"] = ""
        from production.viewer_psychology import ScoreBreakdown
        bd = ScoreBreakdown(hook_strength=0)
        upgraded = self.vp._heuristic_upgrade(script, bd)
        self.assertNotEqual(upgraded.get("hook", ""), "")

    def test_heuristic_upgrade_adds_comment_bait(self):
        script = self._make_script(outro="")
        script["outro"] = ""
        from production.viewer_psychology import ScoreBreakdown
        bd = ScoreBreakdown(comment_bait=0)
        upgraded = self.vp._heuristic_upgrade(script, bd)
        self.assertIn("comment", upgraded.get("outro", "").lower())

    def test_emotional_arc_assigned(self):
        # Use 10s scenes so early arc phases (shock 0-10s, relate 10-30s) are hit
        script = self._make_script(n_scenes=10, dur=10)
        scenes = self.vp.build_emotional_arc(script["scenes"])
        emotions = [s["emotion"] for s in scenes]
        self.assertIn("shock", emotions)
        self.assertIn("relate", emotions)

    def test_score_breakdown_sums_to_total(self):
        script  = self._make_script()
        bd      = self.vp._score_breakdown(script)
        manual  = (
            bd.hook_strength + bd.open_loops + bd.pattern_interrupts +
            bd.curiosity_gaps + bd.emotional_arc + bd.retention_triggers +
            bd.comment_bait + bd.pacing
        )
        self.assertAlmostEqual(bd.total, manual, places=5)

    def test_score_breakdown_within_max_bounds(self):
        script = self._make_script()
        bd     = self.vp._score_breakdown(script)
        self.assertLessEqual(bd.hook_strength,      20.0)
        self.assertLessEqual(bd.open_loops,         15.0)
        self.assertLessEqual(bd.pattern_interrupts, 10.0)
        self.assertLessEqual(bd.curiosity_gaps,     10.0)
        self.assertLessEqual(bd.emotional_arc,      15.0)
        self.assertLessEqual(bd.retention_triggers, 10.0)
        self.assertLessEqual(bd.comment_bait,       10.0)
        self.assertLessEqual(bd.pacing,             10.0)

    def test_engagement_too_low_raised_on_empty_script(self):
        script = {"title": "x", "hook": "", "outro": "", "scenes": []}
        with self.assertRaises(self.Err):
            self.vp.engineer(script)

    def test_fallback_comment_bait_is_string(self):
        bait = self.vp._fallback_comment_bait("renewable energy")
        self.assertIsInstance(bait, str)
        self.assertGreater(len(bait), 10)

    def test_arc_label_covers_full_duration(self):
        for t in [0, 5, 15, 45, 120, 300, 600]:
            label = self.vp._arc_label(t)
            self.assertIsInstance(label, str)
            self.assertGreater(len(label), 0)


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
