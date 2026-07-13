"""
Master scheduler — coordinates all pipeline jobs using APScheduler.
Weekday: market scrape → script → build → upload (8AM and 5PM ET).
Sunday: educational video (11AM ET).
Runs as a long-lived process; designed for server/cron deployment.
Process isolation: each heavy pipeline run spawns a child subprocess.
"""
import logging
import multiprocessing
import os
import sys
from datetime import datetime
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    log_path = settings.logs_dir / f"scheduler_{datetime.now().strftime('%Y%m%d')}.log"
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_path, encoding="utf-8"),
    ]
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in handlers:
        h.setFormatter(fmt)
        root.addHandler(h)


def _run_weekday_in_process() -> None:
    """Isolated child-process entry for weekday pipeline."""
    from scheduler.weekday_scheduler import WeekdayScheduler
    sch = WeekdayScheduler()
    result = sch.run()
    if result:
        logger.info("Weekday pipeline complete: %s", result)
    else:
        logger.error("Weekday pipeline returned no result")


def _run_sunday_in_process() -> None:
    """Isolated child-process entry for Sunday pipeline."""
    from scheduler.sunday_scheduler import SundayScheduler
    sch = SundayScheduler()
    result = sch.run()
    if result:
        logger.info("Sunday pipeline complete: %s", result)
    else:
        logger.error("Sunday pipeline returned no result")


def _run_isolated(target_fn, name: str) -> None:
    """Spawn target_fn in a fresh child process for isolation.

    Uses the 'spawn' start method so the child gets a clean interpreter —
    no inherited log file handles, API clients, or scheduler state from the
    parent (fork would share all of these and risks deadlocks/races).
    """
    logger.info("Spawning isolated process for: %s (PID parent: %d)", name, os.getpid())
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=target_fn, name=name, daemon=False)
    proc.start()
    proc.join(timeout=7200)   # 2-hour hard timeout per pipeline run

    if proc.is_alive():
        # Timed out — terminate so a hung run can't block tomorrow's jobs
        logger.error("Isolated process '%s' (PID %s) exceeded 2h timeout — terminating", name, proc.pid)
        proc.terminate()
        proc.join(timeout=30)
        if proc.is_alive():
            logger.error("Isolated process '%s' did not terminate — killing", name)
            proc.kill()
            proc.join(timeout=10)
        return

    if proc.exitcode != 0:
        logger.error("Isolated process '%s' exited with code %s", name, proc.exitcode)
    else:
        logger.info("Isolated process '%s' completed successfully", name)


