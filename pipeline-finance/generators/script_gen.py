"""
generators/script_gen.py — DriftWire326
Claude AI script generation with tier-aware weekday prompts,
Sunday theme routing, and Shorts 5-card format.
"""
import json
import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic

from config.settings import (
    ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CLAUDE_TEMPERATURE,
    SCRIPT_STYLES, HOOK_VARIATIONS,
)
from config.prompts import (
    SYSTEM_PERSONA,
    TOPIC_SELECTOR_PROMPT,
    WEEKDAY_SCRIPT_TIER1_PROMPT,
    WEEKDAY_SCRIPT_TIER2_PROMPT,
    WEEKDAY_SCRIPT_TIER3_PROMPT,
    SHORTS_SCRIPT_PROMPT,
    SUNDAY_INVESTMENT_SCRIPT_PROMPT,
    SUNDAY_INSURANCE_SCRIPT_PROMPT,
    SUNDAY_SAVINGS_SCRIPT_PROMPT,
    SUNDAY_BONUS_SCRIPT_PROMPT,
)
from monitor.usage_ledger import record

logger = logging.getLogger(__name__)

_TIER_PROMPTS = {
    "tier1": WEEKDAY_SCRIPT_TIER1_PROMPT,
    "tier2": WEEKDAY_SCRIPT_TIER2_PROMPT,
    "tier3": WEEKDAY_SCRIPT_TIER3_PROMPT,
}

_SUNDAY_PROMPTS = {
    "investment_banking": SUNDAY_INVESTMENT_SCRIPT_PROMPT,
    "insurance_protection": SUNDAY_INSURANCE_SCRIPT_PROMPT,
    "savings_wealth": SUNDAY_SAVINGS_SCRIPT_PROMPT,
    "rotating_bonus": SUNDAY_BONUS_SCRIPT_PROMPT,
}

# Words-per-minute by video type (used for duration estimation)
_WPM = {"weekday": 155, "sunday": 140, "shorts": 165}


# ── Data models ──────────────────────────────────────────────────────────────

@dataclass
class GeneratedScript:
    video_type: str                   # "weekday" | "sunday" | "shorts"
    title_draft: str
    script: str
    word_count: int
    estimated_duration_seconds: int
    segments: dict[str, str]          # section name → content
    tier: str                         # "tier1" | "tier2" | "tier3"
    style: str
    raw_prompt: str
    model: str
    tokens_used: int
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    script_path: Optional[Path] = None

    def save(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = output_dir / f"{self.video_type}_script_{ts}.txt"
        path.write_text(
            f"# {self.title_draft}\n"
            f"# Type: {self.video_type} | Tier: {self.tier} | Style: {self.style}\n"
            f"# Words: {self.word_count} | Est: "
            f"{self.estimated_duration_seconds // 60}m{self.estimated_duration_seconds % 60}s\n"
            f"# Generated: {self.generated_at} | Model: {self.model}\n\n"
            f"{self.script}",
            encoding="utf-8",
        )
        self.script_path = path
        logger.info("Script saved → %s", path)
        return path


# ── Helpers ──────────────────────────────────────────────────────────────────

def _call_claude(system: str, user_prompt: str) -> tuple[str, int]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=CLAUDE_MAX_TOKENS,
        temperature=CLAUDE_TEMPERATURE,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    record(resp, "script_gen")
    text = resp.content[0].text
    tokens = resp.usage.input_tokens + resp.usage.output_tokens
    return text, tokens


def _parse_segments(script: str) -> dict[str, str]:
    """Split script on [SECTION HEADER] markers into a dict."""
    pattern = r"\[([A-Z][A-Z0-9 &_—\-]+)\](.*?)(?=\[[A-Z]|\Z)"
    return {
        name.strip(): content.strip()
        for name, content in re.findall(pattern, script, re.DOTALL)
    }


def _estimate_duration(word_count: int, video_type: str) -> int:
    wpm = _WPM.get(video_type, 150)
    return int((word_count / wpm) * 60)


def _random_style() -> str:
    return random.choice(SCRIPT_STYLES)


def _random_hook() -> str:
    return random.choice(HOOK_VARIATIONS)


# ── Weekday script ───────────────────────────────────────────────────────────

# Seed terms for the trending-search context. Deliberately short: the
# scraper sleeps a second between keywords to respect Google's rate limit,
# so every extra seed is another second of a pipeline that publishes to a
# clock.
_TREND_SEEDS = ["stock market today", "S&P 500", "inflation"]
_TREND_CACHE_NAME = "trending_queries.json"


def _trending_context(limit: int = 6) -> str:
    """
    What people are actually searching about markets right now.

    The scraper for this has existed since the project started and nothing
    ever called it, so every script was written purely from price data —
    which is a large part of why every recap came out sounding the same.
    Giving Claude the day's rising queries lets the script lead with what
    people are already curious about.

    Strictly optional. Google Trends is unofficial and rate-limited, and a
    script that publishes on a schedule must never wait on it or fail with
    it, so every path here returns "" rather than raising.
    """
    import json
    from datetime import date
    # Imported locally: this module pulls named constants from config.settings,
    # not the settings object itself.
    from config.settings import settings

    cache = settings.logs_dir / _TREND_CACHE_NAME
    today = date.today().isoformat()
    try:
        if cache.exists():
            cached = json.loads(cache.read_text(encoding="utf-8"))
            # Two runs a weekday share one fetch: the day's searches do not
            # change enough between them to be worth the rate-limit budget.
            if cached.get("date") == today:
                queries = cached.get("queries", [])
                return _format_trends(queries[:limit])
    except Exception as exc:
        logger.debug("Trend cache unreadable (non-fatal): %s", exc)

    try:
        from scrapers.trends_scraper import TrendsScraper
        rising = TrendsScraper().get_rising_queries(keywords=_TREND_SEEDS)
        queries = [item["query"] for item in rising if item.get("query")]
    except Exception as exc:
        logger.warning("Google Trends unavailable (non-fatal): %s", exc)
        return ""

    if not queries:
        return ""
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"date": today, "queries": queries}),
                         encoding="utf-8")
    except Exception as exc:
        logger.debug("Trend cache not written (non-fatal): %s", exc)
    return _format_trends(queries[:limit])


