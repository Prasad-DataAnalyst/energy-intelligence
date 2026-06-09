#!/usr/bin/env bash
# setup.sh — Full install + verification for the YouTube AI Engine
# Usage: bash setup.sh
set -e

ENGINE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ENGINE_DIR"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "  ${GREEN}✓${NC}  $*"; }
warn() { echo -e "  ${YELLOW}!${NC}  $*"; }
fail() { echo -e "  ${RED}✗${NC}  $*"; }

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  YouTube AI Engine — Full Setup"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Step 1: Python version check ───────────────────────────────────────────────
echo ""
echo "[1/8] Checking Python version…"
PYTHON=$(which python3)
PY_VER=$($PYTHON -c "import sys; print('%d.%d' % sys.version_info[:2])")
PY_MIN="3.10"
if python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)"; then
  ok "Python $PY_VER"
else
  fail "Python $PY_VER — requires 3.10+. Install Python 3.10 or later."
  exit 1
fi

# ── Step 2: Core Python dependencies ───────────────────────────────────────────
echo ""
echo "[2/8] Installing Python dependencies…"
pip install --quiet --upgrade pip 2>/dev/null || true
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
  urllib3 \
  numpy
ok "Core dependencies installed"

echo "  Installing optional providers (best-effort)…"
pip install --quiet elevenlabs edge-tts openai 2>/dev/null && ok "Voice providers (ElevenLabs, edge-tts, OpenAI)" || warn "Optional voice providers skipped"
pip install --quiet replicate 2>/dev/null && ok "Replicate image generation" || warn "Replicate skipped (optional)"

# Upgrade cryptography to avoid cffi backend mismatch
pip install --quiet --ignore-installed cryptography cffi 2>/dev/null || true

# ── Step 3: System dependency — espeak-ng ──────────────────────────────────────
echo ""
echo "[3/8] Checking espeak-ng (offline TTS fallback)…"
if command -v espeak-ng &>/dev/null; then
  ok "espeak-ng $(espeak-ng --version 2>&1 | head -1)"
else
  warn "espeak-ng not found — installing…"
  if command -v apt-get &>/dev/null; then
    apt-get install -y -q espeak-ng espeak-ng-data 2>/dev/null \
      || { cd /tmp && apt-get download -q espeak-ng espeak-ng-data libespeak-ng1 libpcaudio0 libsonic0 2>/dev/null || true
           dpkg -i libsonic0*.deb libpcaudio0*.deb espeak-ng-data*.deb libespeak-ng1*.deb espeak-ng*.deb 2>/dev/null || true
           cd "$ENGINE_DIR"; }
    command -v espeak-ng &>/dev/null && ok "espeak-ng installed" || warn "Could not install espeak-ng — voice generation will use edge-tts instead"
  else
    warn "apt-get not available — install espeak-ng manually for offline TTS"
  fi
fi

# ── Step 4: Directory structure ────────────────────────────────────────────────
echo ""
echo "[4/8] Ensuring directory structure…"
mkdir -p data output logs vault
mkdir -p output/energy_2026
mkdir -p data/input
ok "Directories: data/ output/ logs/ vault/"

# ── Step 5: Config setup ───────────────────────────────────────────────────────
echo ""
echo "[5/8] Config files…"
if [ ! -f config/api_keys.env ]; then
  cp config/api_keys.env.example config/api_keys.env
  warn "Created config/api_keys.env — FILL IN YOUR API KEYS BEFORE RUNNING"
else
  ok "config/api_keys.env exists"
fi

for f in config/master_config.yaml config/channel_persona.yaml config/style_profiles.yaml; do
  [ -f "$f" ] && ok "$f" || fail "Missing: $f"
done

