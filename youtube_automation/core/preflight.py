"""
core/preflight.py
GetMindFuelNow — Pre-flight health checks, auto-correction, and email alerts.

Runs before every pipeline to:
  1. Check disk space, memory, OAuth token, ffmpeg, Python packages, network
  2. Auto-fix what it can (disk cleanup, pip install, token refresh)
  3. Email prasad2t@gmail.com when issues need manual action

One-time email setup (3 minutes):
  1. myaccount.google.com → Security → 2-Step Verification (enable if not on)
  2. myaccount.google.com → Security → App passwords
  3. Create password → name "GetMindFuelNow VM" → copy the 16-char password
  4. Add to .env:  GMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import shutil
import smtplib
import subprocess
import sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import NamedTuple

HERE = Path(__file__).parent.parent
sys.path.insert(0, str(HERE))

log = logging.getLogger("preflight")

ALERT_EMAIL = "prasad2t@gmail.com"

# Minimum free disk (GB) needed per video type
_MIN_DISK_GB = {
    "daily": 2.0,
    "weekly": 4.0,
    "monthly": 8.0,
    "yearly": 16.0,
    "special_event": 3.0,
}


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class CheckResult(NamedTuple):
    name: str
    ok: bool
    message: str
    auto_fixed: bool = False
    fix_message: str = ""


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(subject: str, html_body: str) -> bool:
    """Send HTML email via Gmail SMTP. Requires GMAIL_APP_PASSWORD in .env."""
    try:
        from dotenv import load_dotenv
        load_dotenv(HERE / ".env")
    except Exception:
        pass

    password = os.getenv("GMAIL_APP_PASSWORD", "").strip()
    if not password:
        log.warning(
            "GMAIL_APP_PASSWORD not set — email alerts disabled.\n"
            "  Setup: myaccount.google.com → Security → App passwords\n"
            "  Then add GMAIL_APP_PASSWORD=xxxx to .env"
        )
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = ALERT_EMAIL
        msg["To"] = ALERT_EMAIL
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(ALERT_EMAIL, password)
            smtp.sendmail(ALERT_EMAIL, ALERT_EMAIL, msg.as_string())

        log.info("Alert email sent → %s  [%s]", ALERT_EMAIL, subject[:60])
        return True
    except smtplib.SMTPAuthenticationError:
        log.warning(
            "Gmail auth failed — check GMAIL_APP_PASSWORD in .env\n"
            "  App passwords require 2-Step Verification enabled on Google account."
        )
        return False
    except Exception as e:
        log.warning("Email send failed: %s", e)
        return False


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_disk_space(video_type: str) -> CheckResult:
    min_gb = _MIN_DISK_GB.get(video_type, 2.0)
    stat = shutil.disk_usage("/")
    available_gb = stat.free / (1024 ** 3)

    if available_gb >= min_gb + 0.5:
        return CheckResult("disk_space", True, f"{available_gb:.1f}GB free (need {min_gb}GB + 0.5GB safety margin)")

    # Auto-fix: delete output dirs older than 7 days
    freed_gb = 0.0
    output_dir = HERE / "output"
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=7)

    if output_dir.exists():
        for d in sorted(output_dir.iterdir()):
            if not d.is_dir():
                continue
            mtime = datetime.datetime.utcfromtimestamp(d.stat().st_mtime)
            if mtime < cutoff:
                size = sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
                shutil.rmtree(d, ignore_errors=True)
                freed_gb += size / (1024 ** 3)
                log.info("Disk cleanup: removed %s (%.0fMB)", d.name, size / 1_048_576)

    stat2 = shutil.disk_usage("/")
    avail2 = stat2.free / (1024 ** 3)

    if avail2 >= min_gb + 0.5:
        return CheckResult(
            "disk_space", True,
            f"{avail2:.1f}GB free after cleanup",
            auto_fixed=True,
            fix_message=f"Freed {freed_gb:.1f}GB by removing output files older than 7 days",
        )

    return CheckResult(
        "disk_space", False,
        f"Only {avail2:.1f}GB free — need {min_gb}GB for {video_type}. "
        f"Freed {freed_gb:.1f}GB but still insufficient.",
        auto_fixed=freed_gb > 0,
        fix_message=f"Freed {freed_gb:.1f}GB but need {min_gb - avail2:.1f}GB more — manual cleanup required",
    )


def _check_memory() -> CheckResult:
    try:
        with open("/proc/meminfo") as f:
            info = {line.split(":")[0]: int(line.split()[1])
                    for line in f if ":" in line and line.split()[1].isdigit()}
        avail_mb = info.get("MemAvailable", 0) // 1024
        if avail_mb >= 150:
            return CheckResult("memory", True, f"{avail_mb}MB RAM available")
        return CheckResult(
            "memory", False,
            f"Low RAM: {avail_mb}MB free (ffmpeg may be slow — swap will be used)",
        )
    except Exception as e:
        return CheckResult("memory", True, f"Memory check skipped: {e}")


def _check_oauth_token() -> CheckResult:
    token_path = HERE / "youtube_token.json"
    if not token_path.exists():
        return CheckResult(
            "oauth_token", False,
            "youtube_token.json missing — run: python first_time_setup.py --no-upload",
        )
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        import config

        creds = Credentials.from_authorized_user_file(str(token_path), config.YOUTUBE_SCOPES)
        if creds.valid:
            return CheckResult("oauth_token", True, "OAuth token valid")

        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            return CheckResult(
                "oauth_token", True, "OAuth token refreshed",
                auto_fixed=True, fix_message="Token expired — auto-refreshed",
            )

        return CheckResult(
            "oauth_token", False,
            "Token invalid and cannot refresh — run: python first_time_setup.py --no-upload",
        )
    except Exception as e:
        return CheckResult("oauth_token", False, f"Token check error: {e}")


def _check_ffmpeg() -> CheckResult:
    try:
        r = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0:
            ver = r.stdout.splitlines()[0][:70] if r.stdout else "ok"
            return CheckResult("ffmpeg", True, ver)
        return CheckResult("ffmpeg", False, "ffmpeg returned non-zero exit")
    except FileNotFoundError:
        return CheckResult(
            "ffmpeg", False,
            "ffmpeg not installed — run: sudo apt-get install -y ffmpeg",
        )
    except Exception as e:
        return CheckResult("ffmpeg", False, f"ffmpeg check error: {e}")


def _check_python_packages() -> CheckResult:
    required = {
        "edge_tts":        "edge-tts",
        "PIL":             "Pillow",
        "googleapiclient": "google-api-python-client",
        "requests":        "requests",
    }
    missing = []
    for mod, pkg in required.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if not missing:
        return CheckResult("python_packages", True, "All packages present")

    # Auto-install missing packages
    fixed, failed = [], []
    python = sys.executable
    for pkg in missing:
        try:
            log.info("Auto-installing: %s", pkg)
            subprocess.run(
                [python, "-m", "pip", "install", "--quiet", pkg],
                capture_output=True, timeout=300, check=True,
            )
            fixed.append(pkg)
        except Exception as e:
            log.warning("pip install %s failed: %s", pkg, e)
            failed.append(pkg)

    # Re-check after installs
    still_missing = []
    for mod in required:
        try:
            __import__(mod)
        except ImportError:
            still_missing.append(required[mod])

    if not still_missing:
        return CheckResult(
            "python_packages", True, "All packages available after auto-install",
            auto_fixed=True, fix_message=f"Auto-installed: {', '.join(fixed)}",
        )

    return CheckResult(
        "python_packages", False,
        f"Auto-install failed for: {', '.join(still_missing)}",
        auto_fixed=bool(fixed),
        fix_message=f"Installed {', '.join(fixed)} but {', '.join(still_missing)} still missing",
    )


def _check_network() -> CheckResult:
    import urllib.request
    for url in ["https://www.google.com", "https://8.8.8.8"]:
        try:
            urllib.request.urlopen(url, timeout=8)
            return CheckResult("network", True, "Internet reachable")
        except Exception:
            continue
    return CheckResult(
        "network", False,
        "No internet — edge-tts voiceover and YouTube upload will fail",
    )


def _check_retry_queue() -> CheckResult:
    q_path = HERE / "logs" / "upload_retry_queue.json"
    if not q_path.exists():
        return CheckResult("retry_queue", True, "Retry queue empty")
    try:
        items = json.loads(q_path.read_text()) or []
        if not items:
            return CheckResult("retry_queue", True, "Retry queue empty")

        now = datetime.datetime.utcnow()
        old = [i for i in items
               if (now - datetime.datetime.fromisoformat(
                   i.get("queued_at", now.isoformat())
               )).days >= 3]

        if old:
            titles = [i.get("metadata", {}).get("title", "?")[:40] for i in old[:3]]
            return CheckResult(
                "retry_queue", False,
                f"{len(items)} queued upload(s); {len(old)} stuck >3 days: {'; '.join(titles)}",
            )

        return CheckResult(
            "retry_queue", True,
            f"{len(items)} pending upload(s) — will auto-retry on next daemon start",
        )
    except Exception as e:
        return CheckResult("retry_queue", True, f"Queue check skipped: {e}")


# ---------------------------------------------------------------------------
# Main preflight runner
# ---------------------------------------------------------------------------

def run_preflight(video_type: str = "daily") -> tuple[bool, list[CheckResult]]:
    """
    Run all pre-flight checks.

    Returns (pipeline_can_run, check_results).
    Sends email alert if issues found and GMAIL_APP_PASSWORD is set.
    Critical failures (disk, oauth, ffmpeg, network) block the pipeline.
    Non-critical warnings let the pipeline proceed.
    """
    log.info("=" * 55)
    log.info("PRE-FLIGHT CHECK — %s video", video_type.upper())
    log.info("=" * 55)

    checks = [
        _check_disk_space(video_type),
        _check_memory(),
        _check_oauth_token(),
        _check_ffmpeg(),
        _check_python_packages(),
        _check_network(),
        _check_retry_queue(),
    ]

    for c in checks:
        status = "OK  " if c.ok else "FAIL"
        log.info("  [%s] %-20s %s", status, c.name, c.message)
        if c.auto_fixed:
            log.info("         AUTO-FIXED: %s", c.fix_message)

    failed  = [c for c in checks if not c.ok]
    fixed   = [c for c in checks if c.auto_fixed]
    critical_names = {"disk_space", "oauth_token", "ffmpeg", "network"}
    critical_fails = [c for c in failed if c.name in critical_names]

    if not failed:
        log.info("Pre-flight PASSED%s",
                 f" (auto-fixed {len(fixed)} issue(s))" if fixed else "")
        log.info("=" * 55)
        return True, checks

    log.warning("Pre-flight: %d check(s) failed, %d auto-fixed", len(failed), len(fixed))
    log.info("=" * 55)

    # Send email report
    _send_preflight_email(video_type, checks, failed, fixed)

    if critical_fails:
        log.error("CRITICAL failures — pipeline BLOCKED: %s",
                  ", ".join(c.name for c in critical_fails))
        return False, checks

    log.warning("Non-critical failures — pipeline will proceed with caution")
    return True, checks


# ---------------------------------------------------------------------------
# Email reports
# ---------------------------------------------------------------------------

def _send_preflight_email(video_type: str, checks: list, failed: list, fixed: list):
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    critical_names = {"disk_space", "oauth_token", "ffmpeg", "network"}
    is_critical = any(c.name in critical_names for c in failed)
    emoji = "🚨" if is_critical else "⚠️"
    level = "CRITICAL — Pipeline Blocked" if is_critical else "Warning"
    subject = f"{emoji} GetMindFuelNow — {level}: {video_type} pre-flight ({now})"

    rows = ""
    for c in checks:
        icon = "✅" if c.ok else "❌"
        bg = "#d4edda" if c.ok else "#f8d7da"
        fix_cell = ("✔ " + c.fix_message) if c.auto_fixed else ("—" if c.ok else "⚠ Manual action needed")
        rows += (
            f'<tr style="background:{bg}">'
            f'<td style="padding:8px;border:1px solid #ccc">{icon} {c.name.replace("_"," ").title()}</td>'
            f'<td style="padding:8px;border:1px solid #ccc">{c.message}</td>'
            f'<td style="padding:8px;border:1px solid #ccc">{fix_cell}</td>'
            f'</tr>'
        )

    action_items = "".join(
        f'<li><strong>{c.name.replace("_"," ").title()}:</strong> {c.message}</li>'
        for c in failed if not c.auto_fixed or not c.ok
    )

    banner_color = "#8b0000" if is_critical else "#856404"
    banner_bg    = "#f8d7da" if is_critical else "#fff3cd"
    banner_msg   = "🚨 Pipeline is BLOCKED until critical issues are resolved." if is_critical \
                   else "⚠️ Pipeline will attempt to run but some features may degrade."

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
<div style="background:#1a0a3a;color:white;padding:20px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">{emoji} GetMindFuelNow — Pre-flight {level}</h2>
  <p style="margin:5px 0;opacity:.8">{now} · Video type: <strong>{video_type}</strong></p>
</div>
<div style="padding:20px;border:1px solid #ddd">
  <div style="background:{banner_bg};color:{banner_color};border:1px solid {banner_color};padding:12px;border-radius:4px;margin-bottom:16px">
    {banner_msg}
  </div>
  <h3 style="margin-top:0">Health Check Results</h3>
  <table style="width:100%;border-collapse:collapse">
    <tr style="background:#343a40;color:white">
      <th style="padding:8px;text-align:left">Check</th>
      <th style="padding:8px;text-align:left">Status</th>
      <th style="padding:8px;text-align:left">Auto-Fix Applied</th>
    </tr>{rows}
  </table>
  {'<h3>Action Required</h3><ul>' + action_items + '</ul>' if action_items else ''}
  <h3>Quick Commands (SSH to VM)</h3>
  <pre style="background:#2d2d2d;color:#f8f8f2;padding:12px;border-radius:4px;font-size:12px">cd ~/energy-intelligence/youtube_automation

# Check daemon status
sudo systemctl status getmindfuelnow

# View live logs
sudo journalctl -u getmindfuelnow -f

# Manual run
python core/horoscope_pipeline.py --now --type {video_type}

# Re-authorize YouTube (if OAuth issue)
rm youtube_token.json
python first_time_setup.py --no-upload

# Free disk space manually
df -h /
ls -lhS output/ | head -20</pre>
</div>
<div style="background:#1a0a3a;color:white;padding:10px 20px;border-radius:0 0 8px 8px;font-size:12px">
  GetMindFuelNow · Auto-generated from Google Cloud VM
</div>
</body></html>"""
    _send_email(subject, html)


