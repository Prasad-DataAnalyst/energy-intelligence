"""
channel_manager/comment_monitor.py — DriftWire326 Module 24
Pulls new comments daily at 20:00 ET.
Classifies comments and generates suggested replies via Claude.
Adds disclaimer to any financial advice requests.
Flags spam automatically via YouTube API moderation.

Comments saved to: logs/comments/YYYY-MM-DD.json
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import anthropic

from config.settings import settings
from monitor.usage_ledger import record

logger = logging.getLogger(__name__)

COMMENTS_DIR = settings.logs_dir / "comments"
COMMENTS_DIR.mkdir(parents=True, exist_ok=True)

# Classification labels
CLASS_SPAM = "spam"
CLASS_FINANCIAL_ADVICE = "financial_advice_request"
CLASS_POSITIVE = "positive"
CLASS_QUESTION = "question"
CLASS_GENERAL = "general"

# Spam signal patterns — fast regex pre-filter before Claude classification
_SPAM_PATTERNS = [
    re.compile(r"(subscribe.*back|sub4sub|follow.*back)", re.IGNORECASE),
    re.compile(r"(dm me|whatsapp|telegram|signal).*invest", re.IGNORECASE),
    re.compile(r"(guaranteed profit|double your money|passive income.*\$)", re.IGNORECASE),
    re.compile(r"(click.*link.*bio|link in bio.*trade|trade signals)", re.IGNORECASE),
    re.compile(r"(forex|crypto.*signal|binary option).*profit", re.IGNORECASE),
    re.compile(r"i (made|earned|profited).*\$\d{3,}", re.IGNORECASE),
    re.compile(r"contact.*expert.*trader", re.IGNORECASE),
]

# Canned reply templates (Claude fills in the gaps)
_FINANCIAL_ADVICE_DISCLAIMER = (
    "\n\n⚠️ Reminder: Nothing on this channel is financial advice. "
    "Always consult a licensed financial advisor before making investment decisions. "
    f"{settings.disclaimer_text}"
)

_CLASSIFY_PROMPT = """Classify this YouTube comment from a finance channel (@DriftWire326) into exactly one category.

Comment: "{comment_text}"

Categories:
- spam: promotional, bot-like, offers financial services, sub4sub, suspicious links
- financial_advice_request: asking "should I buy/sell X?", "is X a good investment?", seeking personal investment guidance
- positive: compliment, thank you, likes the content
- question: genuine question about market concepts, economic data, or channel content
- general: other on-topic comment

Return ONLY the category label (one word)."""

_REPLY_PROMPT = """You are responding to a YouTube comment for @DriftWire326, a finance education channel for Gen Z and Millennial investors.

Comment: "{comment_text}"
Category: {classification}