def _format_trends(queries: list) -> str:
    if not queries:
        return ""
    listed = ", ".join(str(q) for q in queries)
    return (
        "WHAT PEOPLE ARE SEARCHING RIGHT NOW (Google Trends, rising queries):\n"
        f"{listed}\n"
        "If any of these connect to today's market data above, lead with that "
        "connection — it is what the audience already wants explained. Ignore "
        "any that do not relate to today's session."
    )


def generate_weekday_script(
    market_narrative: str,
    earnings_narrative: str,
    economic_narrative: str,
    tier: str = "tier2",
    topic: str = "",
    anchor_number: str = "",
) -> GeneratedScript:
    """
    Generate a tier-aware weekday script.
    tier: "tier1" (breakout ≥5%), "tier2" (notable 2-5%), "tier3" (routine).
    """
    logger.info("Generating weekday script | tier=%s | model=%s", tier, CLAUDE_MODEL)

    style = _random_style()
    hook  = _random_hook()

    # Compose context block from the data narratives, plus what the audience
    # is searching for today when Trends is reachable.
    context = "\n\n".join([part for part in (
        market_narrative,
        earnings_narrative,
        economic_narrative,
        _trending_context(),
    ) if part])

    prompt_template = _TIER_PROMPTS.get(tier, WEEKDAY_SCRIPT_TIER2_PROMPT)

    if tier == "tier3":
        prompt = prompt_template.format(
            topic=topic or "Today's Market Action",
            context=context,
            style=style,
            hook=hook,
        )
    else:
        prompt = prompt_template.format(
            topic=topic or "Today's Market Action",
            anchor_number=anchor_number or "significant market move",
            context=context,
            style=style,
            hook=hook,
        )

    script_text, tokens = _call_claude(SYSTEM_PERSONA, prompt)
    word_count = len(script_text.split())
    segments   = _parse_segments(script_text)
    hook_seg   = segments.get("HOOK", "")
    title_draft = (hook_seg[:80].split(".")[0].strip() if hook_seg else topic) or "Daily Market Recap"

    result = GeneratedScript(
        video_type="weekday",
        title_draft=title_draft,
        script=script_text,
        word_count=word_count,
        estimated_duration_seconds=_estimate_duration(word_count, "weekday"),
        segments=segments,
        tier=tier,
        style=style,
        raw_prompt=prompt,
        model=CLAUDE_MODEL,
        tokens_used=tokens,
    )
    logger.info("Weekday script: %d words | ~%ds | %d tokens",
                word_count, result.estimated_duration_seconds, tokens)
    return result


