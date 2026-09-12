"""
intelligence/community_engine.py — Community Engagement Automation
==================================================================
Turns passive viewers into loyal community members through intelligent
comment management, strategic posts, and subscriber retention.

Cycle (every 6h):
  CommentIntelligence       — fetch, categorize, queue replies via Claude
  CommentAutomator          — execute replies/hearts/pins via YouTube API
  EngagementVelocityTracker — rapid first-hour replies on new uploads
  CommunityPostGenerator    — 3x/week: polls, teasers, requests, hot takes
  AudienceRetentionAnalyzer — detect drops, identify culprit, recover in 48h
  CommunityEngine           — orchestrator

YouTube API scope notes:
  - Comment replies:     YouTube Data API v3  (comments.insert — OAuth required)
  - Heart / pin:         YouTube Studio API   (requires session; stub provided)
  - Community posts:     YouTube Studio API   (requires session; content queued)
  All actions are logged in memory; actual execution checks for API credentials.
"""
import json
import logging
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

log = logging.getLogger("brain.community_engine")

HERE = Path(__file__).parent.parent

# ── Comment categorization signals ───────────────────────────────────────────

CATEGORY_PATTERNS: Dict[str, List[str]] = {
    "question": [
        r"\?", r"\bhow\b", r"\bwhat\b", r"\bwhy\b", r"\bwhen\b",
        r"\bwhere\b", r"\bcan you\b", r"\bcould you\b", r"\bdo you\b",
        r"\bplease explain\b", r"\bhelp me\b", r"\bwondering\b",
    ],
    "complaint": [
        r"\bwrong\b", r"\berror\b", r"\bmistake\b", r"\bincorrect\b",
        r"\bdisagree\b", r"\bboring\b", r"\bterrible\b", r"\bawful\b",
        r"\bdisappointed\b", r"\bclickbait\b", r"\blied\b", r"\bnot true\b",
        r"\binaccurate\b", r"\bfactually\b", r"\bdislike\b", r"\bunsubscrib",
    ],
    "suggestion": [
        r"\byou should\b", r"\bplease make\b", r"\bnext video\b",
        r"\bwould love\b", r"\bmore about\b", r"\btopic\b", r"\brequest\b",
        r"\bsuggestion\b", r"\bdo a video\b", r"\bmake a video\b",
        r"\bcover\b", r"\btalking about\b",
    ],
    "praise": [
        r"\bgreat\b", r"\bexcellent\b", r"\bamazing\b", r"\bawesome\b",
        r"\blove\b", r"\bthank\b", r"\bperfect\b", r"\bbest\b",
        r"\bbrilliant\b", r"\bfantastic\b", r"\bhelpful\b", r"\bwow\b",
        r"\bincredible\b", r"\bsubscrib", r"\bunderrated\b",
    ],
}

# ── Pin candidate scoring signals ────────────────────────────────────────────

PIN_SIGNALS: Dict[str, float] = {
    "question":      15.0,   # questions invite others to answer
    "debate_trigger": 22.0,  # "I disagree", "actually", "you're wrong"
    "funny":          18.0,  # "lol", "😂", "hilarious"
    "insight":        16.0,  # "I didn't know", "mind blown", "learned"
    "story":          14.0,  # "this happened to me", "I remember when"
    "high_like_base": 20.0,  # base bonus if comment has existing likes
}

PIN_KEYWORDS = {
    "debate_trigger": ["disagree", "actually", "you're wrong", "wrong",
                       "i think differently", "unpopular opinion"],
    "funny":          ["lol", "😂", "haha", "hilarious", "ironic", "😅"],
    "insight":        ["didn't know", "mind blown", "learned", "never knew",
                       "eye opening", "wow didn't realize"],
    "story":          ["this happened to me", "i remember", "in my experience",
                       "personally", "true story"],
}

# ── Community post templates ──────────────────────────────────────────────────

POST_SCHEDULES = [
    (1, 18),   # Tuesday  18:00 UTC
    (3, 18),   # Thursday 18:00 UTC
    (5, 14),   # Saturday 14:00 UTC
]

POST_TYPE_CYCLE = ["poll", "request", "controversial", "teaser", "poll"]


# ── 1. CommentIntelligence ────────────────────────────────────────────────────

