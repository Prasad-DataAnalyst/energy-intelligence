#!/bin/bash
# Run once: nohup bash start_scheduler.sh &
cd "$(dirname "$0")"
source .venv/bin/activate
echo "Scheduler started at $(date)" >> logs/scheduler.log
python daily_runner.py --schedule >> logs/scheduler.log 2>&1
