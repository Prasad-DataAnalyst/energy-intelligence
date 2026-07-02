"""
channel_manager/content_tracker.py — DriftWire326 Module 32
ContentTracker: prevents duplicate or near-duplicate content within a 14-day window.
Tracks published topics by title keyword fingerprint and similarity score.
State persisted atomically to logs/content_history.json.
"""
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

from config.settings import settings

logger = logging.getLogger(__name__)

_HISTORY_PATH = settings.logs_dir / "content_history.json"
_DEDUP_WINDOW_DAYS = 14
_SIMILARITY_THRESHOLD = 0.60  # Jaccard similarity above this → duplicate


def _keyword_fingerprint(text: str) -> set[str]:
    """Extract meaningful keywords from a title/topic string."""
    # Lowercase, remove punctuation, split on whitespace
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    words = text.split()
    # Filter stopwords
    _STOPWORDS = {
        "a", "an", "the", "is", "are", "was", "were", "be", "been",
        "being", "in", "on", "at", "to", "for", "of", "and", "or",
        "but", "not", "with", "as", "by", "from", "this", "that",
        "today", "market", "recap", "update", "weekly", "sunday",
        "monday", "tuesday", "wednesday", "thursday", "friday",
    }
    return {w for w in words if w not in _STOPWORDS and len(w) >= 3}


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Jaccard similarity: |A ∩ B| / |A ∪ B|. Returns 0.0 if both sets empty."""
    if not set_a and not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


@dataclass
class ContentRecord:
    topic: str
    title: str
    published_date: str
    video_id: Optional[str] = None
    fingerprint: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "topic": self.topic,
            "title": self.title,
            "published_date": self.published_date,
            "video_id": self.video_id,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ContentRecord":
        return cls(
            topic=d.get("topic", ""),
            title=d.get("title", ""),
            published_date=d.get("published_date", ""),
            video_id=d.get("video_id"),
            fingerprint=d.get("fingerprint", []),
        )

    @property
    def keyword_set(self) -> set[str]:
        return set(self.fingerprint)


class ContentTracker:
    """
    14-day deduplication window for DriftWire326 content.
    Checks new topics/titles against recent history before generation.
    """

    def __init__(self, history_path: Optional[Path] = None):
        self._path = history_path or _HISTORY_PATH
        self._records: list[ContentRecord] = []
        self._load()

    # ── Persistence ──────────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._path.exists():
            self._records = []
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = [ContentRecord.from_dict(r) for r in raw]
            self._prune_old()
        except Exception as exc:
            logger.warning("ContentTracker load failed: %s — starting with empty history", exc)
            self._records = []

    def _save(self) -> None:
        tmp = self._path.with_suffix(".json.tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps([r.to_dict() for r in self._records], indent=2),
            encoding="utf-8",
        )
        os.replace(tmp, self._path)
        logger.debug("ContentTracker history saved (%d records)", len(self._records))

    def _prune_old(self) -> None:
        """Remove records older than DEDUP_WINDOW_DAYS."""
        cutoff = (date.today() - timedelta(days=_DEDUP_WINDOW_DAYS)).isoformat()
        before = len(self._records)
        self._records = [r for r in self._records if r.published_date >= cutoff]
        removed = before - len(self._records)
        if removed:
            logger.debug("ContentTracker: pruned %d records older than %s", removed, cutoff)

    # ── Public interface ──────────────────────────────────────────────────────

    def is_duplicate(self, topic: str, title: str = "") -> tuple[bool, float]:
        """
        Check if a topic+title is too similar to recent content.

        Returns:
            (is_duplicate: bool, max_similarity: float)
            is_duplicate is True if max_similarity ≥ _SIMILARITY_THRESHOLD.
        """
        candidate_fingerprint = _keyword_fingerprint(f"{topic} {title}")
        max_sim = 0.0
        for record in self._records:
            sim = _jaccard_similarity(candidate_fingerprint, record.keyword_set)
            if sim > max_sim:
                max_sim = sim

        is_dup = max_sim >= _SIMILARITY_THRESHOLD
        if is_dup:
            logger.info(
                "Duplicate detected for topic '%s' (similarity=%.2f)", topic[:60], max_sim
            )
        return is_dup, round(max_sim, 3)

    def record_content(
        self,
        topic: str,
        title: str = "",
        video_id: Optional[str] = None,
    ) -> ContentRecord:
        """
        Record a new piece of content into the history.
        Call this after a video is successfully uploaded.
        """
        fingerprint = list(_keyword_fingerprint(f"{topic} {title}"))
        record = ContentRecord(
            topic=topic,
            title=title,
            published_date=date.today().isoformat(),
            video_id=video_id,
            fingerprint=fingerprint,
        )
        self._records.append(record)
        self._save()
        logger.info("Content recorded: '%s' (%s)", title[:60] or topic[:60], date.today())
        return record

    def get_recent(self, days: int = _DEDUP_WINDOW_DAYS) -> list[ContentRecord]:
        """Return records from the last N days, newest first."""
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        recent = [r for r in self._records if r.published_date >= cutoff]
        return sorted(recent, key=lambda r: r.published_date, reverse=True)

    def get_recent_topics(self, days: int = _DEDUP_WINDOW_DAYS) -> list[str]:
        """Return list of recent topic strings for use in script prompts."""
        return [r.topic for r in self.get_recent(days)]

    def clear_history(self) -> None:
        """Wipe all history. Use only in tests or manual resets."""
        self._records = []
        self._save()
        logger.warning("ContentTracker history cleared")
