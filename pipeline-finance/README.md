# DriftWire326 — YouTube Finance Automation Pipeline

> Fully automated YouTube finance channel pipeline for **@DriftWire326**.  
> Niche: US stock market news, economic data, financial education.  
> Audience: Gen Z + Millennial investors.

---

## Architecture

```
pipeline-finance/
├── config/          Settings, prompts, OAuth credentials
│   ├── settings.py          Pydantic settings (loads from .env)
│   ├── prompts.py           All Claude prompt templates
│   └── finance_oauth.json   YouTube OAuth2 credential placeholder
├── scrapers/        Market data, economic indicators, earnings, news
│   ├── market_scraper.py    yfinance — prices, movers, sectors, VIX pre-check
│   ├── economic_scraper.py  FRED API — CPI, GDP, unemployment, Fed rate
│   ├── earnings_scraper.py  SEC EDGAR + Finnhub — EPS beats/misses
│   ├── trends_scraper.py    Google Trends via pytrends (free, no key)
│   ├── rss_scraper.py       Financial news RSS via feedparser (free, no key)
│   └── topic_library.py     Sunday educational topic bank
├── generators/      AI-powered content generation
│   ├── script_gen.py        Claude API — full scripts with [SECTION] markers
│   ├── compliance_filter.py Rule engine + Claude semantic review + auto-fix
│   ├── chart_generator.py   matplotlib/mplfinance — branded PNG charts
│   ├── audio_gen.py         edge-tts + SSML / gTTS fallback, 120–180s range
│   ├── thumbnail_gen.py     Pillow — 1280×720 branded JPEG thumbnails
│   └── title_gen.py         Claude API — scored titles, descriptions, tags
├── builders/        Video assembly (MoviePy/ffmpeg)
│   ├── video_builder.py     Main video — Ken Burns, captions, watermark
│   ├── shorts_builder.py    YouTube Shorts — cards, ≤60s hard limit
│   └── fallback_builder.py  Emergency video — cached data + gTTS + static frame
├── uploader/        YouTube Data API v3
│   ├── uploader.py          OAuth2 upload, 30-min gap, failed queue, upload manifest
│   ├── quota_tracker.py     10,000 unit/day tracking, reset, alerts
│   └── preflight.py         Pre-upload gate — video/audio/thumbnail/quota/compliance
├── scheduler/       APScheduler — automated daily runs + reliability layer
│   ├── weekday_scheduler.py Mon–Fri pipeline (checkpointed, preflight-gated)
│   ├── sunday_scheduler.py  4-theme educational cycle (dedup + script cache)
│   ├── master_scheduler.py  12 scheduled jobs, process isolation, heartbeat
│   ├── pipeline_state.py    Checkpoint/resume — crashed runs resume mid-pipeline
│   ├── post_upload.py       Post-upload chain: playlist, captions, pin, manifest
│   └── deadman.py           18:00 ET dead-man switch + checkpoint retry job
├── monitor/         Health checks, KPI tracking, alerts
│   └── monitor.py           Pipeline health, quota, API status, email alerts
├── channel_manager/ Channel-level automation (Modules 21–32)
│   ├── playlist_manager.py  Auto-creates & routes videos to 6 playlists
│   ├── community_poster.py  Weekly watchlist post Sunday 09:00 ET (Claude-generated)
│   ├── analytics_tracker.py YouTube Analytics daily pull + weekly report + CTR A/B swap
│   ├── comment_monitor.py   Daily comment pull, classify, reply, spam flagging
│   ├── end_screen_manager.py Subscribe + best-video end screens (best-effort API)
│   ├── post_manager.py      Pinned disclaimer comment + channel description refresh
│   ├── subtitle_manager.py  SRT caption generation + upload (400 units/video)
│   ├── performance_tracker.py EMA-weighted best style/hook/template/time slot
│   └── content_tracker.py   14-day topic dedup window (Jaccard similarity)
├── deploy/
│   └── driftwire326.service systemd unit — auto-restart scheduler daemon
├── tests/           pytest test suite — 365 tests, all passing
├── assets/
│   └── templates/           Thumbnail template PNGs (A–G, place before first run)
├── logs/
│   ├── analytics/           Daily analytics JSON + weekly reports
│   ├── comments/            Daily classified comments with suggested replies
│   └── community_posts/     Generated community post text + publish records
├── output/          Generated videos, audio, thumbnails, charts
└── main.py          CLI entry point
```

## Pipeline Flow

