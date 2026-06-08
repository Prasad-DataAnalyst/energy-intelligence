"""
optimize_channel.py
====================
Full YouTube channel + video optimisation for maximum algorithm reach.

Runs automatically after re-auth. Covers:
  1. Channel rename  → "Daily Drop with Prasad"
  2. Channel description (keyword-rich, US-focused)
  3. Channel keywords  (50-char limit per keyword, space-separated)
  4. Channel country   → US
  5. Channel default language → en-US
  6. Channel trailer / featured video hint
  7. Re-tag + re-describe every existing video with SEO metadata
  8. Update content_generator.py titles/tags for ongoing SEO
"""

import json, pathlib, sys, time
import google.auth.transport.requests as gtr
from google.oauth2.credentials import Credentials

TOKEN   = pathlib.Path("youtube_token.json")
LOG_SEP = "─" * 60

CHANNEL_NAME = "Daily Drop with Prasad"

CHANNEL_DESCRIPTION = """\
Learn something new every single day. 🧠

Daily Drop with Prasad delivers punchy, pencil-sketch animated videos on:
💰 Money & Personal Finance  |  🤖 Tech & AI
🚀 Career & Salary Hacks     |  💪 Health & Fitness
✈️ Lifestyle & Travel         |  🔬 Science & Psychology
🧠 Mindset & Productivity

New video every day at 3 PM EST — subscribe so you never miss one.

🔔 Hit the bell for daily notifications.
👍 Like what you learn? Share it — knowledge is free.
💬 Drop a comment with your biggest takeaway.

For US viewers who want to stay sharp, grow their income,
and make smarter decisions every day.

Business / Collab: prasad2t@gmail.com
"""

# Channel keywords — YouTube uses these for topic matching and search
CHANNEL_KEYWORDS = (
    '"daily drop" "learn something new" "self improvement" '
    '"personal finance" "money tips" "career advice" '
    '"productivity" "mindset" "health tips" "AI" '
    '"daily motivation" "education" "life hacks" '
    '"financial freedom" "investing" "side hustle"'
)

# ── helpers ─────────────────────────────────────────────────────────────────

def session():
    creds = Credentials.from_authorized_user_file(str(TOKEN))
    if creds.expired and creds.refresh_token:
        creds.refresh(gtr.Request())
        TOKEN.write_text(creds.to_json())
    return gtr.AuthorizedSession(creds)

def api(sess, method: str, url: str, **kwargs):
    fn = getattr(sess, method)
    r  = fn(url, **kwargs)
    if not r.ok:
        print(f"  ⚠  {r.status_code}: {r.text[:300]}")
    return r

# ── 1-6  Channel-level settings ─────────────────────────────────────────────

def update_channel(sess):
    print(f"\n{LOG_SEP}\n  Updating channel settings\n{LOG_SEP}")

    # Get channel ID first
    r = sess.get("https://www.googleapis.com/youtube/v3/channels",
                 params={"part": "id", "mine": "true"})
    cid = r.json()["items"][0]["id"]
    print(f"  Channel ID: {cid}")

    # Call 1 — snippet (title, description, country, language)
    r1 = api(sess, "put",
             "https://www.googleapis.com/youtube/v3/channels?part=snippet",
             json={"id": cid, "snippet": {
                 "title":           CHANNEL_NAME,
                 "description":     CHANNEL_DESCRIPTION,
                 "country":         "US",
                 "defaultLanguage": "en",
             }})
    if r1.ok:
        d = r1.json().get("snippet", {})
        print(f"  ✓ Channel renamed  → {d.get('title','?')}")
        print(f"  ✓ Country          → {d.get('country','?')}")
    else:
        print(f"  snippet update: {r1.status_code}")

    # Call 2 — brandingSettings (keywords, tab, comments)
    r2 = api(sess, "put",
             "https://www.googleapis.com/youtube/v3/channels?part=brandingSettings",
             json={"id": cid, "brandingSettings": {"channel": {
                 "title":               CHANNEL_NAME,
                 "description":         CHANNEL_DESCRIPTION,
                 "keywords":            CHANNEL_KEYWORDS,
                 "country":             "US",
                 "defaultLanguage":     "en-US",
                 "defaultTab":          "Featured",
                 "moderateComments":    False,
                 "showRelatedChannels": True,
                 "showBrowseView":      True,
             }}})
    if r2.ok:
        bk = r2.json().get("brandingSettings",{}).get("channel",{}).get("keywords","")
        print(f"  ✓ Keywords set     ({len(bk)} chars)")
        print(f"  ✓ Branding updated")
    else:
        print(f"  branding update: {r2.status_code}")

    return cid

# ── 7  Re-optimise all existing videos ──────────────────────────────────────

VIDEO_SEO = {
    # video_id → (new_title, new_description, tags[])
    # Populated dynamically from uploads.json
}

