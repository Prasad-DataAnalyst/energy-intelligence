"""
Phase 1 — Trend Scorer & Topic Extractor
==========================================
Scores raw trends, deduplicates, picks the #1 topic, then uses Claude to
expand it into structured talking points, hooks, and visual direction.
"""
import logging
import os
import re
from typing import List, Optional
import anthropic

from utils.models import TrendResult
from phase1_trends.fetcher import RawTrend

log = logging.getLogger("viral_pipeline.trends.scorer")

# ── Content-fit keywords (boost score if trend matches) ──────────────────────
CONTENT_FIT_BOOSTS = {
    "ai":          0.3,
    "money":       0.25,
    "hack":        0.2,
    "secret":      0.2,
    "viral":       0.15,
    "how to":      0.2,
    "surprising":  0.15,
    "exposed":     0.2,
    "truth":       0.2,
    "science":     0.15,
    "health":      0.15,
    "technology":  0.2,
    "billion":     0.2,
    "million":     0.15,
    "breaking":    0.1,
}

# ── Topics to AVOID (controversial, brand-risk, not monetisable) ─────────────
BLOCKLIST = [
    "sex", "porn", "violence", "murder", "war crime",
    "suicide", "self harm", "drug", "nsfw",
]


def _content_fit(title: str) -> float:
    t = title.lower()
    score = 0.0
    for kw, boost in CONTENT_FIT_BOOSTS.items():
        if kw in t:
            score += boost
    return min(score, 1.0)


def _is_blocked(title: str) -> bool:
    t = title.lower()
    return any(bw in t for bw in BLOCKLIST)


def _normalise_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    return title[:200]


def score_raw_trends(trends: List[RawTrend],
                     cfg: dict) -> List[RawTrend]:
    """
    Compute a composite score for each raw trend.
    score = velocity_weight*velocity + engagement_weight*engagement
            + content_fit_weight*content_fit
    """
    vw = cfg.get("velocity_weight", 0.4)
    ew = cfg.get("engagement_weight", 0.4)
    cw = cfg.get("content_fit_weight", 0.2)

    for t in trends:
        if _is_blocked(t.title):
            t.score = -1.0
            continue
        t.title = _normalise_title(t.title)

        # Engagement proxy from upvotes + comments + views
        engagement = min(1.0, (t.upvotes + t.comments * 5) / 100_000
                         + t.views / 50_000_000)
        cf = _content_fit(t.title)
        t.score = vw * t.velocity + ew * engagement + cw * cf

    return [t for t in trends if t.score >= 0]


def deduplicate(trends: List[RawTrend], threshold: float = 0.75) -> List[RawTrend]:
    """Remove near-duplicate titles using token overlap."""
    seen: List[str] = []
    unique: List[RawTrend] = []
    for t in trends:
        tokens = set(t.title.lower().split())
        duplicate = False
        for s in seen:
            s_tokens = set(s.lower().split())
            if len(tokens) == 0 or len(s_tokens) == 0:
                continue
            overlap = len(tokens & s_tokens) / max(len(tokens), len(s_tokens))
            if overlap >= threshold:
                duplicate = True
                break
        if not duplicate:
            unique.append(t)
            seen.append(t.title)
    return unique


def pick_top_trend(trends: List[RawTrend], n: int = 1) -> List[RawTrend]:
    """Sort by score descending, return top-n."""
    return sorted(trends, key=lambda t: t.score, reverse=True)[:n]


def expand_with_claude(raw: RawTrend, model: str = "claude-sonnet-4-6",
                       style_preset: str = "tech-dark") -> TrendResult:
    """
    Call Claude to expand the winning trend into structured content brief.
    Returns a full TrendResult. Falls back to minimal data if API unavailable.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        log.warning("ANTHROPIC_API_KEY not set — using minimal trend expansion")
        return _minimal_expansion(raw)

    prompt = f"""You are a viral YouTube content strategist.

