"""
intelligence/competitor_dominator.py — Deep Competitor Intelligence Engine
===========================================================================
Goes far beyond competitor_spy.py.  Provides:

  TitleDecoder      — NLP pattern analysis + AI-powered title alternatives
  CommentMiner      — YouTube comment complaint/question extraction
  ScheduleMapper    — Upload-schedule reverse-engineering
  GapExploiter      — Uncovered-topic and under-served audience detection
  TitleWarfare      — Real-time RSS monitoring + counter-title generation
  AudiencePoacher   — Engagement-comment drafts (human review required)
  CompetitorDominator — Main orchestrator

All network calls are wrapped in try/except and return [] or {} on failure.
"""
import json
import logging
import os
import re
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.competitor_dominator")

HERE = Path(__file__).parent.parent

# ── Power-word lists ──────────────────────────────────────────────────────────

POWER_WORDS = [
    "exposed", "secret", "never", "always", "worst", "best",
    "actually", "surprising", "shocking", "hidden", "revealed",
    "truth", "lies", "dangerous", "critical", "urgent", "ultimate",
    "proven", "guaranteed", "instant", "massive", "insane",
]

EMOTIONAL_TRIGGERS = {
    "fear":      ["danger", "risk", "threat", "warning", "mistake", "fail",
                  "wrong", "avoid", "stop", "never", "worst", "crisis"],
    "curiosity": ["secret", "hidden", "real", "truth", "actually", "why",
                  "how", "what", "revealed", "surprising", "unknown", "mystery"],
    "greed":     ["free", "money", "earn", "profit", "rich", "save",
                  "best", "top", "ultimate", "maximum", "bonus"],
    "urgency":   ["now", "today", "immediately", "urgent", "last", "final",
                  "deadline", "limited", "before", "running out"],
}


# ─────────────────────────────────────────────────────────────────────────────
# TitleDecoder
# ─────────────────────────────────────────────────────────────────────────────

