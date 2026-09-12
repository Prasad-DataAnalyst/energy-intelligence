"""
content/category_universe.py — Category Intelligence & Planning
===============================================================
Manages the full universe of video categories, their RPM tiers,
weekly scheduling, cross-category intersections, and title generation.
Drives data-informed decisions about what to create and when.
"""
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("brain.category_universe")

HERE = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CATEGORY_UNIVERSE = {
    # TIER 1 — $15-50 RPM
    "personal_finance":  {"tier": 1, "rpm_low": 15, "rpm_high": 50, "emoji": "💰", "day": 0, "keywords": ["investing", "budgeting", "FIRE", "wealth", "passive income", "stocks", "retirement"]},
    "business_startup":  {"tier": 1, "rpm_low": 12, "rpm_high": 40, "emoji": "💰", "day": 1, "keywords": ["entrepreneurship", "side hustle", "startup", "revenue", "profit", "business model"]},
    "technology":        {"tier": 1, "rpm_low": 10, "rpm_high": 35, "emoji": "💰", "day": 1, "keywords": ["AI", "software", "gadgets", "reviews", "tech", "automation", "app"]},
    "health_medical":    {"tier": 1, "rpm_low": 10, "rpm_high": 30, "emoji": "💰", "day": 2, "keywords": ["mental health", "nutrition", "fitness", "medicine", "wellness", "diet", "exercise"]},
    "legal_law":         {"tier": 1, "rpm_low": 15, "rpm_high": 45, "emoji": "💰", "day": 3, "keywords": ["rights", "contracts", "lawsuit", "legal", "law", "court", "attorney"]},
    "real_estate":       {"tier": 1, "rpm_low": 12, "rpm_high": 40, "emoji": "💰", "day": 0, "keywords": ["investing", "property", "mortgage", "housing market", "rent", "buying", "selling"]},
    "insurance":         {"tier": 1, "rpm_low": 15, "rpm_high": 50, "emoji": "💰", "day": 0, "keywords": ["insurance", "coverage", "premiums", "claims", "life insurance", "health insurance"]},
    "career_education":  {"tier": 1, "rpm_low": 8,  "rpm_high": 25, "emoji": "💰", "day": 4, "keywords": ["career", "skills", "degree", "job market", "salary", "promotion", "resume"]},
    # TIER 2 — $8-15 RPM
    "science":           {"tier": 2, "rpm_low": 8,  "rpm_high": 15, "emoji": "🎯", "day": 2, "keywords": ["physics", "biology", "space", "climate", "discovery", "research", "experiment"]},
    "history":           {"tier": 2, "rpm_low": 7,  "rpm_high": 14, "emoji": "🎯", "day": 3, "keywords": ["war", "empire", "ancient", "historical", "revolution", "dynasty", "civilization"]},
    "true_crime":        {"tier": 2, "rpm_low": 8,  "rpm_high": 15, "emoji": "🎯", "day": 3, "keywords": ["crime", "murder", "investigation", "detective", "case", "serial killer", "fraud"]},
    "politics":          {"tier": 2, "rpm_low": 7,  "rpm_high": 14, "emoji": "🎯", "day": 4, "keywords": ["election", "policy", "government", "president", "congress", "geopolitics", "democracy"]},
    "psychology":        {"tier": 2, "rpm_low": 8,  "rpm_high": 15, "emoji": "🎯", "day": 4, "keywords": ["behavior", "mindset", "relationships", "cognitive", "mental", "emotion", "bias"]},
    "philosophy":        {"tier": 2, "rpm_low": 7,  "rpm_high": 13, "emoji": "🎯", "day": 6, "keywords": ["ethics", "meaning", "consciousness", "morality", "existence", "truth", "Stoicism"]},
    "self_improvement":  {"tier": 2, "rpm_low": 7,  "rpm_high": 15, "emoji": "🎯", "day": 4, "keywords": ["productivity", "habits", "discipline", "success", "morning routine", "focus", "goals"]},
    "food_cooking":      {"tier": 2, "rpm_low": 5,  "rpm_high": 12, "emoji": "🎯", "day": 5, "keywords": ["recipe", "cooking", "food science", "restaurant", "meal prep", "nutrition", "cuisine"]},
    "travel":            {"tier": 2, "rpm_low": 5,  "rpm_high": 12, "emoji": "🎯", "day": 5, "keywords": ["destination", "travel tips", "culture", "budget travel", "adventure", "explore", "trip"]},
    # TIER 3 — $3-8 RPM
    "entertainment":     {"tier": 3, "rpm_low": 3,  "rpm_high": 8,  "emoji": "📱", "day": 5, "keywords": ["movies", "music", "celebrity", "pop culture", "TV shows", "streaming", "actor"]},
    "sports":            {"tier": 3, "rpm_low": 4,  "rpm_high": 9,  "emoji": "📱", "day": 5, "keywords": ["sports", "athlete", "championship", "game", "team", "player", "record"]},
    "gaming":            {"tier": 3, "rpm_low": 3,  "rpm_high": 8,  "emoji": "📱", "day": 5, "keywords": ["game", "gaming", "esports", "console", "PlayStation", "Xbox", "review"]},
    "animals_nature":    {"tier": 3, "rpm_low": 3,  "rpm_high": 7,  "emoji": "📱", "day": 6, "keywords": ["wildlife", "animals", "nature", "environment", "ecosystem", "pets", "species"]},
    "diy_crafts":        {"tier": 3, "rpm_low": 3,  "rpm_high": 7,  "emoji": "📱", "day": 6, "keywords": ["DIY", "crafts", "project", "tutorial", "build", "make", "how to"]},
    "relationships":     {"tier": 3, "rpm_low": 4,  "rpm_high": 9,  "emoji": "📱", "day": 4, "keywords": ["relationship", "dating", "marriage", "family", "communication", "love", "breakup"]},
    "fashion_beauty":    {"tier": 3, "rpm_low": 3,  "rpm_high": 8,  "emoji": "📱", "day": 5, "keywords": ["fashion", "beauty", "makeup", "style", "trends", "skincare", "outfit"]},
    # TIER 4 — Emerging High Value
    "ai_automation":     {"tier": 4, "rpm_low": 10, "rpm_high": 35, "emoji": "🚀", "day": 1, "keywords": ["AI tools", "automation", "ChatGPT", "machine learning", "robot", "future of work", "AGI"]},
    "climate_energy":    {"tier": 4, "rpm_low": 8,  "rpm_high": 20, "emoji": "🚀", "day": 2, "keywords": ["renewable energy", "climate change", "solar", "wind", "EV", "carbon", "sustainability"]},
    "geopolitics":       {"tier": 4, "rpm_low": 7,  "rpm_high": 18, "emoji": "🚀", "day": 3, "keywords": ["geopolitics", "global power", "war", "trade war", "sanctions", "alliance", "empire"]},
    "future_tech":       {"tier": 4, "rpm_low": 9,  "rpm_high": 28, "emoji": "🚀", "day": 1, "keywords": ["robotics", "biotech", "quantum", "nanotechnology", "longevity", "brain-computer", "fusion"]},
    "spirituality":      {"tier": 4, "rpm_low": 5,  "rpm_high": 15, "emoji": "🚀", "day": 6, "keywords": ["meditation", "mindfulness", "spirituality", "consciousness", "purpose", "meaning", "Buddhism"]},
    "alternative_history": {"tier": 4, "rpm_low": 6, "rpm_high": 16, "emoji": "🚀", "day": 3, "keywords": ["what if", "conspiracy", "mystery", "secret", "cover-up", "lost civilization", "ancient"]},
}

