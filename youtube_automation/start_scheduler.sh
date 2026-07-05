#!/bin/bash
# ============================================================
# start_scheduler.sh — persistent watchdog for the horoscope daemon
# GetMindFuelNow — All 12 Zodiac Signs, Daily/Weekly/Monthly/Yearly
#
# Usage: bash start_scheduler.sh          (runs in foreground)
#        nohup bash start_scheduler.sh &  (runs in background)
# ============================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── RETIRED ────────────────────────────────────────────────────────────────────
# This watchdog respawned core/horoscope_daemon.py — a second, parallel uploader
# that produced the old-format videos/shorts. The cron pipeline (run_daily.py) is
# now the only uploader. Exit immediately so nothing relaunches the old daemon.
# Escape hatch for debugging only: ALLOW_LEGACY_DAEMON=1 bash start_scheduler.sh
if [ "${ALLOW_LEGACY_DAEMON:-0}" != "1" ]; then
    echo "[RETIRED] start_scheduler.sh is disabled — the cron pipeline (run_daily.py) is the only uploader."
    exit 0
fi

LOG_FILE="logs/scheduler.log"
PID_FILE="logs/daemon.pid"
PYTHON="python3"

# Activate virtualenv if present
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
    PYTHON=".venv/bin/python3"
fi

mkdir -p logs

echo "============================================================" >> "$LOG_FILE"
echo "GetMindFuelNow Horoscope Watchdog started at $(date -u '+%Y-%m-%d %H:%M:%S UTC')" >> "$LOG_FILE"
echo "PID: $$" >> "$LOG_FILE"
echo "$$" > "$PID_FILE"

# Trap SIGTERM/SIGINT for clean shutdown
cleanup() {
    echo "Watchdog shutting down at $(date -u '+%Y-%m-%d %H:%M:%S UTC')" >> "$LOG_FILE"
    rm -f "$PID_FILE"
    exit 0
}
trap cleanup SIGTERM SIGINT

BACKOFF=5
MAX_BACKOFF=300

while true; do
    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Starting horoscope daemon..." >> "$LOG_FILE"

    $PYTHON core/horoscope_daemon.py >> "$LOG_FILE" 2>&1
    EXIT_CODE=$?

    echo "[$(date -u '+%Y-%m-%d %H:%M:%S UTC')] Daemon exited (code=$EXIT_CODE). Restarting in ${BACKOFF}s..." >> "$LOG_FILE"

    sleep "$BACKOFF"

    # Exponential backoff capped at MAX_BACKOFF
    BACKOFF=$(( BACKOFF * 2 ))
    if [ "$BACKOFF" -gt "$MAX_BACKOFF" ]; then
        BACKOFF=$MAX_BACKOFF
    fi
done
