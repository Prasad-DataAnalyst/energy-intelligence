#!/bin/bash
# ============================================================
# cloudshell_setup.sh
# Run this ONCE in Google Cloud Shell to authorize YouTube
# and generate the secrets needed for GitHub Actions.
#
# Usage (inside Google Cloud Shell):
#   bash cloudshell_setup.sh
# ============================================================
set -e

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  GetMindFuelNow — One-Time Cloud Shell Setup${NC}"
echo -e "${CYAN}  Owner: Prasad Selvaraj (prasad2t@gmail.com)${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Step 1: Install system packages ──────────────────────────────────────────
echo -e "${YELLOW}[1/6] Installing system packages...${NC}"
sudo apt-get update -qq
sudo apt-get install -y ffmpeg espeak-ng python3-venv python3-pip -q
echo -e "${GREEN}    Done${NC}"

# ── Step 2: Python virtual environment ───────────────────────────────────────
echo -e "${YELLOW}[2/6] Setting up Python environment...${NC}"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo -e "${GREEN}    Done${NC}"

# ── Step 3: Check for client_secrets.json ────────────────────────────────────
echo ""
echo -e "${YELLOW}[3/6] Checking for client_secrets.json...${NC}"
if [ ! -f "client_secrets.json" ]; then
    echo ""
    echo -e "${RED}  client_secrets.json NOT FOUND${NC}"
    echo ""
    echo "  You need to download it from Google Cloud Console."
    echo "  Follow these steps:"
    echo ""
    echo "  1. Go to: https://console.cloud.google.com"
    echo "  2. Select your project (GetMindFuelNow)"
    echo "  3. Click: APIs & Services → Credentials"
    echo "  4. Click the download icon next to your OAuth 2.0 Client"
    echo "  5. Rename the downloaded file to: client_secrets.json"
    echo ""
    echo "  In Cloud Shell, click the 3-dot menu (top right) →"
    echo "  'Upload' → upload your client_secrets.json"
    echo "  Then move it here:"
    echo "  mv ~/client_secrets.json $SCRIPT_DIR/client_secrets.json"
    echo ""
    echo "  Then re-run this script: bash cloudshell_setup.sh"
    exit 1
fi
echo -e "${GREEN}    Found client_secrets.json${NC}"

# ── Step 4: YouTube OAuth ─────────────────────────────────────────────────────
echo ""
echo -e "${YELLOW}[4/6] Authorizing YouTube access...${NC}"
echo ""
echo "  A URL will appear below."
echo "  → Open it on your phone or any browser"
echo "  → Sign in with prasad2t@gmail.com"
echo "  → Click Allow"
echo "  → Copy the code shown"
echo "  → Paste it back here"
echo ""

python first_time_setup.py --no-upload
echo -e "${GREEN}    YouTube authorized!${NC}"

# ── Step 5: Encode secrets for GitHub ────────────────────────────────────────
echo ""
echo -e "${YELLOW}[5/6] Encoding secrets for GitHub...${NC}"
echo ""

if [ ! -f "youtube_token.json" ]; then
    echo -e "${RED}  youtube_token.json not found — authorization may have failed${NC}"
    exit 1
fi

TOKEN_B64=$(base64 -w 0 youtube_token.json)
SECRETS_B64=$(base64 -w 0 client_secrets.json)

echo -e "${GREEN}    Encoded successfully${NC}"

# ── Step 6: Print all GitHub Secrets to add ──────────────────────────────────
echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${CYAN}  STEP 6/6 — ADD THESE TO GITHUB SECRETS${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
echo "  Go to: https://github.com/prasad-dataanalyst/energy-intelligence"
echo "  Click: Settings → Secrets and variables → Actions → New repository secret"
echo ""
echo -e "${YELLOW}  Secret 1 — Name: YOUTUBE_TOKEN_JSON${NC}"
echo "  Value (copy everything between the lines):"
echo "  ────────────────────────────────────────"
echo "$TOKEN_B64"
echo "  ────────────────────────────────────────"
echo ""
echo -e "${YELLOW}  Secret 2 — Name: YOUTUBE_CLIENT_SECRETS${NC}"
echo "  Value:"
echo "  ────────────────────────────────────────"
echo "$SECRETS_B64"
echo "  ────────────────────────────────────────"
echo ""
echo -e "${YELLOW}  Secret 3 — Name: YOUTUBE_CHANNEL_ID${NC}"
CHANNEL_ID=$(python3 -c "
import json
try:
    with open('youtube_token.json') as f:
        pass
    import sys
    sys.path.insert(0,'.')
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_file('youtube_token.json')
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
    yt = build('youtube','v3',credentials=creds)
    resp = yt.channels().list(part='snippet',mine=True).execute()
    print(resp['items'][0]['id'])
except Exception as e:
    print('(run: python first_time_setup.py to get this)')
" 2>/dev/null)
echo "  Value: $CHANNEL_ID"
echo ""
echo -e "${YELLOW}  Optional secrets (add if you have them — improves quality):${NC}"
echo ""
echo "  Secret 4 — Name: ELEVENLABS_API_KEY"
echo "  Value: (get free at elevenlabs.io)"
echo ""
echo "  Secret 5 — Name: OPENAI_API_KEY"
echo "  Value: (get at platform.openai.com)"
echo ""
echo "  Secret 6 — Name: REPLICATE_API_TOKEN"
echo "  Value: (get at replicate.com)"
echo ""
echo "  Secret 7 — Name: PEXELS_API_KEY"
echo "  Value: (get FREE at pexels.com/api)"
echo ""
echo -e "${CYAN}============================================================${NC}"
echo -e "${GREEN}  After adding all secrets → go to GitHub Actions tab${NC}"
echo -e "${GREEN}  Click: Daily YouTube Video → Run workflow → Run workflow${NC}"
echo -e "${GREEN}  Your first video will upload in about 5-10 minutes!${NC}"
echo -e "${CYAN}============================================================${NC}"
echo ""
