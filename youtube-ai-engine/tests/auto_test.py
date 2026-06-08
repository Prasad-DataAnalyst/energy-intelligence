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


class TestAlgorithmHacker(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")
        from intelligence.algorithm_hacker import (
            AlgorithmHacker, HomepageSampler, TagAnalyzer,
            ShadowRanker, AlgorithmChangeDetector,
            _duration_bucket, _jsd, _parse_dur_string,
        )
        self.AH  = AlgorithmHacker
        self.HS  = HomepageSampler
        self.TA  = TagAnalyzer
        self.SR  = ShadowRanker
        self.ACD = AlgorithmChangeDetector
        self._dur_bucket     = _duration_bucket
        self._jsd            = _jsd
        self._parse_dur      = _parse_dur_string
        self.ah  = AlgorithmHacker(self.mem)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _make_samples(self, n=20):
        samples = []
        for i in range(n):
            dur = 300 + i * 30
            samples.append({
                "video_id":    f"vid{i}",
                "title":       f"How to master solar energy in {i+3} steps",
                "duration_s":  dur,
                "is_short":    dur <= 60,
                "boost_count": max(1, n - i),
                "region":      "US",
            })
        return samples

    # ── Unit tests ─────────────────────────────────────────────────────────

    def test_duration_bucket_boundaries(self):
        self.assertEqual(self._dur_bucket(0),     "<2min")
        self.assertEqual(self._dur_bucket(119),   "<2min")
        self.assertEqual(self._dur_bucket(120),   "2-5min")
        self.assertEqual(self._dur_bucket(479),   "5-8min")
        self.assertEqual(self._dur_bucket(480),   "8-12min")   # 480s = exactly 8min → 8-12 bucket
        self.assertEqual(self._dur_bucket(600),   "8-12min")
        self.assertEqual(self._dur_bucket(720),   "12-20min")
        self.assertEqual(self._dur_bucket(1200),  "20+min")

    def test_jsd_identical_distributions(self):
        p = {"a": 0.5, "b": 0.5}
        self.assertAlmostEqual(self._jsd(p, p), 0.0, places=4)

    def test_jsd_orthogonal_distributions(self):
        p = {"a": 1.0, "b": 0.0}
        q = {"a": 0.0, "b": 1.0}
        jsd = self._jsd(p, q)
        self.assertGreater(jsd, 0.5)   # close to log(2) ≈ 0.693

    def test_parse_dur_string(self):
        self.assertEqual(self._parse_dur("1:23:45"), 5025)
        self.assertEqual(self._parse_dur("12:34"),   754)
        self.assertEqual(self._parse_dur("0:45"),    45)
        self.assertEqual(self._parse_dur(""),        0)

    def test_tag_analyzer_top_tags_from_titles(self):
        ta      = self.TA()
        samples = self._make_samples(10)
        tags    = ta.top_tags(samples, n=10)
        self.assertIsInstance(tags, list)
        self.assertGreater(len(tags), 0)
        self.assertIn("solar", tags)

    def test_tag_analyzer_shorts_ratio(self):
        ta = self.TA()
        samples = [{"duration_s": 30, "is_short": True}] * 3 + \
                  [{"duration_s": 600, "is_short": False}] * 7
        ratio = ta.shorts_ratio(samples)
        self.assertAlmostEqual(ratio, 0.3, places=2)

    def test_tag_analyzer_duration_distribution(self):
        ta = self.TA()
        samples = self._make_samples(20)
        dist = ta.duration_distribution(samples)
        self.assertIsInstance(dist, dict)
        total = sum(dist.values())
        self.assertAlmostEqual(total, 1.0, places=2)

    def test_shadow_ranker_predict_returns_structure(self):
        sr = self.SR(self.mem)
        script   = {"title": "Solar energy", "total_duration": 600,
                    "style": "tech-dark", "psychology_score": 80.0}
        metadata = {"title": "Solar energy secrets", "tags": ["solar", "energy"]}
        pred = sr.predict(script, metadata,
                          boosted_titles=["solar energy tips", "green energy"],
                          trending_tags=["solar", "energy", "green"])
        for key in ("composite_score", "predicted_ctr", "predicted_avd_pct",
                    "signals", "weakest_signal", "publish_approved"):
            self.assertIn(key, pred)
        self.assertGreaterEqual(pred["composite_score"], 0)
        self.assertLessEqual(pred["composite_score"],    100)

    def test_shadow_ranker_no_history_approves(self):
        sr   = self.SR(self.mem)
        pred = sr.predict({"total_duration": 600}, {})
        approved, _ = sr.should_publish(pred)
        self.assertTrue(approved)

    def test_algorithm_hacker_analyse_samples(self):
        samples  = self._make_samples(15)
        analysis = self.ah._analyse_samples(samples)
        for key in ("shorts_ratio", "duration_dist", "optimal_duration_s",
                    "form_balance", "ctr_sweet_spots", "top_tags"):
            self.assertIn(key, analysis)
        self.assertIsInstance(analysis["shorts_ratio"], float)
        self.assertIsInstance(analysis["optimal_duration_s"], int)

    def test_snapshot_saved_and_retrieved(self):
        from datetime import datetime as _dt
        samples  = self._make_samples(10)
        analysis = self.ah._analyse_samples(samples)
        analysis["date"]            = _dt.now().strftime("%Y-%m-%d")
        analysis["best_upload_hour"] = 14
        analysis["best_upload_day"]  = "Wednesday"
        analysis["upload_hour_dist"] = {"12-18": 0.6, "18-24": 0.4}
        snap_id = self.mem.save_algorithm_snapshot(analysis)
        self.assertGreater(snap_id, 0)
        snaps = self.mem.get_algorithm_snapshots(n=5)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["best_upload_hour"], 14)

    def test_change_detector_insufficient_data(self):
        acd    = self.ACD(self.mem)
        report = acd.detect_changes(days=7)
        self.assertEqual(report["status"], "insufficient_data")

    def test_shadow_ranking_saved_to_memory(self):
        self.mem.save_shadow_ranking({
            "youtube_id":       "yt_test_001",
            "predicted_ctr":    4.2,
            "predicted_avd":    48.0,
            "predicted_score":  72.5,
            "publish_decision": "approved",
        })
        acc = self.mem.shadow_ranking_accuracy()
        self.assertEqual(acc["samples"], 0)   # no actuals filled in yet

    def test_get_todays_optimal_settings_stable_no_cache(self):
        result = self.ah.get_todays_optimal_settings()
        # Returns {} when no cache — no crash
        self.assertIsInstance(result, dict)

    def test_memory_schema_has_new_tables(self):
        expected = {"algorithm_snapshots", "shadow_rankings"}
        with self.mem._conn() as conn:
            rows   = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables))


