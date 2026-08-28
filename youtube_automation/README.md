# GetMindFuelNow — Hollywood-Grade YouTube Automation System

**Owner:** Prasad Selvaraj (prasad2t@gmail.com)
**Channel:** GetMindFuelNow
**Repository:** prasad-dataanalyst/energy-intelligence
**Registered:** 2026-06-09

---

## What This System Does Every Day (Fully Automatic)

At **15:00 UTC every day**, the daemon wakes up and completes an 8-stage pipeline — no human action needed:

```
15:00 UTC
  │
  ├── Stage 1 — TREND INTELLIGENCE  (finds today's viral topic)
  ├── Stage 2 — CINEMATIC SCRIPT    (writes a 480-second screenplay)
  ├── Stage 3 — BROADCAST AUDIO     (records voiceover + music)
  ├── Stage 4 — HOLLYWOOD VISUALS   (generates 1080p 24fps video)
  ├── Stage 5 — COLOR GRADE         (cinematic color grading)
  ├── Stage 6 — QUALITY CHECK       (must score ≥50/100 to upload)
  ├── Stage 7 — YOUTUBE UPLOAD      (publishes to GetMindFuelNow)
  │
  ├── +5  min → pins a comment on the video
  ├── +60 min → posts to YouTube Community tab
  │
  └── Continuously generates YouTube Shorts from the same video
```

---

## Daily Output Per Video

| Deliverable | Detail |
|---|---|
| Main video | 1080p 24fps, ~8 minutes, Hollywood color grade |
| Thumbnail | 1280×720, AI-generated, uploaded automatically |
| YouTube Shorts | Up to 5 clips (≤60s, 9:16 portrait crop) |
| Pinned comment | Posted automatically 5 minutes after upload |
| Community post | Posted automatically 60 minutes after upload |
| SEO package | Optimized title, description, tags, chapters |

---

## Architecture — All Modules

### Entry Point
```
start_scheduler.sh
  └── core/automation_daemon.py        ← persistent process, runs 24/7
        └── core/cinematic_daily_pipeline.py  ← fires at 15:00 UTC
```

### Stage 1 — Trend Intelligence
**`modules/intelligence/trend_analyzer.py`**
- Scans 15+ live sources: Google Trends, Reddit, HackerNews, Twitter/X, YouTube Trending
- Falls back to 50+ adaptive sources if primary sources fail
- Scores each topic: virality × RPM × emotional impact × search volume
- Picks the single best opportunity for today

### Stage 2 — Cinematic Script Writer
**`modules/production/cinematic_script_writer.py`**
- Writes a 480-second screenplay using a psychological engagement formula
- Structure: Cold Open (8s) → Hook (12s) → Promise (25s) → 4 Value Loops (75s each) → Climax (120s) → CTA (60s)
- Scores: curiosity×12 + fear×10 + desire×10 + open_loops×8 + social_proof×6 + pattern_interrupts×8
- Minimum quality score: 70/100 to proceed

### Stage 3 — Broadcast Audio Engine
**`modules/production/cinematic_audio_engine.py`**
- Voice generation (5-tier fallback):
  1. ElevenLabs eleven_turbo_v2_5 (Hollywood quality)
  2. OpenAI tts-1-hd (professional)
  3. edge-tts (free, good quality)
  4. espeak-ng (offline fallback)
  5. Silent WAV (last resort — video still produced)
- Broadcast chain: HP 80Hz → warmth +2dB@200Hz → presence +3dB@3kHz → de-essing -6dB@6.5kHz → compression 3:1 → loudness -14 LUFS
- Background music mixed at -18dB under voice

### Stage 4 — Hollywood Visual Engine
**`modules/production/hollywood_visual_engine.py`**
- Visual generation (4-tier fallback):
  1. Runway ML Gen-3 (AI cinematic video)
  2. SDXL via Replicate + Ken Burns parallax (6 directions, 12% oversample)
  3. Pexels stock footage (colour-graded to style)
  4. PIL-generated scenes (always works, zero cost)
- Handheld camera shake: 1.5px Gaussian RMS via cv2.warpAffine
- Output: 1920×1080, 24fps

### Stage 5 — Cinematic Editor
**`modules/production/cinematic_editor.py`**
- 9 Hollywood transitions: hard_cut, cross_dissolve, zoom_punch, whip_pan, glitch, light_leak, smash_cut, j_cut, l_cut
- Word-highlight captions burned via Whisper ASR → ASS subtitles
- Stat overlays + source lower-thirds
- Color grades: tech_dark, documentary, education

### Stage 6 — Quality Gate
**`modules/evolution/quality_comparator.py`**
- Scores every video: technical + content + psychological + visual diversity + emotional arc
- Minimum 50/100 to upload — below this, video is held for manual review
- Compares against previous video to track improvement over time

### Stage 7 — YouTube Upload
**`youtube_uploader.py`**
- Resumable upload (5 MB chunks) — survives connection drops
- Injects: optimized title, description with chapter markers, up to 500 tags
- Retry queue: if YouTube quota exceeded, retries after next UTC midnight (max 3 attempts)

### Stage 8 — Post-Upload Automation
**`modules/youtube/thumbnail_generator.py`**
- Generates thumbnail via SDXL (Replicate) → PIL gradient fallback
- Uploads to video automatically