def send_pipeline_failure_email(video_type: str, stage: str,
                                error: str, date: datetime.date) -> None:
    """Email alert when a pipeline stage fails."""
    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    subject = f"🚨 GetMindFuelNow — Pipeline FAILED [{stage.upper()}]: {video_type} {date}"

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
<div style="background:#8b0000;color:white;padding:20px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">🚨 Pipeline Failed — Action Needed</h2>
  <p style="margin:5px 0;opacity:.8">{now}</p>
</div>
<div style="padding:20px;border:1px solid #ddd">
  <table style="width:100%;border-collapse:collapse;margin-bottom:16px">
    <tr><td style="padding:8px;font-weight:bold;width:140px">Video Type</td><td style="padding:8px">{video_type}</td></tr>
    <tr style="background:#f8f9fa"><td style="padding:8px;font-weight:bold">Date</td><td style="padding:8px">{date}</td></tr>
    <tr><td style="padding:8px;font-weight:bold">Failed Stage</td><td style="padding:8px"><span style="color:red;font-weight:bold">[{stage.upper()}]</span></td></tr>
    <tr style="background:#f8f9fa"><td style="padding:8px;font-weight:bold">Error</td>
      <td style="padding:8px"><code style="background:#f1f1f1;padding:4px;display:block;white-space:pre-wrap">{error[:800]}</code></td></tr>
  </table>
  <h3>Recovery</h3>
  <pre style="background:#2d2d2d;color:#f8f8f2;padding:12px;border-radius:4px;font-size:12px">cd ~/energy-intelligence/youtube_automation

