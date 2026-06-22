"""
Master scheduler — coordinates all pipeline jobs using APScheduler.
Weekday: market scrape → script → build → upload (8AM and 5PM ET).
Sunday: educational video (11AM ET).
Runs as a long-lived process; designed for server/cron deployment.
"""
import logging
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


def run_weekday_pipeline() -> None:
    """Full weekday pipeline: scrape → generate → build → upload."""
    logger.info("=" * 60)
    logger.info("WEEKDAY PIPELINE STARTING — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    try:
        from scheduler.weekday_scheduler import WeekdayScheduler
        scheduler = WeekdayScheduler()
        result = scheduler.run()
        if result:
            logger.info("Weekday pipeline complete: %s", result)
        else:
            logger.error("Weekday pipeline returned no result")
    except Exception as exc:
        logger.exception("Weekday pipeline FAILED: %s", exc)


def run_sunday_pipeline() -> None:
    """Full Sunday educational pipeline."""
    logger.info("=" * 60)
    logger.info("SUNDAY PIPELINE STARTING — %s", datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)

    try:
        from scheduler.sunday_scheduler import SundayScheduler
        scheduler = SundayScheduler()
        result = scheduler.run()
        if result:
            logger.info("Sunday pipeline complete: %s", result)
        else:
            logger.error("Sunday pipeline returned no result")
    except Exception as exc:
        logger.exception("Sunday pipeline FAILED: %s", exc)


def run_monitor_check() -> None:
    """Hourly monitor check — performance alerts."""
    try:
        from monitor.monitor import ChannelMonitor
        monitor = ChannelMonitor()
        monitor.run_check()
    except Exception as exc:
        logger.warning("Monitor check failed: %s", exc)


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
