# Mind Fuel Daily — YouTube Pencil-Sketch Animation (US Audience, Ages 18-40)

Generates and uploads a fresh **pencil-sketch animated video** to your YouTube channel every single day.
Content is designed for US viewers aged 18-40 and optimised for YouTube monetisation.

---

## Content Strategy

### Why this works for monetisation

| Factor | Decision | Impact |
|--------|----------|--------|
| **Location** | United States audience targeting | US CPM is 5-10× higher than global average |
| **Category** | Education (ID 27) | Top CPM tier: $15-40 per 1,000 views |
| **Upload time** | 3:00 PM EST | Peak US browsing → best initial view velocity |
| **Frequency** | Daily uploads | YouTube algorithm rewards consistency strongly |
| **Length** | ~2:40 | Meets monetisation threshold; short enough for high retention |

### 7 rotating categories (one per day)

| Day | Category | Avg US CPM |
|-----|----------|------------|
| Monday | 💰 Money & Finance | $20-40 |
| Tuesday | 🤖 Tech & AI | $15-35 |
| Wednesday | 🚀 Career & Salary | $12-25 |
| Thursday | 💪 Health & Fitness | $10-20 |
| Friday | 💰 Money & Finance | $20-40 (double Money day) |
| Saturday | ✈️ Lifestyle & Travel | $7-15 |
| Sunday | 🔬 Science & Psychology | $10-20 |

---

## Video structure (2:40, pencil-sketch on cream paper)

| Scene | Duration | What happens |
|-------|----------|-------------|
| Hook card | 0:00–0:10 | Big statement draws in word-by-word |
| Category badge | 0:10–0:15 | Day's category icon expands from centre |
| 5 Bullet points | 0:15–1:45 | Each point slides in with a number circle |
| Key Takeaway | 1:45–2:15 | Single actionable insight with decorative brackets |
| CTA Outro | 2:15–2:40 | Like / Share / Subscribe pulsing buttons |

---

## Quick start

```bash
cd youtube_automation
chmod +x setup.sh && ./setup.sh
```

Edit `.env`:
```
YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxx   ← your channel ID
YOUTUBE_PRIVACY_STATUS=public
UPLOAD_TIME=15:00                         ← 3 PM your server's local time
```

Place `client_secrets.json` (from Google Cloud Console) in this folder.

### Test without uploading

```bash
source .venv/bin/activate
python daily_runner.py --now --no-upload
# → output/mind_fuel_YYYY-MM-DD.mp4
```

### First real upload (OAuth opens in browser once)

```bash
python daily_runner.py --now
```

### Start the daily scheduler

```bash
# Option A — Python (stays running in background)
nohup python daily_runner.py --schedule > logs/scheduler.log 2>&1 &

# Option B — cron (recommended for VPS/cloud servers)
crontab crontab.txt
```

---

## Google Cloud setup (one-time, free)

1. [console.cloud.google.com](https://console.cloud.google.com) → New project
2. **Enable** YouTube Data API v3
3. APIs & Services → Credentials → **Create OAuth 2.0 Client ID** → Desktop App
4. Download JSON → rename to `client_secrets.json` → place in `youtube_automation/`

---

## Upload log

Every upload is recorded in `logs/uploads.json`:

```json
[
  {
    "date": "2026-06-09",
    "video_id": "dQw4w9WgXcQ",
    "title": "5 Money Mistakes Americans in Their 20s & 30s Regret Most",
    "url": "https://youtu.be/dQw4w9WgXcQ"
  }
]
```

---

## File layout

```
youtube_automation/
├── daily_runner.py        ← run this
├── video_generator.py     ← pencil-sketch animation engine
├── youtube_uploader.py    ← YouTube Data API v3 upload
├── content_generator.py   ← daily topic & script builder (US-focused)
├── config.py              ← settings (reads .env)
├── requirements.txt
├── setup.sh
├── crontab.txt
├── .env.example           ← copy to .env and fill in
├── output/                ← generated MP4s
└── logs/                  ← run logs + uploads.json
```

---

## Topics covered (sample)

**Money** — 401k mistakes, side hustle, index funds, credit score hacks, salary negotiation  
**Tech** — AI jobs impact, smartphone addiction, $100k tech skills, blockchain reality  
**Career** — Getting promoted, LinkedIn secrets, negotiation scripts  
**Health** — Ultra-processed food, Zone 2 training, sleep deprivation cost  
**Lifestyle** — $500 Europe flights, 20-min morning routine, minimalism  
**Science** — Dopamine & social media, memory science, cognitive biases  
**Mindset** — Habit loops, stoicism, why goals fail  