class TitleDecoder:
    """NLP-style title pattern analysis and AI-powered title generation."""

    # ── Structural pattern detection ──────────────────────────────────────

    @staticmethod
    def _detect_structural_patterns(titles: List[str]) -> Dict[str, int]:
        patterns: Dict[str, int] = defaultdict(int)
        for t in titles:
            tl = t.lower()
            if re.search(r"^\d+\s+\w", t) or re.search(r"\btop\s+\d+\b", tl):
                patterns["listicle"] += 1
            if t.rstrip().endswith("?"):
                patterns["question"] += 1
            if tl.startswith("how to") or tl.startswith("how i "):
                patterns["how_to"] += 1
            if " vs " in tl or " vs. " in tl:
                patterns["versus"] += 1
            if any(w in tl for w in ("secret", "expose", "exposed", "hidden")):
                patterns["secret_expose"] += 1
            if "mistake" in tl or "wrong" in tl or "stop doing" in tl:
                patterns["mistake"] += 1
        return dict(patterns)

    @staticmethod
    def _detect_power_words(titles: List[str]) -> Dict[str, int]:
        found: Dict[str, int] = defaultdict(int)
        for t in titles:
            tl = t.lower()
            for pw in POWER_WORDS:
                if pw in tl:
                    found[pw] += 1
        return {k: v for k, v in found.items() if v > 0}

    @staticmethod
    def _detect_emotional_triggers(titles: List[str]) -> Dict[str, int]:
        trigger_counts: Dict[str, int] = defaultdict(int)
        for t in titles:
            tl = t.lower()
            for emotion, words in EMOTIONAL_TRIGGERS.items():
                if any(w in tl for w in words):
                    trigger_counts[emotion] += 1
        return dict(trigger_counts)

    @staticmethod
    def _case_ratio(titles: List[str]) -> Dict[str, float]:
        n = max(len(titles), 1)
        all_caps   = sum(1 for t in titles if t.upper() == t and t.strip())
        title_case = sum(1 for t in titles if t.istitle())
        sentence   = n - all_caps - title_case
        return {
            "all_caps":    round(all_caps / n, 3),
            "title_case":  round(title_case / n, 3),
            "sentence":    round(max(sentence, 0) / n, 3),
        }

    @staticmethod
    def _word_count_stats(titles: List[str]) -> Dict[str, Any]:
        if not titles:
            return {"avg": 0, "min": 0, "max": 0, "sweet_spot": 0}
        counts = [len(t.split()) for t in titles]
        avg = sum(counts) / len(counts)
        # sweet spot = most common word count band
        bands = Counter(c // 2 * 2 for c in counts)
        sweet_spot = bands.most_common(1)[0][0] + 1 if bands else round(avg)
        return {
            "avg":        round(avg, 1),
            "min":        min(counts),
            "max":        max(counts),
            "sweet_spot": sweet_spot,
        }

    def decode_formula(self, titles: List[str]) -> Dict:
        """
        Full NLP pattern analysis of a title corpus.

        Returns:
            formula_template, patterns, power_words, avg_word_count,
            sweet_spot_words, case_style
        """
        if not titles:
            return {
                "formula_template":  "No titles provided",
                "patterns":          {},
                "power_words":       {},
                "emotional_triggers": {},
                "avg_word_count":    0,
                "sweet_spot_words":  7,
                "case_style":        "sentence",
                "sample_size":       0,
            }

        structural = self._detect_structural_patterns(titles)
        power      = self._detect_power_words(titles)
        emotions   = self._detect_emotional_triggers(titles)
        cases      = self._case_ratio(titles)
        wc         = self._word_count_stats(titles)

        # Derive a formula template from top patterns
        top_pattern = max(structural, key=structural.get) if structural else "unknown"
        template_map = {
            "listicle":      "[Number] [Noun Phrase] That [Benefit/Outcome]",
            "question":      "Why/How [Topic] [Surprising Claim]?",
            "how_to":        "How To [Verb] [Topic] (In [Timeframe/Method])",
            "versus":        "[Option A] vs [Option B]: [Verdict/Surprise]",
            "secret_expose": "The [Hidden/Secret] Truth About [Topic]",
            "mistake":       "[Number] [Topic] Mistakes You're Still Making",
        }
        formula_template = template_map.get(top_pattern,
                                             "[Hook Word] [Topic]: [Benefit]")

        # Dominant case style
        case_style = max(cases, key=cases.get)

        return {
            "formula_template":   formula_template,
            "patterns":           structural,
            "power_words":        power,
            "emotional_triggers": emotions,
            "avg_word_count":     wc["avg"],
            "sweet_spot_words":   wc["sweet_spot"],
            "case_style":         case_style,
            "case_ratios":        cases,
            "word_count_stats":   wc,
            "sample_size":        len(titles),
        }

    # ── Title generation ──────────────────────────────────────────────────

    def generate_better_title(self, competitor_title: str,
                               topic: str,
                               our_keywords: List[str]) -> List[str]:
        """
        Generate 3 title alternatives that are stronger than the competitor's.
        Uses Claude Haiku if ANTHROPIC_API_KEY is set; falls back to rule-based.
        """
        api_key = os.getenv("ANTHROPIC_API_KEY", "")
        if api_key:
            try:
                result = self._claude_titles(competitor_title, topic,
                                              our_keywords, api_key)
                if result:
                    return result
            except Exception as e:
                log.debug(f"Claude title generation failed: {e}")

        return self._rule_based_titles(competitor_title, topic, our_keywords)

    def _claude_titles(self, competitor_title: str, topic: str,
                        our_keywords: List[str], api_key: str) -> List[str]:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        kw_str = ", ".join(our_keywords[:5]) if our_keywords else topic
        prompt = (
            f"Competitor title: \"{competitor_title}\"\n"
            f"Our topic: {topic}\n"
            f"Our keywords: {kw_str}\n\n"
            "Generate exactly 3 YouTube title alternatives that:\n"
            "1. Use the same core keywords but with a stronger hook\n"
            "2. Make a more specific or surprising claim\n"
            "3. Deliver a clearer viewer value proposition\n\n"
            "Return ONLY a JSON array of 3 strings. No explanation."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        m   = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            titles = json.loads(m.group())
            if isinstance(titles, list) and len(titles) >= 1:
                return [str(t) for t in titles[:3]]
        return []

    def _rule_based_titles(self, competitor_title: str,
                            topic: str,
                            our_keywords: List[str]) -> List[str]:
        """Rule-based fallback: always available, no API needed."""
        kw = our_keywords[0] if our_keywords else topic
        ct_lower = competitor_title.lower()

        # Pattern 1: add specificity / number
        if re.search(r"^\d+", competitor_title):
            base_num = int(re.match(r"(\d+)", competitor_title).group(1))
            alt1 = f"{base_num + 2} {topic} Facts That Will Change How You Think"
        else:
            alt1 = f"7 Surprising {topic} Secrets Nobody Talks About"

        # Pattern 2: question format with curiosity gap
        alt2 = f"Why Everything You Know About {kw} Is Wrong (Evidence Inside)"

        # Pattern 3: how-to with specific outcome
        alt3 = f"How To Master {topic} in 30 Days: The Step-by-Step Blueprint"

        return [alt1, alt2, alt3]

    # ── Title scoring ─────────────────────────────────────────────────────

    def score_title(self, title: str) -> float:
        """
        Return a 0-100 CTR-potential score based on viewer psychology signals.
        """
        if not title:
            return 0.0

        score = 0.0
        tl = title.lower()
        words = title.split()

        # Length score (sweet spot 6-10 words → full marks, else partial)
        wc = len(words)
        if 6 <= wc <= 10:
            score += 25.0
        elif 4 <= wc <= 12:
            score += 15.0
        elif wc > 0:
            score += 5.0

        # Power word bonus
        pw_count = sum(1 for pw in POWER_WORDS if pw in tl)
        score += min(pw_count * 8, 20)

        # Emotional trigger bonus
        trigger_count = sum(
            1 for words_list in EMOTIONAL_TRIGGERS.values()
            if any(w in tl for w in words_list)
        )
        score += min(trigger_count * 6, 18)

        # Structural pattern bonus
        if re.search(r"^\d+\s+", title):
            score += 10  # listicle
        elif title.rstrip().endswith("?"):
            score += 8   # question
        elif tl.startswith("how to"):
            score += 7   # how-to

        # Specificity bonus (numbers, percentages, timeframes)
        if re.search(r"\d+", title):
            score += 7

        # Case style (Title Case slightly preferred)
        if title.istitle():
            score += 5
        elif not title.isupper():
            score += 3

        return min(round(score, 1), 100.0)


# ─────────────────────────────────────────────────────────────────────────────
# CommentMiner
# ─────────────────────────────────────────────────────────────────────────────

_COMPLAINT_KEYWORDS: Dict[str, List[str]] = {
    "too_long":       ["too long", "dragged on", "get to the point",
                       "skip to", "boring", "waste of time"],
    "too_short":      ["too short", "needed more", "more depth please",
                       "cut too short", "not enough detail"],
    "missing_topic":  ["where's the", "you missed", "what about",
                       "didn't cover", "should have included", "left out"],
    "wants_tutorial": ["tutorial please", "show me how", "step by step",
                       "how do i", "guide needed", "full tutorial"],
    "quality":        ["bad audio", "can't hear", "blurry", "low quality",
                       "poor quality", "audio is bad", "can't understand"],
}


class CommentMiner:
    """Mines YouTube comments for complaints and unanswered questions."""

    def fetch_comments(self, video_id: str,
                        max_results: int = 100) -> List[Dict]:
        """Fetch top comments via YouTube Data API. Falls back to []."""
        api_key = os.getenv("YOUTUBE_DATA_API_KEY", "")
        if not api_key or not video_id:
            return []
        try:
            import requests
            sess = requests.Session()
            sess.verify = False
            r = sess.get(
                "https://www.googleapis.com/youtube/v3/commentThreads",
                params={
                    "part":       "snippet",
                    "videoId":    video_id,
                    "maxResults": min(max_results, 100),
                    "order":      "relevance",
                    "key":        api_key,
                },
                timeout=20,
            )
            if not r.ok:
                return []
            items = r.json().get("items", [])
            comments = []
            for item in items:
                top = item.get("snippet", {}).get("topLevelComment", {})
                snip = top.get("snippet", {})
                comments.append({
                    "text":   snip.get("textDisplay", ""),
                    "likes":  snip.get("likeCount", 0),
                    "author": snip.get("authorDisplayName", ""),
                })
            return comments
        except Exception as e:
            log.debug(f"Comment fetch failed for {video_id}: {e}")
            return []

    def mine_complaints(self, comments: List[Dict]) -> Dict:
        """
        Categorise comments into complaint buckets.

        Returns:
            {complaints: {category: [texts]}, questions: [str],
             total_analyzed: int, complaint_ratio: float}
        """
        buckets: Dict[str, List[str]] = {k: [] for k in _COMPLAINT_KEYWORDS}
        questions: List[str] = []

        for c in comments:
            text = c.get("text", "")
            if not text:
                continue
            tl = text.lower()
            for category, keywords in _COMPLAINT_KEYWORDS.items():
                if any(kw in tl for kw in keywords):
                    buckets[category].append(text[:300])
                    break
            # Extract questions
            for sentence in re.split(r"[.!]", text):
                if "?" in sentence:
                    questions.append(sentence.strip())

        total_complaints = sum(len(v) for v in buckets.values())
        complaint_ratio  = (total_complaints / max(len(comments), 1))

        return {
            "complaints":     buckets,
            "questions":      questions,
            "total_analyzed": len(comments),
            "complaint_ratio": round(complaint_ratio, 4),
        }

    def extract_questions(self, comments: List[Dict]) -> List[str]:
        """Extract all sentences ending in ? from comments."""
        questions: List[str] = []
        for c in comments:
            text = c.get("text", "")
            if not text:
                continue
            for sentence in re.split(r"(?<=[.!])\s+|(?<=\n)", text):
                if "?" in sentence:
                    q = sentence.strip()
                    if q:
                        questions.append(q[:250])
        return questions

    def analyze_video_comments(self, video_id: str) -> Dict:
        """Full pipeline: fetch → mine → extract questions."""
        comments  = self.fetch_comments(video_id)
        analysis  = self.mine_complaints(comments)
        analysis["video_id"] = video_id
        return analysis


# ─────────────────────────────────────────────────────────────────────────────
# ScheduleMapper
# ─────────────────────────────────────────────────────────────────────────────

_DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
              "Friday", "Saturday", "Sunday"]


class ScheduleMapper:
    """Reverse-engineer competitor upload schedules to find open slots."""

    def map_schedule(self, videos: List[Dict]) -> Dict:
        """
        Analyse upload timestamps.

        Returns:
            preferred_days, preferred_hours, avg_gap_days,
            gap_days, irregular
        """
        timestamps: List[datetime] = []
        for v in videos:
            pub = v.get("published", "") or v.get("published_at", "")
            if not pub:
                continue
            try:
                ts = datetime.fromisoformat(
                    pub.replace("Z", "+00:00")
                )
                timestamps.append(ts)
            except Exception:
                continue

        if not timestamps:
            return {
                "preferred_days":  [],
                "preferred_hours": [],
                "avg_gap_days":    0,
                "gap_days":        _DAY_NAMES[:],
                "irregular":       True,
            }

        timestamps.sort()

        # Day histogram
        day_counts: Counter = Counter(ts.weekday() for ts in timestamps)
        peak_count = max(day_counts.values()) if day_counts else 1
        preferred_days = [_DAY_NAMES[d] for d, _ in
                          day_counts.most_common()]

        # Hour histogram (UTC)
        hour_counts: Counter = Counter(ts.hour for ts in timestamps)
        preferred_hours = [h for h, _ in hour_counts.most_common(5)]

        # Gap analysis
        gaps = [(timestamps[i+1] - timestamps[i]).days
                for i in range(len(timestamps) - 1)]
        avg_gap = sum(gaps) / len(gaps) if gaps else 0

        # Days where competitor rarely publishes (<30% of peak)
        gap_days = [
            _DAY_NAMES[d] for d in range(7)
            if day_counts.get(d, 0) < peak_count * 0.30
        ]

        irregular = False
        if len(gaps) >= 2:
            try:
                std = statistics.stdev(gaps)
                irregular = std > 3
            except statistics.StatisticsError:
                irregular = False

        return {
            "preferred_days":  preferred_days,
            "preferred_hours": preferred_hours,
            "avg_gap_days":    round(avg_gap, 2),
            "gap_days":        gap_days,
            "irregular":       irregular,
        }

    def find_competitive_gap(self, schedules: Dict[str, Dict]) -> Dict:
        """
        Across all competitor schedules find day+hour with lowest activity.

        Args:
            schedules: {channel_id: schedule_dict}
        Returns:
            {day, hour, competitor_activity_score}
        """
        # Build a 7×24 activity matrix
        activity: Dict[Tuple[int, int], int] = defaultdict(int)

        for channel_id, sched in schedules.items():
            for day_name in sched.get("preferred_days", []):
                if day_name in _DAY_NAMES:
                    d = _DAY_NAMES.index(day_name)
                    for h in sched.get("preferred_hours", [14]):
                        activity[(d, h)] += 1

        if not activity:
            return {"day": "Wednesday", "hour": 14,
                    "competitor_activity_score": 0}

        # Find the day/hour with least activity
        min_score = min(activity.values())
        best = [(d, h) for (d, h), s in activity.items() if s == min_score]

        # Prefer midweek slots 9-17 UTC
        preferred = [(d, h) for d, h in best if 9 <= h <= 17 and 1 <= d <= 4]
        chosen = preferred[0] if preferred else best[0]

        return {
            "day":                       _DAY_NAMES[chosen[0]],
            "hour":                      chosen[1],
            "competitor_activity_score": min_score,
        }


# ─────────────────────────────────────────────────────────────────────────────
# GapExploiter
# ─────────────────────────────────────────────────────────────────────────────

class GapExploiter:
    """Find content gaps competitors have missed or handled poorly."""

    def find_uncovered_topics(self,
                               all_titles: List[str],
                               trending: List[str]) -> List[Dict]:
        """
        Topics in trending list with zero or 1 match in competitor corpus.
        """
        gaps: List[Dict] = []
        corpus_lower = [t.lower() for t in all_titles]

        for topic in trending:
            tl = topic.lower()
            count = sum(1 for ct in corpus_lower if tl in ct or ct in tl)
            if count <= 1:
                # Opportunity score: higher when completely uncovered
                opportunity = 90.0 if count == 0 else 60.0
                gaps.append({
                    "topic":             topic,
                    "gap_type":          "uncovered",
                    "opportunity_score": opportunity,
                    "competitor_count":  count,
                })

        return sorted(gaps, key=lambda x: x["opportunity_score"], reverse=True)

    def find_poorly_covered(self,
                             channel_analyses: List[Dict]) -> List[Dict]:
        """
        Videos where views < 40% of channel average → poorly covered topics.
        """
        gaps: List[Dict] = []
        for analysis in channel_analyses:
            channel_id   = analysis.get("channel_id", "")
            videos       = analysis.get("videos", [])
            view_counts  = [v.get("views", 0) for v in videos
                            if v.get("views", 0) > 0]
            if not view_counts:
                continue
            avg_views = sum(view_counts) / len(view_counts)
            threshold = avg_views * 0.40

            for v in videos:
                vc = v.get("views", 0)
                if 0 < vc < threshold:
                    topic = v.get("title", "")
                    if topic:
                        gaps.append({
                            "topic":              topic,
                            "gap_type":           "poorly_covered",
                            "competitor_channel": channel_id,
                            "evidence": {
                                "views":     vc,
                                "avg_views": round(avg_views),
                                "pct":       round(vc / avg_views * 100, 1),
                            },
                            "opportunity_score": round(
                                (1 - vc / avg_views) * 70, 1
                            ),
                        })

        return sorted(gaps, key=lambda x: x["opportunity_score"], reverse=True)

    def find_unanswered_questions(self,
                                   all_questions: List[str]) -> List[Dict]:
        """
        Deduplicated questions sorted by frequency → content opportunities.
        """
        if not all_questions:
            return []

        # Simple dedup: normalise → count
        normalized: Dict[str, List[str]] = defaultdict(list)
        for q in all_questions:
            key = re.sub(r"[^a-z0-9 ]", "", q.lower().strip())
            key = " ".join(key.split()[:8])  # first 8 words as key
            if key:
                normalized[key].append(q)

        results: List[Dict] = []
        for key, instances in normalized.items():
            freq = len(instances)
            # Use the most common variant as canonical
            canonical = Counter(instances).most_common(1)[0][0]
            results.append({
                "question":         canonical,
                "gap_type":         "unanswered_question",
                "frequency":        freq,
                "opportunity_score": min(round(freq * 15.0, 1), 90.0),
            })

        return sorted(results, key=lambda x: x["opportunity_score"], reverse=True)

    def build_gap_list(self,
                        all_analyses: Dict,
                        trending: List[str]) -> List[Dict]:
        """
        Combine all gap types, deduplicate, score, sort DESC.
        Saves top 20 to memory.  Returns top 20.
        """
        # Collect all titles and questions from analyses
        all_titles: List[str] = []
        all_questions: List[str] = []
        channel_analyses: List[Dict] = []

        for channel_id, analysis in all_analyses.items():
            videos = analysis.get("videos", [])
            for v in videos:
                if v.get("title"):
                    all_titles.append(v["title"])
            qa = analysis.get("questions", [])
            all_questions.extend(qa)
            channel_analyses.append({
                "channel_id": channel_id,
                "videos":     videos,
            })

        uncovered     = self.find_uncovered_topics(all_titles, trending)
        poorly        = self.find_poorly_covered(channel_analyses)
        unanswered    = self.find_unanswered_questions(all_questions)

        combined = uncovered + poorly + unanswered

        # Deduplicate by topic text (normalised)
        seen: set = set()
        deduped: List[Dict] = []
        for item in combined:
            key = re.sub(r"[^a-z0-9]", "",
                         item.get("topic", item.get("question", "")).lower()[:40])
            if key and key not in seen:
                seen.add(key)
                deduped.append(item)

        # Sort by opportunity_score
        deduped.sort(key=lambda x: x.get("opportunity_score", 0), reverse=True)
        top20 = deduped[:20]

        # Save to memory if available
        try:
            from core.memory import get_memory
            mem = get_memory()
            now = datetime.now(timezone.utc)
            week_num = now.isocalendar()[1]
            for item in top20:
                topic_text = item.get("topic") or item.get("question", "")
                mem.save_content_gap({
                    "topic":             topic_text,
                    "gap_type":          item.get("gap_type", "uncovered"),
                    "opportunity_score": item.get("opportunity_score", 0.0),
                    "evidence":          item,
                    "competitor_ids":    [],
                    "status":            "open",
                    "week_num":          week_num,
                })
        except Exception as e:
            log.debug(f"Could not save gap list to memory: {e}")

        return top20


# ─────────────────────────────────────────────────────────────────────────────
# TitleWarfare
# ─────────────────────────────────────────────────────────────────────────────

class TitleWarfare:
    """Real-time RSS monitoring for new competitor videos + counter-title generation."""

    def __init__(self):
        self._decoder = TitleDecoder()

    def check_rss_for_new_videos(self,
                                  channel_ids: List[str],
                                  last_seen: Dict[str, str]) -> List[Dict]:
        """
        Fetch RSS for each channel; return videos published after last_seen.
        Falls back to empty list gracefully on any failure.
        """
        new_videos: List[Dict] = []
        try:
            import feedparser
        except ImportError:
            log.debug("feedparser not available — RSS check skipped")
            return []

        for channel_id in channel_ids:
            try:
                url  = (f"https://www.youtube.com/feeds/videos.xml"
                        f"?channel_id={channel_id}")
                feed = feedparser.parse(url)
                threshold = last_seen.get(channel_id, "")

                for entry in feed.entries:
                    pub = entry.get("published", "")
                    if threshold and pub <= threshold:
                        continue
                    new_videos.append({
                        "video_id":    entry.get("yt_videoid", ""),
                        "title":       entry.get("title", ""),
                        "published":   pub,
                        "channel_id":  channel_id,
                        "channel":     feed.feed.get("title", channel_id),
                        "link":        entry.get("link", ""),
                    })
            except Exception as e:
                log.debug(f"RSS fetch failed for {channel_id}: {e}")

        return new_videos

    def generate_warfare_titles(self,
                                  competitor_title: str,
                                  competitor_video_id: str,
                                  competitor_channel: str,
                                  upload_time: str) -> Dict:
        """
        Full warfare package for a new competitor video.

        Returns:
            {competitor_title, better_titles, keywords, deadline_utc, urgency_minutes}
        """
        # Extract keywords from competitor title
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "be",
                     "to", "of", "in", "it", "and", "or", "for", "on"}
        words = re.findall(r"\b[a-zA-Z]{3,}\b", competitor_title)
        keywords = [w for w in words if w.lower() not in stopwords][:8]

        # Topic = first few meaningful words
        topic = " ".join(keywords[:3]) if keywords else competitor_title[:30]

        better_titles = self._decoder.generate_better_title(
            competitor_title, topic, keywords
        )

        # 6-hour deadline
        try:
            upload_dt = datetime.fromisoformat(
                upload_time.replace("Z", "+00:00")
            )
        except Exception:
            upload_dt = datetime.now(timezone.utc)

        deadline_dt = upload_dt + timedelta(hours=6)
        now         = datetime.now(timezone.utc)
        urgency_min = max(0, int((deadline_dt - now).total_seconds() / 60))

        return {
            "competitor_title":    competitor_title,
            "competitor_video_id": competitor_video_id,
            "competitor_channel":  competitor_channel,
            "better_titles":       better_titles,
            "keywords":            keywords,
            "deadline_utc":        deadline_dt.isoformat(),
            "urgency_minutes":     urgency_min,
        }

    def get_active_battles(self, memory) -> List[Dict]:
        """Return title battles that are still within deadline."""
        try:
            return memory.get_pending_battles()
        except Exception as e:
            log.debug(f"Could not fetch pending battles: {e}")
            return []

    def monitor_and_alert(self,
                           channel_ids: List[str],
                           memory) -> List[Dict]:
        """
        Full cycle: check RSS → create battles → return active battles.
        """
        # Load last_seen from recent battles in memory
        last_seen: Dict[str, str] = {}
        try:
            recent = memory.get_competitor_intelligence(intel_type="last_seen_rss")
            for rec in recent:
                data = json.loads(rec.get("data", "{}") or "{}")
                last_seen.update(data)
        except Exception:
            pass

        new_videos = self.check_rss_for_new_videos(channel_ids, last_seen)

        for v in new_videos:
            try:
                warfare = self.generate_warfare_titles(
                    competitor_title   = v["title"],
                    competitor_video_id = v["video_id"],
                    competitor_channel = v.get("channel", v["channel_id"]),
                    upload_time        = v["published"],
                )
                memory.save_title_battle({
                    "competitor_channel":  warfare["competitor_channel"],
                    "competitor_video_id": warfare["competitor_video_id"],
                    "competitor_title":    warfare["competitor_title"],
                    "our_titles":          warfare["better_titles"],
                    "best_title":          warfare["better_titles"][0]
                                           if warfare["better_titles"] else "",
                    "keywords":            warfare["keywords"],
                    "deadline_utc":        warfare["deadline_utc"],
                    "urgency_minutes":     warfare["urgency_minutes"],
                })
            except Exception as e:
                log.debug(f"Failed to create battle for {v.get('video_id')}: {e}")

        # Update last_seen
        try:
            new_last_seen: Dict[str, str] = {}
            for v in new_videos:
                cid = v["channel_id"]
                pub = v["published"]
                if cid not in new_last_seen or pub > new_last_seen[cid]:
                    new_last_seen[cid] = pub
            if new_last_seen:
                updated = {**last_seen, **new_last_seen}
                memory.save_competitor_intelligence(
                    "__rss_tracker__", "last_seen_rss", updated
                )
        except Exception as e:
            log.debug(f"Could not update last_seen: {e}")

        return self.get_active_battles(memory)


