"""
production/script_writer.py — Viral Script Generator
=====================================================
Generates structured, platform-optimised video scripts via Claude.

7-section viral structure:
  1. Pattern-Disrupt Hook       (0–3s)   — unexpected claim / visual
  2. Amplify                    (3–7s)   — why this matters RIGHT NOW
  3. Credibility Anchor         (7–12s)  — proof / authority signal
  4. Bold Promise               (12–18s) — what they'll get
  5. Value Loops                (18s–end-30s) — dense insight delivery
  6. Engagement Blast           (end-30s–end-15s) — like/comment CTA
  7. Next Video Hook            (last 10s) — teaser / retention loop

Output: structured dict with scenes, voiceover, timing, b-roll directions.
"""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import anthropic
except ImportError:
    anthropic = None  # type: ignore

log = logging.getLogger("brain.script_writer")

HERE    = Path(__file__).parent.parent
CFG_DIR = HERE / "config"

SYSTEM_PROMPT = """You are an elite YouTube scriptwriter who has written scripts
for channels with 10M+ subscribers. You understand the algorithm, retention
curves, and psychological hooks that make videos viral.

Always return ONLY valid JSON — no markdown, no explanation."""

SCRIPT_PROMPT = """Write a VIRAL YouTube script for the following topic.

TOPIC: {topic_title}
PLATFORM: {platform}
STYLE: {style}
TARGET DURATION: {target_s} seconds
CHANNEL PERSONA: {persona_summary}
COMPETITOR FORMULA: {formula_summary}
RECENT LESSONS: {lessons}

Use this 7-section structure (in scene objects):
1. Pattern-Disrupt Hook (0-3s): Shocking stat, counter-intuitive claim, or visceral visual
2. Amplify (3-7s): Urgency — why this matters RIGHT NOW
3. Credibility Anchor (7-12s): Brief authority proof
4. Bold Promise (12-18s): "By the end of this video you'll know exactly how to…"
5. Value Loops (18s → last 30s): Dense insights with micro-hooks every 60s to prevent drop-off
6. Engagement Blast (last 30s): Like/comment/subscribe CTA tied to the content
7. Next Video Hook (last 10s): Tease the next video to extend watch session

Return JSON with this exact structure:
{{
  "title": "YouTube-optimised title (40-70 chars)",
  "hook": "the opening 3 seconds verbatim",
  "hook_style": "shocking_stat|question|story|visual_description",
  "cta": "call to action text",
  "total_duration": {target_s},
  "platform": "{platform}",
  "style": "{style}",
  "target_audience": "one sentence",
  "scenes": [
    {{
      "id": 1,
      "section": "hook|amplify|credibility|promise|value_loop|engagement|next_hook",
      "duration": 4.0,
      "voiceover": "exact words to say",
      "visual": "what appears on screen",
      "text_overlay": "text shown on screen (max 6 words)",
      "transition": "cut|crossfade|zoom_in|zoom_out|slide",
      "shot_type": "wide|medium|close|graphic|broll",
      "broll_description": "search query for stock footage",
      "pacing": "fast|medium|slow",
      "keywords": ["word1", "word2"]
    }}
  ]
}}"""