# Cross-category viral intersection formulas
INTERSECTION_FORMULAS = [
    ("personal_finance", "psychology",      "Why Smart People Stay Poor"),
    ("technology",       "health_medical",  "How Your Phone Is Destroying Your Brain"),
    ("science",          "personal_finance","The Science of Getting Rich"),
    ("history",          "technology",      "Ancient Inventions We Still Can't Explain"),
    ("true_crime",       "business_startup","The Business Empires Built on Crime"),
    ("psychology",       "relationships",   "The Psychology Behind Why People Cheat"),
    ("science",          "self_improvement","The Neuroscience of Peak Performance"),
    ("ai_automation",    "career_education","Jobs AI Will Destroy First"),
    ("personal_finance", "real_estate",     "The Real Estate Secret Banks Don't Want You to Know"),
    ("history",          "geopolitics",     "How History Predicted The Current War"),
    ("health_medical",   "food_cooking",    "The Food Industry Lies You've Been Fed"),
    ("climate_energy",   "business_startup","The Green Energy Businesses Making Millionaires"),
]

VIRAL_TITLE_TEMPLATES = [
    "The Truth About {topic}",
    "Why {topic} Is Not What You Think",
    "{n} Things Nobody Tells You About {topic}",
    "I Tried {topic} For {time} — Here's What Happened",
    "The Dark Side of {topic}",
    "How {topic} Is {negative_outcome}",
    "The {topic} Secret {group} Doesn't Want You To Know",
    "{topic} Changed My Life — Here's How",
    "The {country} {topic} Method That {result}",
    "What Happens To Your {life_area} After {topic}",
    "{topic_a} vs {topic_b}: Which One Actually Works?",
    "The {year} Guide to {topic} Nobody Else Will Tell You",
]

