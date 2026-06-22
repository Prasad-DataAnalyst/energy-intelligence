"""
Title generator — uses Claude to generate A/B testable YouTube titles.
Scores titles on SEO, curiosity, and click-through potential.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import anthropic

from config.settings import settings
from config.prompts import TITLE_GENERATION_PROMPT, DESCRIPTION_PROMPT

logger = logging.getLogger(__name__)

# YouTube SEO keyword pool for finance content
SEO_KEYWORDS = [
    "stock market", "investing", "stocks", "S&P 500", "Nasdaq",
    "market crash", "bull market", "bear market", "recession",
    "interest rates", "Federal Reserve", "earnings", "Wall Street",
    "passive income", "financial education", "portfolio", "ETF",
]

POWER_WORDS = {
    "urgency": ["Breaking", "Just Now", "Alert", "Warning", "Today", "Right Now"],
    "curiosity": ["Secret", "Revealed", "Nobody Talks About", "Hidden", "Shocking"],
    "social_proof": ["Experts", "Wall Street", "Billionaires", "Insiders", "Analysts"],
    "numbers": ["$", "%", "10x", "100K", "Million", "Trillion"],
    "emotion": ["Crashed", "Surged", "Exploded", "Collapsed", "Skyrocketed", "Tanked"],
}


@dataclass
class TitleScore:
    title: str
    total_score: float
    length_score: float         # 0-20 — optimal: 50-70 chars
    keyword_score: float        # 0-30 — contains SEO keywords
    power_word_score: float     # 0-25 — contains power words
    number_score: float         # 0-15 — contains numbers/stats
    curiosity_score: float      # 0-10 — triggers curiosity gap
    feedback: list[str]


@dataclass
class TitleSet:
    video_type: str
    topic: str
    titles: list[TitleScore]
    winner: TitleScore           # highest scoring title
    ab_test_pair: tuple[TitleScore, TitleScore]  # top 2 for A/B testing
    description: Optional[str]
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _score_title(title: str) -> TitleScore:
    """Score a title on YouTube optimization criteria."""
    feedback = []
    length = len(title)

    # Length score (optimal: 50-70)
    if 50 <= length <= 70:
        length_score = 20.0
    elif 40 <= length < 50 or 70 < length <= 80:
        length_score = 14.0
        feedback.append(f"Title length {length} — aim for 50-70 chars")
    else:
        length_score = 6.0
        feedback.append(f"Title too {'short' if length < 40 else 'long'} ({length} chars)")

    # Keyword score
    title_lower = title.lower()
    matched_kw = [kw for kw in SEO_KEYWORDS if kw.lower() in title_lower]
    keyword_score = min(30.0, len(matched_kw) * 10)
    if not matched_kw:
        feedback.append("No SEO keywords detected — add finance keywords")

    # Power word score
    matched_pw = []
    for category, words in POWER_WORDS.items():
        for word in words:
            if word.lower() in title_lower:
                matched_pw.append(word)
    power_word_score = min(25.0, len(matched_pw) * 8)
    if not matched_pw:
        feedback.append("Add power words: 'Breaking', 'Crashed', 'Revealed', etc.")

    # Number score
    has_number = bool(re.search(r'\d+|[$%]', title))
    number_score = 15.0 if has_number else 0.0
    if not has_number:
        feedback.append("Include a number or stat for higher CTR")

    # Curiosity gap score
    curiosity_triggers = ["?", "why", "how", "secret", "revealed", "what", "nobody"]
    curiosity_score = 10.0 if any(t in title_lower for t in curiosity_triggers) else 0.0

    total = length_score + keyword_score + power_word_score + number_score + curiosity_score

    if total >= 80:
        feedback.append("Excellent title! 🔥")
    elif total >= 60:
        feedback.append("Good title — minor improvements available")
    else:
        feedback.append("Consider rewriting for higher CTR potential")

    return TitleScore(
        title=title,
        total_score=round(total, 1),
        length_score=length_score,
        keyword_score=keyword_score,
        power_word_score=power_word_score,
        number_score=number_score,
        curiosity_score=curiosity_score,
        feedback=feedback,
    )


def _generate_titles_via_claude(
    topic: str,
    key_stat: str,
    video_type: str,
    ticker: Optional[str] = None,
) -> list[str]:
    """Ask Claude to generate title options."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = TITLE_GENERATION_PROMPT.substitute(
        topic=topic,
        key_stat=key_stat,
        video_type=video_type,
        specific_ticker=ticker or "N/A",
    )
    try:
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=1024,
            temperature=0.85,   # higher temp for creative variety
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        json_match = re.search(r'\[.*?\]', raw, re.DOTALL)
        if json_match:
            titles = json.loads(json_match.group())
            if isinstance(titles, list):
                return [str(t) for t in titles]
    except Exception as exc:
        logger.error("Claude title generation failed: %s", exc)
    return []


def _generate_description_via_claude(title: str, script_summary: str, video_type: str) -> Optional[str]:
    """Generate YouTube description from Claude."""
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    prompt = DESCRIPTION_PROMPT.substitute(
        title=title,
        script_summary=script_summary[:500],
        video_type=video_type,
    )
    try:
        resp = client.messages.create(
            model=settings.claude_model,
            max_tokens=1500,
            temperature=0.5,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as exc:
        logger.error("Claude description generation failed: %s", exc)
        return None


def generate_title_set(
    topic: str,
    key_stat: str,
    video_type: str = "weekday_recap",
    script_summary: str = "",
    ticker: Optional[str] = None,
) -> TitleSet:
    """Generate, score, and rank titles. Return winner + A/B pair."""
    logger.info("Generating titles for: %s", topic[:60])

    raw_titles = _generate_titles_via_claude(topic, key_stat, video_type, ticker)

    if not raw_titles:
        # Fallback titles
        raw_titles = [
            f"Stock Market Today: {topic} — What You Need to Know",
            f"BREAKING: {key_stat} — Market Recap {datetime.now().strftime('%b %d')}",
            f"Wall Street Reacts: {topic} Explained",
        ]
        logger.warning("Using fallback titles — Claude generation failed")

    scored = [_score_title(t) for t in raw_titles]
    scored.sort(key=lambda s: s.total_score, reverse=True)

    winner = scored[0]
    ab_pair = (scored[0], scored[1]) if len(scored) >= 2 else (scored[0], scored[0])

    description = _generate_description_via_claude(winner.title, script_summary, video_type)

    result = TitleSet(
        video_type=video_type,
        topic=topic,
        titles=scored,
        winner=winner,
        ab_test_pair=ab_pair,
        description=description,
    )

    logger.info("Title winner (score %.1f): %s", winner.total_score, winner.title)
    return result


def log_title_performance(title: str, ctr: float, views: int) -> None:
    """
    Record observed CTR for future title scoring calibration.
    Appended to a local JSONL file for analysis.
    """
    record = {
        "title": title,
        "ctr": ctr,
        "views": views,
        "logged_at": datetime.now().isoformat(),
        "score": _score_title(title).total_score,
    }
    log_path = settings.logs_dir / "title_performance.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    logger.debug("Title performance logged: CTR %.2f%% for '%s'", ctr, title[:50])