# ─────────────────────────────────────────────────────────────────────────────
# AudiencePoacher
# ─────────────────────────────────────────────────────────────────────────────

_DRAFT_PREFIX = "⚠️ DRAFT — requires human review before posting\n\n"


class AudiencePoacher:
    """
    Generates engagement-comment drafts that add genuine value.
    All drafts require human review — never auto-posts.
    """

    def generate_value_comment(self,
                                competitor_video_id: str,
                                complaint: str,
                                our_content: str) -> str:
        """
        Craft a helpful, transparent comment that addresses a viewer complaint.
        Max 3 sentences. Prefix always includes the draft warning.
        """
        complaint_lower = complaint.lower()

        # Select response strategy based on complaint type
        if "missing_topic" in complaint_lower or "didn't cover" in complaint_lower:
            body = (
                f"Great video! One thing worth exploring is {our_content} — "
                f"I went deep on that angle and found some surprising results. "
                f"Happy to share what I discovered if anyone's interested."
            )
        elif "wants_tutorial" in complaint_lower or "step by step" in complaint_lower:
            body = (
                f"If you want a detailed walkthrough on {our_content}, "
                f"I've put together a comprehensive step-by-step breakdown. "
                f"Let me know and I can point you in the right direction."
            )
        elif "too_long" in complaint_lower:
            body = (
                f"Totally get it — I focused specifically on {our_content} "
                f"in a more concise format that gets straight to the key insight. "
                f"Might be exactly what you're looking for."
            )
        else:
            body = (
                f"Really enjoyed this! For anyone wanting to go deeper on {our_content}, "
                f"I've covered some of the angles this video didn't have time for. "
                f"The results might surprise you."
            )

        return _DRAFT_PREFIX + body

    def find_best_poach_targets(self,
                                 analyses: Dict[str, Dict]) -> List[Dict]:
        """
        Find competitor videos with complaint_ratio > 0.15 and
        complaints in {missing_topic, wants_tutorial}.
        """
        targets: List[Dict] = []
        for channel_id, analysis in analyses.items():
            comment_data = analysis.get("comment_analyses", {})
            for video_id, ca in comment_data.items():
                ratio = ca.get("complaint_ratio", 0)
                complaints = ca.get("complaints", {})
                has_target_complaints = bool(
                    complaints.get("missing_topic") or
                    complaints.get("wants_tutorial")
                )
                if ratio > 0.15 and has_target_complaints:
                    targets.append({
                        "channel_id":      channel_id,
                        "video_id":        video_id,
                        "complaint_ratio": ratio,
                        "complaints":      complaints,
                        "opportunity":     "audience dissatisfied — high poach potential",
                    })

        targets.sort(key=lambda x: x["complaint_ratio"], reverse=True)
        return targets

    def generate_response_video_brief(self,
                                       question: str,
                                       gap: Dict) -> Dict:
        """
        Generate a video brief from an unanswered viewer question.

        Returns:
            {title, hook, key_points, target_keyword, opportunity_score}
        """
        topic = gap.get("topic") or gap.get("question") or question
        opp   = gap.get("opportunity_score", 50.0)

        # Extract keyword from question
        words = re.findall(r"\b[a-zA-Z]{3,}\b", question)
        stopwords = {"the", "how", "why", "what", "when", "where",
                     "does", "can", "you", "your", "this", "that"}
        keywords = [w for w in words if w.lower() not in stopwords]
        target_kw = keywords[0] if keywords else "topic"

        decoder = TitleDecoder()
        titles  = decoder.generate_better_title(question, topic, keywords[:3])
        title   = titles[0] if titles else f"The Complete Answer to: {question[:50]}"

        return {
            "title":             title,
            "hook":              f"You asked: '{question[:80]}' — here's the complete answer.",
            "key_points": [
                f"What most creators get wrong about {target_kw}",
                f"The evidence-based answer to '{question[:40]}'",
                f"Practical steps you can take today",
                f"Why this question matters more than you think",
            ],
            "target_keyword":    target_kw,
            "opportunity_score": opp,
        }


