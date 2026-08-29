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

REPO_URL="${REPO_URL:-https://github.com/Prasad-DataAnalyst/energy-intelligence.git}"
BRANCH="${BRANCH:-claude/driftwire326-youtube-automation-h8zkx8}"
INSTALL_DIR="${INSTALL_DIR:-/opt/driftwire326}"
APP_DIR="$INSTALL_DIR/pipeline-finance"
SERVICE_USER="${SERVICE_USER:-driftwire}"
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
if ! systemctl restart driftwire326 2>/dev/null; then
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

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ Update complete. Verify with:"
echo "   sudo -u $SERVICE_USER bash -c 'cd $APP_DIR && $INSTALL_DIR/venv/bin/python3 main.py --health'"
echo "════════════════════════════════════════════════════════════════════════"
