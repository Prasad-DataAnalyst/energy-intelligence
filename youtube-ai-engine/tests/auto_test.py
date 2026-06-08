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


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
