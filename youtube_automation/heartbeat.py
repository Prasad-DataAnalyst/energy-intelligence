#!/usr/bin/env python3
"""
heartbeat.py — Dead-man's switch for the daily pipeline.

Closes the last silent-failure gap: today, if the cron never fires (VM down,
crontab wiped, clock wrong), *nothing* tells you — no error, no email, just no
video. Two layers fix that:

1. On-VM staleness check (this script, run by cron at 12:00 UTC daily):
     python3 heartbeat.py --check
   Reads logs/last_success.txt (written by run_daily.py after every successful
   run). If the last success is older than MAX_AGE_HOURS, emails an alert.
   Catches: pipeline failing repeatedly, cron misconfigured, upload dead.

2. External ping (optional but recommended — catches total VM death):
   Set HEALTHCHECK_URL in .env to a free https://healthchecks.io check URL
   (create one, set schedule "daily", grace 6h). run_daily.py pings it after
   every successful run. If the VM dies, healthchecks.io notices the MISSING
   ping and emails you — something no on-VM cron can do.

Also usable manually:  python3 heartbeat.py          # show status
"""
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")

STAMP_FILE    = HERE / "logs" / "last_success.txt"
# 28h, not 30: a slow (retried) run stamps ~7:00 UTC, so at the next day's
# 12:00 check a failure is 29h old — 30 would wave it through and the alert
# would arrive a full day late.
MAX_AGE_HOURS = 28


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record_success() -> None:
    """Called by run_daily.py after a fully successful run:
    stamp the time locally and ping the external healthcheck if configured."""
    STAMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    STAMP_FILE.write_text(_utcnow().isoformat())

    url = os.getenv("HEALTHCHECK_URL", "").strip()
    if url:
        try:
            import requests
            requests.get(url, timeout=10)
            print("  [INFO] Healthcheck pinged")
        except Exception as e:
            print(f"  [INFO] Healthcheck ping failed (non-fatal): {e}")


def last_success() -> datetime | None:
    if not STAMP_FILE.exists():
        return None
    try:
        return datetime.fromisoformat(STAMP_FILE.read_text().strip())
    except Exception:
        return None


def _email(subject: str, body: str) -> None:
    import smtplib
    from email.mime.text import MIMEText
    pw  = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    who = os.getenv("GMAIL_ADDRESS", "prasad2t@gmail.com")
    if not pw:
        print("  [WARN] GMAIL_APP_PASSWORD not set — cannot send alert email")
        return
    msg = MIMEText(body)
    msg["Subject"], msg["From"], msg["To"] = subject, who, who
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(who, pw)
            s.sendmail(who, who, msg.as_string())
        print(f"  [INFO] Alert email sent to {who}")
    except Exception as e:
        print(f"  [WARN] Alert email failed: {e}")


def check(alert: bool = True) -> bool:
    """Return True if the pipeline is healthy (recent success)."""
    last = last_success()
    now  = _utcnow()

    if last is None:
        msg = ("No successful pipeline run recorded yet "
               f"({STAMP_FILE} missing). Either the pipeline has never "
               "succeeded since this check was installed, or the stamp was deleted.")
        print(f"  ❌ {msg}")
        if alert:
            _email("GetMindFuelNow ALERT — no successful run recorded", msg)
        return False

    age_h = (now - last).total_seconds() / 3600
    if age_h > MAX_AGE_HOURS:
        msg = (f"Last successful daily video was {age_h:.0f} hours ago "
               f"({last:%Y-%m-%d %H:%M} UTC). The 5:30 AM pipeline has NOT "
               f"succeeded since. Check:\n"
               f"  tail -50 ~/daily_horoscope.log\n"
               f"  python3 doctor.py\n"
               f"  crontab -l")
        print(f"  ❌ STALE: {msg}")
        if alert:
            _email(f"GetMindFuelNow ALERT — no video for {age_h:.0f}h", msg)
        return False

    print(f"  ✅ Healthy — last success {age_h:.1f}h ago ({last:%Y-%m-%d %H:%M} UTC)")
    return True


def _post_due_comments() -> None:
    """Post any queued pinned comments whose video has gone public. Publishes
    are staggered 7-11 AM US Eastern per content type (comments become due
    10 min after each publish, i.e. up to 16:10 UTC in EST months), so the
    hourly `--comments` cron (11:15-17:15 UTC) catches each one within ~1h
    of its video going live. Free when the queue is empty (no API calls)."""
    try:
        import os as _os
        _os.chdir(HERE)          # uploader paths (logs/, token) are CWD-relative
        from youtube_uploader import process_pending_comments
        n = process_pending_comments()
        if n:
            print(f"  [INFO] Posted {n} pending comment(s)")
    except Exception as e:
        print(f"  [INFO] Pending comments skipped: {e}")


def main():
    if "--comments" in sys.argv:
        # Comments-only pass (hourly cron): never emails, never alerts.
        _post_due_comments()
        sys.exit(0)
    if "--check" in sys.argv:
        _post_due_comments()
        sys.exit(0 if check(alert=True) else 1)
    # No args: just report status without emailing.
    check(alert=False)


if __name__ == "__main__":
    main()