class ScriptWriter:
    def __init__(self, memory=None, model: str = "claude-sonnet-4-6"):
        from core.memory import get_memory
        self.memory = memory or get_memory()
        self.model  = model
        self._client = None

    def _claude(self):
        if anthropic is None:
            return None
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=key)
        return self._client

    def _load_persona_summary(self) -> str:
        path = CFG_DIR / "channel_persona.yaml"
        if not path.exists():
            return "Technology education channel for curious adults"
        try:
            import yaml
            with open(path) as f:
                p = yaml.safe_load(f) or {}
            return (f"{p.get('name','Channel')} — {p.get('tagline','')} "
                    f"Audience: {p.get('target_audience','')} "
                    f"Tone: {p.get('tone','informative')}")
        except Exception:
            return "Technology education channel"

    def _load_formula_summary(self) -> str:
        from intelligence.competitor_spy import CompetitorSpy
        formula = CompetitorSpy(self.memory).get_formula()
        if not formula:
            return "Use numbers, questions, and 6-10 word titles"
        patterns = formula.get("common_patterns", [])[:3]
        avg_len  = formula.get("avg_competitor_len", 600)
        return (f"Common patterns: {'; '.join(patterns)}. "
                f"Avg competitor length: {int(avg_len)}s")

    def _load_recent_lessons(self) -> str:
        summary = self.memory.performance_summary()
        return (
            f"Avg CTR: {summary.get('avg_ctr',0):.2f}%  "
            f"Avg retention: {summary.get('avg_avd_pct',0):.1f}%  "
            f"Best style: {self.memory.get_best_config('style_preset') or 'unknown'}"
        )

    # ── Script generation ──────────────────────────────────────────────────

    def generate(self, topic: Dict, style: str = "tech-dark",
                  target_length_s: int = 600,
                  platform: str = "youtube") -> Dict:
        """Generate a full viral script for the given topic."""
        topic_title = topic.get("title", "")
        log.info(f"Script writer: generating for {topic_title!r}")

        prompt = SCRIPT_PROMPT.format(
            topic_title=topic_title,
            platform=platform,
            style=style,
            target_s=target_length_s,
            persona_summary=self._load_persona_summary(),
            formula_summary=self._load_formula_summary(),
            lessons=self._load_recent_lessons(),
        )

        client = self._claude()
        if client:
            try:
                msg = client.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = msg.content[0].text.strip()
                # Strip accidental markdown fences
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$", "", raw)
                script = json.loads(raw)
                log.info(
                    f"  Script: {len(script.get('scenes',[]))} scenes  "
                    f"title={script.get('title','')!r}"
                )
                return self._apply_psychology(script)
            except Exception as e:
                log.warning(f"  Claude script generation failed: {e}")

        # Fallback: minimal structure
        return self._apply_psychology(
            self._fallback_script(topic_title, style, target_length_s, platform)
        )

    def _apply_psychology(self, script: Dict) -> Dict:
        """Run script through viewer psychology engine; log score; return enhanced script."""
        try:
            from production.viewer_psychology import ViewerPsychology, EngagementTooLow
            vp = ViewerPsychology(self.memory)
            script = vp.engineer(script)
            log.info(f"  Psychology score: {script.get('psychology_score', '?')}/100")
        except Exception as e:
            log.warning(f"  Viewer psychology engine failed (non-fatal): {e}")
        return script

    def _fallback_script(self, topic_title: str, style: str,
                          target_length_s: int, platform: str) -> Dict:
        """Rule-based minimal script when Claude is unavailable."""
        scene_duration = target_length_s / 8
        sections = [
            ("hook",        "Did you know the world is changing faster than ever?"),
            ("amplify",     f"Today we're exploring: {topic_title}"),
            ("credibility", "Based on the latest research and expert analysis…"),
            ("promise",     "By the end of this video, you'll understand exactly what this means for you."),
            ("value_loop",  f"Here's the first thing you need to know about {topic_title}…"),
            ("value_loop",  "And here's why it matters more than you think…"),
            ("engagement",  "If you found this useful, smash the like button!"),
            ("next_hook",   "In our next video, we'll go even deeper. Stay tuned."),
        ]
        scenes = []
        for i, (section, voiceover) in enumerate(sections, 1):
            scenes.append({
                "id":               i,
                "section":          section,
                "duration":         round(scene_duration, 1),
                "voiceover":        voiceover,
                "visual":           f"Relevant graphic for scene {i}",
                "text_overlay":     topic_title[:30] if i == 1 else "",
                "transition":       "cut",
                "shot_type":        "graphic",
                "broll_description": topic_title,
                "pacing":           "medium",
                "keywords":         topic_title.lower().split()[:3],
            })
        return {
            "title":           topic_title[:70],
            "hook":            sections[0][1],
            "hook_style":      "statement",
            "cta":             "Like, subscribe, and hit the bell!",
            "total_duration":  target_length_s,
            "platform":        platform,
            "style":           style,
            "target_audience": "curious adults interested in technology",
            "scenes":          scenes,
        }

    def refine_hook(self, script: Dict) -> Dict:
        """Ask Claude to A/B test 3 alternative hooks and pick the best."""
        client = self._claude()
        if not client or not script.get("scenes"):
            return script

        original_hook = script["scenes"][0].get("voiceover", "")
        prompt = (
            f"Given this YouTube video hook:\n'{original_hook}'\n\n"
            "Generate 3 alternative hooks that are more emotionally gripping. "
            "Return JSON: {\"best_hook\": \"...\", \"alternatives\": [\"...\", \"...\"]}"
        )
        try:
            msg = client.messages.create(
                model=self.model, max_tokens=512,
                system="You are a viral hook writer. Return only JSON.",
                messages=[{"role": "user", "content": prompt}],
            )
            raw = msg.content[0].text.strip()
            m   = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                data = json.loads(m.group())
                best = data.get("best_hook", "")
                if best:
                    script["scenes"][0]["voiceover"] = best
                    script["hook"] = best
                    log.info(f"  Hook refined: {best[:60]!r}")
        except Exception as e:
            log.debug(f"Hook refinement failed: {e}")
        return script
