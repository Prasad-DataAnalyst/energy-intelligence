#!/bin/bash
# setup_automation.sh
# Run this ONCE on your VM to schedule daily video generation.
# Usage: bash setup_automation.sh

set -e

USER_HOME="$HOME"
# Resolve the repo dir from this script's location so it works for any user.
REPO="$(cd "$(dirname "$0")" && pwd)"
# Prefer the project venv if present; else fall back to system python3.
if [ -x "$REPO/.venv/bin/python" ]; then
    VENV_PYTHON="$REPO/.venv/bin/python"
else
    VENV_PYTHON="$(command -v python3)"
fi
LOG="$USER_HOME/daily_horoscope.log"

echo ""
echo "============================================"
echo "  GetMindFuelNow — Automation Setup"
echo "============================================"

# ── 1. Verify paths ───────────────────────────────────────────────────────────
echo ""
echo "[1/4] Checking paths..."

if [ ! -f "$REPO/run_daily.py" ]; then
    echo "  ERROR: $REPO/run_daily.py not found"
    echo "  Make sure you ran: git pull origin claude/youtube-daily-animation-o9gyu"
    exit 1
fi

if [ ! -f "$VENV_PYTHON" ]; then
    echo "  ERROR: python3 not found at $VENV_PYTHON"
    exit 1
fi

echo "  OK: run_daily.py found"
echo "  OK: python3 found at $VENV_PYTHON"

# ── 2. Allow passwordless sudo for systemctl stop/start daemon ────────────────
echo ""
echo "[2/4] Configuring passwordless sudo for daemon control..."

SUDOERS_LINE="prasad2t ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop getmindfuelnow, /usr/bin/systemctl start getmindfuelnow, /bin/systemctl stop getmindfuelnow, /bin/systemctl start getmindfuelnow"
SUDOERS_FILE="/etc/sudoers.d/horoscope-daemon"

# Always rewrite so path fixes (/usr/bin vs /bin systemctl) are picked up on re-run.
echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
sudo chmod 440 "$SUDOERS_FILE"
sudo visudo -c -f "$SUDOERS_FILE" && echo "  OK: sudoers configured" || {
    echo "  ERROR: sudoers syntax error — removing"
    sudo rm -f "$SUDOERS_FILE"
    exit 1
}

# ── 3. Install cron job ───────────────────────────────────────────────────────
echo ""
echo "[3/4] Installing cron job (runs daily at 5:30 AM UTC — all 12 signs, auto-upload)..."

# Preflight doctor at 5:00 AM — emails you 30 min early if anything is broken
# (bad key, expired token, low disk) so you can fix it before the 5:30 run.
DOCTOR_CMD="0 5 * * * cd $REPO && $VENV_PYTHON doctor.py --email >> $LOG 2>&1"
# Main pipeline at 5:30 AM.
CRON_CMD="30 5 * * * cd $REPO && $VENV_PYTHON run_daily.py --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"

# Remove any old version of these cron jobs first (both the current runner and
# the legacy daily_runner.py 3 PM job installed by first_time_setup.py).
( crontab -l 2>/dev/null | grep -v -e "run_daily.py" -e "daily_runner.py" -e "doctor.py" ) | crontab - 2>/dev/null || true

# Add doctor (5:00) + main pipeline (5:30)
( crontab -l 2>/dev/null; echo "$DOCTOR_CMD"; echo "$CRON_CMD" ) | crontab -

echo "  OK: cron jobs installed (doctor 5:00, pipeline 5:30)"

# ── 3b. Log rotation so $LOG doesn't grow without bound ───────────────────────
echo ""
echo "[3b] Configuring log rotation..."
LOGROTATE_FILE="/etc/logrotate.d/getmindfuelnow"
if command -v logrotate >/dev/null 2>&1; then
    sudo tee "$LOGROTATE_FILE" > /dev/null <<ROT
$LOG {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
ROT
    echo "  OK: logrotate config → $LOGROTATE_FILE (weekly, keep 8)"
else
    echo "  SKIP: logrotate not installed"
fi

# ── 4. Show confirmation ──────────────────────────────────────────────────────
echo ""
echo "[4/4] Verification..."
echo ""
crontab -l | grep "run_daily" || echo "  WARNING: cron job not found!"

echo ""
echo "============================================"
echo "  SETUP COMPLETE"
echo "============================================"
echo ""
echo "  Schedule : Every day at 5:30 AM (VM time)"
echo "  Mode     : All 12 signs in one video (148s)"
echo "  Log file : $LOG"
echo ""
echo "  To check what time it is on VM:"
echo "    timedatectl"
echo ""
echo "  To watch the log live tomorrow:"
echo "    tail -f $LOG"
echo ""
echo "  To run manually RIGHT NOW:"
echo "    cd $REPO"
echo "    $VENV_PYTHON run_daily.py --date \$(date +%Y%m%d) --period \"\$(date +'%B %Y')\" --upload"
echo ""
echo "  To skip asset generation (reuse today's JSON):"
echo "    $VENV_PYTHON run_daily.py --date \$(date +%Y%m%d) --period \"\$(date +'%B %Y')\" --skip-assets --upload"
echo ""
