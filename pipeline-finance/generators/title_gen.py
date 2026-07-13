"""
generators/title_gen.py — DriftWire326
YouTube title generation: routes to tier-appropriate Claude prompts
(weekday / shorts / sunday), scores locally, returns A/B-testable pair.
"""
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import anthropic

from config.settings import ANTHROPIC_API_KEY, CLAUDE_MODEL, LOGS_DIR
from config.prompts import (
    SYSTEM_PERSONA,
    TITLE_MAIN_PROMPT,
    TITLE_SHORTS_PROMPT,
    TITLE_SUNDAY_PROMPT,
    DESCRIPTION_PROMPT,
    TAGS_PROMPT,
)

logger = logging.getLogger(__name__)

SEO_KEYWORDS = [
    "stock market", "investing", "stocks", "S&P 500", "Nasdaq",
    "market crash", "bull market", "bear market", "recession",
    "interest rates", "Federal Reserve", "earnings", "Wall Street",
    "passive income", "financial education", "portfolio", "ETF",
]

POWER_WORDS = {
    "urgency":      ["Breaking", "Just Now", "Alert", "Warning", "Today", "Right Now"],
    "curiosity":    ["Secret", "Revealed", "Nobody Talks About", "Hidden", "Shocking"],
    "social_proof": ["Experts", "Wall Street", "Billionaires", "Insiders", "Analysts"],
    "numbers":      ["$", "%", "10x", "100K", "Million", "Trillion"],
    "emotion":      ["Crashed", "Surged", "Exploded", "Collapsed", "Skyrocketed", "Tanked"],
}


@dataclass
class TitleScore:
    title: str
    total_score: float
    length_score: float       # 0-20
    keyword_score: float      # 0-30
    power_word_score: float   # 0-25
    number_score: float       # 0-15
    curiosity_score: float    # 0-10
    feedback: list[str]


@dataclass
class TitleSet:
    video_type: str
    topic: str
    titles: list[TitleScore]
    winner: TitleScore
    ab_test_pair: tuple[TitleScore, TitleScore]
    description: Optional[str]
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())


