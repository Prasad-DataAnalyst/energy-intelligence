#!/usr/bin/env python3
"""
doctor.py — Preflight health check for the daily horoscope pipeline.

Catches the problems that historically broke the 5:30 AM cron *before* it wastes
a run: missing/invalid API keys, an expired YouTube token, missing ffmpeg, an
unreachable edge-tts, low disk, missing fonts.

Run standalone any time:
    python3 doctor.py            # human-readable report, exit 0 = healthy
    python3 doctor.py --email    # also email the report if anything is wrong
    python3 doctor.py --deep     # also make a tiny live Anthropic API call

Or imported by run_daily.py, which calls preflight() before rendering.

Each check returns (ok, level, message):
  level "critical" → pipeline cannot succeed; abort the run.
  level "warn"     → run can proceed but attention is needed soon.
"""
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).parent
load_dotenv(HERE / ".env")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Individual checks ────────────────────────────────────────────────────────

def _check_binaries() -> tuple:
    missing = [b for b in ("ffmpeg", "ffprobe") if not shutil.which(b)]
    if missing:
        return False, "critical", f"Missing binaries: {', '.join(missing)}"
    return True, "ok", "ffmpeg + ffprobe present"


def _check_python_deps() -> tuple:
    import importlib
    deps = ["anthropic", "edge_tts", "PIL", "numpy",
            "google.oauth2", "googleapiclient", "dotenv"]
    missing = []
    for d in deps:
        try:
            importlib.import_module(d)
        except Exception:
            missing.append(d)
    if missing:
        return False, "critical", f"Missing Python packages: {', '.join(missing)}"
    return True, "ok", f"All {len(deps)} Python deps import"


def _check_anthropic_key(deep: bool = False) -> tuple:
    key = os.getenv("ANTHROPIC_API_KEY", "")
    if not key:
        return False, "critical", "ANTHROPIC_API_KEY missing from .env"
    if not key.startswith("sk-ant-"):
        return False, "critical", "ANTHROPIC_API_KEY has wrong format (expect sk-ant-...)"
    if not deep:
        return True, "ok", "ANTHROPIC_API_KEY present (format OK)"
    try:
        import anthropic
        c = anthropic.Anthropic()
        c.messages.create(model="claude-sonnet-4-6", max_tokens=5,
                          messages=[{"role": "user", "content": "ok"}])
        return True, "ok", "ANTHROPIC_API_KEY verified with a live call"
    except Exception as e:
        return False, "critical", f"Anthropic live call failed: {str(e)[:120]}"


def _check_youtube_token() -> tuple:
    """Verify the OAuth token loads, has all required scopes, and either is
    valid or can be refreshed. Warn if it expires within 24h."""
    try:
        import config
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
    except Exception as e:
        return False, "critical", f"Cannot import google libs/config: {str(e)[:80]}"

    token_path = HERE / config.TOKEN_FILE
    if not token_path.exists():
        return False, "critical", f"No YouTube token ({config.TOKEN_FILE}). Run reauth_youtube.py"

    # Scope check — MUST read the raw file: passing scopes= to
    # from_authorized_user_file OVERRIDES the file's scopes field, so checking
    # creds.scopes afterwards always "passes" (and each refresh re-serializes
    # the overridden list, laundering the file). force-ssl is required for
    # comments.
    try:
        import json as _json
        raw_scopes = set(_json.loads(token_path.read_text()).get("scopes") or [])
    except Exception:
        raw_scopes = set()
    required = set(config.YOUTUBE_SCOPES)
    if raw_scopes and not required.issubset(raw_scopes):
        miss = {s.rsplit("/", 1)[-1] for s in required - raw_scopes}
        return False, "warn", (f"Token missing scopes {miss} — comments will "
                               f"403. Re-run reauth_youtube.py")

    try:
        creds = Credentials.from_authorized_user_file(str(token_path), config.YOUTUBE_SCOPES)
    except Exception as e:
        return False, "critical", f"Token unreadable: {str(e)[:80]}. Run reauth_youtube.py"

    if creds.valid:
        exp = getattr(creds, "expiry", None)
        if exp and (exp - _utcnow()) < timedelta(hours=24):
            return True, "warn", f"Token valid but expires soon ({exp} UTC) — will auto-refresh"
        return True, "ok", "YouTube token valid"

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            try:
                token_path.write_text(creds.to_json())
            except Exception:
                pass
            return True, "ok", "YouTube token refreshed successfully"
        except Exception as e:
            return False, "critical", (f"Token refresh failed: {str(e)[:100]}. "
                                       f"Run reauth_youtube.py")
    return False, "critical", "Token invalid and not refreshable. Run reauth_youtube.py"