class CommentIntelligence:
    """
    Fetches, categorizes and queues Claude-generated replies for every video.
    Heuristic categorization is the primary path; Claude refines ambiguous cases.
    """

    YT_API_URL = "https://www.googleapis.com/youtube/v3"

    def __init__(self):
        self._yt_key     = os.getenv("YOUTUBE_DATA_API_KEY", "")
        self._claude_key = os.getenv("ANTHROPIC_API_KEY", "")

    # ── Public API ────────────────────────────────────────────────────────

    def fetch_video_comments(self, youtube_id: str,
                              max_results: int = 200) -> List[Dict]:
        """Fetch top-level comments for a video via YouTube Data API v3."""
        if not self._yt_key:
            return []
        url = (
            f"{self.YT_API_URL}/commentThreads"
            f"?part=snippet&videoId={youtube_id}"
            f"&maxResults={min(max_results, 100)}"
            f"&order=relevance&key={self._yt_key}"
        )
        comments = []
        page_token = ""
        fetched    = 0
        while fetched < max_results:
            try:
                req_url = url + (f"&pageToken={page_token}" if page_token else "")
                req = urllib.request.Request(req_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                for item in data.get("items", []):
                    sc   = item["snippet"]["topLevelComment"]["snippet"]
                    comments.append({
                        "comment_id":   item["snippet"]["topLevelComment"]["id"],
                        "author":       sc.get("authorDisplayName", ""),
                        "text":         sc.get("textDisplay", ""),
                        "likes":        sc.get("likeCount", 0),
                        "published_at": sc.get("publishedAt", ""),
                    })
                fetched += len(data.get("items", []))
                page_token = data.get("nextPageToken", "")
                if not page_token or fetched >= max_results:
                    break
                time.sleep(0.2)
            except Exception as e:
                log.debug(f"Comment fetch error for {youtube_id}: {e}")
                break
        return comments

    def categorize_comment(self, text: str) -> str:
        """
        Classify a comment into: question | complaint | suggestion | praise | other.
        Uses regex signal matching; most-hit category wins.
        """
        text_lower = text.lower()
        scores: Dict[str, int] = {cat: 0 for cat in CATEGORY_PATTERNS}
        for category, patterns in CATEGORY_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, text_lower):
                    scores[category] += 1
        best = max(scores, key=lambda c: scores[c])
        return best if scores[best] > 0 else "other"

    def score_pin_candidate(self, comment: Dict) -> float:
        """Return a score 0-100 indicating how well a comment triggers discussion."""
        text  = comment.get("text", "").lower()
        score = 0.0

        # Signal-based scoring
        for sig, sig_score in PIN_SIGNALS.items():
            if sig == "high_like_base":
                likes = comment.get("likes", 0)
                score += min(sig_score, likes * 0.5)
                continue
            keywords = PIN_KEYWORDS.get(sig, [])
            if any(kw in text for kw in keywords):
                score += sig_score

        # Category bonus
        cat = self.categorize_comment(comment.get("text", ""))
        if cat == "question":
            score += PIN_SIGNALS["question"]

        # Length bonus (thoughtful comments)
        word_count = len(text.split())
        if word_count > 30:
            score += 8.0
        elif word_count > 15:
            score += 4.0

        return min(100.0, score)

    def find_pin_candidate(self, comments: List[Dict]) -> Optional[Dict]:
        """Return the comment most likely to trigger discussion."""
        if not comments:
            return None
        scored = [(c, self.score_pin_candidate(c)) for c in comments]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[0][0] if scored else None

    def generate_reply(self, comment: Dict, video_context: Dict) -> str:
        """
        Generate a context-aware reply using Claude Haiku.
        Falls back to template replies when API is unavailable.
        """
        category = comment.get("category",
                                self.categorize_comment(comment.get("text", "")))
        text     = comment.get("text", "")
        topic    = video_context.get("title", "this video")
        channel  = video_context.get("channel_name", "the channel")

        if self._claude_key:
            reply = self._claude_reply(text, category, topic, channel)
            if reply:
                return reply

        # Template fallbacks
        return self._template_reply(text, category, topic)

    def batch_process_video(self, youtube_id: str,
                             video_context: Dict,
                             memory=None) -> Dict:
        """
        Full comment processing for one video:
          fetch → categorize → score pin candidate → queue replies.
        Saves results to memory.
        """
        comments = self.fetch_video_comments(youtube_id)
        result   = {"youtube_id": youtube_id, "fetched": len(comments),
                    "by_category": {}, "pin_candidate": None,
                    "replies_queued": 0}

        categorized: List[Dict] = []
        for c in comments:
            cat   = self.categorize_comment(c["text"])
            reply = self.generate_reply({**c, "category": cat}, video_context)
            pin_s = self.score_pin_candidate(c)
            categorized.append({**c, "category": cat,
                                 "reply_text": reply, "pin_score": pin_s})
            result["by_category"][cat] = result["by_category"].get(cat, 0) + 1

        pin_comment = self.find_pin_candidate(categorized)
        result["pin_candidate"] = pin_comment

        if memory:
            try:
                for c in categorized:
                    memory.save_comment({**c, "youtube_id": youtube_id})
                result["replies_queued"] = len(categorized)
            except Exception as e:
                log.warning(f"Memory save failed for comments: {e}")

        return result

    # ── Private helpers ───────────────────────────────────────────────────

    def _claude_reply(self, text: str, category: str,
                       topic: str, channel: str) -> Optional[str]:
        prompts = {
            "question": (
                f"You are a helpful YouTube creator replying to a viewer question "
                f"on your video about '{topic}'. Write a concise, genuinely helpful "
                f"reply (2-3 sentences max). Don't be sycophantic. "
                f"Comment: {text[:300]}"
            ),
            "complaint": (
                f"You are a YouTube creator responding to criticism on your video "
                f"about '{topic}'. Be empathetic, acknowledge their point, and "
                f"briefly explain or offer to fix. Keep it under 3 sentences, "
                f"no defensiveness. Comment: {text[:300]}"
            ),
            "suggestion": (
                f"A viewer suggested a video topic on your channel about '{topic}'. "
                f"Reply enthusiastically (genuine, not performative) that you love "
                f"the idea and are considering making a video on it. Max 2 sentences. "
                f"Comment: {text[:300]}"
            ),
            "praise": (
                f"Reply to a kind comment on your video about '{topic}'. "
                f"Be warm and personal — mention something specific from their "
                f"comment. Max 2 sentences. Don't just say 'thank you'. "
                f"Comment: {text[:300]}"
            ),
        }
        prompt = prompts.get(category, prompts["praise"])
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._claude_key)
            msg    = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=150,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.debug(f"Claude reply generation failed: {e}")
            return None

    def _template_reply(self, text: str, category: str, topic: str) -> str:
        templates = {
            "question": (
                f"Great question! This is something I cover more in depth on the "
                f"channel — the short answer is in the video, but let me know if "
                f"you'd like me to make a dedicated video on this aspect of {topic}!"
            ),
            "complaint": (
                "Thank you for the feedback — I genuinely appreciate it. "
                "I'll review this point and address it either in a correction "
                "or a follow-up video. Your input helps make the content better!"
            ),
            "suggestion": (
                f"Love this idea! I'm actually already thinking about covering "
                f"this — adding it to my content queue now. Stay subscribed so "
                f"you don't miss it when it goes live! 🎬"
            ),
            "praise": (
                "Really glad this was helpful! Comments like yours are exactly "
                "why I make these videos. More content coming soon — stay tuned! 🙌"
            ),
            "other": (
                "Thanks for watching and engaging! Drop any questions below and "
                "I'll do my best to answer them. 👋"
            ),
        }
        return templates.get(category, templates["other"])