# ── Shorts script ─────────────────────────────────────────────────────────────

def generate_shorts_script(
    topic: str,
    anchor_number: str,
    tier: str = "tier2",
) -> GeneratedScript:
    """Generate a 5-card Shorts script (<55 seconds)."""
    logger.info("Generating Shorts script | %s | tier=%s", topic, tier)

    prompt = SHORTS_SCRIPT_PROMPT.format(
        topic=topic,
        anchor_number=anchor_number,
        tier=tier,
    )

    script_text, tokens = _call_claude(SYSTEM_PERSONA, prompt)
    word_count = len(script_text.split())
    segments   = _parse_segments(script_text)

    result = GeneratedScript(
        video_type="shorts",
        title_draft=topic,
        script=script_text,
        word_count=word_count,
        estimated_duration_seconds=_estimate_duration(word_count, "shorts"),
        segments=segments,
        tier=tier,
        style="shorts_cards",
        raw_prompt=prompt,
        model=CLAUDE_MODEL,
        tokens_used=tokens,
    )
    logger.info("Shorts script: %d words | ~%ds", word_count, result.estimated_duration_seconds)
    return result


# ── Sunday script ─────────────────────────────────────────────────────────────

def generate_sunday_script(
    topic: str,
    theme: str,
    week_context: str = "",
    bonus_theme: str = "",
    audience_level: str = "beginner-intermediate",
) -> GeneratedScript:
    """
    Generate a Sunday educational script.
    theme: one of the SUNDAY_THEMES values from settings.py.
    bonus_theme: only used when theme == "rotating_bonus".
    """
    logger.info("Generating Sunday script | theme=%s | topic=%s", theme, topic)

    prompt_template = _SUNDAY_PROMPTS.get(theme, SUNDAY_INVESTMENT_SCRIPT_PROMPT)

    if theme == "rotating_bonus":
        prompt = prompt_template.format(
            topic=topic,
            bonus_theme=bonus_theme or "macro_finance",
            week_context=week_context or "Markets were mixed this week.",
        )
    else:
        prompt = prompt_template.format(
            topic=topic,
            week_context=week_context or "Markets were mixed this week.",
            audience_level=audience_level,
        )

    script_text, tokens = _call_claude(SYSTEM_PERSONA, prompt)
    word_count = len(script_text.split())
    segments   = _parse_segments(script_text)

    result = GeneratedScript(
        video_type="sunday",
        title_draft=topic,
        script=script_text,
        word_count=word_count,
        estimated_duration_seconds=_estimate_duration(word_count, "sunday"),
        segments=segments,
        tier="tier3",
        style="educational",
        raw_prompt=prompt,
        model=CLAUDE_MODEL,
        tokens_used=tokens,
    )
    logger.info("Sunday script: %d words | ~%ds", word_count, result.estimated_duration_seconds)
    return result


# ── Topic selector ────────────────────────────────────────────────────────────

# ── ScriptGenerator class ────────────────────────────────────────────────────

