# DriftWire326 — YouTube Finance Automation Pipeline

> Fully automated YouTube finance channel pipeline for **@DriftWire326**.  
> Niche: US stock market news, economic data, financial education.  
> Audience: Gen Z + Millennial investors.

---

## Architecture

```
pipeline-finance/
├── config/          Settings, prompts, OAuth credentials
├── scrapers/        Market data (yfinance), economic (FRED), earnings, topic library
├── generators/      AI script writer, compliance filter, charts, audio, thumbnails, titles
├── builders/        Video assembler (MoviePy/ffmpeg), Shorts builder
├── uploader/        YouTube Data API v3 upload + quota tracker
├── scheduler/       APScheduler — weekday (8AM/5PM ET) + Sunday (11AM ET)
├── monitor/         Analytics monitoring + email/webhook alerts
├── tests/           pytest test suite
└── main.py          CLI entry point
```

## Pipeline Flow

```
[Scrape] Market + Earnings + Economic Data
         ↓
[Generate] Claude AI Script → Compliance Filter → Auto-Fix
         ↓
[Create] Charts (matplotlib) + Audio (ElevenLabs/pyttsx3)
         ↓        + Thumbnail (Pillow) + Titles (Claude AI)
[Build] Main Video (MoviePy/ffmpeg 1920×1080) + Short (1080×1920)
         ↓
[Upload] YouTube Data API v3 → Schedule Publish → Set Thumbnail
         ↓
[Monitor] Hourly KPI checks → Milestone alerts → Email notifications
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
```

### 2. Install Dependencies

```bash
cd pipeline-finance
pip install -r requirements.txt
```

### 3. Configure Environment

```bash
cp .env.example .env
# Edit .env with your API keys:
# - ANTHROPIC_API_KEY (required)
# - ELEVENLABS_API_KEY (optional — falls back to pyttsx3)
# - FRED_API_KEY (optional — falls back to mock data)
# - YouTube OAuth setup (see below)
```

### 4. YouTube API Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com)
2. Create a project → Enable **YouTube Data API v3**
3. Create OAuth 2.0 credentials (Desktop App type)
4. Download JSON → save as `config/finance_oauth.json`
5. First run will open a browser for authorization

### 5. Run the Pipeline

```bash
# Check quota status
python main.py --quota

# Dry run (scrape + generate only, no upload)
python main.py --dry-run weekday

# Run weekday pipeline once
python main.py --run weekday

# Run Sunday educational pipeline
python main.py --run sunday

# Start the full scheduler (runs 24/7)
python main.py

# Run tests
python main.py --test
# or directly:
pytest tests/ -v
```

---

## Schedule

| Day       | Time (ET) | Content                              |
|-----------|-----------|--------------------------------------|
| Mon–Fri   | 8:00 AM   | Pre-market briefing (5-7 min)        |
| Mon–Fri   | 5:00 PM   | Post-market full recap (8-10 min)    |
| Daily     | Continuous| YouTube Shorts from key moments      |
| Sunday    | 11:00 AM  | Educational deep-dive (10-12 min)    |

---

## Module Reference

### Scrapers
| Module | Source | Data |
|--------|--------|------|
| `market_scraper.py` | yfinance | Prices, movers, sector performance |
| `economic_scraper.py` | FRED API | CPI, GDP, unemployment, Fed rate |
| `earnings_scraper.py` | yfinance | EPS beats/misses, upcoming reports |

### Generators
| Module | Tech | Output |
|--------|------|--------|
| `script_gen.py` | Claude API | Full scripts with section markers |
| `compliance_filter.py` | Rules + Claude | Compliance report + auto-fixes |
| `chart_generator.py` | matplotlib + mplfinance | PNG chart images |
| `audio_gen.py` | ElevenLabs / pyttsx3 | MP3 audio per segment |
| `thumbnail_gen.py` | Pillow | 1280×720 branded JPG |
| `title_gen.py` | Claude API | 10 scored title options + description |

### YouTube API Quota
The free tier provides **10,000 units/day**.  
Video upload costs **1,600 units** — max ~6 uploads/day safely.  
Quota is tracked in `logs/quota_tracker.json` and reset daily.

---

## Compliance Architecture

All scripts pass through a two-stage compliance gate:

1. **Rule Engine** — regex patterns block guaranteed returns, risk-free claims, direct buy/sell advice
2. **Claude AI Review** — semantic review catches subtle compliance issues  
3. **Auto-Fix** — non-critical issues are automatically corrected  
4. **High-risk scripts are blocked** — never uploaded without manual review

All videos include the standard disclaimer per FTC and SEC guidance.

---

## Brand Guidelines

| Element | Value |
|---------|-------|
| Primary color | `#FF0033` (YouTube red) |
| Background | `#0A0A0F` (near black) |
| Accent | `#FFD700` (gold) |
| Success | `#00CC66` (green) |
| Fonts | Impact (headlines), DejaVu Sans (body) |
| Video format | 1920×1080 @ 30fps, libx264, 8Mbps |
| Shorts format | 1080×1920 @ 30fps, max 55s |
| Thumbnail | 1280×720 JPEG, >95 quality |

---

## Testing

```bash
pytest tests/ -v --cov=. --cov-report=term-missing
```

Tests cover: scraper data models, compliance rules, script parsing,  
title scoring, quota tracking, upload config, video builder assets.

All tests run without API keys using mocks.

---

## Legal

All generated content includes mandatory financial disclaimers.  
This pipeline is for educational content creation only.  
Nothing produced constitutes financial advice.  
Always review AI-generated scripts before publishing.