def _check_disk() -> tuple:
    free_gb = shutil.disk_usage(HERE).free / (1024 ** 3)
    if free_gb < 1.0:
        return False, "critical", f"Only {free_gb:.1f} GB free — renders may fail"
    if free_gb < 3.0:
        return True, "warn", f"Low disk: {free_gb:.1f} GB free"
    return True, "ok", f"Disk OK ({free_gb:.1f} GB free)"


def _check_fonts() -> tuple:
    try:
        import make_daily_video as m
        f = m._glyph_font(60)
        from PIL import Image, ImageDraw
        d = ImageDraw.Draw(Image.new("RGB", (120, 120)))
        if d.textbbox((0, 0), "♈", font=f)[2] > 2:   # ♈
            return True, "ok", "Glyph font renders zodiac symbols"
        return True, "warn", "Zodiac glyphs may render as boxes"
    except Exception as e:
        return True, "warn", f"Font check skipped: {str(e)[:80]}"


def _check_edge_tts() -> tuple:
    """Confirm edge-tts can actually reach the service (network dependency).
    Hard 30s cap — a network black hole must not stall the 5:00/5:30 runs —
    and the temp dir is cleaned up (mkdtemp leaked one per invocation)."""
    try:
        import asyncio, tempfile, edge_tts
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "t.mp3"

            async def _go():
                await asyncio.wait_for(
                    edge_tts.Communicate("test", voice="en-IE-EmilyNeural").save(str(out)),
                    timeout=30,
                )

            asyncio.run(_go())
            if out.exists() and out.stat().st_size > 256:
                return True, "ok", "edge-tts reachable"
        return True, "warn", "edge-tts produced no audio (video will use ambient only)"
    except Exception as e:
        return True, "warn", f"edge-tts unreachable ({str(e)[:60]}) — ambient-only fallback"


def _check_alerting() -> tuple:
    """If email isn't configured, every failure alert (doctor, summary,
    heartbeat) is a silent no-op — the exact blind spot this system exists to
    remove. Not critical (the video still renders) but must be visible."""
    if os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", ""):
        hc = "on" if os.getenv("HEALTHCHECK_URL", "").strip() else "off (optional)"
        return True, "ok", f"Email alerts configured; external healthcheck {hc}"
    return True, "warn", ("GMAIL_APP_PASSWORD not set — failure emails and the "
                          "dead-man's-switch alert CANNOT send")


# ── Orchestration ────────────────────────────────────────────────────────────

def preflight(deep: bool = False) -> tuple:
    """Run all checks. Returns (all_critical_ok, report_lines)."""
    checks = [
        ("Binaries",      _check_binaries()),
        ("Python deps",   _check_python_deps()),
        ("Anthropic key", _check_anthropic_key(deep)),
        ("YouTube token", _check_youtube_token()),
        ("Disk space",    _check_disk()),
        ("Fonts",         _check_fonts()),
        ("edge-tts",      _check_edge_tts()),
        ("Alerting",      _check_alerting()),
    ]
    lines, critical_ok = [], True
    for name, (ok, level, msg) in checks:
        icon = "✅" if ok and level == "ok" else ("⚠️ " if level == "warn" else "❌")
        lines.append(f"  {icon}  {name:<14} {msg}")
        if level == "critical" and not ok:
            critical_ok = False
    return critical_ok, lines


def _email_report(healthy: bool, lines: list) -> None:
    import smtplib
    from email.mime.text import MIMEText
    pw = os.getenv("GMAIL_APP_PASSWORD", "").replace(" ", "")
    who = os.getenv("GMAIL_ADDRESS", "prasad2t@gmail.com")
    if not pw:
        return
    status = "HEALTHY" if healthy else "PROBLEM"
    body = f"GetMindFuelNow preflight — {status}\n{_utcnow():%Y-%m-%d %H:%M UTC}\n\n" + "\n".join(lines)
    msg = MIMEText(body)
    msg["Subject"] = f"GetMindFuelNow doctor — {status}"
    msg["From"] = who
    msg["To"] = who
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(who, pw)
            s.sendmail(who, who, msg.as_string())
    except Exception as e:
        print(f"  [INFO] Email skipped: {e}")


def main():
    deep  = "--deep" in sys.argv
    email = "--email" in sys.argv
    healthy, lines = preflight(deep=deep)
    print("\n" + "=" * 58)
    print("  PREFLIGHT HEALTH CHECK")
    print("=" * 58)
    print("\n".join(lines))
    print("=" * 58)
    print(f"  {'✅ HEALTHY — safe to run' if healthy else '❌ CRITICAL ISSUES — fix before running'}")
    print("=" * 58 + "\n")
    if email and not healthy:
        _email_report(healthy, lines)
    sys.exit(0 if healthy else 1)


if __name__ == "__main__":
    main()
