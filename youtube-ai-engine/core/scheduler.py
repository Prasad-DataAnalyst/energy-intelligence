"""
core/scheduler.py — Daily / Weekly Job Orchestrator
=====================================================
Runs the full self-evolving pipeline on a schedule:

  Daily schedule:
    02:00  Trend hunter fetches top trends
    03:00  Brain picks today's topic & style
    04:00  Script writer generates script
    05:00  Voice engine produces audio
    06:00  Visual engine generates images / B-roll
    07:00  Editor assembles final video
    07:30  Thumbnail AI generates 3 variants
    08:00  YouTube publisher uploads + schedules
    08:30  SEO optimizer posts final metadata
    10:00  Competitor spy daily snapshot
    22:00  Feedback loop checks analytics checkpoints

  Weekly (Sunday 01:00):
    Code auditor — self-improvement run

  Every 6 hours:
    Version manager git snapshot
"""
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger("brain.scheduler")

HERE = Path(__file__).parent.parent


class DailyPipeline:
    """Runs the full production pipeline for one video."""

    def __init__(self, memory=None, dry_run: bool = False):
        from core.memory import get_memory
        self.memory   = memory or get_memory()
        self.dry_run  = dry_run

    def run(self) -> dict:
        from core.brain                       import get_brain
        from intelligence.trend_hunter        import TrendHunter
        from intelligence.competitor_spy      import CompetitorSpy
        from production.script_writer         import ScriptWriter
        from production.voice_engine          import VoiceEngine
        from production.visual_engine         import VisualEngine
        from production.editor                import VideoEditor
        from production.thumbnail_ai          import ThumbnailAI
        from publishing.youtube_publisher     import YouTubePublisher
        from publishing.seo_optimizer         import SEOOptimizer
        from evolution.ab_tester              import ABTester

        brain  = get_brain()
        result = {}

        log.info("=" * 60)
        log.info(f"DAILY PIPELINE — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        log.info("=" * 60)

        # ── 1. Trend hunting ────────────────────────────────────────────────
        log.info("[1/9] Trend hunting…")
        th = TrendHunter(self.memory)
        candidates = th.fetch_all()
        self.memory.save_trends(candidates)
        log.info(f"  Found {len(candidates)} candidates")

        # ── 2. Topic selection ─────────────────────────────────────────────
        log.info("[2/9] Brain selecting topic…")
        recent = self.memory.recent_topics(days=7)
        topic  = brain.decide_topic(candidates, recent)
        style  = brain.decide_style(topic.get("title", ""))
        length = brain.decide_video_length()
        log.info(f"  Topic: {topic['title']!r}  style={style}  length={length}s")
        result["topic"] = topic

        if self.dry_run:
            log.info("DRY RUN — stopping after topic selection")
            return result

        # ── 3. Script writing ──────────────────────────────────────────────
        log.info("[3/9] Writing script…")
        sw     = ScriptWriter(self.memory)
        script = sw.generate(topic, style=style, target_length_s=length)
        result["script"] = script

        # ── 4. Voice / audio ───────────────────────────────────────────────
        log.info("[4/9] Generating voice…")
        ve       = VoiceEngine()
        audio_f  = ve.produce(script)
        result["audio"] = audio_f

        # ── 5. Visuals ─────────────────────────────────────────────────────
        log.info("[5/9] Generating visuals…")
        vis      = VisualEngine()
        frames   = vis.produce(script, style=style)
        result["frames"] = frames

        # ── 6. Video assembly ──────────────────────────────────────────────
        log.info("[6/9] Assembling video…")
        ed       = VideoEditor()
        video_f  = ed.assemble(script, frames, audio_f, style=style)
        result["video"] = video_f

        # ── 7. Thumbnails ──────────────────────────────────────────────────
        log.info("[7/9] Generating thumbnails…")
        ta       = ThumbnailAI(self.memory)
        thumbs   = ta.generate_variants(topic, style=style)
        result["thumbnails"] = thumbs

        # ── 8. Upload ──────────────────────────────────────────────────────
        log.info("[8/9] Publishing to YouTube…")
        seo      = SEOOptimizer(self.memory)
        metadata = seo.build_metadata(topic, script)
        pub      = YouTubePublisher(self.memory)
        yt_id    = pub.upload(video_f, metadata, thumbnail=thumbs[0] if thumbs else None)
        result["youtube_id"] = yt_id

        # ── 9. Register video in memory + start A/B test ───────────────────
        log.info("[9/9] Registering video…")
        if yt_id:
            db_id = self.memory.save_video({
                "date":               datetime.now(timezone.utc).isoformat()[:10],
                "topic":              topic.get("title", ""),
                "title":              metadata.get("title", ""),
                "youtube_id":         yt_id,
                "script_style":       style,
                "video_length_s":     length,
                "thumbnail_variant":  Path(thumbs[0]).stem if thumbs else "",
                "hook_style":         script.get("hook_style", ""),
                "output_dir":         str(Path(video_f).parent) if video_f else "",
            })
            if len(thumbs) > 1:
                abt = ABTester(self.memory)
                abt.register_test(db_id, yt_id, thumbs)
            log.info(f"  Video saved: db_id={db_id}  yt={yt_id}")
            result["db_id"] = db_id

            # ── Meta-learning: every 3rd video, the system reviews itself ──
            try:
                from evolution.meta_learner import MetaLearner
                meta = MetaLearner(self.memory).maybe_run()
                if meta:
                    log.info(f"  Meta-learning cycle ran at milestone {meta['milestone']}")
                    result["meta"] = meta
            except Exception as e:
                log.error(f"Meta-learner failed (non-fatal): {e}", exc_info=True)

        log.info("Daily pipeline complete.")
        return result


class Scheduler:
    """
    Wraps APScheduler (or falls back to a simple sleep loop) to run
    DailyPipeline and maintenance jobs on schedule.
    """

    def __init__(self, memory=None, dry_run: bool = False):
        from core.memory import get_memory
        self.memory  = memory or get_memory()
        self.dry_run = dry_run
        self._sched  = None

    # ── APScheduler setup ──────────────────────────────────────────────────

    def _build_scheduler(self):
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
            from apscheduler.triggers.cron       import CronTrigger
            sched = BlockingScheduler(timezone="UTC")

            # Daily pipeline at 02:00 UTC
            sched.add_job(
                self._run_daily, CronTrigger(hour=2, minute=0),
                id="daily_pipeline", name="Daily video production",
                max_instances=1, coalesce=True,
            )

            # Analytics checkpoints at 22:00 UTC
            sched.add_job(
                self._run_feedback, CronTrigger(hour=22, minute=0),
                id="feedback_loop", name="Analytics feedback loop",
                max_instances=1, coalesce=True,
            )

            # Competitor spy at 10:00 UTC daily
            sched.add_job(
                self._run_competitor_spy, CronTrigger(hour=10, minute=0),
                id="competitor_spy", name="Competitor analysis",
                max_instances=1, coalesce=True,
            )

            # Weekly code audit — Sunday 01:00 UTC
            sched.add_job(
                self._run_code_audit, CronTrigger(day_of_week="sun", hour=1, minute=0),
                id="code_audit", name="Weekly code self-improvement",
                max_instances=1, coalesce=True,
            )

            # Git snapshot every 6 hours
            sched.add_job(
                self._run_version_snapshot, CronTrigger(hour="*/6", minute=30),
                id="git_snapshot", name="Git version snapshot",
                max_instances=1, coalesce=True,
            )

            return sched
        except ImportError:
            log.warning("APScheduler not installed — using sleep-loop fallback")
            return None

    # ── Job implementations ────────────────────────────────────────────────

    def _run_daily(self):
        try:
            DailyPipeline(self.memory, dry_run=self.dry_run).run()
        except Exception as e:
            log.error(f"Daily pipeline crashed: {e}", exc_info=True)

    def _run_feedback(self):
        try:
            from evolution.feedback_loop import FeedbackLoop
            n = FeedbackLoop(self.memory).run_pending_checkpoints()
            log.info(f"Feedback loop: updated {n} videos")
        except Exception as e:
            log.error(f"Feedback loop crashed: {e}", exc_info=True)

    def _run_competitor_spy(self):
        try:
            from intelligence.competitor_spy import CompetitorSpy
            CompetitorSpy(self.memory).build_formula_db()
        except Exception as e:
            log.error(f"Competitor spy crashed: {e}", exc_info=True)

    def _run_code_audit(self):
        try:
            from evolution.code_auditor import CodeAuditor
            result = CodeAuditor(self.memory).run_weekly_audit()
            log.info(f"Code audit: {result.get('upgraded', [])} files upgraded")
        except Exception as e:
            log.error(f"Code audit crashed: {e}", exc_info=True)

    def _run_version_snapshot(self):
        try:
            from evolution.version_manager import VersionManager
            VersionManager().snapshot("auto: 6h checkpoint")
        except Exception as e:
            log.debug(f"Version snapshot failed: {e}")

    # ── Entry points ───────────────────────────────────────────────────────

    def start(self):
        """Start the blocking scheduler (runs forever)."""
        log.info(f"Starting scheduler (dry_run={self.dry_run})…")
        sched = self._build_scheduler()
        if sched:
            log.info("APScheduler running — jobs registered:")
            for job in sched.get_jobs():
                log.info(f"  {job.name} → {job.trigger}")
            sched.start()
        else:
            self._sleep_loop()

    def run_now(self):
        """Run the daily pipeline immediately (for testing/manual trigger)."""
        log.info("Manual trigger: running daily pipeline now…")
        self._run_daily()

    def _sleep_loop(self):
        """Minimal fallback scheduler using time.sleep."""
        log.info("Sleep-loop scheduler started")
        last_daily    = None
        last_feedback = None
        last_audit    = None

        while True:
            now = datetime.now(timezone.utc)
            h   = now.hour

            if h == 2 and last_daily != now.date():
                last_daily = now.date()
                self._run_daily()

            if h == 22 and last_feedback != now.date():
                last_feedback = now.date()
                self._run_feedback()

            if now.weekday() == 6 and h == 1 and last_audit != now.isocalendar()[1]:
                last_audit = now.isocalendar()[1]
                self._run_code_audit()

            time.sleep(60)
