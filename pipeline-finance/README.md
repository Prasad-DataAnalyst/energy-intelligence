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
├── scrapers/        Market data, economic indicators, earnings
│   ├── market_scraper.py    yfinance — prices, movers, sector performance
│   ├── economic_scraper.py  FRED API — CPI, GDP, unemployment, Fed rate
│   ├── earnings_scraper.py  SEC EDGAR + Finnhub — EPS beats/misses
│   └── topic_library.py     Sunday educational topic bank
├── generators/      AI-powered content generation
│   ├── script_gen.py        Claude API — full scripts with [SECTION] markers
│   ├── compliance_filter.py Rule engine + Claude semantic review + auto-fix
│   ├── chart_generator.py   matplotlib/mplfinance — branded PNG charts
│   ├── audio_gen.py         edge-tts primary / gTTS fallback, 120–180s range
│   ├── thumbnail_gen.py     Pillow — 1280×720 branded JPEG thumbnails
│   └── title_gen.py         Claude API — scored titles, descriptions, tags
├── builders/        Video assembly (MoviePy/ffmpeg)
│   ├── video_builder.py     Main video — Ken Burns, captions, watermark
│   └── shorts_builder.py    YouTube Shorts — cards, ≤60s hard limit
├── uploader/        YouTube Data API v3
│   ├── uploader.py          OAuth2 upload, 30-min gap, failed queue
│   └── quota_tracker.py     10,000 unit/day tracking, reset, alerts
├── scheduler/       APScheduler — automated daily runs
│   ├── weekday_scheduler.py Mon–Fri market pipelines + NYSE holiday check
│   ├── sunday_scheduler.py  4-theme educational cycle
│   └── master_scheduler.py  Process-isolated runners + 30-min heartbeat
├── monitor/         Health checks, KPI tracking, alerts
│   └── monitor.py           Pipeline health, quota, API status, email alerts
├── tests/           pytest test suite — 206 tests, all passing
├── assets/          Fonts, watermarks, music library
├── logs/            Quota log, heartbeat log, daily JSONL summaries
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

| Day       | Time (ET)  | Action                                          |
|-----------|------------|-------------------------------------------------|
| Mon–Fri   | 6:00 AM    | Scrape + generate script + build video          |
| Mon–Fri   | 7:00 AM    | Upload main morning briefing                    |
| Mon–Fri   | 12:30 PM   | Upload midday Short                             |
| Mon–Fri   | 4:30 PM    | Upload afternoon market wrap video              |
| Sunday    | 10:00 AM   | Upload Sunday educational deep-dive             |
| Sunday    | 4:00 PM    | Upload Sunday afternoon Short                   |
| Every 30m | —          | Heartbeat log + health checks                   |

Market-day check respects NYSE observed holidays (New Year's Day, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas).

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
| `audio_gen.py` | edge-tts → gTTS fallback | `AudioTrack` — 120–180s validated MP3; weekday voice rotation (Mon=Guy, Tue=Christopher…) |
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
# Run all 206 tests
pytest tests/ -v

# With coverage report
pytest tests/ -v --cov=. --cov-report=term-missing

# Single module
pytest tests/test_chart_generator.py -v
```

| Test file | Tests | Module |
|-----------|-------|--------|
| `test_market_scraper.py` | 12 | market_scraper |
| `test_economic_scraper.py` | 12 | economic_scraper |
| `test_earnings_scraper.py` | 12 | earnings_scraper |
| `test_script_gen.py` | 15 | script_gen |
| `test_compliance.py` | 17 | compliance_filter |
| `test_chart_generator.py` | 8 | ChartGenerator class |
| `test_audio_gen.py` | 10 | AudioGenerator class |
| `test_title_gen.py` | 11 | TitleGenerator class |
| `test_thumbnail_gen.py` | 10 | ThumbnailGenerator class |
| `test_builders.py` | 18 | VideoBuilder + ShortsBuilder |
| `test_scheduler.py` | 18 | WeekdayScheduler + SundayScheduler |
| `test_monitor.py` | 13 | PipelineMonitor + ChannelMonitor |
| `test_youtube_uploader.py` | 20 | YouTubeUploader + QuotaTracker |
| **Total** | **206** | All passing, no live API calls |

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
