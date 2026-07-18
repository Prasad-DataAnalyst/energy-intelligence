#!/usr/bin/env bash
# deploy/vm_setup.sh — one-shot bootstrap for the DriftWire326 pipeline
# on a GCP free-tier e2-micro VM (Debian 12 / Ubuntu 22.04+).
#
# Usage (on the VM):
#   curl -O https://raw.githubusercontent.com/<your-user>/energy-intelligence/<branch>/pipeline-finance/deploy/vm_setup.sh
#   sudo bash vm_setup.sh https://github.com/<your-user>/energy-intelligence.git <branch>
#
# Or clone first and run:  sudo bash pipeline-finance/deploy/vm_setup.sh
#
# What it does:
#   1. System packages: python3-venv, ffmpeg, imagemagick, fonts, espeak
#   2. ImageMagick policy fix (moviepy TextClip needs @/tmp read rights)
#   3. 4 GB swap file (e2-micro has 1 GB RAM — rendering needs headroom)
#   4. Clones the repo to /opt/driftwire326 and installs Python deps in a venv
#   5. Installs + enables the systemd service (starts AFTER you add .env/tokens)
set -euo pipefail

REPO_URL="${1:-}"
BRANCH="${2:-main}"
INSTALL_DIR=/opt/driftwire326
APP_DIR="$INSTALL_DIR/pipeline-finance"
SERVICE_USER=driftwire

echo "── [1/6] System packages ──────────────────────────────────────────────"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
    python3 python3-venv python3-pip git \
    ffmpeg imagemagick \
    fonts-dejavu fonts-liberation \
    espeak libespeak1

echo "── [2/6] ImageMagick policy fix (moviepy TextClip) ────────────────────"
for POLICY in /etc/ImageMagick-6/policy.xml /etc/ImageMagick-7/policy.xml; do
    if [ -f "$POLICY" ]; then
        sed -i 's/<policy domain="path" rights="none" pattern="@\*"\/>/<!-- moviepy needs @ paths: policy relaxed by vm_setup.sh -->/' "$POLICY"
        echo "   patched $POLICY"
    fi
done

echo "── [3/6] Swap (4 GB — required for video rendering on 1 GB RAM) ───────"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l 4G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
    swapon /swapfile
    grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "   4 GB swap active"
else
    echo "   swap already present"
fi

echo "── [4/6] Service user + repo ──────────────────────────────────────────"
id -u $SERVICE_USER &>/dev/null || useradd -r -m -s /bin/bash $SERVICE_USER

if [ ! -d "$APP_DIR" ]; then
    if [ -z "$REPO_URL" ]; then
        echo "❌ Repo not present at $APP_DIR and no repo URL given."
        echo "   Re-run: sudo bash vm_setup.sh <repo-url> <branch>"
        exit 1
    fi
    # Clone the whole repo as INSTALL_DIR so .git is retained —
    # future updates are just: git -C $INSTALL_DIR pull
    git clone --branch "$BRANCH" --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi
chown -R $SERVICE_USER:$SERVICE_USER "$INSTALL_DIR"

echo "── [5/6] Python venv + dependencies ───────────────────────────────────"
sudo -u $SERVICE_USER python3 -m venv "$INSTALL_DIR/venv"
sudo -u $SERVICE_USER "$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
sudo -u $SERVICE_USER "$INSTALL_DIR/venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"
echo "   dependencies installed"

echo "── [6/6] systemd service ──────────────────────────────────────────────"
sed "s|/usr/bin/python3|$INSTALL_DIR/venv/bin/python3|" \
    "$APP_DIR/deploy/driftwire326.service" > /etc/systemd/system/driftwire326.service
systemctl daemon-reload
systemctl enable driftwire326
echo "   service installed + enabled (NOT started yet)"

echo ""
echo "════════════════════════════════════════════════════════════════════════"
echo "✅ VM ready. Before starting the service, from your LOCAL PC copy:"
echo ""
echo "   scp .env config/finance_oauth.json config/youtube_token.json \\"
echo "       config/analytics_token.json <you>@<vm-ip>:/tmp/"
echo ""
echo "Then on the VM:"
echo "   sudo mv /tmp/.env $APP_DIR/.env"
echo "   sudo mv /tmp/finance_oauth.json /tmp/youtube_token.json /tmp/analytics_token.json $APP_DIR/config/"
echo "   sudo chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR"
echo "   sudo chmod 600 $APP_DIR/.env $APP_DIR/config/*token*.json"
echo ""
echo "Recommended in .env for this 1 GB VM:  VIDEO_RESOLUTION=1280x720"
echo ""
echo "Smoke test (as the service user — the cd matters, the service user"
echo "cannot read your home directory):"
echo "   sudo -u $SERVICE_USER bash -c 'cd $APP_DIR && $INSTALL_DIR/venv/bin/python3 -m pytest tests -q'"
echo "   sudo -u $SERVICE_USER bash -c 'cd $APP_DIR && $INSTALL_DIR/venv/bin/python3 main.py --quota'"
echo ""
echo "Start for real:"
echo "   sudo systemctl start driftwire326 && journalctl -u driftwire326 -f"
echo "════════════════════════════════════════════════════════════════════════"