# ─────────────────────────────────────────────────────────────────────────────
# CompetitorDominator  (main orchestrator)
# ─────────────────────────────────────────────────────────────────────────────

class CompetitorDominator:
    """
    Full deep-intelligence orchestrator.
    Wraps CompetitorSpy and adds: title analysis, schedule mapping,
    comment mining, gap exploitation, title warfare, audience poaching.
    """

    def __init__(self, memory=None, channel_ids: List[str] = None):
        from core.memory import get_memory
        self.memory      = memory or get_memory()
        self.channel_ids = channel_ids or self._load_channel_ids()

        self._spy      = None   # lazy init
        self._decoder  = TitleDecoder()
        self._miner    = CommentMiner()
        self._mapper   = ScheduleMapper()
        self._exploiter = GapExploiter()
        self._warfare  = TitleWarfare()
        self._poacher  = AudiencePoacher()

    def _get_spy(self):
        if self._spy is None:
            from intelligence.competitor_spy import CompetitorSpy
            self._spy = CompetitorSpy(self.memory)
        return self._spy

    def _load_channel_ids(self) -> List[str]:
        """Load competitor channel IDs from master_config.yaml."""
        try:
            import yaml
            cfg_path = HERE / "config" / "master_config.yaml"
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
            channels = (
                cfg.get("analytics", {}).get("competitor_channels", []) or
                cfg.get("competitor_channels", [])
            )
            if channels:
                return channels
        except Exception as e:
            log.debug(f"Could not load channel IDs from config: {e}")

        from intelligence.competitor_spy import DEFAULT_COMPETITORS
        return DEFAULT_COMPETITORS

    # ── Single-channel analysis ───────────────────────────────────────────

    def analyze_competitor(self, channel_id: str) -> Dict:
        """
        Full deep dive for one channel:
          1. Fetch 50 videos (RSS fallback)
          2. Stats for top 10 videos
          3. Decode title formula
          4. Map upload schedule
          5. Mine comments for top 5 (most viewed)
          6. Detect underperforming videos
          7. Save to memory
          8. Return full dict
        """
        spy    = self._get_spy()
        videos = spy._rss_fallback(channel_id, max_results=50)

        if not videos:
            log.debug(f"No videos fetched for {channel_id}")
            return {"channel_id": channel_id, "error": "no_videos"}

        # Fetch stats for top 10 videos
        for i, v in enumerate(videos[:10]):
            if not v.get("id"):
                continue
            try:
                stats = spy.fetch_video_stats(v["id"])
                v.update(stats)
            except Exception:
                pass

        # Sort by views descending (those with stats first)
        videos_with_views = [v for v in videos if v.get("views", 0) > 0]
        videos_without    = [v for v in videos if v.get("views", 0) == 0]
        sorted_videos = sorted(videos_with_views, key=lambda x: x["views"],
                                reverse=True) + videos_without

        # Title formula
        titles       = [v["title"] for v in sorted_videos if v.get("title")]
        title_formula = self._decoder.decode_formula(titles)

        # Schedule mapping
        schedule = self._mapper.map_schedule(sorted_videos)

        # Comment mining for top 5 most-viewed
        comment_analyses: Dict[str, Dict] = {}
        top5 = sorted_videos[:5]
        all_questions: List[str] = []
        for v in top5:
            vid_id = v.get("id", "")
            if vid_id:
                try:
                    ca = self._miner.analyze_video_comments(vid_id)
                    comment_analyses[vid_id] = ca
                    all_questions.extend(ca.get("questions", []))
                except Exception as e:
                    log.debug(f"Comment mining failed for {vid_id}: {e}")

        # Detect underperformers (<40% of channel average views)
        view_counts = [v.get("views", 0) for v in sorted_videos
                       if v.get("views", 0) > 0]
        avg_views = (sum(view_counts) / len(view_counts)) if view_counts else 0
        underperformers = [
            v for v in sorted_videos
            if 0 < v.get("views", 0) < avg_views * 0.40
        ]

        result = {
            "channel_id":         channel_id,
            "videos":             sorted_videos,
            "title_formula":      title_formula,
            "schedule":           schedule,
            "comment_analyses":   comment_analyses,
            "questions":          all_questions,
            "underperformers":    underperformers,
            "avg_views":          round(avg_views),
            "analyzed_at":        datetime.now(timezone.utc).isoformat(),
        }

        # Persist to memory
        try:
            self.memory.save_competitor_intelligence(
                channel_id, "full_analysis", result
            )
        except Exception as e:
            log.debug(f"Could not save intelligence for {channel_id}: {e}")

        return result

    # ── Multi-channel analysis ────────────────────────────────────────────

    def analyze_all(self, max_channels: int = 20) -> Dict:
        """Analyze all configured competitor channels."""
        channels = self.channel_ids[:max_channels]
        log.info(f"CompetitorDominator: analysing {len(channels)} channels")
        results: Dict[str, Dict] = {}
        for cid in channels:
            log.info(f"  Analysing {cid[:20]}…")
            try:
                results[cid] = self.analyze_competitor(cid)
            except Exception as e:
                log.warning(f"  Failed to analyse {cid}: {e}")
                results[cid] = {"channel_id": cid, "error": str(e)}
        return results

    # ── Daily cycle ───────────────────────────────────────────────────────

    def run_daily(self, n_sessions: int = None) -> Dict:
        """
        Full daily intelligence cycle:
          1. Refresh competitor analyses
          2. Build gap list
          3. Monitor RSS for title warfare
          4. Find audience poach targets
          5. Save subscriber snapshots
          6. Return combined result
        """
        log.info("CompetitorDominator: running daily cycle…")

        # 1. Analyse competitors
        analyses = self.analyze_all()

        # 2. Build gap list (uses trending topics if available)
        trending = self._get_trending_topics()
        gap_list = self._exploiter.build_gap_list(analyses, trending)
        log.info(f"  Gap list: {len(gap_list)} opportunities found")

        # 3. Title warfare RSS monitor
        try:
            title_battles = self._warfare.monitor_and_alert(
                self.channel_ids, self.memory
            )
        except Exception as e:
            log.debug(f"Title warfare monitor failed: {e}")
            title_battles = []

        # 4. Audience poach targets
        try:
            poach_targets = self._poacher.find_best_poach_targets(analyses)
        except Exception as e:
            log.debug(f"Poach target finder failed: {e}")
            poach_targets = []

        # 5. Save subscriber snapshots
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for channel_id, analysis in analyses.items():
            try:
                self.memory.upsert_competitor_snapshot(
                    channel_id   = channel_id,
                    date         = today,
                    subscribers  = analysis.get("subscribers", 0),
                    total_views  = analysis.get("avg_views", 0),
                    video_count  = len(analysis.get("videos", [])),
                )
            except Exception as e:
                log.debug(f"Snapshot save failed for {channel_id}: {e}")

        result = {
            "analyses":      analyses,
            "gap_list":      gap_list,
            "title_battles": title_battles,
            "poach_targets": poach_targets,
            "generated_at":  datetime.now(timezone.utc).isoformat(),
        }

        log.info(
            f"CompetitorDominator daily cycle complete: "
            f"channels={len(analyses)} gaps={len(gap_list)} "
            f"battles={len(title_battles)} poach={len(poach_targets)}"
        )
        return result

    def _get_trending_topics(self) -> List[str]:
        """Pull recent trending topics from memory, or return empty list."""
        try:
            with self.memory._conn() as conn:
                rows = conn.execute(
                    """SELECT DISTINCT trend FROM trend_history
                       ORDER BY fetched_at DESC LIMIT 30"""
                ).fetchall()
            return [r["trend"] for r in rows if r["trend"]]
        except Exception:
            return []

    # ── Retrieval helpers ─────────────────────────────────────────────────

    def get_gap_list(self, n: int = 20) -> List[Dict]:
        """Retrieve current gap list from memory."""
        try:
            return self.memory.get_content_gaps(status="open", n=n)
        except Exception as e:
            log.debug(f"Could not retrieve gap list: {e}")
            return []

    # ── Reporting ─────────────────────────────────────────────────────────

    def print_report(self, result: Dict) -> None:
        """Human-readable intelligence report."""
        analyses      = result.get("analyses", {})
        gap_list      = result.get("gap_list", [])
        title_battles = result.get("title_battles", [])
        poach_targets = result.get("poach_targets", [])
        ts            = result.get("generated_at", "")

        print("\n" + "=" * 65)
        print("  COMPETITOR DOMINATOR — INTELLIGENCE REPORT")
        print(f"  Generated: {ts[:19]}")
        print("=" * 65)

        print(f"\n  Channels analysed: {len(analyses)}")
        for cid, a in analyses.items():
            vcount = len(a.get("videos", []))
            avg_v  = a.get("avg_views", 0)
            sched  = a.get("schedule", {})
            print(f"    {cid[:24]:24s}  videos={vcount:<4}  avg_views={avg_v:<8}"
                  f"  uploads_on={sched.get('preferred_days', ['?'])[:2]}")

        print(f"\n  Content Gaps ({len(gap_list)} found):")
        for i, g in enumerate(gap_list[:10], 1):
            topic = g.get("topic") or g.get("question", "?")
            score = g.get("opportunity_score", 0)
            gtype = g.get("gap_type", "?")
            print(f"    {i:2d}. [{score:5.1f}] ({gtype[:18]:18s}) {topic[:55]}")

        print(f"\n  Title Battles ({len(title_battles)} active):")
        for b in title_battles[:5]:
            print(f"    ⚔  {b.get('competitor_title','')[:55]}")
            print(f"       deadline={b.get('deadline_utc','')[:16]}"
                  f"  urgency={b.get('urgency_minutes',0)}min")

        print(f"\n  Poach Targets ({len(poach_targets)} found):")
        for p in poach_targets[:3]:
            print(f"    ▶  {p.get('video_id',''):12s}"
                  f"  complaint_ratio={p.get('complaint_ratio',0):.2f}")

        print("=" * 65 + "\n")
