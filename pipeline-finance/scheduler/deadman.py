"""
scheduler/deadman.py — DriftWire326 reliability layer
Dead-man's switch: the single most important alarm in the system.

Runs at 18:00 ET on content days. If no video was uploaded today
(per logs/upload_manifest.jsonl), sends an email alert via the same
SMTP settings the channel monitor uses. Silence must never mean failure.

Also exposes retry_if_needed(), scheduled ~30 minutes after each main
pipeline slot: if today's pipeline started but didn't finish, it re-runs
it — the checkpoint layer makes the re-run cheap (completed steps are
skipped, the Claude call is not repeated).
"""
import logging
import os
import smtplib
from datetime import date, datetime
from email.mime.text import MIMEText

from config.settings import settings

logger = logging.getLogger(__name__)


def _send_email(subject: str, body: str) -> bool:
    """Send an alert email using SMTP settings from .env (same as monitor)."""
    smtp_host = os.environ.get("SMTP_HOST", "")
    smtp_port = int(os.environ.get("SMTP_PORT", "587") or 587)
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASS", "")
    alert_email = os.environ.get("ALERT_EMAIL", smtp_user)

    # Always leave a visible artifact on disk, even when email works — so a
    # human inspecting the VM sees outages without reading the full log.
    try:
        alert_file = settings.logs_dir / "ALERT_LATEST.txt"
        settings.logs_dir.mkdir(parents=True, exist_ok=True)
        alert_file.write_text(
            f"{datetime.now().isoformat()}\n{subject}\n\n{body}\n", encoding="utf-8"
        )
    except Exception as exc:
        logger.error("Could not write alert file: %s", exc)

    if not all([smtp_host, smtp_user, smtp_pass, alert_email]):
        # Loud: unconfigured alerting is why an outage can run for days unnoticed.
        logger.error(
            "ALERT NOT DELIVERED — SMTP is not configured in .env, so this "
            "outage would go unnoticed. Set SMTP_HOST/SMTP_USER/SMTP_PASS/"
            "ALERT_EMAIL. Alert written to logs/ALERT_LATEST.txt: %s", subject,
        )
        return False

    try:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = smtp_user
        msg["To"] = alert_email
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, alert_email, msg.as_string())
        logger.info("Dead-man alert email sent to %s", alert_email)
        return True
    except Exception as exc:
        logger.error("Dead-man alert email failed: %s", exc)
        return False


def check_todays_upload(is_content_day: bool = True) -> bool:
    """
    Dead-man's switch entrypoint (scheduled 18:00 ET).
    Returns True if today's upload is confirmed, False if the alert fired.
    """
    if not is_content_day:
        logger.info("Dead-man check skipped — not a content day")
        return True

    from scheduler.pipeline_state import todays_upload_recorded
    if todays_upload_recorded():
        # An upload that never became visible is indistinguishable from no
        # upload at all. The manifest only records that the API call
        # succeeded, which is how four private videos passed this check for
        # days while the channel stayed empty.
        try:
            from monitor.health_report import _check_video_visibility
            status, _, detail, _ = _check_video_visibility()
            from monitor.health_report import _FAIL as _VIS_FAIL
            if status == _VIS_FAIL:
                logger.error("Uploads recorded but not visible: %s", detail)
                _send_email(
                    f"[DriftWire326] UPLOADED BUT NOT PUBLIC — {date.today()}",
                    f"An upload was recorded for {date.today()}, but the "
                    f"video is not visible to anyone:\n\n{detail}\n\n"
                    "This looks identical to a healthy day everywhere except "
                    "here.\n\nFix:\n"
                    "  main.py --publish-private\n")
                return False
        except Exception as exc:
            logger.warning("Visibility check skipped in dead-man: %s", exc)

        logger.info("Dead-man check OK — upload recorded and visible for %s",
                    date.today())
        return True

    # Gather diagnostic context for the alert
    diagnostics: list[str] = []
    try:
        from scheduler.pipeline_state import PipelineState, state_files
        pipeline = "sunday" if date.today().weekday() == 6 else "weekday"
        # One summary per slot: the weekday pipeline has two, and reporting
        # only the default one would describe a run that may not exist.
        paths = state_files(pipeline)
        if not paths:
            diagnostics.append(f"Pipeline state: no {pipeline} run started today")
        for path in paths:
            slot = path.stem[len(pipeline) + 1:-len(date.today().isoformat()) - 1]
            summary = PipelineState(pipeline, slot=slot or None).summary()
            diagnostics.append(f"Pipeline state [{slot or 'default'}]: {summary}")
    except Exception as exc:
        diagnostics.append(f"(could not read pipeline state: {exc})")

    try:
        from uploader.quota_tracker import QuotaTracker
        diagnostics.append(f"Quota remaining: {QuotaTracker().get_remaining()}")
    except Exception as exc:
        diagnostics.append(f"(could not read quota: {exc})")

    body = (
        f"🚨 DriftWire326 DEAD-MAN ALERT\n\n"
        f"No YouTube upload recorded for {date.today().isoformat()} as of "
        f"{datetime.now().strftime('%H:%M')}.\n\n"
        + "\n".join(diagnostics)
        + "\n\nCheck logs/pipeline_state/ and logs/failed_queue.json, "
        "then run: python main.py --run weekday (resumes from checkpoint)."
    )
    logger.error("DEAD-MAN ALERT: no upload recorded for %s", date.today())
    _send_email(f"[DriftWire326] NO UPLOAD TODAY — {date.today()}", body)

    # Optional last resort: publish the emergency fallback video so the
    # channel never goes dark. Off by default — enable with
    # FALLBACK_AUTO_UPLOAD=true in .env once you trust the template.
    if os.environ.get("FALLBACK_AUTO_UPLOAD", "").lower() == "true":
        try:
            from builders.fallback_builder import build_and_upload_fallback
            video_id = build_and_upload_fallback()
            if video_id:
                _send_email(
                    f"[DriftWire326] Fallback video published — {date.today()}",
                    f"Emergency fallback video uploaded: https://youtu.be/{video_id}",
                )
        except Exception as exc:
            logger.error("Fallback auto-upload failed: %s", exc)

    return False


def retry_if_needed(pipeline: str = "weekday") -> None:
    """
    Retry job scheduled ~30 min after each main slot. If today's pipeline
    did not reach success, re-run it — checkpoints make this cheap.
    """
    from scheduler.pipeline_state import incomplete_slots
    pending = incomplete_slots(pipeline)
    if not pending:
        logger.debug("retry_if_needed(%s): nothing to retry", pipeline)
        return

    for slot in pending:
        logger.warning("retry_if_needed(%s/%s): incomplete — re-running from checkpoint",
                       pipeline, slot or "default")
        try:
            if pipeline == "sunday":
                from scheduler.sunday_scheduler import SundayScheduler
                SundayScheduler().run()
            else:
                # Retry the slot that actually stalled. Re-running the default
                # would resume the morning checkpoint and leave an unfinished
                # evening run untouched.
                from scheduler.weekday_scheduler import WeekdayScheduler
                WeekdayScheduler(slot=slot or "premarket").run()
        except Exception as exc:
            logger.error("retry_if_needed(%s/%s) failed: %s", pipeline, slot, exc)
