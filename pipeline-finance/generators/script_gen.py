"""
generators/script_gen.py — DriftWire326
Claude AI script generation with tier-aware weekday prompts,
Sunday theme routing, and Shorts 5-card format.
"""
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

    # Compose context block from all three data narratives
    context = "\n\n".join([
        market_narrative,
        earnings_narrative,
        economic_narrative,
    ])

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
