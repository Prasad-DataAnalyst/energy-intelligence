#!/bin/bash
# setup_automation.sh
# Run this ONCE on your VM to schedule daily video generation.
# Usage: bash setup_automation.sh

set -e

USER_HOME="/home/prasad2t"
REPO="$USER_HOME/energy-intelligence/youtube_automation"
VENV_PYTHON="$USER_HOME/energy-intelligence/venv/bin/python3"
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
    echo "  ERROR: venv not found at $VENV_PYTHON"
    echo "  Try: python3 -m venv $USER_HOME/energy-intelligence/venv"
    exit 1
fi

echo "  OK: run_daily.py found"
echo "  OK: python venv found"

# ── 2. Allow passwordless sudo for systemctl stop/start daemon ────────────────
echo ""
echo "[2/4] Configuring passwordless sudo for daemon control..."

SUDOERS_LINE="prasad2t ALL=(ALL) NOPASSWD: /bin/systemctl stop getmindfuelnow, /bin/systemctl start getmindfuelnow"
SUDOERS_FILE="/etc/sudoers.d/horoscope-daemon"

if sudo grep -q "getmindfuelnow" /etc/sudoers 2>/dev/null || sudo test -f "$SUDOERS_FILE" 2>/dev/null; then
    echo "  OK: sudoers already configured"
else
    echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null
    sudo chmod 440 "$SUDOERS_FILE"
    sudo visudo -c -f "$SUDOERS_FILE" && echo "  OK: sudoers configured" || {
        echo "  ERROR: sudoers syntax error — removing"
        sudo rm -f "$SUDOERS_FILE"
        exit 1
    }
fi

# ── 3. Install cron job ───────────────────────────────────────────────────────
echo ""
echo "[3/4] Installing cron job (runs daily at 5:30 AM)..."

CRON_CMD="30 5 * * * cd $REPO && $VENV_PYTHON run_daily.py --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --format short >> $LOG 2>&1"

# Remove any old version of this cron job first
( crontab -l 2>/dev/null | grep -v "run_daily.py" ) | crontab - 2>/dev/null || true

# Add the new one
( crontab -l 2>/dev/null; echo "$CRON_CMD" ) | crontab -

echo "  OK: cron job installed"

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
echo "  Log file : $LOG"
echo ""
echo "  To check what time it is on VM:"
echo "    timedatectl"
echo ""
echo "  To watch the log live tomorrow:"
echo "    tail -f $LOG"
echo ""
echo "  To run manually RIGHT NOW (test):"
echo "    cd $REPO"
echo "    $VENV_PYTHON run_daily.py --date \$(date +%Y%m%d) --period \"\$(date +'%B %Y')\" --format short"
echo ""
