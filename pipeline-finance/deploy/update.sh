#!/usr/bin/env bash
# deploy/update.sh — pull the latest code onto the VM, safely.
#
#   sudo bash /opt/driftwire326/pipeline-finance/deploy/update.sh
#
# Replaces per-file `curl` updates, which failed twice in practice: once by
# silently missing files (a run built with half the new code), and once by
# serving a stale raw.githubusercontent CDN copy minutes after a push.
#
# Converts the install to a real git checkout on first run, then it is just
# `git fetch && git reset --hard`. Secrets and state are never touched:
# .env, config/*.json, logs/ and output/ are gitignored, and this script
# backs up the credential files before touching the tree regardless.
set -euo pipefail

# Run from a throwaway copy of ourselves.
#
# This script updates the checkout it lives in, so `git reset --hard` below
# replaces this very file while bash is still reading it. Bash reads a
# script lazily by byte offset rather than loading it whole, so a mid-run
# replacement makes it carry on with stale content or resume at the wrong
# offset in a file that has changed underneath it. Observed in practice: a
# run that checked out a commit containing the restart check still executed
# the previous version and skipped it, reporting success without verifying
# anything.
if [ "${DW_UPDATE_REEXEC:-}" != "1" ]; then
    _copy="$(mktemp /tmp/driftwire-update.XXXXXX)"
    cat "$0" > "$_copy"
    DW_UPDATE_REEXEC=1 exec bash "$_copy" "$@"
fi
# Now running from the copy. Unlink it so /tmp does not accumulate one per
# deploy — on Linux the running shell keeps its open handle regardless.
case "$0" in /tmp/driftwire-update.*) rm -f "$0" ;; esac

REPO_URL="${REPO_URL:-https://github.com/Prasad-DataAnalyst/energy-intelligence.git}"
BRANCH="${BRANCH:-claude/driftwire326-youtube-automation-h8zkx8}"
INSTALL_DIR="${INSTALL_DIR:-/opt/driftwire326}"
APP_DIR="$INSTALL_DIR/pipeline-finance"
SERVICE_USER="${SERVICE_USER:-driftwire}"
# How long to wait for an in-flight video build before giving up on
# restarting. A long-form build takes a few minutes on this instance.
BUILD_WAIT_MINUTES="${BUILD_WAIT_MINUTES:-15}"
BACKUP_DIR="$INSTALL_DIR/.credentials-backup"

echo "── Backing up credentials ─────────────────────────────────────────────"
mkdir -p "$BACKUP_DIR"
for f in .env config/finance_oauth.json config/youtube_token.json config/analytics_token.json; do
    if [ -f "$APP_DIR/$f" ]; then
        cp -p "$APP_DIR/$f" "$BACKUP_DIR/$(basename "$f")"
        echo "   saved $(basename "$f")"
    fi
done

echo "── Syncing code ───────────────────────────────────────────────────────"
# This script runs as root over a driftwire-owned tree, which git refuses to
# touch ("detected dubious ownership"). Passing safe.directory per invocation
# is command-scope config — it works regardless of where HOME points under
# sudo, which a `git config --global` write does not reliably do.
GIT=(git -c "safe.directory=$INSTALL_DIR")
# Also record it globally so manual `git` calls in this directory work later.
git config --global --add safe.directory "$INSTALL_DIR" 2>/dev/null || true

cd "$INSTALL_DIR"
if [ ! -d .git ]; then
    echo "   first run — converting to a git checkout"
    "${GIT[@]}" init -q
fi
# Set the remote every run, not just on init: an interrupted first run can
# leave .git present but remote-less, and the guard above would skip it.
"${GIT[@]}" remote add origin "$REPO_URL" 2>/dev/null \
    || "${GIT[@]}" remote set-url origin "$REPO_URL"

BEFORE_SHA="$("${GIT[@]}" rev-parse HEAD 2>/dev/null || echo none)"
"${GIT[@]}" fetch -q --depth 1 origin "$BRANCH"
# -f/--hard overwrites tracked code only; gitignored secrets and state survive.
"${GIT[@]}" checkout -q -f -B "$BRANCH" "origin/$BRANCH"
"${GIT[@]}" reset -q --hard "origin/$BRANCH"
echo "   now at: $("${GIT[@]}" log --oneline -1)"

echo "── Restoring credentials ──────────────────────────────────────────────"
for f in .env finance_oauth.json youtube_token.json analytics_token.json; do
    src="$BACKUP_DIR/$f"
    [ -f "$src" ] || continue
    if [ "$f" = ".env" ]; then
        dest="$APP_DIR/.env"
    else
        dest="$APP_DIR/config/$f"
    fi
    # Restore only when the checkout removed or replaced the real file.
    if [ ! -f "$dest" ] || ! cmp -s "$src" "$dest"; then
        cp -p "$src" "$dest"
        echo "   restored $f"
    fi
done

echo "── Dependencies ───────────────────────────────────────────────────────"
if [ -x "$INSTALL_DIR/venv/bin/pip" ]; then
    if "$INSTALL_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"; then
        echo "   requirements satisfied"
    else
        echo "   ⚠️  pip reported a problem — check manually, code is already updated"
    fi
else
    echo "   (no venv at $INSTALL_DIR/venv — skipped)"
fi

