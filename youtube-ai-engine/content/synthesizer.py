"""
content/synthesizer.py — Multi-Source Content Synthesis Engine
==============================================================
Transforms raw source items (news, research, data, reddit threads, etc.)
into ranked, production-ready YouTube video ideas by finding surprising
cross-source connections and applying narrative frameworks.
"""
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger("brain.synthesizer")

HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYNTHESIS_PATTERNS = [
    {"name": "data_meets_story",    "formula": "stat_from_source_1 + human_story_from_source_2"},
    {"name": "science_confirms",    "formula": "research_finding + everyday_example"},
    {"name": "historical_parallel", "formula": "past_event + current_event"},
    {"name": "money_connection",    "formula": "topic + financial_impact"},
    {"name": "nobody_talks_about",  "formula": "obscure_source_1 + mainstream_relevance"},
    {"name": "before_after",        "formula": "old_way + new_discovery"},
    {"name": "vs_battle",           "formula": "option_a + option_b + surprising_winner"},
]

TRANSFORMATION_TEMPLATES = {
    "blog_post":      "I read {n} {niche} blogs so you don't have to — here's what I found",
    "research_paper": "Scientists Just Discovered {finding}",
    "data_file":      "The Data Reveals Something Shocking About {topic}",
    "news_article":   "{event} Changes Everything — Here's Why",
    "reddit_thread":  "People Are Finally Talking About {topic}",
    "old_archive":    "This {age} Year Old Document Predicted {current_event}",
    "personal_notes": "What I Learned After {time} of {activity}",
    "statistics":     "The Numbers Don't Lie: {finding}",
    "book":           "This Book Changed How I See {topic} Forever",
    "court_case":     "This Case Could Change Everything About {topic}",
    "csv_data":       "What {dataset} Data Reveals About {topic}",
    "general":        "The Truth About {topic} Nobody Is Talking About",
}

MIN_SOURCES_FOR_SYNTHESIS = 2

# Common English stop-words to exclude from keyword extraction
_STOP_WORDS: Set[str] = {
    "a", "an", "the", "is", "in", "of", "to", "for", "and", "or", "but",
    "on", "at", "by", "as", "it", "its", "was", "are", "be", "been",
    "has", "had", "have", "with", "from", "this", "that", "these", "those",
    "he", "she", "they", "we", "you", "i", "his", "her", "our", "their",
    "my", "your", "can", "will", "may", "not", "no", "so", "up", "do",
    "did", "out", "if", "about", "into", "over", "after", "more", "also",
    "than", "then", "when", "where", "which", "who", "what", "how",
    "all", "any", "each", "both", "few", "some", "such", "only", "own",
    "same", "just", "because", "while", "between", "through", "during",
    "new", "now", "one", "two", "three", "get", "got", "been", "being",
}

# Negative-framing signal words for scoring
_NEGATIVE_WORDS: Set[str] = {
    "destroying", "ruining", "killing", "danger", "dark", "worst",
    "never", "stop", "avoid", "toxic", "dangerous", "failing", "broken",
    "lies", "lie", "scam", "fake", "bad", "wrong", "terrible", "dead",
    "die", "destroy", "collapse", "crash", "fail", "lose", "lost",
}

# Secret/truth signal words for scoring
_SECRET_WORDS: Set[str] = {
    "truth", "secret", "revealed", "hidden", "untold", "nobody", "exposed",
    "real", "actually", "shocking", "surprising", "unknown", "unspoken",
    "reveal", "discover", "found", "proof", "evidence", "admitted",
}

# Trending/timely signal phrases
_TIMELY_WORDS: Set[str] = {
    "just", "new", "breaking", "latest", "2024", "2025", "2026",
    "now", "today", "recent", "update", "announces", "launches",
    "discovered", "released", "official",
}

