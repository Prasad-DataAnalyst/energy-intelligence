"""
core/automation_daemon.py
Persistent daily automation daemon.

Responsibilities:
  • Run the full cinematic pipeline at 15:00 UTC every day
  • Process the upload retry queue on startup
  • Resume any pending post_scheduler actions on startup
  • Write a heartbeat file every 60 s so external monitors can detect hangs
  • Handle SIGTERM / SIGINT for clean shutdown
  • Never exit on pipeline errors — log and wait for the next scheduled run

Start via:  bash start_scheduler.sh        (watchdog, recommended)
            python core/automation_daemon.py  (direct, foreground)
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

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))

# ── Logging ────────────────────────────────────────────────────────────────────
LOGS_DIR = HERE / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "daemon.log"),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger("daemon")

_HEARTBEAT_FILE = LOGS_DIR / "daemon_heartbeat.txt"
_STATUS_FILE    = LOGS_DIR / "daemon_status.json"

# ── Upload time ────────────────────────────────────────────────────────────────
try:
    import config
    _UPLOAD_HOUR, _UPLOAD_MIN = map(int, config.UPLOAD_TIME.split(":"))
except Exception:
    _UPLOAD_HOUR, _UPLOAD_MIN = 15, 0

# ── Graceful shutdown ──────────────────────────────────────────────────────────
_SHUTDOWN = False


def _handle_signal(sig, _frame):
    global _SHUTDOWN
    log.info("Signal %s received — shutting down after current task", sig)
    _SHUTDOWN = True


signal.signal(signal.SIGTERM, _handle_signal)
signal.signal(signal.SIGINT,  _handle_signal)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _write_heartbeat(status: str = "idle") -> None:
    try:
        _HEARTBEAT_FILE.write_text(
            f"{datetime.datetime.utcnow().isoformat()}Z  status={status}\n"
        )
    except Exception:
        pass


def _write_status(data: dict) -> None:
    try:
        _STATUS_FILE.write_text(json.dumps(data, indent=2, default=str))
    except Exception:
        pass


def _seconds_until_next_run() -> float:
    """Seconds until the next 15:00 UTC run (fires today if not yet passed)."""
    now = datetime.datetime.utcnow()
    target = now.replace(hour=_UPLOAD_HOUR, minute=_UPLOAD_MIN, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


def _startup_tasks() -> None:
    """Tasks to run once at daemon startup."""
    # Resume pending community posts / pinned comments
    try:
        from modules.youtube.post_scheduler import load_and_resume_queue
        n = load_and_resume_queue()
        if n:
            log.info("Resumed %d pending post actions from queue", n)
    except Exception as exc:
        log.warning("post_scheduler resume failed: %s", exc)

    # Retry any previously-failed uploads
    try:
        import youtube_uploader
        youtube_uploader.process_retry_queue()
    except Exception as exc:
        log.warning("Upload retry queue processing failed: %s", exc)

    # Start self-enhancement loop in background
    try:
        from modules.intelligence.self_enhancement_loop import start_background_loop
        start_background_loop()
        log.info("SelfEnhancementLoop background thread started")
    except Exception as exc:
        log.debug("SelfEnhancementLoop skipped: %s", exc)


def _run_pipeline() -> dict:
    """Run the cinematic daily pipeline and return the production report."""
    log.info("=" * 60)
    log.info("STARTING DAILY CINEMATIC PIPELINE")
    log.info("=" * 60)
    _write_heartbeat("running")

    try:
        from core.cinematic_daily_pipeline import run_cinematic_pipeline
        report = run_cinematic_pipeline(upload=True)
    except ImportError:
        # Allow running from inside the youtube_automation directory
        from cinematic_daily_pipeline import run_cinematic_pipeline   # type: ignore
        report = run_cinematic_pipeline(upload=True)

    return report


# ── Main loop ──────────────────────────────────────────────────────────────────

def main() -> None:
    log.info("Automation daemon starting (PID=%d)", os.getpid())
    log.info("Daily run target: %02d:%02d UTC", _UPLOAD_HOUR, _UPLOAD_MIN)

    _startup_tasks()

    last_run_date: str = ""

    # Heartbeat ticker — runs in main loop, updated every iteration
    heartbeat_interval = 60   # seconds

    # Track when we last wrote a heartbeat to avoid excessive writes
    last_heartbeat = 0.0

    while not _SHUTDOWN:
        now = datetime.datetime.utcnow()
        today = now.strftime("%Y-%m-%d")

        # Heartbeat
        if time.time() - last_heartbeat >= heartbeat_interval:
            _write_heartbeat("idle")
            last_heartbeat = time.time()

        # Status file
        secs_to_run = _seconds_until_next_run()
        next_run_dt = now + datetime.timedelta(seconds=secs_to_run)
        _write_status({
            "pid":          os.getpid(),
            "status":       "idle",
            "last_run":     last_run_date or "never",
            "next_run_utc": next_run_dt.strftime("%Y-%m-%d %H:%M UTC"),
            "uptime_s":     int(time.time()),
        })

        # Is it time to run?
        current_hhmm = (now.hour, now.minute)
        is_run_time   = (
            current_hhmm >= (_UPLOAD_HOUR, _UPLOAD_MIN)
            and today != last_run_date
        )

        if is_run_time:
            last_run_date = today
            _write_heartbeat("running")
            run_start = time.time()

            try:
                report = _run_pipeline()
                elapsed = time.time() - run_start
                success = report.get("success", False)
                errors  = report.get("errors", [])

                log.info(
                    "Pipeline completed in %.0fs — %s",
                    elapsed,
                    "SUCCESS" if success else f"ERRORS: {errors}",
                )
                _write_status({
                    "pid":          os.getpid(),
                    "status":       "idle",
                    "last_run":     today,
                    "last_success": success,
                    "last_errors":  errors,
                    "next_run_utc": (
                        datetime.datetime.utcnow()
                        + datetime.timedelta(seconds=_seconds_until_next_run())
                    ).strftime("%Y-%m-%d %H:%M UTC"),
                })

            except Exception:
                log.error("Pipeline crashed:\n%s", traceback.format_exc())
                _write_heartbeat("error")

            _write_heartbeat("idle")

        # Retry queue — check once per hour at :30 past the hour
        if now.minute == 30 and now.second < 30:
            try:
                import youtube_uploader
                youtube_uploader.process_retry_queue()
            except Exception as exc:
                log.debug("Retry queue check: %s", exc)

        # Sleep in small increments so we respond to SIGTERM promptly
        for _ in range(30):
            if _SHUTDOWN:
                break
            time.sleep(1)

    log.info("Daemon shut down cleanly")


if __name__ == "__main__":
    main()