def _score_title(title: str) -> TitleScore:
    feedback = []
    length = len(title)

    if 50 <= length <= 70:
        length_score = 20.0
    elif 40 <= length < 50 or 70 < length <= 80:
        length_score = 14.0
        feedback.append(f"Title length {length} — aim for 50-70 chars")
    else:
        length_score = 6.0
        feedback.append(f"Title too {'short' if length < 40 else 'long'} ({length} chars)")

    title_lower = title.lower()
    matched_kw = [kw for kw in SEO_KEYWORDS if kw.lower() in title_lower]
    keyword_score = min(30.0, len(matched_kw) * 10)
    if not matched_kw:
        feedback.append("No SEO keywords detected — add finance keywords")

    matched_pw = [w for cat in POWER_WORDS.values() for w in cat if w.lower() in title_lower]
    power_word_score = min(25.0, len(matched_pw) * 8)
    if not matched_pw:
        feedback.append("Add power words: 'Breaking', 'Crashed', 'Revealed', etc.")

    has_number = bool(re.search(r'\d+|[$%]', title))
    number_score = 15.0 if has_number else 0.0
    if not has_number:
        feedback.append("Include a number or stat for higher CTR")

    curiosity_triggers = ["?", "why", "how", "secret", "revealed", "what", "nobody"]
    curiosity_score = 10.0 if any(t in title_lower for t in curiosity_triggers) else 0.0

    total = length_score + keyword_score + power_word_score + number_score + curiosity_score

    if total >= 80:
        feedback.append("Excellent title!")
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
    anchor_number: str,
    video_type: str,
    hook_card_text: str = "",
    sunday_theme: str = "",
    script_summary: str = "",
) -> list[str]:
    """Route to the correct title prompt and return a list of title strings."""
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    vtype = video_type.lower()
    if "short" in vtype:
        prompt = TITLE_SHORTS_PROMPT.format(
            topic=topic,
            anchor_number=anchor_number,
            hook_card_text=hook_card_text or topic[:40],
        )
    elif "sunday" in vtype or "education" in vtype:
        prompt = TITLE_SUNDAY_PROMPT.format(
            topic=topic,
            sunday_theme=sunday_theme or "investment_banking",
        )
    else:
        prompt = TITLE_MAIN_PROMPT.format(
            topic=topic,
            anchor_number=anchor_number or "key market move",
            script_summary=script_summary[:400] if script_summary else topic,
        )

    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1024,
            temperature=0.85,
            system=SYSTEM_PERSONA,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text
        json_match = re.search(r'\{.*\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            options = data.get("options", [])
            recommended = data.get("recommended", "")
            titles = [opt["title"] for opt in options if "title" in opt]
            # Move Claude's recommended to front so local scorer can confirm it
            if recommended and recommended in titles:
                titles.remove(recommended)
                titles.insert(0, recommended)
            return titles
    except Exception as exc:
        logger.error("Claude title generation failed: %s", exc)
    return []


def _generate_description_via_claude(
    title: str,
    script_summary: str,
    video_type: str,
    tags: str = "",
) -> Optional[str]:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    prompt = DESCRIPTION_PROMPT.format(
        title=title,
        script_summary=script_summary[:500],
        video_type=video_type,
        tags=tags or "finance, stocks, market news, investing, DriftWire326",
        date=datetime.now().strftime("%Y-%m-%d"),
    )
    try:
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            temperature=0.5,
            system=SYSTEM_PERSONA,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text
    except Exception as exc:
        logger.error("Claude description generation failed: %s", exc)
        return None


def generate_title_set(
    topic: str,
    anchor_number: str = "",
    video_type: str = "weekday",      # "weekday" | "shorts" | "sunday"
    script_summary: str = "",
    hook_card_text: str = "",
    sunday_theme: str = "",
    ticker: Optional[str] = None,
    # Legacy alias accepted for backward compat with older callers
    key_stat: str = "",
) -> TitleSet:
    """Generate, score, and rank titles. Returns winner + A/B pair."""
    effective_anchor = anchor_number or key_stat
    logger.info("Generating titles for: %s | type=%s", topic[:60], video_type)

    raw_titles = _generate_titles_via_claude(
        topic=topic,
        anchor_number=effective_anchor,
        video_type=video_type,
        hook_card_text=hook_card_text,
        sunday_theme=sunday_theme,
        script_summary=script_summary,
    )

    if not raw_titles:
        raw_titles = [
            f"Stock Market Today: {topic} — What You Need to Know",
            f"BREAKING: {effective_anchor or topic} — Market Recap {datetime.now().strftime('%b %d')}",
            f"Wall Street Reacts: {topic} Explained",
        ]
        logger.warning("Using fallback titles — Claude generation failed")

    scored = sorted([_score_title(t) for t in raw_titles], key=lambda s: s.total_score, reverse=True)
    winner = scored[0]
    ab_pair = (scored[0], scored[1]) if len(scored) >= 2 else (scored[0], scored[0])

    description = _generate_description_via_claude(
        winner.title, script_summary, video_type,
    )

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


# ── TitleGenerator class ─────────────────────────────────────────────────────

class TitleGenerator:
    """Class-based title, description, and tags generation for DriftWire326."""

    SHORTS_CHAR_LIMIT = 40
    BASE_TAGS = [
        "stocks", "stock market", "investing", "finance",
        "market news", "DriftWire326", "Wall Street", "S&P 500",
    ]

    def generate_main_title(
        self,
        topic: str,
        anchor_number: str = "",
        script_summary: str = "",
        tier: str = "tier2",
    ) -> TitleSet:
        """Generate title set for a weekday main video."""
        logger.info("Generating weekday title | tier=%s | topic=%s", tier, topic[:50])
        return generate_title_set(
            topic=topic,
            anchor_number=anchor_number,
            video_type="weekday",
            script_summary=script_summary,
        )

    def generate_shorts_title(
        self,
        topic: str,
        anchor_number: str = "",
        hook_card_text: str = "",
    ) -> str:
        """
        Generate a Shorts title with a hard 40-character cap.
        Returns the winning title, truncated to 40 chars if needed.
        """
        ts = generate_title_set(
            topic=topic,
            anchor_number=anchor_number,
            video_type="shorts",
            hook_card_text=hook_card_text,
        )
        title = ts.winner.title
        if len(title) > self.SHORTS_CHAR_LIMIT:
            title = title[:self.SHORTS_CHAR_LIMIT].rstrip()
            logger.info("Shorts title truncated to 40 chars: %s", title)
        return title

    def generate_sunday_title(
        self,
        topic: str,
        sunday_theme: str = "investment_banking",
        script_summary: str = "",
    ) -> TitleSet:
        """Generate title set for a Sunday educational video."""
        logger.info("Generating Sunday title | theme=%s | topic=%s", sunday_theme, topic[:50])
        return generate_title_set(
            topic=topic,
            video_type="sunday",
            sunday_theme=sunday_theme,
            script_summary=script_summary,
        )

    def generate_description(
        self,
        title: str,
        script_summary: str,
        video_type: str = "weekday",
        extra_tags: Optional[list[str]] = None,
    ) -> str:
        """
        Generate YouTube description.
        Auto-appends disclaimer and AI disclosure at the end.
        """
        from config.settings import settings
        tags_str = ", ".join(self.generate_tags(extra_tags))
        desc = _generate_description_via_claude(
            title=title,
            script_summary=script_summary,
            video_type=video_type,
            tags=tags_str,
        ) or f"{title}\n\n{script_summary[:300]}"

        # Always append compliance footer
        if settings.disclaimer_text not in desc:
            desc = desc.rstrip() + f"\n\n{settings.disclaimer_text}"
        ai_disclosure = "Narration is AI-generated."
        if ai_disclosure not in desc:
            desc = desc.rstrip() + f"\n\n{ai_disclosure}"
        return desc

    def generate_tags(self, extra: Optional[list[str]] = None) -> list[str]:
        """Always starts with BASE_TAGS; caller may append extras."""
        tags = list(self.BASE_TAGS)
        if extra:
            for t in extra:
                if t not in tags:
                    tags.append(t)
        return tags[:500]  # YouTube caps at 500 chars total when joined

    def run_promise_check(
        self,
        title: str,
        script: str,
        topic: str,
        anchor_number: str = "",
        max_attempts: int = 2,
    ) -> str:
        """
        Verify title promises match script content (≥60% word overlap).
        Re-generates up to max_attempts times if check fails.
        Returns the best title found.
        """
        from generators.compliance_filter import ComplianceFilter
        cf = ComplianceFilter()

        if cf.run_promise_match(title, script):
            return title

        logger.warning("Promise mismatch for title '%s' — regenerating", title[:60])
        for attempt in range(max_attempts):
            ts = self.generate_main_title(topic, anchor_number, script[:400])
            candidate = ts.winner.title
            if cf.run_promise_match(candidate, script):
                logger.info("Promise match resolved on attempt %d: %s", attempt + 1, candidate)
                return candidate
            logger.warning("Attempt %d still mismatched: %s", attempt + 1, candidate[:60])

        logger.error("Promise check failed after %d attempts — using original title", max_attempts)
        return title


# ── Description Metadata Footer ───────────────────────────────────────────────

_DEFAULT_SOURCES = [
    "Yahoo Finance market data",
    "SEC EDGAR filings",
    "Finnhub earnings calendar",
    "FRED economic data",
]


def build_metadata_footer(sources: Optional[list[str]] = None) -> str:
    """
    Data-cutoff timestamp + sources block appended to every video description.
    Content-spec requirement: every video states when its data was pulled
    and where it came from.
    """
    cutoff = datetime.now().strftime("%B %d, %Y %I:%M %p ET")
    source_list = sources or _DEFAULT_SOURCES
    return (
        f"\n\n📅 Data as of: {cutoff}"
        f"\n📊 Sources: {', '.join(source_list)}"
    )


# ── Chapter Marker Generation ─────────────────────────────────────────────────

_SECTION_HEADERS = re.compile(
    r"^#+\s+(.+)$|^\*\*(.+)\*\*$|^=+\s*(.+?)\s*=+$",
    re.MULTILINE,
)

# Sections to always include as chapters (if found in script)
_FORCED_SECTIONS = [
    "intro", "introduction", "market open", "overview",
    "top movers", "gainers", "losers",
    "sector", "economic", "earnings",
    "outlook", "summary", "what to watch",
]


def generate_chapter_markers(script_text: str, audio_duration_seconds: float = 0.0) -> str:
    """
    Extract section headers from a script and generate YouTube chapter timestamps.

    Chapter timestamps are estimated by distributing sections evenly across
    the audio duration. If audio_duration_seconds is 0, uses word-count pacing.

    Returns a string suitable for appending to a video description, e.g.:
        0:00 Intro
        0:45 Top Movers
        2:10 Sector Performance
        3:30 Economic Data
        5:00 Outlook

    YouTube requires the first chapter to start at 0:00.
    """
    headers: list[str] = []
    for m in _SECTION_HEADERS.finditer(script_text):
        label = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        if label:
            headers.append(label)

    if not headers:
        # Fallback: try to detect implied sections via keyword scanning
        lower = script_text.lower()
        detected: list[str] = []
        for sec in _FORCED_SECTIONS:
            if sec in lower:
                detected.append(sec.title())
        headers = detected or ["Intro", "Market Recap", "Outlook"]

    n = len(headers)
    if audio_duration_seconds <= 0:
        words = len(script_text.split())
        audio_duration_seconds = max(words / (140 / 60), 30)

    # Distribute headers evenly; first chapter always at 0:00
    interval = audio_duration_seconds / n
    lines: list[str] = []
    for i, header in enumerate(headers):
        offset = int(i * interval)
        mins, secs = divmod(offset, 60)
        lines.append(f"{mins}:{secs:02d} {header}")

    return "\n".join(lines)


def extract_script_tags(script_text: str) -> list[str]:
    """
    Scan a script for ticker symbols and named topics to use as YouTube tags.
    Returns deduplicated list of uppercase ticker symbols + lowercase topic tags.
    """
    tags: list[str] = []

    # Ticker symbols: 1-5 uppercase letters possibly preceded by $ or wrapped in parens
    ticker_re = re.compile(r"\b(?:\$)?([A-Z]{1,5})\b")
    for m in ticker_re.finditer(script_text):
        sym = m.group(1)
        # Basic filter: exclude common English words
        if len(sym) >= 2 and sym not in {
            "I", "A", "IS", "AN", "IN", "AT", "TO", "OR", "AND", "THE",
            "FOR", "OF", "ON", "UP", "US", "ALL", "NEW", "NOW", "GDP",
            "ETF", "ETFs", "VIX", "FED", "CEO", "CFO", "IPO", "SEC",
        }:
            tags.append(sym)

    # Deduplicate preserving order
    seen: set[str] = set()
    unique_tags: list[str] = []
    for t in tags:
        if t not in seen:
            seen.add(t)
            unique_tags.append(t)

    return unique_tags[:20]


def log_title_performance(title: str, ctr: float, views: int) -> None:
    """Record observed CTR to a JSONL file for future scoring calibration."""
    record = {
        "title": title,
        "ctr": ctr,
        "views": views,
        "logged_at": datetime.now().isoformat(),
        "score": _score_title(title).total_score,
    }
    log_path = LOGS_DIR / "title_performance.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(record) + "\n")
    logger.debug("Title performance logged: CTR %.2f%% for '%s'", ctr, title[:50])