# 12 angle templates mirrored from category_universe for standalone use
_ANGLE_TEMPLATES = [
    "The Truth About {topic}",
    "Why {topic} Is Not What You Think",
    "7 Things Nobody Tells You About {topic}",
    "I Tried {topic} For 30 Days — Here's What Happened",
    "The Dark Side of {topic}",
    "How {topic} Is Ruining Your Future",
    "The {topic} Secret Experts Don't Want You To Know",
    "{topic} Changed My Life — Here's How",
    "The Japanese {topic} Method That Actually Works",
    "What Happens To Your Health After {topic}",
    "{topic} vs The Old Way: Which One Actually Works?",
    f"The {datetime.now(timezone.utc).year} Guide to {{topic}} Nobody Else Will Tell You",
]


# ---------------------------------------------------------------------------
# Class 1: SourceMatcher
# ---------------------------------------------------------------------------

class SourceMatcher:
    """Finds related source items using Jaccard keyword similarity."""

    def __init__(self):
        pass

    def extract_keywords(self, text: str) -> Set[str]:
        """Lowercase, tokenize, remove stop-words and short tokens."""
        words = re.findall(r"\b[a-z]{3,}\b", text.lower())
        return {w for w in words if w not in _STOP_WORDS}

    def jaccard_similarity(self, a: Set[str], b: Set[str]) -> float:
        """Compute |a ∩ b| / |a ∪ b|; return 0.0 if both sets are empty."""
        if not a and not b:
            return 0.0
        intersection = len(a & b)
        union        = len(a | b)
        if union == 0:
            return 0.0
        return intersection / union

    def _item_text(self, item: Dict) -> str:
        """Extract searchable text from an item dict."""
        parts = []
        for field in ("title", "topic", "content", "summary", "description",
                      "keywords", "category", "tags"):
            val = item.get(field, "")
            if isinstance(val, list):
                parts.append(" ".join(str(v) for v in val))
            elif val:
                parts.append(str(val))
        return " ".join(parts)

    def find_related_sources(
        self,
        items: List[Dict],
        threshold: float = 0.3,
    ) -> List[Tuple[Dict, Dict, float]]:
        """Return all pairs of items with Jaccard similarity above threshold."""
        if len(items) < 2:
            return []

        # Pre-compute keyword sets
        kw_cache: Dict[int, Set[str]] = {}
        for idx, item in enumerate(items):
            text = self._item_text(item)
            kw_cache[idx] = self.extract_keywords(text)

        related: List[Tuple[Dict, Dict, float]] = []
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                score = self.jaccard_similarity(kw_cache[i], kw_cache[j])
                if score > threshold:
                    related.append((items[i], items[j], round(score, 4)))

        # Sort by similarity descending
        related.sort(key=lambda x: x[2], reverse=True)
        return related

    def group_by_topic(
        self,
        items: List[Dict],
        n_groups: int = 5,
    ) -> Dict[str, List[Dict]]:
        """Group items by most common keywords; return {keyword: [items]}."""
        if not items:
            return {}

        # Tally keyword frequency across all items
        freq: Dict[str, int] = {}
        item_keywords: List[Set[str]] = []
        for item in items:
            text = self._item_text(item)
            kws  = self.extract_keywords(text)
            item_keywords.append(kws)
            for kw in kws:
                freq[kw] = freq.get(kw, 0) + 1

        # Pick top n_groups keywords by frequency (min 2 occurrences)
        top_keywords = sorted(
            [kw for kw, cnt in freq.items() if cnt >= 2],
            key=lambda k: freq[k],
            reverse=True,
        )[:n_groups]

        if not top_keywords:
            # Fallback: assign all items to "general"
            return {"general": list(items)}

        groups: Dict[str, List[Dict]] = {kw: [] for kw in top_keywords}
        for item, kws in zip(items, item_keywords):
            for kw in top_keywords:
                if kw in kws:
                    groups[kw].append(item)

        # Remove empty groups
        return {kw: grp for kw, grp in groups.items() if grp}


# ---------------------------------------------------------------------------
# Class 2: NarrativeBuilder
# ---------------------------------------------------------------------------

