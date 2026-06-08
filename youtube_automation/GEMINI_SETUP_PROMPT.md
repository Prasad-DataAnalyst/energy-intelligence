# Gemini Prompt — Complete YouTube Daily Drop Setup

Copy everything between the lines and paste it into Gemini.

---

## PROMPT START
---

You are acting as my personal automation assistant with full permission to complete the following setup on my behalf. I am Prasad Selvaraj (prasad2t@gmail.com). Take every action listed below from start to finish without stopping to ask me to do things manually. If a step requires a decision, choose the option that stays within Google's free tier.

---

### Context

I have a Python-based YouTube automation system in my repository:
**GitHub:** `https://github.com/Prasad-DataAnalyst/energy-intelligence`
**Branch:** `claude/youtube-daily-animation-o9gyu`
**Folder:** `youtube_automation/`

This system generates a daily pencil-sketch animated video and uploads it to my YouTube channel automatically. Everything is coded and tested. The only missing piece is the Google Cloud OAuth credentials.

---

### My account details

- **Google account:** prasad2t@gmail.com
- **Desired GCP project ID:** `daily-drop-prasad`
- **Desired GCP project name:** Daily Drop Prasad
- **YouTube channel name to create (if none exists):** Daily Drop with Prasad
- **Free tier constraint:** stay within all Google Cloud free quotas

---

### Task 1 — Google Cloud Project

1. Open Google Cloud Console at https://console.cloud.google.com
2. Sign in with prasad2t@gmail.com
3. Create a new project:
   - Project name: `Daily Drop Prasad`
   - Project ID: `daily-drop-prasad`
4. Set this as the active project

---

### Task 2 — Enable YouTube Data API v3

1. In the project `daily-drop-prasad`, go to **APIs & Services → Library**
2. Search for **YouTube Data API v3**
3. Click **Enable**
4. Confirm it is active under **APIs & Services → Enabled APIs**

Free tier note: YouTube Data API v3 gives 10,000 units/day free. One video upload costs ~1,600 units. We upload once per day — well within the free limit.

---

### Task 3 — Configure OAuth Consent Screen

1. Go to **APIs & Services → OAuth consent screen**
2. Select **External** user type → Create
3. Fill in:
   - App name: `Daily Drop Uploader`
   - User support email: `prasad2t@gmail.com`
   - Developer contact email: `prasad2t@gmail.com`
4. Click **Save and Continue** through Scopes (no changes needed)
5. Under **Test users**, add `prasad2t@gmail.com`
6. Click **Save and Continue** → **Back to Dashboard**

---

### Task 4 — Create OAuth 2.0 Client Credentials

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → OAuth client ID**
3. Set:
   - Application type: **Desktop app**
   - Name: `daily-drop-uploader`
4. Click **Create**
5. In the confirmation dialog, click **Download JSON**
6. The file will be named something like `client_secret_XXXXXX.apps.googleusercontent.com.json`
7. **Rename it to:** `client_secrets.json`
8. Place it at this path in my project: `youtube_automation/client_secrets.json`
   - If using GitHub, commit it to the branch `claude/youtube-daily-animation-o9gyu`
   - If working locally, drop it in the `youtube_automation/` folder

---

### Task 5 — YouTube Channel

1. Go to https://www.youtube.com — sign in with prasad2t@gmail.com
2. Check if a channel already exists:
   - If YES: note the channel ID (Settings → Advanced settings → Channel ID starts with UC...)
   - If NO: create one at https://www.youtube.com/create_channel
     - Channel name: **Daily Drop with Prasad**
     - Handle: `@DailyDropPrasad` (or closest available)
     - Description: *Learn something new every day. Money, career, health, tech, mindset and more — one video daily at 3 PM EST.*
3. Record the Channel ID (format: `UCxxxxxxxxxxxxxxxxxxxxxxxx`)

---

### Task 6 — Update the .env file

In the repository at `youtube_automation/.env` (copy from `.env.example` if it doesn't exist), set:

```
YOUTUBE_CLIENT_SECRETS_FILE=client_secrets.json
YOUTUBE_CHANNEL_ID=<the channel ID from Task 5>
YOUTUBE_CATEGORY_ID=27
YOUTUBE_PRIVACY_STATUS=public
OUTPUT_DIR=output
VIDEO_WIDTH=1280
VIDEO_HEIGHT=720
VIDEO_FPS=15
VIDEO_DURATION_SECONDS=92
UPLOAD_TIME=15:00
EIA_API_KEY=
```

---

### Task 7 — Run First-Time Setup

In a terminal inside the `youtube_automation/` folder, run:

```bash
source .venv/bin/activate
python first_time_setup.py
```

When the script prints an authorisation URL:
1. Open the URL in a browser signed in as prasad2t@gmail.com
2. Click **Allow**
3. Copy the authorisation code shown
4. Paste it back into the terminal

The script will then:
- Confirm the channel ID
- Write the .env file
- Generate today's video
- Upload it to the channel
- Install the daily cron job (uploads every day at 15:00)

---

### Task 8 — Verify

After the script completes, confirm:
- [ ] A video appears in https://www.youtube.com/channel (or studio.youtube.com)
- [ ] `logs/uploads.json` contains an entry with today's date and a YouTube URL
- [ ] `crontab -l` shows the daily upload job

---

### Important constraints

- **Stay on the free tier** for all Google Cloud services
- **Do not enable billing** unless explicitly required for a free-tier service
- **Do not share or store** any passwords anywhere
- YouTube Data API v3 free quota: 10,000 units/day — one upload per day uses ~1,600 units ✓
- Cloud Resource Manager API: free ✓
- OAuth 2.0: free ✓

---

### Summary of what you need to deliver

| Item | Where |
|------|-------|
| `client_secrets.json` | `youtube_automation/client_secrets.json` |
| `.env` with channel ID | `youtube_automation/.env` |
| First video live on YouTube | youtube.com/channel/... |
| Daily cron job running | `crontab -l` output |

Start from Task 1 and complete all 8 tasks. Report back with the YouTube channel URL and the video URL once done.

---
## PROMPT END

---

## Notes on using this prompt

- Paste the above into **Gemini Advanced** (1.5 Pro or 2.0) for best results — it handles multi-step tasks better than the free tier.
- If Gemini asks for clarification on any step, tell it: *"Use prasad2t@gmail.com, stay free tier, complete all steps."*
- Gemini can open URLs and interact with Google services when given permission in Gemini Advanced with Google Workspace integration enabled.
- The `client_secrets.json` file Gemini downloads should be pasted/uploaded here: `youtube_automation/client_secrets.json`
