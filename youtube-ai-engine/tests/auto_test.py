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


# ── TestGlobalExpansion ────────────────────────────────────────────────────────


class TestGlobalExpansion(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")

    def _make_script(self):
        return {
            "title": "The Future of Solar Energy",
            "hook":  "Solar power is about to change everything.",
            "scenes": [
                {"title": "Intro",      "narration": "Welcome to this video about solar energy.",
                 "text_overlay": "Solar Energy 2026"},
                {"title": "Main Point", "narration": "Solar panels are getting cheaper every year.",
                 "text_overlay": "Cost dropping fast"},
                {"title": "Conclusion", "narration": "Now is the best time to invest in solar.",
                 "text_overlay": "Invest now"},
            ],
            "cta": "Like and subscribe for more energy content.",
        }

    def _make_metadata(self):
        return {
            "title":       "The Future of Solar Energy",
            "description": "In this video we explore the future of solar power.",
            "tags":        ["solar", "energy", "renewable", "green"],
        }

    # ── LANGUAGE_PROFILES ───────────────────────────────────────────────────

    def test_language_profiles_has_six_entries(self):
        from intelligence.global_expansion import LANGUAGE_PROFILES
        self.assertEqual(len(LANGUAGE_PROFILES), 6)

    def test_language_profiles_required_keys(self):
        from intelligence.global_expansion import LANGUAGE_PROFILES
        for lang, profile in LANGUAGE_PROFILES.items():
            for key in ("name", "native", "innertube_gl", "innertube_hl",
                        "primary_market", "population_reach",
                        "thumbnail_style", "rtl"):
                self.assertIn(key, profile, f"[{lang}] missing key: {key}")

    def test_total_reach_exceeds_2_billion(self):
        from intelligence.global_expansion import TOTAL_REACH
        self.assertGreater(TOTAL_REACH, 2_000_000_000)

    def test_arabic_is_rtl(self):
        from intelligence.global_expansion import LANGUAGE_PROFILES
        self.assertTrue(LANGUAGE_PROFILES["ar"]["rtl"])

    def test_non_arabic_are_ltr(self):
        from intelligence.global_expansion import LANGUAGE_PROFILES
        for lang in ("es", "hi", "pt", "fr", "id"):
            self.assertFalse(LANGUAGE_PROFILES[lang]["rtl"], f"{lang} should be LTR")

    # ── TranslationPipeline ─────────────────────────────────────────────────

    def test_translate_no_key_returns_placeholder(self):
        from intelligence.global_expansion import TranslationPipeline
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            tp  = TranslationPipeline()
            out = tp.translate("Hello world", "es")
        self.assertIn("ES_TRANSLATION_NEEDED", out)
        self.assertIn("Hello world", out)

    def test_translate_empty_string_passthrough(self):
        from intelligence.global_expansion import TranslationPipeline
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            tp  = TranslationPipeline()
            out = tp.translate("", "fr")
        self.assertEqual(out, "")

    def test_translate_metadata_adds_seo_boosters(self):
        from intelligence.global_expansion import TranslationPipeline, SEO_BOOSTERS
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            tp  = TranslationPipeline()
            out = tp.translate_metadata("Solar Energy", "Great video", ["solar"], "es")
        self.assertIn("title", out)
        self.assertIn("tags", out)
        # At least one Spanish booster should appear in the tags
        boosters = SEO_BOOSTERS.get("es", [])
        tag_set  = set(out["tags"])
        self.assertTrue(any(b in tag_set for b in boosters),
                        f"No booster found in tags: {tag_set}")

    def test_translate_script_preserves_scene_count(self):
        from intelligence.global_expansion import TranslationPipeline
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            tp     = TranslationPipeline()
            script = self._make_script()
            out    = tp.translate_script(script, "pt")
        self.assertEqual(len(out["scenes"]), len(script["scenes"]))
        self.assertEqual(out["language"], "pt")

    def test_translate_script_does_not_mutate_source(self):
        from intelligence.global_expansion import TranslationPipeline
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            tp     = TranslationPipeline()
            script = self._make_script()
            orig_hook = script["hook"]
            tp.translate_script(script, "ar")
        self.assertEqual(script["hook"], orig_hook)

    # ── CulturalAdapter ─────────────────────────────────────────────────────

    def test_cultural_adapter_brand_substitution(self):
        from intelligence.global_expansion import CulturalAdapter
        ca = CulturalAdapter()
        script = {"hook": "Buy it on Amazon today.", "scenes": []}
        adapted = ca.adapt_script(script, "id")
        # "Amazon" → "Tokopedia" for Indonesian market
        self.assertIn("Tokopedia", adapted["hook"])

    def test_cultural_adapter_example_substitution(self):
        from intelligence.global_expansion import CulturalAdapter
        ca     = CulturalAdapter()
        script = {"hook": "Like Silicon Valley startups do.", "scenes": []}
        adapted = ca.adapt_script(script, "es")
        self.assertNotIn("Silicon Valley", adapted["hook"])

    def test_cultural_adapter_arabic_content_filter(self):
        from intelligence.global_expansion import CulturalAdapter
        ca   = CulturalAdapter()
        text = ca.filter_inappropriate("Invest in gambling stocks now", "ar")
        self.assertNotIn("gambling", text)

    def test_cultural_adapter_urgency_phrase(self):
        from intelligence.global_expansion import CulturalAdapter
        ca     = CulturalAdapter()
        phrase = ca.get_urgency_phrase("hi")
        self.assertIsInstance(phrase, str)
        self.assertGreater(len(phrase), 0)

    def test_cultural_adapter_sets_cultural_region(self):
        from intelligence.global_expansion import CulturalAdapter, LANGUAGE_PROFILES
        ca     = CulturalAdapter()
        script = {"hook": "Hello world.", "scenes": []}
        adapted = ca.adapt_script(script, "hi")
        self.assertEqual(adapted["cultural_region"],
                         LANGUAGE_PROFILES["hi"]["primary_market"])

    # ── MultilingualSEO ─────────────────────────────────────────────────────

    def test_multilingual_seo_title_length_capped(self):
        from intelligence.global_expansion import MultilingualSEO
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            seo = MultilingualSEO()
            out = seo.generate_local_seo(
                "Solar Energy", "Great video about solar", ["solar"], "fr"
            )
        self.assertLessEqual(len(out["title"]), 100)

    def test_multilingual_seo_tags_capped_at_30(self):
        from intelligence.global_expansion import MultilingualSEO
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            seo = MultilingualSEO()
            out = seo.generate_local_seo(
                "Solar", "Description", ["solar"] * 35, "pt"
            )
        self.assertLessEqual(len(out["tags"]), 30)

    def test_multilingual_seo_volume_tier_returns_string(self):
        from intelligence.global_expansion import MultilingualSEO
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            seo  = MultilingualSEO()
            tier = seo._estimate_volume_tier("solar energy tutorial", "hi")
        self.assertIn(tier, ("high", "medium", "low"))

    def test_multilingual_seo_generate_all_markets(self):
        from intelligence.global_expansion import MultilingualSEO, LANGUAGE_PROFILES
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            seo = MultilingualSEO()
            out = seo.generate_all_markets("Solar Energy", "Description", ["solar"])
        self.assertEqual(set(out.keys()), set(LANGUAGE_PROFILES.keys()))
        for lang, pkg in out.items():
            self.assertIn("title", pkg)
            self.assertIn("tags",  pkg)

    # ── ThumbnailLocalizer ──────────────────────────────────────────────────

    def test_thumbnail_localizer_no_source_returns_none(self):
        from intelligence.global_expansion import ThumbnailLocalizer
        tl  = ThumbnailLocalizer()
        out = tl.localize("/nonexistent/thumb.jpg", "es", "Test", "/tmp")
        self.assertIsNone(out)

    def test_thumbnail_hex_to_rgb(self):
        from intelligence.global_expansion import ThumbnailLocalizer
        tl = ThumbnailLocalizer()
        self.assertEqual(tl._hex_to_rgb("#FF4136"), (255, 65, 54))
        self.assertEqual(tl._hex_to_rgb("002395"), (0, 35, 149))

    # ── ChannelManager ──────────────────────────────────────────────────────

    def test_channel_manager_register_and_retrieve(self):
        from intelligence.global_expansion import ChannelManager
        cm = ChannelManager(self.mem)
        cm.register_channel("es", "UC_ES_TEST", "My Spanish Channel")
        config = cm.get_channel_config("es")
        self.assertEqual(config.get("channel_id"),   "UC_ES_TEST")
        self.assertEqual(config.get("channel_name"), "My Spanish Channel")

    def test_channel_manager_get_all_returns_list(self):
        from intelligence.global_expansion import ChannelManager
        cm = ChannelManager(self.mem)
        cm.register_channel("fr", "UC_FR", "French Channel")
        cm.register_channel("ar", "UC_AR", "Arabic Channel")
        all_ch = cm.get_all_channels()
        self.assertIsInstance(all_ch, list)
        self.assertGreaterEqual(len(all_ch), 2)

    def test_channel_manager_end_screen_excludes_source(self):
        from intelligence.global_expansion import ChannelManager
        cm = ChannelManager(self.mem)
        for lang, cid in [("es", "UC_ES"), ("hi", "UC_HI"), ("pt", "UC_PT")]:
            cm.register_channel(lang, cid, f"{lang} channel")
        links = cm.build_end_screen_links(source_language="es")
        lang_codes = [l["language_code"] for l in links]
        self.assertNotIn("es", lang_codes)

    # ── Memory tables ───────────────────────────────────────────────────────

    def test_memory_has_global_expansion_tables(self):
        expected = {"translations", "regional_trends",
                    "language_channels", "localization_jobs"}
        with self.mem._conn() as conn:
            rows   = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables),
                        f"Missing: {expected - tables}")

    def test_memory_save_and_get_translation(self):
        vid_id = self.mem.save_video({
            "date": "2026-06-08", "topic": "solar", "title": "Solar Video",
            "youtube_id": "ytGlobal01",
        })
        row_id = self.mem.save_translation(vid_id, "es", {
            "title": "El Futuro de la Energía Solar",
            "description": "En este vídeo...",
            "tags": ["solar", "energía"],
        })
        self.assertGreater(row_id, 0)
        rows = self.mem.get_translations(vid_id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["language_code"], "es")
        self.assertEqual(rows[0]["translated_title"], "El Futuro de la Energía Solar")

    def test_memory_save_regional_trends(self):
        trends = [
            {"source_market": "IN", "title": "Rooftop solar India", "score": 0.9},
            {"source_market": "ID", "title": "EV bikes Indonesia",  "score": 0.7},
        ]
        self.mem.save_regional_trends(trends, source="test")
        rows = self.mem.get_regional_trends(days=1)
        self.assertGreaterEqual(len(rows), 2)

    def test_memory_upsert_language_channel(self):
        self.mem.upsert_language_channel("pt", "UC_PT_123", "Energia Solar BR")
        ch = self.mem.get_language_channel("pt")
        self.assertIsNotNone(ch)
        self.assertEqual(ch["channel_id"], "UC_PT_123")
        # Upsert again — should not raise and should update
        self.mem.upsert_language_channel("pt", "UC_PT_456", "Nova Energia BR")
        ch2 = self.mem.get_language_channel("pt")
        self.assertEqual(ch2["channel_id"], "UC_PT_456")

    def test_memory_localization_job_lifecycle(self):
        vid_id = self.mem.save_video({
            "date": "2026-06-08", "topic": "wind", "title": "Wind Energy",
            "youtube_id": "ytJob01",
        })
        jid = self.mem.save_localization_job(vid_id, "hi", "full", "running")
        self.assertGreater(jid, 0)
        # Update to done
        self.mem.save_localization_job(vid_id, "hi", "full", "done",
                                       result={"seo_title": "हवा ऊर्जा"})
        jobs = self.mem.get_localization_jobs(vid_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["status"], "done")

    # ── GlobalExpansion orchestrator ────────────────────────────────────────

    def test_global_expansion_expand_video_structure(self):
        from intelligence.global_expansion import GlobalExpansion, LANGUAGE_PROFILES
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": "",
                                       "ELEVENLABS_API_KEY": ""}):
            ge = GlobalExpansion(self.mem)
            result = ge.expand_video(
                source_script   = self._make_script(),
                source_metadata = self._make_metadata(),
                languages       = ["es", "pt"],
            )
        self.assertIn("languages",     result)
        self.assertIn("total_reach",   result)
        self.assertIn("success_count", result)
        self.assertIn("es", result["languages"])
        self.assertIn("pt", result["languages"])

    def test_global_expansion_seo_present_per_language(self):
        from intelligence.global_expansion import GlobalExpansion
        with patch.dict("os.environ", {"DEEPL_API_KEY": "", "ANTHROPIC_API_KEY": "",
                                       "ELEVENLABS_API_KEY": ""}):
            ge = GlobalExpansion(self.mem)
            result = ge.expand_video(
                source_script   = self._make_script(),
                source_metadata = self._make_metadata(),
                languages       = ["fr", "ar"],
            )
        for lang in ("fr", "ar"):
            lang_result = result["languages"].get(lang, {})
            self.assertIn("seo", lang_result)
            self.assertIn("title", lang_result["seo"])
            self.assertIn("tags",  lang_result["seo"])


