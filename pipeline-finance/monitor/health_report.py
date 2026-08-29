"""
monitor/health_report.py — DriftWire326 one-command diagnosis
`python main.py --health` answers the only question that matters when the
channel goes quiet: WHY is nothing being published?

Checks, in the order failures actually happen:
  1. Anthropic credit / API reachable   (a dead key stops every run)
  2. Disk space                          (a full disk fails every render)
  3. The scheduler daemon                (is it even running?)
  4. Today's + recent pipeline states    (where did runs stop?)
  5. Upload manifest                     (what actually published, and when?)
  6. YouTube quota                       (exhausted?)
  7. Alerting itself                     (is SMTP configured? if not, silence
                                          means nothing — the #1 blind spot)
Prints a verdict and the single most likely cause.
"""
import json
import logging
import os
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from config.settings import settings

logger = logging.getLogger(__name__)

_OK, _WARN, _FAIL = "✅", "⚠️ ", "❌"


def _line(status: str, label: str, detail: str = "") -> str:
    return f"{status} {label}" + (f" — {detail}" if detail else "")


def _check_anthropic() -> tuple[str, str, str]:
    """Returns (status, label, detail). A dead key halts every pipeline run."""
    if not settings.anthropic_api_key:
        return _FAIL, "Anthropic API key", "not set in .env"
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
        # A 1-token completion is the only way to prove credit actually remains;
        # models.list() succeeds even on an exhausted account.
        client.messages.create(
            model=settings.claude_model,
            max_tokens=1,
            messages=[{"role": "user", "content": "hi"}],
        )
        return _OK, "Anthropic API", "key valid, credit available"
    except Exception as exc:
        text = str(exc)
        if "credit" in text.lower() or "balance" in text.lower() or "402" in text:
            return _FAIL, "Anthropic API", "OUT OF CREDIT — add funds at console.anthropic.com"
        if "authentication" in text.lower() or "401" in text:
            return _FAIL, "Anthropic API", "key rejected (invalid or revoked)"
        return _FAIL, "Anthropic API", text[:120]


def _check_disk() -> tuple[str, str, str]:
    try:
        usage = shutil.disk_usage(str(settings.root_dir))
        free_gb = usage.free / 1e9
        pct_used = usage.used / usage.total * 100
        detail = f"{free_gb:.1f} GB free ({pct_used:.0f}% used)"
        if free_gb < 1.0:
            return _FAIL, "Disk space", detail + " — renders WILL fail"
        if free_gb < 3.0:
            return _WARN, "Disk space", detail + " — clean output/ soon"
        return _OK, "Disk space", detail
    except Exception as exc:
        return _WARN, "Disk space", str(exc)[:80]


def _check_daemon() -> tuple[str, str, str]:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", "driftwire326"],
            capture_output=True, text=True, timeout=15,
        )
        state = result.stdout.strip() or result.stderr.strip() or "not installed"
        if state == "active":
            return _OK, "Scheduler daemon", "active (running)"
        return _FAIL, "Scheduler daemon", f"{state} — run: sudo systemctl start driftwire326"
    except FileNotFoundError:
        return _WARN, "Scheduler daemon", "systemctl unavailable (not a systemd host)"
    except Exception as exc:
        return _WARN, "Scheduler daemon", str(exc)[:80]


def _check_heartbeat() -> tuple[str, str, str]:
    """The daemon writes a heartbeat every 30 min — proves it is alive AND working."""
    hb = settings.logs_dir / "heartbeat.log"
    if not hb.exists():
        return _FAIL, "Daemon heartbeat", "never written — daemon has not run its jobs"
    try:
        age_min = (datetime.now().timestamp() - hb.stat().st_mtime) / 60
        if age_min > 90:
            return _FAIL, "Daemon heartbeat", f"stale — last beat {age_min/60:.1f} h ago"
        return _OK, "Daemon heartbeat", f"last beat {age_min:.0f} min ago"
    except Exception as exc:
        return _WARN, "Daemon heartbeat", str(exc)[:80]