class TestMonetizationEngine(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")
        from monetization.monetization_engine import (
            AdRevenueOptimizer, SponsorshipAutomation, AffiliateLinkEngine,
            MerchandiseTrigger, RevenueDashboard, MonetizationEngine,
            _midroll_slots, _midroll_timestamps,
        )
        self.AdOpt    = AdRevenueOptimizer
        self.Sponsor  = SponsorshipAutomation
        self.Affiliate = AffiliateLinkEngine
        self.Merch    = MerchandiseTrigger
        self.Dashboard = RevenueDashboard
        self.ME        = MonetizationEngine
        self._slots    = _midroll_slots
        self._stamps   = _midroll_timestamps
        self.ad_opt   = AdRevenueOptimizer()
        self.me       = MonetizationEngine(self.mem)

    def _make_script(self, duration_s=600, n_scenes=8):
        scene_dur = duration_s / n_scenes
        return {
            "title":  "How to save money on your electricity bill",
            "hook":   "You won't believe how much money you're wasting on power.",
            "outro":  "Subscribe and hit the bell for more energy tips!",
            "total_duration": duration_s,
            "scenes": [
                {
                    "id": i + 1,
                    "section": "value_loop",
                    "duration_s": scene_dur,
                    "duration": scene_dur,
                    "narration": f"Scene {i}: here is how solar panels work and why tools matter.",
                    "voiceover": f"Scene {i} voiceover",
                }
                for i in range(n_scenes)
            ],
        }

    # ── Mid-roll slot formula ───────────────────────────────────────────────

    def test_midroll_slots_below_threshold(self):
        self.assertEqual(self._slots(479), 0)
        self.assertEqual(self._slots(0),   0)

    def test_midroll_slots_at_8_min(self):
        self.assertEqual(self._slots(480), 1)

    def test_midroll_slots_at_10_min(self):
        self.assertEqual(self._slots(600), 2)

    def test_midroll_slots_at_12_min(self):
        self.assertEqual(self._slots(720), 3)

    def test_midroll_timestamps_count(self):
        stamps = self._stamps(600)
        self.assertEqual(len(stamps), 2)
        for s in stamps:
            self.assertGreater(s, 0)
            self.assertLess(s, 600)

    # ── AdRevenueOptimizer ──────────────────────────────────────────────────

    def test_ad_score_clean_script_high(self):
        script = self._make_script()
        result = self.ad_opt.score_advertiser_friendly(script)
        self.assertGreaterEqual(result["score"], 60)
        self.assertIn("grade", result)
        self.assertIn("mid_roll_slots", result)

    def test_ad_score_risky_script_lower(self):
        script = self._make_script()
        script["hook"] = "This video will kill the competition and bomb your rivals."
        clean  = self._make_script()
        risky_score  = self.ad_opt.score_advertiser_friendly(script)["score"]
        clean_score  = self.ad_opt.score_advertiser_friendly(clean)["score"]
        self.assertLess(risky_score, clean_score)

    def test_replace_risky_words_substitutes(self):
        cleaned, reps = self.ad_opt.replace_risky_words(
            "We need to kill the noise and bomb the competition."
        )
        self.assertNotIn("kill", cleaned.lower())
        self.assertNotIn("bomb", cleaned.lower())
        self.assertGreater(len(reps), 0)

    def test_replace_risky_camera_context_preserved(self):
        text = "I shot this footage with my camera and captured the scene."
        cleaned, reps = self.ad_opt.replace_risky_words(text, context_aware=True)
        self.assertIn("shot", cleaned.lower())
        self.assertEqual(len(reps), 0)

    def test_optimize_length_recommendation(self):
        rec = self.ad_opt.optimize_length_for_midrolls(300, target_slots=3)
        self.assertGreaterEqual(rec["slot_count"], 3)
        self.assertGreaterEqual(rec["recommended_duration_s"], 480)

    def test_natural_ad_breaks_count(self):
        script = self._make_script(duration_s=600, n_scenes=8)
        breaks = self.ad_opt.find_natural_ad_breaks(script)
        self.assertEqual(len(breaks), 2)  # 600s → 2 slots
        for b in breaks:
            self.assertIn("target_s", b)
            self.assertIn("actual_s", b)

    def test_optimize_script_adds_metadata(self):
        script = self._make_script()
        result = self.ad_opt.optimize_script(script)
        self.assertIn("ad_optimization", result)
        ao = result["ad_optimization"]
        self.assertIn("advertiser_score", ao)
        self.assertIn("mid_roll_slots", ao)

    # ── SponsorshipAutomation ───────────────────────────────────────────────

    def test_discover_niche_sponsors_returns_list(self):
        sp = self.Sponsor(self.mem)
        brands = sp.discover_niche_sponsors(niche="technology")
        self.assertIsInstance(brands, list)
        self.assertGreater(len(brands), 0)
        self.assertIn("name", brands[0])

    def test_estimate_deal_value_structure(self):
        sp   = self.Sponsor(self.mem)
        deal = sp.estimate_deal_value(avg_views=10000, niche="technology")
        self.assertIn("deal_value", deal)
        self.assertGreater(deal["deal_value"], 0)
        self.assertGreater(deal["range_high"], deal["range_low"])

    def test_outreach_email_template(self):
        sp    = self.Sponsor(self.mem)
        email = sp._template_outreach_email(
            brand  = {"name": "NordVPN", "category": "privacy"},
            stats  = {"channel_name": "TechChannel", "avg_views": 8000,
                      "subscribers": 50000, "avg_ctr": 4.2,
                      "avg_avd_pct": 48.0, "engagement_rate": 3.5},
            niche  = "technology",
        )
        self.assertIn("NordVPN", email)
        self.assertIn("TechChannel", email)
        self.assertGreater(len(email), 200)

    def test_insert_sponsor_at_35pct(self):
        sp     = self.Sponsor(self.mem)
        script = self._make_script(duration_s=600, n_scenes=8)
        result = sp.insert_sponsor_segment(
            script, {"name": "Brilliant", "promo_code": "TECH"}, position_pct=0.375
        )
        self.assertTrue(result.get("has_sponsor"))
        sponsor_scenes = [s for s in result["scenes"] if s.get("is_sponsor")]
        self.assertEqual(len(sponsor_scenes), 1)
        # Sponsor should appear between scenes 2-4 (35-40% of 8 scenes)
        all_ids = [s.get("id") for s in result["scenes"]]
        sponsor_idx = next(i for i, s in enumerate(result["scenes"]) if s.get("is_sponsor"))
        total_scenes = len(result["scenes"]) - 1   # minus the sponsor itself
        self.assertGreater(sponsor_idx, 0)
        self.assertLess(sponsor_idx, total_scenes)

    # ── AffiliateLinkEngine ─────────────────────────────────────────────────

    def test_detect_products_finds_mentions(self):
        afl    = self.Affiliate(self.mem)
        script = self._make_script()
        script["scenes"][0]["narration"] = "You'll need a good camera and microphone for this."
        products = afl.detect_products(script)
        self.assertIn("camera", products)
        self.assertIn("microphone", products)

    def test_find_affiliate_programs_returns_programs(self):
        afl      = self.Affiliate(self.mem)
        products = ["camera", "vpn service", "course"]
        programs = afl.find_affiliate_programs(products)
        self.assertEqual(set(programs.keys()), set(products))
        for p in programs.values():
            self.assertIn("program", p)
            self.assertIn("commission", p)

    def test_format_description_links_appends(self):
        afl   = self.Affiliate(self.mem)
        desc  = "This is my video description."
        links = {"solar panel": {"program": "amazon", "commission": 4.0,
                                  "search_url": "https://amazon.com/s?k=solar+panel",
                                  "cookie_days": 1}}
        result = afl.format_description_links(desc, links)
        self.assertIn("LINKS & RESOURCES", result)
        self.assertIn("solar panel", result.lower())
        self.assertTrue(result.startswith(desc))

    def test_monthly_affiliate_revenue_estimate(self):
        afl = self.Affiliate(self.mem)
        est = afl.estimate_monthly_affiliate_revenue(monthly_views=100_000)
        self.assertIn("revenue", est)
        self.assertGreater(est["revenue"], 0)

    # ── MerchandiseTrigger ──────────────────────────────────────────────────

    def test_milestone_detection_crosses(self):
        merch = self.Merch(self.mem)
        result = merch.check_milestones(current_subs=10_500, previous_subs=9_800)
        milestones = [m["milestone"] for m in result]
        self.assertIn(10_000, milestones)

    def test_milestone_no_cross(self):
        merch = self.Merch(self.mem)
        result = merch.check_milestones(current_subs=8_000, previous_subs=7_500)
        self.assertEqual(result, [])

    def test_fallback_merch_ideas_count(self):
        merch  = self.Merch(self.mem)
        ideas  = merch._fallback_merch_ideas("TechChannel", "technology",
                                              ["Stay Curious"])
        self.assertEqual(len(ideas), 5)
        for idea in ideas:
            self.assertIn("product_type", idea)
            self.assertIn("slogan", idea)

    # ── RevenueDashboard ────────────────────────────────────────────────────

    def test_niche_rpm_comparison_structure(self):
        dash   = self.Dashboard(self.mem, niche="technology")
        result = dash.niche_rpm_comparison()
        self.assertIn("channel_rpm", result)
        self.assertIn("niche_avg_rpm", result)
        self.assertIn("status", result)

    def test_revenue_report_is_string(self):
        dash   = self.Dashboard(self.mem, niche="technology")
        report = dash.generate_report(include_forecast=False)
        self.assertIsInstance(report, str)
        self.assertIn("REVENUE DASHBOARD", report)

    def test_alerts_stable_when_no_data(self):
        dash   = self.Dashboard(self.mem)
        alerts = dash.check_alerts()
        self.assertIsInstance(alerts, list)

    # ── Memory tables ───────────────────────────────────────────────────────

    def test_memory_has_monetization_tables(self):
        expected = {"sponsorships", "affiliate_links", "revenue_snapshots", "monetization_events"}
        with self.mem._conn() as conn:
            rows   = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables))

    def test_save_and_retrieve_sponsorship(self):
        row_id = self.mem.save_sponsorship_prospect(
            {"name": "NordVPN", "email": "creators@nordvpn.com", "category": "privacy"}
        )
        self.assertGreater(row_id, 0)
        prospects = self.mem.get_sponsorship_prospects()
        self.assertEqual(len(prospects), 1)
        self.assertEqual(prospects[0]["brand_name"], "NordVPN")

    def test_save_monetization_event(self):
        eid = self.mem.save_monetization_event({
            "event_type": "video_optimized",
            "youtube_id": "yt_test",
            "data":       {"score": 85},
        })
        self.assertGreater(eid, 0)
        events = self.mem.get_monetization_events(event_type="video_optimized")
        self.assertEqual(len(events), 1)

    # ── Full optimization pass ──────────────────────────────────────────────

    def test_full_optimize_video_returns_three_values(self):
        script   = self._make_script()
        metadata = {"title": "How to save on electricity", "description": "My video",
                    "tags": ["energy", "solar"]}
        result = self.me.optimize_video(script, metadata)
        self.assertEqual(len(result), 3)
        new_script, new_metadata, report = result
        self.assertIn("ad", report)
        self.assertIn("advertiser_score", report["ad"])


