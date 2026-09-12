"""
intelligence/performance_scorer.py — Universal 1-100 Output Scorer
===================================================================
Scores every video production output on a 100-point scale across:
  - Script quality          (25 pts)
  - Audio clarity / warmth  (20 pts)
  - Visual quality          (20 pts)
  - SEO & metadata          (20 pts)
  - Thumbnail CTR potential (15 pts)

Scores feed back into memory so the Brain can avoid repeating
low-scoring approaches.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import anthropic

log = logging.getLogger("brain.performance_scorer")

HERE = Path(__file__).parent.parent

SCORING_SYSTEM = """You are a YouTube production quality assessor.
Score the provided video assets on a 1-100 scale across specified dimensions.
Return ONLY a JSON object with numeric scores. No explanation."""

SCORING_PROMPT = """Score this YouTube video production on each dimension.

TOPIC: {topic}
TITLE: {title}
SCRIPT_EXCERPT: {script_excerpt}
THUMBNAIL_STYLE: {thumbnail_style}
VIDEO_STYLE: {video_style}
HAS_CAPTIONS: {has_captions}
AUDIO_QUALITY: {audio_quality}
VIDEO_DURATION_S: {duration_s}

Return JSON:
{{
  "script_quality": 0-25,
  "audio_clarity": 0-20,
  "visual_quality": 0-20,
  "seo_strength": 0-20,
  "thumbnail_ctr_potential": 0-15,
  "total": 0-100,
  "weakest_dimension": "name",
  "top_improvement": "one sentence"
}}"""


class PerformanceScorer:
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

    # ── Rule-based subscores (no API needed) ──────────────────────────────

    @staticmethod
    def _score_script(script: Dict) -> int:
        """0-25 based on structure, length, hook presence."""
        score = 0
        hook = script.get("hook", "")
        if hook and len(hook) > 20:
            score += 8
        scenes = script.get("scenes", [])
        if len(scenes) >= 5:
            score += 7
        total_words = sum(len(s.get("voiceover", "").split())
                          for s in scenes)
        if total_words > 300:
            score += 5
        if script.get("cta"):
            score += 5
        return min(score, 25)

    @staticmethod
    def _score_audio(audio_path: Optional[str]) -> int:
        """0-20 based on file existence and size."""
        if not audio_path:
            return 0
        p = Path(audio_path)
        if not p.exists():
            return 0
        size_kb = p.stat().st_size / 1024
        if size_kb < 10:
            return 5
        if size_kb < 100:
            return 12
        return 18

    @staticmethod
    def _score_visuals(video_path: Optional[str],
                       thumbnail_paths: List[str]) -> int:
        """0-20 based on video existence + thumbnail count."""
        score = 0
        if video_path and Path(video_path).exists():
            score += 12
        score += min(len(thumbnail_paths), 3) * 2
        return min(score, 20)

    @staticmethod
    def _score_seo(metadata: Dict) -> int:
        """0-20 based on title length, description, tags."""
        score = 0
        title = metadata.get("title", "")
        if 30 <= len(title) <= 80:
            score += 7
        desc = metadata.get("description", "")
        if len(desc) > 200:
            score += 7
        tags = metadata.get("tags", [])
        if len(tags) >= 10:
            score += 6
        return min(score, 20)

    @staticmethod
    def _score_thumbnail(thumbnail_paths: List[str]) -> int:
        """0-15: have 3 variants → full marks."""
        n = len(thumbnail_paths)
        return min(n * 5, 15)

    # ── Claude-powered scoring (enhanced) ─────────────────────────────────

    def _claude_score(self, topic: str, metadata: Dict,
                       script: Dict, style: str,
                       has_captions: bool) -> Optional[Dict]:
        client = self._claude()
        if not client:
            return None

        scenes = script.get("scenes", [])
        excerpt = " ".join(
            s.get("voiceover", "")[:80] for s in scenes[:3]
        )
        prompt = SCORING_PROMPT.format(
            topic=topic,
            title=metadata.get("title", ""),
            script_excerpt=excerpt[:300],
            thumbnail_style=metadata.get("thumbnail_style", ""),
            video_style=style,
            has_captions=has_captions,
            audio_quality="espeak+eq" if not os.getenv("ELEVENLABS_API_KEY") else "elevenlabs",
            duration_s=script.get("total_duration", 0),
        )
        try:
            msg = client.messages.create(
                model=self.model,
                max_tokens=512,
                system=SCORING_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            m   = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
        except Exception as e:
            log.debug(f"Claude scoring failed: {e}")
        return None

    # ── Main scoring API ───────────────────────────────────────────────────

    def score(self, topic: str, script: Dict, metadata: Dict,
               audio_path: Optional[str] = None,
               video_path: Optional[str] = None,
               thumbnail_paths: Optional[List[str]] = None,
               style: str = "tech-dark",
               has_captions: bool = False) -> Dict:
        """
        Return a 100-point score breakdown for this video production.
        Tries Claude first; falls back to rule-based scoring.
        """
        thumbnail_paths = thumbnail_paths or []

        # Try Claude first
        claude_result = self._claude_score(topic, metadata, script, style, has_captions)
        if claude_result and claude_result.get("total"):
            result = claude_result
        else:
            # Rule-based fallback
            s_script = self._score_script(script)
            s_audio  = self._score_audio(audio_path)
            s_visual = self._score_visuals(video_path, thumbnail_paths)
            s_seo    = self._score_seo(metadata)
            s_thumb  = self._score_thumbnail(thumbnail_paths)
            total    = s_script + s_audio + s_visual + s_seo + s_thumb

            dims = {
                "script_quality":          s_script,
                "audio_clarity":           s_audio,
                "visual_quality":          s_visual,
                "seo_strength":            s_seo,
                "thumbnail_ctr_potential": s_thumb,
            }
            weakest = min(dims, key=lambda k: dims[k] / {
                "script_quality": 25, "audio_clarity": 20,
                "visual_quality": 20, "seo_strength": 20,
                "thumbnail_ctr_potential": 15,
            }[k])

            result = {**dims, "total": total, "weakest_dimension": weakest,
                      "top_improvement": f"Improve {weakest.replace('_', ' ')}"}

        result["topic"]    = topic
        result["scored_at"] = datetime.now(timezone.utc).isoformat()

        grade = "A" if result["total"] >= 85 else \
                "B" if result["total"] >= 70 else \
                "C" if result["total"] >= 55 else "D"
        result["grade"] = grade

        log.info(f"Score: {result['total']}/100 ({grade}) "
                 f"— weakest: {result['weakest_dimension']}")
        return result

    def score_and_store(self, db_video_id: int, **kwargs) -> Dict:
        """Score and persist to memory (updates config_performance)."""
        result = self.score(**kwargs)

        total = result.get("total", 0)
        style = kwargs.get("style", "")
        if style and total:
            self.memory.update_config_performance(
                "style_preset", style,
                ctr=total / 100,   # use normalised score as proxy CTR
                avd=0, rpm=0,
            )

        # Save score JSON alongside video
        video_path = kwargs.get("video_path")
        if video_path:
            score_path = Path(video_path).parent / "performance_score.json"
            with open(score_path, "w") as f:
                json.dump(result, f, indent=2)

        return result
