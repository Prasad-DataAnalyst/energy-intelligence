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

# The cron will run with THIS interpreter — verify it can import the pipeline's
# dependencies NOW instead of failing at 5:30 AM. (A .venv detected here does
# NOT see packages installed with the system pip3.)
if "$VENV_PYTHON" -c "import anthropic, edge_tts, PIL, numpy, dotenv, googleapiclient, requests, swisseph" 2>/dev/null; then
    echo "  OK: pipeline dependencies import with $VENV_PYTHON"
else
    echo ""
    echo "  ERROR: $VENV_PYTHON cannot import the pipeline's dependencies."
    echo "  Install them into that interpreter first:"
    echo "    $VENV_PYTHON -m pip install -r $REPO/requirements-core.txt"
    echo "  Then re-run: bash setup_automation.sh"
    exit 1
fi

# ── 2. Allow passwordless sudo for systemctl stop/start daemon ────────────────
echo ""
echo "[2/4] Configuring passwordless sudo for daemon control..."

# Use the ACTUAL login user — on GCP with OS Login the account is e.g.
# prasad2t_gmail_com, not prasad2t; a hardcoded name silently disables the
# daemon pause and the render then competes with the daemon for RAM.
RUN_USER="$(id -un)"
SUDOERS_LINE="$RUN_USER ALL=(ALL) NOPASSWD: /usr/bin/systemctl stop getmindfuelnow, /usr/bin/systemctl start getmindfuelnow, /bin/systemctl stop getmindfuelnow, /bin/systemctl start getmindfuelnow"
SUDOERS_FILE="/etc/sudoers.d/horoscope-daemon"

# Best-effort: a failed sudo here must not abort the script before the cron
# jobs are installed (set -e is active).
if echo "$SUDOERS_LINE" | sudo tee "$SUDOERS_FILE" > /dev/null 2>&1; then
    sudo chmod 440 "$SUDOERS_FILE"
    if sudo visudo -c -f "$SUDOERS_FILE" > /dev/null 2>&1; then
        echo "  OK: sudoers configured for user $RUN_USER"
    else
        echo "  WARN: sudoers syntax error — removing (daemon pause disabled)"
        sudo rm -f "$SUDOERS_FILE"
    fi
else
    echo "  WARN: could not write sudoers (no sudo?) — daemon pause disabled"
fi

# ── 2b. RETIRE the old standalone daemon (bulletproof) ────────────────────────
# core/horoscope_daemon.py (the `getmindfuelnow` service) was a SECOND, parallel
# uploader that produced the old-format 7-minute videos and per-sign shorts.
# It TRAPS SIGTERM for a "graceful" shutdown and can sit in D (uninterruptible)
# state mid-upload, so a gentle stop leaves it alive and still uploading. We
# therefore: mask the unit (can never start again — stronger than disable),
# then SIGKILL every related process and its watchdog.
echo ""
echo "[2b] Retiring the old daemon (cron pipeline is now the only uploader)..."
# There have been MULTIPLE service names launching the same daemon
# (getmindfuelnow.service AND horoscope-daemon.service). Discover EVERY unit
# that references the daemon/watchdog, plus the known names, and mask them all
# so none can restart it on boot.
UNITS="getmindfuelnow horoscope-daemon"
for f in $(sudo grep -rl "horoscope_daemon\|start_scheduler\|automation_daemon" \
           /etc/systemd/system/ 2>/dev/null); do
    UNITS="$UNITS $(basename "$f")"
done
MASKED=""
for unit in $(echo "$UNITS" | tr ' ' '\n' | sort -u); do
    [ -z "$unit" ] && continue
    if systemctl list-unit-files 2>/dev/null | grep -q "^${unit}"; then
        sudo systemctl disable --now "$unit" 2>/dev/null || true
        sudo systemctl mask "$unit" 2>/dev/null && MASKED="$MASKED $unit"
    fi
done
if [ -n "$MASKED" ]; then
    echo "  OK: stopped + masked (cannot restart):$MASKED"
else
    echo "  OK: no daemon services registered"
fi
# SIGKILL the watchdog FIRST (so it can't respawn the daemon), then the daemon.
# The [s]/[h] bracket trick keeps pkill -f from matching its OWN sudo wrapper's
# command line (which also contains the pattern) — without it, pkill kills the
# sudo process it's running under and bash prints a confusing "Killed" line.
sudo pkill -9 -f "[s]tart_scheduler.sh"   2>/dev/null && echo "  OK: killed start_scheduler.sh" || true
sudo pkill -9 -f "[h]oroscope_daemon.py"  2>/dev/null && echo "  OK: killed horoscope_daemon.py" || true
sleep 2
if pgrep -f "[h]oroscope_daemon.py" >/dev/null 2>&1; then
    echo "  WARN: horoscope_daemon.py STILL running (likely stuck in D-state I/O)."
    echo "        It cannot restart (service masked) but is finishing current I/O."
    echo "        Reboot to clear it fully:  sudo reboot"
else
    echo "  OK: no horoscope_daemon.py process remaining"
fi

# ── 3. Install cron job ───────────────────────────────────────────────────────
echo ""
echo "[3/4] Installing cron job (runs daily at 5:30 AM UTC — all 12 signs, auto-upload)..."

