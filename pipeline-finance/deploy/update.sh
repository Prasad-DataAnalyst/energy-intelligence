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
if [ ! -d "$INSTALL_DIR/.git" ]; then
    echo "   first run — converting to a git checkout"
    cd "$INSTALL_DIR"
    git init -q
    git remote add origin "$REPO_URL" 2>/dev/null || git remote set-url origin "$REPO_URL"
fi

cd "$INSTALL_DIR"
git fetch -q --depth 1 origin "$BRANCH"
# -f/--hard overwrites tracked code only; gitignored secrets and state survive.
git checkout -q -f -B "$BRANCH" "origin/$BRANCH"
git reset -q --hard "origin/$BRANCH"
echo "   now at: $(git log --oneline -1)"

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
"$INSTALL_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
echo "   requirements satisfied"

echo "── Permissions ────────────────────────────────────────────────────────"
chown -R "$SERVICE_USER:$SERVICE_USER" "$INSTALL_DIR"
chmod 600 "$APP_DIR/.env" 2>/dev/null || true
chmod 600 "$APP_DIR"/config/*token*.json 2>/dev/null || true

echo "── Restarting scheduler ───────────────────────────────────────────────"
systemctl restart driftwire326 || echo "   (service not installed — skipped)"
sleep 5

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ Update complete. Verify with:"
echo "   sudo -u $SERVICE_USER bash -c 'cd $APP_DIR && $INSTALL_DIR/venv/bin/python3 main.py --health'"
echo "════════════════════════════════════════════════════════════════════════"