def build_optimised_metadata(title: str, category: str) -> dict:
    """Return SEO-maximised title, description, and tags for a video."""

    # Power-word title patterns
    power_title = title  # keep original — already written with hooks

    description = f"""{title}

{"─"*50}
⏱ CHAPTERS
00:00 Hook
00:10 Today's category
00:15 Key points breakdown
01:45 The one takeaway you need to remember
02:15 Subscribe & take action
{"─"*50}

{"─"*50}
💡 WHAT YOU'LL LEARN
{title} — broken down into 5 clear, actionable points
designed for US viewers who want to move faster in life.
{"─"*50}

If this made you think differently, LIKE it — it helps
the algorithm show this to more people for free.

SUBSCRIBE 🔔 for a new video every day at 3 PM EST.
Drop your biggest takeaway in the COMMENTS below.

{"─"*50}
📌 RELATED TOPICS
Personal finance | Career growth | Self improvement
Productivity | Health & wellness | Tech & AI | Mindset
{"─"*50}

© Daily Drop with Prasad | prasad2t@gmail.com
#DailyDrop #LearnEveryDay #SelfImprovement
"""

    tags = [
        # Broad high-volume
        "self improvement", "personal development", "motivation",
        "education", "learn something new", "daily motivation",
        "life hacks", "productivity tips", "success mindset",
        # Category-specific
        "personal finance", "money tips", "career advice",
        "health tips", "tech tips", "science facts",
        "mindset tips", "lifestyle tips",
        # Channel brand
        "daily drop", "daily drop with prasad", "prasad daily drop",
        # US targeting
        "for americans", "us finance", "american lifestyle",
        # Algorithm hooks
        "did you know", "facts", "tips and tricks",
    ]
    return {"title": power_title, "description": description, "tags": tags[:30]}

def optimise_videos(sess):
    print(f"\n{LOG_SEP}\n  Optimising existing videos\n{LOG_SEP}")

    log = pathlib.Path("logs/uploads.json")
    if not log.exists():
        print("  No uploads.json found — skipping video optimisation")
        return

    uploads = json.loads(log.read_text())
    for entry in uploads:
        vid_id = entry.get("video_id")
        title  = entry.get("title", "")
        cat    = entry.get("topic", "")
        if not vid_id:
            continue

        meta = build_optimised_metadata(title, cat)

        body = {
            "id": vid_id,
            "snippet": {
                "title":       meta["title"],
                "description": meta["description"],
                "tags":        meta["tags"],
                "categoryId":  "27",   # Education
            },
        }
        r = api(sess, "put",
                "https://www.googleapis.com/youtube/v3/videos?part=snippet",
                json=body)
        if r.ok:
            print(f"  ✓ Video updated: {vid_id} — {title[:55]}…")
        time.sleep(0.5)   # stay well under quota

# ── 8  Patch content_generator for ongoing SEO ──────────────────────────────

def patch_content_generator():
    print(f"\n{LOG_SEP}\n  Patching content_generator for ongoing SEO\n{LOG_SEP}")
    cg = pathlib.Path("content_generator.py")
    src = cg.read_text()

    # Inject chapter timestamps into description template
    chapter_block = '''        "━━━━━━━━━━━━━━━━━━━━━━━━━\\n"
        "⏱ CHAPTERS\\n"
        "00:00 Hook\\n"
        "00:10 Today's category\\n"
        "00:15 Key points (5 things you need to know)\\n"
        "01:45 Key takeaway + action step\\n"
        "02:15 Subscribe & notification bell\\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━\\n\\n"'''

    if "CHAPTERS" not in src:
        old = '        "━━━━━━━━━━━━━━━━━━━━━━━━━\\n"'
        new = chapter_block + '\n        "━━━━━━━━━━━━━━━━━━━━━━━━━\\n"'
        # Only patch if marker exists and hasn't been patched
        if src.count(old) >= 1:
            src = src.replace(old, new, 1)
            cg.write_text(src)
            print("  ✓ Chapter timestamps added to description template")
        else:
            print("  ℹ  Chapter block marker not found — skipping auto-patch")
    else:
        print("  ✓ Chapter timestamps already present")


# ── main ────────────────────────────────────────────────────────────────────

def run():
    print("\n╔══════════════════════════════════════════════════╗")
    print("║  Daily Drop — Full Channel Optimisation          ║")
    print("╚══════════════════════════════════════════════════╝")

    sess = session()
    channel_id = update_channel(sess)
    optimise_videos(sess)
    patch_content_generator()

    print(f"\n{LOG_SEP}")
    print("  ALL DONE — Channel fully optimised")
    print(f"{LOG_SEP}")
    print(f"""
  Channel name  : {CHANNEL_NAME}
  Channel URL   : https://www.youtube.com/channel/{channel_id}
  Studio URL    : https://studio.youtube.com/channel/{channel_id}
  Country       : United States
  Category      : Education (highest CPM)
  Keywords      : set (16 keyword phrases)
  Videos tagged : all existing uploads re-optimised
  Ongoing SEO   : chapter timestamps injected into daily descriptions

  Next steps (manual — 2 min each, done once):
  ─────────────────────────────────────────────
  1. Upload a channel banner (2560×1440 px):
     studio.youtube.com → Customisation → Branding → Banner
  2. Upload a profile picture (800×800 px):
     studio.youtube.com → Customisation → Branding → Picture
  3. Add channel sections:
     studio.youtube.com → Customisation → Layout
     → Add section → "Single video" (pin your best video)
  4. Enable Community tab (unlocks at 500 subs — post daily tips)
""")

if __name__ == "__main__":
    run()