A trending topic has been detected:
Title: "{raw.title}"
Source: {raw.source}
Category: {raw.category}
Velocity score: {raw.velocity:.2f}

Expand this into a complete content brief for a YouTube video targeting US audiences aged 18-40.

Return ONLY a JSON object with this exact structure:
{{
  "topic": "short 3-6 word topic label",
  "title": "compelling YouTube video title (max 70 chars)",
  "target_audience": "specific audience description",
  "talking_points": ["point 1", "point 2", "point 3", "point 4", "point 5"],
  "emotional_hooks": ["hook 1", "hook 2", "hook 3"],
  "visual_style": "describe the visual aesthetic (e.g. dark cinematic, vibrant energetic)",
  "keywords": ["kw1", "kw2", "kw3", "kw4", "kw5", "kw6", "kw7", "kw8"],
  "category": "Money|Tech|Health|Science|Mindset|Career|Lifestyle"
}}"""

    try:
        client = anthropic.Anthropic(api_key=api_key)
        msg = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # Extract JSON even if surrounded by markdown fences
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise ValueError("No JSON in response")
        import json
        data = json.loads(m.group())
        return TrendResult(
            topic=data.get("topic", raw.title[:50]),
            title=data.get("title", raw.title),
            score=raw.score,
            velocity=raw.velocity,
            engagement_potential=raw.score,
            sources=[raw.source],
            talking_points=data.get("talking_points", []),
            target_audience=data.get("target_audience", "general"),
            emotional_hooks=data.get("emotional_hooks", []),
            visual_style=data.get("visual_style", "cinematic"),
            keywords=data.get("keywords", []),
            category=data.get("category", raw.category),
        )
    except Exception as e:
        log.error(f"Claude expansion failed: {e} — using minimal expansion")
        return _minimal_expansion(raw)


def _minimal_expansion(raw: RawTrend) -> TrendResult:
    """Build a TrendResult from raw data without Claude."""
    words = raw.title.split()
    talking_points = [
        f"Key insight #{i+1} about {raw.title}" for i in range(5)
    ]
    return TrendResult(
        topic=" ".join(words[:6]),
        title=raw.title,
        score=raw.score,
        velocity=raw.velocity,
        engagement_potential=raw.score,
        sources=[raw.source],
        talking_points=talking_points,
        target_audience="US adults 18-40",
        emotional_hooks=[f"Did you know about {raw.title}?",
                         f"This changes everything about {words[0] if words else 'it'}",
                         "Most people get this completely wrong"],
        visual_style="dark cinematic",
        keywords=words[:8],
        category=raw.category,
    )


def run_phase1(cfg: dict) -> TrendResult:
    """Full Phase 1 synchronous wrapper."""
    import asyncio
    from phase1_trends.fetcher import fetch_all_trends

    log.info("Phase 1: Fetching trends from all sources…")
    raw_trends = asyncio.run(fetch_all_trends())

    if not raw_trends:
        log.warning("No trends fetched — using fallback topic")
        raw_trends = [RawTrend(
            title="AI Tools Changing How Americans Work in 2025",
            source="fallback", velocity=0.8, score=0.8
        )]

    log.info(f"  Scoring {len(raw_trends)} raw trends…")
    scored = score_raw_trends(raw_trends, cfg.get("trends", {}))
    unique = deduplicate(scored)
    top = pick_top_trend(unique, n=1)

    winner = top[0]
    log.info(f"  Winner: '{winner.title}' (score={winner.score:.3f}, "
             f"source={winner.source})")

    log.info("  Expanding with Claude…")
    model = cfg.get("script", {}).get("model", "claude-sonnet-4-6")
    style = cfg.get("video", {}).get("style_preset", "tech-dark")
    trend_result = expand_with_claude(winner, model=model, style_preset=style)

    log.info(f"  Topic    : {trend_result.topic}")
    log.info(f"  Title    : {trend_result.title}")
    log.info(f"  Audience : {trend_result.target_audience}")
    return trend_result
