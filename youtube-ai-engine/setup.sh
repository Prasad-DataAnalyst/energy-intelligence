#!/usr/bin/env bash
# setup.sh — One-command install for the YouTube AI Engine
# Usage: bash setup.sh
set -e

VENV_DIR=".venv"
PYTHON=$(which python3)

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  YouTube AI Engine — Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Python virtual environment ─────────────────────────────────────────────────
if [ ! -d "$VENV_DIR" ]; then
  echo "[1/5] Creating virtual environment…"
  $PYTHON -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"
pip install --upgrade pip --quiet

# ── Core dependencies ──────────────────────────────────────────────────────────
echo "[2/5] Installing Python dependencies…"
pip install --quiet \
  anthropic \
  google-api-python-client \
  google-auth google-auth-oauthlib google-auth-httplib2 \
  feedparser \
  pyyaml \
  pillow \
  requests \
  apscheduler \
  pytrends \
  urllib3

# ── Optional: enhanced providers ──────────────────────────────────────────────
echo "[3/5] Installing optional dependencies (best-effort)…"
pip install --quiet elevenlabs edge-tts openai 2>/dev/null || true
pip install --quiet whisper-openai 2>/dev/null || true
pip install --quiet replicate 2>/dev/null || true

# ── System: espeak-ng TTS (offline fallback) ───────────────────────────────────
echo "[4/5] Checking espeak-ng…"
if ! command -v espeak-ng &>/dev/null; then
  echo "  Installing espeak-ng (offline TTS)…"
  if command -v apt-get &>/dev/null; then
    # Direct dpkg install to avoid dependency conflicts in cloud
    cd /tmp
    apt-get download espeak-ng espeak-ng-data libespeak-ng1 libpcaudio0 libsonic0 2>/dev/null || true
    dpkg -i libsonic0*.deb libpcaudio0*.deb espeak-ng-data*.deb libespeak-ng1*.deb espeak-ng*.deb 2>/dev/null || true
    cd -
  else
    echo "  Warning: apt-get not found. Install espeak-ng manually."
  fi
else
  echo "  espeak-ng already installed ✓"
fi

# ── Config setup ────────────────────────────────────────────────────────────────
echo "[5/5] Setting up config…"
if [ ! -f config/api_keys.env ]; then
  cp config/api_keys.env.example config/api_keys.env
  echo "  Created config/api_keys.env — fill in your API keys!"
fi

# Create required directories
mkdir -p data output logs

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Setup complete!"
echo ""
echo "  Next steps:"
echo "  1. Fill in config/api_keys.env with your API keys"
echo "  2. Authenticate YouTube: cd ../youtube_automation && python setup_youtube_auth.py"
echo "  3. Run tests: python main.py --test"
echo "  4. Run pipeline: python main.py --all"
echo "  5. Start scheduler: python main.py --schedule"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
