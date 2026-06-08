# Energy Intelligence — YouTube Daily Animation

Automatically generates and uploads a **pencil-sketch animated energy briefing** to YouTube every day.

## What it does

| Step | What happens |
|------|-------------|
| **Content** | Fetches live Brent, WTI, Henry Hub, and LNG prices from EIA API |
| **Animation** | Renders a 3-minute pencil-sketch video (5 scenes, 1080p) |
| **Upload** | Pushes the MP4 to your YouTube channel via the Data API |
| **Schedule** | Runs daily at the time you set in `.env` |

## Video structure (3 minutes)

| Scene | Duration | Content |
|-------|----------|---------|
| 0 – Title card | 8 s | Channel logo + today's date + topic |
| 1 – Price ticker | 40 s | Brent, WTI, Henry Hub, LNG JKM with change indicators |
| 2 – Chart | 60 s | Brent vs WTI 30-day animated line chart |
| 3 – Focus topic | 52 s | 4 key bullets on today's energy topic |
| 4 – Outro | 20 s | Subscribe CTA + contact |

## Quick start

```bash
cd youtube_automation
chmod +x setup.sh
./setup.sh
```

Then edit `.env`:

```
EIA_API_KEY=your_eia_key         # free at eia.gov/opendata
YOUTUBE_CHANNEL_ID=UCxxxxxxxx    # Your channel ID
YOUTUBE_PRIVACY_STATUS=public
UPLOAD_TIME=08:00
```

Place your Google OAuth credentials file as `client_secrets.json` (see below).

### Test video generation (no upload)

```bash
source .venv/bin/activate
python daily_runner.py --now --no-upload
# Output: output/energy_daily_YYYY-MM-DD.mp4
```

### First upload (triggers browser OAuth)

```bash
python daily_runner.py --now
```

### Start daily scheduler

```bash
# Option A — Python scheduler (stays running)
nohup python daily_runner.py --schedule &

# Option B — cron (recommended for servers)
crontab crontab.txt
```

## Google Cloud setup (one-time)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a project → **Enable YouTube Data API v3**
3. APIs & Services → Credentials → **Create OAuth 2.0 Client ID** (Desktop App)
4. Download JSON → save as `client_secrets.json` in `youtube_automation/`

## EIA API key (free)

Register at [eia.gov/opendata](https://www.eia.gov/opendata/register.php) — instant approval, no credit card.

## Upload log

Every successful upload is appended to `logs/uploads.json`:

```json
[
  {
    "date": "2026-06-08",
    "video_id": "abc123XYZ",
    "title": "Energy Market Daily | 08 Jun 2026 | ...",
    "url": "https://youtu.be/abc123XYZ"
  }
]
```

## File layout

```
youtube_automation/
├── daily_runner.py        # Orchestrator — run this
├── video_generator.py     # Pencil-sketch animation engine
├── youtube_uploader.py    # YouTube API upload
├── content_generator.py   # EIA data + daily script builder
├── config.py              # All settings (reads from .env)
├── requirements.txt
├── setup.sh
├── crontab.txt
├── .env.example           # Copy to .env and fill in
├── output/                # Generated MP4s
└── logs/                  # Run logs + uploads.json
```