echo "── Permissions ────────────────────────────────────────────────────────"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$APP_DIR/.env" 2>/dev/null || true
chmod 600 "$APP_DIR"/config/*token*.json 2>/dev/null || true

echo "── Restarting scheduler ───────────────────────────────────────────────"
AFTER_SHA="$("${GIT[@]}" rev-parse HEAD 2>/dev/null || echo none)"

# A pipeline runs as a child of the scheduler, so restarting mid-build kills
# it and loses that video — systemd takes the whole cgroup down with the unit.
#
# Detected by looking for the multiprocessing bootstrap in a child's command
# line. "Does the scheduler have any children at all" was the obvious test
# and it was wrong: the spawn start method also launches a
# multiprocessing.resource_tracker child, and that one lives as long as the
# daemon does. So from the first pipeline run onward the guard was
# permanently true, every deploy waited its full fifteen minutes and gave
# up, and the daemon kept running whatever code it had started with. The
# tracker is bookkeeping, not work; a zombie is already finished.
pipeline_children() {
    local pid stat cmd found=""
    for pid in $(pgrep -P "$1" 2>/dev/null); do
        stat="$(ps -o stat= -p "$pid" 2>/dev/null | tr -d ' ')"
        cmd="$(ps -o args= -p "$pid" 2>/dev/null)"
        case "$stat" in Z*) continue ;; esac
        case "$cmd" in *resource_tracker*) continue ;; esac
        found="$found $pid"
    done
    echo "${found# }"
}

SCHED_PID="$(systemctl show -p MainPID --value driftwire326 2>/dev/null || echo 0)"
if [ "${SCHED_PID:-0}" -gt 0 ] 2>/dev/null && [ -n "$(pipeline_children "$SCHED_PID")" ]; then
    # Wait it out rather than refusing and handing back a restart command.
    # Printing "run systemctl restart yourself" invites exactly the thing the
    # guard exists to prevent: the operator runs it immediately and kills the
    # build anyway. Waiting is what they actually wanted.
    echo "   a pipeline is building — waiting for it to finish (up to ${BUILD_WAIT_MINUTES}m)"
    for _pid in $(pipeline_children "$SCHED_PID"); do
        echo "      PID $_pid, running $(ps -o etime= -p "$_pid" 2>/dev/null | tr -d ' ')"
    done
    waited=0
    while [ "$waited" -lt "$((BUILD_WAIT_MINUTES * 2))" ]; do
        sleep 30
        waited=$((waited + 1))
        if [ -z "$(pipeline_children "$SCHED_PID")" ]; then
            break
        fi
        if [ $((waited % 4)) -eq 0 ]; then
            # $waited counts 30-second sleeps. Printing "${waited}0s" labelled
            # them as 10 seconds each, so a full 15-minute wait reported
            # "280s elapsed" and looked like it had given up after 4 minutes.
            echo "      still building… $((waited / 2))m$((waited % 2 * 30))s elapsed"
        fi
    done
    if [ -n "$(pipeline_children "$SCHED_PID")" ]; then
        echo "   ⚠️  still building after ${BUILD_WAIT_MINUTES}m — leaving the daemon alone."
        echo "      The code is updated and takes effect on the next scheduled run."
        echo "      Do NOT run 'systemctl restart' until the build finishes or you"
        echo "      will lose that video."
    else
        echo "   build finished — restarting now"
        systemctl restart driftwire326 2>/dev/null || true
        sleep 8
        state=$(systemctl is-active driftwire326 2>/dev/null || true)
        [ "$state" = "active" ] && echo "   ✅ scheduler active" \
            || echo "   ❌ scheduler is '$state' after restart"
    fi
elif [ "$BEFORE_SHA" = "$AFTER_SHA" ]; then
    echo "   already at $AFTER_SHA — nothing changed, leaving the scheduler alone"
elif ! systemctl restart driftwire326 2>/dev/null; then
    echo "   (service not installed — skipped)"
else
    # `systemctl restart` returns as soon as the unit is *started*, not when
    # it is healthy: a Type=simple unit that dies 200ms later still exits 0.
    # Reporting success on that basis is what the 39-day outage looked like
    # from the outside — a daemon crash-looping in "activating (auto-restart)"
    # while everything upstream of it said fine. Ask what state it is in.
    sleep 8
    state=$(systemctl is-active driftwire326 2>/dev/null || true)
    if [ "$state" != "active" ]; then
        echo "   ❌ scheduler is '$state', not 'active' — it did not survive the restart"
        echo "      the code is updated; the daemon is not running."
        echo ""
        journalctl -u driftwire326 -n 25 --no-pager 2>&1 | sed 's/^/   /'
        echo ""
        echo "   startup errors are also written to $APP_DIR/logs/STARTUP_ERROR.txt"
        exit 1
    fi
    echo "   ✅ scheduler active"
fi

# Print the commands rather than leaving them to be remembered. The install
# is not in a home directory, is owned by another user, and runs from a venv
# rather than a `python` on PATH — so a plausible-looking command typed from
# memory fails on all three counts.
RUN_AS="sudo -u $SERVICE_USER bash -c 'cd $APP_DIR && $INSTALL_DIR/venv/bin/python3 main.py"
echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ Update complete. Run these from anywhere on this VM:"
echo ""
echo "   Is the pipeline healthy?"
echo "   $RUN_AS --health'"
echo ""
echo "   Why are the videos not being watched?"
echo "   $RUN_AS --diagnose'"
echo ""
echo "   What is actually public on the channel?"
echo "   $RUN_AS --verify-uploads'"
echo "════════════════════════════════════════════════════════════════════════"