Write a short, friendly reply (max 200 characters).
- Tone: knowledgeable but approachable, like a smart friend
- Do NOT give specific investment advice
- For financial_advice_request: acknowledge the question but redirect to doing their own research
- For question: give a brief helpful answer about the concept
- For positive: thank them warmly and encourage engagement
- Do NOT include any URLs or emoji spam
- If financial_advice_request, end with the disclaimer (it will be appended automatically, don't add it yourself)

Return ONLY the reply text."""


@dataclass
class Comment:
    comment_id: str
    video_id: str
    author: str
    author_channel_id: str
    text: str
    published_at: str
    like_count: int = 0
    classification: Optional[str] = None
    suggested_reply: Optional[str] = None
    is_spam: bool = False
    reply_posted: bool = False

    def to_dict(self) -> dict:
        return {
            "comment_id": self.comment_id,
            "video_id": self.video_id,
            "author": self.author,
            "author_channel_id": self.author_channel_id,
            "text": self.text,
            "published_at": self.published_at,
            "like_count": self.like_count,
            "classification": self.classification,
            "suggested_reply": self.suggested_reply,
            "is_spam": self.is_spam,
            "reply_posted": self.reply_posted,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Comment":
        """
        Safe factory for raw JSON dicts. Ignores unknown keys and supplies
        defaults for missing optional fields, so schema drift in saved
        comment files can never raise TypeError.
        """
        return cls(
            comment_id=data.get("comment_id", ""),
            video_id=data.get("video_id", ""),
            author=data.get("author", ""),
            author_channel_id=data.get("author_channel_id", ""),
            text=data.get("text", ""),
            published_at=data.get("published_at", ""),
            like_count=int(data.get("like_count", 0) or 0),
            classification=data.get("classification"),
            suggested_reply=data.get("suggested_reply"),
            is_spam=bool(data.get("is_spam", False)),
            reply_posted=bool(data.get("reply_posted", False)),
        )


def _get_youtube_service():
    from uploader.uploader import _get_authenticated_service
    return _get_authenticated_service()


def _get_claude_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _is_spam_regex(text: str) -> bool:
    """Fast regex pre-filter — True if any spam pattern matches."""
    return any(p.search(text) for p in _SPAM_PATTERNS)


class CommentMonitor:
    """Fetches, classifies, and generates replies for YouTube comments."""

    def __init__(self, youtube_service=None):
        self._svc = youtube_service

    def _service(self):
        if self._svc is None:
            self._svc = _get_youtube_service()
        return self._svc

    # ── Fetching ─────────────────────────────────────────────────────────────

    def fetch_new_comments(
        self,
        video_ids: Optional[list[str]] = None,
        max_results: int = 50,
    ) -> list[Comment]:
        """
        Fetch top-level comments from specified videos (or most recent channel videos).
        Returns list of Comment objects, newest first.
        """
        if video_ids is None:
            video_ids = self._list_recent_video_ids()

        comments: list[Comment] = []
        for video_id in video_ids:
            batch = self._fetch_comments_for_video(video_id, max_results // max(len(video_ids), 1))
            comments.extend(batch)

        # Sort newest first
        comments.sort(key=lambda c: c.published_at, reverse=True)
        logger.info("Fetched %d comments from %d video(s)", len(comments), len(video_ids))
        return comments

    def _list_recent_video_ids(self, max_videos: int = 10) -> list[str]:
        """List the channel's most recent video IDs."""
        try:
            channel_id = settings.channel_id
            response = self._service().search().list(
                part="id",
                channelId=channel_id,
                type="video",
                order="date",
                maxResults=max_videos,
            ).execute()
            return [item["id"]["videoId"] for item in response.get("items", [])]
        except Exception as exc:
            logger.error("Failed to list recent videos: %s", exc)
            return []

    def _fetch_comments_for_video(self, video_id: str, max_results: int = 20) -> list[Comment]:
        """Fetch top-level comments for a single video."""
        comments: list[Comment] = []
        try:
            response = self._service().commentThreads().list(
                part="snippet",
                videoId=video_id,
                maxResults=min(max_results, 100),
                order="time",
                textFormat="plainText",
            ).execute()

            for item in response.get("items", []):
                top = item["snippet"]["topLevelComment"]["snippet"]
                comments.append(Comment(
                    comment_id=item["snippet"]["topLevelComment"]["id"],
                    video_id=video_id,
                    author=top.get("authorDisplayName", ""),
                    author_channel_id=top.get("authorChannelId", {}).get("value", ""),
                    text=top.get("textDisplay", ""),
                    published_at=top.get("publishedAt", ""),
                    like_count=top.get("likeCount", 0),
                ))
        except Exception as exc:
            logger.error("Failed to fetch comments for video %s: %s", video_id, exc)
        return comments

    # ── Classification ────────────────────────────────────────────────────────

    def classify_comment(self, text: str) -> str:
        """
        Classify a comment into: spam | financial_advice_request | positive | question | general.
        Uses regex fast-path for obvious spam, then Claude for nuanced cases.
        """
        if _is_spam_regex(text):
            return CLASS_SPAM

        try:
            client = _get_claude_client()
            message = client.messages.create(
                model=settings.claude_model,
                max_tokens=10,
                messages=[{
                    "role": "user",
                    "content": _CLASSIFY_PROMPT.format(comment_text=text[:500]),
                }],
            )
            record(message, "comment_monitor")
            label = message.content[0].text.strip().lower()
            if label not in {CLASS_SPAM, CLASS_FINANCIAL_ADVICE, CLASS_POSITIVE,
                             CLASS_QUESTION, CLASS_GENERAL}:
                label = CLASS_GENERAL
            return label
        except Exception as exc:
            logger.warning("Comment classification failed: %s — defaulting to general", exc)
            return CLASS_GENERAL

    # ── Reply generation ──────────────────────────────────────────────────────

    def generate_reply(self, comment: Comment) -> str:
        """
        Generate a suggested reply for a comment via Claude.
        Financial advice requests always get the disclaimer appended.
        Max 500 chars total after disclaimer.
        """
        classification = comment.classification or CLASS_GENERAL
        try:
            client = _get_claude_client()
            message = client.messages.create(
                model=settings.claude_model,
                max_tokens=100,
                messages=[{
                    "role": "user",
                    "content": _REPLY_PROMPT.format(
                        comment_text=comment.text[:400],
                        classification=classification,
                    ),
                }],
            )
            record(message, "comment_monitor")
            reply = message.content[0].text.strip()
        except Exception as exc:
            logger.warning("Reply generation failed: %s", exc)
            reply = "Thanks for watching! 🙏"

        # Always append disclaimer for financial advice requests
        if classification == CLASS_FINANCIAL_ADVICE:
            reply = reply + _FINANCIAL_ADVICE_DISCLAIMER
            # YouTube comment limit is 10,000 chars — but keep it short
            if len(reply) > 900:
                reply = reply[:900] + "..."

        return reply

    # ── Moderation ────────────────────────────────────────────────────────────

    def flag_spam(self, comment_id: str) -> bool:
        """
        Set comment moderation status to 'rejected' (hides comment from public view).
        Returns True on success.
        """
        try:
            self._service().comments().setModerationStatus(
                id=comment_id,
                moderationStatus="rejected",
                banAuthor=False,
            ).execute()
            logger.info("Spam flagged and hidden: comment %s", comment_id)
            return True
        except Exception as exc:
            logger.error("Failed to flag comment %s as spam: %s", comment_id, exc)
            return False

    # ── Persistence ──────────────────────────────────────────────────────────

    def save_comments(
        self,
        comments: list[Comment],
        date_str: Optional[str] = None,
    ) -> Path:
        """Save comment list to logs/comments/YYYY-MM-DD.json."""
        if date_str is None:
            date_str = date.today().isoformat()
        out_path = COMMENTS_DIR / f"{date_str}.json"
        out_path.write_text(
            json.dumps([c.to_dict() for c in comments], indent=2)
        )
        logger.info("Comments saved → %s (%d comments)", out_path.name, len(comments))
        return out_path

    # ── Scheduled entrypoint ─────────────────────────────────────────────────

    def run_daily_check(
        self,
        video_ids: Optional[list[str]] = None,
    ) -> list[Comment]:
        """
        Main entrypoint called daily at 20:00 ET.
        Fetches → classifies → generates replies → flags spam → saves.
        Returns the processed comment list.
        """
        logger.info("Starting daily comment check")
        comments = self.fetch_new_comments(video_ids=video_ids)

        spam_flagged = 0
        for comment in comments:
            comment.classification = self.classify_comment(comment.text)

            if comment.classification == CLASS_SPAM:
                comment.is_spam = True
                self.flag_spam(comment.comment_id)
                spam_flagged += 1
            else:
                comment.suggested_reply = self.generate_reply(comment)

        self.save_comments(comments)
        logger.info(
            "Daily comment check complete: %d total | %d spam flagged | %d replied",
            len(comments),
            spam_flagged,
            len(comments) - spam_flagged,
        )
        return comments

    def get_pending_replies(self, date_str: Optional[str] = None) -> list[Comment]:
        """
        Return comments from a given day that have suggested replies but
        haven't been posted yet. Useful for operator review before posting.
        """
        if date_str is None:
            date_str = date.today().isoformat()
        data_file = COMMENTS_DIR / f"{date_str}.json"
        if not data_file.exists():
            return []

        try:
            raw = json.loads(data_file.read_text())
            return [
                Comment.from_dict(c) for c in raw
                if c.get("suggested_reply") and not c.get("reply_posted") and not c.get("is_spam")
            ]
        except Exception as exc:
            logger.error("Failed to load comments for %s: %s", date_str, exc)
            return []