def run_weekday_pipeline() -> None:
    """Full weekday pipeline: scrape → generate → build → upload (process-isolated)."""
    logger.info("=" * 60)
    logger.info("WEEKDAY PIPELINE STARTING — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)
    try:
        _run_isolated(_run_weekday_in_process, "WeekdayPipeline")
    except Exception as exc:
        logger.exception("Weekday pipeline FAILED: %s", exc)


def run_sunday_pipeline() -> None:
    """Full Sunday educational pipeline (process-isolated)."""
    logger.info("=" * 60)
    logger.info("SUNDAY PIPELINE STARTING — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)
    try:
        _run_isolated(_run_sunday_in_process, "SundayPipeline")
    except Exception as exc:
        logger.exception("Sunday pipeline FAILED: %s", exc)


def run_heartbeat() -> None:
    """30-minute heartbeat — logs scheduler liveness to logs/heartbeat.log."""
    hb_path = settings.logs_dir / "heartbeat.log"
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(hb_path, "a", encoding="utf-8") as f:
        f.write(f"[HEARTBEAT] {ts} | PID={os.getpid()} | scheduler alive\n")
    logger.debug("Heartbeat logged at %s", ts)


def run_monitor_check() -> None:
    """Hourly monitor check — performance alerts."""
    try:
        from monitor.monitor import ChannelMonitor
        monitor = ChannelMonitor()
        monitor.run_check()
    except Exception as exc:
        logger.warning("Monitor check failed: %s", exc)


def run_pipeline_retry() -> None:
    """30 min after the main slots: resume today's pipeline if it didn't finish."""
    try:
        from scheduler.deadman import retry_if_needed
        pipeline = "sunday" if datetime.now().weekday() == 6 else "weekday"
        retry_if_needed(pipeline)
    except Exception as exc:
        logger.error("Pipeline retry job failed: %s", exc)


def run_deadman_check() -> None:
    """18:00 ET — alert if no video was uploaded today."""
    try:
        from scheduler.deadman import check_todays_upload
        from scheduler.weekday_scheduler import WeekdayScheduler
        weekday = datetime.now().weekday()
        is_content_day = weekday == 6 or WeekdayScheduler().is_market_day()
        check_todays_upload(is_content_day=is_content_day)
    except Exception as exc:
        logger.error("Dead-man check failed: %s", exc)


def run_analytics_pull() -> None:
    """21:30 ET daily — pull video stats via YouTube Analytics API."""
    try:
        from channel_manager.analytics_tracker import AnalyticsTracker
        AnalyticsTracker().run_daily_pull()
    except Exception as exc:
        logger.warning("Analytics pull failed: %s", exc)


def run_comment_check() -> None:
    """20:00 ET daily — fetch, classify, and reply-draft comments; flag spam."""
    try:
        from channel_manager.comment_monitor import CommentMonitor
        CommentMonitor().run_daily_check()
    except Exception as exc:
        logger.warning("Comment check failed: %s", exc)


def run_community_post() -> None:
    """Sunday 09:00 ET — weekly watchlist community post."""
    try:
        from channel_manager.community_poster import CommunityPoster
        CommunityPoster().run_weekly_post()
    except Exception as exc:
        logger.warning("Community post failed: %s", exc)


def run_description_refresh() -> None:
    """Monday 07:30 ET — refresh channel description with current date."""
    try:
        from channel_manager.post_manager import PostManager
        PostManager().refresh_channel_description()
    except Exception as exc:
        logger.warning("Channel description refresh failed: %s", exc)


def start_scheduler() -> None:
    """Start APScheduler with all jobs configured."""
    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        logger.error("APScheduler not installed. Run: pip install apscheduler")
        sys.exit(1)

    _setup_logging()
    tz = settings.timezone

    scheduler = BlockingScheduler(timezone=tz)

    # ── Weekday jobs ──────────────────────────────────────────────────────
    # Pre-market recap (8 AM ET, Mon–Fri)
    scheduler.add_job(
        run_weekday_pipeline,
        CronTrigger(day_of_week="mon-fri", hour=8, minute=0, timezone=tz),
        id="weekday_premarket",
        name="Weekday Pre-Market Pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,    # 30 min grace
    )

    # Post-market recap (5 PM ET, Mon–Fri)
    scheduler.add_job(
        run_weekday_pipeline,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=0, timezone=tz),
        id="weekday_postmarket",
        name="Weekday Post-Market Pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # ── Sunday educational ────────────────────────────────────────────────
    scheduler.add_job(
        run_sunday_pipeline,
        CronTrigger(day_of_week="sun", hour=11, minute=0, timezone=tz),
        id="sunday_educational",
        name="Sunday Educational Pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── Monitor (every 2 hours) ───────────────────────────────────────────
    scheduler.add_job(
        run_monitor_check,
        CronTrigger(minute=0, hour="*/2"),
        id="monitor_check",
        name="Channel Performance Monitor",
        max_instances=1,
        coalesce=True,
    )

    # ── Heartbeat (every 30 minutes) ─────────────────────────────────────
    scheduler.add_job(
        run_heartbeat,
        CronTrigger(minute="0,30"),
        id="heartbeat",
        name="Scheduler Heartbeat",
        max_instances=1,
        coalesce=True,
    )

    # ── Reliability: pipeline retry (30 min after main slots) ─────────────
    scheduler.add_job(
        run_pipeline_retry,
        CronTrigger(day_of_week="mon-fri", hour="8,17", minute=45, timezone=tz),
        id="pipeline_retry_weekday",
        name="Weekday Pipeline Retry (checkpoint resume)",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_pipeline_retry,
        CronTrigger(day_of_week="sun", hour=11, minute=45, timezone=tz),
        id="pipeline_retry_sunday",
        name="Sunday Pipeline Retry (checkpoint resume)",
        max_instances=1,
        coalesce=True,
    )

    # ── Reliability: dead-man switch (18:00 ET daily) ──────────────────────
    scheduler.add_job(
        run_deadman_check,
        CronTrigger(hour=18, minute=0, timezone=tz),
        id="deadman_check",
        name="Dead-Man Upload Check",
        max_instances=1,
        coalesce=True,
    )

    # ── Channel management ─────────────────────────────────────────────────
    scheduler.add_job(
        run_comment_check,
        CronTrigger(hour=20, minute=0, timezone=tz),
        id="comment_check",
        name="Daily Comment Monitor",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_analytics_pull,
        CronTrigger(hour=21, minute=30, timezone=tz),
        id="analytics_pull",
        name="Daily Analytics Pull",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_community_post,
        CronTrigger(day_of_week="sun", hour=9, minute=0, timezone=tz),
        id="community_post",
        name="Weekly Community Post",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        run_description_refresh,
        CronTrigger(day_of_week="mon", hour=7, minute=30, timezone=tz),
        id="description_refresh",
        name="Weekly Channel Description Refresh",
        max_instances=1,
        coalesce=True,
    )

    logger.info("DriftWire326 Scheduler starting — %d jobs registered", len(scheduler.get_jobs()))
    for job in scheduler.get_jobs():
        logger.info("  Job: %s | Next run: %s", job.name, job.next_run_time)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()