WEEKLY_SCHEDULE = {
    0: ["personal_finance", "real_estate", "insurance"],          # Monday
    1: ["technology", "ai_automation", "business_startup"],       # Tuesday
    2: ["science", "health_medical", "climate_energy"],           # Wednesday
    3: ["history", "true_crime", "geopolitics"],                  # Thursday
    4: ["psychology", "self_improvement", "career_education"],    # Friday
    5: ["entertainment", "sports", "food_cooking"],               # Saturday
    6: None,  # Sunday = trending wildcard
}

# Default fill-in values for template slots
_TEMPLATE_DEFAULTS = {
    "n":               "7",
    "time":            "30 Days",
    "negative_outcome": "Ruining Your Future",
    "group":           "Experts",
    "country":         "Japanese",
    "result":          "Actually Works",
    "life_area":       "Health",
    "topic_a":         "Option A",
    "topic_b":         "Option B",
    "year":            str(datetime.now(timezone.utc).year),
}

# Negative-framing signal words for title scoring
_NEGATIVE_WORDS = {
    "destroying", "ruining", "killing", "danger", "dark", "worst",
    "never", "stop", "avoid", "toxic", "dangerous", "failing", "broken",
    "lies", "lie", "scam", "fake", "bad", "wrong", "terrible",
}

# Secret/truth signal words for title scoring
_SECRET_WORDS = {
    "truth", "secret", "revealed", "hidden", "untold", "nobody", "exposed",
    "real", "actually", "shocking", "surprising", "unknown", "unspoken",
}


# ---------------------------------------------------------------------------
# Class 1: CategoryPerformanceTracker
# ---------------------------------------------------------------------------