# ── 2. CommentAutomator ──────────────────────────────────────────────────────

class CommentAutomator:
    """
    Executes queued comment actions via YouTube Data API v3 and YouTube Studio.

    Quota budget (10,000 units/day default):
      commentThreads.list  = 1 unit
      comments.insert      = 50 units
      Heart / pin          = YouTube Studio API (no quota cost, session required)
    """

    YT_API_URL     = "https://www.googleapis.com/youtube/v3"
    DAILY_QUOTA    = 10_000
    REPLY_COST     = 50
    MAX_REPLIES    = DAILY_QUOTA // REPLY_COST  # 200 replies/day max

    def __init__(self):
        self._oauth_token = os.getenv("YOUTUBE_OAUTH_TOKEN", "")
        self._yt_key      = os.getenv("YOUTUBE_DATA_API_KEY", "")

    def reply_to_comment(self, comment_id: str, reply_text: str,
                          video_id: str = "") -> bool:
        """
        Post a reply using YouTube Data API comments.insert (OAuth required).
        Returns True on success.
        """
        if not self._oauth_token:
            log.debug(f"  Reply queued (no OAuth token): {reply_text[:50]!r}")
            return False
        payload = json.dumps({
            "snippet": {
                "parentId":    comment_id,
                "textOriginal": reply_text[:10_000],
            }
        }).encode()
        req = urllib.request.Request(
            f"{self.YT_API_URL}/comments?part=snippet",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._oauth_token}",
                "Content-Type":  "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            log.debug(f"Reply failed for {comment_id}: {e}")
            return False

    def heart_comment(self, comment_id: str) -> bool:
        """
        Heart a comment.
        YouTube Studio API action — requires session cookie (SAPISID).
        Returns False with a log entry when session is not configured.
        """
        session = os.getenv("YOUTUBE_STUDIO_SESSION", "")
        if not session:
            log.debug(f"  Heart queued (no Studio session): {comment_id}")
            return False
        # YouTube Studio internal endpoint (unofficial)
        url     = "https://studio.youtube.com/youtubei/v1/comment/perform_comment_action"
        payload = json.dumps({
            "actions": [{"action": "ACTION_HEART_COMMENT",
                         "entityId": comment_id,
                         "commentId": comment_id}],
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Cookie": f"SAPISID={session}",
                     "Content-Type": "application/json",
                     "X-Origin": "https://studio.youtube.com"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            log.debug(f"Heart failed for {comment_id}: {e}")
            return False

    def pin_comment(self, comment_id: str, video_id: str) -> bool:
        """
        Pin a comment to the top of a video.
        YouTube Studio API action — requires session cookie.
        """
        session = os.getenv("YOUTUBE_STUDIO_SESSION", "")
        if not session:
            log.debug(f"  Pin queued (no Studio session): {comment_id}")
            return False
        url     = "https://studio.youtube.com/youtubei/v1/comment/perform_comment_action"
        payload = json.dumps({
            "actions": [{"action": "ACTION_PIN_COMMENT",
                         "entityId": comment_id,
                         "videoId":  video_id}],
        }).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Cookie": f"SAPISID={session}",
                     "Content-Type": "application/json",
                     "X-Origin": "https://studio.youtube.com"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status == 200
        except Exception as e:
            log.debug(f"Pin failed for {comment_id}: {e}")
            return False

    def heart_top_n(self, video_id: str, memory,
                     n: int = 20) -> Dict:
        """Heart the top N comments by like count on a video."""
        comments = memory.get_comments(youtube_id=video_id, order_by="likes", n=n)
        hearted  = 0
        for c in comments:
            if not c.get("is_hearted") and self.heart_comment(c["comment_id"]):
                memory.update_comment_status(c["comment_id"], is_hearted=True)
                hearted += 1
        return {"video_id": video_id, "hearted": hearted, "attempted": len(comments)}

    def execute_pending_replies(self, memory, limit: int = 20) -> Dict:
        """
        Pop pending reply jobs from memory and execute them.
        Respects daily quota (200 API replies/day).
        """
        pending = memory.get_pending_replies(limit=limit)
        replied = skipped = failed = 0
        for c in pending:
            reply_text = c.get("reply_text", "")
            comment_id = c.get("comment_id", "")
            if not reply_text or not comment_id:
                skipped += 1
                continue
            success = self.reply_to_comment(comment_id, reply_text,
                                             c.get("youtube_id", ""))
            if success:
                memory.update_comment_status(comment_id, reply_status="replied")
                replied += 1
            else:
                memory.update_comment_status(comment_id, reply_status="queued")
                failed += 1
        return {"replied": replied, "skipped": skipped, "failed": failed}


# ── 3. CommunityPostGenerator ────────────────────────────────────────────────

class CommunityPostGenerator:
    """
    Generates and schedules 3x/week community posts.
    Post types: poll | teaser | request | controversial | announcement

    Community post publishing requires YouTube Studio API (session cookie).
    Content is generated and stored in memory for review + manual/automated publish.
    """

    def __init__(self):
        self._claude_key = os.getenv("ANTHROPIC_API_KEY", "")

    def generate_poll(self, topics: List[str],
                       niche: str = "energy") -> Dict:
        """Generate a 2-option poll post."""
        if len(topics) < 2:
            topics = topics + [f"Deep dive into {niche} trends",
                                f"Beginner's guide to {niche}"]
        a, b = topics[0][:60], topics[1][:60]
        return {
            "type":        "poll",
            "content":     f"🗳️ My next video will be about:\n\nA) {a}\n\nB) {b}\n\nWhich one do YOU want to see? Vote below! 👇",
            "poll_options": [a, b],
            "engagement_goal": "vote_split",
        }

    def generate_teaser(self, upcoming_video: Dict) -> Dict:
        """Generate a behind-the-scenes teaser post."""
        title   = upcoming_video.get("title", "something big")
        hook    = upcoming_video.get("hook", "")
        teaser  = self._claude_generate(
            f"Write a short (2-3 sentence) YouTube community post teasing an "
            f"upcoming video titled '{title}'. Create curiosity without revealing "
            f"the key insight. Add 1-2 relevant emojis. Be conversational.",
        ) or (
            f"🎬 I've been working on something BIG...\n\n"
            f"My next video is about {title} and I think it might be one of my "
            f"best yet. The research alone took me 3 days.\n\nDropping soon 🔥"
        )
        return {
            "type":         "teaser",
            "content":      teaser,
            "poll_options": [],
            "engagement_goal": "curiosity",
            "video_title":  title,
        }

    def generate_request_post(self, topic: str) -> Dict:
        """Generate a 'help me make this video' post."""
        content = self._claude_generate(
            f"Write a short YouTube community post (3-4 sentences) saying you're "
            f"making a video about '{topic}' and asking viewers what specific "
            f"questions or sub-topics they want covered. End with a direct "
            f"question. Add 1-2 emojis. Conversational tone."
        ) or (
            f"🎥 I'm making a video about {topic}!\n\n"
            f"Before I film, I want to make sure I cover what YOU actually care "
            f"about. What are your biggest questions about this topic?\n\n"
            f"Drop them in the comments — the best ones make it into the video! 👇"
        )
        return {
            "type":         "request",
            "content":      content,
            "poll_options": [],
            "engagement_goal": "comment_volume",
            "topic":        topic,
        }

    def generate_controversial_take(self, niche: str = "energy") -> Dict:
        """Generate a debate-inviting take on the niche topic."""
        content = self._claude_generate(
            f"Write a short (2-3 sentence) YouTube community post sharing a "
            f"slightly controversial but defensible opinion about {niche}. "
            f"It should spark respectful debate. End with 'Agree or disagree?' "
            f"Keep it factual, not sensational. Add 1 emoji."
        ) or (
            f"🔥 Hot take: Most people are approaching {niche} completely wrong.\n\n"
            f"The conventional wisdom has it backwards — the data tells a very "
            f"different story. I'll explain in my next video.\n\n"
            f"Agree or disagree? Drop your take below 👇"
        )
        return {
            "type":         "controversial",
            "content":      content,
            "poll_options": [],
            "engagement_goal": "debate",
        }

    def schedule_weekly_posts(self, topics: List[str] = None,
                               niche: str = "energy",
                               memory=None,
                               upcoming_video: Dict = None) -> List[Dict]:
        """
        Build and schedule 3 community posts for the coming week.
        Returns list of post dicts (also saved to memory if provided).
        """
        now   = datetime.now(timezone.utc)
        posts = []
        type_cycle = [
            "poll",
            "request" if topics else "controversial",
            "teaser" if upcoming_video else "controversial",
        ]

        for i, (dow, hour) in enumerate(POST_SCHEDULES[:3]):
            # Find next occurrence of this day-of-week
            days_ahead = (dow - now.weekday()) % 7 or 7
            sched_dt   = (now + timedelta(days=days_ahead)).replace(
                hour=hour, minute=0, second=0, microsecond=0
            )

            post_type = type_cycle[i % len(type_cycle)]
            if post_type == "poll":
                post = self.generate_poll(topics or [f"{niche} innovations",
                                                      f"{niche} history"], niche)
            elif post_type == "teaser" and upcoming_video:
                post = self.generate_teaser(upcoming_video)
            elif post_type == "request" and topics:
                post = self.generate_request_post(topics[i % len(topics)])
            else:
                post = self.generate_controversial_take(niche)

            post["scheduled_at"]    = sched_dt.isoformat()
            post["status"]          = "scheduled"
            posts.append(post)

            if memory:
                try:
                    memory.save_community_post(post)
                except Exception as e:
                    log.debug(f"Post schedule save failed: {e}")

        return posts

    def publish_post(self, post: Dict) -> bool:
        """
        Publish to YouTube Community via Studio API (session required).
        Falls back to logging the content for manual posting.
        """
        session = os.getenv("YOUTUBE_STUDIO_SESSION", "")
        if not session:
            log.info(f"  Community post (manual publish required):\n"
                     f"  Type: {post.get('type')}  | "
                     f"Scheduled: {post.get('scheduled_at', 'now')}\n"
                     f"  Content: {post.get('content','')[:120]}…")
            return False

        url = "https://studio.youtube.com/youtubei/v1/community_posts/create_post"
        payload_data: Dict = {"contentText": {"runs": [{"text": post["content"]}]}}
        if post.get("poll_options"):
            payload_data["poll"] = {
                "choices": [{"text": o} for o in post["poll_options"]]
            }
        payload = json.dumps({"postParams": payload_data}).encode()
        req = urllib.request.Request(
            url, data=payload,
            headers={"Cookie":       f"SAPISID={session}",
                     "Content-Type": "application/json",
                     "X-Origin":     "https://studio.youtube.com"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return resp.status == 200
        except Exception as e:
            log.debug(f"Community post publish failed: {e}")
            return False

    def _claude_generate(self, prompt: str) -> Optional[str]:
        if not self._claude_key:
            return None
        try:
            import anthropic
            client = anthropic.Anthropic(api_key=self._claude_key)
            msg    = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=200,
                messages=[{"role": "user", "content": prompt}],
            )
            return msg.content[0].text.strip()
        except Exception as e:
            log.debug(f"Claude generate failed: {e}")
            return None


# ── 4. AudienceRetentionAnalyzer ─────────────────────────────────────────────

class AudienceRetentionAnalyzer:
    """
    Monitors subscriber counts, detects drops, identifies culprit videos,
    and auto-adjusts content strategy within 48 hours.

    Drop thresholds:
      Warning:  >0.5% drop in 24h
      Critical: >2.0% drop in 48h
    """

    DROP_WARNING_PCT  = 0.5
    DROP_CRITICAL_PCT = 2.0

    def detect_subscriber_drop(self, memory) -> Dict:
        """Compare current subscriber count vs 7-day history."""
        try:
            snapshots = memory.get_algorithm_snapshots(n=7)
        except Exception:
            snapshots = []

        result = {
            "drop_detected":  False,
            "severity":       "none",
            "change_pct":     0.0,
            "change_count":   0,
            "current":        0,
            "baseline":       0,
        }
        if len(snapshots) < 2:
            return result

        # Use revenue snapshots for subscriber data as fallback
        try:
            rev_snaps = memory.get_revenue_snapshots(days=7)
        except Exception:
            rev_snaps = []

        if len(rev_snaps) < 2:
            return result

        current  = rev_snaps[0].get("subscribers", 0)
        baseline = rev_snaps[-1].get("subscribers", 0)
        if baseline <= 0:
            return result

        change_pct   = (current - baseline) / baseline * 100
        change_count = current - baseline

        result.update({
            "current":      current,
            "baseline":     baseline,
            "change_pct":   round(change_pct, 3),
            "change_count": change_count,
        })

        if change_pct < -self.DROP_CRITICAL_PCT:
            result.update({"drop_detected": True, "severity": "critical"})
        elif change_pct < -self.DROP_WARNING_PCT:
            result.update({"drop_detected": True, "severity": "warning"})

        return result

    def identify_culprit_video(self, memory) -> Optional[Dict]:
        """
        Find the video most likely to have caused recent unsubscribes.
        Heuristic: video published within the drop window with lowest CTR
        or highest unsubscribe correlation.
        """
        try:
            recent = memory.get_video_stats(n=10)
        except Exception:
            return None

        if not recent:
            return None

        # Videos with CTR significantly below channel average
        avg_ctr = sum(v.get("ctr", 5.0) or 5.0 for v in recent) / len(recent)
        suspects = [
            v for v in recent
            if (v.get("ctr") or 5.0) < avg_ctr * 0.6   # >40% below average
        ]
        suspects.sort(key=lambda v: v.get("ctr") or 5.0)
        if suspects:
            return {
                "youtube_id":  suspects[0].get("youtube_id"),
                "title":       suspects[0].get("title"),
                "ctr":         suspects[0].get("ctr"),
                "avg_ctr":     round(avg_ctr, 2),
                "severity":    "low_ctr",
            }
        return None

    def generate_recovery_strategy(self, drop_analysis: Dict,
                                    culprit: Dict = None) -> Dict:
        """
        Build a 48-hour content strategy adjustment to recover from a drop.
        Returns action dict with immediate steps.
        """
        actions = []

        if culprit:
            actions.append({
                "action":   "revert_style",
                "detail":   f"Avoid style/topic of {culprit.get('title','culprit video')}",
                "urgency":  "immediate",
            })

        severity = drop_analysis.get("severity", "none")
        if severity == "critical":
            actions += [
                {"action": "post_apology_community",
                 "detail": "Post community update acknowledging recent content change",
                 "urgency": "within_6h"},
                {"action": "return_to_core_format",
                 "detail": "Next 3 videos: return to top-performing format",
                 "urgency": "within_48h"},
                {"action": "reactivate_dormant",
                 "detail": "Community post targeting dormant subscribers",
                 "urgency": "within_24h"},
            ]
        elif severity == "warning":
            actions += [
                {"action": "increase_frequency",
                 "detail": "Add one extra community post this week",
                 "urgency": "within_24h"},
                {"action": "revert_content_style",
                 "detail": "Next video: return to proven format",
                 "urgency": "within_48h"},
            ]

        return {
            "severity":  severity,
            "change_pct": drop_analysis.get("change_pct", 0),
            "actions":   actions,
            "recovery_deadline": (
                datetime.now(timezone.utc) + timedelta(hours=48)
            ).isoformat(),
        }

    def create_reengagement_post(self, niche: str = "energy") -> Dict:
        """Community post designed to reactivate dormant subscribers."""
        claude_key = os.getenv("ANTHROPIC_API_KEY", "")
        content    = None
        if claude_key:
            try:
                import anthropic
                client  = anthropic.Anthropic(api_key=claude_key)
                msg     = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=200,
                    messages=[{"role": "user", "content":
                        f"Write a YouTube community post (3-4 sentences) to "
                        f"re-engage subscribers who haven't seen recent videos "
                        f"about {niche}. Ask a direct question that requires "
                        f"a personal answer. Be warm and genuine. 1-2 emojis."}],
                )
                content = msg.content[0].text.strip()
            except Exception:
                pass

        if not content:
            content = (
                f"👋 Hey! I've noticed some of you haven't been around lately — "
                f"totally understand, life gets busy!\n\n"
                f"Quick question: what's the ONE thing about {niche} you most "
                f"want to understand better?\n\n"
                f"Your answer will shape my next few videos 👇"
            )

        return {
            "type":         "reengagement",
            "content":      content,
            "poll_options": [],
            "engagement_goal": "reactivation",
            "scheduled_at": datetime.now(timezone.utc).isoformat(),
            "status":       "scheduled",
        }

    def get_revenue_snapshots(self, memory, days: int = 7) -> List[Dict]:
        """Helper — gracefully fetch revenue snapshots."""
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            with memory._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM revenue_snapshots WHERE created_at >= ? "
                    "ORDER BY created_at DESC",
                    (cutoff,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


# ── 5. EngagementVelocityTracker ─────────────────────────────────────────────

class EngagementVelocityTracker:
    """
    Monitors newly-published videos and triggers rapid first-hour responses.
    The YouTube algorithm heavily weights engagement velocity in the first 60 minutes.

    Strategy:
      - Reply to the first 10 comments within 60 minutes
      - Reply before viewers leave the page (boosts notification re-opens)
      - Each reply = one more algorithmic engagement signal
    """

    REPLY_WINDOW_HOURS = 1
    FIRST_REPLIES      = 10

    def __init__(self):
        self._intel = CommentIntelligence()

    def get_new_video_comments(self, youtube_id: str,
                                published_at: str,
                                memory) -> List[Dict]:
        """
        Return comments posted within the first hour of a video's publication.
        Fetches live if no comments are cached.
        """
        # Try cache first
        try:
            cached = memory.get_comments(youtube_id=youtube_id,
                                          within_hours=self.REPLY_WINDOW_HOURS)
            if cached:
                return cached
        except Exception:
            pass

        # Fetch live
        comments = self._intel.fetch_video_comments(youtube_id, max_results=50)
        if not published_at:
            return comments[:self.FIRST_REPLIES]

        try:
            pub_dt  = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            cutoff  = pub_dt + timedelta(hours=self.REPLY_WINDOW_HOURS)
            recent  = [
                c for c in comments
                if c.get("published_at") and
                datetime.fromisoformat(
                    c["published_at"].replace("Z", "+00:00")
                ) <= cutoff
            ]
            return recent[:self.FIRST_REPLIES]
        except Exception:
            return comments[:self.FIRST_REPLIES]

    def queue_rapid_replies(self, youtube_id: str,
                             published_at: str,
                             video_context: Dict,
                             memory) -> Dict:
        """
        Fetch first-hour comments and queue replies for all of them.
        Returns queued count.
        """
        comments = self.get_new_video_comments(youtube_id, published_at, memory)
        queued   = 0
        for c in comments[:self.FIRST_REPLIES]:
            cat   = self._intel.categorize_comment(c.get("text", ""))
            reply = self._intel.generate_reply({**c, "category": cat},
                                                video_context)
            pin_s = self._intel.score_pin_candidate(c)
            try:
                memory.save_comment({
                    **c,
                    "youtube_id":   youtube_id,
                    "category":     cat,
                    "reply_text":   reply,
                    "pin_score":    pin_s,
                    "reply_status": "queued",
                })
                queued += 1
            except Exception:
                pass

        log.info(f"  Velocity: {len(comments)} first-hour comments on {youtube_id}, "
                 f"{queued} replies queued")
        return {"youtube_id": youtube_id, "comments_found": len(comments),
                "replies_queued": queued}

    def monitor_recent_uploads(self, memory, hours_back: int = 2) -> List[Dict]:
        """
        Check if any videos were published within the last N hours
        and still need rapid-reply treatment.
        """
        try:
            cutoff  = (datetime.now(timezone.utc) - timedelta(hours=hours_back)).isoformat()
            with memory._conn() as conn:
                rows = conn.execute(
                    "SELECT youtube_id, title, created_at FROM videos "
                    "WHERE created_at >= ? AND youtube_id IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT 5",
                    (cutoff,),
                ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []


# ── 6. CommunityEngine (orchestrator) ────────────────────────────────────────

class CommunityEngine:
    """
    Top-level orchestrator for the full community engagement cycle.
    Called every 6 hours from the scheduler.

    Full cycle:
      1. Velocity check   — rapid replies on videos < 2h old
      2. Comment cycle    — fetch + categorize + queue all recent video comments
      3. Execute replies  — post queued replies via YouTube API
      4. Community posts  — publish any due posts; schedule next week's batch
      5. Retention check  — detect drops, generate recovery strategy
    """

    def __init__(self, memory=None):
        self._memory    = memory
        self._intel     = CommentIntelligence()
        self._automator = CommentAutomator()
        self._poster    = CommunityPostGenerator()
        self._retention = AudienceRetentionAnalyzer()
        self._velocity  = EngagementVelocityTracker()

    # ── Main entry points ──────────────────────────────────────────────────

    def run_full_cycle(self, niche: str = "energy") -> Dict:
        """Execute all engagement modules and return combined result."""
        result: Dict = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "velocity":    {},
            "comments":    {},
            "replies":     {},
            "posts":       {},
            "retention":   {},
        }

        # 1. Velocity check for very recent uploads
        try:
            result["velocity"] = self._run_velocity_check()
        except Exception as e:
            log.error(f"Velocity check failed (non-fatal): {e}", exc_info=True)

        # 2. Comment cycle for all recent videos
        try:
            result["comments"] = self._run_comment_cycle()
        except Exception as e:
            log.error(f"Comment cycle failed (non-fatal): {e}", exc_info=True)

        # 3. Execute queued replies
        try:
            result["replies"] = self._automator.execute_pending_replies(
                self._memory, limit=50
            )
        except Exception as e:
            log.error(f"Reply execution failed (non-fatal): {e}", exc_info=True)

        # 4. Community posts
        try:
            result["posts"] = self._run_community_posts(niche)
        except Exception as e:
            log.error(f"Community posts failed (non-fatal): {e}", exc_info=True)

        # 5. Retention check
        try:
            result["retention"] = self._run_retention_check(niche)
        except Exception as e:
            log.error(f"Retention check failed (non-fatal): {e}", exc_info=True)

        return result

    def run_comment_cycle(self) -> Dict:
        return self._run_comment_cycle()

    def run_community_posts(self, niche: str = "energy") -> Dict:
        return self._run_community_posts(niche)

    def run_retention_check(self, niche: str = "energy") -> Dict:
        return self._run_retention_check(niche)

    def print_report(self, result: Dict) -> None:
        log.info("\n" + "=" * 60)
        log.info("COMMUNITY ENGINE REPORT")
        log.info("=" * 60)
        v = result.get("velocity", {})
        if v:
            log.info(f"  Velocity:  {v.get('uploads_checked', 0)} recent uploads | "
                     f"{v.get('replies_queued', 0)} rapid replies queued")
        c = result.get("comments", {})
        if c:
            log.info(f"  Comments:  {c.get('total_fetched', 0)} fetched | "
                     f"by category: {c.get('by_category', {})}")
        r = result.get("replies", {})
        if r:
            log.info(f"  Replies:   {r.get('replied', 0)} sent | "
                     f"{r.get('failed', 0)} failed (no OAuth)")
        p = result.get("posts", {})
        if p:
            log.info(f"  Posts:     {p.get('published', 0)} published | "
                     f"{p.get('scheduled', 0)} scheduled")
        ret = result.get("retention", {})
        if ret:
            drop = ret.get("drop", {})
            sev  = drop.get("severity", "none")
            pct  = drop.get("change_pct", 0)
            log.info(f"  Retention: {sev} (Δ{pct:+.2f}%) | "
                     f"actions: {len(ret.get('strategy', {}).get('actions', []))}")

    # ── Private helpers ───────────────────────────────────────────────────

    def _run_velocity_check(self) -> Dict:
        """Queue rapid replies for videos published in the last 2 hours."""
        recent   = self._velocity.monitor_recent_uploads(self._memory, hours_back=2)
        total_q  = 0
        for vid in recent:
            ctx = {"title": vid.get("title", ""), "youtube_id": vid.get("youtube_id")}
            r   = self._velocity.queue_rapid_replies(
                vid["youtube_id"], vid.get("created_at", ""), ctx, self._memory
            )
            total_q += r.get("replies_queued", 0)
        return {"uploads_checked": len(recent), "replies_queued": total_q}

    def _run_comment_cycle(self) -> Dict:
        """Fetch and process comments for the 5 most recent videos."""
        try:
            recent_videos = self._memory.get_video_stats(n=5)
        except Exception:
            recent_videos = []

        total_fetched  = 0
        by_category: Dict[str, int] = {}
        pin_actions    = 0

        for vid in recent_videos:
            yt_id = vid.get("youtube_id", "")
            if not yt_id:
                continue
            ctx    = {"title": vid.get("title", ""), "channel_name": ""}
            batch  = self._intel.batch_process_video(yt_id, ctx, self._memory)
            total_fetched += batch.get("fetched", 0)
            for cat, cnt in batch.get("by_category", {}).items():
                by_category[cat] = by_category.get(cat, 0) + cnt

            # Heart top 20 and pin the best discussion-starter
            self._automator.heart_top_n(yt_id, self._memory, n=20)
            pin = batch.get("pin_candidate")
            if pin:
                self._automator.pin_comment(pin.get("comment_id", ""), yt_id)
                pin_actions += 1

        return {"total_fetched": total_fetched, "by_category": by_category,
                "pin_actions": pin_actions, "videos_processed": len(recent_videos)}

    def _run_community_posts(self, niche: str) -> Dict:
        """Publish due posts and schedule next week's batch."""
        published = skipped = 0

        # Publish any posts that are due now
        try:
            due = self._memory.get_due_community_posts()
        except Exception:
            due = []

        for post in due:
            ok = self._poster.publish_post(post)
            if ok:
                try:
                    self._memory.update_community_post_status(post["id"], "published")
                except Exception:
                    pass
                published += 1
            else:
                skipped += 1

        # Schedule next week's batch if fewer than 3 future posts exist
        try:
            future = self._memory.get_scheduled_community_posts(n=10)
        except Exception:
            future = []

        scheduled = 0
        if len(future) < 3:
            recent_gaps = []
            try:
                gaps = self._memory.get_content_gaps(status="open", n=3)
                recent_gaps = [g["topic"] for g in gaps]
            except Exception:
                pass

            new_posts = self._poster.schedule_weekly_posts(
                topics=recent_gaps or None,
                niche=niche,
                memory=self._memory,
            )
            scheduled = len(new_posts)

        return {"published": published, "skipped_no_auth": skipped,
                "scheduled": scheduled}

    def _run_retention_check(self, niche: str) -> Dict:
        drop     = self._retention.detect_subscriber_drop(self._memory)
        culprit  = None
        strategy = {}

        if drop.get("drop_detected"):
            culprit  = self._retention.identify_culprit_video(self._memory)
            strategy = self._retention.generate_recovery_strategy(drop, culprit)
            log.warning(f"  ⚠️  Subscriber drop: {drop.get('severity')} "
                        f"({drop.get('change_pct'):+.2f}%)")

            # Save retention event
            try:
                self._memory.save_retention_event({
                    "event_type":      drop["severity"],
                    "subscriber_count": drop.get("current", 0),
                    "change_count":     drop.get("change_count", 0),
                    "change_pct":       drop.get("change_pct", 0),
                    "culprit_video":    culprit.get("youtube_id") if culprit else "",
                    "strategy_applied": strategy,
                })
            except Exception:
                pass

            # Auto-post reengagement community post
            if drop.get("severity") == "critical":
                re_post = self._retention.create_reengagement_post(niche)
                try:
                    self._memory.save_community_post(re_post)
                except Exception:
                    pass

        return {"drop": drop, "culprit": culprit, "strategy": strategy}
