# DriftWire326 — Google Cloud Deployment Guide (zero-GCP-cost)

Runs the full pipeline on a **GCP Always-Free e2-micro VM**. Total Google
Cloud cost: **$0/month** when you follow the free-tier rules below exactly.
The only paid service in the whole system is the Anthropic API (~$10–30/mo,
billed by Anthropic).

## Cost model — why this is $0 on Google Cloud

| Item | Free-tier rule | Our usage |
|---|---|---|
| Compute | 1× e2-micro, in **us-west1, us-central1, or us-east1** only | 1× e2-micro ✅ |
| Disk | ≤30 GB standard persistent disk | 30 GB ✅ |
| Egress | Traffic to Google services (incl. YouTube upload) is not billed as internet egress; 1 GB/mo free otherwise | Videos go to YouTube ✅ |
| YouTube Data API v3 | Free, 10,000 quota units/day | ~6,500/day peak ✅ |
| YouTube Analytics API v2 | Free | ~30 units/day ✅ |

Video **processing** happens on the VM's own CPU (moviepy + ffmpeg) — no
Transcoder API, no Cloud Storage, no paid ML services. Rendering a 4-minute
720p video on an e2-micro takes roughly 15–40 minutes, which fits easily
between the schedule slots (the pipeline allows 2 hours per run).

**Guard-rail:** set a Budget alert at $1 (step A6) so any accidental paid
usage emails you immediately.

---

## Phase A — Google Cloud Console (~25 min, one time, in your browser)

1. **Create a project**: console.cloud.google.com → New Project →
   name it `driftwire326`.
2. **Enable APIs**: APIs & Services → Library → enable both:
   - **YouTube Data API v3**
   - **YouTube Analytics API**
3. **OAuth consent screen**: APIs & Services → OAuth consent screen →
   - User type: **External** → Create
   - App name `DriftWire326`, your email for both contact fields
   - Scopes: skip (the app requests them at runtime)
   - Test users: **add the Gmail account that owns the YouTube channel**
4. **Publish the consent screen**: on the consent screen page press
   **"Publish app"**. ⚠️ This matters: in *Testing* mode refresh tokens
   die every 7 days and the pipeline would stop weekly. Published +
   unverified is fine for a private single-user app — you'll just see an
   "unverified app" warning once during token minting.
5. **Create credentials**: APIs & Services → Credentials → Create
   Credentials → **OAuth client ID** → Application type: **Desktop app** →
   name `driftwire326-desktop` → **Download JSON**.
   Save the file as `pipeline-finance/config/finance_oauth.json` in your
   local clone.
6. **Budget alert**: Billing → Budgets & alerts → Create budget →
   amount **$1** → alert at 50/90/100%. This is your "no surprise charges"
   tripwire.

## Phase B — Your local PC (~10 min, one time)

