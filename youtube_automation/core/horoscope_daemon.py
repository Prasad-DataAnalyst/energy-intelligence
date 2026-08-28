"""
core/horoscope_daemon.py
GetMindFuelNow — 24/7 Horoscope Automation Daemon

Schedule:
  Daily      — every day at 19:00 UTC
  Weekly     — every Monday at 18:00 UTC
  Monthly    — 1st of each month at 17:00 UTC
  Yearly     — January 1st at 12:00 UTC
  Special    — checked daily; events run at 20:00 UTC day-of

Features:
  - Writes heartbeat every 60 seconds to logs/horoscope_daemon_heartbeat.json
  - Handles SIGTERM / SIGINT cleanly (finishes current run before exit)
  - Processes upload retry queue on startup
  - Prevents duplicate runs on the same date+type via run-lock files

Usage:
  python core/horoscope_daemon.py
  python core/horoscope_daemon.py --schedule   (same, kept for symmetry)
  nohup python core/horoscope_daemon.py >> logs/daemon.log 2>&1 &
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

# ── Logging ───────────────────────────────────────────────────────────────────
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

log_file = LOGS_DIR / f"horoscope_daemon_{datetime.date.today().isoformat()}.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("horoscope_daemon")

# ── Paths ──────────────────────────────────────────────────────────────────────
HEARTBEAT_PATH = LOGS_DIR / "horoscope_daemon_heartbeat.json"
LOCKS_DIR = LOGS_DIR / "locks"
LOCKS_DIR.mkdir(exist_ok=True)

# ── Schedule (HH:MM UTC) ───────────────────────────────────────────────────────
# Pipeline takes ~30 minutes to generate + upload.
# Start times are chosen so video is LIVE before US viewers wake up:
#
#  UTC 05:00 = 1:00 AM EDT = 10:00 PM PDT  →  live by 05:30 UTC
#  US East Coast wakes 7 AM EDT = 11:00 UTC → video is 5.5 hrs old, has early views
#  YouTube algorithm rewards fresh engagement in first hour from night-owl viewers.
#
# Type         Start UTC    Live UTC    US East live     Why
# daily        05:00        05:30       1:30 AM EDT      Ready when US wakes up
# weekly (Mon) 04:30        05:00       1:00 AM EDT      Monday morning commute
# monthly (1st)04:00        04:30       12:30 AM EDT     1st-of-month searchers
# yearly       03:00        03:30       11:30 PM EST     New Year's night viewers
# special event05:00        05:30       1:30 AM EDT      Day-of event searches
SCHEDULE: dict[str, str] = {
    "daily":         "05:00",
    "weekly":        "04:30",   # Mondays
    "monthly":       "04:00",   # 1st of month
    "yearly":        "03:00",   # January 1st
    "special_event": "05:00",   # day-of astronomical event
}

# ── State ─────────────────────────────────────────────────────────────────────
_SHUTDOWN_REQUESTED = False
_CURRENT_RUN_TYPE: str | None = None


def _handle_signal(signum: int, frame: object) -> None:
    """Handle SIGTERM / SIGINT — request clean shutdown after current run."""
    global _SHUTDOWN_REQUESTED
    sig_name = signal.Signals(signum).name
    log.info(f"Signal received: {sig_name} — requesting shutdown after current run")
    _SHUTDOWN_REQUESTED = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT, _handle_signal)


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------

def _write_heartbeat(extra: dict | None = None) -> None:
    """Write daemon status to heartbeat file."""
    try:
        payload = {
            "status": "running" if not _SHUTDOWN_REQUESTED else "shutdown_requested",
            "timestamp": datetime.datetime.utcnow().isoformat(),
            "pid": os.getpid(),
            "current_run": _CURRENT_RUN_TYPE,
        }
        if extra:
            payload.update(extra)
        HEARTBEAT_PATH.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        log.debug(f"Heartbeat write failed: {e}")


# ---------------------------------------------------------------------------
# Run-lock helpers (prevent duplicate runs)
# ---------------------------------------------------------------------------

def _lock_path(video_type: str, date: datetime.date) -> Path:
    return LOCKS_DIR / f"{video_type}_{date.isoformat()}.lock"


def _is_locked(video_type: str, date: datetime.date) -> bool:
    return _lock_path(video_type, date).exists()


def _acquire_lock(video_type: str, date: datetime.date) -> None:
    lp = _lock_path(video_type, date)
    lp.write_text(json.dumps({
        "video_type": video_type,
        "date": date.isoformat(),
        "started_at": datetime.datetime.utcnow().isoformat(),
        "pid": os.getpid(),
    }))


def _release_lock(video_type: str, date: datetime.date) -> None:
    lp = _lock_path(video_type, date)
    try:
        lp.unlink()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Schedule checking
# ---------------------------------------------------------------------------

def _is_past_scheduled_time(target_hhmm: str, now: datetime.datetime) -> bool:
    """Return True if now is at or past target_hhmm today (UTC)."""
    h, m = map(int, target_hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return now >= target


def _should_run(video_type: str, now: datetime.datetime) -> bool:
    """
    Determine if a specific video type should run now.

    Strategy: catch-up scheduling.
      If the scheduled time has passed today and we haven't run yet → run now.
      This means a daemon that starts at 23:00 will still produce today's video.

    Catch-up windows (so we don't upload a day-late video):
      daily:         19:00–23:59 UTC same day
      weekly:        18:00–23:59 UTC same Monday
      monthly:       17:00–23:59 UTC same 1st-of-month
      yearly:        12:00–23:59 UTC same Jan 1st
      special_event: 20:00–23:59 UTC same event day
    """
    today = now.date()

    # Already ran today for this type? Skip.
    if _is_locked(video_type, today):
        return False

    upload_time = SCHEDULE[video_type]

    # Not past the scheduled time yet? Wait.
    if not _is_past_scheduled_time(upload_time, now):
        return False

    # Past window close (midnight UTC) — don't upload a stale video
    # (We allow the full rest of the day after the scheduled time.)

    if video_type == "daily":
        return True

    if video_type == "weekly":
        return today.weekday() == 0  # Monday

    if video_type == "monthly":
        return today.day == 1

    if video_type == "yearly":
        return today.month == 1 and today.day == 1

    if video_type == "special_event":
        from modules.horoscope.video_types import is_special_event_day
        return is_special_event_day(today)

    return False


# ---------------------------------------------------------------------------
# Single-type runner (called from main loop)
# ---------------------------------------------------------------------------

def _safe_run(video_type: str, date: datetime.date, event_name: str = "",
               event_type: str = "") -> bool:
    """
    Execute one horoscope pipeline run safely.
    Runs pre-flight checks first; sends email alerts on failure.
    Returns True on success.
    """
    global _CURRENT_RUN_TYPE
    _CURRENT_RUN_TYPE = video_type

    if _is_locked(video_type, date):
        log.info(f"Already ran {video_type} for {date} — skipping")
        _CURRENT_RUN_TYPE = None
        return True

    # ── Pre-flight checks ────────────────────────────────────────────────────
    try:
        from core.preflight import run_preflight
        can_run, check_results = run_preflight(video_type)
        if not can_run:
            log.error(f"Pre-flight BLOCKED {video_type} — see email for details")
            _CURRENT_RUN_TYPE = None
            return False
    except Exception as pf_err:
        log.warning(f"Pre-flight check error (proceeding anyway): {pf_err}")

    _acquire_lock(video_type, date)
    log.info(f"Starting {video_type.upper()} run for {date}")

    try:
        from core.horoscope_pipeline import run_horoscope_pipeline
        report = run_horoscope_pipeline(
            video_type=video_type,
            date=date,
            upload=True,
            event_name=event_name,
            event_type=event_type,
        )
        if report.get("success"):
            log.info(f"{video_type.upper()} run completed successfully")
            # Send success email if SEND_SUCCESS_EMAIL=true
            try:
                from core.preflight import send_success_summary_email
                url = report.get("output_files", {}).get("youtube_url", "")
                elapsed_str = report.get("total_elapsed", "0min")
                elapsed_min = float(elapsed_str.replace("min","").replace("s","")) / (1 if "min" in elapsed_str else 60)
                send_success_summary_email(video_type, date, url, elapsed_min)
            except Exception:
                pass
            return True
        else:
            errors = report.get("errors", [])
            log.warning(f"{video_type.upper()} run completed with errors: {errors}")
            # Email on pipeline stage failure
            if errors:
                try:
                    from core.preflight import send_pipeline_failure_email
                    failed_stage = errors[0].split(":")[0] if errors else "unknown"
                    send_pipeline_failure_email(video_type, failed_stage,
                                               "\n".join(errors), date)
                except Exception:
                    pass
            return len(errors) == 0

    except Exception as e:
        log.error(f"{video_type.upper()} run crashed: {e}\n{traceback.format_exc()}")
        try:
            from core.preflight import send_pipeline_failure_email
            send_pipeline_failure_email(video_type, "pipeline_crash", str(e), date)
        except Exception:
            pass
        return False

    finally:
        _CURRENT_RUN_TYPE = None
        # Keep lock to prevent double-runs on same day


# ---------------------------------------------------------------------------
# Startup: process retry queue
# ---------------------------------------------------------------------------

def _startup_tasks() -> None:
    """Tasks to run once when the daemon starts."""
    log.info("Daemon starting up — processing upload retry queue")
    try:
        from core.horoscope_pipeline import process_retry_queue
        n = process_retry_queue()
        log.info(f"Retry queue: processed {n} item(s)")
    except Exception as e:
        log.warning(f"Retry queue processing failed: {e}")

    # Update channel bio/keywords (runs at most once per week)
    try:
        from youtube_uploader import update_channel_branding
        update_channel_branding()
    except Exception as e:
        log.debug(f"Channel branding update on startup skipped: {e}")

    # Clean up old locks (> 25 hours)
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(hours=25)
    for lock_file in LOCKS_DIR.glob("*.lock"):
        try:
            data = json.loads(lock_file.read_text())
            started = datetime.datetime.fromisoformat(data.get("started_at", "2000-01-01"))
            if started < cutoff:
                lock_file.unlink()
                log.debug(f"Removed stale lock: {lock_file.name}")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main daemon loop
# ---------------------------------------------------------------------------

def run_daemon() -> None:
    """Main 24/7 scheduling loop."""
    log.info("=" * 60)
    log.info("GetMindFuelNow Horoscope Daemon starting")
    log.info(f"PID: {os.getpid()}")
    log.info(f"Log: {log_file}")
    log.info("=" * 60)
    log.info("Schedule (UTC → US Eastern):")
    log.info(f"  Daily:         every day       {SCHEDULE['daily']} UTC = ~1:00 AM EDT")
    log.info(f"  Weekly:        every Monday    {SCHEDULE['weekly']} UTC = ~12:30 AM EDT")
    log.info(f"  Monthly:       1st of month    {SCHEDULE['monthly']} UTC = ~12:00 AM EDT")
    log.info(f"  Yearly:        January 1st     {SCHEDULE['yearly']} UTC = ~11:00 PM EST")
    log.info(f"  Special Event: day-of-event    {SCHEDULE['special_event']} UTC = ~1:00 AM EDT")
    log.info("  Videos go LIVE ~30min after start — ready before US wakes up")
    log.info("=" * 60)

    _startup_tasks()
    _write_heartbeat({"event": "startup"})

    last_heartbeat = time.time()
    HEARTBEAT_INTERVAL = 60  # seconds

    while not _SHUTDOWN_REQUESTED:
        now_utc = datetime.datetime.utcnow()
        today = now_utc.date()

        # Check all video types
        for vtype in ["yearly", "monthly", "weekly", "special_event", "daily"]:
            if _SHUTDOWN_REQUESTED:
                break

            if _should_run(vtype, now_utc):
                ev_name = ev_type = ""
                if vtype == "special_event":
                    try:
                        from modules.horoscope.video_types import get_event_for_date
                        ev = get_event_for_date(today)
                        if ev:
                            ev_name = ev["name"]
                            ev_type = ev["type"]
                    except Exception:
                        pass

                log.info(f"Triggering {vtype} run")
                success = _safe_run(vtype, today, ev_name, ev_type)
                _write_heartbeat({
                    "last_run_type": vtype,
                    "last_run_success": success,
                    "last_run_at": now_utc.isoformat(),
                })

        # Write heartbeat every 60 seconds
        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL:
            _write_heartbeat()
            last_heartbeat = time.time()

        # Sleep 30 seconds between schedule checks
        time.sleep(30)

    log.info("Shutdown requested — daemon exiting cleanly")
    _write_heartbeat({"event": "shutdown"})
    sys.exit(0)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # ── RETIRED ────────────────────────────────────────────────────────────────
    # This standalone daemon was a SECOND, parallel uploader that produced the
    # old-format 7-minute videos and per-sign shorts. It is retired: the cron
    # pipeline (run_daily.py) is now the single source of truth. Whatever
    # relaunches this (a stray systemd unit, a root @reboot cron, rc.local, or
    # the start_scheduler.sh watchdog) now hits this guard and exits WITHOUT
    # uploading anything. Escape hatch for debugging only: ALLOW_LEGACY_DAEMON=1.
    if os.getenv("ALLOW_LEGACY_DAEMON") != "1":
        print("[RETIRED] horoscope_daemon.py is disabled — the cron pipeline "
              "(run_daily.py) is the only uploader. Exiting without action.",
              file=sys.stderr)
        sys.exit(0)

    import argparse
    parser = argparse.ArgumentParser(description="GetMindFuelNow Horoscope Daemon")
    parser.add_argument("--schedule", action="store_true", help="Alias for running the daemon (default behavior)")
    parser.add_argument("--once",     action="store_true", help="Run once immediately and exit")
    parser.add_argument("--type",     type=str, default="daily",
                        choices=["daily", "weekly", "monthly", "yearly", "special_event"],
                        help="Which type to run with --once")
    args = parser.parse_args()

    if args.once:
        # One-shot run for testing
        log.info(f"One-shot run: {args.type}")
        _startup_tasks()
        _safe_run(args.type, datetime.date.today())
    else:
        run_daemon()