class CategoryPerformanceTracker:
    """Tracks per-category performance stats and recommends best daily picks."""

    def __init__(self, memory=None):
        self._memory = memory

    def _get_memory(self, memory=None):
        """Return provided memory or fallback to instance memory."""
        return memory or self._memory

    def update_category_stats(
        self,
        category: str,
        views: int,
        rpm: float,
        subs_gained: int,
        memory=None,
    ) -> None:
        """Compute revenue from a video and persist category stats to memory."""
        mem = self._get_memory(memory)
        revenue = (views * rpm) / 1000.0
        data = {
            "category":    category,
            "views":       views,
            "rpm":         rpm,
            "subs_gained": subs_gained,
            "revenue":     revenue,
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        if mem is not None:
            try:
                mem.save_category_stat(data)
            except Exception as exc:
                log.warning("update_category_stats: memory write failed — %s", exc)
        log.debug(
            "category=%s views=%d rpm=%.2f revenue=%.2f",
            category, views, rpm, revenue,
        )

    def get_best_categories(self, n: int = 5, memory=None) -> List[Dict]:
        """Return top-n categories by average revenue per video (descending)."""
        mem = self._get_memory(memory)
        if mem is None:
            return []
        try:
            stats: List[Dict] = mem.get_category_stats(n=200) or []
        except Exception as exc:
            log.warning("get_best_categories: memory read failed — %s", exc)
            return []

        # Aggregate per category
        agg: Dict[str, Dict] = {}
        for row in stats:
            cat = row.get("category", "")
            if not cat:
                continue
            if cat not in agg:
                agg[cat] = {"total_revenue": 0.0, "count": 0, "total_views": 0}
            agg[cat]["total_revenue"] += float(row.get("revenue", 0.0))
            agg[cat]["count"]         += 1
            agg[cat]["total_views"]   += int(row.get("views", 0))

        results: List[Dict] = []
        for cat, data in agg.items():
            count = data["count"] or 1
            rev_per_video = data["total_revenue"] / count
            cat_info = CATEGORY_UNIVERSE.get(cat, {})
            results.append({
                "category":         cat,
                "revenue_per_video": round(rev_per_video, 2),
                "total_videos":     data["count"],
                "total_views":      data["total_views"],
                "tier":             cat_info.get("tier", 0),
                "rpm_high":         cat_info.get("rpm_high", 0),
                "emoji":            cat_info.get("emoji", ""),
            })

        results.sort(key=lambda x: x["revenue_per_video"], reverse=True)
        return results[:n]

    def get_today_recommended(self, memory=None) -> List[str]:
        """Return today's scheduled categories plus top performers."""
        weekday = datetime.now(timezone.utc).weekday()  # 0=Monday
        scheduled = WEEKLY_SCHEDULE.get(weekday) or []
        try:
            best = self.get_best_categories(n=3, memory=memory)
            best_cats = [b["category"] for b in best]
        except Exception as exc:
            log.warning("get_today_recommended: best-category fetch failed — %s", exc)
            best_cats = []

        # Merge: scheduled first, then best performers not already included
        combined: List[str] = list(scheduled)
        for cat in best_cats:
            if cat not in combined:
                combined.append(cat)
        return combined

    def calculate_revenue_per_video(self, category: str, memory=None) -> float:
        """Return the average revenue per video for a given category."""
        mem = self._get_memory(memory)
        if mem is None:
            # Fall back to midpoint RPM estimate if no memory available
            info = CATEGORY_UNIVERSE.get(category, {})
            rpm_mid = (info.get("rpm_low", 5) + info.get("rpm_high", 10)) / 2.0
            return round(rpm_mid * 1000 / 1000, 2)  # assumes ~1000 average views base
        try:
            stats: List[Dict] = mem.get_category_stats(category=category, n=50) or []
        except Exception as exc:
            log.warning("calculate_revenue_per_video: memory read failed — %s", exc)
            return 0.0

        if not stats:
            return 0.0

        revenues = [float(r.get("revenue", 0.0)) for r in stats if r.get("revenue")]
        if not revenues:
            return 0.0
        return round(sum(revenues) / len(revenues), 2)


# ---------------------------------------------------------------------------
# Class 2: CrossCategoryMapper
# ---------------------------------------------------------------------------

class CrossCategoryMapper:
    """Maps cross-category intersection opportunities and generates combined titles."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        """Lazy-init Anthropic client only when API key is available."""
        if self._client is not None:
            return self._client
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            return self._client
        except Exception as exc:
            log.warning("CrossCategoryMapper: Anthropic init failed — %s", exc)
            return None

    def find_intersection_topics(self, cat_a: str, cat_b: str) -> List[str]:
        """Return titles for a category pair from INTERSECTION_FORMULAS or via Claude."""
        # Look up predefined intersections (order-independent)
        predefined = []
        for a, b, title in INTERSECTION_FORMULAS:
            if (a == cat_a and b == cat_b) or (a == cat_b and b == cat_a):
                predefined.append(title)

        if predefined:
            return predefined

        # Try Claude if no predefined formula exists
        client = self._get_client()
        if client is not None:
            try:
                prompt = (
                    f"Generate 3 YouTube title ideas that combine the categories "
                    f"'{cat_a}' and '{cat_b}'. "
                    f"Make them curiosity-gap titles that attract both audiences. "
                    f"Return only the 3 titles, one per line."
                )
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip()
                titles = [t.strip("•-– ").strip() for t in raw.splitlines() if t.strip()]
                titles = [t for t in titles if len(t) > 5][:3]
                if titles:
                    return titles
            except Exception as exc:
                log.warning("find_intersection_topics: Claude call failed — %s", exc)

        # Final fallback: template-based title
        return [f"The {cat_a.replace('_', ' ').title()} Secret That {cat_b.replace('_', ' ').title()} Experts Know"]

    def generate_intersection_title(self, cat_a: str, cat_b: str, topic: str) -> str:
        """Generate a single intersection title using Claude or a template fallback."""
        client = self._get_client()
        if client is not None:
            try:
                prompt = (
                    f"Generate 3 YouTube title ideas that combine {cat_a} and {cat_b} "
                    f"around the topic: {topic}. "
                    f"Make them curiosity-gap titles that attract both audiences."
                )
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip()
                lines = [t.strip("•-– 1234567890.").strip() for t in raw.splitlines() if t.strip()]
                lines = [t for t in lines if len(t) > 5]
                if lines:
                    return lines[0]
            except Exception as exc:
                log.warning("generate_intersection_title: Claude call failed — %s", exc)

        return f"The {cat_a.replace('_', ' ').title()} Secret That {cat_b.replace('_', ' ').title()} Experts Know"

    def get_all_intersections(self) -> List[Dict]:
        """Return all predefined intersections enriched with a combined RPM estimate."""
        results = []
        for cat_a, cat_b, title in INTERSECTION_FORMULAS:
            info_a = CATEGORY_UNIVERSE.get(cat_a, {})
            info_b = CATEGORY_UNIVERSE.get(cat_b, {})
            rpm_a  = (info_a.get("rpm_low", 5) + info_a.get("rpm_high", 10)) / 2.0
            rpm_b  = (info_b.get("rpm_low", 5) + info_b.get("rpm_high", 10)) / 2.0
            # Combined estimate: weighted average biased toward the higher tier
            combined_rpm = round(max(rpm_a, rpm_b) * 0.7 + min(rpm_a, rpm_b) * 0.3, 2)
            results.append({
                "cat_a":               cat_a,
                "cat_b":               cat_b,
                "title":               title,
                "combined_rpm_estimate": combined_rpm,
            })
        return results

    def find_triple_intersection(self, cats: List[str]) -> Optional[str]:
        """If 3 categories share keyword overlap, generate a combined title."""
        if len(cats) < 3:
            return None

        # Collect keyword sets for each category
        kw_sets: List[Tuple[str, set]] = []
        for cat in cats[:3]:
            info = CATEGORY_UNIVERSE.get(cat, {})
            kws  = set(k.lower() for k in info.get("keywords", []))
            kw_sets.append((cat, kws))

        # Find shared keywords across all three
        shared = kw_sets[0][1] & kw_sets[1][1] & kw_sets[2][1]
        common_thread = next(iter(shared), None) if shared else None

        cat_a, cat_b, cat_c = [c for c, _ in kw_sets]

        client = self._get_client()
        if client is not None:
            try:
                thread_hint = f" connected by '{common_thread}'" if common_thread else ""
                prompt = (
                    f"Generate one viral YouTube title that combines the topics: "
                    f"{cat_a}, {cat_b}, and {cat_c}{thread_hint}. "
                    f"Return only the title."
                )
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=128,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip().strip('"').strip("'")
                if raw:
                    return raw
            except Exception as exc:
                log.warning("find_triple_intersection: Claude call failed — %s", exc)

        # Fallback: compose a title from the category names
        parts = [c.replace("_", " ").title() for c in [cat_a, cat_b, cat_c]]
        if common_thread:
            return f"How {parts[0]}, {parts[1]}, and {parts[2]} Are All Connected to {common_thread.title()}"
        return f"The Surprising Link Between {parts[0]}, {parts[1]}, and {parts[2]}"


# ---------------------------------------------------------------------------
# Class 3: TitleGenerator
# ---------------------------------------------------------------------------

class TitleGenerator:
    """Generates viral YouTube titles using templates and optional Claude enhancement."""

    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not api_key:
            return None
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=api_key)
            return self._client
        except Exception as exc:
            log.warning("TitleGenerator: Anthropic init failed — %s", exc)
            return None

    def _fill_template(self, template: str, topic: str, **kwargs) -> str:
        """Fill template slots with topic and optional kwargs, using defaults for remainder."""
        slots = {**_TEMPLATE_DEFAULTS, "topic": topic, "topic_a": topic, "topic_b": topic}
        slots.update(kwargs)
        try:
            return template.format_map(slots)
        except KeyError as exc:
            log.debug("_fill_template: unresolved slot %s in '%s'", exc, template)
            # Manually replace remaining {slot} patterns with topic
            result = template
            for m in re.findall(r"\{(\w+)\}", template):
                result = result.replace("{" + m + "}", topic)
            return result

    def generate_titles(self, topic: str, category: str, n: int = 12) -> List[str]:
        """Generate n titles: up to 3 from Claude, rest from templates."""
        titles: List[str] = []

        # Attempt Claude for the first 3 titles
        client = self._get_client()
        if client is not None:
            try:
                prompt = (
                    f"Generate 3 viral YouTube titles about '{topic}' in the '{category}' niche. "
                    f"Use curiosity-gap, numbers, or negative framing. "
                    f"Return only the 3 titles, one per line."
                )
                message = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = message.content[0].text.strip()
                claude_titles = [
                    t.strip("•-– 1234567890.").strip()
                    for t in raw.splitlines()
                    if t.strip()
                ]
                claude_titles = [t for t in claude_titles if len(t) > 5][:3]
                titles.extend(claude_titles)
            except Exception as exc:
                log.warning("generate_titles: Claude call failed — %s", exc)

        # Fill remaining slots from templates
        for template in VIRAL_TITLE_TEMPLATES:
            if len(titles) >= n:
                break
            candidate = self._fill_template(template, topic)
            if candidate not in titles:
                titles.append(candidate)

        return titles[:n]

    def get_angle_variants(self, topic: str) -> List[Dict]:
        """Return all 12 angle variants with predicted CTR bonus."""
        # Rough CTR bonus heuristics per template pattern
        ctr_bonuses = [12, 10, 15, 11, 13, 10, 14, 8, 9, 11, 12, 10]
        results = []
        for idx, (template, bonus) in enumerate(
            zip(VIRAL_TITLE_TEMPLATES, ctr_bonuses), start=1
        ):
            title = self._fill_template(template, topic)
            results.append({
                "angle_number":       idx,
                "template":           template,
                "title":              title,
                "predicted_ctr_bonus": bonus,
            })
        return results

    def score_title(self, title: str) -> Dict:
        """Score a title on viral-signal dimensions; returns component flags and total."""
        lower = title.lower()
        words = set(re.findall(r"\b\w+\b", lower))

        has_number       = bool(re.search(r"\b\d+\b", title))
        has_negative     = bool(words & _NEGATIVE_WORDS)
        has_secret       = bool(words & _SECRET_WORDS)
        # Curiosity gap: title ends with question-like phrase or contains "why/how/what"
        has_curiosity    = bool(re.search(r"\b(why|how|what|here'?s|this is)\b", lower))

        score = 0.0
        if has_curiosity:    score += 25.0
        if has_number:       score += 20.0
        if has_secret:       score += 22.0
        if has_negative:     score += 18.0
        # Length bonus: 7-15 words is ideal
        word_count = len(title.split())
        if 7 <= word_count <= 15:
            score += 15.0
        score = min(score, 100.0)

        return {
            "curiosity_gap":    has_curiosity,
            "number_present":   has_number,
            "negative_framing": has_negative,
            "secret_framing":   has_secret,
            "word_count":       word_count,
            "total_score":      round(score, 1),
        }


# ---------------------------------------------------------------------------
# Class 4: CategoryOptimizer
# ---------------------------------------------------------------------------

class CategoryOptimizer:
    """Combines scheduling, performance data, and trending topics into actionable plans."""

    def __init__(self, memory=None):
        self._memory  = memory
        self._tracker = CategoryPerformanceTracker(memory=memory)
        self._titler  = TitleGenerator()

    def _get_memory(self, memory=None):
        return memory or self._memory

    def get_category_info(self, category: str) -> Dict:
        """Return CATEGORY_UNIVERSE entry for a category, with safe defaults."""
        defaults = {
            "tier":     0,
            "rpm_low":  3,
            "rpm_high": 10,
            "emoji":    "📌",
            "day":      -1,
            "keywords": [],
        }
        info = CATEGORY_UNIVERSE.get(category, {})
        return {**defaults, **info}

    def get_optimal_category_for_today(self, memory=None) -> Dict:
        """Return the single best category recommendation for today."""
        mem = self._get_memory(memory)
        weekday = datetime.now(timezone.utc).weekday()
        scheduled = WEEKLY_SCHEDULE.get(weekday) or []

        # Attempt to rank by performance data
        try:
            best = self._tracker.get_best_categories(n=5, memory=mem)
            best_cats = [b["category"] for b in best]
        except Exception as exc:
            log.warning("get_optimal_category_for_today: tracker failed — %s", exc)
            best_cats = []

        # Prefer intersection of scheduled + best; fall back to scheduled, then best
        candidate = None
        for cat in best_cats:
            if cat in scheduled:
                candidate = cat
                break
        if candidate is None:
            candidate = scheduled[0] if scheduled else (best_cats[0] if best_cats else "personal_finance")

        info = self.get_category_info(candidate)
        rpm_mid = (info["rpm_low"] + info["rpm_high"]) / 2.0

        if scheduled and candidate in scheduled:
            reason = f"Scheduled for {['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][weekday]} and historically strong performer."
        elif best_cats and candidate == best_cats[0]:
            reason = "Top revenue-per-video category based on historical data."
        else:
            reason = f"Default scheduled category for {['Monday','Tuesday','Wednesday','Thursday','Friday','Saturday','Sunday'][weekday]}."

        # Generate a couple of sample titles to show count
        sample_topic = info["keywords"][0] if info["keywords"] else candidate.replace("_", " ")
        titles = self._titler.generate_titles(sample_topic, candidate, n=12)

        return {
            "category":      candidate,
            "reason":        reason,
            "predicted_rpm": round(rpm_mid, 2),
            "titles_count":  len(titles),
            "tier":          info["tier"],
            "emoji":         info["emoji"],
        }

    def get_weekly_plan(self, memory=None) -> Dict:
        """Return a full 7-day category plan."""
        mem = self._get_memory(memory)
        plan: Dict[int, List[str]] = {}
        for day in range(7):
            scheduled = WEEKLY_SCHEDULE.get(day)
            if scheduled is None:
                # Sunday: pick trending wildcard from best performers
                try:
                    best = self._tracker.get_best_categories(n=3, memory=mem)
                    plan[day] = [b["category"] for b in best] or ["personal_finance"]
                except Exception:
                    plan[day] = ["personal_finance"]
            else:
                plan[day] = list(scheduled)
        return plan

    def suggest_next_video(
        self,
        trending_topics: List[str] = None,
        memory=None,
    ) -> Dict:
        """Combine category scheduling and trending topics into a single video suggestion."""
        mem = self._get_memory(memory)
        optimal = self.get_optimal_category_for_today(memory=mem)
        category = optimal["category"]
        info = self.get_category_info(category)

        # Pick best topic: trending topic that matches category keywords, else first keyword
        topic = None
        if trending_topics:
            cat_keywords = [kw.lower() for kw in info.get("keywords", [])]
            for t in trending_topics:
                t_lower = t.lower()
                if any(kw in t_lower for kw in cat_keywords):
                    topic = t
                    break

        if topic is None:
            topic = (
                trending_topics[0]
                if trending_topics
                else (info["keywords"][0] if info["keywords"] else category.replace("_", " "))
            )

        titles = self._titler.generate_titles(topic, category, n=12)

        reasoning = (
            f"Category '{category}' is scheduled today (tier {info['tier']}, "
            f"RPM ~${info['rpm_low']}-${info['rpm_high']}). "
            f"Topic '{topic}' best matches current trends and category keywords."
        )

        return {
            "category":         category,
            "topic":            topic,
            "suggested_titles": titles,
            "reasoning":        reasoning,
            "predicted_rpm":    optimal["predicted_rpm"],
        }


# ---------------------------------------------------------------------------
# Class 5: CategoryUniverse (orchestrator)
# ---------------------------------------------------------------------------

class CategoryUniverse:
    """Orchestrates the full daily category-planning cycle."""

    def __init__(self, memory=None):
        self._memory   = memory
        self._tracker  = CategoryPerformanceTracker(memory=memory)
        self._mapper   = CrossCategoryMapper()
        self._titler   = TitleGenerator()
        self._optimizer = CategoryOptimizer(memory=memory)

    def run_daily_planning(
        self,
        trending: List[Dict] = None,
        memory=None,
    ) -> Dict:
        """Execute a full daily planning cycle and return a structured result."""
        mem = self._get_memory(memory)
        log.info("CategoryUniverse: running daily planning — %s", datetime.now(timezone.utc).isoformat())

        trending_topics: List[str] = []
        if trending:
            for item in trending:
                if isinstance(item, dict):
                    t = item.get("topic") or item.get("title") or item.get("keyword", "")
                else:
                    t = str(item)
                if t:
                    trending_topics.append(t)

        # 1. Optimal category + suggestion
        try:
            suggestion = self._optimizer.suggest_next_video(
                trending_topics=trending_topics or None, memory=mem
            )
        except Exception as exc:
            log.warning("run_daily_planning: suggest_next_video failed — %s", exc)
            suggestion = {}

        # 2. Weekly plan
        try:
            weekly_plan = self._optimizer.get_weekly_plan(memory=mem)
        except Exception as exc:
            log.warning("run_daily_planning: get_weekly_plan failed — %s", exc)
            weekly_plan = {}

        # 3. Best categories by performance
        try:
            best_categories = self._tracker.get_best_categories(n=5, memory=mem)
        except Exception as exc:
            log.warning("run_daily_planning: get_best_categories failed — %s", exc)
            best_categories = []

        # 4. Top intersections
        try:
            intersections = self._mapper.get_all_intersections()[:3]
        except Exception as exc:
            log.warning("run_daily_planning: get_all_intersections failed — %s", exc)
            intersections = []

        # 5. Save generated ideas to memory
        if mem is not None and suggestion.get("suggested_titles"):
            for title in suggestion["suggested_titles"][:5]:
                idea = {
                    "title":      title,
                    "category":   suggestion.get("category", ""),
                    "topic":      suggestion.get("topic", ""),
                    "status":     "pending",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                try:
                    mem.save_content_idea(idea)
                except Exception as exc:
                    log.warning("run_daily_planning: save_content_idea failed — %s", exc)

        result = {
            "generated_at":    datetime.now(timezone.utc).isoformat(),
            "suggestion":      suggestion,
            "weekly_plan":     weekly_plan,
            "best_categories": best_categories,
            "top_intersections": intersections,
            "trending_used":   trending_topics[:5] if trending_topics else [],
        }
        log.info(
            "CategoryUniverse: daily planning complete — category=%s titles=%d",
            suggestion.get("category", "N/A"),
            len(suggestion.get("suggested_titles", [])),
        )
        return result

    def _get_memory(self, memory=None):
        return memory or self._memory

    def print_report(self, result: Dict) -> None:
        """Print a formatted planning report to stdout."""
        print("\n" + "=" * 60)
        print("  CATEGORY UNIVERSE — DAILY PLANNING REPORT")
        print("=" * 60)
        print(f"  Generated: {result.get('generated_at', 'N/A')}")

        suggestion = result.get("suggestion", {})
        if suggestion:
            cat  = suggestion.get("category", "N/A")
            info = CATEGORY_UNIVERSE.get(cat, {})
            emoji = info.get("emoji", "")
            tier  = info.get("tier", "?")
            rpm   = suggestion.get("predicted_rpm", 0)
            print(f"\n  {emoji} TODAY'S PICK: {cat.upper().replace('_', ' ')} (Tier {tier})")
            print(f"  Predicted RPM: ${rpm:.2f}")
            print(f"  Reason: {suggestion.get('reasoning', '')}")
            titles = suggestion.get("suggested_titles", [])
            if titles:
                print(f"\n  Sample Titles ({len(titles)}):")
                for t in titles[:5]:
                    print(f"    • {t}")

        best = result.get("best_categories", [])
        if best:
            print(f"\n  TOP PERFORMING CATEGORIES:")
            for b in best:
                print(
                    f"    {b.get('emoji','')} {b['category']:<22} "
                    f"${b['revenue_per_video']:>6.2f}/video  "
                    f"({b['total_videos']} videos)"
                )

        intersections = result.get("top_intersections", [])
        if intersections:
            print(f"\n  TOP INTERSECTIONS:")
            for x in intersections:
                print(
                    f"    [{x['cat_a']} × {x['cat_b']}] "
                    f"RPM ~${x['combined_rpm_estimate']} — {x['title']}"
                )

        trending = result.get("trending_used", [])
        if trending:
            print(f"\n  TRENDING TOPICS USED: {', '.join(trending)}")

        print("=" * 60 + "\n")