# Retry manually:
python core/horoscope_pipeline.py --now --type {video_type} --date {date}

# Upload an already-generated video:
python -c "
from youtube_uploader import upload_video
import glob, json
mp4s = sorted(glob.glob('output/horoscope_{date}*/*.mp4'))
print(mp4s)
"</pre>
</div>
<div style="background:#1a0a3a;color:white;padding:10px 20px;border-radius:0 0 8px 8px;font-size:12px">
  GetMindFuelNow · Auto-generated alert
</div>
</body></html>"""
    _send_email(subject, html)


def send_success_summary_email(video_type: str, date: datetime.date,
                               youtube_url: str, duration_min: float) -> None:
    """Weekly success summary email (only sent if SEND_SUCCESS_EMAIL=true in .env)."""
    if os.getenv("SEND_SUCCESS_EMAIL", "false").lower() != "true":
        return

    now = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    subject = f"✅ GetMindFuelNow — {video_type.title()} video live: {date}"

    html = f"""<html><body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto">
<div style="background:#0d5e2e;color:white;padding:20px;border-radius:8px 8px 0 0">
  <h2 style="margin:0">✅ Video Published Successfully</h2>
  <p style="margin:5px 0;opacity:.8">{now}</p>
</div>
<div style="padding:20px;border:1px solid #ddd">
  <table style="width:100%">
    <tr><td style="padding:8px;font-weight:bold">Type</td><td>{video_type}</td></tr>
    <tr style="background:#f8f9fa"><td style="padding:8px;font-weight:bold">Date</td><td>{date}</td></tr>
    <tr><td style="padding:8px;font-weight:bold">Duration</td><td>{duration_min:.1f} min to generate</td></tr>
    <tr style="background:#f8f9fa"><td style="padding:8px;font-weight:bold">YouTube</td>
      <td><a href="{youtube_url}">{youtube_url}</a></td></tr>
  </table>
</div>
</body></html>"""
    _send_email(subject, html)
