# Connecting prasad2t@gmail.com to the Daily Drop Automation

Do this once. After that, videos upload automatically every day.

---

## Step 1 — Create a Google Cloud project (5 min)

1. Open [console.cloud.google.com](https://console.cloud.google.com) and **sign in with prasad2t@gmail.com**
2. Click the project dropdown at the top → **New Project**
3. Name: `daily-drop-youtube` → **Create**

---

## Step 2 — Enable the YouTube Data API (1 min)

1. In your new project, go to **APIs & Services → Library**
2. Search for `YouTube Data API v3`
3. Click it → **Enable**

---

## Step 3 — Create OAuth credentials (3 min)

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. If prompted to configure a consent screen:
   - User type: **External** → Create
   - App name: `Daily Drop` | Support email: `prasad2t@gmail.com`
   - Click **Save and Continue** through all screens (scopes/test users can be skipped for now)
4. Back in Credentials → **+ Create Credentials → OAuth client ID**
   - Application type: **Desktop app**
   - Name: `mind-fuel-daily-uploader`
   - Click **Create**
5. In the popup, click **Download JSON**
6. Rename the file to `client_secrets.json`
7. Copy it into `youtube_automation/client_secrets.json`

---

## Step 4 — Find your YouTube channel ID (1 min)

1. Go to [youtube.com](https://youtube.com) — sign in with prasad2t@gmail.com
2. Click your profile picture → **Settings**
3. Click **Advanced settings**
4. Copy your **Channel ID** — it starts with `UC...`

---

## Step 5 — Create your channel if you don't have one yet

1. Go to [youtube.com/create_channel](https://www.youtube.com/create_channel)
2. Channel name: **Mind Fuel Daily**
3. Choose a custom URL: `@GetMindFuelNow` (or similar)
4. Add a description: *"Learn something new every day. Money, career, health, tech, mindset & more — one video daily at 3 PM EST."*
5. Then go back to Step 4 to get your channel ID.

---

## Step 6 — Add your channel ID to `.env` (30 sec)

```bash
cd youtube_automation
cp .env.example .env
```

Open `.env` and set:

```
YOUTUBE_CHANNEL_ID=UCxxxxxxxxxxxxxxxxxxxxxxxxx   ← paste your channel ID here
YOUTUBE_PRIVACY_STATUS=public
UPLOAD_TIME=15:00
```

---

## Step 7 — First test run (generates video, no upload)

```bash
cd youtube_automation
./setup.sh                           # installs dependencies
source .venv/bin/activate
python daily_runner.py --now --no-upload
```

Check `output/` for your first `.mp4`.

---

## Step 8 — First real upload (opens browser for Google sign-in)

```bash
python daily_runner.py --now
```

A browser window opens → sign in with **prasad2t@gmail.com** → Allow.
The token is saved locally so you never need to do this again.

---

## Step 9 — Start daily uploads

```bash
# Runs forever, uploads at 15:00 every day
nohup python daily_runner.py --schedule > logs/scheduler.log 2>&1 &

# OR add to crontab (recommended for cloud servers):
crontab crontab.txt
```

---

## Checklist

- [ ] Google Cloud project created under prasad2t@gmail.com
- [ ] YouTube Data API v3 enabled
- [ ] `client_secrets.json` placed in `youtube_automation/`
- [ ] `.env` filled in with channel ID
- [ ] Test video generated successfully
- [ ] First upload authenticated
- [ ] Daily scheduler running

---

## YouTube monetisation requirements (to unlock ads)

You need to reach these thresholds before AdSense pays you:

| Requirement | Target | Current |
|-------------|--------|---------|
| Subscribers | 1,000 | — |
| Watch hours (last 12 months) | 4,000 hrs | — |
| Daily uploads | Every day | ✅ Automated |

At 1 video/day and realistic growth, most channels hit monetisation in **3-6 months**.
