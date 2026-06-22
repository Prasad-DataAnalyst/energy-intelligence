"""
Script generator — uses Claude API to produce full video scripts
from scraped market data, economic indicators, and earnings news.
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic

from config.settings import settings
from config.prompts import (
    SYSTEM_PERSONA,
    WEEKDAY_SCRIPT_PROMPT,
    SUNDAY_SCRIPT_PROMPT,
    SHORTS_SCRIPT_PROMPT,
)

logger = logging.getLogger(__name__)


@dataclass
class GeneratedScript:
    video_type: str          # "weekday" | "sunday" | "shorts"
    title_draft: str
    script: str
    word_count: int
    estimated_duration_seconds: int
    segments: dict[str, str]  # parsed section name → content
    raw_prompt: str
    model: str
    tokens_used: int
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    script_path: Optional[Path] = None

    def save(self, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{self.video_type}_script_{timestamp}.txt"
        path = output_dir / filename
        content = (
            f"# {self.title_draft}\n"
            f"# Type: {self.video_type} | Words: {self.word_count} | "
            f"Est. Duration: {self.estimated_duration_seconds // 60}m {self.estimated_duration_seconds % 60}s\n"
            f"# Generated: {self.generated_at} | Model: {self.model}\n\n"
            f"{self.script}"
        )
        path.write_text(content, encoding="utf-8")
        self.script_path = path
        logger.info("Script saved → %s", path)
        return path


def _parse_segments(script: str) -> dict[str, str]:
    """Split script into named sections using [SECTION] markers."""
    import re
    segments = {}
    pattern = r"\[([A-Z][A-Z0-9 &_—\-]+)\](.*?)(?=\[[A-Z]|\Z)"
    matches = re.findall(pattern, script, re.DOTALL)
    for name, content in matches:
        segments[name.strip()] = content.strip()
    return segments


def _estimate_duration(word_count: int, video_type: str) -> int:
    """Estimate duration in seconds based on word count and pacing."""
    wpm = {
        "weekday": 155,  # faster, energetic
        "sunday": 140,   # slightly slower for education
        "shorts": 165,   # very fast, punchy
    }.get(video_type, 150)
    return int((word_count / wpm) * 60)


def _call_claude(system: str, user_prompt: str) -> tuple[str, int]:
    """Call Claude API and return (content, tokens_used)."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    response = client.messages.create(
        model=settings.claude_model,
        max_tokens=settings.claude_max_tokens,
        temperature=settings.claude_temperature,
        system=system,
        messages=[{"role": "user", "content": user_prompt}],
    )
    content = response.content[0].text
    tokens = response.usage.input_tokens + response.usage.output_tokens
    return content, tokens


def generate_weekday_script(
    market_narrative: str,
    earnings_narrative: str,
    economic_narrative: str,
    headlines: str = "",
) -> GeneratedScript:
    """Generate a weekday market recap script."""
    logger.info("Generating weekday script via Claude %s", settings.claude_model)

    prompt = WEEKDAY_SCRIPT_PROMPT.substitute(
        market_data=market_narrative,
        earnings_data=earnings_narrative,
        economic_data=economic_narrative,
        headlines=headlines or "No major breaking headlines.",
    )

    script_text, tokens = _call_claude(SYSTEM_PERSONA, prompt)
    word_count = len(script_text.split())
    segments = _parse_segments(script_text)

    # Extract a draft title from the HOOK segment
    hook = segments.get("HOOK", "")
    title_draft = hook[:80].split(".")[0].strip() if hook else "Daily Market Recap"

    result = GeneratedScript(
        video_type="weekday",
        title_draft=title_draft,
        script=script_text,
        word_count=word_count,
        estimated_duration_seconds=_estimate_duration(word_count, "weekday"),
        segments=segments,
        raw_prompt=prompt,
        model=settings.claude_model,
        tokens_used=tokens,
    )

    logger.info("Weekday script: %d words, est. %ds, %d tokens used",
                word_count, result.estimated_duration_seconds, tokens)
    return result


def generate_sunday_script(
    topic: str,
    subtopics: list[str],
    key_concepts: list[str],
    current_relevance: str,
) -> GeneratedScript:
    """Generate a Sunday educational deep-dive script."""
    logger.info("Generating Sunday script — topic: %s", topic)

    prompt = SUNDAY_SCRIPT_PROMPT.substitute(
        topic=topic,
        subtopics=", ".join(subtopics),
        key_concepts=", ".join(key_concepts),
        current_relevance=current_relevance,
    )

    script_text, tokens = _call_claude(SYSTEM_PERSONA, prompt)
    word_count = len(script_text.split())
    segments = _parse_segments(script_text)

    result = GeneratedScript(
        video_type="sunday",
        title_draft=topic,
        script=script_text,
        word_count=word_count,
        estimated_duration_seconds=_estimate_duration(word_count, "sunday"),
        segments=segments,
        raw_prompt=prompt,
        model=settings.claude_model,
        tokens_used=tokens,
    )

    logger.info("Sunday script: %d words, est. %ds", word_count, result.estimated_duration_seconds)
    return result


def generate_shorts_script(topic: str, key_fact: str) -> GeneratedScript:
    """Generate a 55-second Shorts script."""
    logger.info("Generating Shorts script — %s", topic)

    prompt = SHORTS_SCRIPT_PROMPT.substitute(topic=topic, key_fact=key_fact)
    script_text, tokens = _call_claude(SYSTEM_PERSONA, prompt)
    word_count = len(script_text.split())
    segments = _parse_segments(script_text)

    result = GeneratedScript(
        video_type="shorts",
        title_draft=topic,
        script=script_text,
        word_count=word_count,
        estimated_duration_seconds=_estimate_duration(word_count, "shorts"),
        segments=segments,
        raw_prompt=prompt,
        model=settings.claude_model,
        tokens_used=tokens,
    )

    logger.info("Shorts script: %d words, est. %ds", word_count, result.estimated_duration_seconds)
    return result
