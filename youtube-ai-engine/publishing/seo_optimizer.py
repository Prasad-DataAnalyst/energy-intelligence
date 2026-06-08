"""
publishing/seo_optimizer.py — Title / Description / Tags Optimizer
===================================================================
Uses Claude + competitor analysis to generate maximum-reach metadata:
  - A/B title variants (picks historically best formula)
  - Description with timestamps, keywords, CTAs, links
  - 15 high-volume / low-competition tags
  - Chapter markers from scene timing
  - Category selection
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import anthropic

log = logging.getLogger("brain.seo_optimizer")

SEO_SYSTEM = """You are a YouTube SEO expert who has grown channels to millions of subscribers.
Generate metadata that maximises CTR, AVD, and search discovery.
Return ONLY valid JSON."""

SEO_PROMPT = """Generate YouTube metadata for this video.

TOPIC: {topic}
SCRIPT_HOOK: {hook}
STYLE: {style}
CHANNEL PERSONA: {persona}
PERFORMANCE HISTORY: avg CTR {avg_ctr:.2f}%, avg retention {avg_avd:.1f}%
COMPETITOR PATTERNS: {formula}

Return JSON:
{{
  "title": "40-70 char, curiosity-gap + keyword, no clickbait",
  "title_variants": ["alt title 1", "alt title 2"],
  "description": "3-4 paragraphs: hook, value, social proof, CTA. Include keyword naturally 3x.",
  "tags": ["tag1", ..., "tag15"],
  "category_id": "28",
  "chapters": [
    {{"time": "0:00", "label": "Introduction"}},
    {{"time": "0:15", "label": "..."}}
  ],
  "thumbnail_style": "face_text|bold_stat|cinematic",
  "best_upload_time": "HH:MM UTC"
}}"""


class SEOOptimizer:
    def __init__(self, memory=None, model: str = "claude-sonnet-4-6"):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        self.model  = model
        self._client: Optional[anthropic.Anthropic] = None

    def _claude(self) -> Optional[anthropic.Anthropic]:
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _persona_summary(self) -> str:
        try:
            import yaml
            path = (
                __import__("pathlib").Path(__file__).parent.parent
                / "config" / "channel_persona.yaml"
            )
            if path.exists():
                with open(path) as f:
                    p = yaml.safe_load(f) or {}
                return f"{p.get('name','')} — {p.get('tagline','')} — {p.get('target_audience','')}"
        except Exception:
            pass
        return "Technology education channel for curious adults"

    def _formula_summary(self) -> str:
        from intelligence.competitor_spy import CompetitorSpy
        f = CompetitorSpy(self.memory).get_formula()
        if not f:
            return "Use numbered lists and question hooks"
        return "; ".join(f.get("common_patterns", [])[:3])

    @staticmethod
    def _build_chapters(script: Dict) -> List[Dict]:
        """Build chapter markers from scene timing."""
        chapters = []
        cursor   = 0.0
        sections_seen = set()
        for scene in script.get("scenes", []):
            section = scene.get("section", "")
            if section not in sections_seen:
                sections_seen.add(section)
                m   = int(cursor // 60)
                s   = int(cursor % 60)
                label = section.replace("_", " ").title()
                chapters.append({"time": f"{m}:{s:02d}", "label": label})
            cursor += float(scene.get("duration", 5))
        return chapters

    # ── Rule-based fallback ────────────────────────────────────────────────

    @staticmethod
    def _fallback_metadata(topic: Dict, script: Dict) -> Dict:
        title  = topic.get("title", "")[:70]
        hook   = script.get("hook", "")[:100]
        desc   = (
            f"{hook}\n\n"
            f"In this video we explore {title}.\n\n"
            f"📌 Subscribe for daily insights on technology, energy, and science.\n"
            f"👍 Like if you found this helpful!\n\n"
            f"#technology #AI #science #education #{title.split()[0].lower()}"
        )
        words = re.findall(r"\w{4,}", title.lower())
        tags  = list(dict.fromkeys(
            words + ["technology", "AI", "science", "education",
                     "future", "2025", "explained", "how it works"]
        ))[:15]
        return {
            "title":           title,
            "title_variants":  [title],
            "description":     desc,
            "tags":            tags,
            "category_id":     "28",
            "chapters":        [],
            "thumbnail_style": "face_text",
            "best_upload_time": "08:00",
        }

    # ── Main API ───────────────────────────────────────────────────────────

    def build_metadata(self, topic: Dict, script: Dict,
                        style: str = "tech-dark") -> Dict:
        """Generate full SEO metadata for the video."""
        summary   = self.memory.performance_summary()
        prompt    = SEO_PROMPT.format(
            topic=topic.get("title", ""),
            hook=script.get("hook", "")[:200],
            style=style,
            persona=self._persona_summary(),
            avg_ctr=summary.get("avg_ctr", 0),
            avg_avd=summary.get("avg_avd_pct", 0),
            formula=self._formula_summary(),
        )

        client = self._claude()
        if client:
            try:
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=1500,
                    system=SEO_SYSTEM,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = msg.content[0].text.strip()
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                m   = re.search(r"\{.*\}", raw, re.DOTALL)
                if m:
                    metadata = json.loads(m.group())
                    # Inject computed chapters if missing
                    if not metadata.get("chapters"):
                        metadata["chapters"] = self._build_chapters(script)
                    log.info(f"SEO metadata: {metadata.get('title','')!r}")
                    return metadata
            except Exception as e:
                log.warning(f"SEO Claude call failed: {e}")

        metadata = self._fallback_metadata(topic, script)
        metadata["chapters"] = self._build_chapters(script)
        return metadata

    def format_description_with_chapters(self, metadata: Dict) -> str:
        """Add chapter timestamps to description body."""
        chapters = metadata.get("chapters", [])
        if not chapters:
            return metadata.get("description", "")

        chapter_block = "\n".join(
            f"{c['time']} {c['label']}" for c in chapters
        )
        desc = metadata.get("description", "")
        return f"{desc}\n\n⏱ CHAPTERS:\n{chapter_block}"