# ── Step 6: Database init ──────────────────────────────────────────────────────
echo ""
echo "[6/8] Initialising database…"
python3 -c "
import sys; sys.path.insert(0,'.')
from core.memory import Memory
m = Memory('data/performance_db.sqlite')
import sqlite3
with m._conn() as c:
    tables = [r[0] for r in c.execute(\"SELECT name FROM sqlite_master WHERE type='table'\").fetchall()]
print(f'  {len(tables)} tables in data/performance_db.sqlite')
" && ok "Database initialised" || fail "Database init failed"

# ── Step 7: Module imports verification ────────────────────────────────────────
echo ""
echo "[7/8] Verifying all modules import cleanly…"
python3 -c "
import sys, importlib
sys.path.insert(0,'.')

modules = [
    'core.memory','core.brain','core.scheduler','core.self_healer',
    'core.registry','core.event_bus','core.circuit_breaker',
    'core.config_manager','core.health_monitor',
    'interfaces','interfaces.base_module','interfaces.base_job','interfaces.base_manager',
    'framework','framework.code_validator','framework.patch_manager',
    'framework.error_corrector','framework.update_pipeline',
    'content.archive_miner','content.category_universe','content.data_storyteller',
    'content.private_vault','content.synthesizer',
    'data.external_feeds','data.ingest_documents',
    'intelligence.algorithm_decoder','intelligence.algorithm_hacker',
    'intelligence.audience_analyzer','intelligence.community_engine',
    'intelligence.competitor_dominator','intelligence.competitor_spy',
    'intelligence.future_proof','intelligence.global_expansion',
    'intelligence.growth_hacker','intelligence.performance_scorer',
    'intelligence.trend_hunter',
    'production.caption_engine','production.editor','production.script_writer',
    'production.shorts_factory','production.thumbnail_ai',
    'production.viewer_psychology','production.visual_engine','production.voice_engine',
    'publishing.scheduler_optimizer','publishing.seo_optimizer',
    'publishing.youtube_publisher','publishing.youtube_studio',
    'monetization.fast_track','monetization.monetization_engine',
    'evolution.ab_tester','evolution.code_auditor','evolution.feedback_loop',
    'evolution.meta_learner','evolution.version_manager',
]
ok, failed = [], []
for m in modules:
    try:
        importlib.import_module(m)
        ok.append(m)
    except Exception as e:
        failed.append((m, str(e)))

print(f'  {len(ok)}/{len(modules)} modules OK')
for m,e in failed:
    print(f'  FAIL {m}: {e}')
sys.exit(1 if failed else 0)
" && ok "All 55 modules import cleanly" || fail "Some modules failed to import"

# ── Step 8: Test suite ─────────────────────────────────────────────────────────
echo ""
echo "[8/8] Running test suite…"
RESULT=$(python3 -m unittest tests.auto_test 2>&1 | tail -4)
echo "$RESULT" | grep -q "^OK" \
  && ok "$(echo "$RESULT" | grep 'Ran')" \
  || { fail "Tests failed!"; echo "$RESULT"; exit 1; }

# ── Scheduler job summary ──────────────────────────────────────────────────────
echo ""
echo "  Scheduled jobs (UTC cron):"
python3 -c "
import sys; sys.path.insert(0,'.')
from core.scheduler import Scheduler
from core.memory import Memory
s = Scheduler(Memory(':memory:'))
sched = s._build_scheduler()
for j in sched.get_jobs():
    print(f'    [{j.id:30s}] {str(j.trigger)}')
" 2>/dev/null || true

# ── Done ───────────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "  ${GREEN}Setup complete!${NC}"
echo ""
echo "  Required before first run:"
echo "    1. Edit  config/api_keys.env  (ANTHROPIC_API_KEY, YOUTUBE_*)"
echo "    2. Place OAuth token:  data/youtube_oauth_token.json"
echo "       (generate with: python main.py --setup-oauth)"
echo ""
echo "  Run commands:"
echo "    python main.py --test            # smoke test all phases"
echo "    python main.py --phase content   # generate today's video"
echo "    python main.py --phase studio    # YouTube Studio maintenance"
echo "    python main.py --phase update    # self-healing update cycle"
echo "    python main.py --all             # full daily pipeline"
echo "    python main.py --schedule        # start the cron scheduler"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
