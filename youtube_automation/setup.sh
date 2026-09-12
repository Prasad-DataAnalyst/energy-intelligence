#!/usr/bin/env bash
# One-time setup for the YouTube automation system.
set -e

echo "=== Energy Intelligence — YouTube Automation Setup ==="

# 1. Python virtual environment
if [ ! -d ".venv" ]; then
  echo "[1/5] Creating Python virtual environment…"
  python3 -m venv .venv
fi

source .venv/bin/activate

# 2. System dependencies (Ubuntu/Debian)
echo "[2/5] Installing system packages (ffmpeg, fonts)…"
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg fonts-dejavu-core

# 3. Python dependencies
echo "[3/5] Installing Python packages…"
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

# 4. Env file
if [ ! -f ".env" ]; then
  echo "[4/5] Creating .env from template…"
  cp .env.example .env
  echo "  *** Edit .env and fill in your API keys before running. ***"
else
  echo "[4/5] .env already exists — skipping."
fi

# 5. Output dirs
echo "[5/5] Creating output directories…"
mkdir -p output logs

echo ""
echo "Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Fill in .env  (EIA_API_KEY, YOUTUBE_CHANNEL_ID, YOUTUBE_PRIVACY_STATUS)"
echo "  2. Download client_secrets.json from Google Cloud Console"
echo "     (APIs & Services → Credentials → OAuth 2.0 Client IDs → Desktop App)"
echo "  3. Test video generation (no upload):"
echo "       source .venv/bin/activate && python daily_runner.py --now --no-upload"
echo "  4. Authenticate with YouTube (first upload triggers browser OAuth):"
echo "       python daily_runner.py --now"
echo "  5. Start daily scheduler:"
echo "       nohup python daily_runner.py --schedule &"
echo "     Or add to crontab (see crontab.txt)"