```
[Scrape]    Market (yfinance) + Earnings (SEC EDGAR/Finnhub) + Economic (FRED)
               ↓
[Generate]  Claude AI Script → Compliance Filter → Auto-Fix
               ↓
[Create]    Charts (matplotlib) + Audio (edge-tts, 120–180s) + Thumbnail (Pillow)
               ↓            + Titles & Tags (Claude AI, promise-checked)
[Build]     Main Video (MoviePy/ffmpeg 1920×1080 30fps) + Short (1080×1920 ≤60s)
               ↓
[Upload]    YouTube Data API v3 → Set Thumbnail → Record Quota Usage
               ↓
[Monitor]   Health checks every 30 min → Alerts → Daily JSONL summary
```

---

## Setup

> **Deploying to Google Cloud?** Follow **`deploy/GCP_SETUP.md`** — a
> zero-GCP-cost walkthrough (always-free e2-micro VM) covering the Cloud
> project, OAuth credentials (`deploy/oauth_bootstrap.py`), VM bootstrap
> (`deploy/vm_setup.sh`), verification runs, and go-live. The steps below
> are the generic local-machine setup.

### 1. Prerequisites

```bash
# Python 3.11+
python --version

# ffmpeg (required for video building)
sudo apt install ffmpeg         # Ubuntu/Debian
brew install ffmpeg             # macOS

# System font for thumbnails (optional — DejaVu is bundled)
sudo apt install fonts-dejavu   # Ubuntu/Debian
```

### 2. Install Dependencies

```bash
cd pipeline-finance
pip install -r requirements.txt

# Additional audio libraries (required for audio duration + fallback TTS)
pip install mutagen gtts edge-tts
```

### 3. Configure Environment

```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
# Required
ANTHROPIC_API_KEY=sk-ant-...

# Required for earnings data
FINNHUB_API_KEY=your_finnhub_key

# Optional — falls back to public FRED endpoints
FRED_API_KEY=your_fred_key

# Optional — for email alerts
ALERT_EMAIL_FROM=alerts@yourdomain.com
ALERT_EMAIL_TO=you@yourdomain.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_PASSWORD=your_app_password
```