# Preflight doctor at 5:00 AM — emails you 30 min early if anything is broken
# (bad key, expired token, low disk) so you can fix it before the 5:30 run.
DOCTOR_CMD="0 5 * * * cd $REPO && $VENV_PYTHON doctor.py --email >> $LOG 2>&1"
# DAILY pipeline at 5:30 AM (every day) → publishes 10:00 UTC as a Short.
CRON_CMD="30 5 * * * cd $REPO && $VENV_PYTHON run_daily.py --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# WEEKLY pipeline — Sundays 07:30 (after the daily finishes, so the 1-core VM
# never renders two videos at once). "This week" outlook, still a Short.
WEEKLY_CMD="30 7 * * 0 cd $REPO && $VENV_PYTHON run_daily.py --type weekly --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# TOPIC pipeline at 06:00 (every day) — the LONG-FORM (~8 min) astrology
# topic-of-the-day. This is the MONETIZATION engine: banks watch hours and is
# mid-roll-ad eligible. Runs after the daily short so the 1-core VM renders one
# at a time. Topic is chosen from content_calendar by day-of-year (365 unique).
TOPIC_CMD="0 6 * * * cd $REPO && $VENV_PYTHON run_daily.py --type topic --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# MONTHLY pipeline — 1st of the month 08:00. "This month" deep-dive; longer
# (~4.5 min) so YouTube treats it as a regular video (better watch-time credit).
MONTHLY_CMD="0 8 1 * * cd $REPO && $VENV_PYTHON run_daily.py --type monthly --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# WEEKLYFULL pipeline — Mondays 09:00. LONG-FORM (~8 min) in-depth weekly
# horoscope, all 12 signs with a full narrated reading each (not just short
# lines) — mid-roll-ad eligible, for MONETIZATION. Runs well after the Monday
# daily-short (5:30)/topic (6:00) and clear of Sunday's short weekly (7:30).
WEEKLYFULL_CMD="0 9 * * 1 cd $REPO && $VENV_PYTHON run_daily.py --type weeklyfull --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# PREDICTION videos — SHORT (~90s) LANDSCAPE (16:9) with stock images + a
# crispy astrology prediction + disclaimer. Four categories, staggered well
# clear of the long-form renders (topic finishes ~6:15); each is a fast render
# and the flock lock serializes anyway. A "no matches" sports day exits clean.
#   sports    06:30 daily   — today's featured match, astrology pick
#   crypto    06:50 daily   — today's crypto-market astrology mood (not advice)
#   celebrity 07:10 daily   — a rotating zodiac sign's celebrity archetype
#   political 07:20 Mon/Wed/Fri — general national 'mood' only (no elections)
PRED_SPORTS_CMD="30 6 * * * cd $REPO && $VENV_PYTHON run_daily.py --type prediction --category sports --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
PRED_CRYPTO_CMD="50 6 * * * cd $REPO && $VENV_PYTHON run_daily.py --type prediction --category crypto --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
PRED_CELEB_CMD="10 7 * * * cd $REPO && $VENV_PYTHON run_daily.py --type prediction --category celebrity --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
PRED_POLI_CMD="20 7 * * 1,3,5 cd $REPO && $VENV_PYTHON run_daily.py --type prediction --category political --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# WEEKLY TAROT — Saturdays 09:00. LONG-FORM (~7-8 min) all-signs tarot
# reading with real public-domain Rider-Waite card imagery — mid-roll-ad
# eligible, for MONETIZATION. Saturday has no other long-form render.
TAROT_CMD="0 9 * * 6 cd $REPO && $VENV_PYTHON run_daily.py --type tarotweekly --date \$(date +\\%Y\\%m\\%d) --period \"\$(date +'\\%B \\%Y')\" --upload >> $LOG 2>&1"
# Heartbeat check at 12:00 — emails an alert if no successful run in >28h
# (catches: cron never fired, pipeline failing every day, crontab wiped).
HEARTBEAT_CMD="0 12 * * * cd $REPO && $VENV_PYTHON heartbeat.py --check >> $LOG 2>&1"

# Remove any old version of these cron jobs first (both the current runner and
# the legacy daily_runner.py 3 PM job installed by first_time_setup.py).
( crontab -l 2>/dev/null | grep -v -e "run_daily.py" -e "daily_runner.py" -e "doctor.py" -e "heartbeat.py" ) | crontab - 2>/dev/null || true

# Add doctor (5:00) + daily short (5:30) + topic long-form (6:00) + prediction
# sports/crypto/celebrity (6:30/6:50/7:10 daily) + political (Mon/Wed/Fri 7:20)
# + weekly short (Sun 7:30) + monthly (1st 8:00) + weekly long-form (Mon 9:00)
# + heartbeat (12:00)
( crontab -l 2>/dev/null; echo "$DOCTOR_CMD"; echo "$CRON_CMD"; echo "$TOPIC_CMD"; echo "$PRED_SPORTS_CMD"; echo "$PRED_CRYPTO_CMD"; echo "$PRED_CELEB_CMD"; echo "$PRED_POLI_CMD"; echo "$TAROT_CMD"; echo "$WEEKLY_CMD"; echo "$MONTHLY_CMD"; echo "$WEEKLYFULL_CMD"; echo "$HEARTBEAT_CMD" ) | crontab -

echo "  OK: cron jobs installed (doctor 5:00, daily-short 5:30, topic 6:00, prediction sports/crypto/celebrity 6:30/6:50/7:10, prediction-political Mon/Wed/Fri 7:20, tarot Sat 9:00, weekly-short Sun 7:30, monthly 1st 8:00, weekly-long-form Mon 9:00, heartbeat 12:00)"

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
