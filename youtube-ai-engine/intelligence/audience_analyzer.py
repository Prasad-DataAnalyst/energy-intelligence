"""
intelligence/audience_analyzer.py — Comment & Audience Analysis
================================================================
Mines YouTube comments to understand what the audience actually wants:
  - Sentiment analysis on recent comments
  - Pain point extraction ("why didn't you cover X?", "I wish…")
  - Question extraction → future video seeds
  - Desire mapping: what viewers reward with likes + engagement
  - Subscriber intent signals
"""
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger("brain.audience_analyzer")

HERE = Path(__file__).parent.parent

PAIN_PATTERNS = [
    r"wish(?:ed)? you(?:'d| had)? (?:covered|talked|mentioned|included)\s+(.{10,60})",
    r"what about\s+(.{5,50})\??",
    r"you forgot\s+(.{5,50})",
    r"missing\s+(.{5,50})",
    r"can you (?:do|make|cover|explain)\s+(.{5,60})\??",
    r"next (?:video|time) (?:cover|do|make)\s+(.{5,60})",
    r"please (?:do|cover|make|explain)\s+(.{5,60})",
]

QUESTION_PATTERN = re.compile(
    r"([A-Z][^.!?]{15,120}\?)", re.MULTILINE
)


class AudienceAnalyzer:
    def __init__(self, memory=None):
        from core.memory import get_memory
        self.memory = memory or get_memory()

    # ── Comment fetching ───────────────────────────────────────────────────

    def _fetch_comments(self, youtube_video_id: str,
                         max_results: int = 100) -> List[str]:
        """Fetch top-level comments via YouTube Data API v3."""
        api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not api_key:
            log.debug("No YOUTUBE_DATA_API_KEY — skipping comment fetch")
            return []
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            r = sess.get(
                "https://www.googleapis.com/youtube/v3/commentThreads",
                params={
                    "part":        "snippet",
                    "videoId":     youtube_video_id,
                    "maxResults":  min(max_results, 100),
                    "order":       "relevance",
                    "key":         api_key,
                },
                timeout=20,
            )
            if not r.ok:
                log.debug(f"Comments API error: {r.status_code}")
                return []
            comments = []
            for item in r.json().get("items", []):
                text = (item.get("snippet", {})
                            .get("topLevelComment", {})
                            .get("snippet", {})
                            .get("textDisplay", ""))
                if text:
                    comments.append(text)
            return comments
        except Exception as e:
            log.debug(f"Comment fetch failed: {e}")
            return []

    # ── Analysis ───────────────────────────────────────────────────────────

    @staticmethod
    def _sentiment(text: str) -> str:
        """Trivial keyword-based sentiment: positive / negative / neutral."""
        pos = len(re.findall(
            r"\b(great|amazing|excellent|love|best|perfect|awesome|helpful)\b",
            text, re.I))
        neg = len(re.findall(
            r"\b(bad|terrible|wrong|boring|useless|waste|hate|worst)\b",
            text, re.I))
        if pos > neg:
            return "positive"
        if neg > pos:
            return "negative"
        return "neutral"

    @staticmethod
    def _extract_pain_points(comments: List[str]) -> List[str]:
        pains = []
        for c in comments:
            for pat in PAIN_PATTERNS:
                for m in re.finditer(pat, c, re.I):
                    pains.append(m.group(1).strip())
        return pains

    @staticmethod
    def _extract_questions(comments: List[str]) -> List[str]:
        qs = []
        for c in comments:
            for m in QUESTION_PATTERN.findall(c):
                q = m.strip()
                if 20 < len(q) < 120:
                    qs.append(q)
        return qs

    @staticmethod
    def _top_keywords(comments: List[str], n: int = 20) -> List[str]:
        STOPWORDS = {
            "the", "a", "an", "is", "it", "to", "of", "in", "for", "and",
            "or", "but", "this", "that", "was", "are", "you", "i", "we",
            "my", "your", "me", "he", "she", "they", "has", "have", "had",
            "with", "at", "on", "by", "be", "not", "from", "what", "so",
            "do", "did", "can", "just", "if", "its", "as", "all", "also",
        }
        words = []
        for c in comments:
            words.extend(re.findall(r"[a-z]{4,}", c.lower()))
        counts = Counter(w for w in words if w not in STOPWORDS)
        return [w for w, _ in counts.most_common(n)]

    # ── Main API ───────────────────────────────────────────────────────────

    def analyse_video(self, youtube_video_id: str) -> Dict:
        """Full audience analysis for a single video."""
        comments = self._fetch_comments(youtube_video_id)
        if not comments:
            return {"video_id": youtube_video_id, "comments": 0}

        sentiments = [self._sentiment(c) for c in comments]
        pos_pct = sentiments.count("positive") / len(sentiments)
        neg_pct = sentiments.count("negative") / len(sentiments)

        pains     = self._extract_pain_points(comments)
        questions = self._extract_questions(comments)
        keywords  = self._top_keywords(comments)

        result = {
            "video_id":       youtube_video_id,
            "comments":       len(comments),
            "sentiment_pos":  round(pos_pct, 3),
            "sentiment_neg":  round(neg_pct, 3),
            "pain_points":    list(dict.fromkeys(pains))[:10],  # deduped
            "questions":      list(dict.fromkeys(questions))[:10],
            "top_keywords":   keywords,
            "analysed_at":    datetime.now(timezone.utc).isoformat(),
        }

        log.info(
            f"Audience analysis {youtube_video_id}: "
            f"{len(comments)} comments  +{pos_pct:.0%} positive  "
            f"{len(pains)} pain points  {len(questions)} questions"
        )
        return result

    def build_desire_map(self, video_ids: List[str]) -> Dict:
        """
        Aggregate audience analysis across multiple videos into a
        'desire map' — ranked list of what the audience wants more of.
        """
        all_pains:     List[str] = []
        all_questions: List[str] = []
        all_keywords:  List[str] = []

        for vid in video_ids:
            result = self.analyse_video(vid)
            all_pains.extend(result.get("pain_points", []))
            all_questions.extend(result.get("questions", []))
            all_keywords.extend(result.get("top_keywords", []))
            time.sleep(0.5)

        pain_counts  = Counter(all_pains)
        kw_counts    = Counter(all_keywords)

        desire_map = {
            "top_pain_points": [p for p, _ in pain_counts.most_common(15)],
            "top_questions":   list(dict.fromkeys(all_questions))[:15],
            "top_keywords":    [w for w, _ in kw_counts.most_common(20)],
            "video_count":     len(video_ids),
            "generated_at":    datetime.now(timezone.utc).isoformat(),
        }

        # Save to data/
        out = HERE / "data" / "desire_map.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(desire_map, f, indent=2)

        log.info(f"Desire map saved → {out}")
        return desire_map

    def get_video_seeds(self, n: int = 10) -> List[str]:
        """Return top question-based video topic seeds from the desire map."""
        map_path = HERE / "data" / "desire_map.json"
        if not map_path.exists():
            return []
        with open(map_path) as f:
            dm = json.load(f)
        seeds = dm.get("top_questions", [])[:n]
        log.info(f"Audience video seeds: {seeds}")
        return seeds