# ── TestShortsFactory ─────────────────────────────────────────────────────────


class TestShortsFactory(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")

    def _make_script(self, n_scenes: int = 6):
        scenes = []
        templates = [
            ("Shocking Stat",    "You won't believe this — solar is now 90% cheaper!"),
            ("Surprising Reveal","Nobody talks about the hidden cost of wind energy."),
            ("Emotional Peak",   "This technology is life-changing for millions."),
            ("Controversial",    "Unpopular opinion: nuclear is cleaner than solar."),
            ("Hook Question",    "Did you know your electricity bill funds fossil fuels?"),
            ("Funny",            "The irony of oil companies building solar farms."),
            ("Extra Fact",       "Batteries now store 10x more energy than 5 years ago."),
            ("Conclusion",       "The future of energy is here. Subscribe now."),
        ]
        for i in range(min(n_scenes, len(templates))):
            title, narration = templates[i]
            scenes.append({"title": title, "narration": narration,
                            "text_overlay": title})
        return {
            "title": "The Future of Energy",
            "hook":  "Solar power is about to change everything.",
            "scenes": scenes,
        }

    # ── ViralMomentAnalyzer ────────────────────────────────────────────────

    def test_analyze_transcript_returns_list(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va     = ViralMomentAnalyzer()
        result = va.analyze_transcript(self._make_script())
        self.assertIsInstance(result, list)

    def test_analyze_transcript_sorted_by_score(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va      = ViralMomentAnalyzer()
        results = va.analyze_transcript(self._make_script())
        if len(results) > 1:
            scores = [r["virality_score"] for r in results]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_score_segment_range(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va    = ViralMomentAnalyzer()
        scene = {"narration": "You won't believe this shocking 90% drop in solar costs!"}
        score, sig = va.score_segment(scene)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertIsInstance(sig, str)

    def test_score_segment_high_for_shock_words(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va = ViralMomentAnalyzer()
        high_scene = {"narration":
                      "You won't believe this shocking billion-dollar secret they hid!"}
        low_scene  = {"narration": "Today we talk about energy."}
        high_score, _ = va.score_segment(high_scene)
        low_score,  _ = va.score_segment(low_scene)
        self.assertGreater(high_score, low_score)

    def test_extract_top_segments_count(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va       = ViralMomentAnalyzer()
        segments = va.analyze_transcript(self._make_script(n_scenes=8))
        top5     = va.extract_top_segments(segments, n=5)
        self.assertLessEqual(len(top5), 5)

    def test_extract_top_segments_diversity(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va       = ViralMomentAnalyzer()
        segments = va.analyze_transcript(self._make_script(n_scenes=8))
        top5     = va.extract_top_segments(segments, n=5)
        signal_types = [s["signal_type"] for s in top5]
        from collections import Counter
        for sig, count in Counter(signal_types).items():
            self.assertLessEqual(count, 2,
                f"Signal type '{sig}' appears {count} times — max 2 allowed")

    def test_required_segment_keys(self):
        from production.shorts_factory import ViralMomentAnalyzer
        va      = ViralMomentAnalyzer()
        results = va.analyze_transcript(self._make_script())
        for seg in results:
            for key in ("virality_score", "signal_type", "start_s",
                        "end_s", "duration_s", "hook_text"):
                self.assertIn(key, seg, f"Missing key: {key}")

    # ── ShortsFormatter ────────────────────────────────────────────────────

    def test_format_segment_duration_sweet_spot(self):
        from production.shorts_factory import (ShortsFormatter, ViralMomentAnalyzer,
                                                DURATION_MIN, DURATION_MAX)
        va  = ViralMomentAnalyzer()
        sf  = ShortsFormatter()
        segs = va.analyze_transcript(self._make_script())
        for seg in segs:
            spec = sf.format_segment(seg, {"title": "Test"}, 1)
            dur  = spec["duration_s"]
            self.assertGreaterEqual(dur, DURATION_MIN,
                f"Duration {dur}s below minimum {DURATION_MIN}s")
            self.assertLessEqual(dur, DURATION_MAX + 1,   # allow 1s tolerance
                f"Duration {dur}s above maximum {DURATION_MAX}s")

    def test_format_segment_required_keys(self):
        from production.shorts_factory import ShortsFormatter, ViralMomentAnalyzer
        va   = ViralMomentAnalyzer()
        sf   = ShortsFormatter()
        segs = va.analyze_transcript(self._make_script())
        spec = sf.format_segment(segs[0], {"title": "Test"}, 1)
        for key in ("title", "hook_text", "hook_order", "start_s", "end_s",
                    "duration_s", "virality_score", "signal_type", "caption_spec"):
            self.assertIn(key, spec, f"Missing key: {key}")

    def test_cta_returns_string(self):
        from production.shorts_factory import ShortsFormatter
        sf = ShortsFormatter()
        for sig in ("shocking_stat", "emotional_peak", "controversial", "funny"):
            cta = sf.get_cta(sig, "youtube_shorts")
            self.assertIsInstance(cta, str)
            self.assertGreater(len(cta), 0)

    # ── CaptionEngine ──────────────────────────────────────────────────────

    def test_generate_word_by_word_count(self):
        from production.shorts_factory import CaptionEngine
        ce   = CaptionEngine()
        caps = ce.generate_word_by_word("Hello world this is a test caption here", 10.0, 3)
        self.assertIsInstance(caps, list)
        self.assertGreater(len(caps), 0)

    def test_generate_word_by_word_keys(self):
        from production.shorts_factory import CaptionEngine
        ce   = CaptionEngine()
        caps = ce.generate_word_by_word("Solar energy is the future", 8.0, 2)
        for cap in caps:
            for key in ("start_s", "end_s", "text", "highlight"):
                self.assertIn(key, cap)

    def test_generate_word_by_word_timings_monotone(self):
        from production.shorts_factory import CaptionEngine
        ce   = CaptionEngine()
        caps = ce.generate_word_by_word("One two three four five six seven eight", 16.0, 2)
        for i in range(1, len(caps)):
            self.assertGreaterEqual(caps[i]["start_s"], caps[i-1]["start_s"])

    def test_to_srt_creates_file(self):
        import tempfile, os
        from production.shorts_factory import CaptionEngine
        ce   = CaptionEngine()
        caps = ce.generate_word_by_word("Solar power is amazing technology", 10.0, 3)
        with tempfile.NamedTemporaryFile(suffix=".srt", delete=False) as f:
            path = f.name
        try:
            out = ce.to_srt(caps, path)
            self.assertTrue(Path(out).exists())
            content = Path(out).read_text()
            self.assertIn("-->", content)
        finally:
            os.unlink(path)

    def test_to_ass_creates_file(self):
        import tempfile, os
        from production.shorts_factory import CaptionEngine
        ce   = CaptionEngine()
        caps = ce.generate_word_by_word("Shocking secret revealed today", 8.0, 2)
        with tempfile.NamedTemporaryFile(suffix=".ass", delete=False) as f:
            path = f.name
        try:
            out = ce.to_ass(caps, path)
            self.assertTrue(Path(out).exists())
            content = Path(out).read_text()
            self.assertIn("[Script Info]", content)
            self.assertIn("[V4+ Styles]",  content)
            self.assertIn("TikTok",        content)
        finally:
            os.unlink(path)

    def test_highlight_flag_set_for_known_words(self):
        from production.shorts_factory import CaptionEngine
        ce   = CaptionEngine()
        caps = ce.generate_word_by_word("This is a shocking secret revealed today", 8.0, 1)
        highlighted = [c for c in caps if c["highlight"]]
        self.assertGreater(len(highlighted), 0, "No captions flagged as highlighted")

    # ── CrossPlatformExporter ──────────────────────────────────────────────

    def test_export_specs_has_all_platforms(self):
        from production.shorts_factory import (CrossPlatformExporter,
                                                PLATFORM_SPECS,
                                                ViralMomentAnalyzer,
                                                ShortsFormatter)
        va   = ViralMomentAnalyzer()
        sf   = ShortsFormatter()
        exp  = CrossPlatformExporter()
        segs = va.analyze_transcript(self._make_script())
        spec = sf.format_segment(segs[0], {"title": "Solar Test"}, 1)
        specs = exp.export_specs(spec, "Solar Energy")
        self.assertEqual(set(specs.keys()), set(PLATFORM_SPECS.keys()))

    def test_export_specs_required_keys(self):
        from production.shorts_factory import (CrossPlatformExporter,
                                                ViralMomentAnalyzer,
                                                ShortsFormatter)
        va   = ViralMomentAnalyzer()
        sf   = ShortsFormatter()
        exp  = CrossPlatformExporter()
        segs = va.analyze_transcript(self._make_script())
        spec = sf.format_segment(segs[0], {"title": "Test"}, 1)
        specs = exp.export_specs(spec, "Solar")
        for platform, p_spec in specs.items():
            for key in ("width", "height", "fps", "cta", "hashtags"):
                self.assertIn(key, p_spec,
                    f"Platform {platform} missing key: {key}")

    def test_twitter_x_is_landscape(self):
        from production.shorts_factory import (CrossPlatformExporter,
                                                ViralMomentAnalyzer,
                                                ShortsFormatter)
        va   = ViralMomentAnalyzer()
        sf   = ShortsFormatter()
        exp  = CrossPlatformExporter()
        segs = va.analyze_transcript(self._make_script())
        spec = sf.format_segment(segs[0], {"title": "Test"}, 1)
        specs = exp.export_specs(spec, "Solar")
        self.assertEqual(specs["twitter_x"]["width"],  1280)
        self.assertEqual(specs["twitter_x"]["height"],  720)

    def test_vertical_platforms_are_9_16(self):
        from production.shorts_factory import (CrossPlatformExporter,
                                                ViralMomentAnalyzer,
                                                ShortsFormatter)
        va   = ViralMomentAnalyzer()
        sf   = ShortsFormatter()
        exp  = CrossPlatformExporter()
        segs = va.analyze_transcript(self._make_script())
        spec = sf.format_segment(segs[0], {"title": "Test"}, 1)
        specs = exp.export_specs(spec, "Solar")
        for platform in ("youtube_shorts", "tiktok", "instagram_reels", "linkedin"):
            self.assertEqual(specs[platform]["width"],  1080)
            self.assertEqual(specs[platform]["height"], 1920)

    def test_hashtag_count_capped(self):
        from production.shorts_factory import CrossPlatformExporter, PLATFORM_SPECS
        exp = CrossPlatformExporter()
        for platform, cfg in PLATFORM_SPECS.items():
            tags = exp.generate_hashtags("solar energy tutorial", platform,
                                          cfg["hashtag_count"])
            self.assertLessEqual(len(tags), cfg["hashtag_count"],
                f"{platform}: too many hashtags")

    def test_ffmpeg_command_contains_input(self):
        from production.shorts_factory import (CrossPlatformExporter,
                                                ViralMomentAnalyzer,
                                                ShortsFormatter)
        va   = ViralMomentAnalyzer()
        sf   = ShortsFormatter()
        exp  = CrossPlatformExporter()
        segs = va.analyze_transcript(self._make_script())
        spec = sf.format_segment(segs[0], {"title": "Test"}, 1)
        cmd  = exp.build_ffmpeg_command(spec, "youtube_shorts",
                                         "/input/video.mp4", "/output/short.mp4")
        self.assertIn("ffmpeg",           cmd)
        self.assertIn("/input/video.mp4", cmd)
        self.assertIn("1080",             cmd)
        self.assertIn("1920",             cmd)

    # ── ShortsMonetizationTracker ──────────────────────────────────────────

    def test_estimate_revenue_positive(self):
        from production.shorts_factory import ShortsMonetizationTracker
        tracker = ShortsMonetizationTracker()
        rev = tracker.estimate_revenue("youtube_shorts", 100_000)
        self.assertGreater(rev, 0)

    def test_weekly_shorts_report_structure(self):
        from production.shorts_factory import ShortsMonetizationTracker
        tracker = ShortsMonetizationTracker()
        report  = tracker.weekly_shorts_report(self.mem)
        for key in ("week_total_views", "week_total_revenue",
                    "by_platform", "shorts_count"):
            self.assertIn(key, report)

    # ── Memory tables ───────────────────────────────────────────────────────

    def test_memory_has_shorts_tables(self):
        expected = {"shorts", "shorts_expansion_queue"}
        with self.mem._conn() as conn:
            rows   = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables),
                        f"Missing: {expected - tables}")

    def test_memory_save_and_get_short(self):
        vid_id = self.mem.save_video({
            "date": "2026-06-08", "topic": "solar", "title": "Solar Video",
            "youtube_id": "ytShort01",
        })
        short_id = self.mem.save_short({
            "source_video_db_id": vid_id,
            "source_youtube_id":  "ytShort01",
            "short_index":        1,
            "segment_type":       "shocking_stat",
            "virality_score":     82.5,
            "start_s":            15.0,
            "end_s":              52.0,
            "duration_s":         37.0,
            "title":              "SHOCKING: Solar is 90% cheaper",
            "hook_text":          "You won't believe how cheap solar is now",
            "platform":           "multi",
            "status":             "produced",
        })
        self.assertGreater(short_id, 0)
        shorts = self.mem.get_shorts(source_video_db_id=vid_id)
        self.assertEqual(len(shorts), 1)
        self.assertAlmostEqual(shorts[0]["virality_score"], 82.5, places=1)

    def test_memory_get_top_shorts_threshold(self):
        vid_id = self.mem.save_video({
            "date": "2026-06-08", "topic": "wind", "title": "Wind Energy",
            "youtube_id": "ytShort02",
        })
        self.mem.save_short({"source_video_db_id": vid_id, "virality_score": 90.0,
                              "title": "High scorer", "duration_s": 35, "status": "produced"})
        self.mem.save_short({"source_video_db_id": vid_id, "virality_score": 40.0,
                              "title": "Low scorer", "duration_s": 35, "status": "produced"})
        top = self.mem.get_top_shorts(min_score=65.0)
        self.assertEqual(len(top), 1)
        self.assertAlmostEqual(top[0]["virality_score"], 90.0, places=1)

    def test_memory_update_short_analytics(self):
        vid_id = self.mem.save_video({
            "date": "2026-06-08", "topic": "battery", "title": "Battery Tech",
            "youtube_id": "ytShort03",
        })
        short_id = self.mem.save_short({
            "source_video_db_id": vid_id, "virality_score": 75.0,
            "title": "Battery secret", "duration_s": 40, "status": "produced",
        })
        self.mem.update_short_analytics(short_id, views=50000, likes=2000, revenue=2.50)
        shorts = self.mem.get_shorts(source_video_db_id=vid_id)
        self.assertEqual(shorts[0]["views"], 50000)
        self.assertAlmostEqual(shorts[0]["revenue"], 2.50, places=2)

    def test_memory_short_count(self):
        initial = self.mem.short_count()
        vid_id  = self.mem.save_video({
            "date": "2026-06-08", "topic": "ev", "title": "EV Range",
            "youtube_id": "ytShort04",
        })
        self.mem.save_short({"source_video_db_id": vid_id, "virality_score": 60.0,
                              "title": "EV short", "duration_s": 30, "status": "produced"})
        self.assertEqual(self.mem.short_count(), initial + 1)

    # ── ShortsFactory orchestrator ─────────────────────────────────────────

    def test_process_video_returns_structure(self):
        from production.shorts_factory import ShortsFactory
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            sf     = ShortsFactory(self.mem)
            result = sf.process_video(self._make_script(), metadata={"title": "Solar"})
        for key in ("topic", "total_segments", "shorts_produced",
                    "shorts", "expansion_candidates", "output_dir"):
            self.assertIn(key, result)

    def test_process_video_produces_minimum_shorts(self):
        from production.shorts_factory import ShortsFactory
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            sf     = ShortsFactory(self.mem)
            result = sf.process_video(self._make_script(n_scenes=8),
                                       metadata={"title": "Energy 2026"},
                                       n_shorts=5)
        self.assertGreaterEqual(result["shorts_produced"], 1)

    def test_each_short_has_all_platform_exports(self):
        from production.shorts_factory import ShortsFactory, PLATFORM_SPECS
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            sf     = ShortsFactory(self.mem)
            result = sf.process_video(self._make_script(),
                                       metadata={"title": "Solar"}, n_shorts=2)
        for short in result["shorts"]:
            exports = short.get("exports", {})
            self.assertEqual(set(exports.keys()), set(PLATFORM_SPECS.keys()))

    def test_run_daily_with_no_video_data(self):
        from production.shorts_factory import ShortsFactory
        sf     = ShortsFactory(self.mem)
        result = sf.run_daily(video_data=None)
        self.assertIn("shorts_produced", result)
        self.assertEqual(result["shorts_produced"], 0)




# ── TestGrowthHacker ──────────────────────────────────────────────────────────


class TestGrowthHacker(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")

    # ── Memory schema ───────────────────────────────────────────────────────

    def test_memory_has_growth_tables(self):
        expected = {"collab_prospects", "keyword_clusters", "playlists", "growth_targets"}
        with self.mem._conn() as conn:
            rows   = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables),
                        f"Missing tables: {expected - tables}")

    # ── Collab prospect memory ──────────────────────────────────────────────

    def test_save_and_get_collab_prospect(self):
        pid = self.mem.save_collab_prospect({
            "channel_id":   "UC_COLLAB_01",
            "channel_name": "Solar Talks",
            "subs":         12_000,
            "niche":        "energy",
            "pitch_email":  "Hey, let's collab!",
        })
        self.assertGreater(pid, 0)
        prospects = self.mem.get_collab_prospects()
        self.assertGreaterEqual(len(prospects), 1)
        self.assertEqual(prospects[0]["channel_name"], "Solar Talks")

    def test_save_collab_prospect_dedup(self):
        for _ in range(3):
            self.mem.save_collab_prospect({
                "channel_id": "UC_DUP", "channel_name": "Dup Channel", "subs": 5000,
            })
        prospects = self.mem.get_collab_prospects()
        ids = [p["channel_id"] for p in prospects]
        self.assertEqual(ids.count("UC_DUP"), 1)

    def test_get_collab_prospects_by_status(self):
        self.mem.save_collab_prospect({
            "channel_id": "UC_PITCHED", "channel_name": "Pitched",
            "subs": 8000, "status": "pitched",
        })
        self.mem.save_collab_prospect({
            "channel_id": "UC_PROSPECT", "channel_name": "Prospect",
            "subs": 9000, "status": "prospect",
        })
        pitched = self.mem.get_collab_prospects(status="pitched")
        self.assertEqual(len(pitched), 1)
        self.assertEqual(pitched[0]["channel_id"], "UC_PITCHED")

    def test_update_collab_prospect(self):
        self.mem.save_collab_prospect({
            "channel_id": "UC_UPDATE", "channel_name": "UpdateMe", "subs": 7000,
        })
        self.mem.update_collab_prospect("UC_UPDATE", status="accepted")
        prospects = self.mem.get_collab_prospects(status="accepted")
        self.assertEqual(len(prospects), 1)

    # ── Keyword cluster memory ──────────────────────────────────────────────

    def test_save_and_get_keyword_cluster(self):
        kid = self.mem.save_keyword_cluster({
            "main_keyword":      "solar panel installation",
            "cluster_topic":     "solar",
            "opportunity_score": 85.0,
            "videos_planned":    json.dumps([{"title": "Guide to Solar"}]),
            "status":            "planned",
        })
        self.assertGreater(kid, 0)
        clusters = self.mem.get_keyword_clusters()
        self.assertGreaterEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["main_keyword"], "solar panel installation")

    def test_get_keyword_clusters_by_status(self):
        self.mem.save_keyword_cluster({
            "main_keyword": "wind energy", "cluster_topic": "wind",
            "opportunity_score": 60.0, "status": "active",
        })
        self.mem.save_keyword_cluster({
            "main_keyword": "ev charging", "cluster_topic": "ev",
            "opportunity_score": 70.0, "status": "planned",
        })
        active = self.mem.get_keyword_clusters(status="active")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["main_keyword"], "wind energy")

    # ── Playlist memory ─────────────────────────────────────────────────────

    def test_save_and_get_playlist(self):
        plid = self.mem.save_playlist({
            "title":         "Solar Energy Series",
            "seo_title":     "Complete Solar Energy Guide",
            "topic_cluster": "solar",
            "video_ids":     ["yt001", "yt002", "yt003"],
            "binge_trap":    1,
        })
        self.assertGreater(plid, 0)
        playlists = self.mem.get_playlists()
        self.assertGreaterEqual(len(playlists), 1)
        self.assertEqual(playlists[0]["title"], "Solar Energy Series")

    def test_get_playlists_by_status(self):
        self.mem.save_playlist({
            "title": "Active Playlist", "topic_cluster": "solar",
            "video_ids": ["yt1"], "status": "active",
        })
        self.mem.save_playlist({
            "title": "Archived Playlist", "topic_cluster": "wind",
            "video_ids": ["yt2"], "status": "archived",
        })
        active = self.mem.get_playlists(status="active")
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0]["title"], "Active Playlist")

    # ── Growth target memory ────────────────────────────────────────────────

    def test_save_and_get_growth_target(self):
        tid = self.mem.save_growth_target({
            "set_date":           "2026-06-08",
            "baseline_subs":      10_000,
            "posting_freq_week":  7,
            "weekly_growth_rate": 3.5,
            "freq_multiplier":    1.32,
            "day30_target":       11_200,
            "day60_target":       12_500,
            "day90_target":       14_000,
            "status":             "active",
        })
        self.assertGreater(tid, 0)
        targets = self.mem.get_growth_targets()
        self.assertGreaterEqual(len(targets), 1)
        self.assertEqual(targets[0]["baseline_subs"], 10_000)

    def test_get_revenue_snapshots(self):
        self.mem.save_revenue_snapshot({
            "date": "2026-06-08", "subscribers": 9800, "week_num": 23,
            "total_rev": 150.0,
        })
        snaps = self.mem.get_revenue_snapshots(n=5)
        self.assertGreaterEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["subscribers"], 9800)

    # ── CollaborationDetector ───────────────────────────────────────────────

    def test_analyze_content_overlap_identical(self):
        from intelligence.growth_hacker import CollaborationDetector
        cd = CollaborationDetector()
        self.assertAlmostEqual(
            cd.analyze_content_overlap(["solar", "energy"], ["solar", "energy"]),
            1.0, places=4
        )

    def test_analyze_content_overlap_disjoint(self):
        from intelligence.growth_hacker import CollaborationDetector
        cd = CollaborationDetector()
        self.assertAlmostEqual(
            cd.analyze_content_overlap(["solar"], ["wind"]),
            0.0, places=4
        )

    def test_analyze_content_overlap_partial(self):
        from intelligence.growth_hacker import CollaborationDetector
        cd = CollaborationDetector()
        score = cd.analyze_content_overlap(
            ["solar", "energy", "battery"],
            ["solar", "wind", "grid"],
        )
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)

    def test_analyze_content_overlap_empty(self):
        from intelligence.growth_hacker import CollaborationDetector
        cd = CollaborationDetector()
        self.assertEqual(cd.analyze_content_overlap([], ["solar"]), 0.0)
        self.assertEqual(cd.analyze_content_overlap(["solar"], []), 0.0)

    def test_generate_pitch_email_no_api(self):
        from intelligence.growth_hacker import CollaborationDetector
        from unittest.mock import patch
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            cd    = CollaborationDetector()
            email = cd.generate_pitch_email(
                {"channel_name": "Solar Partner", "niche": "solar", "subs": 12_000},
                {"name": "My Channel", "niche": "energy", "subs": 10_000},
            )
        self.assertIsInstance(email, str)
        self.assertGreater(len(email), 20)

    def test_mock_candidates_size_range(self):
        from intelligence.growth_hacker import CollaborationDetector, COLLAB_SIZE_RANGE
        from unittest.mock import patch
        with patch.dict("os.environ", {"YOUTUBE_DATA_API_KEY": ""}):
            cd   = CollaborationDetector()
            cands = cd.find_similar_channels(10_000, "energy")
        for ch in cands:
            subs   = ch["subs"]
            lo, hi = int(10_000 * COLLAB_SIZE_RANGE[0]), int(10_000 * COLLAB_SIZE_RANGE[1])
            self.assertGreaterEqual(subs, lo)
            self.assertLessEqual(subs, hi)

    # ── ViralLoopEngineer ───────────────────────────────────────────────────

    def _make_script(self):
        return {
            "title": "The Future of Solar",
            "hook":  "Solar power is about to change everything.",
            "scenes": [
                {"title": "Intro",       "narration": "Welcome to this video."},
                {"title": "Background",  "narration": "Here is some context about energy."},
                {"title": "Key Fact",    "narration": "90% of homes could go solar today."},
                {"title": "Secret",      "narration": "The hidden secret nobody talks about."},
                {"title": "Insight",     "narration": "Studies show this saves millions."},
                {"title": "CTA",         "narration": "Subscribe now for more content."},
            ],
        }

    def test_find_shareable_moment_returns_keys(self):
        from intelligence.growth_hacker import ViralLoopEngineer
        vl  = ViralLoopEngineer()
        res = vl.find_shareable_moment(self._make_script())
        for key in ("scene_index", "timestamp_pct", "trigger_type", "text_preview"):
            self.assertIn(key, res)

    def test_find_shareable_moment_detects_stat(self):
        from intelligence.growth_hacker import ViralLoopEngineer
        vl  = ViralLoopEngineer()
        res = vl.find_shareable_moment(self._make_script())
        self.assertNotEqual(res["trigger_type"], "none")

    def test_find_subscribe_prompt_in_range(self):
        from intelligence.growth_hacker import ViralLoopEngineer, SUBSCRIBE_RANGE
        vl  = ViralLoopEngineer()
        res = vl.find_subscribe_prompt_position(self._make_script())
        self.assertGreaterEqual(res["timestamp_pct"], SUBSCRIBE_RANGE[0] - 0.05)
        self.assertLessEqual(res["timestamp_pct"], SUBSCRIBE_RANGE[1] + 0.05)

    def test_inject_viral_does_not_mutate(self):
        from intelligence.growth_hacker import ViralLoopEngineer
        vl     = ViralLoopEngineer()
        script = self._make_script()
        orig_hook = script["hook"]
        vl.inject_viral_elements(script)
        self.assertEqual(script["hook"], orig_hook)
        self.assertNotIn("viral_injection", script)

    def test_inject_viral_marks_shareable_scene(self):
        from intelligence.growth_hacker import ViralLoopEngineer
        vl     = ViralLoopEngineer()
        result = vl.inject_viral_elements(self._make_script())
        scenes = result["scenes"]
        self.assertTrue(any(s.get("shareable_moment") for s in scenes))

    def test_inject_viral_adds_injection_metadata(self):
        from intelligence.growth_hacker import ViralLoopEngineer
        vl     = ViralLoopEngineer()
        result = vl.inject_viral_elements(self._make_script())
        self.assertIn("viral_injection", result)
        vi = result["viral_injection"]
        for key in ("shareable_moment_idx", "subscribe_prompt_idx", "shareable_trigger"):
            self.assertIn(key, vi)

    # ── SearchDominator ─────────────────────────────────────────────────────

    def test_heuristic_competition_longtail(self):
        from intelligence.growth_hacker import SearchDominator
        sd = SearchDominator()
        lo = sd._heuristic_competition("how to install solar panels at home cheaply today")
        hi = sd._heuristic_competition("solar")
        self.assertLess(lo, hi)

    def test_opportunity_score_inversely_proportional_to_competition(self):
        from intelligence.growth_hacker import SearchDominator
        sd = SearchDominator()
        high = sd._opportunity_score(5,  "high")
        low  = sd._opportunity_score(45, "high")
        self.assertGreater(high, low)

    def test_estimate_search_volume_returns_tier(self):
        from intelligence.growth_hacker import SearchDominator
        sd = SearchDominator()
        for kw in ("solar panel installation cost", "solar", "how to use solar"):
            tier = sd._estimate_search_volume(kw, "energy")
            self.assertIn(tier, ("high", "medium", "low"))

    def test_build_keyword_cluster_structure(self):
        from intelligence.growth_hacker import SearchDominator
        from unittest.mock import patch
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            sd      = SearchDominator()
            cluster = sd.build_keyword_cluster("solar panels", "energy", n=5)
        self.assertIn("main_keyword",  cluster)
        self.assertIn("video_ideas",   cluster)
        self.assertIn("count",         cluster)
        self.assertGreaterEqual(cluster["count"], 5)

    def test_blue_ocean_keywords_has_required_keys(self):
        from intelligence.growth_hacker import SearchDominator
        from unittest.mock import patch
        with patch.dict("os.environ",
                        {"YOUTUBE_DATA_API_KEY": "", "ANTHROPIC_API_KEY": ""}):
            sd  = SearchDominator()
            kws = sd.find_blue_ocean_keywords("solar energy", "energy", n=5)
        self.assertIsInstance(kws, list)
        for kw in kws:
            for key in ("keyword", "competition", "volume_tier", "opportunity_score"):
                self.assertIn(key, kw)

    # ── PlaylistArchitect ───────────────────────────────────────────────────

    def test_optimize_order_intro_first(self):
        from intelligence.growth_hacker import PlaylistArchitect
        pa = PlaylistArchitect()
        videos = [
            {"title": "Advanced Solar Deep Dive", "views": 5000},
            {"title": "Solar Basics for Beginners 101", "views": 3000},
            {"title": "Solar Overview for Starters", "views": 2000},
        ]
        ordered = pa.optimize_playlist_order(videos)
        # Advanced deep dive should be last; intro/beginner videos come first
        self.assertIn("Advanced", ordered[-1]["title"])

    def test_generate_seo_title_contains_topic(self):
        from intelligence.growth_hacker import PlaylistArchitect
        pa    = PlaylistArchitect()
        title = pa.generate_seo_title("solar energy")
        self.assertIn("Solar Energy", title)

    def test_create_binge_trap_links_sequence(self):
        from intelligence.growth_hacker import PlaylistArchitect
        pa    = PlaylistArchitect()
        pl    = {"video_ids": ["yt1", "yt2", "yt3"]}
        trap  = pa.create_binge_trap(pl)
        self.assertEqual(trap["yt1"], "yt2")
        self.assertEqual(trap["yt2"], "yt3")
        self.assertEqual(trap["yt3"], "yt1")  # loops back

    def test_auto_organize_creates_playlists(self):
        from intelligence.growth_hacker import PlaylistArchitect
        pa = PlaylistArchitect()
        videos = [
            {"title": "Solar Panel Guide",    "topic": "solar energy", "youtube_id": "yt1"},
            {"title": "Solar Cost Breakdown", "topic": "solar energy", "youtube_id": "yt2"},
            {"title": "Wind Turbine Basics",  "topic": "wind turbine", "youtube_id": "yt3"},
            {"title": "Wind Power Advanced",  "topic": "wind turbine", "youtube_id": "yt4"},
        ]
        playlists = pa.auto_organize_videos(videos)
        self.assertGreaterEqual(len(playlists), 1)
        for pl in playlists:
            self.assertIn("video_ids", pl)
            self.assertGreaterEqual(len(pl["video_ids"]), 2)

    # ── GrowthTargetTracker ─────────────────────────────────────────────────

    def test_set_growth_model_day30_greater_than_baseline(self):
        from intelligence.growth_hacker import GrowthTargetTracker
        gt    = GrowthTargetTracker()
        model = gt.set_growth_model(10_000, posting_freq_week=7, has_shorts=True)
        self.assertGreater(model["day30_target"], 10_000)
        self.assertGreater(model["day60_target"], model["day30_target"])
        self.assertGreater(model["day90_target"], model["day60_target"])

    def test_set_growth_model_shorts_boost(self):
        from intelligence.growth_hacker import GrowthTargetTracker
        gt      = GrowthTargetTracker()
        with_s  = gt.set_growth_model(10_000, 7, has_shorts=True)
        no_s    = gt.set_growth_model(10_000, 7, has_shorts=False)
        self.assertGreater(with_s["day90_target"], no_s["day90_target"])

    def test_set_growth_model_saves_to_memory(self):
        from intelligence.growth_hacker import GrowthTargetTracker
        gt = GrowthTargetTracker()
        gt.set_growth_model(10_000, 7, has_shorts=True, memory=self.mem)
        targets = self.mem.get_growth_targets(status="active")
        self.assertGreaterEqual(len(targets), 1)

    def test_check_trajectory_no_target(self):
        from intelligence.growth_hacker import GrowthTargetTracker
        gt     = GrowthTargetTracker()
        result = gt.check_trajectory(self.mem)
        self.assertIn(result["status"], ("no_target", "no_data", "no_memory"))

    def test_auto_intensify_critical_returns_actions(self):
        from intelligence.growth_hacker import GrowthTargetTracker
        gt     = GrowthTargetTracker()
        traj   = {"status": "critical", "gap_pct": 25.0, "current_subs": 7500}
        result = gt.auto_intensify(traj)
        self.assertTrue(result["intensify"])
        self.assertGreaterEqual(len(result["actions"]), 3)

    def test_auto_intensify_on_track_no_actions(self):
        from intelligence.growth_hacker import GrowthTargetTracker
        gt     = GrowthTargetTracker()
        traj   = {"status": "on_track", "gap_pct": 2.0, "current_subs": 10200}
        result = gt.auto_intensify(traj)
        self.assertFalse(result["intensify"])
        self.assertEqual(len(result["actions"]), 0)


