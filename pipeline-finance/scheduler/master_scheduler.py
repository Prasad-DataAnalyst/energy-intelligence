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


def _run_weekday_in_process(slot: str = "premarket") -> None:
    """Isolated child-process entry for weekday pipeline."""
    from scheduler.weekday_scheduler import WeekdayScheduler
    sch = WeekdayScheduler(slot=slot)
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


def _run_isolated(target_fn, name: str, args: tuple = ()) -> None:
    """Spawn target_fn in a fresh child process for isolation.

    Uses the 'spawn' start method so the child gets a clean interpreter —
    no inherited log file handles, API clients, or scheduler state from the
    parent (fork would share all of these and risks deadlocks/races).
    """
    logger.info("Spawning isolated process for: %s (PID parent: %d)", name, os.getpid())
    ctx = multiprocessing.get_context("spawn")
    # args must be picklable — "spawn" re-imports the module in the child
    # rather than inheriting memory, so a closure would not survive.
    proc = ctx.Process(target=target_fn, args=args, name=name, daemon=False)
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


def run_weekday_pipeline(slot: str = "premarket") -> None:
    """Full weekday pipeline: scrape → generate → build → upload (process-isolated)."""
    logger.info("=" * 60)
    logger.info("WEEKDAY PIPELINE STARTING (%s) — %s",
                slot, datetime.now().strftime("%Y-%m-%d %H:%M"))
    logger.info("=" * 60)
    try:
        _run_isolated(_run_weekday_in_process, f"WeekdayPipeline-{slot}", (slot,))
    except Exception as exc:
        logger.exception("Weekday pipeline (%s) FAILED: %s", slot, exc)


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


def run_themed_short_job() -> None:
    """Day-themed Short: generate → build → upload (Mon-Fri 12:30, Sat 11:00)."""
    try:
        from scheduler.short_pipeline import run_themed_short
        video_id = run_themed_short()
        if video_id:
            logger.info("Themed Short published: %s", video_id)
        else:
            logger.info("Themed Short skipped or failed today (see logs)")
    except Exception as exc:
        logger.error("Themed Short job failed: %s", exc)


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
        # Sat (Short), Sun (deep-dive), and market weekdays all publish content
        is_content_day = weekday in (5, 6) or WeekdayScheduler().is_market_day()
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


# Cron timing for the jobs that actually publish content. Single source of
# truth: start_scheduler() registers from this, and the health report reads it
# to answer "when is the next video due?" without duplicating the schedule.
CONTENT_SLOTS: dict[str, dict] = {
    "weekday_premarket": {"day_of_week": "mon-fri", "hour": 8, "minute": 0},
    "midday_short":      {"day_of_week": "mon-fri", "hour": 12, "minute": 30},
    "weekday_postmarket": {"day_of_week": "mon-fri", "hour": 17, "minute": 15},
    "saturday_short":    {"day_of_week": "sat", "hour": 11, "minute": 0},
    "sunday_educational": {"day_of_week": "sun", "hour": 11, "minute": 0},
}

CONTENT_SLOT_NAMES: dict[str, str] = {
    "weekday_premarket": "Pre-market video",
    "midday_short": "Midday Short",
    "weekday_postmarket": "Post-market video",
    "saturday_short": "Saturday Short",
    "sunday_educational": "Sunday deep-dive",
}


REGISTERED_JOBS_FILE = "registered_jobs.json"


def _write_registered_jobs(scheduler) -> None:
    """
    Dump the live job table once the scheduler has started.

    Runs from an EVENT_SCHEDULER_STARTED listener rather than inline: before
    start() a job's next_run_time slot is unassigned, so the fire times are
    only real once the scheduler is up. The listener fires before the
    blocking loop begins, verified against APScheduler 3.11.
    """
    import json
    try:
        payload = {
            "written_at": datetime.now().isoformat(),
            "pid": os.getpid(),
            "jobs": [
                {
                    "id": job.id,
                    "name": job.name,
                    "args": list(job.args or ()),
                    "next_run": (lambda run: run.isoformat() if run else None)(
                        getattr(job, "next_run_time", None)),
                }
                for job in scheduler.get_jobs()
            ],
        }
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        path = settings.logs_dir / REGISTERED_JOBS_FILE
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(tmp, path)
        logger.info("Registered %d jobs → %s", len(payload["jobs"]), path.name)
    except Exception as exc:
        # Never let bookkeeping take down a scheduler that is otherwise fine.
        logger.warning("Could not record registered jobs (non-fatal): %s", exc)