class NarrativeBuilder:
    """Builds a structured video narrative from 2-5 source items."""

    def __init__(self):
        self._matcher = SourceMatcher()

    def find_surprising_connection(self, source_a: Dict, source_b: Dict) -> str:
        """Return a sentence describing the surprising link between two sources."""
        topic_a = (
            source_a.get("topic")
            or source_a.get("title")
            or source_a.get("category", "Topic A")
        )
        topic_b = (
            source_b.get("topic")
            or source_b.get("title")
            or source_b.get("category", "Topic B")
        )

        # Find shared keywords as the common thread
        kw_a = self._matcher.extract_keywords(
            f"{topic_a} {source_a.get('content', '')} {source_a.get('summary', '')}"
        )
        kw_b = self._matcher.extract_keywords(
            f"{topic_b} {source_b.get('content', '')} {source_b.get('summary', '')}"
        )
        shared = kw_a & kw_b
        if shared:
            thread = sorted(shared, key=len, reverse=True)[0]
            return (
                f"While {topic_a} seems unrelated to {topic_b}, "
                f"they're connected by {thread}"
            )
        return (
            f"While {topic_a} seems unrelated to {topic_b}, "
            f"they share a surprising common thread"
        )

    def _build_hook(self, sources: List[Dict]) -> str:
        """Build an opening hook from the most surprising fact/stat found in sources."""
        for source in sources:
            for field in ("key_stat", "stat", "hook", "headline", "title"):
                val = source.get(field, "")
                if val and isinstance(val, str) and len(val) > 10:
                    return f"Here's something almost nobody knows: {val}"
        # Fallback: use first source's topic
        topic = (
            sources[0].get("topic")
            or sources[0].get("title", "this topic")
        )
        return f"What you're about to learn about {topic} will change how you see everything."

    def _build_conflict(self, sources: List[Dict]) -> str:
        """Identify a tension or contradiction across the sources."""
        if len(sources) < 2:
            topic = sources[0].get("topic") or sources[0].get("title", "this subject")
            return f"Most people think they understand {topic} — but the data tells a very different story."

        topic_a = sources[0].get("topic") or sources[0].get("title", "the first perspective")
        topic_b = sources[1].get("topic") or sources[1].get("title", "the second perspective")

        # Look for explicit contradictions in summary/content fields
        content_a = (sources[0].get("content") or sources[0].get("summary", "")).lower()
        content_b = (sources[1].get("content") or sources[1].get("summary", "")).lower()

        contradiction_pairs = [
            ("increase", "decrease"), ("rise", "fall"), ("growth", "decline"),
            ("safe", "dangerous"), ("healthy", "toxic"), ("good", "bad"),
            ("proven", "debunked"), ("effective", "useless"),
        ]
        for word_a, word_b in contradiction_pairs:
            if word_a in content_a and word_b in content_b:
                return (
                    f"While {topic_a} suggests it {word_a}s, "
                    f"{topic_b} reveals the opposite may be true."
                )
            if word_b in content_a and word_a in content_b:
                return (
                    f"While {topic_a} points to {word_b}, "
                    f"{topic_b} shows signs of {word_a}."
                )

        return (
            f"The mainstream view on {topic_a} directly contradicts "
            f"what {topic_b} actually shows."
        )

    def _build_revelation(self, connection: str, sources: List[Dict]) -> str:
        """Synthesize the 'aha moment' from the connection and sources."""
        key_stats = []
        for source in sources:
            stat = source.get("key_stat") or source.get("stat") or source.get("finding", "")
            if stat and isinstance(stat, str):
                key_stats.append(stat)

        if key_stats:
            return (
                f"{connection}. "
                f"The evidence is clear: {key_stats[0]}. "
                f"Once you see this pattern, you can't unsee it."
            )
        return (
            f"{connection}. "
            f"This hidden pattern explains more than most experts are willing to admit."
        )

    def build_narrative(self, sources: List[Dict], category: str = "general") -> Dict:
        """Build a complete video narrative structure from 2-5 sources."""
        if not sources:
            return {
                "hook":             "No sources provided.",
                "conflict":         "",
                "revelation":       "",
                "cta":              "Subscribe for more insights.",
                "title_suggestions": [],
                "category":         category,
            }

        # Find connection between first two sources
        connection = ""
        if len(sources) >= 2:
            try:
                connection = self.find_surprising_connection(sources[0], sources[1])
            except Exception as exc:
                log.warning("build_narrative: find_surprising_connection failed — %s", exc)
                connection = "These sources reveal a deeper truth when viewed together."

        hook       = self._build_hook(sources)
        conflict   = self._build_conflict(sources)
        revelation = self._build_revelation(connection or conflict, sources)

        # Derive title suggestions from the main topic
        main_topic = (
            sources[0].get("topic")
            or sources[0].get("title", category)
        )
        title_suggestions = [
            f"The Truth About {main_topic}",
            f"Why {main_topic} Is Not What You Think",
            f"The {main_topic} Secret Nobody Is Talking About",
        ]

        # CTA tailored to category
        cta_map = {
            "personal_finance": "If this saved you money, share it with someone who needs it.",
            "health_medical":   "Share this with someone who needs to hear it.",
            "technology":       "Subscribe — we cover this every week.",
            "general":          "Subscribe for more insights like this.",
        }
        cta = cta_map.get(category, cta_map["general"])

        return {
            "hook":              hook,
            "conflict":          conflict,
            "revelation":        revelation,
            "cta":               cta,
            "title_suggestions": title_suggestions,
            "category":          category,
        }