**`modules/youtube/shorts_publisher.py`**
- Extracts up to 5 highlight clips from main video
- Crops each to 9:16 portrait (1080×1920)
- Uploads each as a YouTube Short with #Shorts tag

**`modules/youtube/post_scheduler.py`**
- Pins a CTA comment 5 minutes after upload
- Posts to YouTube Community tab 60 minutes after upload
- All actions persisted to JSON — survives daemon restarts

---

## Self-Healing & Self-Improvement (Runs 24/7 in Background)

### API Key Manager — `services/api_key_manager.py`
- Manages 14+ services
- Auto-quarantines failed keys for 24h, then re-tests
- Scans environment for new keys automatically

### Source Registry — `services/source_registry.py`
- 50+ content sources across: trending, news, facts, images, video, music
- Each source has an EMA reliability score, updated after every use
- Discovers new RSS feeds and public APIs on its own

### Adaptive Fetcher — `services/adaptive_fetcher.py`
- Always tries the highest-reliability source first
- Falls through the chain on any failure
- 10-minute in-process cache to reduce API calls

### Self-Enhancement Loop — `modules/intelligence/self_enhancement_loop.py`
- Background daemon thread, never stops
- Every 30 minutes: health check all sources
- Every 6 hours: discover new content sources
- Every 24 hours: performance report
- Correlates source choices with video quality scores to improve over time

---

## Full File Map

```
youtube_automation/
├── start_scheduler.sh                  ← launch point (watchdog with backoff restart)
├── config.py                           ← all settings and env vars
├── youtube_uploader.py                 ← YouTube upload + thumbnail + comments + retry queue
├── first_time_setup.py                 ← OAuth setup (run once only)
│
├── core/
│   ├── automation_daemon.py            ← 24/7 daemon, 15:00 UTC daily trigger
│   └── cinematic_daily_pipeline.py     ← full 8-stage pipeline
│
├── modules/
│   ├── intelligence/
│   │   ├── trend_analyzer.py           ← viral topic discovery (15+ sources)
│   │   └── self_enhancement_loop.py    ← 24/7 background self-improvement
│   ├── production/
│   │   ├── cinematic_script_writer.py  ← 480s psychological screenplay
│   │   ├── cinematic_audio_engine.py   ← broadcast-quality voice + music
│   │   ├── hollywood_visual_engine.py  ← 1080p 24fps AI video generation
│   │   └── cinematic_editor.py         ← 9 transitions + captions + color grade
│   ├── evolution/
│   │   └── quality_comparator.py       ← 50/100 quality gate + comparison
│   └── youtube/
│       ├── youtube_packager.py         ← SEO title / tags / description / chapters
│       ├── thumbnail_generator.py      ← AI thumbnail generation + upload
│       ├── shorts_publisher.py         ← Shorts extraction + upload (up to 5)
│       └── post_scheduler.py           ← scheduled community post + pin comment
│
└── services/
    ├── api_key_manager.py              ← key health tracking + quarantine + rotation
    ├── source_registry.py              ← 50+ sources + EMA reliability scores
    ├── adaptive_fetcher.py             ← cascading fallback across all sources
    ├── elevenlabs_service.py
    ├── openai_tts_service.py
    ├── replicate_service.py
    ├── runway_service.py
    ├── pexels_service.py
    └── pixabay_service.py
```

---

## How to Start

```bash
# First time — authenticate YouTube OAuth (run once)
python first_time_setup.py

# Start 24/7 daemon in foreground
bash start_scheduler.sh

# Start in background (survives terminal close)
nohup bash start_scheduler.sh &

# Run one video right now (production)
python core/cinematic_daily_pipeline.py --now

# Dry run — generate but don't upload
python core/cinematic_daily_pipeline.py --now --no-upload

# Test a specific topic
python core/cinematic_daily_pipeline.py --test --topic "AI just changed everything"
```

---

## Monitoring

| File | What it shows |
|---|---|
| `logs/daemon_heartbeat.txt` | Updated every 60s — stale = daemon hung |
| `logs/daemon_status.json` | Status, last run date, next run time |
| `logs/daemon.log` | Full daemon log |
| `logs/cinematic_YYYY-MM-DD.log` | Per-day pipeline log |
| `output/YYYY-MM-DD/production_log.json` | Stage-by-stage report per video |
| `output/YYYY-MM-DD/HELD_FOR_REVIEW.txt` | Appears if quality score < 50 |

---

## API Keys (set in .env)

| Service | Variable | Used For |
|---|---|---|
| ElevenLabs | `ELEVENLABS_API_KEY` | Best quality voice |
| OpenAI | `OPENAI_API_KEY` | Voice fallback + script AI |
| Replicate | `REPLICATE_API_TOKEN` | SDXL visuals + thumbnail |
| Runway ML | `RUNWAY_API_KEY` | Top-tier AI video generation |
| Pexels | `PEXELS_API_KEY` | Stock footage fallback |
| Pixabay | `PIXABAY_API_KEY` | Stock image fallback |
| YouTube OAuth | `youtube_token.json` | Upload + all channel actions |

The system works with any subset — missing keys are quarantined and the system falls back automatically.

---

*All rights reserved. Prasad Selvaraj © 2026.*