Mint the OAuth tokens (needs a browser, so it can't run on the VM):

```bash
git clone <your-repo-url>
cd energy-intelligence/pipeline-finance
python3 -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install google-auth-oauthlib google-api-python-client

# finance_oauth.json must already be in config/ (Phase A step 5)
python deploy/oauth_bootstrap.py
```

A browser opens twice — sign in with the channel's Google account and
approve. This produces `config/youtube_token.json` and
`config/analytics_token.json`.

Then create your `.env` from the template:

```bash
cp .env.example .env
# edit .env:
#   ANTHROPIC_API_KEY=sk-ant-...        (required — console.anthropic.com)
#   FRED_API_KEY=...                    (optional, free — fred.stlouisfed.org)
#   SMTP_* + ALERT_EMAIL                (recommended — dead-man switch emails)
#   VIDEO_RESOLUTION=1280x720           (recommended on the 1 GB free VM)
```

## Phase C — Create the VM (~10 min)

Console → Compute Engine → Create instance:

| Setting | Value (free tier — don't deviate) |
|---|---|
| Name | `driftwire326` |
| Region | **us-central1** (or us-west1 / us-east1) |
| Machine type | **e2-micro** |
| Boot disk | Debian 12, **30 GB standard persistent disk** (not SSD) |
| Firewall | leave HTTP/HTTPS unchecked (nothing inbound needed) |

SSH in (browser SSH button is fine) and run:

```bash
curl -O https://raw.githubusercontent.com/<user>/energy-intelligence/<branch>/pipeline-finance/deploy/vm_setup.sh
sudo bash vm_setup.sh https://github.com/<user>/energy-intelligence.git <branch>
```

(Private repo? `git clone` it manually first, then
`sudo bash energy-intelligence/pipeline-finance/deploy/vm_setup.sh`.)

The script installs ffmpeg/ImageMagick/fonts, adds 4 GB swap, creates the
`driftwire` service user, installs Python deps in a venv, and registers the
systemd service — **without starting it**.

## Phase D — Move secrets to the VM (~5 min)

From your **local PC**:

```bash
mkdir -p config-drop   # or use gcloud compute scp
scp .env config/finance_oauth.json config/youtube_token.json config/analytics_token.json \
    <you>@<vm-external-ip>:/tmp/
```

On the **VM**:

```bash
sudo mv /tmp/.env /opt/driftwire326/pipeline-finance/.env
sudo mv /tmp/*.json /opt/driftwire326/pipeline-finance/config/
sudo chown -R driftwire:driftwire /opt/driftwire326
sudo chmod 600 /opt/driftwire326/pipeline-finance/.env \
               /opt/driftwire326/pipeline-finance/config/*token*.json
```

## Phase E — Verify before going live (~30 min)

Run these on the VM, in order — each must pass before the next:

```bash
APP=/opt/driftwire326/pipeline-finance
PY=/opt/driftwire326/venv/bin/python3

# 1. Test suite (no API calls)
sudo -u driftwire $PY -m pytest $APP/tests -q

# 2. Quota + API status (proves .env and tokens load)
sudo -u driftwire bash -c "cd $APP && $PY main.py --quota"

# 3. Data-only dry run (scrapers + Claude, no upload)
sudo -u driftwire bash -c "cd $APP && $PY main.py --run weekday --dry-run"

# 4. ONE full supervised run — this uploads a real (private) video
sudo -u driftwire bash -c "cd $APP && $PY main.py --run weekday"
```

After step 4, check YouTube Studio: you should see a **private, scheduled**
video with thumbnail, chapters, data-cutoff footer, captions, and a pinned
comment — and the 6 playlists auto-created. Delete the video if you don't
want the trial published, or let it go live at its scheduled slot.

## Phase F — Go live

```bash
sudo systemctl start driftwire326
journalctl -u driftwire326 -f       # watch the job registrations
```

You should see "12+ jobs registered". From here the channel runs itself:
- 17 uploads/week (11 long-form + 6 Shorts), all quota-gated
- crashed runs auto-resume from checkpoint at :45
- 6 PM ET dead-man email if a day ever produces nothing
- `Restart=on-failure` resurrects the scheduler process itself

## Week-1 babysitting checklist

- Day 1–2: check `journalctl -u driftwire326` and YouTube Studio daily
- Confirm the dead-man email arrives if you stop the service for a day (test it!)
- Watch `logs/quota_state.json` — should stay well under 10,000/day
- If renders are too slow or OOM: confirm `VIDEO_RESOLUTION=1280x720` in .env
  and that swap is active (`swapon --show`)
- After ~2 weeks, review `logs/analytics/` weekly reports → decide Phase 2

## Ongoing costs recap

| Service | Cost |
|---|---|
| GCP e2-micro + 30 GB disk + egress | **$0** (always-free tier) |
| YouTube Data + Analytics APIs | **$0** |
| edge-tts, yfinance, RSS, pytrends, FRED, SEC EDGAR | **$0** |
| Anthropic API (~40 Claude calls/day, small prompts) | ~$10–30/mo |