class ScriptGenerator:
    """Class-based script generation with cyclic styles and hook history."""

    def __init__(self, output_dir: Optional[Path] = None):
        from config.settings import settings
        self.output_dir = output_dir or (settings.output_dir / "scripts")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._style_state_path = settings.logs_dir / "style_state.json"
        self._hook_history_path = settings.logs_dir / "hook_history.json"

    # ── Data loading ──────────────────────────────────────────────────────────

    def load_todays_data(self) -> dict:
        """Merge latest market + economic + earnings JSON from output/scripts/."""
        from glob import glob as _glob
        today = datetime.now().strftime("%Y%m%d")
        data: dict = {}
        for label in ("market", "economic", "earnings"):
            pattern = str(self.output_dir / f"{label}_{today}*.json")
            files = sorted(_glob(pattern), reverse=True)
            if files:
                try:
                    data[label] = json.loads(Path(files[0]).read_text())
                except Exception as exc:
                    logger.warning("Could not load %s data: %s", label, exc)
                    data[label] = {}
            else:
                data[label] = {}
        logger.info("Loaded today's data: keys=%s", list(data.keys()))
        return data

    def select_topics(self, data: dict) -> list[dict]:
        """Use Claude TOPIC_SELECTOR_PROMPT to rank topics by news value."""
        topics_json = json.dumps(data, default=str)[:4000]
        return select_best_topic(
            topics_json=topics_json,
            date_str=datetime.now().strftime("%Y-%m-%d"),
        )

    def detect_tier(self, topic_data: dict) -> int:
        """Return 1 (≥5%), 2 (≥2%), or 3 (routine) based on topic_data."""
        change_pct = abs(float(topic_data.get("change_pct") or 0))
        score = float(topic_data.get("score") or 0)
        if change_pct >= 5 or score >= 80:
            return 1
        if change_pct >= 2 or score >= 50:
            return 2
        return 3

    def get_style(self) -> str:
        """Return next style in cyclic rotation through SCRIPT_STYLES."""
        state = self._load_json(self._style_state_path, {"index": 0})
        idx = state.get("index", 0) % len(SCRIPT_STYLES)
        self._save_json(self._style_state_path, {"index": idx + 1})
        return SCRIPT_STYLES[idx]

    def get_hook(self) -> str:
        """Randomly select HOOK_VARIATION; avoid repeats for 5-day window."""
        history = self._load_json(self._hook_history_path, [])
        if not isinstance(history, list):
            history = []
        available = [h for h in HOOK_VARIATIONS if h not in history]
        if not available:
            history = []
            available = list(HOOK_VARIATIONS)
        hook = random.choice(available)
        history.append(hook)
        self._save_json(self._hook_history_path, history[-5:])
        return hook

    # ── Generation ────────────────────────────────────────────────────────────

    def generate_main_script(
        self,
        topic: str,
        tier: int,
        style: str,
        hook: str,
        market_narrative: str = "",
        earnings_narrative: str = "",
        economic_narrative: str = "",
        anchor_number: str = "",
    ) -> GeneratedScript:
        """Generate tier-appropriate weekday script via Claude."""
        return generate_weekday_script(
            market_narrative=market_narrative,
            earnings_narrative=earnings_narrative,
            economic_narrative=economic_narrative,
            tier=f"tier{tier}",
            topic=topic,
            anchor_number=anchor_number,
        )

    def generate_shorts_cards(self, topic: str, tier: int) -> GeneratedScript:
        """Generate 5-card Shorts script via SHORTS_SCRIPT_PROMPT."""
        return generate_shorts_script(topic=topic, anchor_number=topic, tier=f"tier{tier}")

    def validate_word_count(
        self,
        script: GeneratedScript,
        min_words: int,
        max_words: int,
        context: Optional[dict] = None,
    ) -> GeneratedScript:
        """Regenerate if word count outside [min, max]. Max 3 attempts."""
        for attempt in range(3):
            if min_words <= script.word_count <= max_words:
                return script
            logger.warning(
                "Word count %d outside [%d,%d] — attempt %d/3",
                script.word_count, min_words, max_words, attempt + 1,
            )
            ctx = context or {}
            if script.video_type == "weekday":
                script = generate_weekday_script(
                    market_narrative=ctx.get("market", ""),
                    earnings_narrative=ctx.get("earnings", ""),
                    economic_narrative=ctx.get("economic", ""),
                    tier=script.tier,
                    topic=script.title_draft,
                )
            else:
                break
        logger.warning("Word count still OOR after 3 attempts: %d", script.word_count)
        return script

    def extract_anchor_number(self, script: GeneratedScript) -> str:
        """Pull the leading key statistic from the script text."""
        for pat in [
            r'(?:fell|dropped|rose|surged|gained|lost|up|down)\s+([\d.]+\s*%)',
            r'\$([\d,.]+(?:\s*(?:B|M|T|billion|million|trillion))?)',
            r'([\d.]+\s*%)',
        ]:
            m = re.search(pat, script.script, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        m = re.search(r'[\d.]+[%$BMK]', script.script)
        return m.group(0) if m else ""

    def save_scripts(self, scripts: dict[str, GeneratedScript]) -> dict[str, Path]:
        """Run compliance filter then save each script to output/scripts/."""
        from generators.compliance_filter import ComplianceFilter
        cf = ComplianceFilter()
        paths: dict[str, Path] = {}
        for key, script in scripts.items():
            result = cf.filter(script.script, script.title_draft)
            script.script = result["script"]   # always use (possibly fixed) script
            paths[key] = script.save(self.output_dir)
        return paths

    # ── Private helpers ───────────────────────────────────────────────────────

    def _load_json(self, path: Path, default):
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception as exc:
                logger.debug("Could not load %s (%s) — using default", path.name, exc)
        return default

    def _save_json(self, path: Path, data) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))

    # ── Script Cache ──────────────────────────────────────────────────────────

    def _cache_key(self, video_type: str, topic: str, tier: int) -> str:
        """Stable cache key for a script request (sha256, first 16 chars)."""
        import hashlib
        raw = f"{video_type}:{topic.strip().lower()}:{tier}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def _cache_path(self, cache_key: str) -> Path:
        cache_dir = self.output_dir / "script_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / f"{cache_key}.json"

    def get_cached_script(self, video_type: str, topic: str, tier: int) -> Optional["GeneratedScript"]:
        """Return a cached GeneratedScript if one was saved today, else None."""
        from datetime import date as _date
        key = self._cache_key(video_type, topic, tier)
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text())
            # Expire cache entries from previous days
            cached_date = data.get("cached_date", "")
            if cached_date != _date.today().isoformat():
                path.unlink(missing_ok=True)
                return None
            return GeneratedScript(
                video_type=data["video_type"],
                title_draft=data.get("title_draft", ""),
                script=data.get("script", ""),
                word_count=data.get("word_count", 0),
                estimated_duration_seconds=data.get("estimated_duration_seconds", 0),
                segments=data.get("segments", {}),
                tier=data.get("tier", str(tier)),
                style=data.get("style", ""),
                raw_prompt=data.get("raw_prompt", ""),
                model=data.get("model", ""),
                tokens_used=data.get("tokens_used", 0),
            )
        except Exception as exc:
            logger.debug("Script cache miss for %s: %s", key, exc)
            return None

    def cache_script(self, script: "GeneratedScript", topic: str, tier: int) -> None:
        """Persist a GeneratedScript to the day-scoped cache."""
        from datetime import date as _date
        key = self._cache_key(script.video_type, topic, tier)
        path = self._cache_path(key)
        data = {
            "video_type": script.video_type,
            "title_draft": script.title_draft,
            "script": script.script,
            "word_count": script.word_count,
            "estimated_duration_seconds": script.estimated_duration_seconds,
            "segments": script.segments,
            "tier": script.tier,
            "style": script.style,
            "raw_prompt": script.raw_prompt,
            "model": script.model,
            "tokens_used": script.tokens_used,
            "topic": topic,
            "cached_date": _date.today().isoformat(),
        }
        try:
            path.write_text(json.dumps(data, default=str))
            logger.debug("Script cached: %s (%s/%s)", key, script.video_type, topic[:40])
        except Exception as exc:
            logger.warning("Script cache write failed: %s", exc)


def select_best_topic(
    topics_json: str,
    date_str: str,
    day_type: str = "weekday",
) -> list[dict]:
    """
    Ask Claude to rank the top 3 topics by news/gossip value.
    Returns parsed JSON list or empty list on failure.
    """
    import json as _json
    prompt = TOPIC_SELECTOR_PROMPT.format(
        topics_json=topics_json,
        date=date_str,
        day_type=day_type,
    )
    try:
        raw, _ = _call_claude(SYSTEM_PERSONA, prompt)
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            return _json.loads(match.group())
    except Exception as exc:
        logger.error("Topic selector failed: %s", exc)
    return []