def _check_startup_error() -> tuple[str, str, str, list[str]]:
    """
    A scheduler startup crash writes its traceback here. Surfacing it turns a
    silent systemd restart loop into a named cause.
    """
    path = settings.logs_dir / "STARTUP_ERROR.txt"
    if not path.exists():
        return _OK, "Scheduler startup", "no recorded startup crash", []
    try:
        text = path.read_text(encoding="utf-8").strip()
        stamp = text.splitlines()[0] if text else "?"
        # The final traceback line is the exception itself — the useful part.
        last = [l for l in text.splitlines() if l.strip()][-1]
        age_note = ""
        try:
            age_h = (datetime.now() - datetime.fromisoformat(stamp)).total_seconds() / 3600
            age_note = f" ({age_h:.1f} h ago)"
            if age_h > 24:
                # Stale file from an already-fixed crash — inform, don't alarm.
                return (_WARN, "Scheduler startup",
                        f"old crash recorded{age_note} — delete logs/STARTUP_ERROR.txt "
                        f"once resolved", [f"   {last[:150]}"])
        except Exception:
            pass
        return (_FAIL, "Scheduler startup", f"crashed at boot{age_note}",
                [f"   {last[:150]}", f"   full traceback: {path}"])
    except Exception as exc:
        return _WARN, "Scheduler startup", str(exc)[:80], []


def _check_pipeline_states(days: int = 5) -> tuple[str, str, str, list[str]]:
    """Read the last N days of checkpoint files: where did runs stop?"""
    from scheduler.pipeline_state import STATE_DIR
    detail_lines: list[str] = []
    unfinished: list[str] = []
    if not STATE_DIR.exists():
        return _FAIL, "Pipeline runs", "no state dir — no pipeline has ever started", []

    found_any = False
    for offset in range(days):
        day = (date.today() - timedelta(days=offset)).isoformat()
        # Glob rather than build the filename: the weekday pipeline writes one
        # checkpoint per slot now (weekday_premarket_<day>.json). Constructing
        # "weekday_<day>.json" would find nothing and report "no runs" — the
        # exact false all-clear this report exists to prevent.
        for path in sorted(STATE_DIR.glob(f"*_{day}.json")):
            pipeline = path.stem[:-len(day) - 1]
            found_any = True
            try:
                data = json.loads(path.read_text())
                outcome = data.get("outcome") or "incomplete"
                steps = [s for s, e in data.get("steps", {}).items() if e.get("done")]
                errors = data.get("errors", [])
                mark = "✅" if outcome == "success" else "❌"
                line = f"   {mark} {day} {pipeline}: {outcome}, {len(steps)} steps done"
                if errors:
                    line += f"\n      last error: {errors[-1][:150]}"
                detail_lines.append(line)
                # A run that failed, or one left incomplete on a day that is
                # over, is a run that did not publish. Today's incomplete run
                # may simply still be going.
                if outcome == "failed" or (outcome == "incomplete" and offset > 0):
                    unfinished.append(f"{day} {pipeline}")
            except Exception as exc:
                detail_lines.append(f"   ⚠️  {day} {pipeline}: unreadable ({exc})")
                unfinished.append(f"{day} {pipeline} (unreadable)")

    if not found_any:
        # A daemon that only just started has had no chance to fire yet —
        # that is "waiting", not "broken".
        if _daemon_uptime_hours() is not None and _daemon_uptime_hours() < 12:
            return (_WARN, "Pipeline runs",
                    "none yet — daemon started recently, waiting for the next slot", [])
        return (_FAIL, "Pipeline runs",
                f"NO runs attempted in {days} days — the scheduler never fired", [])
    if unfinished:
        # Never report OK over a failed run. Two weekday slots means one can
        # publish while the other does not, and a green headline above a red
        # line is how a partial outage goes unnoticed for weeks.
        return (_WARN, "Pipeline runs",
                f"{len(detail_lines)} run(s) recorded, "
                f"{len(unfinished)} did not publish: {', '.join(unfinished[:3])}",
                detail_lines)
    return _OK, "Pipeline runs", f"{len(detail_lines)} run(s) recorded", detail_lines