# ---------------------------------------------------------------------------
# Class 3: ContentTransformer
# ---------------------------------------------------------------------------

class ContentTransformer:
    """Transforms a single source item into one or more video concepts."""

    def __init__(self):
        pass

    def get_template(self, source_type: str) -> str:
        """Look up template by source_type; fall back to 'general'."""
        return TRANSFORMATION_TEMPLATES.get(source_type, TRANSFORMATION_TEMPLATES["general"])

    def apply_template(self, template: str, item: Dict) -> str:
        """Fill template slots with values from item, using sensible defaults."""
        topic       = item.get("topic") or item.get("title", "this topic")
        finding     = item.get("finding") or item.get("key_stat") or item.get("headline", topic)
        event       = item.get("event") or item.get("title", topic)
        niche       = item.get("category") or item.get("niche", "finance")
        dataset     = item.get("dataset") or item.get("source", "new data")
        current_event = item.get("current_event") or item.get("title", "recent events")

        slots = {
            "n":             str(item.get("n", "50")),
            "niche":         str(niche),
            "finding":       str(finding),
            "topic":         str(topic),
            "event":         str(event),
            "age":           str(item.get("age", "100")),
            "current_event": str(current_event),
            "time":          str(item.get("time", "5 years")),
            "activity":      str(item.get("activity", niche)),
            "dataset":       str(dataset),
        }
        try:
            return template.format_map(slots)
        except KeyError as exc:
            log.debug("apply_template: unresolved slot %s", exc)
            result = template
            for m in re.findall(r"\{(\w+)\}", template):
                result = result.replace("{" + m + "}", str(topic))
            return result

    def transform(self, item: Dict, source_type: str = "general") -> Dict:
        """Apply the appropriate transformation template to produce a video concept."""
        template = self.get_template(source_type)
        title    = self.apply_template(template, item)
        topic    = item.get("topic") or item.get("title", "unknown")
        category = item.get("category", "general")

        return {
            "source_type": source_type,
            "title":       title,
            "topic":       topic,
            "category":    category,
            "source_item": item,
            "hook":        f"What if everything you know about {topic} is wrong?",
            "created_at":  datetime.now(timezone.utc).isoformat(),
        }

    def generate_12_angles(self, topic: str, category: str = "general") -> List[Dict]:
        """Generate all 12 angle variants for a topic."""
        results = []
        hook_starters = [
            "What if everything you know about {topic} is wrong?",
            "Nobody talks about the real side of {topic}.",
            "The {topic} industry doesn't want you to see this.",
            "I spent 30 days studying {topic} — here's what shocked me.",
            "This {topic} secret is worth thousands of dollars.",
            "The dark history of {topic} they left out of textbooks.",
            "Why every expert is wrong about {topic}.",
            "This one {topic} fact changes everything.",
            "How {topic} quietly controls your daily life.",
            "The government's hidden {topic} data finally leaked.",
            "Insiders are panicking — here's the {topic} truth.",
            "If you're ignoring {topic}, this video is for you.",
        ]
        for idx, (template, hook_tpl) in enumerate(
            zip(_ANGLE_TEMPLATES, hook_starters), start=1
        ):
            try:
                title = template.format(topic=topic)
            except KeyError:
                title = template.replace("{topic}", topic)

            hook = hook_tpl.format(topic=topic)
            results.append({
                "angle_num":      idx,
                "template_name":  template.split()[0].lower().strip("{"),
                "title":          title,
                "hook_suggestion": hook,
                "category":       category,
            })
        return results

    def batch_transform(self, items: List[Dict]) -> List[Dict]:
        """Transform a list of source items into video concepts."""
        results = []
        for item in items:
            source_type = item.get("source_type", item.get("type", "general"))
            try:
                concept = self.transform(item, source_type=source_type)
                results.append(concept)
            except Exception as exc:
                log.warning("batch_transform: transform failed for item — %s", exc)
        return results