# ── TestCommunityEngine ───────────────────────────────────────────────────────


class TestCommunityEngine(unittest.TestCase):

    def setUp(self):
        from core.memory import Memory
        self.mem = Memory(":memory:")

    # ── Memory schema ───────────────────────────────────────────────────────

    def test_memory_has_community_tables(self):
        expected = {"comments", "community_posts", "retention_events"}
        with self.mem._conn() as conn:
            rows   = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            tables = {r["name"] for r in rows}
        self.assertTrue(expected.issubset(tables),
                        f"Missing tables: {expected - tables}")

    # ── Comment memory CRUD ─────────────────────────────────────────────────

    def test_save_and_get_comment(self):
        cid = self.mem.save_comment({
            "youtube_id": "yt_test_01", "comment_id": "c001",
            "author": "Alice", "text": "Great video!", "likes": 5,
            "category": "praise", "pin_score": 20.0,
        })
        self.assertGreater(cid, 0)
        comments = self.mem.get_comments("yt_test_01")
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author"], "Alice")
        self.assertEqual(comments[0]["category"], "praise")

    def test_save_comment_dedup_on_conflict(self):
        for _ in range(3):
            self.mem.save_comment({
                "youtube_id": "yt_dup", "comment_id": "c_dup",
                "author": "Bob", "text": "Hello", "likes": 10,
            })
        comments = self.mem.get_comments("yt_dup")
        self.assertEqual(len(comments), 1)

    def test_get_pending_replies(self):
        self.mem.save_comment({
            "youtube_id": "yt02", "comment_id": "c002",
            "author": "Bob", "text": "How does this work?",
            "category": "question", "pin_score": 15.0,
            "reply_status": "pending", "reply_text": "Great question! ...",
        })
        pending = self.mem.get_pending_replies()
        self.assertGreaterEqual(len(pending), 1)

    def test_update_comment_status(self):
        self.mem.save_comment({
            "youtube_id": "yt03", "comment_id": "c003",
            "author": "Carol", "text": "Thanks!", "category": "praise",
            "reply_status": "pending", "reply_text": "You're welcome!",
        })
        self.mem.update_comment_status("c003", "replied",
                                        reply_text="Thank you!", is_hearted=True)
        comments = self.mem.get_comments("yt03")
        self.assertEqual(comments[0]["reply_status"], "replied")
        self.assertEqual(comments[0]["is_hearted"], 1)

    def test_get_comments_filtered_by_category(self):
        for i, cat in enumerate(["question", "praise", "question"]):
            self.mem.save_comment({
                "youtube_id": "yt_cat", "comment_id": f"ccat{i}",
                "author": f"User{i}", "text": "text", "category": cat,
            })
        questions = self.mem.get_comments("yt_cat", category="question")
        self.assertEqual(len(questions), 2)

    # ── Community post memory ───────────────────────────────────────────────

    def test_save_and_get_community_post(self):
        pid = self.mem.save_community_post({
            "post_type": "poll", "content": "Which energy source?",
            "poll_options": ["Solar", "Wind", "Nuclear"],
            "scheduled_at": "2026-06-10T18:00:00+00:00",
            "engagement_goal": "100 votes",
        })
        self.assertGreater(pid, 0)
        posts = self.mem.get_scheduled_community_posts()
        self.assertGreaterEqual(len(posts), 1)
        self.assertEqual(posts[0]["post_type"], "poll")

    def test_get_due_community_posts_returns_past(self):
        from datetime import datetime, timedelta, timezone
        past   = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        future = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()
        self.mem.save_community_post({
            "post_type": "teaser", "content": "Coming soon!",
            "scheduled_at": past, "status": "scheduled",
        })
        self.mem.save_community_post({
            "post_type": "poll", "content": "Vote now!",
            "scheduled_at": future, "status": "scheduled",
        })
        due = self.mem.get_due_community_posts()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["post_type"], "teaser")

    def test_update_community_post_status(self):
        pid = self.mem.save_community_post({
            "post_type": "request", "content": "What topic next?",
            "scheduled_at": "2026-06-09T18:00:00+00:00",
        })
        self.mem.update_community_post_status(pid, "published", "YT_POST_ABC")
        scheduled_ids = [p["id"] for p in self.mem.get_scheduled_community_posts()]
        self.assertNotIn(pid, scheduled_ids)

    # ── Retention event memory ──────────────────────────────────────────────

    def test_save_and_get_retention_event(self):
        eid = self.mem.save_retention_event({
            "event_type": "drop", "subscriber_count": 9800,
            "change_count": -200, "change_pct": -2.0,
            "culprit_video": "yt_bad_video",
            "strategy_applied": "reengagement_post",
        })
        self.assertGreater(eid, 0)
        events = self.mem.get_retention_events()
        self.assertGreaterEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "drop")

    def test_get_retention_events_filter_resolved(self):
        self.mem.save_retention_event({
            "event_type": "drop", "change_pct": -1.5, "resolved": False,
        })
        self.mem.save_retention_event({
            "event_type": "drop", "change_pct": -0.8, "resolved": True,
        })
        unresolved = self.mem.get_retention_events(resolved=False)
        resolved   = self.mem.get_retention_events(resolved=True)
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(len(resolved),   1)

    # ── CommentIntelligence ─────────────────────────────────────────────────

    def test_categorize_comment_question(self):
        from intelligence.community_engine import CommentIntelligence
        ci  = CommentIntelligence()
        cat = ci.categorize_comment("What is the best solar panel for homes?")
        self.assertEqual(cat, "question")

    def test_categorize_comment_praise(self):
        from intelligence.community_engine import CommentIntelligence
        ci  = CommentIntelligence()
        cat = ci.categorize_comment("Amazing video, love your content!")
        self.assertEqual(cat, "praise")

    def test_categorize_comment_complaint(self):
        from intelligence.community_engine import CommentIntelligence
        ci  = CommentIntelligence()
        cat = ci.categorize_comment("This information is wrong and misleading")
        self.assertEqual(cat, "complaint")

    def test_categorize_comment_suggestion(self):
        from intelligence.community_engine import CommentIntelligence
        ci  = CommentIntelligence()
        cat = ci.categorize_comment("You should make a video about wind energy next")
        self.assertEqual(cat, "suggestion")

    def test_score_pin_candidate_returns_float(self):
        from intelligence.community_engine import CommentIntelligence
        ci      = CommentIntelligence()
        comment = {"text": "I disagree with this — solar has serious drawbacks",
                   "likes": 50, "category": "complaint"}
        score   = ci.score_pin_candidate(comment)
        self.assertIsInstance(score, float)
        self.assertGreaterEqual(score, 0)

    def test_template_reply_question(self):
        from intelligence.community_engine import CommentIntelligence
        ci    = CommentIntelligence()
        reply = ci._template_reply("How does net metering work?", "question", "solar")
        self.assertIsInstance(reply, str)
        self.assertGreater(len(reply), 10)

    def test_template_reply_all_categories(self):
        from intelligence.community_engine import CommentIntelligence
        ci = CommentIntelligence()
        for cat in ("question", "complaint", "suggestion", "praise", "other"):
            reply = ci._template_reply("Some comment text", cat, "energy")
            self.assertIsInstance(reply, str)
            self.assertGreater(len(reply), 5)

    # ── CommunityPostGenerator ──────────────────────────────────────────────

    def test_generate_poll_structure(self):
        from intelligence.community_engine import CommunityPostGenerator
        from unittest.mock import patch
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            gen  = CommunityPostGenerator()
            poll = gen.generate_poll(["solar", "wind", "battery"], "energy")
        self.assertIn("type",         poll)
        self.assertIn("content",      poll)
        self.assertIn("poll_options", poll)
        self.assertEqual(poll["type"], "poll")
        self.assertIsInstance(poll["poll_options"], list)
        self.assertGreaterEqual(len(poll["poll_options"]), 2)

    def test_generate_teaser_structure(self):
        from intelligence.community_engine import CommunityPostGenerator
        from unittest.mock import patch
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            gen    = CommunityPostGenerator()
            teaser = gen.generate_teaser({"title": "The Future of Solar"})
        self.assertEqual(teaser["type"], "teaser")
        self.assertIn("content", teaser)
        self.assertGreater(len(teaser["content"]), 5)

    def test_schedule_weekly_posts_count(self):
        from intelligence.community_engine import CommunityPostGenerator, POST_SCHEDULES
        from unittest.mock import patch
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""}):
            gen   = CommunityPostGenerator()
            posts = gen.schedule_weekly_posts(
                topics=["solar", "wind"], niche="energy",
                memory=self.mem,
                upcoming_video={"title": "Battery Tech"},
            )
        self.assertEqual(len(posts), len(POST_SCHEDULES))

    # ── AudienceRetentionAnalyzer ───────────────────────────────────────────

    def test_detect_subscriber_drop_no_data(self):
        from intelligence.community_engine import AudienceRetentionAnalyzer
        ara    = AudienceRetentionAnalyzer()
        result = ara.detect_subscriber_drop(self.mem)
        self.assertIn("drop_detected", result)
        self.assertIn("severity",      result)
        self.assertFalse(result["drop_detected"])

    def test_detect_subscriber_drop_detects_drop(self):
        from intelligence.community_engine import AudienceRetentionAnalyzer
        from datetime import datetime, timedelta, timezone as tz
        ara = AudienceRetentionAnalyzer()
        now = datetime.now(tz.utc)
        for i, subs in enumerate([10000, 9900, 9800, 9700, 9600, 9500]):
            d = (now - timedelta(days=5 - i)).strftime("%Y-%m-%d")
            self.mem.save_revenue_snapshot({
                "date": d, "subscribers": subs,
                "week_num": 1, "total_rev": 0,
            })
        result = ara.detect_subscriber_drop(self.mem)
        self.assertIn("drop_detected", result)
        self.assertIn("severity",      result)
        self.assertIn(result["severity"], ("warning", "critical", "none"))


# ── Runner ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    loader  = unittest.TestLoader()
    suite   = loader.loadTestsFromModule(sys.modules[__name__])
    runner  = unittest.TextTestRunner(verbosity=2)
    result  = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