def _daemon_uptime_hours() -> "float | None":
    """
    Hours since the CURRENT scheduler process started.

    heartbeat.log is append-only across restarts, so the newest line is only
    ever minutes old. Taking that as "uptime" made the freshly-restarted
    grace period permanent, which would suppress the very outage warning this
    report exists to raise. Each beat records its PID, so the run of trailing
    lines sharing the newest PID belongs to the live process — its first beat
    is the real start time.
    """
    hb = settings.logs_dir / "heartbeat.log"
    if not hb.exists():
        return None
    try:
        beats: list[tuple[datetime, str]] = []
        for line in hb.read_text(encoding="utf-8").splitlines():
            if "] " not in line or "PID=" not in line:
                continue
            stamp = line.split("] ")[1].split(" |")[0].strip()
            pid = line.split("PID=")[1].split(" |")[0].strip()
            beats.append((datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S"), pid))
        if not beats:
            return None

        current_pid = beats[-1][1]
        started = beats[-1][0]
        for stamp, pid in reversed(beats):
            if pid != current_pid:
                break
            started = stamp
        return max((datetime.now() - started).total_seconds() / 3600, 0.0)
    except Exception:
        return None


def _next_content_slots() -> list[str]:
    """Human-readable 'when is the next video due' lines."""
    try:
        from scheduler.master_scheduler import next_content_runs
        return [
            f"   {label}: {fire.strftime('%a %b %d, %-I:%M %p %Z')}"
            for label, fire in next_content_runs(limit=3)
        ]
    except Exception:
        return []


def _check_uploads() -> tuple[str, str, str, list[str]]:
    try:
        from uploader.uploader import load_upload_manifest
        records = load_upload_manifest()
    except Exception as exc:
        return _WARN, "Uploads", f"manifest unreadable: {exc}", []

    if not records:
        return _FAIL, "Uploads", "manifest empty — nothing has ever published", []

    newest = records[0].get("uploaded_at", "")
    try:
        age_days = (datetime.now() - datetime.fromisoformat(newest)).days
    except Exception:
        age_days = -1

    lines = [
        f"   {r.get('uploaded_at', '?')[:16]}  {r.get('video_type', '?'):8}  "
        f"{str(r.get('title', ''))[:52]}"
        for r in records[:5]
    ]
    if age_days > 1:
        uptime = _daemon_uptime_hours()
        if uptime is not None and uptime < 12:
            return (_WARN, "Uploads",
                    f"last upload {age_days} days ago — daemon restarted "
                    f"{uptime:.1f} h ago, nothing due yet", lines)
        return (_FAIL, "Uploads",
                f"last upload was {age_days} days ago ({newest[:16]})", lines)
    return _OK, "Uploads", f"{len(records)} total, newest {newest[:16]}", lines


def _check_quota() -> tuple[str, str, str]:
    try:
        from uploader.quota_tracker import QuotaTracker
        qt = QuotaTracker()
        remaining = qt.get_remaining()
        if not qt.can_upload():
            return _FAIL, "YouTube quota", f"{remaining} units left — below upload threshold"
        return _OK, "YouTube quota", f"{remaining:,} units remaining today"
    except Exception as exc:
        return _WARN, "YouTube quota", str(exc)[:80]


def _check_alerting() -> tuple[str, str, str]:
    """
    THE blind spot: if SMTP is unset, the dead-man switch fires into a log
    and you learn about outages days later.
    """
    required = ("SMTP_HOST", "SMTP_USER", "SMTP_PASS")
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        return (_FAIL, "Alerting (dead-man email)",
                f"NOT configured (missing {', '.join(missing)}) — outages stay silent")
    if not os.environ.get("ALERT_EMAIL", os.environ.get("SMTP_USER", "")):
        return _WARN, "Alerting (dead-man email)", "ALERT_EMAIL unset"
    return _OK, "Alerting (dead-man email)", f"to {os.environ.get('ALERT_EMAIL')}"


def _check_tokens() -> tuple[str, str, str]:
    cfg = settings.root_dir / "config"
    missing = [
        name for name in ("finance_oauth.json", "youtube_token.json", "analytics_token.json")
        if not (cfg / name).exists()
    ]
    if missing:
        return _FAIL, "YouTube credentials", f"missing {', '.join(missing)}"
    return _OK, "YouTube credentials", "all 3 files present"


def run_health_report() -> int:
    """Print the full report. Returns 0 if healthy, 1 if any FAIL."""
    print()
    print("═" * 66)
    print(f"  DriftWire326 HEALTH REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("═" * 66)
    print()

    failures: list[str] = []
    simple_checks = [
        _check_anthropic(), _check_disk(), _check_daemon(),
        _check_heartbeat(), _check_quota(), _check_tokens(), _check_alerting(),
    ]
    for status, label, detail in simple_checks:
        print(_line(status, label, detail))
        if status == _FAIL:
            failures.append(label)

    print()
    for status, label, detail, lines in (
        _check_startup_error(), _check_pipeline_states(), _check_uploads()
    ):
        print(_line(status, label, detail))
        for line in lines:
            print(line)
        if status == _FAIL:
            failures.append(label)
        print()

    upcoming = _next_content_slots()
    if upcoming:
        print("Next scheduled content:")
        for line in upcoming:
            print(line)
        print()

    print("═" * 66)
    if not failures:
        print("  VERDICT: healthy — publishing normally.")
    else:
        print(f"  VERDICT: {len(failures)} problem(s): {', '.join(failures)}")
        print()
        # Order matters — name the upstream cause, not the symptom
        if "Scheduler startup" in failures:
            print("  ROOT CAUSE: the scheduler crashed during startup — see the")
            print("  traceback above and in logs/STARTUP_ERROR.txt. systemd keeps")
            print("  restarting it, which shows as 'activating', not 'failed'.")
        elif "Anthropic API" in failures or "Anthropic API key" in failures:
            print("  ROOT CAUSE: no Claude access — every run aborts at the API")
            print("  gate before spending anything. Top up credit, then:")
            print("    sudo systemctl restart driftwire326")
        elif "Disk space" in failures:
            print("  ROOT CAUSE: disk full — renders cannot write. Free space:")
            print("    sudo find /opt/driftwire326/pipeline-finance/output -type f "
                  "-mtime +3 -delete")
        elif "Scheduler daemon" in failures or "Daemon heartbeat" in failures:
            print("  ROOT CAUSE: the scheduler is not running its jobs. Start it and")
            print("  read why it stopped:")
            print("    sudo systemctl start driftwire326")
            print("    sudo journalctl -u driftwire326 -n 60 --no-pager")
        elif "Pipeline runs" in failures:
            print("  ROOT CAUSE: the daemon is up but no pipeline fired — check the")
            print("  job schedule and the service log:")
            print("    sudo journalctl -u driftwire326 -n 60 --no-pager")
        elif "YouTube quota" in failures:
            print("  ROOT CAUSE: today's API quota is spent; it resets at midnight PT.")
        elif "YouTube credentials" in failures:
            print("  ROOT CAUSE: OAuth files missing — re-run deploy/oauth_bootstrap.py")
        if "Alerting (dead-man email)" in failures:
            print()
            print("  ALSO: alerting is off, which is why nobody told you. Add SMTP_HOST,")
            print("  SMTP_USER, SMTP_PASS (Gmail app password) and ALERT_EMAIL to .env.")
    print("═" * 66)
    print()
    return 1 if failures else 0