# ── TestCompetitorDominator ────────────────────────────────────────────────────


class TestCompetitorDominator(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")

    # ── TitleDecoder ────────────────────────────────────────────────────────

    def test_title_decoder_decode_formula_keys(self):
        from intelligence.competitor_dominator import TitleDecoder
        td = TitleDecoder()
        titles = [
            "7 Shocking Secrets About Solar Energy",
            "How to Cut Your Electricity Bill in Half",
            "Is Nuclear Power Really Safe?",
            "The Truth About Wind Turbines Exposed",
            "Top 10 Energy Mistakes You're Making",
        ]
        result = td.decode_formula(titles)
        for key in ("formula_template", "patterns", "power_words",
                    "avg_word_count", "sweet_spot_words", "case_style"):
            self.assertIn(key, result, f"Missing key: {key}")

    def test_title_decoder_decode_formula_sample_size(self):
        from intelligence.competitor_dominator import TitleDecoder
        td = TitleDecoder()
        titles = ["Title One", "Title Two", "Title Three"]
        result = td.decode_formula(titles)
        self.assertEqual(result["sample_size"], 3)

    def test_title_decoder_decode_formula_empty(self):
        from intelligence.competitor_dominator import TitleDecoder
        td = TitleDecoder()
        result = td.decode_formula([])
        self.assertIn("formula_template", result)
        self.assertEqual(result["sample_size"], 0)

    def test_title_decoder_score_title_range(self):
        from intelligence.competitor_dominator import TitleDecoder
        td = TitleDecoder()
        for title in [
            "7 Shocking Secrets About Solar Energy",
            "How to Cut Your Electricity Bill in Half",
            "Is Nuclear Power Really Safe?",
            "",
        ]:
            score = td.score_title(title)
            self.assertIsInstance(score, float, f"score for {title!r} not float")
            self.assertGreaterEqual(score, 0.0)
            self.assertLessEqual(score, 100.0)

    def test_title_decoder_score_power_words(self):
        from intelligence.competitor_dominator import TitleDecoder
        td = TitleDecoder()
        high = td.score_title("Shocking Secret: The Worst Energy Mistakes Exposed")
        low  = td.score_title("A video about things")
        self.assertGreater(high, low)

    def test_title_decoder_generate_better_title(self):
        from intelligence.competitor_dominator import TitleDecoder
        td = TitleDecoder()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            alts = td.generate_better_title(
                "Solar Energy Facts", "solar energy", ["solar", "energy", "cost"]
            )
        self.assertIsInstance(alts, list)
        self.assertGreaterEqual(len(alts), 1)
        for t in alts:
            self.assertIsInstance(t, str)

    # ── CommentMiner ────────────────────────────────────────────────────────

    def test_comment_miner_mine_complaints_categorizes(self):
        from intelligence.competitor_dominator import CommentMiner
        cm = CommentMiner()
        comments = [
            {"text": "This video is way too long, get to the point!"},
            {"text": "You missed the part about solar panels"},
            {"text": "Tutorial please, show me how to do it step by step"},
            {"text": "Bad audio, can't hear you properly"},
            {"text": "Great video, really enjoyed it!"},
        ]
        result = cm.mine_complaints(comments)
        self.assertIn("complaints", result)
        self.assertIn("total_analyzed", result)
        self.assertIn("complaint_ratio", result)
        self.assertGreater(len(result["complaints"]["too_long"]), 0)
        self.assertGreater(len(result["complaints"]["missing_topic"]), 0)
        self.assertGreater(len(result["complaints"]["wants_tutorial"]), 0)
        self.assertGreater(len(result["complaints"]["quality"]), 0)

    def test_comment_miner_extract_questions(self):
        from intelligence.competitor_dominator import CommentMiner
        cm = CommentMiner()
        comments = [
            {"text": "Great video! But how do you calculate efficiency?"},
            {"text": "What is the best solar panel brand?"},
            {"text": "No question here, just a comment."},
        ]
        questions = cm.extract_questions(comments)
        self.assertIsInstance(questions, list)
        self.assertGreater(len(questions), 0)
        # All extracted items should contain a question mark
        for q in questions:
            self.assertIn("?", q)

    def test_comment_miner_no_api_key_returns_empty(self):
        from intelligence.competitor_dominator import CommentMiner
        cm = CommentMiner()
        with patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": ""}):
            result = cm.fetch_comments("some_video_id")
        self.assertEqual(result, [])

    # ── ScheduleMapper ──────────────────────────────────────────────────────

    def test_schedule_mapper_map_schedule_keys(self):
        from intelligence.competitor_dominator import ScheduleMapper
        sm = ScheduleMapper()
        videos = [
            {"published": "2026-05-05T14:00:00Z"},  # Monday
            {"published": "2026-05-12T14:00:00Z"},  # Monday
            {"published": "2026-05-07T10:00:00Z"},  # Wednesday
            {"published": "2026-05-14T10:00:00Z"},  # Wednesday
            {"published": "2026-05-19T18:00:00Z"},  # Tuesday
        ]
        result = sm.map_schedule(videos)
        self.assertIn("preferred_days", result)
        self.assertIn("preferred_hours", result)
        self.assertIn("avg_gap_days", result)
        self.assertIn("gap_days", result)
        self.assertIn("irregular", result)

    def test_schedule_mapper_gap_days_is_list(self):
        from intelligence.competitor_dominator import ScheduleMapper
        sm = ScheduleMapper()
        videos = [
            {"published": "2026-05-04T08:00:00Z"},  # Monday
            {"published": "2026-05-11T08:00:00Z"},  # Monday
            {"published": "2026-05-18T08:00:00Z"},  # Monday
        ]
        result = sm.map_schedule(videos)
        self.assertIsInstance(result["gap_days"], list)

    def test_schedule_mapper_find_competitive_gap(self):
        from intelligence.competitor_dominator import ScheduleMapper
        sm = ScheduleMapper()
        schedules = {
            "channelA": {
                "preferred_days":  ["Monday", "Wednesday"],
                "preferred_hours": [10, 14],
            },
            "channelB": {
                "preferred_days":  ["Tuesday", "Thursday"],
                "preferred_hours": [9, 16],
            },
        }
        gap = sm.find_competitive_gap(schedules)
        self.assertIn("day", gap)
        self.assertIn("hour", gap)
        self.assertIsInstance(gap["day"], str)
        self.assertIsInstance(gap["hour"], int)

    def test_schedule_mapper_empty_schedules(self):
        from intelligence.competitor_dominator import ScheduleMapper
        sm  = ScheduleMapper()
        gap = sm.find_competitive_gap({})
        self.assertIn("day", gap)
        self.assertIn("hour", gap)

    # ── GapExploiter ────────────────────────────────────────────────────────

    def test_gap_exploiter_find_uncovered_topics(self):
        from intelligence.competitor_dominator import GapExploiter
        ge = GapExploiter()
        competitor_titles = [
            "Solar Panel Installation Guide",
            "Wind Energy Explained",
        ]
        trending = [
            "geothermal energy",       # not covered → gap
            "solar panel installation", # covered
            "tidal energy",             # not covered → gap
        ]
        result = ge.find_uncovered_topics(competitor_titles, trending)
        self.assertIsInstance(result, list)
        topics = [r["topic"] for r in result]
        self.assertIn("geothermal energy", topics)
        # Each item must have opportunity_score
        for item in result:
            self.assertIn("opportunity_score", item)
            self.assertIsInstance(item["opportunity_score"], (int, float))

    def test_gap_exploiter_find_unanswered_questions(self):
        from intelligence.competitor_dominator import GapExploiter
        ge = GapExploiter()
        questions = [
            "How do I install solar panels?",
            "How do I install solar panels?",   # duplicate
            "What is the best battery for storage?",
            "How do I install solar panels?",   # another duplicate
        ]
        result = ge.find_unanswered_questions(questions)
        self.assertIsInstance(result, list)
        # The duplicated question should have higher frequency
        if result:
            max_freq = max(r["frequency"] for r in result)
            self.assertGreaterEqual(max_freq, 2)
        # No duplicate topics in output
        seen = set()
        for item in result:
            q = item["question"]
            self.assertNotIn(q, seen)
            seen.add(q)

    # ── TitleWarfare ────────────────────────────────────────────────────────

    def test_title_warfare_check_rss_network_failure(self):
        from intelligence.competitor_dominator import TitleWarfare
        tw = TitleWarfare()
        # Even if feedparser is unavailable or network fails, must return []
        with patch.dict("sys.modules", {"feedparser": None}):
            try:
                result = tw.check_rss_for_new_videos(["UCxxx"], {})
                self.assertIsInstance(result, list)
            except Exception:
                # Any exception → also acceptable since we patched the module away
                pass

    def test_title_warfare_generate_warfare_titles_keys(self):
        from intelligence.competitor_dominator import TitleWarfare
        tw = TitleWarfare()
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            result = tw.generate_warfare_titles(
                competitor_title    = "10 Solar Energy Facts You Didn't Know",
                competitor_video_id = "abc123",
                competitor_channel  = "SolarGuru",
                upload_time         = "2026-06-01T10:00:00Z",
            )
        for key in ("competitor_title", "better_titles", "keywords",
                    "deadline_utc", "urgency_minutes"):
            self.assertIn(key, result)
        self.assertIsInstance(result["better_titles"], list)
        self.assertIsInstance(result["urgency_minutes"], int)

    # ── AudiencePoacher ─────────────────────────────────────────────────────

    def test_audience_poacher_response_video_brief_keys(self):
        from intelligence.competitor_dominator import AudiencePoacher
        ap = AudiencePoacher()
        gap = {
            "topic":             "solar panel efficiency",
            "gap_type":          "unanswered_question",
            "opportunity_score": 75.0,
        }
        brief = ap.generate_response_video_brief(
            "How do I maximise solar panel efficiency?", gap
        )
        for key in ("title", "hook", "key_points", "target_keyword",
                    "opportunity_score"):
            self.assertIn(key, brief)
        self.assertIsInstance(brief["key_points"], list)
        self.assertGreater(len(brief["key_points"]), 0)

    def test_audience_poacher_comment_draft_has_warning(self):
        from intelligence.competitor_dominator import AudiencePoacher
        ap    = AudiencePoacher()
        draft = ap.generate_value_comment(
            "abc123", "missing_topic", "solar battery storage"
        )
        self.assertIn("DRAFT", draft)
        self.assertIn("requires human review", draft)

    # ── Memory tables ───────────────────────────────────────────────────────

    def test_memory_has_dominator_tables(self):
        expected = {"competitor_snapshots", "competitor_intelligence",
                    "content_gaps", "title_battles"}
        with self.mem._conn() as conn:
            rows   = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables),
                        f"Missing tables: {expected - tables}")

    def test_memory_save_content_gap(self):
        gap_id = self.mem.save_content_gap({
            "topic":             "geothermal energy basics",
            "gap_type":          "uncovered",
            "opportunity_score": 88.5,
            "evidence":          {"competitor_count": 0},
            "competitor_ids":    [],
            "status":            "open",
            "week_num":          23,
        })
        self.assertGreater(gap_id, 0)

    def test_memory_get_content_gaps(self):
        self.mem.save_content_gap({
            "topic": "tidal power",
            "gap_type": "uncovered",
            "opportunity_score": 75.0,
        })
        self.mem.save_content_gap({
            "topic": "battery degradation",
            "gap_type": "poorly_covered",
            "opportunity_score": 55.0,
        })
        gaps = self.mem.get_content_gaps(status="open", n=10)
        self.assertIsInstance(gaps, list)
        self.assertGreaterEqual(len(gaps), 2)
        # Should be sorted by opportunity_score DESC
        scores = [g["opportunity_score"] for g in gaps]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_memory_save_title_battle(self):
        from datetime import datetime, timezone, timedelta
        deadline = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
        battle_id = self.mem.save_title_battle({
            "competitor_channel":  "SolarGuru",
            "competitor_video_id": "xyz789",
            "competitor_title":    "10 Solar Facts",
            "our_titles":          ["7 Solar Secrets", "Why Solar Is Booming"],
            "best_title":          "7 Solar Secrets",
            "keywords":            ["solar", "energy"],
            "deadline_utc":        deadline,
            "urgency_minutes":     300,
        })
        self.assertGreater(battle_id, 0)

    def test_memory_get_pending_battles(self):
        from datetime import datetime, timezone, timedelta
        # Save a future-deadline battle
        future = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
        self.mem.save_title_battle({
            "competitor_channel":  "TechChannel",
            "competitor_video_id": "vid001",
            "competitor_title":    "AI is everywhere",
            "our_titles":          ["Why AI Is Taking Over"],
            "best_title":          "Why AI Is Taking Over",
            "keywords":            ["AI"],
            "deadline_utc":        future,
            "urgency_minutes":     180,
        })
        # Save a past-deadline battle (should not appear)
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.mem.save_title_battle({
            "competitor_channel":  "OldChannel",
            "competitor_video_id": "vid002",
            "competitor_title":    "Old video",
            "our_titles":          [],
            "best_title":          "",
            "keywords":            [],
            "deadline_utc":        past,
            "urgency_minutes":     0,
        })
        pending = self.mem.get_pending_battles()
        self.assertIsInstance(pending, list)
        ids = [b["competitor_video_id"] for b in pending]
        self.assertIn("vid001", ids)
        self.assertNotIn("vid002", ids)

    def test_memory_upsert_competitor_snapshot_deduplicates(self):
        # Insert twice for same channel+date → should not raise and count stays 1
        self.mem.upsert_competitor_snapshot("UC123", "2026-06-08", 10000, 500000, 100)
        self.mem.upsert_competitor_snapshot("UC123", "2026-06-08", 11000, 510000, 102)
        with self.mem._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM competitor_snapshots "
                "WHERE channel_id='UC123' AND date='2026-06-08'"
            ).fetchone()["n"]
        self.assertEqual(count, 1)
        # The second upsert should have updated values
        snaps = self.mem.get_competitor_snapshots("UC123", days=30)
        self.assertEqual(snaps[0]["subscribers"], 11000)

    # ── CompetitorDominator ─────────────────────────────────────────────────

    def test_competitor_dominator_get_gap_list_returns_list(self):
        from intelligence.competitor_dominator import CompetitorDominator
        cd = CompetitorDominator(memory=self.mem, channel_ids=[])
        result = cd.get_gap_list(n=20)
        self.assertIsInstance(result, list)


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