# ---------------------------------------------------------------------------
# Class 4: IdeaScorer
# ---------------------------------------------------------------------------

class IdeaScorer:
    """Scores and ranks video ideas on virality potential (0–100)."""

    def __init__(self):
        pass

    def score_idea(self, idea: Dict) -> float:
        """Return a 0–100 virality score based on title and metadata signals."""
        score = 0.0
        title = (idea.get("title") or "").lower()
        words = set(re.findall(r"\b\w+\b", title))

        # Title has a number
        if re.search(r"\b\d+\b", title):
            score += 15.0

        # Negative framing
        if words & _NEGATIVE_WORDS:
            score += 10.0

        # Secret / truth / reveal framing
        if words & _SECRET_WORDS:
            score += 12.0

        # Cross-category idea (multiple categories listed)
        categories = idea.get("categories") or idea.get("category", "")
        if isinstance(categories, list) and len(categories) > 1:
            score += 20.0
        elif isinstance(categories, str) and "," in categories:
            score += 20.0
        elif idea.get("cat_a") and idea.get("cat_b"):
            score += 20.0

        # Has supporting data / stat
        has_data = bool(
            idea.get("key_stat")
            or idea.get("stat")
            or idea.get("finding")
            or idea.get("data_point")
        )
        if has_data:
            score += 15.0

        # Timely/trending signal
        if words & _TIMELY_WORDS:
            score += 15.0

        # Ideal title length: 7–15 words
        word_count = len(title.split())
        if 7 <= word_count <= 15:
            score += 13.0

        return min(score, 100.0)

    def rank_ideas(self, ideas: List[Dict], n: int = 10) -> List[Dict]:
        """Add synthesis_score to each idea and return top-n sorted descending."""
        scored = []
        for idea in ideas:
            try:
                s = self.score_idea(idea)
            except Exception as exc:
                log.warning("rank_ideas: score_idea failed — %s", exc)
                s = 0.0
            enriched = dict(idea)
            enriched["synthesis_score"] = round(s, 1)
            scored.append(enriched)

        scored.sort(key=lambda x: x["synthesis_score"], reverse=True)
        return scored[:n]

    def find_best_intersection(self, ideas: List[Dict]) -> Optional[Dict]:
        """Return the idea with the most category overlap (highest cross-category signal)."""
        best: Optional[Dict] = None
        best_overlap = -1

        for idea in ideas:
            overlap = 0
            categories = idea.get("categories") or idea.get("category", "")
            if isinstance(categories, list):
                overlap = len(categories)
            elif isinstance(categories, str) and "," in categories:
                overlap = len(categories.split(","))
            elif idea.get("cat_a") and idea.get("cat_b"):
                overlap = 2
                if idea.get("cat_c"):
                    overlap = 3

            if overlap > best_overlap:
                best_overlap = overlap
                best = idea

        return best if best_overlap > 1 else None


# ---------------------------------------------------------------------------
# Class 5: ContentSynthesizer (orchestrator)
# ---------------------------------------------------------------------------