**Security rules — never break these:**
- `.env` must never be committed to git (it's in `.gitignore`)
- `config/finance_oauth.json` is a placeholder — real credentials handled at runtime
- `MIN_QUOTA_TO_UPLOAD = 1700` is enforced before every upload
- All scripts must pass the compliance gate before any upload

### 4. YouTube API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **YouTube Data API v3**
3. Create OAuth 2.0 credentials → Application type: **Desktop App**
4. Download the JSON → save as `config/finance_oauth.json`
5. First run opens a browser for one-time authorization; token is cached automatically

### 5. Run the Pipeline

```bash
# Check quota status
python main.py --quota

# Run full weekday pipeline once (scrape → build → upload)
python main.py --mode full

# Individual stages
python main.py --mode scrape              # scrape only
python main.py --mode build --topic AAPL  # build only for a topic
python main.py --mode upload              # upload from output/ queue

# Sunday educational pipeline
python main.py --mode sunday

# Dry run (no upload)
python main.py --run weekday --dry-run
python main.py --run sunday  --dry-run

# Start the full 24/7 scheduler
python main.py

# Run tests
python main.py --mode test
# or directly:
pytest tests/ -v
```

---

## Schedule

| Day       | Time (ET)  | Action                                            |
|-----------|------------|---------------------------------------------------|
| Mon–Fri   | 8:00 AM    | Pre-market long video (scrape → script → build → upload) |
| Mon–Fri   | 8:45 AM    | Pipeline retry (resumes from checkpoint if incomplete) |
| Mon–Fri   | 12:30 PM   | **Day-themed Short** (see rotation below)         |
| Mon–Fri   | 5:15 PM    | Post-market long video                            |
| Mon–Fri   | 5:45 PM    | Pipeline retry (checkpoint resume)                |
| Saturday  | 11:00 AM   | Evergreen educational Short                       |
| Sunday    | 9:00 AM    | Weekly community post (watchlist)                 |
| Sunday    | 11:00 AM   | Educational deep-dive long video (4-theme cycle)  |
| Sunday    | 11:45 AM   | Pipeline retry (checkpoint resume)                |
| Monday    | 7:30 AM    | Channel description refresh                       |
| Daily     | 6:00 PM    | **Dead-man switch** — email alert if no upload today |
| Daily     | 8:00 PM    | Comment monitor (classify, reply drafts, spam flags) |
| Daily     | 9:30 PM    | Analytics pull (views, CTR, watch time)           |
| Every 2h  | —          | Channel performance monitor                       |
| Every 30m | —          | Heartbeat log                                     |

**Weekly output: 11 long-form videos (max 4:00, 420–500 word scripts) + 6 Shorts.**

### Short theme rotation (12:30 PM Mon–Fri, 11:00 AM Sat)

| Day | Format |
|-----|--------|
| Monday | Three Stocks to Watch (live top movers) |
| Tuesday | Market News Explained (top RSS story — skipped if no meaningful story) |
| Wednesday | Economic Report Explained (rotating: CPI, GDP, payrolls, yields…) |
| Thursday | Personal Finance Tip (rotating: emergency funds, credit, DCA…) |
| Friday | Week in 60 Seconds (weekly index performance) |
| Saturday | Finance Explained Simply (evergreen educational) |

Every description carries a data-cutoff timestamp and sources list.

Market-day check respects NYSE observed holidays (New Year's Day, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas).

---

## Reliability Layer (error-free daily output)

Five mechanisms keep the channel publishing even when something breaks:

1. **API health gate** — before spending anything, each run verifies Claude and
   yfinance respond (one retry after 5 min). Failures abort cleanly with the
   checkpoint preserved.
2. **Checkpoint/resume** (`scheduler/pipeline_state.py`) — every step records
   completion + artifacts to `logs/pipeline_state/`. A crashed run resumed by
   the retry job skips completed steps — the Claude script call is never repeated.
3. **Preflight gate** (`uploader/preflight.py`) — video streams (ffprobe), audio
   duration/silence (pydub), thumbnail, title, compliance phrases, and quota are
   all verified *before* the 1,600-unit upload is attempted.
4. **Dead-man switch** (`scheduler/deadman.py`) — at 18:00 ET, if no upload is
   recorded in `logs/upload_manifest.jsonl`, an email alert fires with pipeline
   diagnostics. Silence never means failure.
5. **Emergency fallback video** (`builders/fallback_builder.py`) — a minimal
   publishable video (cached market data → gTTS → static branded frame → ffmpeg)
   that needs neither Claude nor live market data. Set `FALLBACK_AUTO_UPLOAD=true`
   in `.env` to let the dead-man switch publish it automatically as a last resort.

Deploy as a self-restarting daemon with `deploy/driftwire326.service`
(systemd — `Restart=on-failure`).

---

## Module Reference

### Config (Modules 1–2)

| File | Purpose |
|------|---------|
| `config/settings.py` | Pydantic `Settings` — all paths, keys, brand constants, disclaimer text |
| `config/prompts.py` | All Claude prompt templates for script, title, compliance |

### Scrapers (Modules 3–4)

| Module | Source | Data |
|--------|--------|------|
| `market_scraper.py` | yfinance | Prices, % moves, top movers, sector heatmap; tier1 ≥5%, tier2 ≥2% |
| `economic_scraper.py` | FRED REST API | CPI, GDP, unemployment rate, Fed funds rate |
| `earnings_scraper.py` | SEC EDGAR + Finnhub | EPS beats/misses, upcoming reports, 8-K filings |
| `topic_library.py` | Static bank | 50+ Sunday educational topics in 4 theme categories |

### Generators (Modules 5–9)

| Module | Tech | Output |
|--------|------|--------|
| `script_gen.py` | Claude `claude-sonnet-4-6` | Weekday/Sunday scripts with `[HOOK]`, `[DATA]`, `[ANALYSIS]`, `[CLOSE]` markers |
| `compliance_filter.py` | Regex + Claude | ComplianceReport with auto-fixed script; blocks high-risk content |
| `chart_generator.py` | matplotlib + mplfinance | `ChartFile` — price_line, candlestick, animated MP4, economic_bar, 4-panel indices |
| `audio_gen.py` | edge-tts + SSML → gTTS fallback | `AudioTrack` — 120–180s validated MP3; weekday voice rotation; SSML markup adds `<break>` after numbers, `<emphasis>` on %, `<prosody rate="slow">` on stat-heavy sentences |
| `thumbnail_gen.py` | Pillow | `ThumbnailFile` — 1280×720 JPEG ≤2MB; weekday + Sunday templates; 15% opacity watermark |
| `title_gen.py` | Claude API | `TitleSet` with 10 scored options; description auto-appends disclaimer + AI disclosure |

### Builders (Modules 10–11)

| Module | Tech | Output |
|--------|------|--------|
| `video_builder.py` | MoviePy + ffmpeg | `BuiltVideo` — 1920×1080 H.264 30fps AAC; Ken Burns, 52px captions, lower-third, 8% music mix |
| `shorts_builder.py` | MoviePy + ffmpeg | `BuiltShort` — 1080×1920 ≤60s; card-style text overlay, 15% music, 7-day no-repeat music rotation |

### Uploader (Modules 12–13)

| Module | Purpose |
|--------|---------|
| `uploader.py` | `YouTubeUploader` — OAuth2 resumable upload; category 25 (News & Politics); auto-appends `#Shorts`; 30-min gap between uploads; failed queue to `logs/failed_queue.json` |
| `quota_tracker.py` | `QuotaTracker` — 10,000 unit/day limit; per-operation cost table; `alert_low_quota(threshold=2000)`; persists to `logs/quota_tracker.json` |

### Scheduler (Modules 14–16)

| Module | Purpose |
|--------|---------|
| `weekday_scheduler.py` | Mon–Fri pipelines; `is_market_day()` with NYSE holiday list; morning/midday/afternoon slots |
| `sunday_scheduler.py` | 4-week theme cycle (investment_banking → insurance → savings → rotating_bonus); `get_sunday_topic()` |
| `master_scheduler.py` | APScheduler cron; each pipeline run in isolated child process with 2-hour hard timeout; 30-min heartbeat to `logs/heartbeat.log` |

### Channel Manager (Modules 21–24)

| Module | Purpose |
|--------|---------|
| `playlist_manager.py` | Auto-creates 6 canonical playlists on first run; caches IDs to `logs/playlist_ids.json`; `route_video_to_playlist(video_id, video_type, sunday_theme)` adds each video to the correct playlist automatically |
| `community_poster.py` | Every Sunday 09:00 ET — Claude generates a weekly watchlist post from market data; attempts YouTube API post (Partner Program required); saves to `logs/community_posts/` for manual publish if API unavailable |
| `analytics_tracker.py` | Daily analytics pull (YouTube Analytics API v2 — separate OAuth token `config/analytics_token.json`); weekly report every Monday; `flag_low_ctr_videos(threshold=0.02)` + `swap_title_ab(video_id, new_title)` for underperforming videos |
| `comment_monitor.py` | Daily at 20:00 ET — fetches comments, classifies via regex + Claude, generates replies, auto-flags spam via `comments.setModerationStatus`; financial advice requests always get the legal disclaimer appended |

**6 Managed Playlists:**

| Playlist | Routes from |
|----------|-------------|
| Daily Market Recaps | `video_type="weekday"` |
| Market Shorts | `video_type="shorts"` |
| Sunday: Investing 101 | `sunday_theme="investment_banking"` |
| Sunday: Insurance | `sunday_theme="insurance_protection"` |
| Sunday: Savings & Wealth | `sunday_theme="savings_wealth"` |
| Sunday: Special Topics | `sunday_theme="rotating_bonus"` |

**Thumbnail Templates (assets/templates/):**

Place PNG files named `template_a.png` through `template_g.png` (1280×720) in `assets/templates/` before the first run. These are the Canva-designed base templates. `thumbnail_gen.py` uses them as backgrounds when present; falls back to Pillow-generated templates otherwise. Suggested template variations:
- A: Red/black breaking news (tier1 crash)
- B: Green/black bullish breakout (tier1 surge)
- C: Dark neutral with data (tier2 daily recap)
- D: Purple educational (Sunday Investing 101)
- E: Blue trustworthy (Sunday Insurance)
- F: Gold wealth (Sunday Savings & Wealth)
- G: Teal special topics (Sunday rotating)

### Monitor (Module 17)

`monitor.py` — `PipelineMonitor` class:
- `check_pipeline_health()` — aggregates all checks into a health dict
- `check_quota_status()` — alerts when remaining < 2,000 units
- `check_api_status()` — liveness ping of Anthropic API + yfinance
- `check_last_upload_success()` — reads upload log for recent success
- `log_daily_summary()` — appends to `logs/pipeline_daily.jsonl`
- `alert(message, level)` — email + webhook (Slack/Discord)

### CLI (Module 18)

`main.py` — argument reference:

```
--mode {full,scrape,build,upload,test,sunday}   Pipeline stage to run
--date YYYY-MM-DD                                Override run date
--topic TOPIC                                    Override scraped topic
--run {weekday,sunday}                           Legacy: run once
--dry-run                                        Skip upload step
--quota                                          Show quota status and exit
--test                                           Run pytest suite and exit
```

---

## YouTube API Quota

The free tier provides **10,000 units/day**.

| Operation | Cost (units) |
|-----------|-------------|
| `videos.insert` (upload) | 1,600 |
| `thumbnails.set` | 50 |
| `videos.list` | 1 |
| `videos.update` | 50 |

The pipeline enforces `MIN_QUOTA_TO_UPLOAD = 1700` before every upload. With 3 uploads/day (morning main + Short + afternoon main), daily usage is ~3,350 units, well within the 10,000 limit.

---

## Compliance Architecture

All scripts pass through a mandatory two-stage gate before any video is built:

1. **Rule Engine** — regex blocks guaranteed-return claims, risk-free language, direct buy/sell advice
2. **Claude AI Semantic Review** — catches subtle compliance issues the rules miss
3. **Auto-Fix** — non-critical issues are corrected in-place; script re-evaluated
4. **Hard Block** — high-risk scripts are rejected and never uploaded

Every video description automatically includes:
- `settings.disclaimer_text` (standard financial disclaimer)
- `"Narration is AI-generated."` (FTC AI disclosure)

---

## Brand Guidelines

| Element | Value |
|---------|-------|
| Channel handle | `@DriftWire326` |
| Primary color | `#FF0033` (YouTube red) |
| Background | `#0A0A0F` (near black) |
| Accent | `#FFD700` (gold) |
| Success green | `#00CC66` |
| Danger red | `#FF3333` |
| Fonts | Impact (headlines), DejaVu Sans (body) |
| Main video | 1920×1080 @ 30fps, H.264, 8 Mbps, AAC 192k |
| Shorts | 1080×1920 @ 30fps, H.264, ≤60s hard limit |
| Thumbnail | 1280×720 JPEG, ≤2MB, auto-recompressed if over |
| Audio | 120–180s validated range; silence padding if short |

---

## Testing

```bash
# Run all 365 tests
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=. --cov-report=term-missing

# Single module
pytest tests/test_chart_generator.py -v
```

| Test file | Tests | Module |
|-----------|-------|--------|
| `test_scraper.py` | 43 | market/economic/earnings scrapers |
| `test_script_gen.py` | 19 | script_gen |
| `test_compliance.py` | 11 | compliance_filter |
| `test_chart_generator.py` | 8 | ChartGenerator class |
| `test_audio_gen.py` | 10 | AudioGenerator class |
| `test_title_gen.py` | 11 | TitleGenerator class |
| `test_thumbnail_gen.py` | 10 | ThumbnailGenerator class |
| `test_builders.py` | 21 | VideoBuilder + ShortsBuilder |
| `test_video_builder.py` | 6 | video assembly details |
| `test_scheduler.py` | 19 | WeekdayScheduler + SundayScheduler |
| `test_monitor.py` | 14 | PipelineMonitor + ChannelMonitor |
| `test_uploader.py` | 15 | upload config + queue |
| `test_youtube_uploader.py` | 19 | YouTubeUploader + QuotaTracker |
| `test_channel_manager.py` | 40 | PlaylistManager + CommunityPoster + AnalyticsTracker + CommentMonitor |
| `test_enhancements.py` | 76 | Modules 25–32 (end screens, captions, trends, RSS, preflight, trackers) |
| `test_reliability.py` | 43 | Checkpoints, post-upload chain, dead-man switch, fallback builder |
| **Total** | **365** | All passing, no live API calls |

All tests run without API keys using `unittest.mock` patches.

---

## Directory Outputs

| Path | Contents |
|------|----------|
| `output/weekday/` | Built main videos per date |
| `output/shorts/` | Built Shorts per date |
| `output/charts/` | PNG charts organized by ticker/date |
| `output/audio/` | MP3 segments per pipeline run |
| `output/thumbnails/` | JPEG thumbnails per video |
| `logs/quota_tracker.json` | Daily quota state (auto-reset) |
| `logs/heartbeat.log` | 30-min pipeline heartbeat |
| `logs/pipeline_daily.jsonl` | Per-run JSON summaries |
| `logs/failed_queue.json` | Uploads queued for retry |

---

## Legal

All generated content includes mandatory financial disclaimers per FTC and SEC guidance.  
This pipeline is for educational content creation only.  
Nothing produced constitutes financial advice.  
Always review AI-generated scripts before publishing.