def next_content_runs(limit: int = 3) -> list[tuple[str, "datetime"]]:
    """
    Upcoming content slots as (label, fire time), soonest first.
    Returns [] if APScheduler or the timezone is unavailable.
    """
    try:
        from apscheduler.triggers.cron import CronTrigger
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(settings.timezone)
        except Exception:
            import pytz
            tz = pytz.timezone(settings.timezone)

        now = datetime.now(tz)
        upcoming: list[tuple[str, datetime]] = []
        for job_id, cron in CONTENT_SLOTS.items():
            fire = CronTrigger(timezone=tz, **cron).get_next_fire_time(None, now)
            if fire:
                upcoming.append((CONTENT_SLOT_NAMES.get(job_id, job_id), fire))
        upcoming.sort(key=lambda pair: pair[1])
        return upcoming[:limit]
    except Exception as exc:
        logger.debug("Could not compute next content runs: %s", exc)
        return []


def start_scheduler() -> None:
    """Start APScheduler with all jobs configured."""
    # Logging first: a startup crash must land in the log, not vanish into
    # a systemd restart loop.
    _setup_logging()

    try:
        from apscheduler.schedulers.blocking import BlockingScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError as exc:
        logger.error(
            "Cannot import APScheduler 3.x (%s). Version 4.x removed "
            "apscheduler.schedulers.blocking — install the supported line: "
            "pip install 'APScheduler>=3.10.4,<4.0'", exc,
        )
        sys.exit(1)

    tz = settings.timezone

    # Prove liveness immediately — the heartbeat file is how an operator (and
    # `main.py --health`) distinguishes "daemon running its jobs" from
    # "daemon crash-looping". Waiting for the first :00/:30 tick hid a
    # start-up crash loop for weeks.
    try:
        run_heartbeat()
    except Exception as exc:
        logger.warning("Startup heartbeat failed: %s", exc)

    scheduler = BlockingScheduler(timezone=tz)

    # ── Weekday jobs ──────────────────────────────────────────────────────
    # Pre-market recap (8 AM ET, Mon–Fri)
    scheduler.add_job(
        run_weekday_pipeline,
        CronTrigger(timezone=tz, **CONTENT_SLOTS["weekday_premarket"]),
        id="weekday_premarket",
        args=["premarket"],
        name="Weekday Pre-Market Pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,    # 30 min grace
    )

    # Post-market recap (5:15 PM ET, Mon–Fri — spec: markets settle by 5:15).
    # Its own slot, so it publishes its own video rather than seeing the
    # morning run's success and returning without doing anything. If the
    # morning run is unfinished this one adopts it instead — see
    # WeekdayScheduler._resolve_state.
    scheduler.add_job(
        run_weekday_pipeline,
        CronTrigger(timezone=tz, **CONTENT_SLOTS["weekday_postmarket"]),
        id="weekday_postmarket",
        args=["postmarket"],
        name="Weekday Post-Market Pipeline",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # Midday themed Short (12:30 PM ET, Mon–Fri)
    scheduler.add_job(
        run_themed_short_job,
        CronTrigger(timezone=tz, **CONTENT_SLOTS["midday_short"]),
        id="midday_short",
        name="Midday Themed Short",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=1800,
    )

    # Saturday evergreen Short (11:00 AM ET)
    scheduler.add_job(
        run_themed_short_job,
        CronTrigger(timezone=tz, **CONTENT_SLOTS["saturday_short"]),
        id="saturday_short",
        name="Saturday Evergreen Short",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )

    # ── Sunday educational ────────────────────────────────────────────────
    scheduler.add_job(
        run_sunday_pipeline,
        CronTrigger(timezone=tz, **CONTENT_SLOTS["sunday_educational"]),
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

    # Record what the daemon actually registered, so the health report can
    # check the running scheduler instead of re-deriving the same config it
    # was built from. A report that only reads CONTENT_SLOTS prints an
    # identical "next scheduled content" list whether the daemon holds those
    # jobs or none at all — which is the gap the 39-day outage lived in.
    # Guarded: this is bookkeeping, and bookkeeping must never be the reason
    # the scheduler fails to start. An import error here would do exactly what
    # the outage did — kill the daemon one line before it begins.
    try:
        from apscheduler.events import EVENT_SCHEDULER_STARTED
        scheduler.add_listener(
            lambda event: _write_registered_jobs(scheduler), EVENT_SCHEDULER_STARTED)
    except Exception as exc:
        logger.warning("Job-table listener unavailable (non-fatal): %s", exc)

    logger.info("DriftWire326 Scheduler starting — %d jobs registered", len(scheduler.get_jobs()))
    for job in scheduler.get_jobs():
        # Jobs added before start() are pending and their next_run_time slot is
        # unassigned — reading it directly raises AttributeError and killed the
        # daemon on every boot. getattr keeps the listing informational.
        next_run = getattr(job, "next_run_time", None) or "on first trigger"
        logger.info("  Job: %s | Next run: %s", job.name, next_run)

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Scheduler stopped by user")
        scheduler.shutdown()


if __name__ == "__main__":
    start_scheduler()