class ContentSynthesizer:
    """Orchestrates the full synthesis pipeline from raw sources to ranked video ideas."""

    def __init__(self, memory=None):
        self._memory     = memory
        self._matcher    = SourceMatcher()
        self._narrative  = NarrativeBuilder()
        self._transformer = ContentTransformer()
        self._scorer     = IdeaScorer()

    def _get_memory(self, memory=None):
        return memory or self._memory

    def synthesize_single(self, item: Dict) -> List[Dict]:
        """Generate 12 angle-variants from a single source item."""
        topic    = item.get("topic") or item.get("title", "unknown topic")
        category = item.get("category", "general")
        angles   = self._transformer.generate_12_angles(topic, category=category)
        # Enrich each angle with source metadata
        results = []
        for angle in angles:
            enriched = dict(angle)
            enriched["source_item"] = item
            enriched["key_stat"]    = item.get("key_stat") or item.get("stat", "")
            results.append(enriched)
        return results

    def synthesize_from_sources(
        self,
        items: List[Dict],
        n_ideas: int = 10,
    ) -> List[Dict]:
        """
        Full pipeline: raw source items → ranked video ideas.

        Steps:
          1. Single-source transformations (12 angles each)
          2. Pair-based narrative synthesis for related source pairs
          3. Score and rank all generated ideas
        """
        if not items:
            return []

        all_ideas: List[Dict] = []

        # Step 1: single-source transformations
        for item in items:
            try:
                concepts = self.synthesize_single(item)
                all_ideas.extend(concepts)
            except Exception as exc:
                log.warning("synthesize_from_sources: synthesize_single failed — %s", exc)

        # Step 2: pair-based narratives for related sources
        if len(items) >= MIN_SOURCES_FOR_SYNTHESIS:
            try:
                related_pairs = self._matcher.find_related_sources(items, threshold=0.2)
            except Exception as exc:
                log.warning("synthesize_from_sources: find_related_sources failed — %s", exc)
                related_pairs = []

            for src_a, src_b, similarity in related_pairs[:10]:  # cap at 10 pairs
                try:
                    category = src_a.get("category") or src_b.get("category", "general")
                    narrative = self._narrative.build_narrative(
                        [src_a, src_b], category=category
                    )
                    for title in narrative.get("title_suggestions", []):
                        all_ideas.append({
                            "title":       title,
                            "topic":       (
                                src_a.get("topic")
                                or src_a.get("title", "")
                            ),
                            "cat_a":       src_a.get("category", ""),
                            "cat_b":       src_b.get("category", ""),
                            "categories":  [
                                src_a.get("category", ""),
                                src_b.get("category", ""),
                            ],
                            "hook":        narrative.get("hook", ""),
                            "conflict":    narrative.get("conflict", ""),
                            "revelation":  narrative.get("revelation", ""),
                            "similarity":  similarity,
                            "synthesis_pattern": "pair_narrative",
                            "created_at":  datetime.now(timezone.utc).isoformat(),
                        })
                except Exception as exc:
                    log.warning("synthesize_from_sources: narrative build failed — %s", exc)

        # Step 3: rank
        try:
            ranked = self._scorer.rank_ideas(all_ideas, n=n_ideas)
        except Exception as exc:
            log.warning("synthesize_from_sources: rank_ideas failed — %s", exc)
            ranked = all_ideas[:n_ideas]

        return ranked

    def run_daily_synthesis(self, memory=None) -> Dict:
        """
        Pull source items from memory, run synthesis, save ideas, return summary.

        Memory calls:
          - memory.get_content_ideas(status='pending', n=20) for existing ideas (dedup)
          - memory.get_category_stats(n=50) as a source of recent topics
          - memory.save_content_idea(data) for each new idea
        """
        mem = self._get_memory(memory)
        log.info(
            "ContentSynthesizer: starting daily synthesis — %s",
            datetime.now(timezone.utc).isoformat(),
        )

        source_items: List[Dict] = []

        # Pull recent category stats as proxy source items
        if mem is not None:
            try:
                stats = mem.get_category_stats(n=50) or []
                for stat in stats:
                    cat  = stat.get("category", "general")
                    source_items.append({
                        "topic":      cat.replace("_", " ").title(),
                        "category":   cat,
                        "key_stat":   f"RPM ${stat.get('rpm', 0):.1f}, {stat.get('views', 0)} views",
                        "source_type": "statistics",
                    })
            except Exception as exc:
                log.warning("run_daily_synthesis: get_category_stats failed — %s", exc)

        # If no source items available, generate a minimal synthetic set
        if not source_items:
            log.info("run_daily_synthesis: no source items from memory — using defaults")
            source_items = [
                {"topic": "Artificial Intelligence",    "category": "ai_automation",    "source_type": "news_article"},
                {"topic": "Personal Finance Mistakes",  "category": "personal_finance",  "source_type": "blog_post"},
                {"topic": "Health and Longevity",       "category": "health_medical",    "source_type": "research_paper"},
                {"topic": "Climate Change Economics",   "category": "climate_energy",    "source_type": "data_file"},
                {"topic": "Psychology of Success",      "category": "psychology",        "source_type": "book"},
            ]

        # Run synthesis
        ideas = []
        try:
            ideas = self.synthesize_from_sources(source_items, n_ideas=20)
        except Exception as exc:
            log.warning("run_daily_synthesis: synthesize_from_sources failed — %s", exc)

        # Save ideas to memory (deduplicate by title)
        saved_count = 0
        if mem is not None:
            existing_titles: set = set()
            try:
                existing = mem.get_content_ideas(status="pending", n=50) or []
                existing_titles = {e.get("title", "") for e in existing}
            except Exception as exc:
                log.warning("run_daily_synthesis: get_content_ideas failed — %s", exc)

            for idea in ideas:
                title = idea.get("title", "")
                if not title or title in existing_titles:
                    continue
                record = {
                    "title":           title,
                    "topic":           idea.get("topic", ""),
                    "category":        idea.get("category") or idea.get("cat_a", "general"),
                    "status":          "pending",
                    "synthesis_score": idea.get("synthesis_score", 0.0),
                    "hook":            idea.get("hook", ""),
                    "created_at":      datetime.now(timezone.utc).isoformat(),
                }
                try:
                    mem.save_content_idea(record)
                    existing_titles.add(title)
                    saved_count += 1
                except Exception as exc:
                    log.warning("run_daily_synthesis: save_content_idea failed — %s", exc)

        top_3 = ideas[:3]
        result = {
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "ideas_generated":  len(ideas),
            "ideas_saved":      saved_count,
            "sources_used":     len(source_items),
            "top_3":            top_3,
        }
        log.info(
            "ContentSynthesizer: daily synthesis complete — %d ideas generated, %d saved",
            len(ideas),
            saved_count,
        )
        return result

    def print_report(self, result: Dict) -> None:
        """Print a formatted synthesis report to stdout."""
        print("\n" + "=" * 60)
        print("  CONTENT SYNTHESIZER — DAILY SYNTHESIS REPORT")
        print("=" * 60)
        print(f"  Generated:  {result.get('generated_at', 'N/A')}")
        print(f"  Sources:    {result.get('sources_used', 0)}")
        print(f"  Ideas:      {result.get('ideas_generated', 0)} generated, "
              f"{result.get('ideas_saved', 0)} saved to memory")

        top_3 = result.get("top_3", [])
        if top_3:
            print(f"\n  TOP 3 IDEAS:")
            for idx, idea in enumerate(top_3, start=1):
                score = idea.get("synthesis_score", 0.0)
                title = idea.get("title", "N/A")
                cat   = idea.get("category") or idea.get("cat_a", "general")
                print(f"\n  [{idx}] Score: {score:.1f}/100")
                print(f"       Title:    {title}")
                print(f"       Category: {cat}")
                hook = idea.get("hook", "")
                if hook:
                    print(f"       Hook:     {hook[:80]}{'...' if len(hook) > 80 else ''}")

        print("=" * 60 + "\n")
